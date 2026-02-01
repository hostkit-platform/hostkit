#!/usr/bin/env node

// Sync script for hostkit-context MCP server
// Rebuilds the search index from CLAUDE.md

import { loadConfig } from './config.js';
import { buildIndex } from './indexers/claudemd.js';
import { createLogger } from './utils/logger.js';

const logger = createLogger('sync');

async function main() {
  const quiet = process.argv.includes('--quiet');

  if (!quiet) {
    console.log('hostkit-context: Syncing documentation index...');
  }

  try {
    await loadConfig();
    await buildIndex();

    if (!quiet) {
      console.log('hostkit-context: Sync complete');
      console.log('');
      console.log('Next steps:');
      console.log('  1. Install Python dependencies: pip install -r embeddings/requirements.txt');
      console.log('  2. Generate embeddings: npm run build-embeddings');
    }
  } catch (error) {
    if (!quiet) {
      console.error('Sync failed:', error);
    }
    process.exit(1);
  }
}

main();
