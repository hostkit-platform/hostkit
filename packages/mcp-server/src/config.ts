// Configuration management for hostkit-context MCP server

import { readFile } from 'fs/promises';
import { existsSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';
import type { Config } from './types.js';

// Default configuration
const DEFAULT_CONFIG: Config = {
  vps: {
    host: process.env.HOSTKIT_VPS_HOST || '',
    port: parseInt(process.env.HOSTKIT_VPS_PORT || '22', 10),
    user: process.env.HOSTKIT_SSH_USER || 'ai-operator',
    keyPath: (process.env.HOSTKIT_SSH_KEY_PATH || '~/.ssh/id_ed25519').replace(
      '~',
      homedir()
    ),
  },
  dataDir: (process.env.HOSTKIT_CONTEXT_DIR || '~/.hostkit-context').replace(
    '~',
    homedir()
  ),
  cache: {
    projectsTtl: parseInt(process.env.CACHE_TTL_PROJECTS || '60000', 10),
    healthTtl: parseInt(process.env.CACHE_TTL_HEALTH || '30000', 10),
    projectTtl: parseInt(process.env.CACHE_TTL_PROJECT || '30000', 10),
  },
  logging: {
    level: (process.env.LOG_LEVEL as Config['logging']['level']) || 'info',
    debug: process.env.DEBUG === 'true',
  },
};

// Singleton config instance
let cachedConfig: Config | null = null;

/**
 * Load configuration from file and environment.
 * Priority: env vars > config file > defaults
 */
export async function loadConfig(): Promise<Config> {
  if (cachedConfig) {
    return cachedConfig;
  }

  // Start with defaults
  let config = { ...DEFAULT_CONFIG };

  // Try to load from config file
  const configPath = join(config.dataDir, 'config.json');
  if (existsSync(configPath)) {
    try {
      const content = await readFile(configPath, 'utf-8');
      const fileConfig = JSON.parse(content);
      config = deepMerge(config, fileConfig);

      // Expand ~ in paths after loading
      if (config.vps.keyPath.startsWith('~')) {
        config.vps.keyPath = config.vps.keyPath.replace('~', homedir());
      }
      if (config.dataDir.startsWith('~')) {
        config.dataDir = config.dataDir.replace('~', homedir());
      }
    } catch {
      // Ignore config file errors, use defaults
    }
  }

  cachedConfig = config;
  return config;
}

/**
 * Get the current configuration (must call loadConfig first).
 */
export function getConfig(): Config {
  if (!cachedConfig) {
    throw new Error('Configuration not loaded. Call loadConfig() first.');
  }
  return cachedConfig;
}

/**
 * Get a specific config path.
 */
export function getDataPath(...segments: string[]): string {
  const config = getConfig();
  return join(config.dataDir, ...segments);
}

/**
 * Deep merge two objects.
 */
function deepMerge<T extends Record<string, unknown>>(target: T, source: Partial<T>): T {
  const result = { ...target };

  for (const key of Object.keys(source) as (keyof T)[]) {
    const sourceValue = source[key];
    const targetValue = target[key];

    if (
      sourceValue &&
      typeof sourceValue === 'object' &&
      !Array.isArray(sourceValue) &&
      targetValue &&
      typeof targetValue === 'object' &&
      !Array.isArray(targetValue)
    ) {
      result[key] = deepMerge(
        targetValue as Record<string, unknown>,
        sourceValue as Record<string, unknown>
      ) as T[keyof T];
    } else if (sourceValue !== undefined) {
      result[key] = sourceValue as T[keyof T];
    }
  }

  return result;
}

/**
 * Clear the cached config (for testing).
 */
export function clearConfigCache(): void {
  cachedConfig = null;
}
