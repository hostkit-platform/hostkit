// hostkit_solutions tool implementation

import { existsSync, mkdirSync } from 'fs';
import { join } from 'path';
import { getDataPath } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { SolutionsParams, Solution, ToolResponse } from '../types.js';

const logger = createLogger('tools:solutions');

// Database instance (lazy loaded)
let db: import('better-sqlite3').Database | null = null;

/**
 * Initialize the solutions database.
 */
async function getDatabase(): Promise<import('better-sqlite3').Database> {
  if (db) return db;

  const Database = (await import('better-sqlite3')).default;
  const solutionsDir = getDataPath('solutions');

  // Ensure directory exists
  if (!existsSync(solutionsDir)) {
    mkdirSync(solutionsDir, { recursive: true });
  }

  const dbPath = join(solutionsDir, 'solutions.db');
  db = new Database(dbPath);

  // Initialize schema
  db.exec(`
    CREATE TABLE IF NOT EXISTS solutions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      problem TEXT NOT NULL,
      solution TEXT NOT NULL,
      project TEXT,
      tags TEXT,
      usefulness_score INTEGER DEFAULT 0,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS solution_uses (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      solution_id INTEGER REFERENCES solutions(id),
      project TEXT NOT NULL,
      was_helpful BOOLEAN,
      used_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_solutions_project ON solutions(project);
    CREATE INDEX IF NOT EXISTS idx_solutions_tags ON solutions(tags);
  `);

  logger.info('Solutions database initialized', { path: dbPath });
  return db;
}

/**
 * Search for solutions using text matching.
 * (Will be enhanced with embeddings later)
 */
async function searchSolutions(query: string, limit: number): Promise<Solution[]> {
  const database = await getDatabase();

  // Simple text search for now (will add semantic search later)
  const rows = database
    .prepare(
      `
      SELECT id, problem, solution, project, tags, usefulness_score, created_at
      FROM solutions
      WHERE problem LIKE ? OR solution LIKE ? OR tags LIKE ?
      ORDER BY usefulness_score DESC, created_at DESC
      LIMIT ?
    `
    )
    .all(`%${query}%`, `%${query}%`, `%${query}%`, limit) as Array<{
    id: number;
    problem: string;
    solution: string;
    project: string | null;
    tags: string | null;
    usefulness_score: number;
    created_at: string;
  }>;

  return rows.map((row) => ({
    id: row.id,
    problem: row.problem,
    solution: row.solution,
    project: row.project || undefined,
    tags: row.tags ? JSON.parse(row.tags) : [],
    usefulnessScore: row.usefulness_score,
    createdAt: new Date(row.created_at),
  }));
}

/**
 * Record a new solution.
 */
async function recordSolution(
  problem: string,
  solution: string,
  project?: string,
  tags?: string[]
): Promise<Solution> {
  const database = await getDatabase();

  const result = database
    .prepare(
      `
      INSERT INTO solutions (problem, solution, project, tags)
      VALUES (?, ?, ?, ?)
    `
    )
    .run(problem, solution, project || null, tags ? JSON.stringify(tags) : null);

  const id = result.lastInsertRowid as number;

  logger.info('Solution recorded', { id, problem: problem.substring(0, 50) });

  return {
    id,
    problem,
    solution,
    project,
    tags: tags || [],
    usefulnessScore: 0,
    createdAt: new Date(),
  };
}

/**
 * List recent solutions.
 */
async function listSolutions(limit: number): Promise<Solution[]> {
  const database = await getDatabase();

  const rows = database
    .prepare(
      `
      SELECT id, problem, solution, project, tags, usefulness_score, created_at
      FROM solutions
      ORDER BY created_at DESC
      LIMIT ?
    `
    )
    .all(limit) as Array<{
    id: number;
    problem: string;
    solution: string;
    project: string | null;
    tags: string | null;
    usefulness_score: number;
    created_at: string;
  }>;

  return rows.map((row) => ({
    id: row.id,
    problem: row.problem,
    solution: row.solution,
    project: row.project || undefined,
    tags: row.tags ? JSON.parse(row.tags) : [],
    usefulnessScore: row.usefulness_score,
    createdAt: new Date(row.created_at),
  }));
}

/**
 * Handle hostkit_solutions tool calls.
 */
export async function handleSolutions(params: SolutionsParams): Promise<ToolResponse> {
  const { action, query, problem, solution, project, tags, limit = 5 } = params;

  logger.info('Solutions request', { action, query, problem });

  try {
    switch (action) {
      case 'search': {
        if (!query) {
          return {
            success: false,
            error: {
              code: 'MISSING_QUERY',
              message: 'Query required for search action',
            },
          };
        }

        const results = await searchSolutions(query, limit);

        return {
          success: true,
          data: {
            query,
            results,
            totalResults: results.length,
          },
        };
      }

      case 'record': {
        if (!problem || !solution) {
          return {
            success: false,
            error: {
              code: 'MISSING_FIELDS',
              message: 'Both problem and solution required for record action',
            },
          };
        }

        const recorded = await recordSolution(problem, solution, project, tags);

        return {
          success: true,
          data: {
            recorded,
            message: 'Solution recorded successfully',
          },
        };
      }

      case 'list': {
        const solutions = await listSolutions(limit);

        return {
          success: true,
          data: {
            solutions,
            totalSolutions: solutions.length,
          },
        };
      }

      default:
        return {
          success: false,
          error: {
            code: 'INVALID_ACTION',
            message: `Unknown action: ${action}. Use search, record, or list.`,
          },
        };
    }
  } catch (error) {
    logger.error('Solutions operation failed', error);

    return {
      success: false,
      error: {
        code: 'SOLUTIONS_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

/**
 * Close the database connection.
 */
export function closeSolutionsDatabase(): void {
  if (db) {
    db.close();
    db = null;
    logger.debug('Solutions database closed');
  }
}
