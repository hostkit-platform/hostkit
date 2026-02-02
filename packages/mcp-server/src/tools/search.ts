// hostkit_search tool implementation

import { createLogger } from '../utils/logger.js';
import { embedQuery } from '../embeddings.js';
import type { SearchParams, SearchResult, ToolResponse, DocChunk } from '../types.js';

const logger = createLogger('tools:search');

// Placeholder for search index (will be populated by indexer)
let searchIndex: {
  chunks: DocChunk[];
  embeddings: number[][];
  tfidfIndex: Map<string, Map<string, number>>;
} | null = null;

/**
 * Initialize the search index.
 * Called during server startup.
 */
export function initializeSearchIndex(index: {
  chunks: DocChunk[];
  embeddings: number[][];
  tfidfIndex: Map<string, Map<string, number>>;
}): void {
  searchIndex = index;
  logger.info(`Search index initialized with ${index.chunks.length} chunks`);
}

/**
 * Check if search index is available.
 */
export function isSearchIndexReady(): boolean {
  return searchIndex !== null && searchIndex.chunks.length > 0;
}

/**
 * Compute cosine similarity between two vectors.
 */
function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) return 0;

  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dotProduct / denom;
}

/**
 * Tokenize text for TF-IDF.
 */
function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 2);
}

/**
 * Compute TF-IDF score for a query against chunks.
 */
function computeTfidfScores(
  queryTokens: string[],
  tfidfIndex: Map<string, Map<string, number>>
): Map<string, number> {
  const scores = new Map<string, number>();

  for (const token of queryTokens) {
    const chunkScores = tfidfIndex.get(token);
    if (chunkScores) {
      for (const [chunkId, score] of chunkScores) {
        scores.set(chunkId, (scores.get(chunkId) || 0) + score);
      }
    }
  }

  return scores;
}

/**
 * Get query embedding via Transformers.js.
 * Falls back to keyword-only search if embedding fails.
 */
async function getQueryEmbedding(query: string): Promise<number[] | null> {
  try {
    return await embedQuery(query);
  } catch (error) {
    logger.warn('Embedding failed, falling back to keyword search', error);
    return null;
  }
}

/**
 * Handle hostkit_search tool calls.
 */
export async function handleSearch(params: SearchParams): Promise<ToolResponse> {
  const { query, limit = 5, filter = 'all' } = params;

  logger.info('Search request', { query, limit, filter });

  if (!searchIndex) {
    return {
      success: false,
      error: {
        code: 'INDEX_NOT_READY',
        message:
          'Search index not initialized. Run `npm run sync` to generate the index.',
      },
    };
  }

  try {
    const queryTokens = tokenize(query);

    // Get TF-IDF scores
    const tfidfScores = computeTfidfScores(queryTokens, searchIndex.tfidfIndex);

    // Try to get semantic scores
    let queryEmbedding = await getQueryEmbedding(query);
    let semanticScores: Map<string, number> = new Map();

    if (queryEmbedding && searchIndex.embeddings.length > 0) {
      for (let i = 0; i < searchIndex.chunks.length; i++) {
        const chunk = searchIndex.chunks[i];
        const embedding = searchIndex.embeddings[i];
        if (embedding) {
          const similarity = cosineSimilarity(queryEmbedding, embedding);
          semanticScores.set(chunk.id, similarity);
        }
      }
    }

    // Compute hybrid scores and filter
    const semanticAvailable = queryEmbedding !== null && semanticScores.size > 0;
    const results: SearchResult[] = [];

    for (const chunk of searchIndex.chunks) {
      // Apply filter
      if (filter !== 'all') {
        const typeMap: Record<string, string> = {
          commands: 'command',
          services: 'service',
          concepts: 'concept',
          examples: 'example',
        };
        if (chunk.chunkType !== typeMap[filter]) {
          continue;
        }
      }

      const semantic = semanticScores.get(chunk.id) || 0;
      const tfidf = tfidfScores.get(chunk.id) || 0;

      // Skip if no match at all
      if (semantic === 0 && tfidf === 0) {
        continue;
      }

      results.push({
        chunk,
        semanticScore: semantic,
        tfidfScore: tfidf,
        hybridScore: 0, // computed after normalization below
      });
    }

    // Normalize TF-IDF scores per-query to 0-1 range
    const maxTfidf = Math.max(...results.map((r) => r.tfidfScore), 0.001);

    for (const result of results) {
      const normTfidf = result.tfidfScore / maxTfidf;
      result.hybridScore = semanticAvailable
        ? 0.7 * result.semanticScore + 0.3 * normTfidf
        : normTfidf; // full weight to TF-IDF when no semantic
    }

    // Sort by hybrid score and limit
    results.sort((a, b) => b.hybridScore - a.hybridScore);
    const topResults = results.slice(0, limit);

    // Format for response
    const formattedResults = topResults.map((r) => ({
      id: r.chunk.id,
      title: r.chunk.title,
      section: r.chunk.section,
      type: r.chunk.chunkType,
      content: r.chunk.content.substring(0, 500) + (r.chunk.content.length > 500 ? '...' : ''),
      score: Math.round(r.hybridScore * 100) / 100,
      matchType:
        r.semanticScore > 0.3 ? 'semantic' : r.tfidfScore > 0 ? 'keyword' : 'none',
    }));

    return {
      success: true,
      data: {
        query,
        results: formattedResults,
        totalMatches: results.length,
        indexSize: searchIndex.chunks.length,
      },
    };
  } catch (error) {
    logger.error('Search failed', error);

    return {
      success: false,
      error: {
        code: 'SEARCH_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}
