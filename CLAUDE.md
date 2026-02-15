# HostKit - Substrate Intelligence Documentation

## Quick Navigation

| Use Case | Go To |
|----------|-------|
| **First time?** | [Architecture & Lifecycle →](ARCHITECTURE.md) |
| **Adding authentication?** | [Auth System →](AUTH.md) |
| **OAuth or Magic Links?** | [Provider Details →](AUTH-PROVIDERS.md) |
| **Auth not working?** | [Auth Troubleshooting →](AUTH-TROUBLESHOOTING.md) |
| **Deploying Next.js?** | [Next.js Complete Guide →](NEXTJS.md) |
| **Deployment failing?** | [Troubleshooting →](TROUBLESHOOTING.md) |
| **How does deploy work?** | [Deployment Pipeline →](DEPLOYMENT.md) |
| **Managing environment?** | [Environment Variables →](ENVIRONMENT.md) |
| **MCP tools reference?** | [See below](#mcp-tools---primary-interface) |
| **Need example?** | [Project Template →](NEXTJS.md#recommended-project-structure) |

---

## I Am The HostKit Substrate Intelligence

I have root access to `145.223.74.213` and am responsible for:
- ✅ Debugging & developing HostKit platform code
- ✅ Providing platform architecture docs
- ✅ Executing root-level operations (project creation, service management)
- ✅ Creating migration documents for project agents

**Project agents**: You deploy & manage your own project code. HostKit is your deployment platform.

---

## What is HostKit?

HostKit is a **VPS-native deployment platform** where each project runs as:

```
Project "myapp":
├── Linux user: myapp (isolated)
├── Home: /home/myapp/
├── Service: hostkit-myapp (systemd)
├── Port: 8001 (assigned)
├── Domain: myapp.hostkit.dev (auto, SSL included)
├── Database: PostgreSQL (optional)
└── Storage: Redis + MinIO (automatic)
```

**Key characteristics**:
- Each project is a Linux user with isolated home directory
- Services managed by systemd (automatic restart, logging)
- Nginx reverse proxy handles HTTPS/domains
- Atomic deployments (zero downtime)
- Instant rollbacks (just a symlink change)

**Read more**: [Full Architecture →](ARCHITECTURE.md)

---

## MCP Tools - The Primary Interface

All HostKit operations go through MCP tools. **Never construct SSH commands manually.**

### hostkit_search

**Semantic search over HostKit documentation.**

```python
hostkit_search(
  query="how do I enable payments",
  limit=5,
  filter="services"  # all | commands | services | concepts | examples
)
```

Use this to:
- Learn how services work
- Find the right command
- Understand configuration options
- Find troubleshooting patterns

---

### hostkit_state

**Live VPS state with intelligent caching.**

```python
hostkit_state(
  scope="projects",     # all | projects | health | resources | project
  project="myapp",      # required when scope is "project"
  refresh=False         # force cache bypass
)
```

**Scopes:**
| Scope | Returns |
|-------|---------|
| `all` | Projects + health + resources |
| `projects` | All projects with status, services, URLs |
| `health` | VPS CPU, memory, disk |
| `resources` | Detailed resource breakdown |
| `project` | Single project info + health |

Use before any operation to check current state.

---

### hostkit_execute

**Execute any HostKit command.**

```python
hostkit_execute(
  command="deploy myapp --install --build",
  project="myapp",           # optional, auto-detected
  json_mode=True             # add --json flag (default)
)
```

**Common commands:**

```python
# Project lifecycle
hostkit_execute(command="project create myapp --nextjs --with-db")
hostkit_execute(command="project list")
hostkit_execute(command="deploy myapp --install --build --restart")
hostkit_execute(command="rollback myapp")

# Services
hostkit_execute(command="auth enable myapp")
hostkit_execute(command="payments enable myapp")
hostkit_execute(command="minio enable myapp --public")

# Operations
hostkit_execute(command="health myapp")
hostkit_execute(command="service logs myapp --follow")
hostkit_execute(command="env set myapp DEBUG=true --restart")

# Database
hostkit_execute(command="db create myapp")
hostkit_execute(command="db query myapp 'SELECT * FROM users'")
```

---

### hostkit_deploy_local

**Deploy from local filesystem** (rsync + build + health check).

```python
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/project",
  install=True,          # npm install in target
  build=True,            # npm run build before deploy
  wait_healthy=True,     # poll health for 2 min
  cleanup=True,          # remove temp files
  override_ratelimit=False
)
```

Automatically:
1. Rsyncs files to VPS
2. Builds (if build=True)
3. Installs dependencies
4. Activates release (atomic)
5. Restarts service
6. Polls health check

**Read more**: [Deployment Pipeline →](DEPLOYMENT.md)

---

### hostkit_validate

**Pre-flight checks for deployment.**

```python
hostkit_validate(project="myapp")
```

Checks:
- Entry point exists
- Dependencies installed
- Required env vars set
- Database connection (if DB)
- Port availability
- Service status

---

### hostkit_env_set / hostkit_env_get

**Manage environment variables.**

```python
# Get all or specific vars
hostkit_env_get(project="myapp")
hostkit_env_get(project="myapp", keys=["PORT", "DATABASE_URL"])

# Set variables
hostkit_env_set(
  project="myapp",
  variables={"DEBUG": "true", "LOG_LEVEL": "debug"},
  restart=True  # restart service after setting
)
```

**Read more**: [Environment Variables →](ENVIRONMENT.md)

---

### hostkit_db_schema / hostkit_db_query

**Database operations.**

```python
# Get schema
hostkit_db_schema(project="myapp")
hostkit_db_schema(project="myapp", table="users")

# Query database
hostkit_db_query(project="myapp", query="SELECT * FROM users LIMIT 10")
hostkit_db_query(
  project="myapp",
  query="UPDATE users SET active=true WHERE id=1",
  allow_write=True  # required for write operations
)
```

---

### hostkit_wait_healthy

**Poll until service is healthy.**

```python
hostkit_wait_healthy(
  project="myapp",
  timeout=120000,  # 2 minutes (in ms)
  interval=5000    # check every 5 seconds
)
```

Useful after deployments/restarts.

---

### hostkit_fix_permissions

**Detect and fix sudoers gaps** (self-healing permission system).

```python
hostkit_fix_permissions(action="analyze")
hostkit_fix_permissions(action="fix", project="myapp")
hostkit_fix_permissions(action="sync")  # fix all projects
```

---

### hostkit_solutions

**Cross-project learning database** (problems solved once, benefit all).

```python
# Search for similar issues
hostkit_solutions(
  action="search",
  query="nginx 502 error"
)

# Record a solution you discovered
hostkit_solutions(
  action="record",
  problem="Deployment timeout on npm install",
  solution="Removed large @types/* packages",
  project="myapp",
  tags=["npm", "timeout", "performance"]
)
```

---

## Key Concepts

### Project Creation Flow

```
hostkit_execute(command="project create myapp --nextjs --with-db")
    ↓
1. Validate project name (lowercase, hyphens only)
2. Create Linux user & home directory
3. Create directory structure (/home/myapp/releases, /home/myapp/app)
4. Assign port (8001+) and Redis DB
5. Create .env with defaults (PORT, HOST, REDIS_URL, etc.)
6. Generate systemd service file
7. Register in database
8. Auto-register myapp.hostkit.dev domain with SSL
```

**Read more**: [Full Lifecycle →](ARCHITECTURE.md#project-provisioning-lifecycle)

---

### Deployment Flow

```
Source code
    ↓
[Optional: npm install + npm run build]
    ↓
Create release directory (timestamped)
    ↓
Rsync files (smart detection: Next.js standalone vs standard)
    ↓
[Optional: npm install]
    ↓
Activate release (atomic symlink swap)
    ↓
Restart service
    ↓
Poll health check (/api/health) for 2 minutes
    ↓
Cleanup old releases (keep 5)
    ↓
Success or rollback
```

**Timeline**: 5-25 minutes depending on flags

**Read more**: [Deployment Pipeline →](DEPLOYMENT.md)

---

### Next.js Specifics

**Critical requirement**:
```javascript
// next.config.js
module.exports = {
  output: 'standalone',  // ⚠️ REQUIRED - do not omit
  reactStrictMode: true,
};
```

**Two build modes detected**:
1. **Next.js Standalone** (recommended): `.next/standalone/server.js` → `node server.js`
2. **Next.js Standard** (fallback): `.next/` + `npm start`

**Required health endpoint**:
```typescript
// app/api/health/route.ts
export async function GET() {
  return Response.json({ status: 'ok' });
}
```

**Port binding**: Automatic (Next.js reads `process.env.PORT`)

**Read more**: [Next.js Complete Guide →](NEXTJS.md)

---

### Environment Variables

**How they work**:
1. Systemd loads from `/home/{project}/.env`
2. All `KEY=VALUE` lines become environment variables
3. Process starts with these vars in environment
4. No hot reload — restart required for changes

**Variable types**:
- **Server-only**: `DATABASE_URL`, `API_SECRET`
- **Client-visible**: `NEXT_PUBLIC_*` prefix (for Next.js)

**Format**:
```bash
KEY=VALUE
KEY="value with spaces"
# Comments supported
# Empty lines ignored
```

**Sourced by**: Systemd service via `EnvironmentFile=/home/{project}/.env`

**Read more**: [Environment Variables →](ENVIRONMENT.md)

---

## Common Operations

### Deploy Next.js App

```python
# Option 1: Deploy local build (fastest)
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/app",  # Contains .next/, public/, node_modules/
  install=True,
  build=False,  # Already built
  wait_healthy=True
)

# Option 2: Deploy source & build on VPS
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",
  install=True,
  build=True,  # Build on VPS
  wait_healthy=True
)
```

**Timeline**: 2-5 min (option 1) or 15-25 min (option 2)

---

### Check Project Status

```python
hostkit_state(scope="project", project="myapp")
```

Returns: service status, port, domains, enabled services, resources

---

### View Logs

```python
hostkit_execute(command="service logs myapp --follow")
hostkit_execute(command="service logs myapp --tail 50")
hostkit_execute(command="service logs myapp --stderr-only")
```

---

### Enable a Service

```python
# Auth service (handles OAuth)
hostkit_execute(command="auth enable myapp")

# Payments (Stripe integration)
hostkit_execute(command="payments enable myapp")

# Storage (MinIO S3-compatible)
hostkit_execute(command="minio enable myapp --public")

# Database (PostgreSQL, if not created at project creation)
hostkit_execute(command="db create myapp")
```

---

### Manage Authentication

```python
# Enable auth with OAuth providers
hostkit_execute(command="auth enable myapp --google-client-id=xxx --google-client-secret=yyy")

# View auth configuration
hostkit_execute(command="auth config myapp --show")

# List authenticated users
hostkit_execute(command="auth users myapp")

# Check auth service logs
hostkit_execute(command="auth logs myapp --follow")

# Disable auth (be careful!)
hostkit_execute(command="auth disable myapp --force")
```

**See also**: [Authentication System →](AUTH.md)

---

### Rollback to Previous Release

```python
hostkit_execute(command="rollback myapp")
```

**What happens**:
- Symlink `app` → `releases/{previous}/`
- Service restarts
- Instant (no build needed)
- **Note**: Database changes NOT rolled back (requires checkpoint restore)

---

### Diagnose Issues

```python
# Check service health
hostkit_execute(command="health myapp")

# Get diagnostic info
hostkit_execute(command="diagnose myapp")

# Test startup
hostkit_execute(command="diagnose myapp --run-test")

# View logs
hostkit_execute(command="service logs myapp --follow")
```

---

## Critical Constraints

### DO NOT

❌ **Hardcode PORT**: Use `process.env.PORT`

❌ **Skip `--install` or `--build`**: Always include when deploying code

❌ **Skip `output: 'standalone'`**: Required in next.config.js

❌ **Modify systemd service**: HostKit manages it

❌ **SSH into project user**: Work through HostKit CLI/MCP

❌ **Commit .env with secrets**: Use vault + `--inject-secrets`

### Limits

| Resource | Limit |
|----------|-------|
| npm install timeout | 10 minutes |
| npm build timeout | 10 minutes |
| Health check timeout | 2 minutes |
| Deploy rate | 10/hour (bypass: `--override-ratelimit`) |
| Releases kept | 5 most recent |
| Project name | 3-32 chars, lowercase, hyphens only |

---

## Troubleshooting Quick Links

- **Auth issues** → [Auth Troubleshooting →](AUTH-TROUBLESHOOTING.md)
  - Email not sending → [Solution →](AUTH-TROUBLESHOOTING.md#email-not-sending)
  - Token refresh failing → [Solution →](AUTH-TROUBLESHOOTING.md#token-refresh-failing)
  - OAuth sign-in fails → [Solution →](AUTH-TROUBLESHOOTING.md#oauth-provider-sign-in-fails)
  - Multi-tab logout issues → [Solution →](AUTH-TROUBLESHOOTING.md#multi-tab-session-issues)
- **Deploy fails** → [Troubleshooting →](TROUBLESHOOTING.md)
- **App won't start** → [Runtime Errors →](TROUBLESHOOTING.md#common-runtime-errors)
- **Health check fails** → [Health Check Issues →](TROUBLESHOOTING.md#health-check-fails)
- **Environment vars undefined** → [Env Vars →](TROUBLESHOOTING.md#environment-variables-undefined)
- **npm install timeout** → [Build Timeout →](TROUBLESHOOTING.md#npm-install-timeout-10-minutes)
- **Next.js specific** → [NEXTJS.md →](NEXTJS.md#common-nextjs-issues-on-hostkit)

---

## Documentation Structure

```
CLAUDE.md                    ← You are here (index + MCP reference)
├── ARCHITECTURE.md          ← System design, provisioning, directory layout
├── DEPLOYMENT.md            ← Full deploy pipeline, modes, timeouts
├── NEXTJS.md                ← Next.js specifics, config, build output
├── ENVIRONMENT.md           ← Env vars, secrets, configuration
├── TROUBLESHOOTING.md       ← Common issues & solutions
│
├── AUTH.md                  ← Authentication system overview
├── AUTH-PROVIDERS.md        ← Email/password, OAuth, magic links, providers
└── AUTH-TROUBLESHOOTING.md  ← Auth-specific issues & solutions
```

Each document is designed to be:
- **Standalone**: Can be read independently
- **Linked**: Cross-references to related docs
- **Detailed**: Includes examples and exact commands
- **Searchable**: Tables, clear headings, grep-friendly

**Auth Documents**:
- **AUTH.md**: System architecture, token management, session handling, integration
- **AUTH-PROVIDERS.md**: Request/response formats for each provider (Google, Apple, email, magic links, anonymous)
- **AUTH-TROUBLESHOOTING.md**: Common auth issues with step-by-step solutions

---

## For Project Agents

If you're a Claude Code agent running on HostKit:

1. **Read**: [Architecture →](ARCHITECTURE.md) to understand your environment
2. **Learn**: [Next.js Guide →](NEXTJS.md) for app-specific requirements
3. **Add Auth** (optional): [AUTH →](AUTH.md) for authentication setup
   - [Provider Details →](AUTH-PROVIDERS.md) for OAuth, magic links, etc.
4. **Deploy**: [Deployment →](DEPLOYMENT.md) for detailed pipeline
5. **Troubleshoot**: [Troubleshooting →](TROUBLESHOOTING.md) when things break
   - [Auth Issues →](AUTH-TROUBLESHOOTING.md) for authentication problems
6. **Reference**: [Environment →](ENVIRONMENT.md) for config & secrets

---

## For Platform Developers

If you're working on HostKit itself:

The codebase is organized as:
```
packages/
├── cli/                     # Python CLI (runs on VPS at 145.223.74.213)
│   ├── src/hostkit/
│   │   ├── commands/        # CLI commands (project, deploy, health, etc.)
│   │   └── services/        # Core business logic (deploy_service, project_service, etc.)
│   └── templates/           # Project templates (nextjs, auth, payment, etc.)
├── mcp-server/              # TypeScript MCP server (runs locally)
│   ├── src/                 # MCP tools, SSH, search, indexers
│   └── data/index/          # Search index (pre-built)
└── agent/                   # Claude Code agent template
    ├── CLAUDE.md.template   # Agent identity doc for project agents
    └── claude-settings.json # Permission config
```

**Key services**:
- `ProjectService` - Project creation/deletion/management
- `DeployService` - Build & deployment logic
- `HealthService` - Health checks and diagnostics
- `NginxService` - Reverse proxy & domain management
- `EnvService` - Environment variable management
- `ReleaseService` - Release-based deployments & rollbacks

---

## Links & Resources

- **VPS Address**: `145.223.74.213`
- **HostKit Version**: 0.2.33
- **Python Version**: 3.11+
- **Node.js**: Latest LTS (available on VPS)
- **GitHub**: hostkit-platform/hostkit

---

**Last updated**: February 2025 · HostKit v0.2.33
