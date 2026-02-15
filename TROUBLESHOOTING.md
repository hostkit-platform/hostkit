# Troubleshooting Guide

## Deployment Issues

### Deploy Succeeds But App Won't Start

**Symptoms**: Deploy marked as success, but health check fails or service keeps restarting

**Possible Causes**:
1. Missing `/api/health` endpoint
2. App crashes on startup (uncaught error)
3. Port binding issue
4. Missing dependencies or node_modules

**Debug Steps**:

```bash
# 1. Check logs for errors
hostkit service logs {project} --follow

# 2. Look for error messages in stderr
hostkit service logs {project} --stderr-only --tail 50

# 3. Check if port is listening
lsof -i :{PORT}

# 4. Check env vars are set
hostkit env get {project}

# 5. Manually test service
ssh {project}@vps
cd app
npm start
# Watch for startup errors
```

**Common Error Messages**:

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot find module 'X'` | Missing dependency | Add to package.json, redeploy with --install |
| `EADDRINUSE: address already in use :{port}` | Port conflict | Check lsof output, kill process, redeploy |
| `ENOENT: no such file or directory` | Missing file | Check .next structure, rebuild locally |
| `Error: Cannot find database` | DB not created | Use --with-db flag to create database |
| `TypeError: Cannot read property of undefined` | Logic error | Check app code, test locally, fix and redeploy |

---

### npm install Timeout (10+ minutes)

**Symptom**: Deploy fails with "npm install timeout" error

**Possible Causes**:
1. Dependencies too large
2. npm registry slow or unreachable
3. Network issue
4. Disk space full

**Debug Steps**:

```bash
# 1. Test locally (should complete in < 10 min)
npm install
# If takes > 10 min locally, issue is with dependencies

# 2. Check disk space
df -h /home/{project}
# If < 1GB free, cleanup old releases

# 3. Check npm cache
npm cache clean --force

# 4. Try specific registry
npm install --registry https://registry.npmjs.org/
```

**Solutions**:

```bash
# Remove unnecessary dependencies
npm list  # Find large packages
npm uninstall large-package

# Use faster registry (Aliyun mirror in China)
npm config set registry https://registry.npmmirror.com/

# Increase timeout
npm config set fetch-timeout 120000

# Use npm ci instead of install (for CI/CD)
npm ci --production
```

---

### npm build Timeout (10+ minutes)

**Symptom**: Deploy fails with "npm build timeout" error

**Possible Causes**:
1. Large codebase or many files
2. Slow server CPU
3. Memory pressure
4. Complex build process (type checking, etc.)

**Debug Steps**:

```bash
# 1. Test locally
time npm run build
# Note the duration

# 2. Check if faster on VPS
# (VPS may have slower CPU)

# 3. Check memory usage
free -h

# 4. Check system load
uptime
```

**Solutions**:

```bash
# Skip type checking in build if not critical
# In tsconfig.json, or in next.config.js:
module.exports = {
  typescript: {
    ignoreBuildErrors: true,  // Only if testing
  },
};

# Optimize build:
# - Remove unused dependencies
# - Use SWC (already default in Next.js 12+)
# - Skip source maps if not needed

# next.config.js
module.exports = {
  productionBrowserSourceMaps: false,  // Smaller output
  swcMinify: true,  // Faster
};
```

---

### "Module not found" After Deploy

**Symptom**: Deploy succeeds, app starts, then crashes with "Cannot find module"

**Possible Causes**:
1. Skipped `--install` flag
2. node_modules excluded from standalone
3. Package not in package-lock.json
4. Circular imports breaking build

**Debug Steps**:

```bash
# 1. Check if install ran
ls -la /home/{project}/app/node_modules

# 2. Check package.json
cat /home/{project}/app/package.json | grep "missing-module"

# 3. Check logs for exact error
hostkit service logs {project} --tail 20

# 4. Manually install
ssh {project}@vps
cd /home/{project}/app
npm install --production
```

**Solutions**:

```bash
# Redeploy with install flag
hostkit deploy {project} --source /path --install --restart

# Or manually fix
ssh {project}@vps
cd /home/{project}/app
npm install
systemctl restart hostkit-{project}

# Check local build works
npm run build
npm install
npm start
# Should work without errors
```

---

## Common Runtime Errors

### Health Check Fails

**Symptom**: Deploy succeeds but health check times out, service keeps restarting

**Cause**: Missing or broken `/api/health` endpoint

**Solution**:

```typescript
// app/api/health/route.ts
export async function GET() {
  return Response.json(
    { status: 'ok', timestamp: new Date().toISOString() },
    { status: 200 }
  );
}
```

Redeploy and health check should pass.

---

### Port Already in Use

**Symptom**: Service won't start: "EADDRINUSE: address already in use :{PORT}"

**Cause**: Another process using the same port

**Debug**:

```bash
# Find what's using the port
lsof -i :{PORT}
netstat -tlnp | grep :{PORT}

# Kill the process
kill -9 <PID>

# Or restart everything
systemctl restart hostkit-{project}
```

---

### Database Connection Refused

**Symptom**: "ECONNREFUSED" when connecting to DATABASE_URL

**Possible Causes**:
1. Database not created (`--with-db` not used)
2. Wrong DATABASE_URL
3. PostgreSQL not running
4. User permissions issue

**Debug**:

```bash
# Check if database exists
hostkit db list

# Check DATABASE_URL
hostkit env get {project} DATABASE_URL

# Try direct connection
psql $DATABASE_URL

# Check PostgreSQL status
systemctl status postgresql
```

**Solutions**:

```bash
# Create database if missing
hostkit db create {project}

# Or recreate project with --with-db
hostkit project delete {project} --force
hostkit project create {project} --nextjs --with-db

# Test connection
psql -U {project} -d {project}_db -h localhost
```

---

### Environment Variables Undefined

**Symptom**: `process.env.MY_VAR` is undefined in code

**Debug**:

```bash
# Check if var exists
hostkit env get {project} MY_VAR

# Check in running process
ps aux | grep "npm start"

# Check systemd loaded it
systemctl cat hostkit-{project} | grep EnvironmentFile

# View actual .env file
cat /home/{project}/.env
```

**Solutions**:

```bash
# Set the variable
hostkit env set {project} MY_VAR=value --restart

# Or manually edit and restart
ssh {project}@vps
nano /home/{project}/.env
systemctl restart hostkit-{project}

# For Next.js client-side, must use NEXT_PUBLIC_
NEXT_PUBLIC_VAR=value
```

---

### App Runs But Returns 500 Errors

**Symptom**: App starts successfully but requests return 500 error

**Debug**:

```bash
# Check API route logs
hostkit service logs {project} --stderr-only

# Test API manually
curl http://127.0.0.1:{PORT}/api/health -v

# Check database connectivity
# In app logs, look for "Cannot connect to database"

# Check for runtime errors
# Look in error.log for stack traces
```

**Solutions**:

```bash
# Add error handling to routes
// app/api/route.ts
try {
  // Logic here
  return Response.json({...});
} catch (error) {
  console.error('API error:', error);
  return Response.json({error: 'Internal error'}, {status: 500});
}

# Add global error handler
// app/error.tsx
export default function Error({error, reset}) {
  console.error('Global error:', error);
  return <div>Error: {error.message}</div>;
}
```

---

## Next.js Specific Issues

### Build Output Missing `standalone/`

**Symptom**: Build succeeds but `.next/standalone/` doesn't exist

**Cause**: Missing `output: 'standalone'` in next.config.js

**Fix**:

```javascript
// next.config.js
const nextConfig = {
  output: 'standalone',  // Add this line
  reactStrictMode: true,
};

module.exports = nextConfig;
```

Then rebuild: `npm run build`

---

### Stale Content After Deploy

**Symptom**: Deploy succeeds but old content showing

**Possible Causes**:
1. Build not run (`--build=false`)
2. Browser cache
3. Cloudflare cache

**Solutions**:

```bash
# Redeploy with build
hostkit deploy {project} --source . --build --install --restart

# Clear browser cache
# Ctrl+Shift+Delete → Clear all

# If using Cloudflare, purge cache
# Or use cache buster in URL
<script src="/js/app.js?v=20250215"></script>
```

---

### Missing Metadata Export

**Symptom**: Warnings about metadata not exported from layout

**Fix**:

```typescript
// app/layout.tsx
export const metadata = {
  title: 'My App',
  description: 'App description',
};

export default function RootLayout({children}) {
  return (
    <html>
      <body>{children}</body>
    </html>
  );
}
```

---

## Network and Performance Issues

### Slow Deployment (> 20 minutes)

**Possible Causes**:
1. Large npm install (dependencies)
2. Large build output
3. Slow rsync (network issue)
4. VPS CPU bottleneck

**Debug**:

```bash
# Check file sizes
du -sh /home/{project}/app
ls -lh /home/{project}/.next/standalone/

# Monitor VPS during deploy
top
# Look for high CPU/memory

# Check network
iperf or speedtest
```

**Solutions**:

```bash
# Reduce dependency size
npm list  # Find large packages
npm install --no-optional

# Use --build=false if already built
hostkit deploy {project} --source ./build --install

# Split large packages
# Don't deploy full node_modules, use --install

# Optimize next.config.js
module.exports = {
  output: 'standalone',
  swcMinify: true,
  productionBrowserSourceMaps: false,
};
```

---

### High Memory Usage

**Symptom**: App crashes with OOM error, or systemd kills it

**Debug**:

```bash
# Check memory limit (if any)
systemctl cat hostkit-{project} | grep Memory

# Monitor memory
watch -n 1 'ps aux | grep npm start'

# Check for memory leaks in app
# Use heap snapshots if Node debugging enabled
```

**Solutions**:

```bash
# Optimize app to use less memory
# - Fix memory leaks
# - Cache aggressively
# - Use streaming responses

# Increase Node memory limit
# Edit systemd service:
systemctl edit hostkit-{project}
# Add: Environment="NODE_OPTIONS=--max-old-space-size=2048"

# Restart
systemctl restart hostkit-{project}
```

---

### Health Check Times Out

**Symptom**: Health check polling never completes

**Cause**: App taking > 2 minutes to start

**Debug**:

```bash
# Time how long startup takes
time npm start
# Note the duration before "ready - started server on"

# Check logs during startup
hostkit service logs {project} --follow
# Watch for slow initialization
```

**Solutions**:

```bash
# Reduce startup time
// Defer expensive operations
async function startupCheck() {
  // Do this after server starts, not in synchronous startup
  const db = await prisma.$connect();
}

// Or use lazy initialization
export const db = new PrismaClient({
  // Only connect when first query made
});

// For long-running startup, increase health check timeout
# Requires host config change (uncommon)
```

---

## Rollback and Recovery

### Quick Recovery from Failed Deploy

```bash
# 1. Rollback to previous release
hostkit rollback {project}

# 2. Service automatically restarts
# 3. Old release is active again
# Takes ~5 seconds

# 4. Verify health
hostkit health {project}
curl http://127.0.0.1:{PORT}/api/health
```

---

### Restore Database from Checkpoint

If deploy succeeded but database schema incompatible:

```bash
# 1. List checkpoints
hostkit checkpoint list {project} --limit 10

# 2. Restore to previous checkpoint
hostkit checkpoint restore {project} {checkpoint_id}

# 3. Verify data
hostkit db query {project} "SELECT * FROM {table} LIMIT 1"
```

---

### Recover from Secrets Error

If secrets injected incorrectly:

```bash
# 1. View secrets
hostkit secrets list {project}

# 2. Rotate/fix secret
hostkit secrets set {project} API_KEY new_value

# 3. Redeploy with correct secrets
hostkit deploy {project} --inject-secrets --restart

# 4. Verify
hostkit env get {project} API_KEY
```

---

## Getting Help

### Collect Diagnostics

Before asking for help, gather:

```bash
# 1. Service status
systemctl status hostkit-{project}

# 2. Logs
hostkit service logs {project} --tail 100

# 3. Environment
hostkit env get {project}

# 4. Recent deploys
hostkit deploy history {project} --last 5

# 5. Health check
hostkit health {project}

# 6. Project details
hostkit project info {project}

# 7. Available space
df -h /home/{project}
du -sh /home/{project}/*
```

### Common Commands for Debugging

```bash
# View logs in real-time
hostkit service logs {project} --follow

# View specific error lines
hostkit service logs {project} --stderr-only --tail 50

# Check service status
systemctl status hostkit-{project}

# Restart service
systemctl restart hostkit-{project}

# Manual start with debug
ssh {project}@vps
cd /home/{project}/app
npm start
# Ctrl+C to stop

# Check what's using resources
top
ps aux
lsof -i

# Verify configuration
cat /etc/systemd/system/hostkit-{project}.service
cat /home/{project}/.env
cat /etc/nginx/sites-available/hostkit-{project}
```

---

## Links to Related Documentation

- **[Deployment Pipeline →](DEPLOYMENT.md)** - Understand deploy process
- **[Next.js Guide →](NEXTJS.md)** - Next.js specific issues
- **[Architecture →](ARCHITECTURE.md)** - System structure
- **[Environment →](ENVIRONMENT.md)** - Environment variables
