# Environment Variables and Configuration

## Quick Start

**View environment variables**:
```bash
hostkit env get {project}                    # All vars
hostkit env get {project} PORT DATABASE_URL  # Specific vars
```

**Set variables**:
```bash
hostkit env set {project} DEBUG=true API_URL=https://api.example.com --restart
```

**Inject secrets from vault**:
```bash
hostkit deploy {project} --inject-secrets  # During deploy
```

---

## Initial .env File

When a project is created, `/home/{project}/.env` is initialized with:

```bash
# HostKit Project Environment
PROJECT_NAME={project_name}
PORT={assigned_port}
HOST=127.0.0.1

# Redis (automatic, always present)
REDIS_URL=redis://localhost:6379/{db_number}
CELERY_BROKER_URL=redis://localhost:6379/{db_number}
CELERY_RESULT_BACKEND=redis://localhost:6379/{db_number}

# PostgreSQL (auto-set by provision, or when --with-db used with project create)
DATABASE_URL=postgresql://{user}:{password}@localhost:5432/{project}_db

# Auth (auto-set by provision, or when --with-auth used)
AUTH_ENABLED=true
AUTH_URL=http://127.0.0.1:{port+1000}
NEXT_PUBLIC_AUTH_URL=https://{project}.hostkit.dev
AUTH_JWT_PUBLIC_KEY="..."

# Storage (auto-set by provision, or when --with-storage used)
S3_ENDPOINT=http://localhost:9000
S3_BUCKET=hostkit-{project}
S3_ACCESS_KEY={generated}
S3_SECRET_KEY={generated}

# Other services added as enabled
# STRIPE_SECRET_KEY=sk_...
# etc.
```

---

## How Environment Variables Are Loaded

### Systemd Integration

The systemd service file specifies:
```ini
EnvironmentFile=/home/{project}/.env
```

**This means**:
1. systemd reads `/home/{project}/.env` before starting the process
2. All `KEY=VALUE` lines become environment variables
3. Comments and empty lines are ignored
4. The process receives all vars in its environment

```bash
# Example .env file
PROJECT_NAME=my-app        # Available as process.env.PROJECT_NAME
PORT=8001                  # Available as process.env.PORT
DEBUG=false                # Available as process.env.DEBUG
API_KEY=secret123          # Available as process.env.API_KEY
# This is a comment       # Ignored
                           # Empty lines ignored
DATABASE_URL=postgresql... # Available as process.env.DATABASE_URL
```

### When Variables Are Loaded

- **At process start**: systemd loads from `.env`
- **Not live reloaded**: Changing `.env` requires service restart
- **No hot reload**: App must be restarted to see new vars

---

## File Location and Format

### Path
```
/home/{project}/.env
```

### Format Rules

**Valid syntax**:
```bash
KEY=VALUE                          # Simple value
KEY="value with spaces"            # Quoted value
KEY='value with spaces'            # Quoted value
EMPTY_VAL=                         # Empty value
LONG_VAL=very-long-string-here    # Long value
MULTILINE_UNSUPPORTED=val1;val2   # Not split, treated as single value
```

**Invalid syntax**:
```bash
KEY = VALUE                        # Spaces around = not allowed
KEY:VALUE                          # Colon separator not supported
$KEY=value                         # Variable expansion not supported
export KEY=VALUE                   # export keyword ignored
KEY="value                         # Unmatched quote fails
```

### Permissions

- Owned by: `{project}:{project}` (project user)
- Readable by: project user and systemd
- Not world-readable (secrets stay private)

---

## Service-Specific Variables

### Added by HostKit When Service Enabled

| Service | Command | Variables Added |
|---------|---------|-----------------|
| Database | `--with-db` | `DATABASE_URL` |
| Auth | `--with-auth` or `hostkit auth enable {project}` | `AUTH_URL=http://127.0.0.1:9001` |
| Payments | `hostkit payments enable {project}` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| SMS | `hostkit sms enable {project}` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` |
| MinIO Storage | `hostkit minio enable {project}` | `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_PUBLIC_URL` |
| Redis | Automatic | `REDIS_URL` (always present) |

### Adding Custom Variables

**Manual method**:
```bash
hostkit env set {project} \
  CUSTOM_VAR1=value1 \
  CUSTOM_VAR2=value2 \
  --restart
```

**Or edit directly** (less recommended):
```bash
# SSH into project user
ssh {project}@vps

# Edit .env
nano .env

# Restart service
systemctl restart hostkit-{project}
```

---

## Variable Types and Conventions

### Standard Naming

```bash
# Project metadata (set by HostKit)
PROJECT_NAME=my-app
PORT=8001
HOST=127.0.0.1

# Database (if enabled)
DATABASE_URL=postgresql://...

# Cache/Queue
REDIS_URL=redis://...

# API keys and secrets (typically from vault)
API_KEY=sk_...
SECRET_KEY=...
WEBHOOK_SECRET=...

# Service URLs (if services enabled)
AUTH_URL=http://127.0.0.1:9001
PAYMENTS_URL=http://127.0.0.1:10001

# Public URLs (for client-side redirects)
NEXT_PUBLIC_API_URL=https://myapp.hostkit.dev

# Feature flags
FEATURE_BETA=true
DEBUG=false

# Third-party service credentials
STRIPE_SECRET_KEY=sk_...
TWILIO_ACCOUNT_SID=...
OPENAI_API_KEY=...
```

### Best Practices

1. **Use SCREAMING_SNAKE_CASE** for variable names
2. **Group related vars**: `STRIPE_*`, `TWILIO_*`, etc.
3. **Prefix secrets**: `{SERVICE}_SECRET`, `{SERVICE}_KEY`
4. **Prefix public vars**: `NEXT_PUBLIC_` (for Next.js)
5. **Document defaults**: Comments in `.env.example`
6. **Never commit secrets**: Use vault injection instead

---

## Secrets Management

### How to Handle API Keys and Secrets

**❌ DO NOT**:
- Hardcode secrets in code
- Commit `.env` to git (contains actual secrets)
- Use environment variables for multi-line secrets

**✅ DO**:
- Store in HostKit secrets vault (via MCP/CLI)
- Inject at deploy time: `--inject-secrets` flag
- Use `.env.example` template with placeholder values

### Workflow: Secrets Vault

```bash
# 1. Store secret in vault
hostkit secrets set {project} API_KEY sk_live_xxxxx

# 2. Inject on deploy
hostkit deploy {project} \
  --source /path \
  --inject-secrets \
  --restart

# 3. Secret automatically written to .env
# (then systemd makes it available to process)

# 4. Verify in app
process.env.API_KEY  // "sk_live_xxxxx"
```

### Vault Commands

```bash
# List secrets for project
hostkit secrets list {project}

# Set a secret
hostkit secrets set {project} KEY value

# Get a secret (for verification)
hostkit secrets get {project} KEY

# Remove a secret
hostkit secrets delete {project} KEY

# Rotate a secret
hostkit secrets rotate {project} KEY new_value
```

---

## Environment Variables in Next.js

### Server-side Only

These are available in API routes and server components:

```typescript
// app/api/route.ts (Server)
export async function GET() {
  const dbUrl = process.env.DATABASE_URL;  // ✅ Works
  const apiKey = process.env.API_KEY;      // ✅ Works

  return Response.json({ status: 'ok' });
}
```

```typescript
// app/page.tsx with 'use server'
'use server';

export default function Page() {
  const dbUrl = process.env.DATABASE_URL;  // ✅ Works
  // ...
}
```

### Client-side (Must Use `NEXT_PUBLIC_`)

Only variables prefixed with `NEXT_PUBLIC_` are available to browser code:

```bash
# .env
NEXT_PUBLIC_API_URL=https://myapp.hostkit.dev
SECRET_API_KEY=sk_...
```

```typescript
// Client component
'use client';

export default function Component() {
  const url = process.env.NEXT_PUBLIC_API_URL;  // ✅ "https://myapp.hostkit.dev"
  const secret = process.env.SECRET_API_KEY;    // ❌ undefined

  // Use NEXT_PUBLIC_ vars for client-side logic
  const response = await fetch(url + '/api/data');
}
```

### Build-time vs Runtime

- **Build-time**: Variables baked into `.next/` during `npm run build`
- **Runtime**: Variables available when process starts

```javascript
// next.config.js
module.exports = {
  env: {
    BUILT_AT: new Date().toISOString(),  // Baked at build time
  },
};
```

```typescript
// app/page.tsx
export default function Page() {
  const builtAt = process.env.BUILT_AT;  // Set at build time, not runtime
  const port = process.env.PORT;         // Set at runtime from .env
}
```

---

## Viewing and Managing Variables

### View All Variables

```bash
hostkit env get {project}
```

Output:
```json
{
  "PROJECT_NAME": "my-app",
  "PORT": "8001",
  "HOST": "127.0.0.1",
  "REDIS_URL": "redis://localhost:6379/2",
  "DATABASE_URL": "postgresql://...",
  "CUSTOM_VAR": "custom_value"
}
```

### View Specific Variables

```bash
hostkit env get {project} PORT DATABASE_URL
```

Output:
```json
{
  "PORT": "8001",
  "DATABASE_URL": "postgresql://..."
}
```

### View Secrets (Masked)

```bash
hostkit env get {project} API_KEY --show-secrets
```

Output:
```
API_KEY: sk_live_... (partially masked)
```

---

## Updating Environment Variables

### Single Variable

```bash
hostkit env set {project} DEBUG=true
```

### Multiple Variables

```bash
hostkit env set {project} \
  DEBUG=true \
  LOG_LEVEL=debug \
  FEATURE_BETA=true
```

### With Service Restart

```bash
hostkit env set {project} CRITICAL_VAR=value --restart
```

This:
1. Updates `/home/{project}/.env`
2. Restarts systemd service
3. Process picks up new vars

### Manual Edit

If you have SSH access:

```bash
ssh {project}@vps

# Edit .env directly
nano /home/{project}/.env

# Restart to pick up changes
systemctl restart hostkit-{project}
```

---

## .env.example Template

Commit this to your repo as a template (without secrets):

```bash
# .env.example - Rename to .env and fill in values

# Required: Set by HostKit on deployment
PROJECT_NAME=my-app
PORT=8001
HOST=127.0.0.1

# Required: Auto-configured by HostKit
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://user:pass@localhost:5432/my-app_db

# Optional: Auth service (if enabled)
AUTH_URL=http://127.0.0.1:9001

# Optional: Third-party services (set in vault, injected on deploy)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
OPENAI_API_KEY=sk-...

# Optional: Public URLs (for client-side)
NEXT_PUBLIC_API_URL=http://localhost:3000
NEXT_PUBLIC_APP_NAME=My App

# Optional: Feature flags
DEBUG=false
FEATURE_BETA=false
LOG_LEVEL=info
```

---

## Common Variable Issues

### Issue 1: Variable undefined after deploy

**Symptom**: `process.env.MY_VAR` is undefined

**Cause**: Variable not in `.env` file

**Fix**:
1. Check: `hostkit env get {project} MY_VAR`
2. If not present: `hostkit env set {project} MY_VAR=value --restart`
3. Redeploy

---

### Issue 2: Changes to .env not taking effect

**Symptom**: Updated `.env` but app still using old value

**Cause**: Process not restarted

**Fix**:
```bash
# Restart service
hostkit service restart {project}

# Or redeploy with --restart
hostkit deploy {project} --restart
```

---

### Issue 3: Secret in git history

**Symptom**: Accidentally committed API key to repo

**Cause**: Committed actual `.env` instead of `.env.example`

**Fix**:
1. Rotate the secret: `hostkit secrets rotate {project} API_KEY new_value`
2. Remove from git history: `git filter-repo --path .env --invert-paths`
3. Use vault for future secrets

---

### Issue 4: Client code can't access variable

**Symptom**: `process.env.MY_VAR` undefined in browser

**Cause**: Missing `NEXT_PUBLIC_` prefix

**Fix**:
```bash
# Rename variable in .env
NEXT_PUBLIC_MY_VAR=value

# Rebuild and deploy
npm run build
hostkit deploy {project} --source ./
```

---

## Special Variables

### HostKit-Reserved

Do not override these (they're set by HostKit):

| Variable | Set By | Override Warning |
|----------|--------|------------------|
| `PROJECT_NAME` | Project creation | If changed, may break things |
| `PORT` | Port assignment | If changed, service won't bind |
| `HOST` | HostKit | Always 127.0.0.1 |
| `REDIS_URL` | HostKit | Will break Redis connections |

### Service-Added Variables

These are added when services are enabled:

- `DATABASE_URL` - PostgreSQL connection string
- `AUTH_URL` - Auth service endpoint
- `STRIPE_*` - Payment service keys
- `TWILIO_*` - SMS service credentials
- `S3_*` - MinIO storage credentials

Don't manually override these unless you have a specific reason.

---

## Links to Related Documentation

- **[Deployment Pipeline →](DEPLOYMENT.md)** - Secrets injection during deploy
- **[Architecture →](ARCHITECTURE.md)** - Service variables reference
- **[Next.js Guide →](NEXTJS.md)** - NEXT_PUBLIC_ variables
