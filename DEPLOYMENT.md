# HostKit Deployment Pipeline

## Quick Start

**Deploy from local machine:**
```bash
# Build locally first
npm run build

# Deploy using MCP tool (from Claude Code)
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/project",
  install=True,
  build=True,
  wait_healthy=True
)
```

**Deploy from VPS:**
```bash
hostkit deploy {project} \
  --source /path/to/source \
  --install \
  --build \
  --restart
```

---

## Deployment Modes

### Mode 1: Deploy Built Output (Recommended for Local Development)

Use when you've already built locally:

```python
# From Claude Code or local CLI
hostkit_deploy_local(
  project="my-app",
  local_path="/path/to/built/output",  # Should contain .next/, public/, node_modules/
  install=True,      # npm install in target
  build=False,       # Skip build (already done)
  wait_healthy=True  # Poll health check
)
```

**Timeline**: 2-5 minutes
- 1-2 min: rsync files
- 1-2 min: npm install
- 30 sec: symlink swap + service restart
- 30 sec: health check polling

### Mode 2: Deploy Source Code + Build on HostKit

Use when source code contains Next.js project:

```bash
hostkit deploy {project} \
  --source /path/to/source \
  --build \
  --install \
  --restart
```

**Timeline**: 15-25 minutes
- 10 min: npm install
- 10 min: npm run build
- 1-2 min: rsync built output
- 1-2 min: npm install dependencies
- 30 sec: symlink swap
- 30 sec: health check

### Mode 3: Deploy from Git

```bash
hostkit deploy {project} \
  --git-repo https://github.com/user/repo.git \
  --git-branch main \
  --build \
  --install
```

---

## Full Deployment Pipeline

```
START
  │
  ├─→ [1] Pre-flight Checks
  │   ├─ Rate limit (10 deploys/hour default)
  │   ├─ Project exists
  │   ├─ Source accessible
  │   └─ Auto-pause status
  │
  ├─→ [2] Optional: Build App
  │   ├─ npm install (10 min timeout)
  │   └─ npm run build (10 min timeout)
  │
  ├─→ [3] Pre-deploy Snapshot
  │   ├─ Database checkpoint (if has DB)
  │   └─ Environment snapshot
  │
  ├─→ [4] Release Creation
  │   └─ New timestamped directory: releases/{YYYYMMdd-HHMMSS}/
  │
  ├─→ [5] File Synchronization
  │   ├─ For Next.js standalone:
  │   │  ├─ Copy .next/standalone contents
  │   │  ├─ Copy .next/static
  │   │  └─ Copy public/
  │   │
  │   └─ For others: Standard rsync with excludes
  │      (excludes: node_modules, .git, .next, __pycache__, venv, etc.)
  │
  ├─→ [6] Dependency Installation (if --install)
  │   ├─ npm install --production in app directory
  │   ├─ 10 minute timeout
  │   └─ Symlink must be active
  │
  ├─→ [7] Activate Release (ATOMIC)
  │   ├─ Create/update symlink: app → releases/{new_release}/
  │   ├─ < 1 second switch
  │   └─ Zero downtime
  │
  ├─→ [8] Service Restart (if --restart)
  │   └─ systemctl restart hostkit-{project}
  │
  ├─→ [9] Secret Injection (if --inject-secrets)
  │   └─ Write secrets from vault to .env
  │
  ├─→ [10] Health Check
  │   ├─ Poll http://127.0.0.1:{port}/api/health
  │   ├─ 5 second intervals
  │   ├─ Up to 2 minute timeout
  │   └─ Expects HTTP 2xx response
  │
  ├─→ [11] Release Cleanup
  │   └─ Keep 5 most recent, delete older
  │
  └─→ [12] Deployment Recorded
      └─ Success/failure logged with metadata

SUCCESS → Service running, release active
FAILURE → Previous release remains active, can rollback
```

---

## Build Detection

HostKit auto-detects build type (priority order):

1. **Next.js Standalone**: `.next/standalone/server.js` found
   - Entry: `node server.js`
   - Start command updated to: `ExecStart=/usr/bin/node /home/{project}/app/server.js`

2. **Next.js Standard**: `.next/` exists but no standalone
   - Entry: `npm start`
   - Uses default: `ExecStart=/usr/bin/npm start`

3. **Node.js**: `package.json` found
   - Entry: `node app/index.js`

4. **Python**: `requirements.txt` or `pyproject.toml` found
   - Entry: `python -m app`

5. **Static**: `index.html` found
   - Entry: None (Nginx serves directly)

6. **Unknown**: No match
   - Deployment fails with suggestion

---

## Timeouts and Limits

| Operation | Timeout | Failure Behavior |
|-----------|---------|------------------|
| npm install | 10 minutes | Deployment fails, previous release stays active |
| npm build | 10 minutes | Deployment fails, previous release stays active |
| Rsync sync | 5 minutes | Deployment fails (unlikely unless > 5GB) |
| Health check | 2 minutes | Marked as failed, but release activated |
| Deployment total | ~30 min (soft limit) | Advisory, actual limit depends on operations |

---

## Deployment Flags

### Core Flags

| Flag | Effect | Default | Required |
|------|--------|---------|----------|
| `--source {path}` | Source directory to deploy | N/A | Yes (or --git-repo) |
| `--build` | Run npm build before deploy | false | No, but recommended |
| `--install` | Run npm install | false | **Recommended: always true** |
| `--restart` | Restart service after deploy | true | No |
| `--inject-secrets` | Inject secrets from vault | false | No |

### Advanced Flags

| Flag | Effect |
|------|--------|
| `--override-ratelimit` | Bypass deploy rate limiting (10/hour) |
| `--git-repo {url}` | Deploy from Git instead of local path |
| `--git-branch {branch}` | Git branch to checkout (default: main) |
| `--git-tag {tag}` | Git tag to checkout |
| `--git-commit {sha}` | Specific Git commit to checkout |

---

## Failure Scenarios and Recovery

### Scenario 1: npm install timeout

**Symptom**: Deploy stops mid-way, error: "npm install timeout"

**Why**: Dependencies too large, registry slow, or network issue

**Recovery**:
1. Check logs: `hostkit service logs {project} --tail 50`
2. Fix dependency issue (remove large packages, use alternatives)
3. Test locally: `npm install` should complete in < 10 min
4. Redeploy: `hostkit deploy {project} --install ...`

**Old release still active**: ✅ No downtime during failure

---

### Scenario 2: Build fails

**Symptom**: Error during `npm run build`

**Why**: Syntax error, missing env var, type error, etc.

**Recovery**:
1. Reproduce locally: `npm run build`
2. Fix the issue in code
3. Commit and redeploy
4. Or manually fix on VPS: `cd /home/{project}/app && npm run build`

**Old release still active**: ✅ No downtime

---

### Scenario 3: Service fails to start

**Symptom**: Health check fails for 2 minutes after deploy

**Why**: App crash, missing env var, port binding issue, etc.

**Recovery**:
1. Check logs: `hostkit service logs {project} --follow`
2. Look for error messages in app.log or error.log
3. Fix the issue (usually code or config related)
4. Redeploy or use rollback if critical
5. `hostkit rollback {project}` → previous release activated

**New release activated but unhealthy**: Can rollback instantly

---

### Scenario 4: Health check endpoint missing

**Symptom**: Deploy succeeds but health check fails

**Why**: App doesn't have `/api/health` endpoint returning 2xx

**Recovery**:
1. Add health endpoint: `app/api/health/route.ts`
   ```typescript
   export async function GET() {
     return Response.json({ status: 'ok' });
   }
   ```
2. Rebuild and redeploy

**Health check is not mandatory**: App works even if health check fails, but deploy marked as failed

---

### Scenario 5: Out of disk space

**Symptom**: Rsync fails: "No space left on device"

**Why**: Project files too large or logs accumulated

**Recovery**:
1. Check disk: `df -h /home`
2. Clear old releases: `hostkit deploy {project} --cleanup`
3. Clear logs: `hostkit service logs {project} --clear`
4. Retry deploy

---

## Post-Deploy Verification

### Check Deployment Status

```bash
# View active release
hostkit deploy status {project}

# View recent deployments
hostkit deploy history {project} --last 10

# Check health
hostkit health {project}
```

### Verify App is Running

```bash
# Check process
ps aux | grep "npm start"

# Check port is listening
netstat -tlnp | grep :{PORT}

# Test health endpoint
curl http://127.0.0.1:{PORT}/api/health

# View logs
hostkit service logs {project} --follow
```

---

## Rollback Procedure

### Quick Rollback

```bash
hostkit rollback {project}
```

**What happens**:
1. Symlink `app` → `releases/{previous}/`
2. Service restarted
3. Instant activation (no build needed)
4. Takes ~5 seconds

### What Does NOT Rollback

| Resource | Manual Action Needed |
|----------|---------------------|
| Database changes | Use `hostkit checkpoint restore {project} {checkpoint_id}` |
| Environment variables | Manually edit `.env` or use `hostkit env set` |
| Secrets | Redeploy with correct secrets |
| File uploads | Not affected by code rollback |

### Partial Rollback Example

```bash
# Rollback code
hostkit rollback {project}

# But restore database from backup
hostkit checkpoint restore {project} {checkpoint_id}

# And revert env var
hostkit env set {project} DEBUG=false --restart
```

---

## Deployment Optimization Tips

### Speed Up Deploys

1. **Skip --build if not needed**
   - Only include if code changed
   - Build locally and deploy built output if possible

2. **Use .next/standalone**
   - Smaller output size
   - Faster rsync

3. **Exclude large files**
   - Use `.gitignore` to exclude build artifacts before rsync
   - No `node_modules/` in source (they're installed)
   - No `.git/` directory

4. **Enable rate limit override during active dev**
   - `--override-ratelimit` to skip 10/hour limit
   - Revoke when done: `hostkit ratelimit enable {project}`

### Reduce Dependency Installation Time

1. **Use npm ci instead of npm install** (faster, reproducible)
   - HostKit uses `npm install --production` by default
   - Add `package-lock.json` to repo

2. **Remove dev dependencies from production**
   - `--production` flag automatically applied
   - Use `npm install --save-dev` for dev-only packages

3. **Use npm workspaces** if monorepo
   - Faster than installing separately

---

## Links to Related Documentation

- **[Next.js Specifics →](NEXTJS.md)** - Build output, standalone config
- **[Environment Configuration →](ENVIRONMENT.md)** - Env vars & secrets
- **[Troubleshooting →](TROUBLESHOOTING.md)** - Common deploy issues
