"""Database tools - Read and write to project databases."""

import asyncio
import json
import logging
import re
from typing import Any

from tools.base import BaseTool, ToolResult, ToolTier

logger = logging.getLogger(__name__)


class DatabaseReadTool(BaseTool):
    """Execute read-only SQL queries against the project's database.

    Tier 1 (read-only): Safe to use without confirmation.

    Only SELECT queries are allowed. Results are formatted as a table
    for readability, with structured data available.
    """

    name = "db_read"  # Underscore for Anthropic API compatibility
    description = (
        "Execute a read-only SQL query against the project's database. "
        "Only SELECT queries are allowed."
    )
    tier = ToolTier.READ_ONLY

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL SELECT query to execute",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum rows to return (default: 100, max: 1000)",
                "default": 100,
            },
        },
        "required": ["query"],
    }

    # Pattern to validate SELECT queries
    SELECT_PATTERN = re.compile(
        r"^\s*SELECT\s+.+\s+FROM\s+",
        re.IGNORECASE | re.DOTALL,
    )

    # Dangerous patterns to reject
    DANGEROUS_PATTERNS = [
        re.compile(r"\bINSERT\b", re.IGNORECASE),
        re.compile(r"\bUPDATE\b", re.IGNORECASE),
        re.compile(r"\bDELETE\b", re.IGNORECASE),
        re.compile(r"\bDROP\b", re.IGNORECASE),
        re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
        re.compile(r"\bALTER\b", re.IGNORECASE),
        re.compile(r"\bCREATE\b", re.IGNORECASE),
        re.compile(r"\bGRANT\b", re.IGNORECASE),
        re.compile(r"\bREVOKE\b", re.IGNORECASE),
    ]

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Execute a SELECT query on the project's database.

        Args:
            project_name: Project whose database to query
            query: SQL SELECT query
            limit: Maximum rows to return

        Returns:
            ToolResult with query results
        """
        query = params.get("query", "").strip()
        limit = min(params.get("limit", 100), 1000)

        # Validate query is SELECT
        if not self.SELECT_PATTERN.match(query):
            return ToolResult(
                success=False,
                output="",
                error="Only SELECT queries are allowed. Query must start with 'SELECT ... FROM'",
            )

        # Check for dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.search(query):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Query contains forbidden keyword. Use db:write for write operations.",
                )

        # Add LIMIT if not present
        if not re.search(r"\bLIMIT\b", query, re.IGNORECASE):
            query = f"{query.rstrip(';')} LIMIT {limit}"

        # Execute via hostkit db shell
        try:
            result = await self._execute_query(project_name, query)
            return result
        except Exception as e:
            logger.exception(f"Database query failed for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

    async def _execute_query(self, project_name: str, query: str) -> ToolResult:
        """Execute query via psql."""
        db_name = f"{project_name}_db"

        # Use psql with JSON output for structured data
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-d",
            db_name,
            "-c",
            query,
            "--no-align",
            "--tuples-only",
            "--field-separator=|",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            return ToolResult(
                success=False,
                output="",
                error=f"Query failed: {error}",
            )

        # Parse output
        output = stdout.decode().strip()

        if not output:
            return ToolResult(
                success=True,
                output="Query returned no results.",
                data={"rows": [], "row_count": 0},
            )

        # Parse rows
        rows = []
        for line in output.splitlines():
            if line.strip():
                rows.append(line.split("|"))

        # Get column names from a separate query
        # For simplicity, we'll just show the raw output
        # In production, we'd parse column names from the query

        # Format as table
        formatted = self._format_table(rows)
        formatted, truncated = self.truncate_output(formatted)

        return ToolResult(
            success=True,
            output=formatted,
            data={
                "rows": rows,
                "row_count": len(rows),
            },
            truncated=truncated,
        )

    def _format_table(self, rows: list[list[str]]) -> str:
        """Format rows as a simple table."""
        if not rows:
            return "No results"

        # Calculate column widths
        col_count = len(rows[0])
        widths = [0] * col_count
        for row in rows:
            for i, cell in enumerate(row):
                if i < col_count:
                    widths[i] = max(widths[i], len(str(cell)))

        # Build table
        lines = []
        for row in rows:
            cells = []
            for i, cell in enumerate(row):
                if i < col_count:
                    cells.append(str(cell).ljust(widths[i]))
            lines.append(" | ".join(cells))

        return "\n".join(lines)


class DatabaseWriteTool(BaseTool):
    """Execute write SQL queries against the project's database.

    Tier 2 (state-change): Requires confirmation or explicit grant.

    Supports INSERT, UPDATE, and DELETE queries. DROP, TRUNCATE,
    and DDL commands are not allowed.
    """

    name = "db_write"
    description = (
        "Execute a write SQL query (INSERT, UPDATE, DELETE) against "
        "the project's database. Use with caution."
    )
    tier = ToolTier.STATE_CHANGE

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL query to execute (INSERT, UPDATE, DELETE)",
            },
        },
        "required": ["query"],
    }

    # Allowed write patterns
    WRITE_PATTERNS = [
        re.compile(r"^\s*INSERT\s+INTO\s+", re.IGNORECASE),
        re.compile(r"^\s*UPDATE\s+\w+\s+SET\s+", re.IGNORECASE),
        re.compile(r"^\s*DELETE\s+FROM\s+", re.IGNORECASE),
    ]

    # Dangerous patterns to reject
    DANGEROUS_PATTERNS = [
        re.compile(r"\bDROP\b", re.IGNORECASE),
        re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
        re.compile(r"\bALTER\b", re.IGNORECASE),
        re.compile(r"\bCREATE\b", re.IGNORECASE),
        re.compile(r"\bGRANT\b", re.IGNORECASE),
        re.compile(r"\bREVOKE\b", re.IGNORECASE),
    ]

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Execute a write query on the project's database.

        Args:
            project_name: Project whose database to modify
            query: SQL query (INSERT, UPDATE, DELETE)

        Returns:
            ToolResult with affected row count
        """
        query = params.get("query", "").strip()

        # Validate query type
        is_valid = any(p.match(query) for p in self.WRITE_PATTERNS)
        if not is_valid:
            return ToolResult(
                success=False,
                output="",
                error="Only INSERT, UPDATE, and DELETE queries are allowed.",
            )

        # Check for dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.search(query):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Query contains forbidden keyword. DDL operations are not allowed.",
                )

        # Execute query
        try:
            result = await self._execute_query(project_name, query)
            return result
        except Exception as e:
            logger.exception(f"Database write failed for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

    async def _execute_query(self, project_name: str, query: str) -> ToolResult:
        """Execute write query via psql."""
        db_name = f"{project_name}_db"

        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-d",
            db_name,
            "-c",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            return ToolResult(
                success=False,
                output="",
                error=f"Query failed: {error}",
            )

        output = stdout.decode().strip()

        # Parse affected rows from output like "UPDATE 5" or "INSERT 0 3"
        affected = 0
        if output:
            parts = output.split()
            if len(parts) >= 2:
                try:
                    # For INSERT, last number is row count
                    # For UPDATE/DELETE, second word is row count
                    affected = int(parts[-1])
                except ValueError:
                    pass

        return ToolResult(
            success=True,
            output=f"Query executed successfully. {affected} row(s) affected.",
            data={
                "affected_rows": affected,
                "raw_output": output,
            },
        )
