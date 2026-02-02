#!/usr/bin/env node

// All-JS sync script for hostkit-context MCP server
// Rebuilds search index: parse docs → build TF-IDF → generate embeddings → save to data/index/

import { writeFile, mkdir } from 'fs/promises';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadConfig } from './config.js';
import { buildIndex } from './indexers/claudemd.js';
import { embedDocuments } from './embeddings.js';
import { createLogger } from './utils/logger.js';

const logger = createLogger('sync');

async function main() {
  const quiet = process.argv.includes('--quiet');

  // Parse --docs flag
  const docsIndex = process.argv.indexOf('--docs');
  let docPaths: string[] | undefined;
  if (docsIndex !== -1) {
    docPaths = [];
    for (let i = docsIndex + 1; i < process.argv.length; i++) {
      if (process.argv[i].startsWith('--')) break;
      docPaths.push(process.argv[i]);
    }
    if (docPaths.length === 0) {
      console.error('Error: --docs requires at least one path');
      process.exit(1);
    }
  }

  // Output to data/index/ in the package (bundled with repo)
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = dirname(__filename);
  const outputDir = join(__dirname, '..', 'data', 'index');

  if (!quiet) {
    console.log('hostkit-context: Syncing documentation index...');
    if (docPaths) {
      console.log(`  Doc paths: ${docPaths.join(', ')}`);
    }
    console.log(`  Output: ${outputDir}`);
  }

  try {
    await loadConfig();

    // Ensure output directory exists
    if (!existsSync(outputDir)) {
      await mkdir(outputDir, { recursive: true });
    }

    // Step 1: Parse docs and build TF-IDF
    if (!quiet) console.log('\n1. Parsing documentation and building TF-IDF index...');
    const { chunks, tfidfIndex } = await buildIndex(docPaths, outputDir);

    if (chunks.length === 0) {
      console.error('Error: No chunks generated. Check doc paths.');
      process.exit(1);
    }

    if (!quiet) console.log(`   ${chunks.length} chunks, ${tfidfIndex.size} TF-IDF terms`);

    // Step 2: Generate embeddings via Transformers.js
    if (!quiet) console.log('\n2. Generating embeddings via Transformers.js...');
    const texts = chunks.map((c) => `${c.title}\n\n${c.content}`);
    const embeddings = await embedDocuments(texts);

    if (!quiet) console.log(`   ${embeddings.length} embeddings (${embeddings[0]?.length || 0} dimensions)`);

    // Step 3: Save embeddings
    if (!quiet) console.log('\n3. Saving embeddings...');
    await writeFile(join(outputDir, 'embeddings.json'), JSON.stringify(embeddings));

    // Update metadata
    const metadataPath = join(outputDir, 'metadata.json');
    let metadata: Record<string, unknown> = {};
    if (existsSync(metadataPath)) {
      const { readFile: rf } = await import('fs/promises');
      metadata = JSON.parse(await rf(metadataPath, 'utf-8'));
    }
    metadata.embeddingsGeneratedAt = new Date().toISOString();
    metadata.modelId = 'Xenova/all-MiniLM-L6-v2';
    metadata.embeddingDimension = embeddings[0]?.length || 0;
    metadata.embeddingCount = embeddings.length;
    await writeFile(metadataPath, JSON.stringify(metadata, null, 2));

    // Report file sizes
    if (!quiet) {
      const { stat } = await import('fs/promises');
      const files = ['chunks.json', 'tfidf.json', 'embeddings.json', 'metadata.json'];
      console.log('\nIndex files:');
      for (const f of files) {
        const fp = join(outputDir, f);
        if (existsSync(fp)) {
          const s = await stat(fp);
          console.log(`  ${f}: ${(s.size / 1024).toFixed(1)} KB`);
        }
      }
      console.log('\nhostkit-context: Sync complete');
    }
  } catch (error) {
    if (!quiet) {
      console.error('Sync failed:', error);
    }
    process.exit(1);
  }
}

main();
