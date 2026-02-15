# HostKit Authentication System

## Overview

HostKit provides a **complete, production-ready authentication system** that deploys as an isolated service alongside your Next.js application. Each project gets its own auth database and service with support for multiple authentication methods.

### Architecture at a Glance

```
Your Next.js App (port 8001)
    ↓
Nginx reverse proxy (handles /auth/* routes)
    ↓
HostKit Auth Service (port 9001, isolated FastAPI service)
    ↓
Project Auth Database (PostgreSQL, project-isolated)
    ↓
OAuth providers, SMTP, storage backends
```

---

## Quick Start: Enable Auth

```bash
# Enable auth with default configuration
hostkit auth enable myapp

# Enable auth with OAuth providers
hostkit auth enable myapp \
  --google-client-id=xxx.apps.googleusercontent.com \
  --google-client-secret=yyy \
  --apple-client-id=com.myapp.web \
  --apple-team-id=AB1234CDEF \
  --apple-key-id=1ABCD23EFG
```

### What Gets Set Up

1. **Dedicated auth database** (`{project}_auth_db`)
2. **FastAPI service** running on `port + 1000`
3. **RSA keypair** for JWT signing (2048-bit)
4. **Systemd service** (`hostkit-{project}-auth.service`)
5. **Nginx routing** for `/auth/*` endpoints
6. **Environment variables** synced to your Next.js app

**Next.js receives**:
```bash
AUTH_ENABLED=true
AUTH_URL=http://127.0.0.1:9001                 # Server-side
NEXT_PUBLIC_AUTH_URL=https://myapp.hostkit.dev # Client-side
AUTH_JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----..."
GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...   # If enabled
APPLE_CLIENT_ID=... APPLE_TEAM_ID=...           # If enabled
```

---

## Authentication Methods

HostKit supports **5 authentication methods**:

| Method | Flow | Best For |
|--------|------|----------|
| **Email/Password** | User signs up, verifies email, signs in with password | Standard accounts |
| **Magic Links** | User receives one-time email link, signs in without password | Passwordless login, low friction |
| **Google OAuth** | User signs in with Google account | Social login (web + mobile) |
| **Apple Sign-In** | User signs in with Apple account | iOS native, web on Safari |
| **Anonymous** | Temporary account created, convertible to full account | Trials, guest access |

**Quick comparison**:
- 🔒 **Most secure**: Magic links + email verification
- ⚡ **Fastest UX**: Google/Apple OAuth + anonymous
- 🎯 **Best conversion**: Email/password + magic links together

See [AUTH-PROVIDERS.md](AUTH-PROVIDERS.md) for detailed implementation of each method.

---

## JWT Token System

### Access vs Refresh Tokens

**Access Token**:
- Used for API requests: `Authorization: Bearer {access_token}`
- Short-lived: 60 minutes
- Signed with RS256 (RSA 2048-bit)
- Expires automatically, request returns 401

**Refresh Token**:
- Exchanged for new access token
- Long-lived: 30 days
- Stored as HTTP-only cookie (secure)
- Database-tracked (can be revoked)
- Enables seamless session management

### Token Lifecycle

```
User signs in
    ↓
Auth service generates 2 tokens (access + refresh)
    ↓
Tokens returned to browser
    ↓
JavaScript stores in HTTP-only cookies (automatic via iron-session)
    ↓
API requests include access_token in Authorization header
    ↓
After 60 minutes: access_token expires
    ↓
Client calls POST /auth/token/refresh with refresh_token
    ↓
New access_token issued (old one discarded)
    ↓
API request retried with fresh token
    ↓
After 30 days: refresh_token expires
    ↓
User prompted to sign in again
```

### Key Management

**Private Key** (server-only):
- Location: `/home/{project}/.auth/jwt_private.pem`
- Used to SIGN tokens
- Never exposed to client
- 2048-bit RSA

**Public Key** (can be public):
- Location: `/home/{project}/.auth/jwt_public.pem`
- Used to VERIFY tokens
- Inlined in project `.env` for Edge runtime
- Available at `/auth/identity/keys` endpoint

**Token Claims**:
```json
{
  "sub": "user-uuid-123",
  "email": "user@example.com",
  "iat": 1705000000,
  "exp": 1705003600,
  "type": "access",
  "anonymous": false
}
```

---

## Database Schema

Each project gets its own PostgreSQL database with these tables:

### Users Table

```sql
users
├── id (UUID, primary key)
├── email (VARCHAR, unique, nullable)
├── password_hash (VARCHAR, bcrypt)
├── email_verified (BOOLEAN)
├── is_anonymous (BOOLEAN)
├── metadata (JSONB, custom data)
├── created_at, updated_at, last_sign_in_at
└── [foreign keys to oauth_accounts, sessions, etc.]
```

**Bcrypt Security**:
- 12-round hashing
- No plaintext passwords stored
- Timing-attack-resistant comparison

### OAuth Accounts Table

```sql
oauth_accounts
├── id (UUID)
├── user_id (FK → users)
├── provider (enum: google|apple|...)
├── provider_user_id (external ID)
├── provider_email
├── access_token, refresh_token
├── token_expires_at
└── UNIQUE(provider, provider_user_id)
```

Enables:
- Multiple OAuth providers per user
- Account linking
- Token management

### Sessions Table

```sql
sessions
├── id (UUID)
├── user_id (FK → users)
├── refresh_token_hash (SHA-256, indexed)
├── ip_address, user_agent
├── created_at, expires_at
├── revoked_at (NULL if active)
├── last_used_at (activity tracking)
```

**Session Revocation**:
- Sign out sets `revoked_at = NOW()`
- Next refresh token exchange fails
- Cross-tab logout automatically synced

### Email Verification & Password Reset

```sql
email_verifications
├── email, token_hash (SHA-256, unique)
├── created_at, expires_at (24 hours)
└── verified_at

password_resets
├── email, token_hash (SHA-256, unique)
├── created_at, expires_at (1 hour)
└── used_at

magic_links
├── email, token_hash (SHA-256, unique)
├── created_at, expires_at (15 minutes)
└── used_at
```

**Token Storage**:
- Tokens hashed with SHA-256 before storage
- One-time use enforced in DB
- Timing-attack-resistant verification

---

## Session Management

### How Sessions Work

1. **Creation**: User signs in → new session created in DB
2. **Storage**: Refresh token stored as HTTP-only cookie
3. **Tracking**: IP address + user agent recorded
4. **Expiry**: 30 days from creation (configurable)
5. **Revocation**: Sign out sets revoked flag
6. **Multi-tab sync**: All tabs share same cookies

### Multi-Tab Behavior

**Scenario**: User logs out in Tab A

1. Tab A calls `POST /auth/token/revoke`
2. Session marked as revoked in DB
3. Tab B makes API request with access token
4. Access token still valid (expires in 60 min), request succeeds
5. After 60 minutes, Tab B needs new access token
6. Token refresh fails (session revoked)
7. Tab B detects logout, redirects to login

**Cross-tab sync**: Detected via `storage` events or polling `/auth/user`

### Activity Tracking

Sessions track `last_used_at`:
- Updated on token refresh
- Use for "logged out due to inactivity" flows
- Detect abandoned sessions

---

## Email Delivery

The auth service sends emails for:
- Email verification (on signup)
- Magic links (passwordless login)
- Password reset links

### SMTP Configuration

**Environment variables** (optional, auth still works without email):
```bash
SMTP_HOST=smtp.sendgrid.com
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASS=SG.xxxxxxxxxxxxx
SMTP_FROM=noreply@myapp.com
SMTP_FROM_NAME="My App"
```

**Set via**:
```bash
hostkit env set myapp \
  SMTP_HOST=smtp.sendgrid.com \
  SMTP_PORT=587 \
  --restart
```

### Email Templates

Auto-generated per provider:
- **Verification email**: Link to verify email address
- **Magic link email**: One-time login link
- **Password reset email**: Link to set new password

All support HTML + plaintext.

---

## API Endpoints Reference

### Public (No Auth Required)

```
POST   /auth/signup                  # Email/password signup
POST   /auth/signin                  # Email/password signin
POST   /auth/verify-email            # Verify email token
POST   /auth/resend-verification     # Resend verification
POST   /auth/forgot-password         # Request password reset
POST   /auth/reset-password          # Apply new password
POST   /auth/magic-link/send         # Send magic link email
POST   /auth/magic-link/verify       # Verify magic link
POST   /auth/oauth/google            # Google flow
POST   /auth/oauth/google/callback   # Google callback
POST   /auth/oauth/google/verify     # Verify native token
POST   /auth/oauth/apple             # Apple flow
POST   /auth/oauth/apple/callback    # Apple callback
POST   /auth/oauth/apple/verify      # Verify native token
POST   /auth/anonymous/signup        # Create anonymous session
POST   /auth/token/refresh           # Get new access token
GET    /auth/identity/keys           # Get public keys
GET    /auth/health                  # Health check
```

### Protected (Access Token Required)

```
GET    /auth/user                    # Get current user
PATCH  /auth/user                    # Update profile
POST   /auth/anonymous/convert       # Convert to full account
POST   /auth/token/revoke            # Sign out
POST   /auth/signout                 # Sign out (clears cookies)
```

See [AUTH-PROVIDERS.md](AUTH-PROVIDERS.md) for request/response examples.

---

## Integration with Next.js

### What HostKit Generates

After enabling auth, these files are generated:

**`lib/session.ts`** - iron-session configuration:
```typescript
export const sessionOptions = {
  password: process.env.SESSION_SECRET,
  cookieName: "auth-session",
  cookieOptions: {
    secure: process.env.NODE_ENV === "production",
    httpOnly: true,
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30  // 30 days
  }
};
```

**`lib/auth.ts`** - Helper functions:
```typescript
export async function signUp(email: string, password: string) { ... }
export async function signIn(email: string, password: string) { ... }
export async function magicLinkSend(email: string) { ... }
export async function refreshToken(refreshToken: string) { ... }
```

**`types/auth.ts`** - TypeScript types:
```typescript
export interface User {
  id: string;
  email: string | null;
  email_verified: boolean;
  is_anonymous: boolean;
}
```

**`middleware.ts`** - Route protection:
```typescript
export function middleware(request: NextRequest) {
  const token = request.cookies.get("access_token");
  if (!token && request.nextUrl.pathname.startsWith("/dashboard")) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}
```

**`AuthContext.tsx`** - Client-side state:
```typescript
export function AuthProvider({ children }) {
  const [user, setUser] = useState<User | null>(null);
  // ... useEffect to fetch user on mount
}
```

### Basic Setup

**1. Add provider to your app**:
```typescript
// app/layout.tsx
import { AuthProvider } from "@/components/AuthProvider";

export default function RootLayout({ children }) {
  return (
    <html>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
```

**2. Use auth in components**:
```typescript
import { useAuth } from "@/lib/auth-context";

export default function Dashboard() {
  const { user, loading } = useAuth();

  if (loading) return <div>Loading...</div>;
  if (!user) return <div>Not authenticated</div>;

  return <div>Welcome, {user.email}</div>;
}
```

**3. Protect routes**:
```typescript
// middleware.ts
export const config = {
  matcher: ["/dashboard/:path*", "/settings/:path*"]
};

export function middleware(request: NextRequest) {
  const token = request.cookies.get("access_token")?.value;
  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}
```

See [AUTH-PROVIDERS.md](AUTH-PROVIDERS.md) for detailed provider-specific integration.

---

## Nginx Routing

HostKit automatically configures Nginx to route all `/auth/*` requests to the auth service:

```nginx
upstream auth_backend {
  server 127.0.0.1:9001;
}

location ~ ^/auth/ {
  proxy_pass http://auth_backend;

  # Preserve headers
  proxy_set_header Host $http_host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;

  # Preserve cookies
  proxy_pass_header Set-Cookie;
  proxy_pass_header Authorization;
}
```

**No manual Nginx configuration needed** — it's automatic.

---

## Configuration & Feature Toggles

### Enable/Disable Providers

```bash
# Enable specific providers
hostkit auth config myapp \
  --enable-magic-link \
  --disable-anonymous \
  --enable-google \
  --enable-apple

# View current config
hostkit auth config myapp --show
```

### Email Settings

```bash
# Set verification requirement
hostkit env set myapp \
  EMAIL_VERIFICATION_REQUIRED=true \
  --restart

# Configure SMTP
hostkit env set myapp \
  SMTP_HOST=smtp.sendgrid.com \
  SMTP_PORT=587 \
  SMTP_USER=apikey \
  SMTP_PASS=SG... \
  --restart
```

### Token Expiry (Advanced)

**Default values** (can be customized in auth service config):
- Access token: 60 minutes
- Refresh token: 30 days
- Email verification token: 24 hours
- Magic link token: 15 minutes
- Password reset token: 1 hour

To customize, modify `/home/{project}/.auth/app/config.py` and redeploy auth service.

---

## Security Best Practices

### Client-Side

✅ **DO**:
- Store tokens in HTTP-only cookies (automatic via iron-session)
- Check `Authorization` header on all API requests
- Handle 401 responses with automatic refresh
- Verify user exists via `/auth/user` endpoint
- Use HTTPS only (HostKit handles this)

❌ **DON'T**:
- Store tokens in localStorage (vulnerable to XSS)
- Trust JWT expiry alone (always validate on server)
- Hardcode redirect URIs (use environment variables)
- Send tokens in URL query parameters

### Server-Side

✅ **DO**:
- Validate JWT signature (don't just decode)
- Check token expiry
- Verify `type: access` claim
- Track user activity (IP, user agent)
- Rotate refresh tokens on each use

❌ **DON'T**:
- Accept expired tokens
- Trust client-provided user ID
- Skip email verification
- Allow unlimited password attempts
- Log sensitive data (passwords, tokens)

### Email

✅ **DO**:
- Verify email before sensitive operations
- Set email verification required by default
- Use SMTP with authentication
- Log email delivery
- Rate limit email sends

❌ **DON'T**:
- Send verification links in plaintext
- Allow unverified users to change email
- Reuse tokens across emails
- Send tokens in multiple ways (email + SMS)

---

## Management Commands

### View Auth Service Logs

```bash
hostkit auth logs myapp --follow
hostkit auth logs myapp --tail 100
hostkit auth logs myapp --stderr-only
```

### List Users

```bash
hostkit auth users myapp
hostkit auth users myapp --limit 50
```

### Disable Auth (Careful!)

```bash
# Shows what will be deleted
hostkit auth disable myapp

# Actually disable (requires confirmation)
hostkit auth disable myapp --force
```

**What happens**:
- Auth database dropped
- Auth service stopped
- Nginx routing removed
- Auth env vars removed from project
- All user sessions invalidated

### Export JWT Public Key

```bash
hostkit auth export-key myapp

# Use in client-side verification (Edge runtime)
# const publicKey = process.env.AUTH_JWT_PUBLIC_KEY;
```

### Sync Environment Variables

```bash
# Manually sync OAuth credentials to project
hostkit auth sync myapp
```

---

## Troubleshooting

For common issues and solutions, see [AUTH-TROUBLESHOOTING.md](AUTH-TROUBLESHOOTING.md).

**Quick links**:
- [Email not sending](AUTH-TROUBLESHOOTING.md#email-not-sending)
- [Token refresh failing](AUTH-TROUBLESHOOTING.md#token-refresh-failing)
- [OAuth signing in fails](AUTH-TROUBLESHOOTING.md#oauth-provider-signin-fails)
- [Multi-tab logout issues](AUTH-TROUBLESHOOTING.md#logout-not-syncing-across-tabs)
- [Session fixation concerns](AUTH-TROUBLESHOOTING.md#session-fixation-attack)

---

## What's Implemented vs What's Not

### ✅ Fully Implemented

- [x] Email/password authentication with bcrypt
- [x] Google OAuth (web + native apps)
- [x] Apple Sign-In (web + iOS)
- [x] Magic links (passwordless)
- [x] Anonymous sessions
- [x] JWT tokens (RS256, 2048-bit RSA)
- [x] Refresh token rotation
- [x] Session tracking & revocation
- [x] Email verification
- [x] Password reset
- [x] Multi-tab session sync
- [x] OAuth CSRF protection (state tokens)
- [x] Bcrypt password hashing
- [x] SMTP email delivery
- [x] Admin commands for user management
- [x] SQLAlchemy 2.0 compatibility
- [x] Activity tracking (last_used_at)

### ❌ Not Implemented

- [ ] HostKit as an OAuth provider (each project isolated)
- [ ] Built-in 2FA/MFA (can be implemented client-side)
- [ ] SMS passwordless (only email magic links)
- [ ] Biometric authentication (mobile only, external)
- [ ] Social OAuth providers beyond Google/Apple (extensible)
- [ ] SAML/SSO enterprise auth (would require provider setup)

---

## Architecture Diagram

```
Browser
  ↓
[HTTPS Request to /auth/signin]
  ↓
Nginx (port 443)
  ↓
proxy_pass http://127.0.0.1:9001
  ↓
FastAPI Auth Service
  ├─ Validates credentials
  ├─ Hashes/checks passwords (bcrypt)
  ├─ Checks OAuth providers (Google, Apple)
  ├─ Generates JWT tokens (RS256)
  ├─ Creates session in DB
  ├─ Returns tokens
  ↓
Nginx sets HTTP-only cookies
  ↓
Browser stores tokens (secure)
  ↓
Next.js app receives tokens
  ↓
iron-session middleware extracts tokens from cookies
  ↓
Components access via useAuth() hook
```

---

## Next Steps

1. **Enable auth**: `hostkit auth enable myapp`
2. **Configure providers**: [AUTH-PROVIDERS.md](AUTH-PROVIDERS.md)
3. **Integrate with Next.js**: See integration section above
4. **Troubleshoot issues**: [AUTH-TROUBLESHOOTING.md](AUTH-TROUBLESHOOTING.md)
5. **Customize**: Modify config/templates as needed

---

**Last updated**: February 2025 · HostKit v0.2.33
