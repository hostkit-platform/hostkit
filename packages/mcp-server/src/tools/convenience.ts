// Convenience MCP tools for common HostKit operations
// These wrap CLI commands with specialized interfaces for better UX

import { getSSHManager } from '../services/ssh.js';
import { createLogger } from '../utils/logger.js';
import type { ToolResponse } from '../types.js';

const logger = createLogger('tools:convenience');

// =============================================================================
// Type Definitions
// =============================================================================

export interface CapabilitiesParams {
  project?: string;
}

export interface WaitHealthyParams {
  project: string;
  timeout?: number;
  interval?: number;
}

export interface EnvSetParams {
  project: string;
  variables: Record<string, string>;
  restart?: boolean;
}

export interface EnvGetParams {
  project: string;
  keys?: string[];
}

export interface ValidateParams {
  project: string;
}

// =============================================================================
// Static Capabilities Data
// =============================================================================

const HOSTKIT_CAPABILITIES = {
  version: '1.0.0',
  commands: {
    project: {
      description: 'Manage projects',
      subcommands: {
        create: {
          description: 'Create a new project',
          flags: {
            '--python': 'Python runtime (uvicorn/gunicorn)',
            '--node': 'Node.js runtime',
            '--nextjs': 'Next.js runtime',
            '--static': 'Static site (nginx)',
            '--with-db': 'Enable PostgreSQL database',
            '--with-auth': 'Enable authentication service',
            '--with-storage': 'Enable file storage',
            '--with-booking': 'Enable booking/scheduling service',
            '--with-sms': 'Enable SMS service (Twilio)',
            '--with-mail': 'Enable mail service',
            '--with-payments': 'Enable payments service (Stripe)',
            '--with-chatbot': 'Enable AI chatbot service',
            '--with-r2': 'Enable Cloudflare R2 storage',
            '--with-vector': 'Enable vector/RAG service',
          },
        },
        list: { description: 'List all projects' },
        info: { description: 'Get project details' },
        delete: { description: 'Delete a project (requires --force)' },
      },
    },
    deploy: {
      description: 'Deploy a project',
      flags: {
        '--source': 'Source directory on VPS',
        '--install': 'Install dependencies',
        '--build': 'Run build step',
        '--migrate': 'Run database migrations',
      },
    },
    rollback: { description: 'Rollback to previous release' },
    health: { description: 'Check project health' },
    validate: { description: 'Validate project configuration' },
    env: {
      description: 'Manage environment variables',
      subcommands: {
        list: { description: 'List all variables' },
        get: { description: 'Get a specific variable' },
        set: { description: 'Set a variable (supports --restart flag)' },
        unset: { description: 'Remove a variable (supports --restart flag)' },
        sync: { description: 'Sync from .env.example' },
        import: { description: 'Import from YAML file' },
      },
    },
    service: {
      description: 'Manage systemd services',
      subcommands: {
        start: { description: 'Start the service' },
        stop: { description: 'Stop the service' },
        restart: { description: 'Restart the service' },
        status: { description: 'Check service status' },
        logs: { description: 'View service logs' },
      },
    },
    nginx: {
      description: 'Manage nginx configuration',
      subcommands: {
        add: { description: 'Add custom domain' },
        remove: { description: 'Remove custom domain' },
        list: { description: 'List domains' },
      },
    },
    backup: {
      description: 'Backup management',
      subcommands: {
        create: { description: 'Create backup (--r2 for cloud)' },
        restore: { description: 'Restore from backup' },
        list: { description: 'List backups' },
      },
    },
    db: {
      description: 'Database operations',
      subcommands: {
        shell: { description: 'Open psql shell' },
        dump: { description: 'Export database' },
        restore: { description: 'Import database' },
      },
    },
  },
  services: {
    auth: {
      description: 'Authentication service (JWT, magic links, OAuth)',
      port_offset: 1000,
      commands: ['auth enable', 'auth disable', 'auth status', 'auth sync', 'auth export-key'],
      integration: {
        critical_warning: 'DO NOT implement OAuth yourself. The auth service handles everything.',
        how_it_works: 'HostKit runs a separate auth microservice that manages users, sessions, and OAuth. Your app just redirects to it and validates JWTs.',
        do_not: [
          'Add GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET to your project .env (they belong to the auth service)',
          'Use next-auth, passport, Auth.js, or similar libraries (use the auth service instead)',
          'Implement OAuth callbacks in your app (the auth service handles callbacks)',
          'Store user credentials or sessions in your app (the auth service manages this)',
        ],
        instead: [
          'Redirect users to /auth/oauth/google/login for web OAuth',
          'POST to /auth/oauth/google/verify-token for native app token verification',
          'Validate JWTs using AUTH_JWT_PUBLIC_KEY from your .env',
          'Call /auth/me to get user info from a valid JWT',
        ],
        get_full_guide: 'Call hostkit_auth_guide with your project name for runtime-specific code examples',
      },
    },
    payments: {
      description: 'Stripe payments service (Connect, subscriptions)',
      port_offset: 2000,
      commands: ['payments enable', 'payments disable', 'payments status'],
      integration: {
        critical_warning: 'DO NOT integrate Stripe directly. The payments service handles Stripe Connect.',
        do_not: [
          'Add STRIPE_SECRET_KEY to your project .env',
          'Use stripe npm/pip package directly for core payment flows',
          'Implement webhook handlers for Stripe events',
        ],
        instead: [
          'Use PAYMENTS_URL environment variable to call the payments service',
          'The payments service handles Connect onboarding, subscriptions, and webhooks',
        ],
      },
    },
    sms: {
      description: 'SMS service (Twilio)',
      port_offset: 3000,
      commands: ['sms enable', 'sms disable', 'sms status'],
      integration: {
        critical_warning: 'DO NOT integrate Twilio directly. Use the SMS service endpoints.',
        do_not: [
          'Add TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN to your project .env',
          'Use twilio npm/pip package directly',
        ],
        instead: [
          'POST to SMS_URL/send with phone and message',
          'The SMS service handles rate limiting and delivery tracking',
        ],
      },
    },
    booking: {
      description: 'Booking/scheduling service',
      port_offset: 4000,
      commands: ['booking enable', 'booking disable', 'booking status', 'booking seed'],
    },
    chatbot: {
      description: 'AI chatbot service (Claude, GPT)',
      port_offset: 5000,
      commands: ['chatbot enable', 'chatbot disable', 'chatbot status', 'chatbot config'],
      integration: {
        critical_warning: 'DO NOT call Anthropic/OpenAI APIs directly. Use the chatbot service.',
        do_not: [
          'Add ANTHROPIC_API_KEY or OPENAI_API_KEY to your project .env',
          'Use anthropic or openai npm/pip packages directly',
        ],
        instead: [
          'POST to CHATBOT_URL/chat with messages array',
          'The chatbot service handles API keys, rate limiting, and model selection',
        ],
      },
    },
    voice: {
      description: 'Voice service (Twilio, Deepgram, Cartesia)',
      port: 8900,
      commands: ['voice enable', 'voice disable', 'voice status'],
    },
    r2: {
      description: 'Cloudflare R2 storage',
      commands: ['r2 enable', 'r2 disable', 'r2 status'],
    },
    vector: {
      description: 'Vector database / RAG service',
      commands: ['vector enable', 'vector disable', 'vector status'],
    },
    mail: {
      description: 'Email sending service',
      commands: ['mail enable', 'mail disable', 'mail status'],
    },
  },
  runtimes: {
    python: {
      start_command: 'venv/bin/python -m app',
      install_command: 'python -m venv venv && venv/bin/pip install -r requirements.txt',
    },
    node: {
      start_command: 'node app/index.js',
      install_command: 'npm install',
    },
    nextjs: {
      start_command: 'npm start',
      install_command: 'npm install',
      build_command: 'npm run build',
    },
    static: {
      description: 'Served directly by nginx',
    },
  },
  project_structure: {
    releases: 'Timestamped deploy directories',
    app: 'Symlink to current release',
    shared: 'Persistent data across deploys',
    '.env': 'Environment variables',
    venv: 'Python virtual environment (if Python)',
    node_modules: 'Node dependencies (if Node/Next.js)',
  },
};

// =============================================================================
// Tool Implementations
// =============================================================================

/**
 * Return HostKit capabilities - available commands, services, and configuration options.
 */
export async function handleCapabilities(
  params: CapabilitiesParams
): Promise<ToolResponse> {
  const { project } = params;

  logger.info('Capabilities request', { project });

  try {
    const result: Record<string, unknown> = {
      hostkit: HOSTKIT_CAPABILITIES,
    };

    // If project specified, also fetch project-specific capabilities
    if (project) {
      const ssh = getSSHManager();
      try {
        const projectCaps = await ssh.executeHostkit(`capabilities --project ${project}`);
        result.project = {
          name: project,
          capabilities: projectCaps,
        };
      } catch (error) {
        // Project capabilities fetch failed, but static caps are still useful
        result.project = {
          name: project,
          error: 'Could not fetch project-specific capabilities',
        };
      }
    }

    return { success: true, data: result };
  } catch (error) {
    logger.error('Capabilities fetch failed', error);
    return {
      success: false,
      error: {
        code: 'CAPABILITIES_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

/**
 * Wait for a project to become healthy, with timeout.
 */
export async function handleWaitHealthy(
  params: WaitHealthyParams
): Promise<ToolResponse> {
  const { project, timeout = 120000, interval = 5000 } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  logger.info('Wait healthy request', { project, timeout, interval });

  const ssh = getSSHManager();
  const startTime = Date.now();
  let lastHealth: unknown = null;
  let attempts = 0;

  try {
    while (Date.now() - startTime < timeout) {
      attempts++;

      try {
        const health = await ssh.executeHostkit(`health ${project}`);
        lastHealth = health;

        // Check if healthy - handle multiple response formats:
        // 1. { success: true, data: { overall: "healthy" } } - from hostkit health --json
        // 2. { healthy: true } - simplified format
        // 3. { status: "running" } - service status format
        const isHealthy =
          (typeof health === 'object' && health !== null && (
            // Format 1: CLI JSON output with nested data
            ('success' in health && health.success === true &&
             'data' in health && typeof health.data === 'object' && health.data !== null &&
             'overall' in health.data && health.data.overall === 'healthy') ||
            // Format 2: Direct healthy flag
            ('healthy' in health && health.healthy === true) ||
            // Format 3: Running status
            ('status' in health && health.status === 'running')
          ));

        if (isHealthy) {
          return {
            success: true,
            data: {
              healthy: true,
              health,
              attempts,
              elapsed_ms: Date.now() - startTime,
            },
          };
        }
      } catch (healthError) {
        // Health check failed, continue polling
        logger.debug('Health check failed, retrying...', { attempt: attempts });
      }

      // Wait before next check
      await new Promise((resolve) => setTimeout(resolve, interval));
    }

    // Timeout reached
    return {
      success: false,
      error: {
        code: 'TIMEOUT',
        message: `Project ${project} did not become healthy within ${timeout}ms`,
        details: {
          last_health: lastHealth,
          attempts,
          elapsed_ms: Date.now() - startTime,
        },
      },
    };
  } catch (error) {
    logger.error('Wait healthy failed', error);
    return {
      success: false,
      error: {
        code: 'WAIT_HEALTHY_ERROR',
        message: error instanceof Error ? error.message : String(error),
        details: {
          last_health: lastHealth,
          attempts,
          elapsed_ms: Date.now() - startTime,
        },
      },
    };
  }
}

/**
 * Set environment variables for a project with optional restart.
 */
export async function handleEnvSet(
  params: EnvSetParams
): Promise<ToolResponse> {
  const { project, variables, restart = false } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  if (!variables || Object.keys(variables).length === 0) {
    return {
      success: false,
      error: {
        code: 'MISSING_VARIABLES',
        message: 'At least one variable is required',
      },
    };
  }

  logger.info('Env set request', { project, variableCount: Object.keys(variables).length, restart });

  const ssh = getSSHManager();
  const results: Record<string, { success: boolean; error?: string }> = {};

  try {
    for (const [key, value] of Object.entries(variables)) {
      try {
        const restartFlag = restart ? ' --restart' : '';
        await ssh.executeHostkit(`env set ${project} ${key}="${value}"${restartFlag}`);
        results[key] = { success: true };
      } catch (error) {
        results[key] = {
          success: false,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    }

    const allSuccess = Object.values(results).every((r) => r.success);

    return {
      success: allSuccess,
      data: {
        project,
        results,
        restarted: restart && allSuccess,
      },
    };
  } catch (error) {
    logger.error('Env set failed', error);
    return {
      success: false,
      error: {
        code: 'ENV_SET_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

/**
 * Get environment variables for a project.
 */
export async function handleEnvGet(
  params: EnvGetParams
): Promise<ToolResponse> {
  const { project, keys } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  logger.info('Env get request', { project, keys });

  const ssh = getSSHManager();

  try {
    if (keys && keys.length > 0) {
      // Get specific keys
      const results: Record<string, string | null> = {};

      for (const key of keys) {
        try {
          const value = await ssh.executeHostkit(`env get ${project} ${key}`);
          results[key] = typeof value === 'string' ? value :
                         (value && typeof value === 'object' && 'value' in value) ?
                         String((value as { value: unknown }).value) : null;
        } catch {
          results[key] = null;
        }
      }

      return {
        success: true,
        data: { project, variables: results },
      };
    } else {
      // Get all variables
      const envList = await ssh.executeHostkit(`env list ${project}`);

      return {
        success: true,
        data: { project, variables: envList },
      };
    }
  } catch (error) {
    logger.error('Env get failed', error);
    return {
      success: false,
      error: {
        code: 'ENV_GET_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

/**
 * Validate a project's configuration and readiness.
 */
export async function handleValidate(
  params: ValidateParams
): Promise<ToolResponse> {
  const { project } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  logger.info('Validate request', { project });

  const ssh = getSSHManager();

  try {
    const result = await ssh.executeHostkit(`validate ${project}`);

    // Parse validation result
    const isValid =
      (typeof result === 'object' && result !== null && 'valid' in result && result.valid === true);

    return {
      success: true,
      data: {
        project,
        valid: isValid,
        validation: result,
      },
    };
  } catch (error) {
    logger.error('Validate failed', error);
    return {
      success: false,
      error: {
        code: 'VALIDATE_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}
