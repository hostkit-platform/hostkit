// LRU Cache with TTL for hostkit-context MCP server

import { createLogger } from '../utils/logger.js';

const logger = createLogger('cache');

interface CacheEntry<T> {
  value: T;
  timestamp: number;
  ttl: number;
}

/**
 * LRU Cache with per-entry TTL support.
 */
export class LRUCache<T> {
  private cache: Map<string, CacheEntry<T>> = new Map();
  private maxSize: number;

  constructor(maxSize: number = 100) {
    this.maxSize = maxSize;
  }

  /**
   * Get a value from the cache.
   * Returns undefined if not found or expired.
   */
  get(key: string): T | undefined {
    const entry = this.cache.get(key);

    if (!entry) {
      return undefined;
    }

    // Check TTL expiry
    if (Date.now() - entry.timestamp > entry.ttl) {
      this.cache.delete(key);
      logger.debug(`Cache miss (expired): ${key}`);
      return undefined;
    }

    // Move to end (most recently used)
    this.cache.delete(key);
    this.cache.set(key, entry);

    logger.debug(`Cache hit: ${key}`);
    return entry.value;
  }

  /**
   * Set a value in the cache with TTL.
   */
  set(key: string, value: T, ttl: number): void {
    // Evict oldest if at capacity
    if (this.cache.size >= this.maxSize && !this.cache.has(key)) {
      const oldestKey = this.cache.keys().next().value;
      if (oldestKey) {
        this.cache.delete(oldestKey);
        logger.debug(`Cache evict (LRU): ${oldestKey}`);
      }
    }

    this.cache.set(key, {
      value,
      timestamp: Date.now(),
      ttl,
    });

    logger.debug(`Cache set: ${key} (TTL: ${ttl}ms)`);
  }

  /**
   * Check if a key exists and is not expired.
   */
  has(key: string): boolean {
    const entry = this.cache.get(key);

    if (!entry) {
      return false;
    }

    if (Date.now() - entry.timestamp > entry.ttl) {
      this.cache.delete(key);
      return false;
    }

    return true;
  }

  /**
   * Delete a key from the cache.
   */
  delete(key: string): boolean {
    return this.cache.delete(key);
  }

  /**
   * Clear all entries from the cache.
   */
  clear(): void {
    this.cache.clear();
    logger.debug('Cache cleared');
  }

  /**
   * Get cache statistics.
   */
  stats(): { size: number; maxSize: number } {
    return {
      size: this.cache.size,
      maxSize: this.maxSize,
    };
  }

  /**
   * Get entry metadata (for debugging).
   */
  getMetadata(key: string): { age: number; ttl: number; expired: boolean } | null {
    const entry = this.cache.get(key);

    if (!entry) {
      return null;
    }

    const age = Date.now() - entry.timestamp;
    return {
      age,
      ttl: entry.ttl,
      expired: age > entry.ttl,
    };
  }
}

// Global cache instances
let stateCache: LRUCache<unknown> | null = null;
let queryCache: LRUCache<number[]> | null = null;

/**
 * Get the state cache instance.
 */
export function getStateCache(): LRUCache<unknown> {
  if (!stateCache) {
    stateCache = new LRUCache<unknown>(50);
  }
  return stateCache;
}

/**
 * Get the query embedding cache instance.
 */
export function getQueryCache(): LRUCache<number[]> {
  if (!queryCache) {
    queryCache = new LRUCache<number[]>(100);
  }
  return queryCache;
}

/**
 * Helper to get cached or fetch data.
 */
export async function getCachedOrFetch<T>(
  cache: LRUCache<T>,
  key: string,
  ttl: number,
  refresh: boolean,
  fetcher: () => Promise<T>
): Promise<{ data: T; cached: boolean; cachedAt?: string }> {
  // Check cache first (unless refresh requested)
  if (!refresh) {
    const cached = cache.get(key) as T | undefined;
    if (cached !== undefined) {
      const metadata = cache.getMetadata(key);
      return {
        data: cached,
        cached: true,
        cachedAt: metadata ? new Date(Date.now() - metadata.age).toISOString() : undefined,
      };
    }
  }

  // Fetch fresh data
  const data = await fetcher();
  cache.set(key, data, ttl);

  return { data, cached: false };
}
