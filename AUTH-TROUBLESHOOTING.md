# HostKit Auth Troubleshooting

Common issues and solutions for authentication problems.

---

## Diagnosis Tools

### Check Auth Service Status

```bash
# Health check
hostkit auth logs myapp --tail 20

# Full service info
hostkit auth logs myapp --follow

# Check if service is running
systemctl status hostkit-myapp-auth

# Test auth endpoint
curl http://127.0.0.1:9001/auth/health
```

### View Live Logs

```bash
# Stream logs in real-time
hostkit auth logs myapp --follow

# Last 100 lines
hostkit auth logs myapp --tail 100

# Errors only
hostkit auth logs myapp --stderr-only
```

### List Registered Users

```bash
hostkit auth users myapp
```

---

## Email Not Sending

### Symptoms
- Verification emails never arrive
- Magic links don't get sent
- Password reset emails missing

### Solutions

**1. Check SMTP Configuration**

```bash
# View current SMTP settings
hostkit env get myapp | grep SMTP

# Should output:
# SMTP_HOST=smtp.sendgrid.com
# SMTP_PORT=587
# SMTP_USER=apikey
# SMTP_PASS=SG...
```

If not configured:

```bash
hostkit env set myapp \
  SMTP_HOST=smtp.sendgrid.com \
  SMTP_PORT=587 \
  SMTP_USER=apikey \
  SMTP_PASS=SG.your_api_key \
  SMTP_FROM=noreply@myapp.com \
  --restart
```

**2. Test SMTP Connection**

Check logs for SMTP errors:

```bash
hostkit auth logs myapp --follow

# Look for lines like:
# ERROR: Could not connect to SMTP server
# ERROR: Authentication failed
# ERROR: TLS handshake failed
```

**3. Verify Credentials**

For SendGrid:
- Username: always `apikey`
- Password: Your SendGrid API key (starts with `SG.`)
- Host: `smtp.sendgrid.com`
- Port: `587` (TLS) or `465` (SSL)

For Gmail:
- Username: your gmail
- Password: App-specific password (not your account password)
- Host: `smtp.gmail.com`
- Port: `587` (TLS)

**4. Check Firewall**

If auth service can't connect to SMTP:

```bash
# Test connection from VPS
ssh root@145.223.74.213
telnet smtp.sendgrid.com 587

# Should see: "220 SMTP service ready"
```

**5. Restart Auth Service**

```bash
hostkit auth sync myapp

# Or full restart
systemctl restart hostkit-myapp-auth

# Wait 5 seconds
sleep 5

# Send test email
hostkit auth logs myapp --follow
# Trigger signup to test
```

### Email Template Issues

If emails are sending but content is wrong:

```bash
# Check template variables
hostkit auth logs myapp --tail 50 | grep -i "template\|render"

# Templates are in auth service
# /home/myapp/.auth/app/templates/email/
```

---

## Token Refresh Failing

### Symptoms
- "Token expired" errors on API calls
- 401 responses persist after refresh attempt
- User gets logged out unexpectedly

### Solutions

**1. Check Token Validity**

```bash
# Decode your token (at jwt.io or locally)
# Check these claims:
# - exp: Has it passed?
# - type: Is it "access"?
# - sub: Is there a user ID?

# Calculate expiry:
# exp value is Unix timestamp in seconds
# Compare with current time: date +%s
```

**2. Verify Refresh Token Setup**

```bash
# Check if refresh token is in cookies
# In browser DevTools:
# Application → Cookies → your domain
# Look for: "refresh_token" cookie (should be HttpOnly)

# Verify it's being sent:
# Check Network tab when calling /auth/token/refresh
# Headers should include cookie
```

**3. Check Session Existence**

```bash
# In database, verify session exists:
hostkit db query myapp 'SELECT * FROM sessions WHERE user_id = $1 LIMIT 1'

# Should show:
# id | user_id | refresh_token_hash | expires_at | revoked_at
# xxx | yyy | zzz | 2025-03-15... | (null)

# If revoked_at is not NULL, session was revoked
```

**4. Verify Public Key**

```bash
# Check if public key is correct
hostkit env get myapp AUTH_JWT_PUBLIC_KEY

# Should start with:
# -----BEGIN PUBLIC KEY-----

# Verify it matches private key
hostkit auth export-key myapp
```

**5. Test Refresh Flow**

```bash
# Get your refresh token from browser cookies
# Then test locally:

curl -X POST http://127.0.0.1:9001/auth/token/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"eyJ..."}'

# Should return new access_token
```

### Common Causes

| Issue | Cause | Fix |
|-------|-------|-----|
| 401 on refresh | Session revoked (user signed out) | User must sign in again |
| Token claimed to be expired but isn't | Clock skew (server time wrong) | Check `date` on VPS |
| Refresh always fails | Token not in cookies | Check iron-session config |
| Works locally, fails on prod | Different public key | Ensure env var synced |

---

## OAuth Provider Sign-In Fails

### Google OAuth Issues

**Symptoms**:
- "Invalid authorization code"
- "Token validation failed"
- Redirect loop

**Solutions**:

1. **Verify Client ID**:
```bash
hostkit env get myapp GOOGLE_CLIENT_ID

# Should match the ID in your frontend:
# process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
```

2. **Check Redirect URI**:

OAuth requires exact match:
```
Configured in your app:
https://accounts.google.com/o/oauth2/v2/auth?
  client_id=xxx
  redirect_uri=https://myapp.hostkit.dev/auth/callback

Must exactly match redirect registered with Google
(if using shared HostKit credentials, this is pre-registered)
```

3. **Verify Token Signature**:
```bash
# Check auth service logs for verification errors
hostkit auth logs myapp --follow

# Look for:
# ERROR: Could not verify Google token
# ERROR: JWT signature invalid
```

4. **Test Token Validation**:
```bash
# Decode Google token (from browser Network tab)
# at jwt.io
# Check claims:
# - aud: Should match client_id
# - iss: Should be accounts.google.com
# - exp: Not expired

# If aud mismatch:
# → Google ID token generated for different client
# → User signed in with different Google account?
```

5. **Public Key Cache**:
```bash
# Google public keys are cached (1 hour TTL)
# If key rotated, cache may be stale

# Force refresh:
hostkit auth sync myapp

# Check cache status in logs:
hostkit auth logs myapp --tail 50 | grep "public.key\|cache"
```

### Apple Sign-In Issues

**Symptoms**:
- "Invalid Apple ID token"
- "Could not verify token signature"
- Sign-in works on iOS but not web

**Solutions**:

1. **Verify Team ID & Key ID**:
```bash
hostkit env get myapp | grep APPLE

# Check for:
# APPLE_TEAM_ID=AB1234CDEF (10 chars)
# APPLE_KEY_ID=1ABCD23EFG (10 chars)
# APPLE_CLIENT_ID=com.myapp.web
```

2. **Check Private Key Format**:
```bash
# Should be PEM format
hostkit env get myapp APPLE_PRIVATE_KEY

# Should start with:
# -----BEGIN PRIVATE KEY-----
# ... base64 content ...
# -----END PRIVATE KEY-----

# If not PEM:
# 1. Download key from Apple Console again
# 2. Export as PEM if needed
# 3. Update: hostkit env set myapp APPLE_PRIVATE_KEY="..." --restart
```

3. **Token Expiry**:
```bash
# Apple tokens valid for 6 months only
# Check when token expires:
# In browser Network tab, find Apple response
# Decode JWT at jwt.io
# Check exp claim

# If expired:
# → User must sign in again
# → Apple will issue fresh token
```

4. **Test on Different Browser**:

Apple Sign-In best support:
- ✅ Safari (iOS, macOS) - Best
- ✅ Chrome (iOS, macOS) - Good
- ✅ Firefox (iOS, macOS) - Good
- ⚠️ Chrome (Android) - May not work
- ⚠️ Firefox (Android) - May not work

If failing on non-Safari, this is expected.

### Generic OAuth Issues

**State Token Mismatch**:
```
Error: "CSRF token mismatch"

Cause:
- Browser sessionStorage cleared
- Incognito window (new storage context)
- Multiple tabs with same redirect

Solution:
- Use persistent state storage (localStorage)
- Check state before exchanging code
```

**Redirect URI Mismatch**:
```
Error: "Redirect URI mismatch"

Cause:
- URI in provider console doesn't match request
- Protocol mismatch (http vs https)
- Domain mismatch (www. vs no www.)

Solution:
- Check exact URI in provider (Google Console, Apple Console)
- Ensure environment variable matches
```

---

## Email Verification Issues

### Verification Link Invalid

**Symptoms**:
- "Invalid verification token"
- "Token already used"
- Token expires before user clicks link

**Solutions**:

1. **Token Expiry Too Short**:

Default: 24 hours

If users need more time:
```bash
# Modify auth service config
# This requires editing the service directly (advanced)

# Or implement "Resend Email" button:
POST /auth/resend-verification
{
  "email": "user@example.com"
}
# Generates new token
```

2. **Resend Verification Email**:

```bash
# Users can request new token:

curl -X POST http://127.0.0.1:9001/auth/resend-verification \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com"}'
```

3. **Token Already Used**:

```bash
# Tokens are one-time use only
# If user clicks twice, second click fails

# Solution: Resend new token
# Don't retry same token
```

4. **Email Provider Filtering**:

Verification emails might be:
- In spam folder (add to contacts)
- Blocked by corporate email filter
- Rate limited (if too many emails sent)

---

## Magic Link Issues

### Magic Links Expire Too Quickly

**Default**: 15 minutes

**Solutions**:

**1. Extend Expiry** (if needed for testing):
```bash
# Edit auth service config
# /home/myapp/.auth/app/config.py
# Change: magic_link_expire_minutes = 15

# Redeploy auth service
systemctl restart hostkit-myapp-auth
```

**2. Rate Limiting**:

```
Error: "Too many requests"

Cause: User requested more than 5 links in 1 hour

Solution:
- Wait 1 hour before requesting new link
- Check RETRY_AFTER in response
```

### Token Already Used

```
Error: "Magic link is invalid or has already been used"

Cause:
- User clicked link twice
- Token used once, then link shared/reused

Solution:
- Resend new magic link
```

---

## Password Reset Issues

### Reset Link Expires Too Quickly

**Default**: 1 hour

If users need more time:
```bash
# Edit auth service config (same as magic links)
# /home/myapp/.auth/app/config.py
# Change: password_reset_expire_minutes = 60
```

### Password Doesn't Meet Requirements

**Default requirements**:
- 8+ characters
- 1 uppercase
- 1 lowercase
- 1 digit
- 1 special character

**Error message**:
```
"Password must be at least 8 characters"
"Must contain uppercase and lowercase letters"
"Must contain at least one number"
"Must contain at least one special character"
```

**Solutions**:
1. Use longer password
2. Mix character types
3. Add special characters: !@#$%^&*

---

## Anonymous Session Issues

### Can't Convert Anonymous User

**Symptoms**:
- "User is not anonymous"
- Convert endpoint rejects user

**Causes & Solutions**:

**1. User Already Has Email**:
```bash
# Anonymous users have email=NULL
# If they converted already, email is set

# Check in database:
hostkit db query myapp 'SELECT id, email, is_anonymous FROM users WHERE id=$1'

# If email is not NULL:
# → User already converted
# → Email/password already set
```

**2. Email Already Exists**:
```
Error: "Email already registered"

Cause:
- Email being used by another user
- Or same user trying to convert with existing email

Solution:
- Use different email
- Check if email already has account
```

**3. Invalid Password**:

```
Error: "Password must be at least 8 characters"

Solution:
- Password doesn't meet requirements (see above)
- Use stronger password
```

---

## Multi-Tab Session Issues

### Logout Not Syncing Across Tabs

**Symptoms**:
- User logs out in Tab A
- Tab B still shows as logged in
- Takes a few seconds/minutes to detect logout

**Causes**:

1. **iron-session not configured**:
```bash
# Check lib/session.ts exists and is configured
ls app/lib/session.ts

# Should have:
# password: process.env.SESSION_SECRET
# sameSite: "lax"
```

2. **No logout event detection**:
```typescript
// AuthContext.tsx should listen for logout:

useEffect(() => {
  const handleStorageChange = (e) => {
    if (e.key === "logout") {
      // User logged out in another tab
      setUser(null);
      router.push("/login");
    }
  };

  window.addEventListener("storage", handleStorageChange);
  return () => window.removeEventListener("storage", handleStorageChange);
}, []);
```

3. **Polling not implemented**:
```typescript
// Periodically check if still logged in:

useEffect(() => {
  const interval = setInterval(async () => {
    const res = await fetch("/auth/user");
    if (res.status === 401) {
      // User no longer authenticated
      setUser(null);
    }
  }, 60000); // Every minute

  return () => clearInterval(interval);
}, []);
```

**Solutions**:

**Option 1: Use storage events**:
```typescript
// In logout handler:
localStorage.setItem("logout", Date.now().toString());

// In AuthContext:
window.addEventListener("storage", (e) => {
  if (e.key === "logout") {
    setUser(null);
  }
});
```

**Option 2: Implement polling**:
```typescript
// Check auth status every 30 seconds
useEffect(() => {
  const checkAuth = async () => {
    const res = await fetch("/auth/user");
    if (res.status === 401 && user) {
      setUser(null);
    }
  };

  const interval = setInterval(checkAuth, 30000);
  return () => clearInterval(interval);
}, [user]);
```

**Option 3: Use service workers**:
```typescript
// Broadcast logout to all tabs via service workers
```

---

## Session Fixation Attack Concerns

### Risk Assessment

**Current HostKit Implementation**:
- ✅ Tracks session IP address + user agent
- ✅ Validates on token refresh
- ⚠️ Doesn't reject on minor changes (same browser)

**When is it risky?**
1. Attacker has same IP (shared WiFi, VPN)
2. Attacker sets user's cookies (rare, requires XSS)
3. User isn't aware they logged in from new device

### Mitigation Strategies

**1. Prompt on New Device**:
```typescript
// After login, check if device is new
const saveDeviceFingerprint = () => {
  const fingerprint = md5(navigator.userAgent + navigator.language);
  localStorage.setItem("device_fingerprint", fingerprint);
};

const checkNewDevice = () => {
  const saved = localStorage.getItem("device_fingerprint");
  const current = md5(navigator.userAgent + navigator.language);

  if (saved !== current) {
    // Prompt user to verify
    showDialog("Sign in from new device - verify email?");
  }
};
```

**2. Revoke Sessions on Sensitive Actions**:
```typescript
// When user changes password, revoke all other sessions
async function changePassword(newPassword) {
  const res = await fetch("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({
      newPassword,
      revoke_other_sessions: true  // Force other devices to re-login
    })
  });
}
```

**3. Enable Email Notifications**:
```typescript
// Send email on:
// - New device sign in
// - Sign in from unusual location (IP geolocation)
// - Session revoked

// Users can click "wasn't me" → revoke session
```

---

## User Account Issues

### Can't Find User

```bash
# List all users
hostkit auth users myapp

# Search for specific email
hostkit auth users myapp | grep "user@example.com"

# Query database directly
hostkit db query myapp 'SELECT * FROM users WHERE email=$1' --args 'user@example.com'
```

### User Locked Out

```bash
# After 5 failed signin attempts, account locks
# Lock lasts 15 minutes

# To force unlock:
# (requires direct DB access, advanced)

hostkit db query myapp \
  'UPDATE users SET failed_signin_count=0 WHERE email=$1' \
  --args 'user@example.com' \
  --allow-write
```

### Need to Delete User

```bash
# This cascades and deletes:
# - User record
# - OAuth accounts
# - Sessions
# - Magic links
# - Password resets
# - Email verifications

hostkit db query myapp \
  'DELETE FROM users WHERE id=$1' \
  --args 'user-uuid-123' \
  --allow-write
```

---

## Performance Issues

### Auth Service Slow

**Symptoms**:
- Login takes 5+ seconds
- Token refresh is slow

**Causes**:

1. **Database slow**:
```bash
# Check auth DB performance
hostkit db query myapp 'SELECT COUNT(*) FROM users'

# If over 10k users, add indexes
hostkit db query myapp 'CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)'
```

2. **OAuth provider slow**:
```bash
# Google/Apple endpoints may be slow
# Check logs:
hostkit auth logs myapp --follow

# Look for durations:
# "Google token validation took 2500ms"

# This is normal, OAuth servers can be slow
```

3. **Network latency**:
```bash
# Check latency from VPS to auth service
ssh root@145.223.74.213
time curl http://127.0.0.1:9001/auth/health

# Should be <50ms
```

### Token Validation Slow in App

**Cause**: Signature verification is CPU-intensive

**Solutions**:
1. Cache public key in memory (don't refetch)
2. Use Edge runtime for verification (runs closer to users)
3. Batch token validations

---

## Debugging Checklist

Use this when something breaks:

- [ ] Check service is running: `systemctl status hostkit-myapp-auth`
- [ ] Check logs: `hostkit auth logs myapp --tail 50`
- [ ] Verify env vars: `hostkit env get myapp | grep AUTH`
- [ ] Test health endpoint: `curl http://127.0.0.1:9001/auth/health`
- [ ] Check database exists: `hostkit db query myapp 'SELECT 1'`
- [ ] Verify Nginx routing: `curl https://myapp.hostkit.dev/auth/health`
- [ ] Check token claims: Decode JWT at jwt.io
- [ ] Review browser Network tab: See request/response
- [ ] Check browser cookies: Application → Cookies
- [ ] Verify SMTP if email issue: `hostkit env get myapp | grep SMTP`
- [ ] Restart service: `systemctl restart hostkit-myapp-auth`
- [ ] Sync env vars: `hostkit auth sync myapp`

---

## Getting Help

If issue persists:

1. **Collect diagnostics**:
```bash
# Save logs
hostkit auth logs myapp --tail 200 > auth_logs.txt

# Save env vars
hostkit env get myapp > env_vars.txt

# Save database state
hostkit db query myapp 'SELECT * FROM users LIMIT 5' > users.json

# Redact secrets before sharing
```

2. **Check HostKit status**:
```bash
hostkit health

# Check VPS resources
hostkit state scope=resources
```

3. **Review CLAUDE.md**:
- [AUTH.md](AUTH.md) - Architecture overview
- [AUTH-PROVIDERS.md](AUTH-PROVIDERS.md) - Provider details
- [ENVIRONMENT.md](ENVIRONMENT.md) - Environment variables

---

**Last updated**: February 2025 · HostKit v0.2.33
