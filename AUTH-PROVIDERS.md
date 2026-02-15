# HostKit Auth Providers - Detailed Implementation

Complete request/response formats and integration details for each authentication provider.

---

## Email/Password Authentication

### Signup

**Endpoint**: `POST /auth/signup`

**Request**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Success Response** (201 Created):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": false,
    "is_anonymous": false,
    "created_at": "2025-02-15T10:30:00Z",
    "updated_at": "2025-02-15T10:30:00Z"
  }
}
```

**Error Responses**:
```json
// Email already exists
{
  "detail": "Email already registered",
  "code": "EMAIL_EXISTS"
}

// Password doesn't meet requirements
{
  "detail": "Password must be at least 8 characters",
  "code": "INVALID_PASSWORD"
}

// Invalid email format
{
  "detail": "Invalid email format",
  "code": "INVALID_EMAIL"
}
```

**What Happens**:
1. Email validated (format check)
2. Email checked for uniqueness
3. Password hashed with bcrypt (12 rounds)
4. User record created in database
5. Session created for new user
6. Verification email sent automatically
7. Access + refresh tokens returned
8. User can sign in immediately (email_verified: false until verified)

**Password Requirements** (customizable):
- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character

### Signin

**Endpoint**: `POST /auth/signin`

**Request**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": true,
    "is_anonymous": false,
    "last_sign_in_at": "2025-02-15T11:00:00Z"
  }
}
```

**Error Responses**:
```json
// Invalid credentials
{
  "detail": "Invalid email or password",
  "code": "INVALID_CREDENTIALS"
}

// User doesn't exist
{
  "detail": "User not found",
  "code": "USER_NOT_FOUND"
}

// Account disabled/locked
{
  "detail": "Account is locked due to too many failed attempts",
  "code": "ACCOUNT_LOCKED"
}
```

**What Happens**:
1. Email lookup in database
2. Password compared with bcrypt hash (constant-time)
3. If no match, rate limiting applied (max 5 failed attempts)
4. On success, new session created
5. IP address + user agent recorded
6. `last_sign_in_at` timestamp updated
7. New access + refresh tokens issued

### Email Verification

**Endpoint**: `POST /auth/verify-email`

**Request**:
```json
{
  "token": "email_verification_token_from_email"
}
```

**Success Response** (200 OK):
```json
{
  "message": "Email verified successfully",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": true
  }
}
```

**Error Responses**:
```json
// Token invalid or malformed
{
  "detail": "Invalid verification token",
  "code": "INVALID_TOKEN"
}

// Token expired
{
  "detail": "Verification token has expired",
  "code": "TOKEN_EXPIRED"
}

// Token already used
{
  "detail": "Token has already been verified",
  "code": "TOKEN_ALREADY_USED"
}
```

**What Happens**:
1. Token hashed and looked up in DB
2. Expiry checked (24 hours default)
3. One-time use verified (used_at must be NULL)
4. User.email_verified set to true
5. Token marked as used (used_at = NOW())
6. Automatic email sent on signup to trigger this

**Resend Verification Email**:
```
POST /auth/resend-verification
{
  "email": "user@example.com"
}

Response (200):
{
  "message": "Verification email sent"
}
```

### Password Reset

**Step 1: Request Reset**

**Endpoint**: `POST /auth/forgot-password`

**Request**:
```json
{
  "email": "user@example.com"
}
```

**Response** (200 OK):
```json
{
  "message": "Password reset email sent if account exists"
}
```

Note: Response is same regardless of whether email exists (security: doesn't leak user emails)

**Step 2: Reset Password**

**Endpoint**: `POST /auth/reset-password`

**Request**:
```json
{
  "token": "password_reset_token_from_email",
  "new_password": "NewSecurePassword456!"
}
```

**Success Response** (200 OK):
```json
{
  "message": "Password reset successfully",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": true
  }
}
```

**Error Responses**:
```json
// Invalid token
{
  "detail": "Invalid reset token",
  "code": "INVALID_TOKEN"
}

// Token expired (1 hour default)
{
  "detail": "Reset token has expired",
  "code": "TOKEN_EXPIRED"
}

// Invalid password
{
  "detail": "Password must be at least 8 characters",
  "code": "INVALID_PASSWORD"
}
```

**What Happens**:
1. Reset link sent to email (contains token)
2. User clicks link in email
3. New password submitted via `/auth/reset-password`
4. Token validated (not expired, not used)
5. Password hashed with bcrypt
6. All existing sessions revoked (forces re-login on other devices)
7. Token marked as used

---

## Magic Links (Passwordless)

### Send Magic Link

**Endpoint**: `POST /auth/magic-link/send`

**Request**:
```json
{
  "email": "user@example.com"
}
```

**Response** (200 OK):
```json
{
  "message": "Magic link sent to email"
}
```

**Error Responses**:
```json
// Rate limited
{
  "detail": "Too many requests. Please try again later.",
  "code": "RATE_LIMITED",
  "retry_after": 60
}

// Invalid email
{
  "detail": "Invalid email format",
  "code": "INVALID_EMAIL"
}
```

**What Happens**:
1. Email validated
2. Rate limiting checked (max 5 per hour per email)
3. Random token generated (32 bytes, base64-encoded)
4. Token hashed with SHA-256
5. Token record inserted in `magic_links` table
6. Email sent with link: `https://myapp.hostkit.dev/auth/magic?token=xxx`
7. Token expires in 15 minutes (configurable)

**Flow**: User enters email → clicks "Send Magic Link" → email arrives → user clicks link → authenticated

### Verify Magic Link

**Endpoint**: `POST /auth/magic-link/verify`

**Request**:
```json
{
  "token": "magic_link_token_from_email"
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": true,
    "is_anonymous": false,
    "created_at": "2025-02-15T10:30:00Z"
  }
}
```

**Error Responses**:
```json
// Token invalid or already used
{
  "detail": "Magic link is invalid or has already been used",
  "code": "INVALID_TOKEN"
}

// Token expired (15 minutes)
{
  "detail": "Magic link has expired",
  "code": "TOKEN_EXPIRED"
}
```

**What Happens**:
1. Token hashed and looked up in DB
2. Expiry verified (15 minutes default)
3. One-time use verified (used_at must be NULL)
4. User record fetched or created (if first time)
5. Session created
6. Token marked as used (used_at = NOW())
7. Access + refresh tokens returned
8. User is fully authenticated (no email verification required)

**Use Cases**:
- First-time signup (no password needed)
- Password recovery (alternative to email/password flow)
- Two-factor authentication supplement
- Passwordless-only apps

---

## Google OAuth

### Architecture

HostKit operates a **credentials proxy** so you don't need to register your app in Google Console:

```
Shared OAuth credentials (registered by HostKit maintainers)
    ↓
/etc/hostkit/oauth.ini (on VPS)
    ↓
Project .env (auto-synced)
    ↓
Your app uses shared credentials
    ↓
Users sign in with their Google account
    ↓
Google returns ID token
    ↓
Your auth service validates token using Google's public keys
```

### Supported Flows

1. **Web Flow** (browser-based, most common)
   - User clicks "Sign in with Google"
   - Redirects to Google login
   - Google redirects back to your callback
   - Auth code exchanged for tokens

2. **Native App Flow** (iOS/Android)
   - App implements Google Sign-In SDK
   - SDK returns ID token directly
   - App sends token to `/auth/oauth/google/verify`

### Web Flow - Step by Step

**Step 1: Initiate OAuth**

**Endpoint**: `POST /auth/oauth/google`

**Request**:
```json
{
  "code": "4/0AXxxxxxxx",
  "redirect_uri": "https://myapp.hostkit.dev/auth/callback"
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@gmail.com",
    "email_verified": true,
    "is_anonymous": false,
    "metadata": {
      "google_id": "123456789",
      "name": "John Doe",
      "picture": "https://lh3.googleusercontent.com/..."
    }
  }
}
```

**Error Responses**:
```json
// Invalid auth code
{
  "detail": "Invalid authorization code",
  "code": "INVALID_CODE"
}

// Redirect URI mismatch
{
  "detail": "Redirect URI mismatch",
  "code": "REDIRECT_URI_MISMATCH"
}

// Token validation failed
{
  "detail": "Could not verify token with Google",
  "code": "INVALID_TOKEN"
}
```

**What Happens**:
1. Auth code sent to `/auth/oauth/google`
2. Auth service exchanges code for ID token (via Google)
3. ID token signature verified using Google's public keys
4. Token expiry checked
5. Audience (aud) claim validated
6. `at_hash` claim verified (ensures token matches code)
7. User ID from token extracted
8. User record created or linked to existing
9. OAuth account record created/updated
10. Session created
11. Your app's access + refresh tokens returned

**Implementation in Next.js**:
```typescript
// components/GoogleSignIn.tsx
import { signIn } from "@/lib/auth";

export function GoogleSignIn() {
  const handleClick = async () => {
    // 1. User redirects to Google
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${new URLSearchParams({
      client_id: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
      redirect_uri: `${process.env.NEXT_PUBLIC_AUTH_URL}/auth/callback`,
      response_type: "code",
      scope: "openid email profile",
      state: generateRandomState()
    })}`;
  };

  return <button onClick={handleClick}>Sign in with Google</button>;
}
```

**Callback handler** (`pages/auth/callback.tsx`):
```typescript
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

export default function AuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");

    if (code && validateState(state)) {
      // Exchange code for tokens
      fetch(`${process.env.NEXT_PUBLIC_AUTH_URL}/auth/oauth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          redirect_uri: `${process.env.NEXT_PUBLIC_AUTH_URL}/auth/callback`
        })
      })
        .then(res => res.json())
        .then(data => {
          // Tokens automatically set as HTTP-only cookies
          router.push("/dashboard");
        });
    }
  }, [code, state]);

  return <div>Signing in...</div>;
}
```

### Native App Flow - Google

**Endpoint**: `POST /auth/oauth/google/verify`

**Request** (from iOS/Android app):
```json
{
  "id_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": { ... }
}
```

**What Happens**:
1. ID token sent directly (no code exchange needed)
2. Signature verified
3. Claims validated
4. User created/linked
5. Your app's tokens returned

**Implementation in Swift**:
```swift
import GoogleSignIn

@MainActor
func signInWithGoogle() {
  guard let clientID = FirebaseApp.app()?.options.clientID else { return }

  let config = GIDConfiguration(clientID: clientID)
  GIDSignIn.sharedInstance.configuration = config

  GIDSignIn.sharedInstance.signIn(withPresenting: self) { result, error in
    guard let user = result?.user else { return }
    guard let idToken = user.idToken?.tokenString else { return }

    // Send to your auth service
    let request = URLRequest(url: URL(string: "/auth/oauth/google/verify")!)
    var request = request
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: ["id_token": idToken])

    URLSession.shared.dataTask(with: request) { data, _, _ in
      // Parse response, store tokens
    }.resume()
  }
}
```

### Configuration

**Environment Variables**:
```bash
# Required
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=yyy...

# Optional (if different web client for web vs mobile)
GOOGLE_WEB_CLIENT_ID=zzz.apps.googleusercontent.com
```

**Enable/Disable**:
```bash
hostkit auth config myapp --enable-google
hostkit auth config myapp --disable-google
```

---

## Apple Sign-In

### Supported Flows

1. **Web Flow** (browser-based)
2. **Native App Flow** (iOS/iPad)

### Web Flow - Step by Step

**Step 1: Initiate Sign in with Apple**

**Endpoint**: `POST /auth/oauth/apple`

**Request**:
```json
{
  "id_token": "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "name": {
      "firstName": "John",
      "lastName": "Doe"
    },
    "email": "user@privaterelay.appleid.com"
  }
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@privaterelay.appleid.com",
    "email_verified": true,
    "is_anonymous": false,
    "metadata": {
      "apple_id": "001234.abcd.5678",
      "name": "John Doe"
    }
  }
}
```

**Error Responses**:
```json
// Token invalid
{
  "detail": "Invalid Apple ID token",
  "code": "INVALID_TOKEN"
}

// Token expired (6 months)
{
  "detail": "Apple ID token has expired",
  "code": "TOKEN_EXPIRED"
}

// Signature verification failed
{
  "detail": "Could not verify token signature",
  "code": "VERIFICATION_FAILED"
}
```

**What Happens**:
1. ID token sent to `/auth/oauth/apple`
2. Token signature verified using Apple's public keys
3. Token expiry checked (6 months)
4. Claims validated
5. User record created or linked
6. OAuth account record created/updated
7. Session created
8. Your app's access + refresh tokens returned

**Implementation in Next.js**:
```typescript
// components/AppleSignIn.tsx
import AppleSignInButton from "@react-oauth/google"; // or use apple-id-signin JS library

export function AppleSignIn() {
  const handleAppleResponse = async (response: any) => {
    // Apple provides authorization result
    const { id_token, user } = response;

    const res = await fetch(
      `${process.env.NEXT_PUBLIC_AUTH_URL}/auth/oauth/apple`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id_token,
          user: {
            name: user?.name,
            email: user?.email
          }
        })
      }
    );

    if (res.ok) {
      window.location.href = "/dashboard";
    }
  };

  return (
    <div id="appleid-signin" data-type="sign in"></div>
  );
}
```

### Native App Flow - Apple

**Endpoint**: `POST /auth/oauth/apple/verify`

**Request** (from iOS app):
```json
{
  "id_token": "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "user": { ... }
}
```

**Implementation in Swift**:
```swift
import AuthenticationServices

class SignInWithAppleDelegate: NSObject, ASAuthorizationControllerDelegate {
  func authorizationController(
    controller: ASAuthorizationController,
    didCompleteWithAuthorization authorization: ASAuthorization
  ) {
    guard let appleIDCredential = authorization.credential as? ASAuthorizationAppleIDCredential else { return }

    let identityToken = appleIDCredential.identityToken
    let user = appleIDCredential.user

    // Send to your auth service
    let request = URLRequest(url: URL(string: "/auth/oauth/apple/verify")!)
    // ... send identityToken
  }
}
```

### Configuration

**Environment Variables**:
```bash
# Required
APPLE_CLIENT_ID=com.myapp.web              # Services ID
APPLE_TEAM_ID=AB1234CDEF                   # 10-character Apple Team ID
APPLE_KEY_ID=1ABCD23EFG                    # Key ID from Apple
APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----..."

# Private key should be PEM-formatted
# Get from: Apple Developer Console > Keys > Create new key
```

**Enable/Disable**:
```bash
hostkit auth config myapp --enable-apple
hostkit auth config myapp --disable-apple
```

### Key Points

- **Email Privacy**: Apple hides user email behind private relay (user@privaterelay.appleid.com)
- **Name Only on First Signin**: User's name returned only first time (not on subsequent logins)
- **6-month Token Validity**: Tokens valid for 6 months (longer than Google's)
- **Team ID Required**: Must have Apple Developer account
- **Browser Support**: Works on Safari, Chrome, Firefox (on iOS limited to Safari)

---

## Anonymous Sessions

### Create Anonymous Account

**Endpoint**: `POST /auth/anonymous/signup`

**Request**:
```json
{}  // No body needed
```

**Success Response** (201 Created):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": null,
    "email_verified": false,
    "is_anonymous": true,
    "created_at": "2025-02-15T10:30:00Z"
  }
}
```

**What Happens**:
1. New user record created with no email
2. `is_anonymous: true` flag set
3. Session created
4. Access + refresh tokens returned
5. User can access app immediately
6. Later, can convert to full account (email/password or OAuth)

**Use Cases**:
- Trial accounts (let users explore without signup)
- Guest checkouts (convert to account on purchase)
- Anonymous surveys
- Temporary data (convert to keep)

### Convert Anonymous to Full Account

**Endpoint**: `POST /auth/anonymous/convert`

**Request**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "email_verified": false,
    "is_anonymous": false
  }
}
```

**Error Responses**:
```json
// Email already exists
{
  "detail": "Email already registered",
  "code": "EMAIL_EXISTS"
}

// Not an anonymous user
{
  "detail": "User is not anonymous",
  "code": "NOT_ANONYMOUS"
}
```

**What Happens**:
1. Email validated and checked for uniqueness
2. Password hashed
3. Email and password_hash added to user record
4. `is_anonymous` set to false
5. New tokens returned (same user ID preserved)
6. Verification email sent
7. All previous data associated with user preserved

**Implementation in Next.js**:
```typescript
// Offer upgrade prompt when user does important action
function UpgradePrompt() {
  const { user } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  if (!user?.is_anonymous) return null;

  const handleUpgrade = async () => {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_AUTH_URL}/auth/anonymous/convert`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      }
    );

    if (res.ok) {
      // User upgraded, refresh state
      window.location.reload();
    }
  };

  return (
    <dialog>
      <h2>Create an account to save your progress</h2>
      <input
        type="email"
        placeholder="Email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <input
        type="password"
        placeholder="Password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button onClick={handleUpgrade}>Create Account</button>
    </dialog>
  );
}
```

---

## Token Management

### Refresh Token

**Endpoint**: `POST /auth/token/refresh`

**Request**:
```json
{
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Success Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

**Error Responses**:
```json
// Token expired
{
  "detail": "Refresh token has expired",
  "code": "TOKEN_EXPIRED"
}

// Session revoked
{
  "detail": "Session has been revoked",
  "code": "SESSION_REVOKED"
}

// Token invalid
{
  "detail": "Invalid refresh token",
  "code": "INVALID_TOKEN"
}
```

**What Happens**:
1. Refresh token signature verified
2. Session ID (`sid`) extracted from token
3. Session lookup in database
4. Session expiry checked
5. Revocation checked
6. New access token issued
7. Optionally: new refresh token issued (rotation)
8. `last_used_at` updated in session

### Revoke Token (Logout)

**Endpoint**: `POST /auth/token/revoke`

**Request** (requires valid access token):
```json
{}
```

**Success Response** (200 OK):
```json
{
  "message": "Token revoked successfully"
}
```

**What Happens**:
1. Access token validated
2. Session ID extracted
3. Session marked as revoked (revoked_at = NOW())
4. Next refresh attempt fails
5. Cookies cleared in browser
6. Multi-tab logout synced (via storage events or polling)

---

## User Profile Management

### Get Current User

**Endpoint**: `GET /auth/user`

**Request** (requires valid access token):
```
Authorization: Bearer {access_token}
```

**Success Response** (200 OK):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "email_verified": true,
  "is_anonymous": false,
  "metadata": {
    "name": "John Doe",
    "picture": "https://..."
  },
  "created_at": "2025-02-15T10:30:00Z",
  "updated_at": "2025-02-15T10:30:00Z",
  "last_sign_in_at": "2025-02-15T11:00:00Z"
}
```

**Error Responses**:
```json
// No valid token
{
  "detail": "Not authenticated",
  "code": "UNAUTHORIZED"
}

// Token expired
{
  "detail": "Token has expired",
  "code": "TOKEN_EXPIRED"
}
```

### Update User Profile

**Endpoint**: `PATCH /auth/user`

**Request** (requires valid access token):
```json
{
  "metadata": {
    "name": "Jane Doe",
    "picture": "https://example.com/picture.jpg"
  }
}
```

**Success Response** (200 OK):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "metadata": {
    "name": "Jane Doe",
    "picture": "https://example.com/picture.jpg"
  },
  "updated_at": "2025-02-15T11:05:00Z"
}
```

**What Happens**:
1. Access token validated
2. User record fetched
3. Metadata merged (not replaced)
4. Updated timestamp set
5. Changes returned

---

## Security & Best Practices

### JWT Token Validation

**Always validate on server**:
```typescript
// Pseudo-code
function validateToken(token: string): TokenPayload {
  // 1. Verify signature
  const payload = jwt.verify(token, publicKey, {
    algorithms: ["RS256"]
  });

  // 2. Check expiry
  if (payload.exp * 1000 < Date.now()) {
    throw new Error("Token expired");
  }

  // 3. Check type
  if (payload.type !== "access") {
    throw new Error("Invalid token type");
  }

  // 4. Check required claims
  if (!payload.sub || !payload.email) {
    throw new Error("Missing required claims");
  }

  return payload;
}
```

### CSRF Protection (OAuth)

OAuth flows use **state tokens** for CSRF protection:

```javascript
// Before redirecting to Google
const state = generateRandomString(32);
sessionStorage.setItem("oauth_state", state);

// In callback
const returnedState = new URLSearchParams(window.location.search).get("state");
if (returnedState !== sessionStorage.getItem("oauth_state")) {
  throw new Error("CSRF token mismatch");
}
```

HostKit auth service validates state automatically.

### Rate Limiting

Applied to:
- Email signup/signin (5 failed attempts = lockout)
- Magic link sends (5 per hour per email)
- Password reset (3 per hour per email)
- OAuth attempts (reasonable limits)

---

**Last updated**: February 2025 · HostKit v0.2.33
