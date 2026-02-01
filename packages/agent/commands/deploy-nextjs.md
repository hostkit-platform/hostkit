# Next.js Deployment to HostKit

You are deploying a Next.js project to HostKit. Follow these steps exactly — skipping any step is the #1 cause of crash loops.

## Pre-Deploy Checklist

Before deploying, verify:

1. **Runtime is correct** — the HostKit project must have been created with `--nextjs`. Runtime is immutable. If wrong, the project must be deleted and recreated.
2. **`next.config.js` has standalone output** — required for HostKit:
   ```js
   output: "standalone"
   ```
   If missing, add it and rebuild.
3. **Project name is lowercase with hyphens only** — no underscores, no uppercase.

## Step 1: Build Locally

```bash
cd {project_local_path}
npm run build
```

Verify the build succeeded:
- `.next/` directory exists and was freshly created
- No build errors in output
- If using `output: "standalone"`, verify `.next/standalone/` exists

**Do NOT skip the local build.** HostKit's remote build may timeout or fail silently on large projects.

## Step 2: Deploy with hostkit_deploy_local

Use the MCP tool with ALL required flags:

```
hostkit_deploy_local:
  project: "{project_name}"
  local_path: "{project_local_path}"
  install: true
  build: true
  wait_healthy: true
```

**CRITICAL FLAGS:**
- `install: true` — runs `npm install` on VPS. Skipping this causes "module not found" crash loops.
- `build: true` — runs `npm run build` on VPS. Skipping this serves stale builds.
- `wait_healthy: true` — blocks until the service responds to health checks.

**All three flags are MANDATORY. Never omit them.**

## Step 2 (Alternative): Large Projects That Timeout

If rsync times out during `hostkit_deploy_local`, use the manual approach:

1. **Rsync with proper excludes:**
   ```bash
   rsync -avz --delete \
     --exclude=node_modules \
     --exclude=.git \
     --exclude=.next \
     {project_local_path}/ ai-operator@{VPS_IP}:/tmp/{project_name}-deploy/
   ```

   **IMPORTANT:** Use root-anchored excludes (`--exclude=/images`) to avoid matching subdirectories. Unanchored `--exclude=images` will match ALL `images/` directories at any depth.

2. **Deploy from remote source:**
   ```
   hostkit_execute:
     command: "deploy {project_name} --source /tmp/{project_name}-deploy --install --build"
   ```

3. **Wait for healthy:**
   ```
   hostkit_wait_healthy:
     project: "{project_name}"
   ```

## Step 3: Post-Deploy Verification (MANDATORY)

Health checks alone are NOT sufficient — they pass even with broken assets, missing CSS, or 500 errors on actual pages.

**You MUST verify the actual site:**

1. **Check the home page loads:**
   ```
   Use Playwright MCP to navigate to https://{project_name}.hostkit.dev
   Take a snapshot and verify content renders correctly
   ```

2. **Check for console errors:**
   ```
   Use browser_console_messages to check for JavaScript errors
   ```

3. **If behind Cloudflare** — assets may be cached from a broken deploy. Append `?v={timestamp}` to bust cache, or purge via Cloudflare dashboard.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Crash loop (restarts every few seconds) | Missing `install: true` | Redeploy with `install: true` |
| "Module not found" in logs | Missing `install: true` | Redeploy with `install: true` |
| Stale content after deploy | Missing `build: true` | Redeploy with `build: true` |
| Site loads but missing CSS/images | Bad rsync excludes or Cloudflare cache | Check excludes, bust cache |
| Health check passes but site broken | Health endpoint doesn't check full app | Use Playwright to verify |
| "next: command not found" | Wrong runtime (not `--nextjs`) | Delete project, recreate with `--nextjs` |

## Summary

The deployment formula is:

```
1. npm run build          (local)
2. hostkit_deploy_local   (install=true, build=true, wait_healthy=true)
3. Playwright verify      (navigate, snapshot, check console)
```

Never deviate from this. Never skip flags. Never trust health checks alone.
