# HostKit Deployment Failures - Troubleshooting Guide

Complete reference for diagnosing and fixing common deployment failures.

**Quick Navigation:**
- [Category A: Timeout Issues](#category-a-timeout-issues)
- [Category B: Build Failures](#category-b-build-failures)
- [Category C: Authentication Service Failures](#category-c-authentication-service-failures)
- [Category D: Database Connection Failures](#category-d-database-connection-failures)
- [Category E: Health Check Failures](#category-e-health-check-failures)
- [Category F: Rsync and Deployment Strategy Issues](#category-f-rsync-and-deployment-strategy-issues)
- [Category G: Nginx/Reverse Proxy Issues](#category-g-nginxreverse-proxy-issues)
- [Quick Diagnostic Checklist](#quick-diagnostic-checklist)

---

## Category A: Timeout Issues

### A1: npm install timeout (10 minutes)

**Symptoms:**
- Deployment hangs during "Installing dependencies"
- Error: `npm install exceeded 10 minute timeout`
- Logs show: `npm ERR! code ETIME`

**Causes:**
- Large dependencies (e.g., @types packages, native modules)
- Slow network on VPS
- Lock file conflicts

**Solutions:**

```python
# Solution 1: Use npm ci instead of npm install
# In your package.json scripts:
{
  "scripts": {
    "deploy": "npm ci --production && npm run build"
  }
}

# Solution 2: Remove unnecessary @types packages locally
# Before deploying:
# $ npm uninstall @types/node @types/react @types/react-dom
# Use: skipLibCheck: true in tsconfig.json instead

# Solution 3: Clear npm cache on VPS
hostkit_execute(command="npm cache clean --force")

# Solution 4: Deploy with override_ratelimit and watch logs
result = hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",
  install=True,
  build=False,
  wait_healthy=False,
  override_ratelimit=True
)

# Then manually wait and check logs
import time
while True:
    time.sleep(5)
    logs = hostkit_execute(command="service logs my-app --tail 5")
    if "listening on port" in str(logs):
        break
```

---

### A2: npm build timeout (10 minutes)

**Symptoms:**
- Deployment hangs during "Building Next.js app"
- Error: `npm run build exceeded 10 minute timeout`
- Large build output suggests build optimization issue

**Causes:**
- Unoptimized webpack config
- Too many pages (1000+)
- Memory pressure on VPS

**Solutions:**

```python
# Solution 1: Optimize Next.js build locally first
# In next.config.js:
module.exports = {
  output: 'standalone',
  reactStrictMode: true,
  swcMinify: true,  // Use SWC instead of Terser (faster)
  experimental: {
    optimizePackageImports: ["lodash"], // Only import used parts
  },
  // Reduce build output
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.optimization.splitChunks.cacheGroups = {
        styles: {
          name: 'styles',
          test: /\.css$/,
          chunks: 'all',
          enforce: true,
        },
      };
    }
    return config;
  },
};

# Solution 2: Pre-build locally, deploy pre-built
# $ npm run build (locally, takes 10 min)
# Then deploy the .next/ directory:
result = hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/output",
  install=True,
  build=False,  # Already built
  wait_healthy=True
)

# Solution 3: Reduce pages temporarily
# Remove or hide large page bundles, deploy, then re-enable

# Solution 4: Check VPS resource limits
health = hostkit_state(scope="health")
if health["resources"]["memory_percent"] > 80:
    print("VPS memory high, build will timeout")
    # Contact HostKit admin to increase VPS resources
```

---

### A3: Health check timeout (2 minutes)

**Symptoms:**
- Deployment completes but health check never passes
- Error: `Health check timeout after 120000ms`
- Logs show app starting but `/api/health` not responding

**Causes:**
- Missing health endpoint
- App startup takes > 2 minutes (slow database init)
- Port binding issue

**Solutions:**

```python
# Solution 1: Ensure health endpoint exists and returns 200
# app/api/health/route.ts:
export async function GET() {
  // Should NOT connect to database
  // Should NOT be slow
  return Response.json({ status: 'ok' });
}

# Solution 2: Manually check what's happening
logs = hostkit_execute(command="service logs my-app --follow")
# Look for "listening on port" or "error during startup"

# Solution 3: Increase health check timeout
# Use: hostkit_wait_healthy(timeout=300000)  # 5 minutes
result = hostkit_deploy_local(
  project="my-app",
  local_path="/path",
  wait_healthy=False  # Don't auto-wait
)
# Then manually wait longer:
hostkit_wait_healthy(project="my-app", timeout=300000, interval=5000)

# Solution 4: Check app startup time
# Add logging to your app startup:
// pages/_app.tsx or main entry point
console.log('App starting...');
// Database connections, warm caches, etc.
console.log('App ready');

# Solution 5: Defer slow initialization
// Move database warmup to POST endpoint, not startup
// Or use lazy initialization:
let db = null;
export async function getDB() {
  if (!db) {
    db = await initDatabase();
  }
  return db;
}
```

---

## Category B: Build Failures

### B1: Next.js Missing `output: 'standalone'`

**Symptoms:**
- Error: `Cannot find .next/standalone/server.js`
- App tries to use `.next/` directory with `npm start`
- Service restarts repeatedly

**Causes:**
- `next.config.js` missing `output: 'standalone'`
- Config not reloaded (old build from cache)

**Solution:**

```javascript
// next.config.js - REQUIRED
module.exports = {
  output: 'standalone',  // ⬅️ CRITICAL
  reactStrictMode: true,
};
```

**After fixing:**
```python
# Rebuild locally and redeploy
# $ npm run build
# $ npm run deploy

result = hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/newly/built/output",
  install=True,
  build=False,
  wait_healthy=True
)
```

---

### B2: Missing Dependencies in package.json

**Symptoms:**
- Error: `Cannot find module 'dependency-name'`
- App crashes on startup
- Error occurs in deployed app but not local

**Causes:**
- Dependency installed locally but not in `package.json`
- Or `package-lock.json` not committed
- Or using npm ci instead of npm install

**Solution:**

```bash
# On local machine:
npm install --save dependency-name
npm install --save-dev dev-dependency  # for dev dependencies

# Verify package.json has the entry
grep "dependency-name" package.json

# Redeploy:
hostkit_deploy_local(
  project="my-app",
  local_path="/path",
  install=True,
  build=True,
  wait_healthy=True
)
```

---

### B3: PORT Environment Variable Not Bound

**Symptoms:**
- App starts but doesn't listen on assigned port
- Error: `EADDRINUSE: address already in use :::3000`
- Service logs show app on port 3000, Nginx expects 8001+

**Causes:**
- Next.js hardcoded to port 3000
- Server doesn't read `process.env.PORT`

**Solution:**

```typescript
// next.config.js
module.exports = {
  output: 'standalone',
  serverRuntimeConfig: {
    // Only available server-side
    apiUrl: process.env.API_URL,
  },
};

// server.js or API route:
const port = parseInt(process.env.PORT || '3000', 10);
const hostname = process.env.HOST || 'localhost';

// For .next/standalone/server.js, Next.js automatically reads PORT
// No changes needed - just ensure PORT is set in .env

# Verify in HostKit:
hostkit_env_get(project="my-app", keys=["PORT"])
# Should return something like: {PORT: "8001"}

# If not set, set it:
hostkit_env_set(
  project="my-app",
  variables={"PORT": "8001"},
  restart=True
)
```

---

## Category C: Authentication Service Failures

### C1: Auth Service Not Enabled

**Symptoms:**
- Redirect to `/auth/oauth/google/login` returns 404
- Auth users list shows empty
- Login button doesn't work

**Solution:**

```python
# Enable auth service
hostkit_execute(command="auth enable my-app")

# Set required environment variables
hostkit_env_set(
  project="my-app",
  variables={
    "AUTH_URL": "https://my-app.hostkit.dev",
    "AUTH_TRUST_HOST": "true",
    "AUTH_JWT_PUBLIC_KEY": "your-key"
  },
  restart=True
)

# Verify auth is running
hostkit_execute(command="auth status my-app")
```

---

### C2: OAuth Provider Configuration Missing

**Symptoms:**
- Error: `GOOGLE_CLIENT_ID not found`
- OAuth sign-in fails with provider error
- Auth logs show: `Missing OAuth configuration`

**Causes:**
- OAuth credentials not set up in auth service
- Client ID/Secret in wrong place (should be in auth service, not app)

**Solution:**

```python
# DO NOT put OAuth credentials in your app .env
# Instead, configure them in auth service:

hostkit_execute(
  command="auth enable my-app --google-client-id=xxx --google-client-secret=yyy",
  json_mode=True
)

# Verify configuration:
hostkit_execute(command="auth config my-app --show")

# View auth service logs:
hostkit_execute(command="auth logs my-app --follow")
```

---

### C3: Session/Cookie Issues

**Symptoms:**
- Login works but user immediately logged out
- JWT token invalid errors
- Multi-tab logout (logout in one tab, other tabs still have access)

**Solution:**

```python
# Ensure AUTH_TRUST_HOST is set
hostkit_env_set(
  project="my-app",
  variables={
    "AUTH_TRUST_HOST": "true",
    "AUTH_URL": "https://my-app.hostkit.dev"
  },
  restart=True
)

# Check auth service health
hostkit_execute(command="auth status my-app")

# View session data in database
hostkit_db_query(
  project="my-app",
  query="SELECT * FROM auth_sessions LIMIT 5"
)

# Check JWT public key matches
hostkit_env_get(project="my-app", keys=["AUTH_JWT_PUBLIC_KEY"])
```

---

## Category D: Database Connection Failures

### D1: DATABASE_URL Not Set

**Symptoms:**
- Error: `DATABASE_URL is undefined`
- App can't connect to database
- Prisma/ORM startup fails

**Solution:**

```python
# Get auto-generated DATABASE_URL
hostkit_env_get(project="my-app", keys=["DATABASE_URL"])

# If empty, database wasn't created at project setup
# Create it:
hostkit_execute(command="db create my-app")

# Verify it's set:
url = hostkit_env_get(project="my-app", keys=["DATABASE_URL"])
print(f"Database URL: {url['variables']['DATABASE_URL']}")

# Test connection:
hostkit_db_query(
  project="my-app",
  query="SELECT version()"
)
```

---

### D2: Database Migrations Failed

**Symptoms:**
- App crashes with schema error
- Error: `table "users" does not exist`
- `prisma migrate deploy` fails

**Solution:**

```python
# Option 1: Run migrations manually
hostkit_db_query(
  project="my-app",
  query="CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT UNIQUE);",
  allow_write=True
)

# Option 2: Use Prisma migrations (if using Prisma)
# In your code, before deploy:
# $ npx prisma migrate deploy

# Then deploy with --build:
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",
  install=True,
  build=True,  # npm run build will run migrations
  wait_healthy=True
)

# Option 3: Check migration status
hostkit_db_query(
  project="my-app",
  query="SELECT * FROM _prisma_migrations"
)
```

---

### D3: Connection Pool Exhausted

**Symptoms:**
- Error: `remaining connection slots reserved for non-replication superuser`
- Lots of IDLE connections
- App becomes unresponsive under load

**Solution:**

```python
# Check active connections
hostkit_db_query(
  project="my-app",
  query="SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname"
)

# Kill idle connections
hostkit_db_query(
  project="my-app",
  query="""
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE state = 'idle' AND backend_start < now() - interval '10 minutes'
  """,
  allow_write=True
)

# Configure connection pooling in your app:
// Use PgBouncer or similar
PGSQL_CONN_POOL=20  # Limit connections

# In Prisma (prisma/schema.prisma):
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")

  // Add to enable connection pooling
  directUrl = env("POSTGRES_URL_NON_POOLING")
}
```

---

## Category E: Health Check Failures

### E1: App Crashes Immediately After Start

**Symptoms:**
- Deployment succeeds but health check fails
- Service status: `failed` or `restarting`
- Logs show startup errors then crash

**Causes:**
- Uncaught exception on startup
- Missing environment variable
- Database connection fails

**Solution:**

```python
# View crash logs
logs = hostkit_execute(command="service logs my-app --tail 50")
# Look for error stack traces

# Check environment variables
env = hostkit_env_get(project="my-app")
# Ensure all required vars are set

# Try to start manually and see output
hostkit_execute(command="service start my-app")

# Debug startup in development:
# $ NODE_ENV=production npm start
# Fix errors locally, redeploy
```

---

### E2: High Memory Usage During Startup

**Symptoms:**
- Health check timeout
- App uses > 1GB RAM
- VPS becomes unresponsive

**Solution:**

```python
# Check VPS health during deployment
health = hostkit_state(scope="health")
print(f"Memory: {health['resources']['memory_percent']}%")
print(f"Disk: {health['resources']['disk_percent']}%")

# Optimize app memory usage:
// next.config.js
module.exports = {
  output: 'standalone',
  swcMinify: true,

  // Reduce build output size
  webpack: (config) => {
    config.optimization.minimize = true;
    return config;
  },
};

# Deploy pre-built (not source) to save memory:
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/output",
  install=True,
  build=False,  # Don't build on VPS
  wait_healthy=True
)
```

---

### E3: Endpoint Responds But with 500 Errors

**Symptoms:**
- Health check passes (endpoint accessible)
- But endpoint returns 500
- App starts but has runtime errors

**Solution:**

```python
# The health endpoint itself must be simple and reliable
# app/api/health/route.ts:
export async function GET() {
  // DO NOT:
  // - Connect to database
  // - Call external APIs
  // - Do complex logic

  // DO:
  // - Return immediately
  try {
    return Response.json({ status: 'ok' });
  } catch (e) {
    return Response.json(
      { error: 'Internal error' },
      { status: 500 }
    );
  }
}

# Test health endpoint locally:
# $ curl http://localhost:3000/api/health
# Should return: {"status":"ok"}

# If database init is slow, defer it:
let dbReady = false;
setTimeout(async () => {
  await initDatabase();
  dbReady = true;
}, 100);  // Start DB init in background

export async function GET(req) {
  return Response.json({ status: 'ok', db_ready: dbReady });
}
```

---

## Category F: Rsync and Deployment Strategy Issues

### F1: Large Files Causing Slow Rsync

**Symptoms:**
- Rsync takes > 10 minutes for small changes
- Network seems slow
- Only 1-2 MB/s transfer speed

**Causes:**
- Uploading node_modules (don't include!)
- Uploading .next/ (don't include for source deploy!)
- Large media files in public/

**Solution:**

```python
# Create .deployignore or .gitignore:
node_modules/
.next/
.env
dist/
build/
coverage/
*.log
.DS_Store

# Strategy A: Deploy only what's needed
# For pre-built deploy, send:
# ✓ .next/standalone/
# ✓ .next/static/
# ✓ public/
# ✓ package.json (for npm install)
# ✗ node_modules/ (HostKit will npm install)
# ✗ .env (stays on VPS)

# For source deploy, send:
# ✓ src/ or app/
# ✓ pages/
# ✓ package.json
# ✓ next.config.js
# ✗ .next/ (will be rebuilt)
# ✗ node_modules/ (will be reinstalled)
# ✗ .env (stays on VPS)

# Use rsync filter or exclude:
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/source",
  # Automatically excludes: node_modules, .next, .env, .git
  install=True,
  build=True
)
```

---

### F2: Inconsistent Deployments

**Symptoms:**
- Sometimes works, sometimes fails
- Different files deployed different times
- Lock file conflicts

**Solution:**

```python
# Use consistent strategy:

# Strategy 1: Always pre-build locally
# $ npm run build
# $ hostkit_deploy_local(build=False, install=True)

# Strategy 2: Always build on VPS
# $ hostkit_deploy_local(build=True, install=True)

# DO NOT mix strategies

# Ensure reproducible builds:
# package.json:
{
  "engines": {
    "node": "18.x"
  },
  "scripts": {
    "build": "npm ci && next build",  // Use ci, not install
    "deploy": "npm ci --production && npm start"
  }
}

# Use npm ci instead of npm install:
hostkit_deploy_local(
  project="my-app",
  local_path="/path",
  install=True,  # Uses npm ci
  build=True
)
```

---

## Category G: Nginx/Reverse Proxy Issues

### G1: 502 Bad Gateway

**Symptoms:**
- Browser shows: `502 Bad Gateway`
- Nginx logs show: `upstream server temporarily disabled`
- App is running but not responding to Nginx

**Causes:**
- App not listening on correct port
- App crashed
- Network connection between Nginx and app broken

**Solution:**

```python
# Check app is running
status = hostkit_state(scope="project", project="my-app")
print(f"Service status: {status['service_status']}")

# Check app is listening on correct port
logs = hostkit_execute(command="service logs my-app --tail 20")
# Should show: "listening on port 8001" or similar

# Verify PORT environment variable
port_env = hostkit_env_get(project="my-app", keys=["PORT"])
print(f"PORT={port_env['variables']['PORT']}")

# Restart app and Nginx
hostkit_execute(command="service restart my-app")

# Check Nginx configuration
hostkit_execute(command="nginx status my-app")
```

---

### G2: 504 Gateway Timeout

**Symptoms:**
- Long-running requests timeout
- Error: `upstream timed out after 60s`
- App works for quick requests, fails for slow ones

**Solution:**

```python
# Nginx default timeout is 60 seconds
# For long-running operations, use:

// pages/api/long-operation.ts
export const maxDuration = 300;  // 5 minutes (for Vercel)

// For standard Node:
const express = require('express');
const app = express();
app.use(express.json({ limit: '50mb' }));

app.post('/api/long-operation', async (req, res) => {
  // Long operation (max 5 min on HostKit)
  res.json({ result: 'done' });
});

// Or use queues for truly long operations:
// 1. Accept request, return 202 Accepted
// 2. Queue task in Redis
// 3. Worker processes in background
// 4. Client polls for result
```

---

## Quick Diagnostic Checklist

**When deployment fails, check in this order:**

```python
# 1. Check if project exists
projects = hostkit_state(scope="projects")
my_project = [p for p in projects if p['name'] == 'my-app'][0]

# 2. Check VPS health
health = hostkit_state(scope="health")
if health['memory_percent'] > 90:
    print("VPS overloaded")

# 3. Check service status
status = hostkit_execute(command="service status my-app")

# 4. Check recent logs
logs = hostkit_execute(command="service logs my-app --tail 100")

# 5. Check environment variables
env = hostkit_env_get(project="my-app")
required = ["PORT", "NODE_ENV", "DATABASE_URL"]
missing = [k for k in required if k not in env]
if missing:
    print(f"Missing: {missing}")

# 6. Test health endpoint
health = hostkit_wait_healthy(
  project="my-app",
  timeout=10000
)

# 7. Check database
if database_configured:
    db = hostkit_db_query(project="my-app", query="SELECT 1")

# 8. Check app logs for errors
logs = hostkit_execute(command="service logs my-app --follow")

# 9. Check Nginx configuration
hostkit_execute(command="nginx status my-app")

# 10. Search solutions database
solutions = hostkit_solutions(
  action="search",
  query="502 bad gateway"
)
```

---

## Most Common Causes

**In order of frequency:**

1. **Missing health endpoint** - `/api/health` not implemented
2. **Wrong deployment strategy** - Mixing pre-built and source deploys
3. **Missing `output: 'standalone'`** - Next.js config error
4. **PORT not set** - Environment variable missing
5. **Large dependencies** - npm install timeout
6. **Database connection** - DATABASE_URL undefined
7. **Auth misconfiguration** - OAuth creds in wrong place
8. **Slow startup** - Health check timeout
9. **502 Bad Gateway** - App port mismatch
10. **Memory exhaustion** - VPS resources exceeded

---

**Last updated**: February 2025 · HostKit v1.0.0
