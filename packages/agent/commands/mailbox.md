# Mailbox Provisioning

You are the HostKit substrate agent provisioning a full IMAP mailbox. Walk through every step sequentially — do not skip steps, and verify each before proceeding.

## Step 1: Gather Inputs

Ask the user for:
- **Project name** (required)
- **Local part / username** — e.g. `tony` (required)
- **Custom domain** (optional, defaults to `<project>.hostkit.dev`)

Derive:
```
DOMAIN = custom_domain or "<project>.hostkit.dev"
EMAIL  = "<local_part>@<DOMAIN>"
```

## Step 2: Pre-flight Checks

### 2a — Verify the project exists

```
hostkit_state: scope="project", project="<project>"
```

If the project does not exist, STOP and tell the user.

### 2b — Check if mail is already enabled

Look at the project capabilities/services in the state response. Note whether mail is already enabled.

### 2c — Record current nginx state (CRITICAL)

Check whether a project-specific nginx config already exists:

```bash
ssh root@{{VPS_IP}} "test -f /etc/nginx/sites-enabled/hostkit-<project> && echo EXISTS || echo MISSING"
```

Save this result — you need it in Step 4.

## Step 3: Enable Mail (if needed)

If mail is not already enabled:

```
hostkit_execute: command="mail enable <project>"
```

If mail is already enabled, skip to Step 4.

## Step 4: Nginx Safeguard

**WHY THIS MATTERS:** `mail enable` can create a project-specific nginx config (`hostkit-<project>`) that **breaks the site**. The wildcard config at `hostkit-wildcard` already handles all `*.hostkit.dev` subdomains with SSL, HTTPS redirect, and service routing. A project-specific config takes precedence (exact server_name match over regex) but typically only listens on port 80 with no SSL — breaking HTTPS access.

### 4a — Check if a NEW config was created

```bash
ssh root@{{VPS_IP}} "test -f /etc/nginx/sites-enabled/hostkit-<project> && echo EXISTS || echo MISSING"
```

Compare to the result from Step 2c:
- If it was **MISSING before** and now **EXISTS** → `mail enable` created it. Proceed to 4b.
- If it **already existed** before → do not remove it. Skip to Step 5.
- If it is still **MISSING** → nothing to do. Skip to Step 5.

### 4b — Check wildcard port mappings

```bash
ssh root@{{VPS_IP}} "grep <project> /etc/nginx/hostkit-ports.conf"
```

If the project appears in the wildcard port mappings, the wildcard config already routes traffic correctly. The project-specific config is redundant and harmful.

### 4c — Remove the project-specific config

```bash
ssh root@{{VPS_IP}} "rm /etc/nginx/sites-enabled/hostkit-<project>"
```

### 4d — Reload nginx safely

```bash
ssh root@{{VPS_IP}} "nginx -t && systemctl reload nginx"
```

If `nginx -t` fails, STOP and report the error. Do not reload with a broken config.

### 4e — Verify the site is still accessible

```
hostkit_execute: command="health <project>"
```

If unhealthy, STOP and troubleshoot before continuing.

## Step 5: Create the Mailbox

```
hostkit_execute: command="mail add <project> <local_part>"
```

**Save the password** from the response — you will need it for the final output.

If the mailbox already exists, note this and continue (the password may not be returned; inform the user they need to use their existing password or reset it).

## Step 6: Create Standard IMAP Folders

Dovecot auto-subscribe may not create physical Maildir directories until first access. Create them now so mail clients see all folders immediately:

```bash
ssh root@{{VPS_IP}} "
  MAILDIR=/var/mail/vhosts/<DOMAIN>/<local_part>
  for folder in .Sent .Drafts .Trash .Junk .Archive; do
    mkdir -p \$MAILDIR/\$folder/{cur,new,tmp}
  done
  chown -R vmail:vmail \$MAILDIR
"
```

Verify the folders exist:

```bash
ssh root@{{VPS_IP}} "ls -d /var/mail/vhosts/<DOMAIN>/<local_part>/.{Sent,Drafts,Trash,Junk,Archive}"
```

## Step 7: Verify Dovecot Auto-Subscribe Config

Check that Dovecot is configured to auto-subscribe clients to standard folders:

```bash
ssh root@{{VPS_IP}} "cat /etc/dovecot/conf.d/15-mailboxes.conf"
```

Verify these mailboxes have `auto = subscribe`:
- Drafts
- Sent (or "Sent Messages")
- Trash (or "Deleted Messages")
- Junk (or "Spam")
- Archive

If any are missing `auto = subscribe`, write the correct config:

```bash
ssh root@{{VPS_IP}} "cat > /etc/dovecot/conf.d/15-mailboxes.conf << 'CONF'
namespace inbox {
  mailbox Drafts {
    auto = subscribe
    special_use = \Drafts
  }
  mailbox Sent {
    auto = subscribe
    special_use = \Sent
  }
  mailbox Trash {
    auto = subscribe
    special_use = \Trash
  }
  mailbox Junk {
    auto = subscribe
    special_use = \Junk
  }
  mailbox Archive {
    auto = subscribe
    special_use = \Archive
  }
}
CONF"
```

Then reload Dovecot:

```bash
ssh root@{{VPS_IP}} "systemctl reload dovecot"
```

## Step 8: Set Up DNS in Cloudflare

### 8a — Get Cloudflare API credentials

```bash
ssh root@{{VPS_IP}} "cat /etc/cloudflare/hostkit.ini"
```

Extract the `dns_cloudflare_api_token` value.

**Constants:**
- Zone ID: `566dd24dd0a0c2b210aecccf58eed373`
- VPS IP: `{{VPS_IP}}`

### 8b — Check existing DNS records

Query Cloudflare to avoid creating duplicates:

```
WebFetch:
  url: https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records?name=<DOMAIN>
  headers: Authorization: Bearer <token>
```

Use `curl` via Bash for API calls since WebFetch cannot set auth headers:

```bash
curl -s -X GET "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records?name=<DOMAIN>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"
```

Also check for `mail.<DOMAIN>` and `_dmarc.<DOMAIN>`:

```bash
curl -s -X GET "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records?name=mail.<DOMAIN>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"

curl -s -X GET "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records?name=_dmarc.<DOMAIN>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"
```

### 8c — Create missing records

Only create records that do not already exist. All records must be **DNS only** (proxied: false).

**MX record** — `<DOMAIN>` → `mail.hostkit.dev`, priority 10:

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MX",
    "name": "<DOMAIN>",
    "content": "mail.hostkit.dev",
    "priority": 10,
    "proxied": false
  }'
```

**A record** — `mail.<DOMAIN>` → `{{VPS_IP}}`:

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "A",
    "name": "mail.<DOMAIN>",
    "content": "{{VPS_IP}}",
    "proxied": false
  }'
```

**TXT record (DMARC)** — `_dmarc.<DOMAIN>`:

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/566dd24dd0a0c2b210aecccf58eed373/dns_records" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "TXT",
    "name": "_dmarc.<DOMAIN>",
    "content": "v=DMARC1; p=quarantine; rua=mailto:postmaster@<DOMAIN>",
    "proxied": false
  }'
```

**Records you do NOT need to create** (already covered globally):
- **SPF** — wildcard TXT for `*.hostkit.dev` already exists
- **DKIM** — wildcard signing rule `*@*.hostkit.dev` in OpenDKIM already covers this

## Step 9: Verify DNS

Run the HostKit DNS check:

```
hostkit_execute: command="mail dns-check <DOMAIN>"
```

Report any failures. Note that DNS propagation may take a few minutes — warn the user if records were just created.

## Step 10: Output Credentials

Present the final result in this exact format:

```
Mailbox: <local_part>@<DOMAIN>
Password: <password>

IMAP: mail.hostkit.dev:993 (SSL/TLS)
SMTP: mail.hostkit.dev:587 (STARTTLS)
Username: <local_part>@<DOMAIN>

Folders: Inbox, Sent, Drafts, Trash, Junk, Archive
```

If DNS records were just created, add:

```
Note: DNS records were just created. Allow a few minutes for propagation
before testing email delivery from external senders.
```

## Error Recovery

- **nginx broken after mail enable** → Restore by removing the project-specific config (Step 4) and reloading. If that fails, check `nginx -t` output.
- **Mailbox creation fails** → Check if the domain is configured in Postfix virtual domains. Run `hostkit_execute: command="mail status <project>"`.
- **Cloudflare API 403** → Token may be expired. Re-read from `/etc/cloudflare/hostkit.ini` and retry.
- **DNS check fails** → Records may need propagation time. Wait 2-3 minutes and re-check.
- **Dovecot reload fails** → Syntax error in config. Check with `doveconf -n` and fix.

## Begin

Now execute starting from Step 1. Ask the user for the required inputs if they weren't provided with the command invocation.
