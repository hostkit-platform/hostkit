// Auth integration guide tool for hostkit-context MCP server
// Returns runtime-specific code examples and critical warnings about auth integration
//
// IMPORTANT: This guide incorporates critical learnings from Tesla Screen,
// the first project to successfully integrate all HostKit auth methods
// (magic link, Google OAuth, Apple OAuth).

import { getSSHManager } from '../services/ssh.js';
import { createLogger } from '../utils/logger.js';
import type { ToolResponse } from '../types.js';

const logger = createLogger('tools:auth-guide');

// =============================================================================
// CRITICAL INTEGRATION FIXES (from Tesla Screen implementation)
// =============================================================================
//
// Issue #1: JWT Public Key Newline Encoding
// The AUTH_JWT_PUBLIC_KEY env var contains literal "\n" strings, not real newlines.
// The jose library's importSPKI() requires proper PEM format with real newlines.
// FIX: Always do: const JWT_PUBLIC_KEY = process.env.AUTH_JWT_PUBLIC_KEY.replace(/\\n/g, "\n");
//
// Issue #2: OAuth Identity Verify Response Structure
// The /auth/identity/verify endpoint returns tokens NESTED under a "session" object:
// { user: {...}, session: { access_token, refresh_token }, is_new_user }
// NOT at the top level. Magic link returns tokens in URL fragment directly.
// FIX: Always extract with: const { access_token, refresh_token } = data.session || data;
//
// Issue #3: Apple Sign In Browser Compatibility
// Apple Sign In does NOT work in Chrome browsers (Apple policy restriction).
// FIX: Detect Chrome and hide the Apple Sign In button.

// =============================================================================
// Type Definitions
// =============================================================================

export interface AuthGuideParams {
  project: string;
}

type Runtime = 'python' | 'node' | 'nextjs' | 'static';

interface AuthConfig {
  auth_url: string;
  auth_port: number;
  jwt_public_key_env: string;
  oauth_providers: {
    google?: { configured: boolean };
    apple?: { configured: boolean };
  };
}

// =============================================================================
// Runtime-Specific Code Examples
// =============================================================================

const CODE_EXAMPLES: Record<Runtime, {
  login_redirect: string;
  verify_jwt: string;
  get_user: string;
  callback_handler: string;
  native_oauth?: string;
  middleware?: string;
}> = {
  nextjs: {
    login_redirect: `// app/login/page.tsx
'use client';

import { useState, useEffect } from 'react';

// IMPORTANT: OAuth goes through central proxy at auth.hostkit.dev
const OAUTH_PROXY_URL = 'https://auth.hostkit.dev';
const APP_URL = 'https://YOUR_PROJECT.hostkit.dev';  // Change this
const PROJECT_NAME = 'YOUR_PROJECT';                  // Change this

export default function LoginPage() {
  const [showAppleSignIn, setShowAppleSignIn] = useState(true);

  // CRITICAL: Apple Sign In doesn't work in Chrome (Apple policy)
  useEffect(() => {
    const userAgent = navigator.userAgent.toLowerCase();
    const isChrome = userAgent.includes('chrome') &&
                     !userAgent.includes('edg') &&
                     !userAgent.includes('opr');
    setShowAppleSignIn(!isChrome);
  }, []);

  const handleOAuth = (provider: 'google' | 'apple') => {
    // Build OAuth proxy URL - NOT direct to auth service
    const params = new URLSearchParams({
      project: PROJECT_NAME,
      return_url: \`\${APP_URL}/auth/callback\`,
    });
    window.location.href = \`\${OAUTH_PROXY_URL}/oauth/\${provider}/start?\${params}\`;
  };

  return (
    <div>
      <button onClick={() => handleOAuth('google')}>
        Sign in with Google
      </button>
      {/* Only show Apple button in compatible browsers */}
      {showAppleSignIn && (
        <button onClick={() => handleOAuth('apple')}>
          Sign in with Apple
        </button>
      )}
    </div>
  );
}`,

    verify_jwt: `// lib/auth.ts
import { jwtVerify, importSPKI } from 'jose';

const AUTH_URL = process.env.AUTH_URL!;

// CRITICAL FIX: AUTH_JWT_PUBLIC_KEY contains literal "\\n" strings, not real newlines.
// The jose library's importSPKI() requires proper PEM format with real newlines.
// This is the #1 cause of "Invalid character" errors during JWT verification.
const JWT_PUBLIC_KEY = (process.env.AUTH_JWT_PUBLIC_KEY || '').replace(/\\\\n/g, '\\n');

export async function verifyToken(token: string) {
  if (!JWT_PUBLIC_KEY) {
    console.error('AUTH_JWT_PUBLIC_KEY not configured');
    return null;
  }

  try {
    const publicKey = await importSPKI(JWT_PUBLIC_KEY, 'RS256');
    const { payload } = await jwtVerify(token, publicKey);
    return payload;
  } catch (error) {
    console.error('Token verification failed:', error);
    return null;
  }
}

export async function getAuthUser(token: string) {
  // Or call the auth service directly
  const response = await fetch(\`\${AUTH_URL}/me\`, {
    headers: { Authorization: \`Bearer \${token}\` },
  });
  if (!response.ok) return null;
  return response.json();
}

// CRITICAL: Identity verify returns tokens nested under 'session', not at top level
export async function verifyIdentity(identityToken: string) {
  const response = await fetch(\`\${AUTH_URL}/identity/verify\`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: identityToken }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to verify identity');
  }

  const data = await response.json();
  // CRITICAL FIX: Tokens are nested under 'session' in the identity verify response
  return data.session || data;
}`,

    callback_handler: `// app/auth/callback/page.tsx
'use client';

import { useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';

const AUTH_URL = 'https://YOUR_PROJECT.hostkit.dev/auth';  // Change this

function AuthCallbackHandler() {
  const searchParams = useSearchParams();
  const router = useRouter();

  useEffect(() => {
    const processCallback = async () => {
      // Check for identity token from OAuth proxy
      const identityToken = searchParams.get('identity');

      if (identityToken) {
        // OAuth flow - verify identity token
        try {
          const response = await fetch(\`\${AUTH_URL}/identity/verify\`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: identityToken }),
          });

          const responseBody = await response.json();
          if (!response.ok) {
            throw new Error(responseBody.detail || 'Failed to verify identity');
          }

          // CRITICAL: Tokens are nested under 'session' in the response
          const { access_token, refresh_token } = responseBody.session || responseBody;

          // Store tokens via session API
          await fetch('/api/auth/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ access_token, refresh_token }),
          });

          router.push('/dashboard');
        } catch (err) {
          router.push('/login?error=' + encodeURIComponent(err.message));
        }
        return;
      }

      // Magic link flow - tokens in URL fragment (hash)
      const hash = window.location.hash.substring(1);
      if (hash) {
        const hashParams = new URLSearchParams(hash);
        const access_token = hashParams.get('access_token');
        const refresh_token = hashParams.get('refresh_token');

        if (access_token && refresh_token) {
          await fetch('/api/auth/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ access_token, refresh_token }),
          });
          router.push('/dashboard');
          return;
        }
      }

      router.push('/login?error=Authentication+failed');
    };

    processCallback();
  }, [searchParams, router]);

  return <div>Processing authentication...</div>;
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <AuthCallbackHandler />
    </Suspense>
  );
}`,

    get_user: `// app/api/user/route.ts
import { cookies } from 'next/headers';
import { verifyToken } from '@/lib/auth';

export async function GET() {
  const cookieStore = await cookies();
  const token = cookieStore.get('auth_token')?.value;

  if (!token) {
    return Response.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const user = await verifyToken(token);
  if (!user) {
    return Response.json({ error: 'Invalid token' }, { status: 401 });
  }

  return Response.json({ user });
}`,

    middleware: `// middleware.ts
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const token = request.cookies.get('auth_token')?.value;

  // Protect routes that require auth
  if (request.nextUrl.pathname.startsWith('/dashboard')) {
    if (!token) {
      return NextResponse.redirect(new URL('/login', request.url));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/dashboard/:path*'],
};`,

    native_oauth: `// For React Native / iOS / Android apps using Google Sign-In SDK
// After getting the ID token from the native SDK, verify it with HostKit:

async function handleNativeGoogleSignIn(idToken: string) {
  const response = await fetch(\`\${AUTH_URL}/oauth/google/verify-token\`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id_token: idToken,
      // Required for iOS - your app's OAuth client ID (not web client ID)
      ios_client_id: 'your-ios-client-id.apps.googleusercontent.com',
    }),
  });

  if (!response.ok) {
    throw new Error('Authentication failed');
  }

  const { access_token, user } = await response.json();
  // Store access_token securely and use it for authenticated requests
  return { access_token, user };
}`,
  },

  node: {
    login_redirect: `// routes/auth.js
const express = require('express');
const router = express.Router();

// IMPORTANT: OAuth goes through central proxy at auth.hostkit.dev
const OAUTH_PROXY_URL = 'https://auth.hostkit.dev';
const APP_URL = 'https://YOUR_PROJECT.hostkit.dev';  // Change this
const PROJECT_NAME = 'YOUR_PROJECT';                  // Change this

// Redirect to HostKit OAuth proxy (NOT direct to auth service)
router.get('/login/:provider', (req, res) => {
  const { provider } = req.params;
  if (!['google', 'apple'].includes(provider)) {
    return res.status(400).json({ error: 'Invalid provider' });
  }
  const params = new URLSearchParams({
    project: PROJECT_NAME,
    return_url: \`\${APP_URL}/auth/callback\`,
  });
  res.redirect(\`\${OAUTH_PROXY_URL}/oauth/\${provider}/start?\${params}\`);
});

module.exports = router;`,

    verify_jwt: `// middleware/auth.js
const jose = require('jose');

const AUTH_URL = process.env.AUTH_URL;
// CRITICAL FIX: AUTH_JWT_PUBLIC_KEY contains literal "\\n" strings, not real newlines.
// The jose library's importSPKI() requires proper PEM format with real newlines.
const JWT_PUBLIC_KEY = (process.env.AUTH_JWT_PUBLIC_KEY || '').replace(/\\\\n/g, '\\n');

async function verifyToken(token) {
  if (!JWT_PUBLIC_KEY) {
    console.error('AUTH_JWT_PUBLIC_KEY not configured');
    return null;
  }

  try {
    const publicKey = await jose.importSPKI(JWT_PUBLIC_KEY, 'RS256');
    const { payload } = await jose.jwtVerify(token, publicKey);
    return payload;
  } catch (error) {
    console.error('Token verification failed:', error);
    return null;
  }
}

// CRITICAL: Identity verify returns tokens nested under 'session'
async function verifyIdentity(identityToken) {
  const response = await fetch(\`\${AUTH_URL}/identity/verify\`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: identityToken }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to verify identity');
  }

  const data = await response.json();
  // CRITICAL FIX: Tokens are nested under 'session' in the response
  return data.session || data;
}

// Express middleware
async function requireAuth(req, res, next) {
  const token = req.cookies.access_token || req.headers.authorization?.replace('Bearer ', '');

  if (!token) {
    return res.status(401).json({ error: 'Not authenticated' });
  }

  const user = await verifyToken(token);
  if (!user) {
    return res.status(401).json({ error: 'Invalid token' });
  }

  req.user = user;
  next();
}

module.exports = { verifyToken, verifyIdentity, requireAuth };`,

    callback_handler: `// routes/callback.js
const express = require('express');
const { verifyIdentity } = require('../middleware/auth');
const router = express.Router();

router.get('/auth/callback', async (req, res) => {
  const { identity, error } = req.query;

  if (error) {
    return res.redirect(\`/login?error=\${encodeURIComponent(error)}\`);
  }

  if (identity) {
    // OAuth flow - verify identity token
    try {
      const tokens = await verifyIdentity(identity);
      res.cookie('access_token', tokens.access_token, { httpOnly: true, secure: true, maxAge: 15 * 60 * 1000 });
      res.cookie('refresh_token', tokens.refresh_token, { httpOnly: true, secure: true, maxAge: 30 * 24 * 60 * 60 * 1000 });
      return res.redirect('/dashboard');
    } catch (err) {
      return res.redirect(\`/login?error=\${encodeURIComponent(err.message)}\`);
    }
  }

  // Magic link flow - tokens in query params or hash fragment (handled client-side)
  const { access_token, refresh_token } = req.query;
  if (access_token && refresh_token) {
    res.cookie('access_token', access_token, { httpOnly: true, secure: true, maxAge: 15 * 60 * 1000 });
    res.cookie('refresh_token', refresh_token, { httpOnly: true, secure: true, maxAge: 30 * 24 * 60 * 60 * 1000 });
    return res.redirect('/dashboard');
  }

  res.redirect('/login?error=Authentication+failed');
});

module.exports = router;`,

    get_user: `// routes/user.js
const express = require('express');
const { requireAuth } = require('../middleware/auth');
const router = express.Router();

router.get('/me', requireAuth, (req, res) => {
  res.json({ user: req.user });
});

module.exports = router;`,
  },

  python: {
    login_redirect: `# routes/auth.py
from urllib.parse import urlencode
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

# IMPORTANT: OAuth goes through central proxy at auth.hostkit.dev
OAUTH_PROXY_URL = "https://auth.hostkit.dev"
APP_URL = "https://YOUR_PROJECT.hostkit.dev"  # Change this
PROJECT_NAME = "YOUR_PROJECT"                  # Change this

@router.get("/login/{provider}")
async def login_oauth(provider: str):
    if provider not in ["google", "apple"]:
        return {"error": "Invalid provider"}

    # Redirect to HostKit OAuth proxy (NOT direct to auth service)
    params = urlencode({
        "project": PROJECT_NAME,
        "return_url": f"{APP_URL}/auth/callback",
    })
    return RedirectResponse(f"{OAUTH_PROXY_URL}/oauth/{provider}/start?{params}")`,

    verify_jwt: `# utils/auth.py
import os
import httpx
from jose import jwt, JWTError
from functools import lru_cache

AUTH_URL = os.environ["AUTH_URL"]

# CRITICAL FIX: AUTH_JWT_PUBLIC_KEY contains literal "\\n" strings, not real newlines.
# The python-jose library requires proper PEM format with real newlines.
_raw_key = os.environ.get("AUTH_JWT_PUBLIC_KEY", "")
JWT_PUBLIC_KEY = _raw_key.replace("\\\\n", "\\n")

@lru_cache()
def get_public_key():
    return JWT_PUBLIC_KEY

def verify_token(token: str) -> dict | None:
    if not JWT_PUBLIC_KEY:
        print("AUTH_JWT_PUBLIC_KEY not configured")
        return None

    try:
        payload = jwt.decode(
            token,
            get_public_key(),
            algorithms=["RS256"]
        )
        return payload
    except JWTError as e:
        print(f"Token verification failed: {e}")
        return None

# CRITICAL: Identity verify returns tokens nested under 'session'
async def verify_identity(identity_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{AUTH_URL}/identity/verify",
            json={"token": identity_token},
        )
        if response.status_code != 200:
            error = response.json()
            raise Exception(error.get("detail", "Failed to verify identity"))

        data = response.json()
        # CRITICAL FIX: Tokens are nested under 'session' in the response
        return data.get("session", data)

async def get_auth_user(token: str) -> dict | None:
    """Alternative: call auth service directly"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{AUTH_URL}/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code != 200:
            return None
        return response.json()`,

    callback_handler: `# routes/callback.py
from urllib.parse import urlencode
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from utils.auth import verify_identity

router = APIRouter()

@router.get("/auth/callback")
async def auth_callback(
    identity: str = Query(None),
    error: str = Query(None),
    access_token: str = Query(None),
    refresh_token: str = Query(None),
):
    if error:
        return RedirectResponse(f"/login?error={error}")

    if identity:
        # OAuth flow - verify identity token
        try:
            tokens = await verify_identity(identity)
            response = RedirectResponse("/dashboard")
            response.set_cookie("access_token", tokens["access_token"], httponly=True, secure=True, max_age=15*60)
            response.set_cookie("refresh_token", tokens["refresh_token"], httponly=True, secure=True, max_age=30*24*60*60)
            return response
        except Exception as e:
            return RedirectResponse(f"/login?error={str(e)}")

    # Magic link flow - tokens in query params
    if access_token and refresh_token:
        response = RedirectResponse("/dashboard")
        response.set_cookie("access_token", access_token, httponly=True, secure=True, max_age=15*60)
        response.set_cookie("refresh_token", refresh_token, httponly=True, secure=True, max_age=30*24*60*60)
        return response

    return RedirectResponse("/login?error=Authentication+failed")`,

    get_user: `# routes/user.py
from fastapi import APIRouter, Depends, HTTPException, Cookie
from utils.auth import verify_token

router = APIRouter()

async def get_current_user(auth_token: str = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = verify_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user

@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {"user": user}`,

    middleware: `# middleware/auth.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from utils.auth import verify_token

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for public routes
        if request.url.path in ["/", "/login", "/auth/callback"]:
            return await call_next(request)

        token = request.cookies.get("auth_token")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if token:
            user = verify_token(token)
            if user:
                request.state.user = user
                return await call_next(request)

        raise HTTPException(status_code=401, detail="Not authenticated")`,
  },

  static: {
    login_redirect: `<!-- IMPORTANT: OAuth goes through central proxy at auth.hostkit.dev -->
<script>
const OAUTH_PROXY_URL = 'https://auth.hostkit.dev';
const APP_URL = 'https://YOUR_PROJECT.hostkit.dev';  // Change this
const PROJECT_NAME = 'YOUR_PROJECT';                  // Change this

// CRITICAL: Apple Sign In doesn't work in Chrome (Apple policy)
function checkAppleSignInSupport() {
  const userAgent = navigator.userAgent.toLowerCase();
  const isChrome = userAgent.includes('chrome') &&
                   !userAgent.includes('edg') &&
                   !userAgent.includes('opr');
  if (isChrome) {
    document.getElementById('apple-signin-btn').style.display = 'none';
  }
}

function loginWithOAuth(provider) {
  const params = new URLSearchParams({
    project: PROJECT_NAME,
    return_url: APP_URL + '/auth/callback.html',
  });
  window.location.href = OAUTH_PROXY_URL + '/oauth/' + provider + '/start?' + params;
}

// Run on page load
checkAppleSignInSupport();
</script>

<button onclick="loginWithOAuth('google')">Sign in with Google</button>
<button id="apple-signin-btn" onclick="loginWithOAuth('apple')">Sign in with Apple</button>`,

    verify_jwt: `<!-- Static sites handle auth via cookies set by the callback page -->
<!-- For API calls that need auth, include credentials -->
<script>
async function fetchWithAuth(url, options = {}) {
  return fetch(url, {
    ...options,
    credentials: 'include',  // Include cookies
  });
}
</script>`,

    callback_handler: `<!-- auth/callback.html - Handle OAuth and magic link callbacks -->
<script>
const AUTH_URL = 'https://YOUR_PROJECT.hostkit.dev/auth';  // Change this

async function handleCallback() {
  const params = new URLSearchParams(window.location.search);
  const hash = window.location.hash.substring(1);

  // Check for identity token (OAuth flow)
  const identityToken = params.get('identity');
  if (identityToken) {
    try {
      const response = await fetch(AUTH_URL + '/identity/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: identityToken }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Auth failed');

      // CRITICAL: Tokens are nested under 'session' in the response
      const tokens = data.session || data;

      // Store tokens
      document.cookie = 'access_token=' + tokens.access_token + '; path=/; secure; max-age=900';
      document.cookie = 'refresh_token=' + tokens.refresh_token + '; path=/; secure; max-age=2592000';

      window.location.href = '/dashboard.html';
    } catch (err) {
      window.location.href = '/login.html?error=' + encodeURIComponent(err.message);
    }
    return;
  }

  // Check for direct tokens (magic link flow - in hash fragment)
  if (hash) {
    const hashParams = new URLSearchParams(hash);
    const accessToken = hashParams.get('access_token');
    const refreshToken = hashParams.get('refresh_token');

    if (accessToken && refreshToken) {
      document.cookie = 'access_token=' + accessToken + '; path=/; secure; max-age=900';
      document.cookie = 'refresh_token=' + refreshToken + '; path=/; secure; max-age=2592000';
      window.location.href = '/dashboard.html';
      return;
    }
  }

  window.location.href = '/login.html?error=Authentication+failed';
}

handleCallback();
</script>`,

    get_user: `<script>
async function getCurrentUser() {
  const response = await fetch('/auth/me', {
    credentials: 'include', // Include cookies
  });

  if (!response.ok) {
    return null;
  }

  return response.json();
}

// On page load
getCurrentUser().then(user => {
  if (user) {
    document.getElementById('user-name').textContent = user.name;
  } else {
    window.location.href = '/login.html';
  }
});
</script>`,
  },
};

// =============================================================================
// Tool Implementation
// =============================================================================

export async function handleAuthGuide(
  params: AuthGuideParams
): Promise<ToolResponse> {
  const { project } = params;

  if (!project) {
    return {
      success: false,
      error: {
        code: 'MISSING_PROJECT',
        message: 'Project name is required',
      },
    };
  }

  logger.info('Auth guide request', { project });

  try {
    const ssh = getSSHManager();

    // Get project info to determine runtime
    const projectInfo = await ssh.executeHostkit(`project info ${project}`);

    // Get auth config
    let authConfig: AuthConfig | null = null;
    try {
      const authResult = await ssh.executeHostkit(`auth config ${project}`);
      if (authResult && typeof authResult === 'object' && 'data' in authResult) {
        const data = authResult.data as Record<string, unknown>;
        authConfig = {
          auth_url: `https://${project}.hostkit.dev/auth`,
          auth_port: (data.auth_port as number) || 9000,
          jwt_public_key_env: 'AUTH_JWT_PUBLIC_KEY',
          oauth_providers: {
            google: data.google_client_id ? { configured: true } : undefined,
            apple: data.apple_client_id ? { configured: true } : undefined,
          },
        };
      }
    } catch (e) {
      // Auth might not be enabled
    }

    // Determine runtime
    let runtime: Runtime = 'node';
    if (projectInfo && typeof projectInfo === 'object') {
      const info = 'data' in projectInfo ? projectInfo.data : projectInfo;
      if (info && typeof info === 'object' && 'project' in info) {
        const proj = info.project as Record<string, unknown>;
        const rt = proj.runtime as string;
        if (rt === 'python') runtime = 'python';
        else if (rt === 'nextjs') runtime = 'nextjs';
        else if (rt === 'static') runtime = 'static';
        else runtime = 'node';
      }
    }

    const codeExamples = CODE_EXAMPLES[runtime];

    const guide = {
      project,
      runtime,
      auth_service: authConfig ? {
        url: authConfig.auth_url,
        internal_port: authConfig.auth_port,
        oauth_proxy: 'https://auth.hostkit.dev',
        oauth_providers: authConfig.oauth_providers,
      } : null,

      // =========================================================================
      // CRITICAL INTEGRATION FIXES (from Tesla Screen - first full implementation)
      // =========================================================================
      critical_fixes: {
        jwt_public_key_newlines: {
          problem: 'AUTH_JWT_PUBLIC_KEY env var contains literal "\\n" strings instead of real newlines. The jose/python-jose library requires proper PEM format.',
          symptom: 'JWT verification fails with "Invalid character" error. Users complete auth but get redirected back to login.',
          fix: 'ALWAYS convert newlines before using the key',
          code_js: 'const JWT_PUBLIC_KEY = (process.env.AUTH_JWT_PUBLIC_KEY || "").replace(/\\\\n/g, "\\n");',
          code_python: 'JWT_PUBLIC_KEY = os.environ.get("AUTH_JWT_PUBLIC_KEY", "").replace("\\\\n", "\\n")',
          severity: 'HIGH - This silently breaks JWT verification',
        },
        oauth_identity_response: {
          problem: '/auth/identity/verify returns tokens NESTED under a "session" object, not at the top level.',
          symptom: 'OAuth flow completes but session creation fails because tokens are extracted incorrectly.',
          fix: 'ALWAYS extract tokens from the session object',
          expected_response: '{ user: {...}, session: { access_token, refresh_token, token_type, expires_in }, is_new_user }',
          code: 'const { access_token, refresh_token } = responseBody.session || responseBody;',
          severity: 'HIGH - Undocumented response structure causes OAuth to silently fail',
        },
        apple_signin_chrome: {
          problem: 'Apple Sign In does NOT work in Chrome browsers due to Apple policy restrictions.',
          symptom: 'Apple Sign In button causes an error or infinite redirect in Chrome.',
          fix: 'Detect Chrome and hide the Apple Sign In button',
          code: 'const isChrome = userAgent.includes("chrome") && !userAgent.includes("edg") && !userAgent.includes("opr"); setShowAppleSignIn(!isChrome);',
          severity: 'MEDIUM - Apple policy, not a HostKit bug',
        },
      },

      critical_warnings: [
        'DO NOT implement OAuth yourself - use the central OAuth proxy at auth.hostkit.dev',
        'DO NOT add GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET to your project .env',
        'DO NOT use next-auth, passport, Auth.js, or similar OAuth libraries',
        'DO NOT redirect directly to /auth/oauth/google/login - use auth.hostkit.dev proxy',
        'DO NOT forget to convert \\n literals to real newlines in AUTH_JWT_PUBLIC_KEY',
        'DO NOT expect tokens at top level of /auth/identity/verify - they are nested under "session"',
        'DO NOT show Apple Sign In button in Chrome browsers',
      ],

      architecture: {
        explanation: 'HostKit uses a central OAuth proxy (auth.hostkit.dev) that handles OAuth for all projects with a single registered callback URL. Your app redirects to the proxy, which handles OAuth and returns an identity token. Your app then exchanges this for session tokens with your project auth service.',
        oauth_flow: [
          '1. User clicks "Sign in with Google" in your app',
          '2. Your app redirects to: https://auth.hostkit.dev/oauth/google/start?project=yourproject&return_url=...',
          '3. OAuth proxy redirects to Google',
          '4. User authenticates with Google',
          '5. Google redirects back to OAuth proxy callback',
          '6. Proxy validates token, creates signed identity payload',
          '7. Proxy redirects to your return_url with ?identity=<short-lived-token>',
          '8. Your app POST /auth/identity/verify { token: identity_token }',
          '9. Response: { user, session: { access_token, refresh_token }, is_new_user }',
          '10. Your app stores tokens in cookies and redirects to dashboard',
        ],
        magic_link_flow: [
          '1. User enters email in your app',
          '2. Your app POST /auth/magic-link/send { email, redirect_url }',
          '3. User receives email with magic link',
          '4. User clicks link',
          '5. Redirect to: {redirect_url}#access_token=...&refresh_token=...',
          '6. Your app extracts tokens from URL fragment (hash)',
          '7. Your app stores tokens in cookies and redirects to dashboard',
        ],
        key_differences: {
          oauth: 'Tokens returned via /auth/identity/verify API response, nested under "session"',
          magic_link: 'Tokens returned in URL fragment (#access_token=...)',
        },
      },

      endpoints: {
        oauth_proxy: {
          description: 'Central OAuth proxy for all HostKit projects (auth.hostkit.dev)',
          google_start: 'GET https://auth.hostkit.dev/oauth/google/start?project={project}&return_url={url}',
          apple_start: 'GET https://auth.hostkit.dev/oauth/apple/start?project={project}&return_url={url}',
          public_key: 'GET https://auth.hostkit.dev/keys/public',
        },
        project_auth: {
          description: 'Your project auth service endpoints',
          identity_verify: {
            endpoint: 'POST /auth/identity/verify',
            body: '{ "token": "<identity_token_from_oauth_proxy>" }',
            response: '{ user, session: { access_token, refresh_token, token_type, expires_in }, is_new_user }',
            note: 'CRITICAL: Tokens are nested under "session", not at top level!',
          },
          magic_link_send: {
            endpoint: 'POST /auth/magic-link/send',
            body: '{ "email": "user@example.com", "redirect_url": "https://yourproject.hostkit.dev/auth/callback" }',
          },
          token_refresh: 'POST /auth/token/refresh { refresh_token }',
          token_revoke: 'POST /auth/token/revoke { refresh_token }',
          me: 'GET /auth/me (with Authorization: Bearer <jwt>)',
        },
        native_oauth: {
          google_verify: 'POST /auth/oauth/google/verify-token',
          apple_verify: 'POST /auth/oauth/apple/verify-token',
          description: 'For mobile apps using native Sign-In SDKs. Send the ID token from the SDK to get a HostKit JWT.',
          params: {
            id_token: 'Required - the ID token from Google/Apple Sign-In SDK',
            ios_client_id: 'Required for iOS Google Sign-In - your iOS OAuth client ID',
            access_token: 'Optional - for additional validation',
          },
        },
      },

      environment_variables: {
        already_set: [
          'AUTH_URL - URL to the auth service (already in your .env)',
          'AUTH_JWT_PUBLIC_KEY - RSA public key for JWT verification (REQUIRES NEWLINE FIX!)',
        ],
        jwt_key_warning: 'AUTH_JWT_PUBLIC_KEY contains literal \\n strings. You MUST convert them: key.replace(/\\\\n/g, "\\n")',
        do_not_add: [
          'GOOGLE_CLIENT_ID - managed by auth service',
          'GOOGLE_CLIENT_SECRET - managed by auth service',
          'APPLE_CLIENT_ID - managed by auth service',
          'Any OAuth credentials - all managed by auth service',
        ],
      },

      code_examples: {
        runtime,
        note: `These examples are specifically for ${runtime} projects. They include CRITICAL FIXES for known issues. Copy and adapt as needed.`,
        login_redirect: {
          description: 'How to redirect users to OAuth (via auth.hostkit.dev proxy)',
          code: codeExamples.login_redirect,
        },
        verify_jwt: {
          description: 'How to verify JWTs from the auth service (INCLUDES NEWLINE FIX)',
          code: codeExamples.verify_jwt,
        },
        callback_handler: {
          description: 'How to handle OAuth and magic link callbacks (INCLUDES SESSION NESTING FIX)',
          code: codeExamples.callback_handler,
        },
        get_user: {
          description: 'How to get the current authenticated user',
          code: codeExamples.get_user,
        },
        ...(codeExamples.middleware ? {
          middleware: {
            description: 'Middleware to protect routes',
            code: codeExamples.middleware,
          },
        } : {}),
        ...(codeExamples.native_oauth ? {
          native_oauth: {
            description: 'How to handle native mobile app OAuth (iOS/Android)',
            code: codeExamples.native_oauth,
          },
        } : {}),
      },

      common_mistakes: [
        {
          mistake: 'Not converting \\n literals in AUTH_JWT_PUBLIC_KEY',
          why_wrong: 'The env var contains literal backslash-n characters, not real newlines. JWT verification will fail with "Invalid character" error.',
          fix: 'ALWAYS do: const JWT_PUBLIC_KEY = process.env.AUTH_JWT_PUBLIC_KEY.replace(/\\\\n/g, "\\n");',
          severity: 'HIGH',
        },
        {
          mistake: 'Extracting tokens from top level of /auth/identity/verify response',
          why_wrong: 'Tokens are nested under "session" object: { user, session: { access_token, refresh_token } }. Extracting from top level returns undefined.',
          fix: 'const { access_token, refresh_token } = responseBody.session || responseBody;',
          severity: 'HIGH',
        },
        {
          mistake: 'Showing Apple Sign In button in Chrome',
          why_wrong: 'Apple Sign In does not work in Chrome browsers due to Apple policy restrictions.',
          fix: 'Detect Chrome and conditionally hide the Apple button. See code examples.',
          severity: 'MEDIUM',
        },
        {
          mistake: 'Redirecting directly to /auth/oauth/google/login',
          why_wrong: 'OAuth must go through the central proxy at auth.hostkit.dev which has the registered callback URL.',
          fix: 'Redirect to: https://auth.hostkit.dev/oauth/{provider}/start?project={project}&return_url={url}',
          severity: 'HIGH',
        },
        {
          mistake: 'Adding GOOGLE_CLIENT_ID to .env and using next-auth',
          why_wrong: 'The auth service already has the OAuth credentials configured. Using next-auth duplicates functionality and won\'t work.',
          fix: 'Remove next-auth and OAuth credentials. Use the OAuth proxy flow.',
          severity: 'HIGH',
        },
        {
          mistake: 'Storing user data in your own database',
          why_wrong: 'The auth service maintains the user database. Duplicating it creates sync issues.',
          fix: 'Store only the user ID from the JWT. Fetch user details from /auth/me when needed.',
          severity: 'MEDIUM',
        },
      ],

      dependencies_needed: runtime === 'nextjs' || runtime === 'node'
        ? ['jose (npm install jose) - for JWT verification']
        : runtime === 'python'
        ? ['python-jose (pip install python-jose) - for JWT verification', 'httpx (pip install httpx) - for async HTTP calls']
        : [],

      next_steps: [
        '1. Remove any existing OAuth libraries (next-auth, passport, etc.)',
        '2. Remove OAuth credentials from your .env (GOOGLE_CLIENT_ID, etc.)',
        '3. Install jose (JS) or python-jose (Python) for JWT verification',
        '4. CRITICAL: Add newline fix when reading AUTH_JWT_PUBLIC_KEY',
        '5. Create login page that redirects to auth.hostkit.dev OAuth proxy',
        '6. Create callback page that handles both OAuth (identity token) and magic link (hash fragment)',
        '7. CRITICAL: Extract tokens from responseBody.session when using /auth/identity/verify',
        '8. Hide Apple Sign In button in Chrome browsers',
        '9. Add middleware to protect authenticated routes',
        '10. Test all auth methods: magic link, Google OAuth, Apple OAuth (in Safari)',
      ],

      debug_tips: {
        description: 'If auth is failing silently, add a debug endpoint to diagnose issues',
        create_debug_endpoint: '/api/auth/debug that returns: hasAccessToken, hasRefreshToken, jwtPublicKeyStart (first 50 chars), verificationSuccess, verificationError',
        common_debug_findings: {
          'jwtPublicKeyStart shows "\\n"': 'Newline fix not applied - key has literal backslash-n',
          'verificationError: Invalid character': 'Newline fix not applied',
          'tokens undefined after identity verify': 'Not extracting from session object',
          'Apple redirect fails in Chrome': 'Normal - Apple policy restriction',
        },
        callback_debug_mode: 'Add ?debug=true to callback URL to pause and show full state instead of auto-redirecting',
      },
    };

    return {
      success: true,
      data: guide,
    };
  } catch (error) {
    logger.error('Auth guide failed', error);
    return {
      success: false,
      error: {
        code: 'AUTH_GUIDE_ERROR',
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}
