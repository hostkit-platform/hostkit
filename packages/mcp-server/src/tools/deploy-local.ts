// hostkit_deploy_local tool implementation
// Deploys local files to VPS via rsync, then executes hostkit deploy
// Auto-provisions projects that don't exist yet (with db, auth, storage)

import { spawn } from 'child_process';
import { existsSync, statSync } from 'fs';
import { resolve, join } from 'path';
import { getSSHManager } from '../services/ssh.js';
import { getConfig, getProjectContext } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { DeployLocalParams, ToolResponse } from '../types.js';

const logger = createLogger('tools:deploy-local');

/**
 * Auth integration warning for projects with auth enabled.
 * Based on Tesla Screen integration learnings.
 */
const AUTH_INTEGRATION_WARNING = {
  message: 'IMPORTANT: This project has auth enabled. Before implementing authentication, read the critical fixes below.',
  action_required: 'Call hostkit_auth_guide with this project name for complete code examples.',
  critical_fixes: [
    {
      issue: 'JWT Public Key Newlines',
      severity: 'HIGH',
      problem: 'AUTH_JWT_PUBLIC_KEY contains literal "\\n" strings, not real newlines',
      fix: 'const JWT_PUBLIC_KEY = process.env.AUTH_JWT_PUBLIC_KEY.replace(/\\\\n/g, "\\n");',
    },
    {
      issue: 'OAuth Response Structure',
      severity: 'HIGH',
      problem: '/auth/identity/verify returns tokens nested under "session", not at top level',
      fix: 'const { access_token, refresh_token } = responseBody.session || responseBody;',
    },
    {
      issue: 'Apple Sign In Browser',
      severity: 'MEDIUM',
      problem: 'Apple Sign In does not work in Chrome browsers (Apple policy)',
      fix: 'Detect Chrome and hide Apple Sign In button',
    },
  ],
};

// Timeout for rsync (5 minutes)
const RSYNC_TIMEOUT = 300000;

// Timeout for health check (2 minutes)
const HEALTH_TIMEOUT = 120000;

/**
 * Execute a command locally and return stdout.
 */
async function execLocal(
  command: string,
  args: string[],
  timeout: number
): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      timeout,
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    proc.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    proc.on('error', (err) => {
      reject(err);
    });

    proc.on('close', (code) => {
      resolve({ stdout, stderr, code: code ?? 1 });
    });

    // Manual timeout fallback
    const timer = setTimeout(() => {
      proc.kill('SIGTERM');
      reject(new Error(`Command timed out after ${timeout}ms`));
    }, timeout);

    proc.on('close', () => clearTimeout(timer));
  });
}

/**
 * Validate the local path exists and is a directory.
 */
function validateLocalPath(localPath: string): { valid: boolean; error?: string; resolvedPath?: string } {
  const resolved = resolve(localPath);

  if (!existsSync(resolved)) {
    return {
      valid: false,
      error: `Path does not exist: ${resolved}`,
    };
  }

  const stat = statSync(resolved);
  if (!stat.isDirectory()) {
    return {
      valid: false,
      error: `Path is not a directory: ${resolved}`,
    };
  }

  return { valid: true, resolvedPath: resolved };
}

/**
 * Detect project runtime from local directory contents.
 *
 * Priority:
 *   1. next.config.* → nextjs
 *   2. package.json (no next.config) → node
 *   3. requirements.txt or pyproject.toml → python
 *   4. index.html (no package.json) → static
 *   5. fallback → nextjs
 */
function detectRuntime(localPath: string): string {
  const hasNextConfig =
    existsSync(join(localPath, 'next.config.js')) ||
    existsSync(join(localPath, 'next.config.mjs')) ||
    existsSync(join(localPath, 'next.config.ts'));

  if (hasNextConfig) return 'nextjs';

  const hasPackageJson = existsSync(join(localPath, 'package.json'));
  if (hasPackageJson) return 'node';

  if (
    existsSync(join(localPath, 'requirements.txt')) ||
    existsSync(join(localPath, 'pyproject.toml'))
  ) {
    return 'python';
  }

  if (existsSync(join(localPath, 'index.html'))) return 'static';

  return 'nextjs';
}

/**
 * Check if a project exists on the VPS.
 */
async function projectExists(project: string): Promise<boolean> {
  try {
    const ssh = getSSHManager();
    const result = await ssh.executeHostkit(`project info ${project}`, { json: true });
    // If we get a result without error, project exists
    const info = result as Record<string, unknown>;
    return info.success !== false;
  } catch {
    return false;
  }
}

/**
 * Auto-provision a project with defaults (db + auth + storage).
 * Uses the provision command which is idempotent.
 */
async function autoProvision(
  project: string,
  runtime: string
): Promise<{ success: boolean; provision_result?: Record<string, unknown>; error?: string }> {
  try {
    const ssh = getSSHManager();

    // Build provision command with runtime flag
    const runtimeFlag = runtime === 'nextjs' ? '--nextjs'
      : runtime === 'python' ? '--python'
      : runtime === 'node' ? '--node'
      : runtime === 'static' ? '--static'
      : '--nextjs';

    // provision defaults to db + auth + storage ON, and --no-start
    // (we don't want to start the service before code is deployed)
    const command = `provision ${project} ${runtimeFlag} --no-start`;

    logger.info('Auto-provisioning project', { project, runtime, command });

    const result = await ssh.executeHostkit(command, { json: true });
    const data = result as Record<string, unknown>;

    // Check if provision succeeded
    if (data.success === false) {
      const errorData = data as { error?: { message?: string } };
      return {
        success: false,
        provision_result: data,
        error: errorData.error?.message || 'Provision failed',
      };
    }

    return {
      success: true,
      provision_result: data,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error('Auto-provision failed', { project, error: message });
    return {
      success: false,
      error: message,
    };
  }
}

/**
 * Rsync files to VPS temp directory.
 */
async function rsyncToVPS(
  localPath: string,
  project: string,
  config: ReturnType<typeof getConfig>
): Promise<{ success: boolean; remotePath?: string; error?: string; stats?: string }> {
  const timestamp = Date.now();
  const remotePath = `/tmp/hostkit-deploy-${project}-${timestamp}`;
  const sshTarget = `${config.vps.user}@${config.vps.host}`;

  // rsync args:
  // -a: archive mode (preserves permissions, timestamps, etc.)
  // -z: compress during transfer
  // -v: verbose
  // -L: dereference symlinks (copy actual files, not symlinks)
  //     This is critical for pnpm projects where node_modules contains symlinks
  //     to .pnpm store that won't exist on the VPS
  // --delete: delete extraneous files on remote
  // --exclude: skip common unneeded directories
  // -e: specify SSH with key
  //
  // NOTE: We do NOT exclude .next or node_modules here because:
  // - Standalone deployments include a minimal .next/server and node_modules
  // - The HostKit deploy service handles detecting build type
  // - Full node_modules should be excluded at the SOURCE level (build locally first)
  const rsyncArgs = [
    '-azvL',
    '--delete',
    '--exclude', '.git',
    '--exclude', '__pycache__',
    '--exclude', '.venv',
    '--exclude', 'venv',
    '--exclude', '.env.local',
    '-e', `ssh -i ${config.vps.keyPath} -o StrictHostKeyChecking=no`,
    `${localPath}/`,
    `${sshTarget}:${remotePath}/`,
  ];

  logger.info('Starting rsync', { localPath, remotePath, target: sshTarget });

  try {
    const result = await execLocal('rsync', rsyncArgs, RSYNC_TIMEOUT);

    if (result.code !== 0) {
      return {
        success: false,
        error: `rsync failed (code ${result.code}): ${result.stderr}`,
      };
    }

    // Extract transfer stats from rsync output
    const statsMatch = result.stdout.match(/sent ([\d,]+) bytes.*received ([\d,]+) bytes/);
    const stats = statsMatch ? `sent ${statsMatch[1]} bytes, received ${statsMatch[2]} bytes` : undefined;

    return {
      success: true,
      remotePath,
      stats,
    };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * Clean up temp directory on VPS.
 */
async function cleanupRemote(remotePath: string): Promise<void> {
  try {
    const ssh = getSSHManager();
    await ssh.execute(`rm -rf ${remotePath}`);
    logger.debug('Cleaned up remote path', { remotePath });
  } catch (error) {
    logger.warn('Failed to cleanup remote path', { remotePath, error });
  }
}

/**
 * Wait for a project to become healthy.
 */
async function waitForHealthy(
  project: string,
  maxWaitMs: number
): Promise<{ healthy: boolean; health?: Record<string, unknown> }> {
  const ssh = getSSHManager();
  const startTime = Date.now();
  const checkInterval = 3000; // Check every 3 seconds

  while (Date.now() - startTime < maxWaitMs) {
    try {
      const result = await ssh.executeHostkit(`health ${project}`, { json: true });
      const health = result as Record<string, unknown>;

      if (health.healthy === true || health.status === 'running') {
        return { healthy: true, health };
      }
    } catch (error) {
      // Health check may fail if service is still starting
      logger.debug('Health check failed, retrying...', { project });
    }

    await new Promise((resolve) => setTimeout(resolve, checkInterval));
  }

  // Final check
  try {
    const result = await ssh.executeHostkit(`health ${project}`, { json: true });
    const health = result as Record<string, unknown>;
    return {
      healthy: health.healthy === true || health.status === 'running',
      health,
    };
  } catch {
    return { healthy: false };
  }
}

/**
 * Handle hostkit_deploy_local tool calls.
 */
export async function handleDeployLocal(params: DeployLocalParams): Promise<ToolResponse> {
  const project = params.project || getProjectContext();
  const {
    local_path,
    build = false,
    install = false,
    wait_healthy = true,
    cleanup = true,
    override_ratelimit = false,
    auto_provision = true,
  } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  logger.info('Deploy local request', { project, local_path, build, install, wait_healthy, auto_provision });

  // Validate local path
  const pathValidation = validateLocalPath(local_path);
  if (!pathValidation.valid) {
    return {
      success: false,
      error: {
        code: 'INVALID_PATH',
        message: pathValidation.error!,
      },
    };
  }

  const config = getConfig();
  let remotePath: string | undefined;
  let provisionResult: Record<string, unknown> | undefined;
  let autoProvisioned = false;

  try {
    // Step 0: Check if project exists, auto-provision if needed
    if (auto_provision) {
      const exists = await projectExists(project);
      if (!exists) {
        const detectedRuntime = detectRuntime(pathValidation.resolvedPath!);
        logger.info('Project does not exist, auto-provisioning', { project, detectedRuntime });

        const provision = await autoProvision(project, detectedRuntime);
        if (!provision.success) {
          return {
            success: false,
            error: {
              code: 'AUTO_PROVISION_FAILED',
              message: `Project '${project}' does not exist and auto-provisioning failed: ${provision.error}`,
              details: { provision_result: provision.provision_result },
            },
          };
        }

        autoProvisioned = true;
        provisionResult = provision.provision_result;
        logger.info('Auto-provision complete', { project });
      }
    }

    // Step 1: rsync files to VPS
    const rsyncResult = await rsyncToVPS(pathValidation.resolvedPath!, project, config);
    if (!rsyncResult.success) {
      return {
        success: false,
        error: {
          code: 'RSYNC_FAILED',
          message: rsyncResult.error!,
        },
      };
    }

    remotePath = rsyncResult.remotePath;
    logger.info('rsync complete', { remotePath, stats: rsyncResult.stats });

    // Step 2: Execute deploy command
    const ssh = getSSHManager();

    // Build deploy command with flags
    const deployFlags: string[] = [];
    deployFlags.push(`--source ${remotePath}`);
    if (build) deployFlags.push('--build');
    if (install) deployFlags.push('--install');
    if (override_ratelimit) deployFlags.push('--override-ratelimit');

    const deployCommand = `deploy ${project} ${deployFlags.join(' ')}`;
    logger.info('Executing deploy', { command: deployCommand });

    let deployResult: unknown;
    try {
      deployResult = await ssh.executeHostkit(deployCommand, {
        project,
        json: true,
      });
    } catch (error) {
      // Cleanup even on deploy failure
      if (cleanup && remotePath) {
        await cleanupRemote(remotePath);
      }

      const message = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        error: {
          code: 'DEPLOY_FAILED',
          message,
          details: { remotePath, auto_provisioned: autoProvisioned },
        },
      };
    }

    // Step 3: Cleanup temp directory (unless disabled)
    if (cleanup && remotePath) {
      await cleanupRemote(remotePath);
    }

    // Step 4: Wait for healthy (if requested)
    let healthResult: { healthy: boolean; health?: Record<string, unknown> } | undefined;
    if (wait_healthy) {
      logger.info('Waiting for service to become healthy', { project });
      healthResult = await waitForHealthy(project, HEALTH_TIMEOUT);

      if (!healthResult.healthy) {
        return {
          success: true, // Deploy succeeded, but service not healthy
          data: {
            deployed: true,
            healthy: false,
            auto_provisioned: autoProvisioned,
            ...(provisionResult ? { provision_result: provisionResult } : {}),
            warning: 'Service deployed but not healthy within timeout',
            deploy_result: deployResult,
            health: healthResult.health,
          },
        };
      }
    }

    // Check if project has auth enabled by querying project info
    let authWarning: typeof AUTH_INTEGRATION_WARNING | undefined;
    try {
      const projectInfo = await ssh.executeHostkit(`project info ${project}`, { json: true });
      const info = projectInfo as Record<string, unknown>;
      if (info.data && typeof info.data === 'object') {
        const data = info.data as Record<string, unknown>;
        if (data.project && typeof data.project === 'object') {
          const proj = data.project as Record<string, unknown>;
          if (Array.isArray(proj.services) && proj.services.includes('auth')) {
            authWarning = AUTH_INTEGRATION_WARNING;
          }
        }
      }
    } catch {
      // Ignore errors checking for auth - not critical
    }

    return {
      success: true,
      data: {
        deployed: true,
        healthy: healthResult?.healthy ?? 'not_checked',
        auto_provisioned: autoProvisioned,
        ...(provisionResult ? { provision_result: provisionResult } : {}),
        source: pathValidation.resolvedPath,
        rsync_stats: rsyncResult.stats,
        deploy_result: deployResult,
        health: healthResult?.health,
        ...(authWarning ? { auth_warning: authWarning } : {}),
      },
    };
  } catch (error) {
    // Cleanup on any error
    if (cleanup && remotePath) {
      await cleanupRemote(remotePath);
    }

    const message = error instanceof Error ? error.message : String(error);
    logger.error('Deploy local failed', { project, error: message });

    return {
      success: false,
      error: {
        code: 'DEPLOY_LOCAL_ERROR',
        message,
      },
    };
  }
}
