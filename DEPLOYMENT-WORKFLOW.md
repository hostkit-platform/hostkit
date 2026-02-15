# HostKit Deployment Workflow

## Complete Guide: From Source Code to Running App

This document covers the full deployment lifecycle for Next.js applications on HostKit.

**Quick Navigation:**
- [Phase 0: Pre-Deployment Setup](#phase-0-pre-deployment-setup)
- [Phase 1: Local Preparation](#phase-1-local-preparation)
- [Phase 2: Pre-Deployment Validation](#phase-2-pre-deployment-validation)
- [Phase 3: Environment Variable Setup](#phase-3-environment-variable-setup)
- [Phase 4: Database Setup](#phase-4-database-setup)
- [Phase 5: Deployment](#phase-5-deployment)
- [Phase 6: Health Check and Validation](#phase-6-health-check-and-validation)
- [Phase 7: Post-Deployment](#phase-7-post-deployment)
- [Phase 8: Rollback](#phase-8-rollback)
- [Rsync Strategy](#rsync-strategy)
- [Health Check Details](#health-check-details)
- [SSL/DNS Configuration](#ssldns-configuration)

---

## Phase 0: Pre-Deployment Setup

Before deploying, ensure the project exists and is configured:

```python
# Step 0.1: Create the project (if new)
hostkit_execute(
  command="project create my-app --nextjs --with-db",
  json_mode=True
)
# Returns: {project_name, port, domain, database_url, redis_url, ...}

# Step 0.2: Verify project status
hostkit_state(scope="project", project="my-app")
# Returns: Current service status, port, domains, enabled services

# Step 0.3: Get current environment variables
current_env = hostkit_env_get(project="my-app")
# Returns all current env vars for inspection
```

---

## Phase 1: Local Preparation

**On your local machine**, before deploying:

```python
# Step 1.1: Build Next.js app locally
# Command line (in your app directory):
# $ npm run build

# This creates:
# - .next/standalone/      (required for deployment)
# - .next/static/          (Next.js static assets)
# - public/                (public assets)
# - node_modules/          (dependencies)

# Step 1.2: Choose deployment strategy:
# OPTION A: Deploy pre-built app (fastest, ~2-5 min)
# OPTION B: Deploy source and build on VPS (~15-25 min)
```

---

## Phase 2: Pre-Deployment Validation

**Before deploying, validate everything**:

```python
# Step 2.1: Validate project configuration
validation = hostkit_validate(project="my-app")
if not validation["valid"]:
    print("Validation errors:", validation["checks"])
    exit(1)

# Step 2.2: Check VPS health
vps_health = hostkit_state(scope="health")
if vps_health["resources"]["memory_percent"] > 85:
    print("Warning: VPS memory high, deploy may timeout")

# Step 2.3: Check for rate limiting
# HostKit allows 10 deploys/hour. Use override_ratelimit carefully:
# - True: Bypass rate limit (use during active dev)
# - False: Respect rate limit (use in production)
```

---

## Phase 3: Environment Variable Setup

**Critical: Set required environment variables BEFORE deploying**

### For Next.js Only:
```python
hostkit_env_set(
  project="my-app",
  variables={
    "NODE_ENV": "production",
    "NEXT_PUBLIC_API_URL": "https://my-app.hostkit.dev",
  },
  restart=False  # Don't restart yet, still deploying
)
```

### For Next.js + Database:
```python
hostkit_env_set(
  project="my-app",
  variables={
    "NODE_ENV": "production",
    "DATABASE_URL": "postgresql://...",  # Auto-set at creation
    "NEXT_PUBLIC_API_URL": "https://my-app.hostkit.dev",
  },
  restart=False
)
```

### For Next.js + Authentication:
```python
# First, enable auth service
hostkit_execute(command="auth enable my-app")

# Then set auth env vars
hostkit_env_set(
  project="my-app",
  variables={
    "AUTH_URL": "https://my-app.hostkit.dev",
    "AUTH_TRUST_HOST": "true",
    "AUTH_JWT_PUBLIC_KEY": "your-jwt-public-key",
  },
  restart=False
)
```

### For Next.js + Database + Stripe Payments:
```python
# Enable payments service
hostkit_execute(command="payments enable my-app")

# Set env vars
hostkit_env_set(
  project="my-app",
  variables={
    "STRIPE_PUBLIC_KEY": "pk_test_xxx",
    "STRIPE_SECRET_KEY": "sk_test_xxx",
    "STRIPE_WEBHOOK_SECRET": "whsec_xxx",
  },
  restart=False
)
```

### For Next.js + Redis Cache:
```python
# Redis URL is auto-set, but you can override:
redis_url = hostkit_env_get(project="my-app", keys=["REDIS_URL"])

hostkit_env_set(
  project="my-app",
  variables={
    "REDIS_URL": redis_url["variables"]["REDIS_URL"],
    "SESSION_STORE": "redis",
  },
  restart=False
)
```

---

## Phase 4: Database Setup (if applicable)

**If using PostgreSQL**:

```python
# Step 4.1: Verify database exists
db_schema = hostkit_db_schema(project="my-app")
# Returns: List of tables

# Step 4.2: Run migrations (if needed)
# Option A: Deploy source with --build, which runs migrations automatically
# Option B: Connect to database and run manual migrations:
hostkit_db_query(
  project="my-app",
  query="CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, email TEXT UNIQUE)",
  allow_write=True
)

# Step 4.3: Seed data (optional)
hostkit_db_query(
  project="my-app",
  query="INSERT INTO users (email) VALUES ('user@example.com') ON CONFLICT DO NOTHING",
  allow_write=True
)
```

---

## Phase 5: Deployment

**Choose one of two strategies**:

### STRATEGY A: Deploy Pre-Built App (Fastest)

**Recommended for CI/CD and production**

```python
# Prerequisites:
# 1. Run: npm run build locally
# 2. Build output includes: .next/standalone/, .next/static/, public/, package.json
# 3. Deploy directory contains built output

result = hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/output",  # Contains .next/, public/, node_modules/
  install=True,       # Install production dependencies
  build=False,        # Already built locally
  wait_healthy=True,  # Poll health for 2 minutes
  cleanup=True,       # Clean temp files
  override_ratelimit=False
)

# Timeline: 2-5 minutes
# Returns: {success, deployment_id, url, duration_ms}
```

**What happens:**
1. Rsync files to VPS: `/home/my-app/releases/{timestamp}/`
2. HostKit detects `.next/standalone/server.js`
3. npm install (production dependencies only)
4. Symlink swap: `/home/my-app/app` → `/releases/{timestamp}/`
5. Systemd service restarts
6. Poll `/api/health` for 2 minutes
7. Service returns to requests

### STRATEGY B: Deploy Source and Build on VPS

**For development, when you want HostKit to handle the build**

```python
result = hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",  # Contains package.json, next.config.js, src/
  install=True,       # Run npm install
  build=True,         # Run npm run build
  wait_healthy=True,  # Poll health for 2 minutes
  cleanup=True,
  override_ratelimit=True  # Use during active development
)

# Timeline: 15-25 minutes
# Breakdown: npm install (5-10 min) + npm run build (5-10 min) + deploy (2-5 min)
# Returns: {success, deployment_id, url, duration_ms}
```

**What happens:**
1. Rsync source to VPS
2. npm install (all dependencies)
3. npm run build (timeout: 10 minutes)
4. Symlink swap
5. Systemd service restarts
6. Poll health check
7. Clean up old releases (keep 5)

---

## Phase 6: Health Check and Validation

**After deployment, verify the app is healthy**:

```python
# Step 6.1: Explicit health check (if not auto-waiting)
health = hostkit_wait_healthy(
  project="my-app",
  timeout=120000,  # 2 minutes
  interval=5000    # Check every 5 seconds
)

if health["healthy"]:
    print("✓ App is healthy and responding")
else:
    print("✗ Health check failed, check logs")
    hostkit_execute(command="service logs my-app --tail 100")

# Step 6.2: Verify environment variables loaded
deployed_env = hostkit_env_get(project="my-app")
# Compare with what you set in Phase 3

# Step 6.3: Test database connection (if applicable)
db_test = hostkit_db_query(
  project="my-app",
  query="SELECT COUNT(*) as count FROM users"
)
print(f"Database connected: {db_test['rows'][0]['count']} users")

# Step 6.4: Check logs for errors
logs = hostkit_execute(command="service logs my-app --tail 20")
# Look for startup errors or warnings
```

---

## Phase 7: Post-Deployment

```python
# Step 7.1: Restart service to ensure all env vars loaded
hostkit_execute(
  command="service restart my-app",
  json_mode=True
)

# Step 7.2: Monitor logs
hostkit_execute(command="service logs my-app --follow")

# Step 7.3: Get public URLs
project_info = hostkit_state(scope="project", project="my-app")
print(f"App URL: {project_info['domains'][0]}")
print(f"Port: {project_info['port']}")

# Step 7.4: Record successful deployment
hostkit_solutions(
  action="record",
  problem="Deploy Next.js app with auth and database",
  solution="Used Strategy A: pre-built, deployed in 3 minutes",
  project="my-app",
  tags=["deployment", "nextjs", "auth", "database", "success"]
)
```

---

## Phase 8: Rollback (if needed)

**If deployment fails or introduces bugs**:

```python
# Step 8.1: Check recent releases
project_info = hostkit_state(scope="project", project="my-app")
# Shows current release and previous 4 releases

# Step 8.2: Rollback to previous release
result = hostkit_execute(
  command="rollback my-app",
  json_mode=True
)

# Timeline: Instant (< 10 seconds)
# What happens:
# 1. Symlink: /home/my-app/app → /home/my-app/releases/{previous}/
# 2. Systemd restarts
# 3. Service responds with previous code
# 4. Database NOT rolled back (requires checkpoint restore)

# Step 8.3: Verify rollback successful
hostkit_wait_healthy(project="my-app", timeout=30000)

# Step 8.4: Investigate failed deploy
logs = hostkit_execute(command="service logs my-app --tail 100")
```

---

## Rsync Strategy

### What Gets Sent to VPS

#### For Next.js Pre-Built Strategy:
```
Deploy Directory:
├── .next/
│   ├── standalone/
│   │   └── server.js    ← HostKit detects this
│   └── static/          ← Client assets
├── public/              ← Public files
├── package.json         ← Dependency manifest
├── package-lock.json    ← Lock file
├── node_modules/        ← (Optional, HostKit installs if missing)
└── next.config.js       ← Must have: output: 'standalone'
```

**HostKit skips:**
- `.git/` directories
- `.next/.turbopack/` (build cache)
- Large `.env` files with secrets

#### For Source Deploy:
```
Deploy Directory:
├── src/                 ← Source code
├── pages/ or app/       ← App directory (Next.js 13+)
├── package.json         ← Required
├── tsconfig.json        ← Type config
├── next.config.js       ← Must have: output: 'standalone'
├── public/              ← Public assets
└── .env.example         ← Template (actual .env stays on VPS)
```

**Important:**
- DO NOT include `.env` with secrets (stays on VPS)
- DO NOT include `node_modules/` (VPS installs fresh)
- DO NOT include `.next/` (VPS rebuilds)

### Create .deployignore

```
node_modules/
.next/
.env
dist/
build/
coverage/
*.log
.DS_Store
.git/
```

---

## Health Check Details

### Required Endpoint

```typescript
// app/api/health/route.ts (Next.js 13+ App Router)
export async function GET() {
  return Response.json({ status: 'ok' });
}
```

### Or (Pages Router):
```javascript
// pages/api/health.js
export default function handler(req, res) {
  res.status(200).json({ status: 'ok' });
}
```

### Health Check Workflow
```
deployment complete
    ↓
GET /api/health
    ↓
Expected response: 200 + { status: 'ok' }
    ↓
If success: Ready for traffic
If 5xx/timeout: Poll again (max 2 min, every 5 sec)
If still failing after 2 min: Rollback
```

### Important: Health Endpoint Should Be Fast

```typescript
// ✓ GOOD - Returns immediately
export async function GET() {
  return Response.json({ status: 'ok' });
}

// ✗ BAD - Connects to database, slow
export async function GET() {
  const count = await db.users.count();
  return Response.json({ status: 'ok', users: count });
}

// ✓ GOOD - Defers slow init to background
let dbReady = false;

// Start DB init in background
setTimeout(async () => {
  await initDatabase();
  dbReady = true;
}, 100);

export async function GET() {
  return Response.json({ status: 'ok', db_ready: dbReady });
}
```

---

## SSL/DNS Configuration

### Automatic (HostKit handles):
- Auto-register `{project-name}.hostkit.dev` domain
- Generate Let's Encrypt SSL certificate
- Auto-renew certificate every 90 days
- Nginx reverse proxy handles HTTPS

### Manual Setup Required:

#### For Custom Domain:
```python
# Add custom domain via nginx service
hostkit_execute(command="nginx add my-app example.com")

# CNAME record in your DNS provider:
# example.com  CNAME  my-app.hostkit.dev
```

#### For OAuth/Payments Integration:
- Use HTTPS domain: `https://my-app.hostkit.dev`
- Or custom domain with proper DNS setup
- Update OAuth redirect URIs to use HTTPS domain

---

## Next.js Configuration Requirements

### next.config.js - REQUIRED

```javascript
module.exports = {
  output: 'standalone',  // ⚠️ CRITICAL - do not omit
  reactStrictMode: true,
  swcMinify: true,  // Faster builds
};
```

### Port Binding - Automatic

Next.js automatically reads `process.env.PORT`:

```typescript
// NO NEED to configure - Next.js handles it
// Just ensure PORT env var is set (HostKit does this)
```

### Environment Variables in Next.js

```typescript
// Server-side only (not sent to client)
const databaseUrl = process.env.DATABASE_URL;

// Client-visible (with NEXT_PUBLIC_ prefix)
const apiUrl = process.env.NEXT_PUBLIC_API_URL;
```

Set via HostKit:
```python
hostkit_env_set(
  project="my-app",
  variables={
    "DATABASE_URL": "postgresql://...",
    "NEXT_PUBLIC_API_URL": "https://api.example.com"
  },
  restart=True
)
```

---

## Deployment Modes Comparison

| Factor | Pre-Built (A) | Source (B) |
|--------|---------------|-----------|
| **Build Location** | Local | VPS |
| **Build Time** | Before deploy | During deploy |
| **Deployment Time** | 2-5 min | 15-25 min |
| **Best For** | Production, CI/CD | Development |
| **Network** | More bandwidth | More time |
| **VPS Load** | Low | High |
| **Build Issues** | Caught locally | Caught on deploy |

---

## Rate Limiting

HostKit limits deploys to **10 per hour** by default.

```python
# Override rate limit (use during active development)
hostkit_deploy_local(
  project="my-app",
  local_path="/path",
  override_ratelimit=True
)

# In production, respect the limit to avoid VPS overload
```

---

## Timeouts

| Operation | Timeout | Configurable |
|-----------|---------|--------------|
| npm install | 10 minutes | No |
| npm build | 10 minutes | No |
| Health check | 2 minutes | Yes (hostkit_wait_healthy) |
| Deploy total | 25 minutes | No |

---

**Last updated**: February 2025 · HostKit v1.0.0
