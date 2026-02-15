# HostKit - Substrate Intelligence Documentation

## Quick Navigation

| Task | Document | What You'll Learn |
|------|----------|-------------------|
| **Need a tool to do X?** | [MCP Tools Reference →](MCP-TOOLS.md) | All 14 MCP tools: parameters, returns, examples |
| **Deploying an app?** | [Deployment Workflow →](DEPLOYMENT-WORKFLOW.md) | Phase-by-phase guide: setup → health check → rollback |
| **Deployment failed?** | [Deployment Failures →](DEPLOYMENT-FAILURES.md) | Solutions for timeouts, 502 errors, build failures |
| **First time?** | [Architecture & Lifecycle →](ARCHITECTURE.md) | How HostKit works internally |
| **Deploying Next.js?** | [Next.js Guide →](NEXTJS.md) | `output: 'standalone'`, config, health check |
| **Adding auth?** | [Auth System →](AUTH.md) | Authentication architecture and integration |
| **Setting up OAuth?** | [Auth Providers →](AUTH-PROVIDERS.md) | Google, Apple, magic links, email/password |
| **Auth broken?** | [Auth Troubleshooting →](AUTH-TROUBLESHOOTING.md) | Fix email, tokens, sessions, OAuth sign-in |
| **Managing config?** | [Environment Variables →](ENVIRONMENT.md) | Secrets, env var format, per-project config |

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

**→ [Full MCP Tools Reference →](MCP-TOOLS.md)** (Complete documentation of all 14 tools)

Quick reference of core tools:

### hostkit_search
Semantic search over HostKit documentation. Find how to do things.

### hostkit_state
Query current VPS/project state. Always use before operations.

### hostkit_execute
Run any HostKit CLI command: deploy, rollback, service management, etc.

### hostkit_deploy_local
Deploy from local filesystem (rsync + build + health check).
- Strategy A: Pre-built (2-5 min)
- Strategy B: Build on VPS (15-25 min)

### hostkit_env_get / hostkit_env_set
Read and write environment variables.

### hostkit_db_schema / hostkit_db_query
Inspect and query databases.

### hostkit_wait_healthy
Poll health check endpoint until healthy.

### hostkit_validate
Pre-flight validation before deployment.

### hostkit_solutions / hostkit_fix_permissions
Diagnostics and cross-project learning.

**→ [Full details in MCP-TOOLS.md →](MCP-TOOLS.md)**

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
Phase 0: Setup       → Create project, verify
Phase 1: Prepare     → Build locally (Strategy A) or prepare source (Strategy B)
Phase 2: Validate    → Check VPS health, rate limits
Phase 3: Env Setup   → Set environment variables
Phase 4: Database    → Migrations, schema setup
Phase 5: Deploy      → Rsync → Install/Build → Activate → Restart
Phase 6: Health      → Poll /api/health until ready
Phase 7: Verify      → Check env vars, test database, review logs
Phase 8: Rollback    → If needed: instant symlink swap to previous
```

**Timeline**:
- Pre-built deploy: 2-5 minutes
- Build on VPS: 15-25 minutes

**Full details**: [Deployment Workflow →](DEPLOYMENT-WORKFLOW.md) | [Troubleshooting →](DEPLOYMENT-FAILURES.md)

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

**→ [Full Deployment Workflow →](DEPLOYMENT-WORKFLOW.md)** (8-phase guide)

```python
# Strategy A: Deploy pre-built (fastest, 2-5 min)
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/app",
  install=True,
  build=False,
  wait_healthy=True
)

# Strategy B: Deploy source & build on VPS (15-25 min)
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",
  install=True,
  build=True,
  wait_healthy=True
)
```

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

**Deployment Issues** → [Deployment Failures →](DEPLOYMENT-FAILURES.md)
- npm install timeout (10 min) → [Category A1](DEPLOYMENT-FAILURES.md#a1-npm-install-timeout-10-minutes)
- npm build timeout (10 min) → [Category A2](DEPLOYMENT-FAILURES.md#a2-npm-build-timeout-10-minutes)
- Health check timeout (2 min) → [Category A3](DEPLOYMENT-FAILURES.md#a3-health-check-timeout-2-minutes)
- Missing `output: 'standalone'` → [Category B1](DEPLOYMENT-FAILURES.md#b1-nextjs-missing-output-standalone)
- 502 Bad Gateway → [Category G1](DEPLOYMENT-FAILURES.md#g1-502-bad-gateway)
- 504 Gateway Timeout → [Category G2](DEPLOYMENT-FAILURES.md#g2-504-gateway-timeout)

**Authentication Issues** → [Auth Troubleshooting →](AUTH-TROUBLESHOOTING.md)
- Email not sending → [Solution](AUTH-TROUBLESHOOTING.md#email-not-sending)
- OAuth sign-in fails → [Solution](AUTH-TROUBLESHOOTING.md#oauth-provider-sign-in-fails)
- Session/cookie issues → [Category C3](DEPLOYMENT-FAILURES.md#c3-sessioncookie-issues)

**Database Issues** → [Deployment Failures](DEPLOYMENT-FAILURES.md)
- DATABASE_URL not set → [Category D1](DEPLOYMENT-FAILURES.md#d1-database_url-not-set)
- Migrations failed → [Category D2](DEPLOYMENT-FAILURES.md#d2-database-migrations-failed)
- Connection pool exhausted → [Category D3](DEPLOYMENT-FAILURES.md#d3-connection-pool-exhausted)

**Quick Diagnostic** → [Checklist](DEPLOYMENT-FAILURES.md#quick-diagnostic-checklist)

---

## Documentation Structure

```
CLAUDE.md                          ← You are here (quick reference + navigation)
│
├─ MCP TOOLS & DEPLOYMENT
├── MCP-TOOLS.md                   ← Complete reference (14 tools, parameters, returns)
├── DEPLOYMENT-WORKFLOW.md         ← Step-by-step deployment guide (Phase 0-8)
├── DEPLOYMENT-FAILURES.md         ← Troubleshooting (timeout, build, auth, DB, etc.)
│
├─ PLATFORM ARCHITECTURE
├── ARCHITECTURE.md                ← System design, provisioning, directory layout
├── NEXTJS.md                      ← Next.js specifics, config, build output
├── ENVIRONMENT.md                 ← Env vars, secrets, configuration
│
├─ AUTHENTICATION
├── AUTH.md                        ← Auth system overview
├── AUTH-PROVIDERS.md              ← OAuth, magic links, email/password providers
├── AUTH-TROUBLESHOOTING.md        ← Auth-specific issues & solutions
│
└─ GENERAL
    ├── TROUBLESHOOTING.md         ← General troubleshooting (legacy, see DEPLOYMENT-FAILURES.md)
    └── DEPLOYMENT.md              ← Legacy deployment docs (see DEPLOYMENT-WORKFLOW.md)
```

**Document Purposes**:

| Document | Purpose | Best For |
|----------|---------|----------|
| **MCP-TOOLS.md** | Complete tool reference | Looking up tool parameters, return values |
| **DEPLOYMENT-WORKFLOW.md** | Deployment lifecycle (8 phases) | Deploying an app, step-by-step guide |
| **DEPLOYMENT-FAILURES.md** | Troubleshooting failures | Debugging 502 errors, timeouts, crashes |
| **ARCHITECTURE.md** | System design | Understanding HostKit internals |
| **NEXTJS.md** | Next.js specifics | Next.js config, standalone mode |
| **AUTH.md** | Auth system | Understanding auth architecture |
| **AUTH-PROVIDERS.md** | OAuth/email details | Setting up specific auth providers |
| **ENVIRONMENT.md** | Env variable management | Configuration and secrets |

Each document is designed to be:
- **Standalone**: Can be read independently
- **Linked**: Cross-references to related docs
- **Detailed**: Includes examples and exact commands
- **Searchable**: Tables, clear headings, grep-friendly

---

## For Project Agents

If you're a Claude Code agent running on HostKit:

**Getting Started:**
1. **Tools**: [MCP Tools Reference →](MCP-TOOLS.md) - Understand what tools are available
2. **Architecture**: [Architecture →](ARCHITECTURE.md) - Understand your environment

**Deploying Your App:**
3. **Deployment Guide**: [Deployment Workflow →](DEPLOYMENT-WORKFLOW.md) - Step-by-step (Phase 0-8)
4. **Next.js Specifics**: [Next.js Guide →](NEXTJS.md) - Config, health check, build output

**Adding Features:**
5. **Authentication**: [Auth System →](AUTH.md) with [Providers →](AUTH-PROVIDERS.md)
6. **Configuration**: [Environment Variables →](ENVIRONMENT.md) for secrets & config

**When Things Break:**
7. **Troubleshooting**: [Deployment Failures →](DEPLOYMENT-FAILURES.md) - Timeouts, 502, crashes
8. **Auth Issues**: [Auth Troubleshooting →](AUTH-TROUBLESHOOTING.md) - Login, sessions, OAuth

**Quick Reference:**
- Deployment failing? → [Deployment Failures →](DEPLOYMENT-FAILURES.md)
- Need a specific tool? → [MCP Tools →](MCP-TOOLS.md)
- Can't remember a command? → Use `hostkit_search()` or [Search docs →](MCP-TOOLS.md#1-hostkit_search)

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
