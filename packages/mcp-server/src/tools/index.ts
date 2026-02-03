// Tool definitions and handler for hostkit-context MCP server

import type { Tool } from '@modelcontextprotocol/sdk/types.js';
import { createLogger } from '../utils/logger.js';

// Tool implementations (will be imported as they're built)
import { handleSearch } from './search.js';
import { handleState } from './state.js';
import { handleExecute } from './execute.js';
import { handlePermissions } from './permissions.js';
import { handleSolutions } from './solutions.js';
import { handleDbSchema, handleDbQuery, handleDbVerify } from './database.js';
import { handleDeployLocal } from './deploy-local.js';
import {
  handleCapabilities,
  handleWaitHealthy,
  handleEnvSet,
  handleEnvGet,
  handleValidate,
} from './convenience.js';
import { handleAuthGuide } from './auth-guide.js';

const logger = createLogger('tools');

// =============================================================================
// Tool Definitions
// =============================================================================

export const TOOLS: Tool[] = [
  {
    name: 'hostkit_search',
    description:
      'Semantic search over HostKit documentation. Returns relevant documentation chunks, commands, and examples for your query.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        query: {
          type: 'string',
          description:
            "Natural language query about HostKit (e.g., 'how do I enable payments', 'deploy with auth')",
        },
        limit: {
          type: 'number',
          description: 'Maximum results to return (default: 5)',
          default: 5,
        },
        filter: {
          type: 'string',
          enum: ['all', 'commands', 'services', 'concepts', 'examples'],
          description: 'Filter results by type',
          default: 'all',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'hostkit_state',
    description:
      'Get live VPS state including projects, health, and resources. Uses caching to reduce SSH calls.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        scope: {
          type: 'string',
          enum: ['all', 'projects', 'health', 'resources', 'project'],
          description: 'What state to retrieve',
          default: 'all',
        },
        project: {
          type: 'string',
          description: "Project name (required when scope is 'project')",
        },
        refresh: {
          type: 'boolean',
          description: 'Force cache refresh',
          default: false,
        },
      },
    },
  },
  {
    name: 'hostkit_execute',
    description:
      'Execute a HostKit command on the VPS. Validates command is allowed before execution. SSH user is determined by server configuration (HOSTKIT_PROJECT env var).',
    inputSchema: {
      type: 'object' as const,
      properties: {
        command: {
          type: 'string',
          description: "The hostkit command to execute (e.g., 'project list', 'deploy myapp')",
        },
        project: {
          type: 'string',
          description: 'Project context (for project-scoped commands). Defaults to HOSTKIT_PROJECT if configured.',
        },
        json_mode: {
          type: 'boolean',
          description: 'Add --json flag for machine-readable output',
          default: true,
        },
      },
      required: ['command'],
    },
  },
  {
    name: 'hostkit_fix_permissions',
    description:
      'Detect and fix sudoers permission gaps. Can analyze or fix specific command failures.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        action: {
          type: 'string',
          enum: ['analyze', 'fix', 'sync'],
          description:
            'analyze=check for gaps, fix=fix specific project, sync=sync all sudoers',
        },
        project: {
          type: 'string',
          description: 'Project name for fix action',
        },
        error_output: {
          type: 'string',
          description: 'The permission error output to analyze',
        },
      },
      required: ['action'],
    },
  },
  {
    name: 'hostkit_solutions',
    description:
      'Search or record cross-project solutions. Accumulates knowledge from resolved issues.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        action: {
          type: 'string',
          enum: ['search', 'record', 'list'],
          description: 'search=find solutions, record=add new solution, list=list recent',
        },
        query: {
          type: 'string',
          description: 'Search query (for search action)',
        },
        problem: {
          type: 'string',
          description: 'Problem description (for record action)',
        },
        solution: {
          type: 'string',
          description: 'Solution description (for record action)',
        },
        project: {
          type: 'string',
          description: 'Project context (for record action)',
        },
        tags: {
          type: 'array',
          items: { type: 'string' },
          description: 'Tags for categorization (for record action)',
        },
        limit: {
          type: 'number',
          description: 'Max results',
          default: 5,
        },
      },
      required: ['action'],
    },
  },
  {
    name: 'hostkit_db_schema',
    description:
      'Get database schema for a HostKit project. Returns tables, columns, indexes, and foreign keys.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        table: {
          type: 'string',
          description: 'Specific table to inspect (optional, returns all tables if not specified)',
        },
      },
    },
  },
  {
    name: 'hostkit_db_query',
    description:
      'Run a SQL query on a HostKit project database. By default, only SELECT queries are allowed. Use allow_write=true for INSERT/UPDATE/DELETE operations.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        query: {
          type: 'string',
          description: 'SQL query to execute',
        },
        limit: {
          type: 'number',
          description: 'Maximum rows to return for SELECT queries (default: 100, max: 100)',
          default: 100,
        },
        allow_write: {
          type: 'boolean',
          description: 'Enable write operations (INSERT/UPDATE/DELETE). Default: false',
          default: false,
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'hostkit_db_verify',
    description:
      'Verify database health for a HostKit project. Checks migrations, indexes, constraints, and seed data.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        checks: {
          type: 'array',
          items: {
            type: 'string',
            enum: ['migrations', 'indexes', 'constraints', 'seeded'],
          },
          description: 'Which checks to run (default: migrations, indexes, constraints)',
          default: ['migrations', 'indexes', 'constraints'],
        },
      },
    },
  },
  {
    name: 'hostkit_deploy_local',
    description:
      'Deploy local files to a HostKit project. Rsyncs files to VPS, runs deploy, and optionally waits for healthy status.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name to deploy to. Defaults to HOSTKIT_PROJECT if configured.',
        },
        local_path: {
          type: 'string',
          description: 'Path to local directory to deploy (required)',
        },
        build: {
          type: 'boolean',
          description: 'Run build step after deploying (e.g., npm run build)',
          default: false,
        },
        install: {
          type: 'boolean',
          description: 'Install dependencies after deploying (e.g., npm install)',
          default: false,
        },
        wait_healthy: {
          type: 'boolean',
          description: 'Wait for service to become healthy after deploy',
          default: true,
        },
        cleanup: {
          type: 'boolean',
          description: 'Clean up temp files on VPS after deploy',
          default: true,
        },
        override_ratelimit: {
          type: 'boolean',
          description: 'Bypass rate limit checks (use during active development)',
          default: false,
        },
      },
      required: ['local_path'],
    },
  },
  {
    name: 'hostkit_capabilities',
    description:
      'Get HostKit capabilities - all available commands, services, flags, and runtimes. Helps plan what operations are possible.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Optional project name to also fetch project-specific capabilities',
        },
      },
    },
  },
  {
    name: 'hostkit_wait_healthy',
    description:
      'Wait for a project to become healthy. Polls health endpoint until success or timeout. Useful after deployments.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        timeout: {
          type: 'number',
          description: 'Maximum wait time in milliseconds (default: 120000 = 2 minutes)',
          default: 120000,
        },
        interval: {
          type: 'number',
          description: 'Time between health checks in milliseconds (default: 5000 = 5 seconds)',
          default: 5000,
        },
      },
    },
  },
  {
    name: 'hostkit_env_set',
    description:
      'Set environment variables for a project. Can set multiple variables at once and optionally restart the service.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        variables: {
          type: 'object',
          description: 'Object of key-value pairs to set (e.g., {"AUTH_URL": "http://..."})',
          additionalProperties: { type: 'string' },
        },
        restart: {
          type: 'boolean',
          description: 'Restart service after setting variables',
          default: false,
        },
      },
      required: ['variables'],
    },
  },
  {
    name: 'hostkit_env_get',
    description:
      'Get environment variables for a project. Can get specific keys or all variables.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
        keys: {
          type: 'array',
          items: { type: 'string' },
          description: 'Specific variable names to get. If omitted, returns all variables.',
        },
      },
    },
  },
  {
    name: 'hostkit_validate',
    description:
      'Validate a project configuration. Checks entrypoint, dependencies, environment, database, services, and ports.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
      },
    },
  },
  {
    name: 'hostkit_auth_guide',
    description:
      'CRITICAL: Call this BEFORE implementing any authentication. Returns runtime-specific code examples and warnings about common auth mistakes. The auth service handles OAuth - do NOT implement it yourself.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        project: {
          type: 'string',
          description: 'Project name. Defaults to HOSTKIT_PROJECT if configured.',
        },
      },
    },
  },
];

// =============================================================================
// Tool Handler
// =============================================================================

export async function handleToolCall(
  name: string,
  args: unknown
): Promise<{ content: { type: 'text'; text: string }[]; isError?: boolean }> {
  logger.debug('Tool call', { name, args });

  try {
    let result: unknown;

    switch (name) {
      case 'hostkit_search':
        result = await handleSearch(args as Parameters<typeof handleSearch>[0]);
        break;

      case 'hostkit_state':
        result = await handleState(args as Parameters<typeof handleState>[0]);
        break;

      case 'hostkit_execute':
        result = await handleExecute(args as Parameters<typeof handleExecute>[0]);
        break;

      case 'hostkit_fix_permissions':
        result = await handlePermissions(args as Parameters<typeof handlePermissions>[0]);
        break;

      case 'hostkit_solutions':
        result = await handleSolutions(args as Parameters<typeof handleSolutions>[0]);
        break;

      case 'hostkit_db_schema':
        result = await handleDbSchema(args as Parameters<typeof handleDbSchema>[0]);
        break;

      case 'hostkit_db_query':
        result = await handleDbQuery(args as Parameters<typeof handleDbQuery>[0]);
        break;

      case 'hostkit_db_verify':
        result = await handleDbVerify(args as Parameters<typeof handleDbVerify>[0]);
        break;

      case 'hostkit_deploy_local':
        result = await handleDeployLocal(args as Parameters<typeof handleDeployLocal>[0]);
        break;

      case 'hostkit_capabilities':
        result = await handleCapabilities(args as Parameters<typeof handleCapabilities>[0]);
        break;

      case 'hostkit_wait_healthy':
        result = await handleWaitHealthy(args as Parameters<typeof handleWaitHealthy>[0]);
        break;

      case 'hostkit_env_set':
        result = await handleEnvSet(args as Parameters<typeof handleEnvSet>[0]);
        break;

      case 'hostkit_env_get':
        result = await handleEnvGet(args as Parameters<typeof handleEnvGet>[0]);
        break;

      case 'hostkit_validate':
        result = await handleValidate(args as Parameters<typeof handleValidate>[0]);
        break;

      case 'hostkit_auth_guide':
        result = await handleAuthGuide(args as Parameters<typeof handleAuthGuide>[0]);
        break;

      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify(result, null, 2),
        },
      ],
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error(`Tool error: ${name}`, error);

    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            success: false,
            error: {
              code: 'TOOL_ERROR',
              message,
            },
          }),
        },
      ],
      isError: true,
    };
  }
}
