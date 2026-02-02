// JS embedding module using Transformers.js (replaces Python subprocess)
// Uses Xenova/all-MiniLM-L6-v2 (ONNX, CPU-only, 384-dim)

import { createLogger } from './utils/logger.js';

const logger = createLogger('embeddings');

let embedder: any = null;

/**
 * Lazy-load the embedding pipeline.
 * Model downloads to HF cache on first use (~30MB ONNX).
 */
async function getEmbedder() {
  if (!embedder) {
    logger.info('Loading embedding model (first call)...');
    const { pipeline } = await import('@huggingface/transformers');
    embedder = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', {
      dtype: 'fp32',
    });
    logger.info('Embedding model loaded');
  }
  return embedder;
}

/**
 * Generate embedding for a single query string.
 * Returns a 384-dimensional float array.
 */
export async function embedQuery(text: string): Promise<number[]> {
  const pipe = await getEmbedder();
  const output = await pipe(text, { pooling: 'mean', normalize: true });
  return Array.from(output.data as Float32Array);
}

/**
 * Generate embeddings for multiple documents.
 * Returns an array of 384-dimensional float arrays.
 */
export async function embedDocuments(texts: string[]): Promise<number[][]> {
  const pipe = await getEmbedder();
  const results: number[][] = [];

  // Process in batches to manage memory
  const batchSize = 32;
  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize);
    logger.info(`Embedding batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(texts.length / batchSize)}`);

    for (const text of batch) {
      const output = await pipe(text, { pooling: 'mean', normalize: true });
      results.push(Array.from(output.data as Float32Array));
    }
  }

  return results;
}
