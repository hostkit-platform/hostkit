// Database introspection tools for hostkit-context MCP server

import { getSSHManager } from '../services/ssh.js';
import { getProjectContext } from '../config.js';
import { createLogger } from '../utils/logger.js';
import type { ToolResponse } from '../types.js';

const logger = createLogger('database');

// =============================================================================
// Types
// =============================================================================

export interface DbSchemaParams {
  project: string;
  table?: string;
}

export interface DbQueryParams {
  project: string;
  query: string;
  limit?: number;
  allow_write?: boolean;
}

export interface DbVerifyParams {
  project: string;
  checks?: ('migrations' | 'indexes' | 'constraints' | 'seeded')[];
}

interface TableColumn {
  name: string;
  type: string;
  nullable: boolean;
  default: string | null;
  primary_key: boolean;
}

interface TableIndex {
  name: string;
  columns: string[];
  unique: boolean;
}

interface ForeignKey {
  column: string;
  references: string;
}

interface TableSchema {
  name: string;
  columns: TableColumn[];
  indexes: TableIndex[];
  foreign_keys: ForeignKey[];
}

interface SchemaResult {
  tables: TableSchema[];
}

interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number;
}

interface VerifyResult {
  migrations: {
    checked: boolean;
    applied: number;
    failed: number;
    pending: boolean;
  } | null;
  indexes: {
    checked: boolean;
    count: number;
    tables: string[];
  } | null;
  constraints: {
    checked: boolean;
    foreignKeys: number;
    checkConstraints: number;
  } | null;
  seeded: {
    checked: boolean;
    tables: Record<string, number>;
  } | null;
  healthy: boolean;
  issues: string[];
}

// =============================================================================
// Utilities
// =============================================================================

/**
 * Parse CSV output from psql into structured data.
 */
function parseCSV(csv: string): { columns: string[]; rows: Record<string, unknown>[] } {
  const lines = csv
    .trim()
    .split('\n')
    .filter((line) => {
      const trimmed = line.trim();
      // Filter out empty lines and hostkit db shell banner
      if (!trimmed) return false;
      if (trimmed.startsWith('Connecting to ')) return false;
      if (trimmed.startsWith('Type \\q to exit')) return false;
      return true;
    });
  if (lines.length === 0) {
    return { columns: [], rows: [] };
  }

  const columns = lines[0].split(',').map((col) => col.trim());
  const rows: Record<string, unknown>[] = [];

  for (let i = 1; i < lines.length; i++) {
    const values = parseCSVLine(lines[i]);
    const row: Record<string, unknown> = {};
    columns.forEach((col, idx) => {
      row[col] = values[idx] ?? null;
    });
    rows.push(row);
  }

  return { columns, rows };
}

/**
 * Parse a single CSV line handling quoted values.
 */
function parseCSVLine(line: string): string[] {
  const values: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];

    if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === ',' && !inQuotes) {
      values.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }

  values.push(current.trim());
  return values;
}

/**
 * Validate query is read-only (SELECT/WITH only).
 */
function isReadOnlyQuery(query: string): boolean {
  const normalized = query.trim().toUpperCase();
  const dangerousKeywords = [
    'DROP',
    'DELETE',
    'UPDATE',
    'INSERT',
    'ALTER',
    'TRUNCATE',
    'CREATE',
    'GRANT',
    'REVOKE',
  ];

  for (const keyword of dangerousKeywords) {
    // Check for keyword at word boundary
    const regex = new RegExp(`\\b${keyword}\\b`);
    if (regex.test(normalized)) {
      return false;
    }
  }

  // Must start with SELECT or WITH
  return normalized.startsWith('SELECT') || normalized.startsWith('WITH');
}

/**
 * Ensure query has a LIMIT clause.
 */
function ensureLimit(query: string, maxLimit: number): string {
  const normalized = query.trim().toUpperCase();

  // Check if already has LIMIT
  if (/\bLIMIT\s+\d+/i.test(query)) {
    return query;
  }

  // Add LIMIT
  return `${query.trim()} LIMIT ${maxLimit}`;
}

/**
 * Escape single quotes for psql.
 */
function escapeSql(str: string): string {
  return str.replace(/'/g, "''");
}

// =============================================================================
// Tool Handlers
// =============================================================================

/**
 * Get database schema for a project.
 */
export async function handleDbSchema(
  params: DbSchemaParams
): Promise<ToolResponse<SchemaResult>> {
  const project = params.project || getProjectContext();
  const { table } = params;

  if (!project) {
    return {
      success: false,
      error: { code: 'MISSING_PROJECT', message: 'Project name is required' },
    };
  }

  const ssh = getSSHManager();

  try {
    // Get all tables or specific table
    const tableFilter = table ? `AND table_name = '${escapeSql(table)}'` : '';

    // Query columns
    const columnsQuery = `
      SELECT
        table_name,
        column_name,
        data_type,
        is_nullable,
        column_default
      FROM information_schema.columns
      WHERE table_schema = 'public' ${tableFilter}
      ORDER BY table_name, ordinal_position
    `;

    const columnsResult = await ssh.execute(
      `echo "COPY (${columnsQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
    );

    // Query primary keys
    const pkQuery = `
      SELECT
        tc.table_name,
        kcu.column_name
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
      WHERE tc.table_schema = 'public'
        AND tc.constraint_type = 'PRIMARY KEY'
        ${tableFilter}
    `;

    const pkResult = await ssh.execute(
      `echo "COPY (${pkQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
    );

    // Query indexes
    const indexQuery = `
      SELECT
        tablename,
        indexname,
        indexdef
      FROM pg_indexes
      WHERE schemaname = 'public'
        ${table ? `AND tablename = '${escapeSql(table)}'` : ''}
    `;

    const indexResult = await ssh.execute(
      `echo "COPY (${indexQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
    );

    // Query foreign keys
    const fkQuery = `
      SELECT
        tc.table_name,
        kcu.column_name,
        ccu.table_name AS foreign_table,
        ccu.column_name AS foreign_column
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
      JOIN information_schema.constraint_column_usage ccu
        ON tc.constraint_name = ccu.constraint_name
      WHERE tc.table_schema = 'public'
        AND tc.constraint_type = 'FOREIGN KEY'
        ${tableFilter}
    `;

    const fkResult = await ssh.execute(
      `echo "COPY (${fkQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
    );

    // Parse results
    const columns = parseCSV(columnsResult);
    const primaryKeys = parseCSV(pkResult);
    const indexes = parseCSV(indexResult);
    const foreignKeys = parseCSV(fkResult);

    // Build primary key lookup
    const pkLookup = new Set<string>();
    for (const row of primaryKeys.rows) {
      pkLookup.add(`${row.table_name}.${row.column_name}`);
    }

    // Group by table
    const tableMap = new Map<string, TableSchema>();

    for (const row of columns.rows) {
      const tableName = row.table_name as string;

      if (!tableMap.has(tableName)) {
        tableMap.set(tableName, {
          name: tableName,
          columns: [],
          indexes: [],
          foreign_keys: [],
        });
      }

      const tableSchema = tableMap.get(tableName)!;
      tableSchema.columns.push({
        name: row.column_name as string,
        type: row.data_type as string,
        nullable: row.is_nullable === 'YES',
        default: row.column_default as string | null,
        primary_key: pkLookup.has(`${tableName}.${row.column_name}`),
      });
    }

    // Add indexes
    for (const row of indexes.rows) {
      const tableName = row.tablename as string;
      const tableSchema = tableMap.get(tableName);
      if (tableSchema) {
        const indexDef = (row.indexdef as string) || '';
        const isUnique = indexDef.includes('UNIQUE');

        // Extract columns from index definition
        const colMatch = indexDef.match(/\(([^)]+)\)/);
        const colList = colMatch ? colMatch[1].split(',').map((c) => c.trim()) : [];

        tableSchema.indexes.push({
          name: (row.indexname as string) || 'unknown',
          columns: colList,
          unique: isUnique,
        });
      }
    }

    // Add foreign keys
    for (const row of foreignKeys.rows) {
      const tableName = row.table_name as string;
      const tableSchema = tableMap.get(tableName);
      if (tableSchema) {
        tableSchema.foreign_keys.push({
          column: row.column_name as string,
          references: `${row.foreign_table}.${row.foreign_column}`,
        });
      }
    }

    logger.info(`Schema retrieved for ${project}`, {
      tables: tableMap.size,
      filtered: table || 'all',
    });

    return {
      success: true,
      data: { tables: Array.from(tableMap.values()) },
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error(`Failed to get schema for ${project}`, error);

    return {
      success: false,
      error: { code: 'SCHEMA_ERROR', message },
    };
  }
}

/**
 * Run a query on a project database.
 * By default only SELECT queries are allowed. Use allow_write=true to enable writes.
 */
export async function handleDbQuery(
  params: DbQueryParams
): Promise<ToolResponse<QueryResult>> {
  const project = params.project || getProjectContext();
  const { query, limit = 100, allow_write = false } = params;

  if (!project) {
    return {
      success: false,
      error: { code: 'MISSING_PROJECT', message: 'Project name is required' },
    };
  }

  if (!query) {
    return {
      success: false,
      error: { code: 'MISSING_QUERY', message: 'Query is required' },
    };
  }

  // Validate query is read-only (unless allow_write is true)
  if (!allow_write && !isReadOnlyQuery(query)) {
    return {
      success: false,
      error: {
        code: 'WRITE_BLOCKED',
        message: 'Only SELECT queries are allowed. Use allow_write=true to enable write operations.',
      },
    };
  }

  const ssh = getSSHManager();

  try {
    const isSelectQuery = isReadOnlyQuery(query);

    if (isSelectQuery) {
      // Apply limit for SELECT queries
      const safeQuery = ensureLimit(query, Math.min(limit, 100));

      logger.info(`Executing SELECT query on ${project}`, { query: safeQuery });

      // Execute query with CSV output for SELECT
      const result = await ssh.execute(
        `echo "COPY (${safeQuery.replace(/"/g, '\\"').replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
      );

      const parsed = parseCSV(result);

      return {
        success: true,
        data: {
          columns: parsed.columns,
          rows: parsed.rows,
          rowCount: parsed.rows.length,
        },
      };
    } else {
      // Write query - use -c flag directly
      logger.info(`Executing write query on ${project}`, { query });

      // Escape the query for shell
      const escapedQuery = query.replace(/"/g, '\\"').replace(/\n/g, ' ');
      const result = await ssh.execute(
        `sudo hostkit db shell ${project} -c "${escapedQuery}"`
      );

      // Parse the result - for writes, we get a simple text response like "UPDATE 1"
      const trimmed = result.trim();

      // Check for common write operation responses
      const writeMatch = trimmed.match(/^(INSERT|UPDATE|DELETE)\s+(\d+)/i);
      if (writeMatch) {
        return {
          success: true,
          data: {
            columns: ['operation', 'affected_rows'],
            rows: [{ operation: writeMatch[1].toUpperCase(), affected_rows: parseInt(writeMatch[2], 10) }],
            rowCount: parseInt(writeMatch[2], 10),
          },
        };
      }

      // For other responses (like RETURNING clauses), try to parse as CSV
      const parsed = parseCSV(trimmed);
      if (parsed.columns.length > 0) {
        return {
          success: true,
          data: {
            columns: parsed.columns,
            rows: parsed.rows,
            rowCount: parsed.rows.length,
          },
        };
      }

      // Fallback - return raw result
      return {
        success: true,
        data: {
          columns: ['result'],
          rows: [{ result: trimmed }],
          rowCount: 1,
        },
      };
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error(`Query failed on ${project}`, error);

    return {
      success: false,
      error: { code: 'QUERY_ERROR', message },
    };
  }
}

/**
 * Verify database health for a project.
 */
export async function handleDbVerify(
  params: DbVerifyParams
): Promise<ToolResponse<VerifyResult>> {
  const project = params.project || getProjectContext();
  const { checks = ['migrations', 'indexes', 'constraints'] } = params;

  if (!project) {
    return {
      success: false,
      error: { code: 'MISSING_PROJECT', message: 'Project name is required' },
    };
  }

  const ssh = getSSHManager();
  const issues: string[] = [];

  const result: VerifyResult = {
    migrations: null,
    indexes: null,
    constraints: null,
    seeded: null,
    healthy: true,
    issues: [],
  };

  try {
    // Check migrations (Prisma)
    if (checks.includes('migrations')) {
      try {
        const migrationQuery = `
          SELECT
            migration_name,
            finished_at,
            applied_steps_count
          FROM _prisma_migrations
          ORDER BY finished_at DESC
          LIMIT 20
        `;

        const migrationResult = await ssh.execute(
          `echo "COPY (${migrationQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project} 2>/dev/null || echo ""`
        );

        if (migrationResult.trim()) {
          const parsed = parseCSV(migrationResult);
          const applied = parsed.rows.filter((r) => r.finished_at).length;
          const failed = parsed.rows.filter((r) => !r.finished_at).length;

          result.migrations = {
            checked: true,
            applied,
            failed,
            pending: failed > 0,
          };

          if (failed > 0) {
            issues.push(`${failed} migration(s) not fully applied`);
          }
        } else {
          result.migrations = {
            checked: true,
            applied: 0,
            failed: 0,
            pending: false,
          };
        }
      } catch (e) {
        // Migration table might not exist (not using Prisma)
        result.migrations = { checked: true, applied: 0, failed: 0, pending: false };
      }
    }

    // Check indexes
    if (checks.includes('indexes')) {
      const indexQuery = `
        SELECT tablename, COUNT(*) as idx_count
        FROM pg_indexes
        WHERE schemaname = 'public'
        GROUP BY tablename
      `;

      const indexResult = await ssh.execute(
        `echo "COPY (${indexQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
      );

      const parsed = parseCSV(indexResult);
      const totalIndexes = parsed.rows.reduce(
        (sum, r) => sum + parseInt(r.idx_count as string, 10),
        0
      );

      result.indexes = {
        checked: true,
        count: totalIndexes,
        tables: parsed.rows.map((r) => r.tablename as string),
      };
    }

    // Check constraints
    if (checks.includes('constraints')) {
      const constraintQuery = `
        SELECT constraint_type, COUNT(*) as cnt
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
        GROUP BY constraint_type
      `;

      const constraintResult = await ssh.execute(
        `echo "COPY (${constraintQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
      );

      const parsed = parseCSV(constraintResult);
      const fkCount =
        parsed.rows.find((r) => r.constraint_type === 'FOREIGN KEY')?.cnt || 0;
      const checkCount =
        parsed.rows.find((r) => r.constraint_type === 'CHECK')?.cnt || 0;

      result.constraints = {
        checked: true,
        foreignKeys: parseInt(fkCount as string, 10) || 0,
        checkConstraints: parseInt(checkCount as string, 10) || 0,
      };
    }

    // Check seeded data
    if (checks.includes('seeded')) {
      // Get row counts for all tables
      const tablesQuery = `
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
      `;

      const tablesResult = await ssh.execute(
        `echo "COPY (${tablesQuery.replace(/\n/g, ' ')}) TO STDOUT WITH CSV HEADER" | sudo hostkit db shell ${project}`
      );

      const tables = parseCSV(tablesResult);
      const tableCounts: Record<string, number> = {};

      for (const row of tables.rows) {
        const tableName = row.table_name as string;
        if (tableName.startsWith('_')) continue; // Skip internal tables

        try {
          const countResult = await ssh.execute(
            `echo "SELECT COUNT(*) FROM \\"${tableName}\\"" | sudo hostkit db shell ${project}`
          );
          tableCounts[tableName] = parseInt(countResult.trim(), 10) || 0;
        } catch {
          tableCounts[tableName] = 0;
        }
      }

      result.seeded = {
        checked: true,
        tables: tableCounts,
      };

      // Flag empty tables (might indicate missing seed data)
      const emptyTables = Object.entries(tableCounts).filter(
        ([name, count]) => count === 0 && !name.startsWith('_')
      );
      if (emptyTables.length > 0) {
        issues.push(`Empty tables: ${emptyTables.map(([n]) => n).join(', ')}`);
      }
    }

    result.issues = issues;
    result.healthy = issues.length === 0;

    logger.info(`Database verification for ${project}`, {
      healthy: result.healthy,
      issues: issues.length,
    });

    return { success: true, data: result };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error(`Database verification failed for ${project}`, error);

    return {
      success: false,
      error: { code: 'VERIFY_ERROR', message },
    };
  }
}
