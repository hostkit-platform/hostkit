// Documentation indexer for hostkit-context MCP server

import { readFile, writeFile, mkdir } from 'fs/promises';
import { existsSync } from 'fs';
import { join } from 'path';
import { getDataPath } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { DocChunk, ChunkType } from '../types.js';

const logger = createLogger('indexer:claudemd');

// Paths to HostKit documentation
const DOC_PATHS = [
  '/Users/ryanchappell/Agents/HostKit-Agent/CLAUDE.md',
  '/Users/ryanchappell/Agents/HostKit-Agent/docs/SERVICES.md',
];

// Chunk size limits
const MAX_CHUNK_SIZE = 3000;
const MIN_CHUNK_SIZE = 100;

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
 * Detect chunk type from title and content.
 */
function detectChunkType(title: string, content: string): ChunkType {
  const titleLower = title.toLowerCase();
  const contentLower = content.toLowerCase();

  if (
    titleLower.includes('service') ||
    titleLower.includes('auth') ||
    titleLower.includes('payment') ||
    titleLower.includes('sms') ||
    titleLower.includes('voice') ||
    titleLower.includes('booking') ||
    titleLower.includes('chatbot') ||
    titleLower.includes('r2') ||
    titleLower.includes('vector')
  ) {
    return 'service';
  }

  if (
    titleLower.includes('command') ||
    contentLower.includes('hostkit ') ||
    contentLower.includes('```bash')
  ) {
    return 'command';
  }

  if (
    contentLower.includes('example') ||
    contentLower.includes('```') ||
    titleLower.includes('example')
  ) {
    return 'example';
  }

  return 'concept';
}

/**
 * Generate a chunk ID from title.
 */
function generateChunkId(title: string, index: number): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .substring(0, 50);

  return `${slug}-${index}`;
}

/**
 * Split content into smaller chunks if needed.
 */
function splitLargeContent(content: string, maxSize: number): string[] {
  if (content.length <= maxSize) {
    return [content];
  }

  const chunks: string[] = [];
  const paragraphs = content.split(/\n\n+/);

  let currentChunk = '';

  for (const para of paragraphs) {
    if (currentChunk.length + para.length + 2 > maxSize) {
      if (currentChunk.length >= MIN_CHUNK_SIZE) {
        chunks.push(currentChunk.trim());
        currentChunk = '';
      }
    }
    currentChunk += (currentChunk ? '\n\n' : '') + para;
  }

  if (currentChunk.length >= MIN_CHUNK_SIZE) {
    chunks.push(currentChunk.trim());
  }

  return chunks;
}

/**
 * Parse markdown documentation into chunks.
 */
export function parseClaudeMd(content: string, source: string = 'CLAUDE.md'): DocChunk[] {
  const chunks: DocChunk[] = [];
  let chunkIndex = 0;

  // Split by ## and ### headers
  const headerPattern = /^(#{2,3})\s+(.+?)$/gm;
  const sections: { level: number; title: string; content: string; start: number }[] = [];

  let lastIndex = 0;
  let match;

  while ((match = headerPattern.exec(content)) !== null) {
    // Save previous section content
    if (sections.length > 0) {
      const prevSection = sections[sections.length - 1];
      prevSection.content = content.substring(prevSection.start, match.index).trim();
    }

    sections.push({
      level: match[1].length,
      title: match[2],
      content: '',
      start: match.index + match[0].length,
    });

    lastIndex = match.index + match[0].length;
  }

  // Don't forget the last section
  if (sections.length > 0) {
    const lastSection = sections[sections.length - 1];
    lastSection.content = content.substring(lastSection.start).trim();
  }

  // Build parent section context
  let currentL2Section = '';

  for (const section of sections) {
    if (section.level === 2) {
      currentL2Section = section.title;
    }

    // Skip empty sections
    if (!section.content || section.content.length < MIN_CHUNK_SIZE) {
      continue;
    }

    // Split large sections
    const contentChunks = splitLargeContent(section.content, MAX_CHUNK_SIZE);

    for (const chunkContent of contentChunks) {
      const chunkType = detectChunkType(section.title, chunkContent);
      const tokens = tokenize(`${section.title} ${chunkContent}`);

      chunks.push({
        id: generateChunkId(section.title, chunkIndex++),
        title: section.title,
        content: chunkContent,
        section: currentL2Section || section.title,
        chunkType,
        tokens,
        source,
      });
    }
  }

  logger.info(`Parsed ${chunks.length} chunks from ${source}`);
  return chunks;
}

/**
 * Build TF-IDF index from chunks.
 */
export function buildTfidfIndex(chunks: DocChunk[]): Map<string, Map<string, number>> {
  const index = new Map<string, Map<string, number>>();

  // Document frequency for each term
  const docFreq = new Map<string, number>();

  // Count document frequency
  for (const chunk of chunks) {
    const uniqueTerms = new Set(chunk.tokens);
    for (const term of uniqueTerms) {
      docFreq.set(term, (docFreq.get(term) || 0) + 1);
    }
  }

  const totalDocs = chunks.length;

  // Calculate TF-IDF for each term in each document
  for (const chunk of chunks) {
    const termFreq = new Map<string, number>();

    // Count term frequency in this chunk
    for (const token of chunk.tokens) {
      termFreq.set(token, (termFreq.get(token) || 0) + 1);
    }

    // Calculate TF-IDF
    for (const [term, tf] of termFreq) {
      const df = docFreq.get(term) || 1;
      const idf = Math.log(totalDocs / df);
      const tfidf = (tf / chunk.tokens.length) * idf;

      if (!index.has(term)) {
        index.set(term, new Map());
      }
      index.get(term)!.set(chunk.id, tfidf);
    }
  }

  logger.info(`Built TF-IDF index with ${index.size} terms`);
  return index;
}

/**
 * Load the search index from disk.
 */
export async function loadSearchIndex(): Promise<{
  chunks: DocChunk[];
  embeddings: number[][];
  tfidfIndex: Map<string, Map<string, number>>;
} | null> {
  const indexDir = getDataPath('index');

  const chunksPath = join(indexDir, 'chunks.json');
  const embeddingsPath = join(indexDir, 'embeddings.json');
  const tfidfPath = join(indexDir, 'tfidf.json');

  // Check if index files exist
  if (!existsSync(chunksPath)) {
    logger.warn('Chunks file not found');
    return null;
  }

  try {
    // Load chunks
    const chunksContent = await readFile(chunksPath, 'utf-8');
    const chunks = JSON.parse(chunksContent) as DocChunk[];

    // Load embeddings (may not exist yet)
    let embeddings: number[][] = [];
    if (existsSync(embeddingsPath)) {
      const embContent = await readFile(embeddingsPath, 'utf-8');
      embeddings = JSON.parse(embContent);
    }

    // Load TF-IDF index
    let tfidfIndex = new Map<string, Map<string, number>>();
    if (existsSync(tfidfPath)) {
      const tfidfContent = await readFile(tfidfPath, 'utf-8');
      const tfidfObj = JSON.parse(tfidfContent) as Record<string, Record<string, number>>;

      for (const [term, docs] of Object.entries(tfidfObj)) {
        tfidfIndex.set(term, new Map(Object.entries(docs)));
      }
    }

    return { chunks, embeddings, tfidfIndex };
  } catch (error) {
    logger.error('Failed to load search index', error);
    return null;
  }
}

/**
 * Save the search index to disk.
 */
export async function saveSearchIndex(
  chunks: DocChunk[],
  tfidfIndex: Map<string, Map<string, number>>
): Promise<void> {
  const indexDir = getDataPath('index');

  // Ensure directory exists
  if (!existsSync(indexDir)) {
    await mkdir(indexDir, { recursive: true });
  }

  // Save chunks
  await writeFile(join(indexDir, 'chunks.json'), JSON.stringify(chunks, null, 2));

  // Save TF-IDF index (convert Maps to objects)
  const tfidfObj: Record<string, Record<string, number>> = {};
  for (const [term, docs] of tfidfIndex) {
    tfidfObj[term] = Object.fromEntries(docs);
  }
  await writeFile(join(indexDir, 'tfidf.json'), JSON.stringify(tfidfObj));

  // Save metadata
  await writeFile(
    join(indexDir, 'metadata.json'),
    JSON.stringify({
      version: '1.0.0',
      generatedAt: new Date().toISOString(),
      chunkCount: chunks.length,
      termCount: tfidfIndex.size,
    })
  );

  logger.info(`Saved search index: ${chunks.length} chunks, ${tfidfIndex.size} terms`);
}

/**
 * Build the search index from all documentation files.
 */
export async function buildIndex(): Promise<void> {
  logger.info('Building search index from documentation files...');

  const allChunks: DocChunk[] = [];

  // Read and parse all documentation files
  for (const docPath of DOC_PATHS) {
    if (!existsSync(docPath)) {
      logger.warn(`Documentation file not found: ${docPath}`);
      continue;
    }

    const content = await readFile(docPath, 'utf-8');
    const sourceName = docPath.split('/').pop() || 'unknown';
    const chunks = parseClaudeMd(content, sourceName);
    allChunks.push(...chunks);
    logger.info(`Parsed ${chunks.length} chunks from ${sourceName}`);
  }

  // Build TF-IDF index
  const tfidfIndex = buildTfidfIndex(allChunks);

  // Save to disk
  await saveSearchIndex(allChunks, tfidfIndex);

  logger.info(`Search index build complete: ${allChunks.length} total chunks`);
}
