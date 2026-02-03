// hostkit_fix_permissions tool implementation

import { getSSHManager } from '../services/ssh.js';
import { getProjectContext } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { PermissionsParams, PermissionGap, ToolResponse } from '../types.js';

const logger = createLogger('tools:permissions');

// Known permission gaps (from codebase analysis)
const KNOWN_GAPS: PermissionGap[] = [
  {
    command: 'chatbot enable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'chatbot disable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'chatbot status',
    scope: 'PROJECT_READ',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'chatbot config',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'chatbot stats',
    scope: 'PROJECT_READ',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'chatbot logs',
    scope: 'PROJECT_READ',
    suggestion: 'Add chatbot entries to sudoers.j2',
  },
  {
    command: 'vector enable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add vector entries to sudoers.j2',
  },
  {
    command: 'vector disable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add vector entries to sudoers.j2',
  },
  {
    command: 'r2 enable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add r2 entries to sudoers.j2',
  },
  {
    command: 'r2 disable',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add r2 entries to sudoers.j2',
  },
  {
    command: 'r2 credentials',
    scope: 'PROJECT_READ',
    suggestion: 'Add r2 credentials entry to sudoers.j2',
  },
  {
    command: 'backup r2 sync',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add backup r2 entries to sudoers.j2',
  },
  {
    command: 'backup r2 list',
    scope: 'PROJECT_READ',
    suggestion: 'Add backup r2 entries to sudoers.j2',
  },
  {
    command: 'backup r2 rotate',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add backup r2 entries to sudoers.j2',
  },
  {
    command: 'backup r2 download',
    scope: 'PROJECT_SCOPED',
    suggestion: 'Add backup r2 entries to sudoers.j2',
  },
];

/**
 * Parse an error output to extract the denied command.
 */
function parsePermissionError(errorOutput: string): string | null {
  // Pattern: "sudo: ... not allowed to run '...'"
  const match = errorOutput.match(/sudo:\s+\S+\s+:\s+.*not allowed to run\s+'([^']+)'/i);
  if (match) {
    return match[1];
  }

  // Pattern: "permission denied" with command context
  const cmdMatch = errorOutput.match(/hostkit\s+(.+?)(?:\s+--|\s*$)/i);
  if (cmdMatch) {
    return cmdMatch[1];
  }

  return null;
}

/**
 * Handle hostkit_fix_permissions tool calls.
 */
export async function handlePermissions(params: PermissionsParams): Promise<ToolResponse> {
  const { action, error_output } = params;
  const project = params.project || getProjectContext();

  logger.info('Permissions request', { action, project });

  try {
    switch (action) {
      case 'analyze': {
        // Analyze for permission gaps
        const gaps: PermissionGap[] = [...KNOWN_GAPS];

        // If error output provided, extract specific gap
        if (error_output) {
          const deniedCommand = parsePermissionError(error_output);
          if (deniedCommand) {
            gaps.unshift({
              command: deniedCommand,
              scope: 'unknown',
              project,
              suggestion: `Add sudoers entry for: ${deniedCommand}`,
            });
          }
        }

        return {
          success: true,
          data: {
            gaps,
            totalGaps: gaps.length,
            recommendation:
              gaps.length > 0
                ? 'Run `hostkit permissions sync` on VPS to fix these gaps'
                : 'No permission gaps detected',
          },
        };
      }

      case 'fix': {
        if (!project) {
          return {
            success: false,
            error: {
              code: 'MISSING_PROJECT',
              message: 'Project name required for fix action',
            },
          };
        }

        // Execute permissions sync for specific project
        const ssh = getSSHManager();

        try {
          const result = await ssh.executeHostkit(`permissions sync --project ${project}`);
          return {
            success: true,
            data: {
              project,
              result,
              message: `Permissions synced for project: ${project}`,
            },
          };
        } catch (error) {
          // If permissions command doesn't exist yet, provide instructions
          const message = error instanceof Error ? error.message : String(error);
          if (message.includes('No such command') || message.includes('not found')) {
            return {
              success: false,
              error: {
                code: 'COMMAND_NOT_FOUND',
                message:
                  'The `hostkit permissions` command is not yet installed on the VPS. ' +
                  'This command needs to be added to HostKit first.',
                details: {
                  requiredFile: '/Users/ryanchappell/Documents/**HostKit**/src/hostkit/commands/permissions.py',
                  workaround:
                    'Update sudoers.j2 template and manually regenerate sudoers for the project',
                },
              },
            };
          }
          throw error;
        }
      }

      case 'sync': {
        // Execute full permissions sync
        const ssh = getSSHManager();

        try {
          const result = await ssh.executeHostkit('permissions sync --all');
          return {
            success: true,
            data: {
              result,
              message: 'Permissions synced for all projects',
            },
          };
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (message.includes('No such command') || message.includes('not found')) {
            return {
              success: false,
              error: {
                code: 'COMMAND_NOT_FOUND',
                message:
                  'The `hostkit permissions` command is not yet installed on the VPS.',
                details: {
                  recommendation: 'First add the permissions command to HostKit CLI',
                },
              },
            };
          }
          throw error;
        }
      }

      default:
        return {
          success: false,
          error: {
            code: 'INVALID_ACTION',
            message: `Unknown action: ${action}. Use analyze, fix, or sync.`,
          },
        };
    }
  } catch (error) {
    logger.error('Permissions operation failed', error);

    return {
      success: false,
      error: {
        code: 'PERMISSIONS_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}
