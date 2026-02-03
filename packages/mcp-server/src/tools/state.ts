// hostkit_state tool implementation

import { getConfig, getProjectContext } from '../config.js';
import { getSSHManager } from '../services/ssh.js';
import { getStateCache, getCachedOrFetch } from '../services/cache.js';
import { createLogger } from '../utils/logger.js';
import type { StateParams, ToolResponse } from '../types.js';

const logger = createLogger('tools:state');

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

/**
 * Check if a project has auth enabled from its info/capabilities.
 */
function hasAuthEnabled(projectData: unknown): boolean {
  if (!projectData || typeof projectData !== 'object') return false;

  const data = projectData as Record<string, unknown>;

  // Check info.project.services
  if (data.info && typeof data.info === 'object') {
    const info = data.info as Record<string, unknown>;
    if (info.data && typeof info.data === 'object') {
      const infoData = info.data as Record<string, unknown>;
      if (infoData.project && typeof infoData.project === 'object') {
        const proj = infoData.project as Record<string, unknown>;
        if (Array.isArray(proj.services) && proj.services.includes('auth')) {
          return true;
        }
      }
    }
  }

  // Check capabilities
  if (data.capabilities && typeof data.capabilities === 'object') {
    const caps = data.capabilities as Record<string, unknown>;
    if (caps.auth) {
      return true;
    }
    if (caps.services && typeof caps.services === 'object') {
      const services = caps.services as Record<string, unknown>;
      if (services.auth) {
        return true;
      }
    }
  }

  return false;
}

/**
 * Handle hostkit_state tool calls.
 */
export async function handleState(
  params: StateParams
): Promise<ToolResponse> {
  const { scope = 'all', refresh = false } = params;
  const project = params.project || getProjectContext();

  logger.info('State request', { scope, project, refresh });

  const config = getConfig();
  const cache = getStateCache();
  const ssh = getSSHManager();

  try {
    switch (scope) {
      case 'projects': {
        const { data, cached, cachedAt } = await getCachedOrFetch(
          cache,
          'projects',
          config.cache.projectsTtl,
          refresh,
          async () => ssh.executeHostkit('project list')
        );
        return { success: true, data, cached, cachedAt };
      }

      case 'health': {
        const { data, cached, cachedAt } = await getCachedOrFetch(
          cache,
          'health',
          config.cache.healthTtl,
          refresh,
          async () => ssh.executeHostkit('status --vps')
        );
        return { success: true, data, cached, cachedAt };
      }

      case 'resources': {
        const { data, cached, cachedAt } = await getCachedOrFetch(
          cache,
          'resources',
          config.cache.healthTtl,
          refresh,
          async () => ssh.executeHostkit('status --resources')
        );
        return { success: true, data, cached, cachedAt };
      }

      case 'project': {
        if (!project) {
          return {
            success: false,
            error: {
              code: 'MISSING_PROJECT',
              message: 'Project name required when scope is "project"',
            },
          };
        }

        const { data, cached, cachedAt } = await getCachedOrFetch(
          cache,
          `project:${project}`,
          config.cache.projectTtl,
          refresh,
          async () => {
            // Fetch multiple pieces of project info in parallel
            const [info, health, capabilities] = await Promise.all([
              ssh.executeHostkit(`project info ${project}`).catch(() => null),
              ssh.executeHostkit(`health ${project}`).catch(() => null),
              ssh.executeHostkit(`capabilities --project ${project}`).catch(() => null),
            ]);

            // Check if project exists (all null means it doesn't)
            if (info === null && health === null && capabilities === null) {
              throw new Error(`Project '${project}' not found or inaccessible`);
            }

            return { info, health, capabilities };
          }
        );

        // Check if auth is enabled and add warning to data
        const authEnabled = hasAuthEnabled(data);
        if (authEnabled) {
          return {
            success: true,
            data: {
              ...data as object,
              auth_warning: AUTH_INTEGRATION_WARNING,
            },
            cached,
            cachedAt,
          };
        }

        return { success: true, data, cached, cachedAt };
      }

      case 'all':
      default: {
        // Fetch all state in parallel
        const [projectsResult, healthResult, resourcesResult] = await Promise.all([
          getCachedOrFetch(cache, 'projects', config.cache.projectsTtl, refresh, async () =>
            ssh.executeHostkit('project list')
          ),
          getCachedOrFetch(cache, 'health', config.cache.healthTtl, refresh, async () =>
            ssh.executeHostkit('status --vps')
          ),
          getCachedOrFetch(cache, 'resources', config.cache.healthTtl, refresh, async () =>
            ssh.executeHostkit('status --resources')
          ),
        ]);

        return {
          success: true,
          data: {
            projects: projectsResult.data,
            health: healthResult.data,
            resources: resourcesResult.data,
          },
          cached: projectsResult.cached && healthResult.cached && resourcesResult.cached,
        };
      }
    }
  } catch (error) {
    logger.error('State fetch failed', error);

    return {
      success: false,
      error: {
        code: 'STATE_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}
