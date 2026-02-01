# HostKit Authentication Guide

This document explains how to use authentication in your HostKit project.

---

## Available Auth Methods

| Method | Status | Description |
|--------|--------|-------------|
| Email/Password | ✅ Available | Traditional email + password registration/login |
| Magic Link | ✅ Available | Passwordless email login links |
| Anonymous | ✅ Available | Guest sessions that can be upgraded later |
| Google OAuth | ✅ Available | Sign in with Google (platform credentials) |
| Apple Sign-In | ✅ Available | Sign in with Apple (platform credentials) |

---

## Enabling Authentication

### Step 1: Enable Auth Service

If auth is not already enabled for your project:

```bash
ssh <project>@{{VPS_IP}} "sudo hostkit auth enable <project>"
```

This creates:
- Auth database (`<project>_auth_db`)
- JWT signing keys
- Auth service on port (project_port + 1000)

### Step 2: Enable OAuth (Optional)

To add Google/Apple OAuth using platform credentials:

```bash
ssh <project>@{{VPS_IP}} "sudo hostkit auth config <project> --from-platform"
```

This injects the platform's OAuth credentials into your auth service. No need to configure your own Google/Apple developer accounts.

---

## Auth Endpoints

Once enabled, your auth service provides these endpoints at `https://<project>.hostkit.dev/auth/`:

### Email/Password
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/register` | POST | Register with email/password |
| `/auth/login` | POST | Login with email/password |
| `/auth/logout` | POST | Logout (invalidate tokens) |
| `/auth/refresh` | POST | Refresh access token |

### Magic Link
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/magic-link/send` | POST | Send magic link email |
| `/auth/magic-link/verify` | POST | Verify magic link token |

### Anonymous
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/anonymous` | POST | Create anonymous session |
| `/auth/anonymous/upgrade` | POST | Upgrade to full account |

### OAuth (after `--from-platform`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/oauth/google` | POST | Initiate Google OAuth |
| `/auth/oauth/google/callback` | GET | Google OAuth callback |
| `/auth/oauth/google/verify-token` | POST | Verify Google ID token (native apps) |
| `/auth/oauth/apple` | POST | Initiate Apple Sign-In |
| `/auth/oauth/apple/callback` | POST | Apple Sign-In callback |
| `/auth/oauth/apple/verify-token` | POST | Verify Apple ID token (native apps) |

### User Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/me` | GET | Get current user |
| `/auth/me` | PATCH | Update current user |
| `/auth/password/change` | POST | Change password |
| `/auth/password/reset/request` | POST | Request password reset |
| `/auth/password/reset/confirm` | POST | Confirm password reset |

---

## OAuth Flow (Web Apps)

### Google OAuth
```javascript
// 1. Initiate OAuth
const response = await fetch('https://<project>.hostkit.dev/auth/oauth/google', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    redirect_uri: 'https://<project>.hostkit.dev/auth/oauth/google/callback',
    final_redirect_uri: 'https://yourapp.com/auth/callback',  // Where to redirect after auth
  })
});

const { authorization_url } = await response.json();

// 2. Redirect user to Google
window.location.href = authorization_url;

// 3. After auth, user is redirected to final_redirect_uri with tokens in URL fragment:
// https://yourapp.com/auth/callback#access_token=xxx&refresh_token=yyy&expires_in=3600
```

### Apple Sign-In
```javascript
// Same flow as Google, but use /auth/oauth/apple
const response = await fetch('https://<project>.hostkit.dev/auth/oauth/apple', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    redirect_uri: 'https://<project>.hostkit.dev/auth/oauth/apple/callback',
    final_redirect_uri: 'https://yourapp.com/auth/callback',
  })
});
```

---

## OAuth Flow (Native Apps)

For iOS/Android apps, use the verify-token endpoints to validate tokens from native SDKs:

### Google (iOS/Android)
```javascript
// After getting ID token from Google Sign-In SDK
const response = await fetch('https://<project>.hostkit.dev/auth/oauth/google/verify-token', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    id_token: '<google-id-token-from-sdk>',
    ios_client_id: '<your-ios-client-id>',  // Required for iOS apps
  })
});

const { user, session } = await response.json();
// session.access_token, session.refresh_token
```

### Apple (iOS)
```javascript
// After getting ID token from ASAuthorizationAppleIDCredential
const response = await fetch('https://<project>.hostkit.dev/auth/oauth/apple/verify-token', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    id_token: '<apple-id-token-from-credential>',
    bundle_id: '<your-app-bundle-id>',  // For audience validation
    user: '<user-json-if-first-signin>',  // Apple only sends name on first sign-in
  })
});

const { user, session } = await response.json();
```

---

## JWT Tokens

HostKit Auth issues RS256-signed JWTs:

- **Access Token**: Short-lived (1 hour default), used for API requests
- **Refresh Token**: Long-lived (30 days default), used to get new access tokens

### Verifying Tokens in Your API

The JWT public key is automatically added to your `.env` as `AUTH_JWT_PUBLIC_KEY`:

```python
# Python example
import jwt
import os

public_key = os.environ['AUTH_JWT_PUBLIC_KEY'].replace('\\n', '\n')

def verify_token(token):
    try:
        payload = jwt.decode(token, public_key, algorithms=['RS256'])
        return payload
    except jwt.InvalidTokenError:
        return None
```

```javascript
// Node.js example
const jwt = require('jsonwebtoken');

const publicKey = process.env.AUTH_JWT_PUBLIC_KEY.replace(/\\n/g, '\n');

function verifyToken(token) {
  try {
    return jwt.verify(token, publicKey, { algorithms: ['RS256'] });
  } catch (error) {
    return null;
  }
}
```

---

## Useful Commands

```bash
# Check auth status
ssh <project>@{{VPS_IP}} "sudo hostkit auth status <project>"

# View auth config
ssh <project>@{{VPS_IP}} "sudo hostkit auth config <project>"

# View auth logs
ssh <project>@{{VPS_IP}} "sudo hostkit auth logs <project>"

# Follow logs in real-time
ssh <project>@{{VPS_IP}} "sudo hostkit auth logs <project> --follow"

# Re-sync JWT public key to .env
ssh <project>@{{VPS_IP}} "sudo hostkit auth export-key <project> --update-env"
```

---

## Important Notes

1. **OAuth credentials are platform-managed** - You don't need your own Google/Apple developer accounts. Use `--from-platform` to get platform credentials.

2. **Callback URLs are automatic** - OAuth callbacks use your `<project>.hostkit.dev` subdomain automatically.

3. **Tokens in URL fragment** - For web OAuth, tokens are returned in the URL fragment (after `#`) for security. Fragments are not sent to servers.

4. **Apple first sign-in** - Apple only sends the user's name on the FIRST sign-in. Store it immediately.

5. **Native app audience** - For iOS apps, pass `ios_client_id` (Google) or `bundle_id` (Apple) for proper audience validation.

---

## Questions?

If you need help with authentication, check the HostKit documentation or ask for assistance.
