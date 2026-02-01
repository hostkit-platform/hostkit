// Structured logging for hostkit-context MCP server

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

// Global log level (can be overridden by environment)
let currentLevel: LogLevel = (process.env.LOG_LEVEL as LogLevel) || 'info';
const DEBUG = process.env.DEBUG === 'true' || process.env.DEBUG?.includes('hostkit-context');

/**
 * Format a log message with timestamp and namespace.
 */
function formatMessage(
  level: LogLevel,
  namespace: string,
  message: string,
  data?: unknown
): string {
  const timestamp = new Date().toISOString();
  const prefix = `[${timestamp}] [${level.toUpperCase()}] [${namespace}]`;

  if (data !== undefined) {
    const dataStr = typeof data === 'object' ? JSON.stringify(data) : String(data);
    return `${prefix} ${message} ${dataStr}`;
  }

  return `${prefix} ${message}`;
}

/**
 * Create a logger instance for a specific namespace.
 */
export function createLogger(namespace: string) {
  return {
    debug(message: string, data?: unknown): void {
      if (DEBUG && LEVELS[currentLevel] <= LEVELS.debug) {
        console.error(formatMessage('debug', namespace, message, data));
      }
    },

    info(message: string, data?: unknown): void {
      if (LEVELS[currentLevel] <= LEVELS.info) {
        console.error(formatMessage('info', namespace, message, data));
      }
    },

    warn(message: string, data?: unknown): void {
      if (LEVELS[currentLevel] <= LEVELS.warn) {
        console.error(formatMessage('warn', namespace, message, data));
      }
    },

    error(message: string, data?: unknown): void {
      if (LEVELS[currentLevel] <= LEVELS.error) {
        console.error(formatMessage('error', namespace, message, data));
      }
    },
  };
}

/**
 * Set the global log level.
 */
export function setLogLevel(level: LogLevel): void {
  currentLevel = level;
}

/**
 * Get the current log level.
 */
export function getLogLevel(): LogLevel {
  return currentLevel;
}
