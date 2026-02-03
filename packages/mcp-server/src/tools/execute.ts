// hostkit_execute tool implementation

import { getSSHManager } from '../services/ssh.js';
import { getProjectContext, isProjectMode } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { ExecuteParams, ToolResponse } from '../types.js';

const logger = createLogger('tools:execute');

// Commands that only work as ai-operator (cross-project or system-level)
const OPERATOR_ONLY_PATTERNS = [
  /^project\s+create\b/,
  /^project\s+list\b/,
  /^project\s+delete\b/,
  /^status\b/,
];

// Commands that are explicitly forbidden for safety
const FORBIDDEN_COMMANDS = [
  'project delete',
  'db delete',
  'redis delete',
  'dns remove',
  'mail remove-domain',
];

// Commands that require specific user context
const PROJECT_SCOPED_PATTERNS = [
  /^deploy\s+\S+/,
  /^rollback\s+\S+/,
  /^health\s+\S+/,
  /^service\s+\w+\s+\S+/,
  /^env\s+\w+\s+\S+/,
  /^payments\s+\w+\s+\S+/,
  /^sms\s+\w+\s+\S+/,
  /^chatbot\s+\w+\s+\S+/,
  /^r2\s+\w+\s+\S+/,
  /^vector\s+\w+\s+\S+/,
  /^booking\s+\w+\s+\S+/,
  /^voice\s+\w+\s+\S+/,
  /^mail\s+\w+\s+\S+/,
];

/**
 * Validate a command before execution.
 */
function validateCommand(command: string): { valid: boolean; error?: string } {
  const normalizedCmd = command.trim().toLowerCase();

  // Check forbidden commands
  for (const forbidden of FORBIDDEN_COMMANDS) {
    if (normalizedCmd.startsWith(forbidden)) {
      return {
        valid: false,
        error: `Command "${forbidden}" is forbidden via MCP. Use HostKit CLI directly with --force flag if needed.`,
      };
    }
  }

  // Check for shell injection attempts
  if (/[;&|`$()]/.test(command)) {
    return {
      valid: false,
      error: 'Command contains potentially dangerous characters',
    };
  }

  return { valid: true };
}

/**
 * Extract project name from a command if present.
 */
function extractProjectFromCommand(command: string): string | undefined {
  // Common patterns: "deploy myapp", "health myapp", "chatbot enable myapp"
  const parts = command.trim().split(/\s+/);

  // Check if command matches project-scoped patterns
  for (const pattern of PROJECT_SCOPED_PATTERNS) {
    if (pattern.test(command)) {
      // For most patterns, project is the last word or after enable/disable/status
      const actionWords = ['enable', 'disable', 'status', 'config', 'logs'];
      for (let i = 0; i < parts.length; i++) {
        if (actionWords.includes(parts[i]) && parts[i + 1]) {
          return parts[i + 1];
        }
      }
      // Otherwise, try the second word (for deploy, rollback, health)
      if (parts.length >= 2) {
        return parts[1];
      }
    }
  }

  return undefined;
}

/**
 * Handle hostkit_execute tool calls.
 */
export async function handleExecute(params: ExecuteParams): Promise<ToolResponse> {
  const { command, project, json_mode = true } = params;
  const configuredProject = getProjectContext();

  logger.info('Execute request', { command, project, json_mode, projectMode: isProjectMode() });

  // Validate command
  const validation = validateCommand(command);
  if (!validation.valid) {
    return {
      success: false,
      error: {
        code: 'INVALID_COMMAND',
        message: validation.error!,
      },
    };
  }

  // Project mode safety checks
  if (configuredProject) {
    const normalizedCmd = command.trim();

    // Block operator-only commands
    for (const pattern of OPERATOR_ONLY_PATTERNS) {
      if (pattern.test(normalizedCmd)) {
        return {
          success: false,
          error: {
            code: 'OPERATOR_ONLY',
            message: `This command requires operator access and is not available in project mode. Ask the HostKit substrate agent to run this for you.`,
          },
        };
      }
    }

    // Validate project-scoped commands target the configured project
    const extractedProject = extractProjectFromCommand(command);
    if (extractedProject && extractedProject !== configuredProject) {
      return {
        success: false,
        error: {
          code: 'CROSS_PROJECT_BLOCKED',
          message: `Cannot target project '${extractedProject}' — this MCP server is scoped to '${configuredProject}'.`,
        },
      };
    }
  }

  // Determine project context: explicit param > extracted from command > configured default
  const projectContext = project || extractProjectFromCommand(command) || configuredProject;

  try {
    const ssh = getSSHManager();

    const result = await ssh.executeHostkit(command, {
      project: projectContext,
      json: json_mode,
    });

    return {
      success: true,
      data: result,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);

    // Check if this is a permission error
    const isPermissionError =
      message.includes('not allowed') ||
      message.includes('permission denied') ||
      message.includes('sudo:');

    logger.error('Execute failed', { command, error: message });

    return {
      success: false,
      error: {
        code: isPermissionError ? 'PERMISSION_DENIED' : 'EXECUTE_ERROR',
        message,
        details: isPermissionError
          ? {
              suggestion:
                'Use hostkit_fix_permissions to analyze and fix this permission gap',
              project: projectContext,
            }
          : undefined,
      },
    };
  }
}
