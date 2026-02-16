# HostKit MCP Tools - Complete Reference

## Table of Contents
- [Overview](#overview)
- [Tool Inventory](#tool-inventory)
- [Quick Reference Table](#quick-reference-table)
- [Tool Categories](#tool-categories)

---

## Overview

All HostKit operations go through MCP (Model Context Protocol) tools. **Never construct SSH commands manually.**

The MCP server provides 14 tools that cover:
- State queries (current project/VPS status)
- Deployments (local filesystem to VPS)
- Environment management (variables, configuration)
- Database operations (schema, queries)
- Service management (health checks, restarts)
- Diagnostics (logs, permissions, solutions)
- Documentation (semantic search)

---

## Tool Inventory

### 1. `hostkit_search`

**Purpose**: Semantic search over HostKit documentation and knowledge base

**Parameters:**
- `query` (string, required): Natural language query about HostKit
  - Example: "how do I enable payments", "deploy with auth"
- `limit` (integer, optional, default=5): Maximum results to return
- `filter` (string, optional, default="all"): Filter results by type
  - `"all"` - All documentation types
  - `"commands"` - CLI commands only
  - `"services"` - Services documentation
  - `"concepts"` - Architecture/concept docs
  - `"examples"` - Code examples

**Returns**: Array of relevant documentation chunks with context

**Example Usage:**
```python
# Find how to enable payments
hostkit_search(
  query="how do I enable payments",
  limit=5,
  filter="services"
)

# Find deployment examples
hostkit_search(
  query="deploy next.js app",
  filter="examples"
)
```

---

### 2. `hostkit_state`

**Purpose**: Retrieve live VPS state with intelligent caching (reduces SSH calls)

**Parameters:**
- `scope` (string, optional, default="all"): What state to retrieve
  - `"all"` - Projects + health + resources
  - `"projects"` - All projects with status, services, URLs
  - `"health"` - VPS CPU, memory, disk usage
  - `"resources"` - Detailed resource breakdown
  - `"project"` - Single project info + health (requires `project` param)
- `project` (string, optional): Project name (required when scope="project")
- `refresh` (boolean, optional, default=false): Force cache bypass

**Returns**:
- `scope="all"`: `{projects: [...], health: {...}, resources: {...}}`
- `scope="projects"`: Array of project objects with status, port, domain, enabled services
- `scope="health"`: `{cpu: %, memory: %, disk: %}`
- `scope="resources"`: Detailed breakdown per project and system
- `scope="project"`: Single project object with full details

**Example Usage:**
```python
# Check all projects and VPS health
hostkit_state(scope="all")

# List all projects
hostkit_state(scope="projects")

# Check single project
hostkit_state(scope="project", project="myapp")

# Get VPS health metrics
hostkit_state(scope="health")

# Force refresh (skip cache)
hostkit_state(scope="project", project="myapp", refresh=True)
```

---

### 3. `hostkit_execute`

**Purpose**: Execute any HostKit CLI command on the VPS

**Parameters:**
- `command` (string, required): The HostKit command to execute
  - Format: `"command-name [subcommand] [args] [flags]"`
- `project` (string, optional): Project name for context (auto-detected from HOSTKIT_PROJECT env)
- `json_mode` (boolean, optional, default=true): Add `--json` flag for machine-readable output

**Returns**: JSON object with `{success: boolean, data: {...}, error?: string}`

**Common Commands:**

```python
# PROJECT LIFECYCLE
hostkit_execute(command="provision myapp")  # Creates project with db + auth + storage
hostkit_execute(command="provision myapp --python --no-auth")  # Python, no auth
hostkit_execute(command="project create myapp --nextjs --with-db")  # Low-level (opt-in flags)
hostkit_execute(command="project list")
hostkit_execute(command="project info myapp")
hostkit_execute(command="project delete myapp --force")

# DEPLOYMENT
hostkit_execute(command="deploy myapp --install --build --restart")
hostkit_execute(command="rollback myapp")
hostkit_execute(command="health myapp")

# SERVICES
hostkit_execute(command="auth enable myapp")
hostkit_execute(command="auth disable myapp --force")
hostkit_execute(command="auth config myapp --show")
hostkit_execute(command="auth users myapp")
hostkit_execute(command="payments enable myapp")
hostkit_execute(command="payments disable myapp")
hostkit_execute(command="minio enable myapp --public")
hostkit_execute(command="mail enable myapp")
hostkit_execute(command="sms enable myapp")

# SYSTEMD SERVICE
hostkit_execute(command="service start myapp")
hostkit_execute(command="service stop myapp")
hostkit_execute(command="service restart myapp")
hostkit_execute(command="service status myapp")
hostkit_execute(command="service logs myapp --follow")
hostkit_execute(command="service logs myapp --tail 50")

# ENVIRONMENT VARIABLES
hostkit_execute(command="env list myapp")
hostkit_execute(command="env get myapp PORT")
hostkit_execute(command="env set myapp DEBUG=true --restart")
hostkit_execute(command="env unset myapp DEBUG --restart")

# DATABASE
hostkit_execute(command="db create myapp")
hostkit_execute(command="db shell myapp")
hostkit_execute(command="db dump myapp")

# DIAGNOSTICS
hostkit_execute(command="diagnose myapp")
hostkit_execute(command="diagnose myapp --run-test")
```

**Example Usage:**
```python
# Deploy with build
result = hostkit_execute(
  command="deploy myapp --install --build --restart",
  project="myapp",
  json_mode=True
)

# Check logs
hostkit_execute(
  command="service logs myapp --follow",
  json_mode=False  # Get raw output
)
```

---

### 4. `hostkit_deploy_local`

**Purpose**: Deploy from local filesystem to HostKit (rsync + build + health check). Auto-provisions the project if it doesn't exist.

**Parameters:**
- `local_path` (string, required): Path to local directory to deploy
  - For Next.js pre-built: contains `.next/`, `public/`, `node_modules/`, `package.json`
  - For source: contains full source code, `package.json`, `next.config.js`
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `install` (boolean, optional, default=false): Run `npm install` on VPS after deploy
- `build` (boolean, optional, default=false): Run `npm run build` on VPS before deploy
- `wait_healthy` (boolean, optional, default=true): Poll health check for up to 2 minutes
- `cleanup` (boolean, optional, default=true): Remove temporary files on VPS after deploy
- `override_ratelimit` (boolean, optional, default=false): Bypass deploy rate limiting (10/hour)
- `auto_provision` (boolean, optional, default=true): Auto-provision project if it doesn't exist (creates project with db, auth, storage). Runtime is auto-detected from local path contents.

**Returns**: `{success: boolean, deployed: boolean, healthy: boolean, auto_provisioned: boolean, provision_result?: {...}}`

**Deployment Steps:**
0. Check if project exists — if not, auto-detect runtime and run `provision` (idempotent)
1. Validate local path exists and is readable
2. Rsync files to VPS (`/home/{project}/releases/{timestamp}/`)
3. Smart detection: Next.js standalone vs standard mode
4. Run npm install (if `install=true`)
5. Run npm build (if `build=true`) - timeout: 10 minutes
6. Activate release (atomic symlink swap: `app` → `releases/{timestamp}/`)
7. Restart systemd service
8. Poll health check (if `wait_healthy=true`) - timeout: 2 minutes
9. Cleanup old releases (keep 5 most recent)
10. Return results

**Runtime Auto-Detection** (when auto-provisioning):
- `next.config.*` → nextjs
- `package.json` (no next.config) → node
- `requirements.txt` / `pyproject.toml` → python
- `index.html` (no package.json) → static
- fallback → nextjs

**Timeline:**
- Pre-built deploy (install=false, build=false): 2-5 minutes
- Source deploy (install=true, build=true): 15-25 minutes

**Example Usage:**
```python
# Option 1: Deploy pre-built Next.js app (fastest)
hostkit_deploy_local(
  project="my-app",
  local_path="/Users/user/my-app/.next",
  install=True,
  build=False,
  wait_healthy=True
)

# Option 2: Deploy source and build on VPS
hostkit_deploy_local(
  project="my-app",
  local_path="/Users/user/my-app",
  install=True,
  build=True,
  wait_healthy=True
)

# Option 3: Quick deploy without health check
hostkit_deploy_local(
  project="my-app",
  local_path="/Users/user/my-app",
  install=False,
  build=False,
  wait_healthy=False,
  override_ratelimit=True
)
```

---

### 5. `hostkit_env_get`

**Purpose**: Retrieve environment variables from a project

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `keys` (array of strings, optional): Specific variable names to retrieve
  - If omitted, returns all variables
  - Example: `["PORT", "DATABASE_URL", "STRIPE_KEY"]`

**Returns**: `{variables: {KEY: "value", ...}}`

**Example Usage:**
```python
# Get all variables
hostkit_env_get(project="myapp")

# Get specific variables
hostkit_env_get(
  project="myapp",
  keys=["PORT", "DATABASE_URL", "REDIS_URL"]
)
```

---

### 6. `hostkit_env_set`

**Purpose**: Set environment variables for a project

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `variables` (object, required): Key-value pairs to set
  - Example: `{"DEBUG": "true", "LOG_LEVEL": "debug"}`
  - Special handling: `NEXT_PUBLIC_*` variables become client-visible
- `restart` (boolean, optional, default=false): Restart service after setting variables

**Returns**: `{success: boolean, variables_set: [...]}`

**Important**: Changes require service restart to take effect (unless `restart=true`)

**Example Usage:**
```python
# Set variables and restart
hostkit_env_set(
  project="myapp",
  variables={
    "DEBUG": "true",
    "LOG_LEVEL": "debug",
    "STRIPE_KEY": "sk_test_xxx"
  },
  restart=True
)

# Set client-visible variable (Next.js)
hostkit_env_set(
  project="myapp",
  variables={
    "NEXT_PUBLIC_API_URL": "https://api.example.com"
  },
  restart=True
)
```

---

### 7. `hostkit_db_schema`

**Purpose**: Retrieve database schema for a project

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `table` (string, optional): Specific table name
  - If omitted, returns all tables

**Returns:**
- Without table: `{tables: [{name, columns: [...], indexes: [...], constraints: [...]}, ...]}`
- With table: `{table: {name, columns: [{name, type, nullable, default}], indexes, constraints, foreign_keys}}`

**Example Usage:**
```python
# Get full schema
hostkit_db_schema(project="myapp")

# Get specific table schema
hostkit_db_schema(project="myapp", table="users")
```

---

### 8. `hostkit_db_query`

**Purpose**: Execute SQL queries against project database

**Parameters:**
- `query` (string, required): SQL query to execute
  - SELECT: Always allowed, no permissions needed
  - INSERT/UPDATE/DELETE: Requires `allow_write=true`
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `allow_write` (boolean, optional, default=false): Enable write operations
- `limit` (integer, optional, default=100, max=100): Max rows for SELECT queries

**Returns:**
- SELECT: `{rows: [{...}, {...}], count: number}`
- INSERT/UPDATE/DELETE: `{rows_affected: number}`

**Example Usage:**
```python
# Read query
hostkit_db_query(
  project="myapp",
  query="SELECT * FROM users WHERE active=true",
  limit=50
)

# Write query (requires allow_write)
hostkit_db_query(
  project="myapp",
  query="UPDATE users SET active=true WHERE id=1",
  allow_write=True
)

# Complex query
hostkit_db_query(
  project="myapp",
  query="SELECT id, email, created_at FROM users ORDER BY created_at DESC LIMIT 10"
)
```

---

### 9. `hostkit_wait_healthy`

**Purpose**: Poll service health endpoint until healthy or timeout

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)
- `timeout` (integer, optional, default=120000): Max wait time in milliseconds (max 600000 = 10 min)
- `interval` (integer, optional, default=5000): Check interval in milliseconds

**Returns:** `{healthy: boolean, status: "ok"|"timeout"|"error", checks: {...}}`

**Health Check Details:**
- Endpoint: `GET /api/health` on project service
- Expected response: `{status: "ok"}`
- Success condition: HTTP 200 with valid JSON response

**Example Usage:**
```python
# Standard: wait 2 minutes (120000ms)
hostkit_wait_healthy(project="myapp")

# Custom timeout: 5 minutes, check every 10 seconds
hostkit_wait_healthy(
  project="myapp",
  timeout=300000,
  interval=10000
)

# Aggressive: check every 1 second, timeout after 30 seconds
hostkit_wait_healthy(
  project="myapp",
  timeout=30000,
  interval=1000
)
```

---

### 10. `hostkit_validate`

**Purpose**: Pre-flight validation before deployment

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)

**Returns:** `{valid: boolean, checks: {entrypoint, dependencies, env_vars, database, port, service}, warnings: [...]}`

**Checks Performed:**
- Entry point exists (`app/index.js` for Node, `server.js` for Next.js)
- Dependencies installed (`node_modules/` exists or installable)
- Required environment variables set
- Database connection (if DB enabled)
- Port available (8001+)
- Service is running or can start

**Example Usage:**
```python
# Pre-deploy validation
result = hostkit_validate(project="myapp")
if result["valid"]:
    print("Ready to deploy")
else:
    print("Validation failed:", result["checks"])
```

---

### 11. `hostkit_fix_permissions`

**Purpose**: Detect and fix sudoers permission gaps (self-healing)

**Parameters:**
- `action` (string, required): What to do
  - `"analyze"` - Check for permission gaps
  - `"fix"` - Fix permissions for specific project
  - `"sync"` - Fix all projects' permissions
- `project` (string, optional): Project name (required for action="fix")
- `error_output` (string, optional): Permission error text to analyze

**Returns:** `{success: boolean, fixes_applied: [...], warnings: [...]}`

**Example Usage:**
```python
# Analyze all permission gaps
hostkit_fix_permissions(action="analyze")

# Fix specific project
hostkit_fix_permissions(action="fix", project="myapp")

# Fix all projects
hostkit_fix_permissions(action="sync")

# Analyze specific error
hostkit_fix_permissions(
  action="analyze",
  error_output="sudo: user myapp is not in the sudoers file"
)
```

---

### 12. `hostkit_solutions`

**Purpose**: Cross-project learning database (problems solved once benefit all)

**Parameters:**
- `action` (string, required): What to do
  - `"search"` - Find similar issues
  - `"record"` - Save a solution
  - `"list"` - Show recent solutions
- `query` (string, optional): Search query (for action="search")
- `problem` (string, optional): Problem description (for action="record")
- `solution` (string, optional): Solution description (for action="record")
- `project` (string, optional): Project name (for action="record")
- `tags` (array, optional): Tags for categorization (for action="record")
- `limit` (integer, optional, default=5): Max results (for search/list)

**Returns:** `{solutions: [{problem, solution, project, tags, created_at}]}`

**Example Usage:**
```python
# Search for similar issues
hostkit_solutions(
  action="search",
  query="nginx 502 error",
  limit=5
)

# Record a solution
hostkit_solutions(
  action="record",
  problem="Deployment timeout on npm install",
  solution="Removed large @types/* packages and used npm ci instead",
  project="myapp",
  tags=["npm", "timeout", "performance", "dependencies"]
)

# List recent solutions
hostkit_solutions(
  action="list",
  limit=10
)
```

---

### 13. `hostkit_auth_guide`

**Purpose**: Get runtime-specific authentication code examples and warnings

**Parameters:**
- `project` (string, optional): Project name (defaults to HOSTKIT_PROJECT env var)

**Returns:**
```json
{
  "critical_warning": "DO NOT implement OAuth yourself. The auth service handles everything.",
  "integration_points": {
    "oauth_redirect": "/auth/oauth/{provider}/login",
    "token_verification": "/auth/oauth/{provider}/verify-token",
    "user_info": "/auth/me",
    "jwt_validation": "Use AUTH_JWT_PUBLIC_KEY"
  },
  "do_not": [...],
  "instead_do": [...],
  "code_examples": {
    "nextjs": {...},
    "python": {...}
  }
}
```

**Example Usage:**
```python
# Get auth integration guide for project
hostkit_auth_guide(project="myapp")
```

---

### 14. `hostkit_capabilities`

**Purpose**: Get all available HostKit commands, services, flags, and runtimes

**Parameters:**
- `project` (string, optional): Project name to also fetch project-specific capabilities

**Returns:**
```json
{
  "version": "1.0.0",
  "commands": {
    "project": {...},
    "deploy": {...},
    "service": {...}
  },
  "services": {
    "auth": {...},
    "payments": {...}
  },
  "runtimes": {
    "python": {...},
    "node": {...},
    "nextjs": {...}
  }
}
```

**Example Usage:**
```python
# Get all capabilities
hostkit_capabilities()

# Get project-specific capabilities
hostkit_capabilities(project="myapp")
```

---

## Quick Reference Table

| Tool | Purpose | Key Parameters | Timeout |
|------|---------|-----------------|---------|
| `hostkit_search` | Documentation search | query, limit, filter | None |
| `hostkit_state` | Live VPS state | scope, project, refresh | 30s |
| `hostkit_execute` | Run HostKit commands | command, project, json_mode | Varies |
| `hostkit_deploy_local` | Deploy from filesystem (auto-provisions) | local_path, install, build, auto_provision | 25min |
| `hostkit_env_get` | Get env variables | project, keys | 10s |
| `hostkit_env_set` | Set env variables | project, variables, restart | 30s |
| `hostkit_db_schema` | Get DB schema | project, table | 10s |
| `hostkit_db_query` | Query database | query, project, allow_write, limit | 30s |
| `hostkit_wait_healthy` | Poll health check | project, timeout, interval | User-set |
| `hostkit_validate` | Pre-flight validation | project | 30s |
| `hostkit_fix_permissions` | Fix sudoers | action, project, error_output | 30s |
| `hostkit_solutions` | Learn from past issues | action, query, limit | 10s |
| `hostkit_auth_guide` | Auth code examples | project | 10s |
| `hostkit_capabilities` | List capabilities | project | 10s |

---

## Tool Categories

### State & Monitoring
- `hostkit_state` - Query current VPS/project state
- `hostkit_health` - Check service health
- `hostkit_validate` - Pre-flight checks

### Deployment
- `hostkit_deploy_local` - Deploy from filesystem
- `hostkit_execute` (deploy subcommand) - Deploy via CLI
- `hostkit_wait_healthy` - Wait for app to be ready

### Configuration
- `hostkit_env_get` - Read environment variables
- `hostkit_env_set` - Write environment variables
- `hostkit_execute` (env subcommand) - Complex env operations

### Database
- `hostkit_db_schema` - Inspect database structure
- `hostkit_db_query` - Read/write data
- `hostkit_execute` (db subcommand) - Backups, migrations

### Services
- `hostkit_execute` (service subcommand) - Start/stop/restart
- `hostkit_execute` (auth/payments/etc) - Enable/disable services

### Diagnostics
- `hostkit_fix_permissions` - Fix permission issues
- `hostkit_solutions` - Search/record solutions
- `hostkit_execute` (logs/diagnose) - View logs and diagnostics

### Documentation & Discovery
- `hostkit_search` - Find docs and examples
- `hostkit_capabilities` - List all available commands
- `hostkit_auth_guide` - Auth integration examples

---

**Last updated**: February 2026 · HostKit v0.2.34
