#!/usr/bin/env node

// hostkit-context MCP Server
// Provides semantic search, live VPS state, permission healing, and cross-project learning

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

import { loadConfig, getDataPath } from './config.js';
import { createLogger } from './utils/logger.js';
import { closeSSHConnections } from './services/ssh.js';
import { closeSolutionsDatabase } from './tools/solutions.js';
import { TOOLS, handleToolCall } from './tools/index.js';
import { loadSearchIndex } from './indexers/claudemd.js';
import { initializeSearchIndex } from './tools/search.js';

const logger = createLogger('server');

async function main() {
  logger.info('Starting hostkit-context MCP Server');

  // Load configuration
  await loadConfig();
  logger.info('Configuration loaded');

  // Try to load search index (non-fatal if not available)
  try {
    const searchIndex = await loadSearchIndex();
    if (searchIndex) {
      initializeSearchIndex(searchIndex);
      logger.info('Search index loaded');
    } else {
      logger.warn('Search index not found. Run `npm run build-embeddings` to generate.');
    }
  } catch (error) {
    logger.warn('Failed to load search index', error);
  }

  // Create MCP server
  const server = new Server(
    {
      name: 'hostkit-context',
      version: '1.0.0',
    },
    {
      capabilities: {
        tools: {},
      },
    }
  );

  // Register tool list handler
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return { tools: TOOLS };
  });

  // Register tool call handler
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    return handleToolCall(name, args);
  });

  // Connect via stdio
  const transport = new StdioServerTransport();
  await server.connect(transport);

  logger.info('hostkit-context MCP Server running');

  // Graceful shutdown
  process.on('SIGINT', () => {
    logger.info('Shutting down...');
    closeSSHConnections();
    closeSolutionsDatabase();
    process.exit(0);
  });

  process.on('SIGTERM', () => {
    logger.info('Shutting down...');
    closeSSHConnections();
    closeSolutionsDatabase();
    process.exit(0);
  });
}

main().catch((error) => {
  console.error('Fatal error:', error);
  process.exit(1);
});
