// SSH Manager for hostkit-context MCP server

import { Client, type ConnectConfig } from 'ssh2';
import { readFileSync } from 'fs';
import { getConfig } from '../config.js';
import { createLogger } from '../utils/logger.js';

const logger = createLogger('ssh');

// Connection pool (reuse connections)
const connectionPool: Map<string, Client> = new Map();

// Connection timeout
const CONNECTION_TIMEOUT = 30000; // 30 seconds
const COMMAND_TIMEOUT = 60000; // 60 seconds

/**
 * Get SSH connection configuration.
 */
function getSSHConfig(user?: string): ConnectConfig {
  const config = getConfig();

  return {
    host: config.vps.host,
    port: config.vps.port,
    username: user || config.vps.user,
    privateKey: readFileSync(config.vps.keyPath),
    readyTimeout: CONNECTION_TIMEOUT,
  };
}

/**
 * Get or create an SSH connection.
 */
async function getConnection(user: string): Promise<Client> {
  const existing = connectionPool.get(user);

  // Reuse existing connection if still connected
  if (existing) {
    // Check if connection is still alive by testing with a simple command
    try {
      await executeOnClient(existing, 'echo ok');
      return existing;
    } catch {
      // Connection dead, remove from pool
      connectionPool.delete(user);
      existing.end();
    }
  }

  // Create new connection
  const client = new Client();
  const sshConfig = getSSHConfig(user);

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      client.end();
      reject(new Error('SSH connection timeout'));
    }, CONNECTION_TIMEOUT);

    client.on('ready', () => {
      clearTimeout(timeout);
      connectionPool.set(user, client);
      logger.info(`SSH connected as ${user}`);
      resolve(client);
    });

    client.on('error', (err) => {
      clearTimeout(timeout);
      connectionPool.delete(user);
      logger.error(`SSH error for ${user}`, err.message);
      reject(err);
    });

    client.on('close', () => {
      connectionPool.delete(user);
      logger.debug(`SSH connection closed for ${user}`);
    });

    client.connect(sshConfig);
  });
}

/**
 * Execute a command on an SSH client.
 */
async function executeOnClient(client: Client, command: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error('SSH command timeout'));
    }, COMMAND_TIMEOUT);

    client.exec(command, (err, stream) => {
      if (err) {
        clearTimeout(timeout);
        reject(err);
        return;
      }

      let stdout = '';
      let stderr = '';

      stream.on('data', (data: Buffer) => {
        stdout += data.toString();
      });

      stream.stderr.on('data', (data: Buffer) => {
        stderr += data.toString();
      });

      stream.on('close', (code: number) => {
        clearTimeout(timeout);

        if (code !== 0) {
          const error = new Error(`Command failed with code ${code}: ${stderr || stdout}`);
          (error as Error & { code: number; stderr: string }).code = code;
          (error as Error & { code: number; stderr: string }).stderr = stderr;
          reject(error);
        } else {
          resolve(stdout);
        }
      });
    });
  });
}

/**
 * SSH Manager class for executing commands on the VPS.
 */
export class SSHManager {
  /**
   * Execute a raw command on the VPS.
   */
  async execute(command: string, user?: string): Promise<string> {
    const config = getConfig();
    const sshUser = user || config.vps.user;

    logger.debug(`Executing: ${command} (as ${sshUser})`);

    const client = await getConnection(sshUser);
    const result = await executeOnClient(client, command);

    return result;
  }

  /**
   * Execute a HostKit command on the VPS.
   */
  async executeHostkit(
    command: string,
    options: {
      project?: string;
      user?: 'ai-operator' | 'project';
      json?: boolean;
    } = {}
  ): Promise<unknown> {
    const { project, user = 'ai-operator', json = true } = options;

    // Determine SSH user
    const sshUser = user === 'project' && project ? project : 'ai-operator';

    // Build command
    const jsonFlag = json ? '--json ' : '';
    const fullCommand = `sudo hostkit ${jsonFlag}${command}`;

    logger.info(`HostKit: ${command}`, { user: sshUser, json });

    const output = await this.execute(fullCommand, sshUser);

    // Parse JSON if requested
    if (json) {
      try {
        // Try parsing the full output first
        return JSON.parse(output.trim());
      } catch {
        // If that fails, try to find the last JSON object in the output
        // npm install, build commands produce non-JSON output before the final JSON
        const jsonMatch = output.match(/\{[\s\S]*\}(?=[^}]*$)/);
        if (jsonMatch) {
          try {
            return JSON.parse(jsonMatch[0]);
          } catch {
            // Fall through to error handling
          }
        }

        // If we still can't parse, check if this was an install/build command
        // and return a structured response instead of throwing
        if (output.includes('added') && output.includes('packages')) {
          return {
            _parsed: false,
            _rawOutput: output.trim(),
            success: true,
            message: 'Command completed (npm output detected)'
          };
        }

        logger.warn('Failed to parse JSON output', output.substring(0, 200));
        throw new Error(`Failed to parse HostKit JSON output. Raw output starts with: ${output.substring(0, 100)}`);
      }
    }

    return output;
  }

  /**
   * Close all SSH connections.
   */
  closeAll(): void {
    for (const [user, client] of connectionPool) {
      client.end();
      logger.debug(`Closed connection for ${user}`);
    }
    connectionPool.clear();
  }
}

// Singleton instance
let sshManager: SSHManager | null = null;

/**
 * Get the SSH manager instance.
 */
export function getSSHManager(): SSHManager {
  if (!sshManager) {
    sshManager = new SSHManager();
  }
  return sshManager;
}

/**
 * Close all SSH connections (for cleanup).
 */
export function closeSSHConnections(): void {
  if (sshManager) {
    sshManager.closeAll();
  }
}
