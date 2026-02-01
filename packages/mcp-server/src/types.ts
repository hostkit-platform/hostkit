// Type definitions for hostkit-context MCP server

// =============================================================================
// Configuration
// =============================================================================

export interface Config {
  vps: {
    host: string;
    port: number;
    user: string;
    keyPath: string;
  };
  dataDir: string;
  cache: {
    projectsTtl: number;
    healthTtl: number;
    projectTtl: number;
  };
  logging: {
    level: 'debug' | 'info' | 'warn' | 'error';
    debug: boolean;
  };
}

// =============================================================================
// Document Chunks (for search)
// =============================================================================

export type ChunkType = 'service' | 'command' | 'concept' | 'example';

export interface DocChunk {
  id: string;
  title: string;
  content: string;
  section: string;
  chunkType: ChunkType;
  tokens: string[];
  source: string;
}

export interface IndexedChunk extends DocChunk {
  embedding: number[];
}

export interface SearchResult {
  chunk: DocChunk;
  semanticScore: number;
  tfidfScore: number;
  hybridScore: number;
}

// =============================================================================
// VPS State
// =============================================================================

export interface Project {
  name: string;
  runtime: string;
  status: 'running' | 'stopped' | 'failed' | 'unknown';
  services: string[];
  url: string;
  port: number;
}

export interface VPSHealth {
  resources: {
    cpuPercent: number;
    memoryGb: string;
    diskGb: string;
  };
  limits: {
    projects: string;
    redisDbs: string;
  };
  healthy: boolean;
}

export interface ProjectState {
  info: Record<string, unknown>;
  health: Record<string, unknown>;
  capabilities: Record<string, unknown>;
}

// =============================================================================
// Permissions
// =============================================================================

export interface PermissionGap {
  command: string;
  scope: string;
  project?: string;
  suggestion: string;
}

export interface PermissionSyncResult {
  synced: string[];
  errors: string[];
}

// =============================================================================
// Solutions
// =============================================================================

export interface Solution {
  id: number;
  problem: string;
  solution: string;
  project?: string;
  tags: string[];
  usefulnessScore: number;
  createdAt: Date;
}

export interface SolutionUse {
  id: number;
  solutionId: number;
  project: string;
  wasHelpful: boolean;
  usedAt: Date;
}

// =============================================================================
// Tool Parameters
// =============================================================================

export interface SearchParams {
  query: string;
  limit?: number;
  filter?: 'all' | 'commands' | 'services' | 'concepts' | 'examples';
}

export interface StateParams {
  scope?: 'all' | 'projects' | 'health' | 'resources' | 'project';
  project?: string;
  refresh?: boolean;
}

export interface ExecuteParams {
  command: string;
  project?: string;
  user?: 'ai-operator' | 'project';
  json_mode?: boolean;
}

export interface PermissionsParams {
  action: 'analyze' | 'fix' | 'sync';
  project?: string;
  error_output?: string;
}

export interface SolutionsParams {
  action: 'search' | 'record' | 'list';
  query?: string;
  problem?: string;
  solution?: string;
  project?: string;
  tags?: string[];
  limit?: number;
}

export interface DbSchemaParams {
  project: string;
  table?: string;
}

export interface DbQueryParams {
  project: string;
  query: string;
  limit?: number;
}

export interface DbVerifyParams {
  project: string;
  checks?: ('migrations' | 'indexes' | 'constraints' | 'seeded')[];
}

export interface DeployLocalParams {
  project: string;
  local_path: string;
  build?: boolean;
  install?: boolean;
  wait_healthy?: boolean;
  cleanup?: boolean;
  override_ratelimit?: boolean;
}

export interface CapabilitiesParams {
  project?: string;
}

export interface WaitHealthyParams {
  project: string;
  timeout?: number;
  interval?: number;
}

export interface EnvSetParams {
  project: string;
  variables: Record<string, string>;
  restart?: boolean;
}

export interface EnvGetParams {
  project: string;
  keys?: string[];
}

export interface ValidateParams {
  project: string;
}

// =============================================================================
// Tool Responses
// =============================================================================

export interface ToolResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  cached?: boolean;
  cachedAt?: string;
}
