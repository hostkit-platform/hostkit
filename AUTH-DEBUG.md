# HostKit Auth Debugging Guide

**Quick Start**: Diagnose auth issues with `hostkit auth diagnose <project>`

This guide helps you troubleshoot HostKit authentication service issues with structured diagnostics, common error patterns, and actionable solutions.

## Quick Diagnostic Workflow

### Step 1: Run Diagnostics
```bash
hostkit auth diagnose myapp
```

This will check:
- ✓ Service status (running, listening)
- ✓ Database connectivity
- ✓ JWT key configuration
- ✓ OAuth provider setup
- ✓ Email/SMTP configuration
- ✓ Recent error patterns

### Step 2: Check Output
The output will show:
- **Status**: Healthy/Degraded/Critical
- **Issues**: Any configuration or runtime problems
- **Recommendations**: Actionable fixes

### Step 3: Apply Fixes
Follow the suggested commands to fix identified issues.

### Step 4: Verify
Run `hostkit auth diagnose myapp` again to confirm fixes.

---

## Common Issues & Solutions

### 500 Internal Server Error on Signup/Signin

**Symptoms**:
- Signup/signin endpoints return HTTP 500
- No clear error message
- Client-side sees generic "server error"

**Diagnosis**:
```bash
hostkit auth diagnose myapp
curl https://myapp.hostkit.dev/auth/health
curl https://myapp.hostkit.dev/auth/diagnose | jq .checks
```

**Common Root Causes**:

#### JWT Keys Missing/Invalid
```
ERROR: JWT keys not configured
SUGGESTION: Regenerate JWT keys: hostkit auth export-key myapp --update-env
```

**Fix**:
```bash
# Check if keys exist
ls /home/myapp/.auth/jwt_*.pem

# Regenerate if missing
hostkit auth disable myapp --force
hostkit auth enable myapp
```

#### Database Connection Failed
```
ERROR: Database connection failed: connection refused
SUGGESTION: Check AUTH_DB_URL and PostgreSQL service
```

**Fix**:
```bash
# Check PostgreSQL is running
systemctl status postgresql

# Check auth database exists
psql -U postgres -c "SELECT datname FROM pg_database WHERE datname LIKE '%auth%';"

# Check database credentials in .env
hostkit env get myapp-auth AUTH_DB_URL
```

#### SMTP Not Configured (for email auth)
```
WARNING: Email enabled but SMTP not configured
SUGGESTION: Configure SMTP: hostkit env set myapp-auth SMTP_HOST=...
```

**Fix**:
```bash
# If email auth is required, configure SMTP
hostkit env set myapp-auth \
  SMTP_HOST=smtp.example.com \
  SMTP_PORT=587 \
  SMTP_USER=username \
  SMTP_PASS=password

# Then restart
systemctl restart hostkit-myapp-auth
```

**Alternative**: Disable email auth if not needed
```bash
hostkit auth config myapp --no-email
```

---

### OAuth Sign-In Fails

**Symptoms**:
- Google/Apple sign-in button returns error
- Redirect fails at provider
- "Callback mismatch" error in browser console

**Diagnosis**:
```bash
hostkit auth diagnose myapp
curl https://myapp.hostkit.dev/auth/diagnose | jq '.checks[] | select(.name=="oauth")'
hostkit auth config myapp  # Check current OAuth setup
```

**Common Root Causes**:

#### OAuth Credentials Not Set
```
ERROR: Google OAuth enabled but CLIENT_SECRET not set
SUGGESTION: Configure OAuth: hostkit auth config myapp --google-client-id=xxx --google-client-secret=yyy
```

**Fix**:
```bash
# Get credentials from Google Cloud Console
# https://console.cloud.google.com/

# Set them
hostkit auth config myapp \
  --google-client-id=YOUR_CLIENT_ID \
  --google-client-secret=YOUR_CLIENT_SECRET
```

#### Redirect URI Mismatch
```
Error: Redirect URI does not match registered
```

**Fix**:
```bash
# Get your project domain
hostkit project status myapp

# In Google Cloud Console, add this redirect URI:
# https://myapp.hostkit.dev/auth/oauth/google/callback

# Or set custom domain
hostkit auth config myapp --base-url=https://yourdomain.com
```

#### OAuth Proxy Unreachable
```
ERROR: Cannot reach OAuth proxy at https://auth.hostkit.dev
```

**Fix**:
```bash
# Check if OAuth proxy is running
curl https://auth.hostkit.dev/health

# If not, contact HostKit support
# OAuth proxy runs centrally on HostKit platform
```

---

### JWT Token Verification Fails

**Symptoms**:
- Access tokens fail validation
- Message: "token_invalid" or "token_expired"
- Login works but subsequent requests fail

**Diagnosis**:
```bash
hostkit auth diagnose myapp
curl https://myapp.hostkit.dev/auth/diagnose | jq '.checks[] | select(.name=="jwt_keys")'
```

**Common Root Causes**:

#### Public Key in .env Doesn't Match File
```
ERROR: Public key file found but .env key doesn't match
```

**Fix**:
```bash
# Sync the public key to .env
hostkit auth export-key myapp --update-env

# Restart auth service
systemctl restart hostkit-myapp-auth

# Verify by running diagnose again
hostkit auth diagnose myapp
```

#### Key File Permissions Wrong
```
ERROR: Cannot read JWT private key: permission denied
```

**Fix**:
```bash
# Fix permissions (should be 600)
chmod 600 /home/myapp/.auth/jwt_*.pem

# Verify
ls -l /home/myapp/.auth/jwt_*.pem

# Restart auth service
systemctl restart hostkit-myapp-auth
```

#### Private Key Deleted/Corrupted
```
ERROR: Private key file not found
```

**Fix** (destructive - deletes all sessions):
```bash
# Disable and re-enable auth (regenerates keys)
hostkit auth disable myapp --force
hostkit auth enable myapp

# Note: This invalidates all existing sessions and tokens
# Users will need to sign in again
```

---

### Database Connection Errors

**Symptoms**:
- "connection refused" errors in logs
- Users cannot sign up/in
- Database appears down

**Diagnosis**:
```bash
hostkit auth diagnose myapp
hostkit service logs myapp-auth --tail 50 | grep -i "database\|connection\|postgres"
```

**Common Root Causes**:

#### PostgreSQL Service Not Running
```
ERROR: Connection refused (PostgreSQL not running)
```

**Fix**:
```bash
# Check status
systemctl status postgresql

# Start if needed
systemctl start postgresql

# Check logs
journalctl -u postgresql -n 20
```

#### AUTH_DB_URL Incorrect
```
ERROR: authentication failed for user "myapp_auth_user"
```

**Fix**:
```bash
# Check current URL
hostkit env get myapp-auth AUTH_DB_URL

# Verify credentials
psql postgresql://myapp_auth_user:PASSWORD@localhost/myapp_auth_db

# If wrong, get correct URL
hostkit auth config myapp --show
```

#### Database Dropped/Recreated
```
ERROR: 42P01 relation "users" does not exist
```

**Fix**:
```bash
# Restart auth service (will run migrations)
systemctl restart hostkit-myapp-auth

# Or reinitialize
hostkit auth disable myapp --force
hostkit auth enable myapp
```

#### Connection Pool Exhausted
```
ERROR: sorry, too many clients already
```

**Fix**:
```bash
# Increase connection limit (contact support)
# Or restart auth service to recycle connections
systemctl restart hostkit-myapp-auth
```

---

### Email Sending Failures

**Symptoms**:
- Magic link emails not arriving
- Verification emails not sent
- "SMTP error" in logs

**Diagnosis**:
```bash
hostkit auth diagnose myapp
hostkit auth logs myapp --tail 50 | grep -i "smtp\|email\|mail"
```

**Common Root Causes**:

#### SMTP Not Configured
```
ERROR: Email enabled but SMTP not configured
SUGGESTION: Set SMTP_* environment variables
```

**Fix**:
```bash
# Configure SMTP
hostkit env set myapp-auth \
  SMTP_HOST=smtp.gmail.com \
  SMTP_PORT=587 \
  SMTP_USER=your-email@gmail.com \
  SMTP_PASS=your-app-password \
  SMTP_FROM=noreply@myapp.com

# For Gmail: Use "App Passwords" (not regular password)
# https://myaccount.google.com/apppasswords

# Restart
systemctl restart hostkit-myapp-auth
```

#### SMTP Credentials Invalid
```
ERROR: SMTP authentication failed
```

**Fix**:
```bash
# Verify credentials work
telnet smtp.gmail.com 587

# Update if wrong
hostkit env set myapp-auth SMTP_USER=correct-email@gmail.com
hostkit env set myapp-auth SMTP_PASS=correct-password
systemctl restart hostkit-myapp-auth
```

#### SMTP Host Unreachable
```
ERROR: Connection refused to SMTP host
```

**Fix**:
```bash
# Check if host and port are correct
nslookup smtp.gmail.com
ping smtp.gmail.com

# Verify firewall allows outbound 587
# Contact your SMTP provider for assistance
```

---

### Service Won't Start

**Symptoms**:
- `systemctl status hostkit-myapp-auth` shows failed
- Service crashes immediately
- No logs or cryptic error messages

**Diagnosis**:
```bash
# Check service status
systemctl status hostkit-myapp-auth

# View full logs
journalctl -u hostkit-myapp-auth -n 100

# Try manual start (may show errors)
sudo -u myapp /home/myapp/.auth/venv/bin/python -m uvicorn main:app --port 9001
```

**Common Root Causes**:

#### Python Dependencies Missing
```
ModuleNotFoundError: No module named 'fastapi'
```

**Fix**:
```bash
# Reinstall dependencies
cd /home/myapp/.auth
pip install -r requirements.txt

# Or redeploy auth service
hostkit auth config myapp --no-restart
systemctl restart hostkit-myapp-auth
```

#### Configuration Syntax Error
```
SyntaxError in config.py
```

**Fix**:
```bash
# Check config syntax
python -m py_compile /home/myapp/.auth/config.py

# Fix any errors manually or redeploy
hostkit auth disable myapp --force
hostkit auth enable myapp
```

#### Port Already In Use
```
OSError: Address already in use (:9001)
```

**Fix**:
```bash
# Check what's using the port
lsof -i :9001

# Kill the process
kill -9 <PID>

# Or restart the service
systemctl restart hostkit-myapp-auth
```

---

## Diagnostic Endpoints

### GET /auth/health
Quick health check.

**Response** (healthy):
```json
{
  "status": "ok",
  "service": "auth",
  "project": "myapp",
  "timestamp": "2025-02-15T10:30:00Z",
  "checks": {
    "database": "ok",
    "jwt": "ok"
  },
  "warnings": null
}
```

**Response** (degraded):
```json
{
  "status": "degraded",
  "checks": {
    "database": "ok",
    "jwt": "warning",
    "email": "warning"
  },
  "warnings": ["JWT key files missing", "Email enabled - verify SMTP"]
}
```

**Usage**:
```bash
# From command line
curl https://myapp.hostkit.dev/auth/health | jq

# Monitor in loop
while true; do curl https://myapp.hostkit.dev/auth/health && sleep 5; done
```

---

### GET /auth/diagnose
Comprehensive diagnostics with configuration validation.

**Response**:
```json
{
  "overall_health": "degraded",
  "checks": [
    {
      "name": "database",
      "status": "ok",
      "message": "Database connected and all required tables exist",
      "suggestion": null,
      "details": {
        "tables": ["magic_links", "oauth_accounts", "sessions", "users"]
      }
    },
    {
      "name": "jwt_keys",
      "status": "error",
      "message": "Public key file not found: /home/myapp/.auth/jwt_public.pem",
      "suggestion": "Regenerate JWT keys: hostkit auth export-key myapp --update-env",
      "details": {
        "public_key_path": "/home/myapp/.auth/jwt_public.pem",
        "public_key_exists": false
      }
    }
  ],
  "configuration": {
    "project_name": "myapp",
    "base_url": "https://myapp.hostkit.dev",
    "email_enabled": true,
    "google_enabled": false,
    "apple_enabled": false,
    "jwt_keys_configured": false
  },
  "timestamp": "2025-02-15T10:35:00Z"
}
```

**Check Details**:
- `name`: Type of check (database, jwt_keys, oauth, email, base_url, cors)
- `status`: "ok", "warning", or "error"
- `message`: Human-readable description
- `suggestion`: Recommended action to fix
- `details`: Technical details for debugging

**Usage**:
```bash
# Full diagnostics
curl https://myapp.hostkit.dev/auth/diagnose | jq

# Check only JWT status
curl https://myapp.hostkit.dev/auth/diagnose | jq '.checks[] | select(.name=="jwt_keys")'

# Check if critical issues
curl https://myapp.hostkit.dev/auth/diagnose | jq '.overall_health'
```

---

## CLI Command Reference

### hostkit auth diagnose
Comprehensive auth service diagnostics.

```bash
# Basic diagnosis
hostkit auth diagnose myapp

# Verbose (more details)
hostkit auth diagnose myapp --verbose

# Test auth endpoints (try signup, signin, etc.)
hostkit auth diagnose myapp --test-endpoints

# JSON output
hostkit --json auth diagnose myapp | jq
```

**Output**:
- Service status (running/stopped)
- Remote diagnostics (from /auth/diagnose endpoint)
- Issues found (if any)
- Recommendations (actionable fixes)

---

### hostkit auth logs
View auth service logs.

```bash
# Last 100 lines
hostkit auth logs myapp

# Last 50 lines
hostkit auth logs myapp --lines 50

# Follow in real-time
hostkit auth logs myapp --follow

# Tail and grep
hostkit auth logs myapp --lines 200 | grep -i "error\|failed"
```

---

### hostkit env get / set
Manage auth environment variables.

```bash
# View all auth env vars
hostkit env get myapp-auth

# View specific var
hostkit env get myapp-auth AUTH_DB_URL

# Set a variable
hostkit env set myapp-auth SMTP_HOST=smtp.gmail.com

# Set multiple
hostkit env set myapp-auth \
  SMTP_HOST=smtp.gmail.com \
  SMTP_PORT=587 \
  SMTP_USER=user@gmail.com

# After changes, restart auth service
systemctl restart hostkit-myapp-auth
```

---

### hostkit service logs
View service logs with systemd.

```bash
# Last 50 lines
hostkit service logs myapp-auth --tail 50

# Follow in real-time
hostkit service logs myapp-auth --follow

# Search for errors
hostkit service logs myapp-auth --tail 500 | grep ERROR

# Last 1 minute of logs
journalctl -u hostkit-myapp-auth --since "1 min ago"
```

---

### hostkit auth export-key
Export and sync JWT public key.

```bash
# Show current public key (PEM format)
hostkit auth export-key myapp

# Show in .env format (escaped newlines)
hostkit auth export-key myapp --env-format

# Update project .env with inline key (for Edge runtime)
hostkit auth export-key myapp --update-env
```

---

## Error Code Reference

| Code | Meaning | Solution |
|------|---------|----------|
| `invalid_credentials` | Wrong email/password | Verify credentials; use signup if new |
| `email_already_registered` | Email exists | Use signin or password reset |
| `token_expired` | JWT/refresh token expired | Get new token via signin |
| `token_invalid` | Token verification failed | Check JWT keys: `hostkit auth export-key myapp --update-env` |
| `user_not_found` | User doesn't exist | Create account with signup |
| `email_not_configured` | SMTP not setup | Configure SMTP env vars |
| `oauth_error` | OAuth provider error | Check OAuth credentials and redirect URI |
| `database_error` | Database connection failed | Check PostgreSQL and AUTH_DB_URL |
| `migration_failed` | Schema migration error | Restart service or reinitialize auth |
| `internal_server_error` | Unhandled exception | Run diagnose and check logs |

---

## Debugging Workflow Examples

### Example 1: User Can't Sign Up

```bash
# 1. Run diagnostics
hostkit auth diagnose myapp

# 2. Check /auth/health
curl https://myapp.hostkit.dev/auth/health | jq

# 3. Check /auth/diagnose for specific issues
curl https://myapp.hostkit.dev/auth/diagnose | jq .checks

# 4. Check logs for errors
hostkit auth logs myapp --tail 100 | grep -i "error\|failed"

# 5. Test database
psql postgresql://myapp_auth_user:PASSWORD@localhost/myapp_auth_db \
  -c "SELECT COUNT(*) FROM users;"

# 6. Check JWT keys
ls -l /home/myapp/.auth/jwt_*.pem

# 7. Re-sync keys if needed
hostkit auth export-key myapp --update-env
systemctl restart hostkit-myapp-auth
```

### Example 2: Google OAuth Not Working

```bash
# 1. Run diagnostics
hostkit auth diagnose myapp

# 2. Check OAuth configuration
hostkit auth config myapp | grep -i google

# 3. Verify Google Cloud credentials
# - Go to: https://console.cloud.google.com/
# - Check Client ID and Secret are set
# - Verify Redirect URI includes:
#   https://myapp.hostkit.dev/auth/oauth/google/callback

# 4. Check remote diagnostics
curl https://myapp.hostkit.dev/auth/diagnose | jq '.checks[] | select(.name=="oauth")'

# 5. View OAuth errors in logs
hostkit auth logs myapp --tail 200 | grep -i "oauth\|google"

# 6. Re-configure if needed
hostkit auth config myapp \
  --google-client-id=YOUR_NEW_ID \
  --google-client-secret=YOUR_NEW_SECRET
```

### Example 3: Email Verification Not Sending

```bash
# 1. Run diagnostics
hostkit auth diagnose myapp

# 2. Check email configuration
hostkit env get myapp-auth | grep SMTP

# 3. Check health endpoint for email warnings
curl https://myapp.hostkit.dev/auth/health | jq .warnings

# 4. View email/SMTP errors
hostkit auth logs myapp --tail 100 | grep -i "email\|smtp\|mail"

# 5. Configure SMTP if missing
hostkit env set myapp-auth \
  SMTP_HOST=smtp.example.com \
  SMTP_PORT=587 \
  SMTP_USER=your-email \
  SMTP_PASS=your-password

# 6. Restart and test
systemctl restart hostkit-myapp-auth
```

---

## Advanced Debugging

### Enable Request Logging
```bash
# Set DEBUG_REQUESTS=true to log all HTTP requests
hostkit env set myapp-auth DEBUG_REQUESTS=true
systemctl restart hostkit-myapp-auth

# View request logs
hostkit auth logs myapp --follow
# Output: → GET /auth/health
#         ✓ GET /auth/health - 200 (12.5ms)
```

### Inspect Database Directly
```bash
# Connect to auth database
psql postgresql://myapp_auth_user:PASSWORD@localhost/myapp_auth_db

# Check users table
SELECT id, email, email_verified, created_at FROM users LIMIT 10;

# Check sessions
SELECT COUNT(*) FROM sessions WHERE expires_at > NOW();

# Check OAuth accounts
SELECT user_id, provider, provider_email FROM oauth_accounts LIMIT 10;

# Check migrations
SELECT * FROM alembic_version;
```

### Trace API Requests
```bash
# With curl and verbose output
curl -v https://myapp.hostkit.dev/auth/health \
  -H "Authorization: Bearer YOUR_TOKEN"

# With tcpdump (network level)
tcpdump -i any -A 'host myapp.hostkit.dev and port 443'

# Check upstream proxy
curl -v https://myapp.hostkit.dev/auth/health \
  -H "X-Forwarded-For: debug"
```

### Check Service Resource Usage
```bash
# Memory and CPU
ps aux | grep hostkit-myapp-auth

# Open file descriptors
lsof -p <PID>

# Network connections
netstat -antp | grep 9001
ss -antp | grep 9001

# Recent system events
dmesg | tail -20
```

---

## Getting Help

### Collect Debug Information
When reporting issues, collect this information:

```bash
# Diagnosis output
hostkit auth diagnose myapp > /tmp/diagnosis.txt

# Auth service logs (last 100 lines)
hostkit auth logs myapp --lines 100 > /tmp/auth-logs.txt

# System health
hostkit auth health myapp > /tmp/health.txt

# Configuration (redacted)
hostkit auth config myapp > /tmp/config.txt

# Combine for support
tar czf debug-info.tar.gz /tmp/diagnosis.txt /tmp/auth-logs.txt /tmp/health.txt /tmp/config.txt
```

### Contact HostKit Support
- Provide debug-info.tar.gz from above
- Describe what users are experiencing
- Include timeline (when did it start?)
- Note any recent changes (new OAuth provider, email config, etc.)

---

## See Also

- [Auth System Architecture](AUTH.md)
- [OAuth Provider Setup](AUTH-PROVIDERS.md)
- [Environment Variables](ENVIRONMENT.md)
- [Deployment Guide](DEPLOYMENT-WORKFLOW.md)
