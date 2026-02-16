# Next.js on HostKit: Complete Guide

## Critical Requirements

### 1. next.config.js MUST Have `output: 'standalone'`

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',  // ⚠️  REQUIRED - do not omit
  reactStrictMode: true,
  swcMinify: true,
};

module.exports = nextConfig;
```

**Why**: HostKit's deployment system expects the standalone build structure. Without it:
- Build deploys successfully
- Service starts
- App crashes on first request
- Health check fails after 2 minutes

**How to verify**: After `npm run build`, check for:
- `.next/standalone/` directory exists
- `.next/standalone/server.js` found
- `.next/standalone/node_modules/` present

---

## Build Outputs: Two Modes

### Mode A: Next.js Standalone (Recommended)

**What you get after `npm run build`**:
```
.next/
├── standalone/                    # Flattened app (server.js at root)
│   ├── server.js                 # Entry point
│   ├── node_modules/             # Dependencies bundled
│   ├── .next/
│   │   ├── server/               # Server-side bundle
│   │   └── ...                   # Other internals
│   └── [embedded source path]/   # Full source path embedded
├── static/                        # Static assets (NOT in standalone)
└── ...
```

**Deployment process**:
1. HostKit detects `.next/standalone/server.js`
2. Copies `.next/standalone/*` to release directory
3. Copies `.next/static/` separately
4. Copies `public/` separately
5. Updates systemd service: `ExecStart=/usr/bin/node /home/{project}/app/server.js`

**⚠️ Critical**: node_modules MUST be in standalone directory
- If missing: "standalone node_modules missing" error
- Fix: Don't exclude node_modules when deploying

**Benefits**:
- Smaller transfer (production dependencies only)
- Faster rsync
- Self-contained (doesn't need separate npm install)

---

### Mode B: Next.js Standard (Fallback)

If `.next/standalone/` missing but `.next/` exists:

**Deployment process**:
1. Detects `.next/` (no standalone)
2. Copies full directory including node_modules
3. Uses default: `ExecStart=/usr/bin/npm start`
4. Requires full npm install on deploy

**Requires**:
- Full `node_modules/` synced to server
- `npm install` on deploy (slower, ~2-5 min)

**When this happens**:
- If you build with non-standalone config
- If you deploy without `output: 'standalone'` in next.config.js
- Generally slower than standalone mode

---

## package.json Structure

### Minimum Required

```json
{
  "name": "my-hostkit-app",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0",
    "next": "^14.0.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/react": "^18.0.0",
    "typescript": "^5.0.0"
  }
}
```

### Recommended Full Stack

```json
{
  "name": "my-hostkit-app",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "jest",
    "db:migrate": "prisma migrate deploy",
    "db:seed": "node prisma/seed.js"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "next": "^14.0.0",
    "@prisma/client": "^5.0.0",
    "zod": "^3.22.0",
    "react-hook-form": "^7.48.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "typescript": "^5.0.0",
    "tailwindcss": "^3.3.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "prisma": "^5.0.0"
  }
}
```

---

## Port Binding

Next.js automatically uses the `PORT` environment variable:

```typescript
// You don't need to handle this - Next.js does it automatically
// The server will listen on the PORT from .env when deployed
```

**In .env (created by HostKit)**:
```bash
PORT=8001          # Assigned by HostKit
HOST=127.0.0.0     # Always localhost
```

**No need to**:
- Import process.env.PORT in code
- Call next.config.js with PORT
- Hardcode port numbers

---

## Required: Health Check Endpoint

Add this endpoint for HostKit health checks:

```typescript
// app/api/health/route.ts
export async function GET() {
  return Response.json(
    { status: 'ok', timestamp: new Date().toISOString() },
    { status: 200 }
  );
}
```

**What HostKit expects**:
- Endpoint: `http://127.0.0.1:{PORT}/api/health`
- Method: GET
- Response: Any HTTP 2xx status
- Time: < 5 seconds

**If missing**:
- Deploy succeeds but health check fails
- Service restarts due to failed health check
- Eventually stabilizes, but marked as failed

---

## Recommended Project Structure

```
my-app/
├── app/                              # Next.js App Router
│   ├── layout.tsx                   # Root layout + metadata
│   ├── page.tsx                     # Home page
│   ├── error.tsx                    # Error boundary
│   ├── not-found.tsx                # 404 page
│   ├── api/
│   │   ├── health/route.ts         # Required: health check
│   │   ├── auth/
│   │   │   ├── signin/route.ts
│   │   │   └── callback/route.ts
│   │   └── [resource]/
│   │       ├── route.ts            # GET, POST, PUT, DELETE
│   │       └── [id]/route.ts
│   ├── (public)/                   # Public routes
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   └── [page]/page.tsx
│   └── (auth)/                     # Protected routes
│       ├── layout.tsx              # Auth check
│       ├── dashboard/page.tsx
│       └── admin/page.tsx
├── components/
│   ├── ui/                         # shadcn/ui components
│   ├── Navigation.tsx
│   └── [feature]/
│       └── FeatureComponent.tsx
├── lib/
│   ├── db.ts                       # Prisma client singleton
│   ├── auth.ts                     # Auth utilities
│   ├── api-client.ts               # API helpers
│   └── logger.ts                   # Logging
├── prisma/
│   ├── schema.prisma               # Database schema
│   └── seed.ts                     # Seed data
├── public/
│   ├── favicon.ico
│   ├── robots.txt
│   └── [images]/
├── styles/
│   ├── globals.css                 # Tailwind + global styles
│   └── variables.css
├── middleware.ts                   # Auth checks, logging
├── next.config.js                  # Config with standalone
├── tailwind.config.ts              # Tailwind theme
├── tsconfig.json                   # TypeScript
├── jest.config.js                  # Tests (optional)
├── package.json
├── .env                            # Template (use .env.example in repo)
└── .gitignore
```

---

## Typical Build Process

### Local Build (before deploying)

```bash
# Install dependencies
npm install

# Build Next.js
npm run build
# Output: .next/, includes:
#   - .next/standalone/
#   - .next/static/
#   - .next/server/
#   - etc.

# Test start locally
npm start
# Runs: next start
# Listens on: http://localhost:3000 (default)
```

### On HostKit (if using --build)

```bash
# HostKit runs (in project working directory):
npm install --production  # 10 min timeout

npm run build             # 10 min timeout
# Same output as local build

# Then deploys the .next/ output
```

---

## Environment Variables

### Available in App

All vars from `/home/{project}/.env` are available:

```typescript
// In your Next.js app
const port = process.env.PORT;           // "8001"
const projectName = process.env.PROJECT_NAME;  // "my-app"
const dbUrl = process.env.DATABASE_URL;   // Auto-set by provision

// Public vars (visible to client)
const apiUrl = process.env.NEXT_PUBLIC_API_URL;  // If defined in .env
```

### Special Prefixes

| Prefix | Visibility | Example |
|--------|-----------|---------|
| (none) | Server-only | `DATABASE_URL`, `API_SECRET` |
| `NEXT_PUBLIC_` | Client-side too | `NEXT_PUBLIC_API_URL` |

```typescript
// app/api/route.ts (Server)
const secret = process.env.API_SECRET;  // ✅ Works

// app/page.tsx (Client Component)
const secret = process.env.API_SECRET;  // ❌ Undefined
const url = process.env.NEXT_PUBLIC_API_URL;  // ✅ Works
```

---

## Database Integration

### With Prisma

```bash
# Install
npm install @prisma/client
npm install -D prisma

# Initialize
npx prisma init

# Configure DATABASE_URL in .env
# DATABASE_URL="postgresql://user:password@localhost:5432/myapp_db"

# Create schema
# See prisma/schema.prisma

# Generate client
npx prisma generate

# Run migrations
npx prisma migrate dev --name init
npx prisma migrate deploy  # For production
```

**In app**:
```typescript
// lib/db.ts
import { PrismaClient } from '@prisma/client';

const globalForPrisma = global as unknown as { prisma: PrismaClient };

export const db = globalForPrisma.prisma ||
  new PrismaClient();

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = db;
```

**Auto-run migrations on deploy**:
- Add to `package.json`:
  ```json
  "scripts": {
    "build": "prisma generate && next build",
    "start": "prisma migrate deploy && next start"
  }
  ```
- Or run in API route on first request

---

## Authentication

### Option A: HostKit Auth Service (Recommended)

Enabled by default with `provision`. Auth env vars (AUTH_URL, AUTH_JWT_PUBLIC_KEY, etc.) are auto-set.

```typescript
// app/api/auth/verify/route.ts
export async function POST(request: Request) {
  const { token } = await request.json();

  // Verify identity token from OAuth proxy
  const response = await fetch('http://127.0.0.1:9001/auth/identity/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });

  if (!response.ok) return Response.json({ error: 'Invalid token' }, { status: 401 });

  const { user, session } = await response.json();

  return Response.json({ user, session });
}
```

### Option B: NextAuth.js (Custom Auth)

```bash
npm install next-auth
```

```typescript
// app/api/auth/[...nextauth]/route.ts
import NextAuth from 'next-auth';
import Credentials from 'next-auth/providers/credentials';

export const authOptions = {
  providers: [
    Credentials({
      async authorize(credentials) {
        // Custom auth logic
        return null;  // or user object
      },
    }),
  ],
};

const handler = NextAuth(authOptions);
export { handler as GET, handler as POST };
```

---

## Deployment Checklist

Before deploying to HostKit:

- [ ] `output: 'standalone'` in next.config.js
- [ ] Health check endpoint at `/api/health`
- [ ] `PORT` from env var (auto in Next.js)
- [ ] Database URL in `.env` (if using DB)
- [ ] Authentication configured (if needed)
- [ ] `npm run build` works locally
- [ ] `.next/standalone/` exists after build
- [ ] `.next/standalone/node_modules/` included
- [ ] No hardcoded localhost/port numbers
- [ ] Error pages configured (`error.tsx`, `not-found.tsx`)
- [ ] Environment variables documented
- [ ] `.gitignore` excludes node_modules, .next, .env.local
- [ ] `package-lock.json` committed (for reproducible builds)

---

## Common Next.js Issues on HostKit

### Issue 1: App crashes after deploy, health check fails

**Symptom**: Deploy succeeds, but health check times out

**Cause**:
- Missing health endpoint
- Port binding issue
- Uncaught error in startup

**Fix**:
1. Add `/api/health` endpoint
2. Check logs: `hostkit service logs {project} --follow`
3. Test locally: `npm run build && npm start`

---

### Issue 2: "Module not found" after deploy

**Symptom**: Deploy succeeds, app crashes: "Cannot find module X"

**Cause**:
- `--install` not used
- node_modules excluded from standalone
- Dependency not in package.json

**Fix**:
1. Redeploy with `--install=true`
2. Or manually: `cd /home/{project}/app && npm install`
3. Verify in package.json

---

### Issue 3: Stale content after deploy

**Symptom**: Changes deployed but old version showing

**Cause**:
- Build didn't run (`--build=false`)
- Cloudflare cache
- Browser cache

**Fix**:
1. Redeploy with `--build=true`
2. Clear browser cache (Ctrl+Shift+Delete)
3. If using Cloudflare: purge cache

---

### Issue 4: Env vars undefined in client code

**Symptom**: `process.env.API_URL` is undefined in browser

**Cause**: Vars without `NEXT_PUBLIC_` prefix aren't exposed to client

**Fix**:
```bash
# In .env
NEXT_PUBLIC_API_URL=https://myapp.hostkit.dev
```

```typescript
// In client component
const url = process.env.NEXT_PUBLIC_API_URL;  // ✅ Works
```

---

### Issue 5: Database connection fails

**Symptom**: `ECONNREFUSED` when connecting to DATABASE_URL

**Cause**:
- Database not created (project created with `project create` without `--with-db`, or `provision --no-db`)
- Wrong DATABASE_URL format
- PostgreSQL not running

**Fix**:
1. Create database: `hostkit db create {project}` (or re-run `provision` which is idempotent)
2. Verify DATABASE_URL: `hostkit env get {project} DATABASE_URL`
3. Test locally: `psql $DATABASE_URL`

---

## Performance Tips

### Optimize Build Size

```javascript
// next.config.js
module.exports = {
  output: 'standalone',
  swcMinify: true,  // Faster minification
  productionBrowserSourceMaps: false,  // Smaller
  onDemandEntries: {
    maxInactiveAge: 60 * 1000,
    pagesBufferLength: 5,
  },
};
```

### Optimize Runtime Performance

1. **Image optimization**:
   ```typescript
   import Image from 'next/image';
   <Image src="/pic.jpg" alt="pic" width={400} height={300} />
   ```

2. **API route optimization**:
   - Cache database queries
   - Use Redis for frequently accessed data
   - Add response caching headers

3. **Streaming responses**:
   ```typescript
   export async function GET() {
     const stream = new ReadableStream({...});
     return new Response(stream);
   }
   ```

---

## Links to Related Documentation

- **[Deployment Pipeline →](DEPLOYMENT.md)** - Full deploy process
- **[Environment Configuration →](ENVIRONMENT.md)** - Env vars & secrets
- **[Architecture →](ARCHITECTURE.md)** - Project structure & ports
- **[Troubleshooting →](TROUBLESHOOTING.md)** - Common issues
