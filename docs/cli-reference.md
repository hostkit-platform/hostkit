# HostKit CLI Reference

Complete reference for the HostKit CLI (`hostkit`). Installed on the VPS, called by the [MCP server](../packages/mcp-server/README.md) over SSH or directly by operators.

All commands support `--json` for machine-readable output. Commands that modify state print a confirmation panel; commands that query state print formatted tables.

> See the [root README](../README.md) for architecture overview and the [MCP server README](../packages/mcp-server/README.md) for the AI-agent tool interface.

---

## Table of Contents

- [Project Lifecycle](#project-lifecycle) — project, provision, validate, capabilities, status
- [Deployment](#deployment) — deploy, rollback, deploys, resume
- [Services](#services) — service, auth, payments, sms, voice, booking, chatbot, claude
- [Database](#database) — db, migrate, checkpoint, query
- [Environment & Secrets](#environment--secrets) — env, secrets, environment
- [Networking](#networking) — nginx, ssl, dns, ratelimit
- [Monitoring & Diagnostics](#monitoring--diagnostics) — health, diagnose, log, metrics, alert, events
- [Storage](#storage) — storage/minio, backup, r2, vector, image
- [Background Jobs](#background-jobs) — worker, cron
- [Access & Security](#access--security) — ssh, permissions, operator, limits, sandbox, exec
- [Utilities](#utilities) — git, docs, mail, redis, autopause

---

## Project Lifecycle

### project

Manage HostKit projects. Each project is an isolated environment with its own Linux user, home directory, systemd service, and assigned port.

```bash
hostkit project create <name> [--python|--node|--nextjs|--static] [--with-db] [--with-auth] ...
hostkit project list
hostkit project info <name>
hostkit project start <name>
hostkit project stop <name>
hostkit project restart <name>
hostkit project delete <name> --force
hostkit project regenerate-sudoers [<name>|--all]
```

#### create

| Flag | Description |
|------|-------------|
| `--python` | Python runtime (default) |
| `--node` | Node.js runtime |
| `--nextjs` | Next.js runtime (standalone output) |
| `--static` | Static site (Nginx serves directly) |
| `-d, --description TEXT` | Project description |
| `--start-cmd TEXT` | Custom start command (overrides runtime default) |
| `--with-db` | Create PostgreSQL database |
| `--with-storage` | Create MinIO storage bucket |
| `--with-auth` | Enable authentication service |
| `--with-payments` | Enable Stripe payments service |
| `--with-sms` | Enable SMS service |
| `--with-mail` | Enable mail service |
| `--with-booking` | Enable booking/scheduling service |
| `--with-chatbot` | Enable AI chatbot service |
| `--with-r2` | Enable Cloudflare R2 storage |
| `--with-vector` | Enable vector/RAG service |
| `--google-client-id TEXT` | Google OAuth client ID (requires `--with-auth`) |
| `--google-client-secret TEXT` | Google OAuth client secret (requires `--with-auth`) |

```bash
# Python project with database and auth
hostkit project create myapp --python --with-db --with-auth

# Next.js project with storage
hostkit project create frontend --nextjs --with-storage

# Static site
hostkit project create landing --static
```

#### delete

Stops the service, removes the Linux user and home directory, and deletes the PostgreSQL database if one exists. Requires `--force`.

```bash
hostkit project delete myapp --force
```

---

### provision

Provision a complete project with all supporting services in one command. Creates project, database, auth, domain, SSL, and initial deployment.

```bash
hostkit provision <name> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--runtime TEXT` | Runtime (python, node, nextjs, static). Default: python |
| `--with-db` | Create PostgreSQL database |
| `--with-auth` | Enable authentication service |
| `--with-secrets` | Inject secrets from vault into .env |
| `--ssh-key TEXT` | SSH public key for project user (repeatable) |
| `--github-user TEXT` | GitHub username to fetch SSH keys from (repeatable) |
| `--domain TEXT` | Domain name to configure in Nginx |
| `--dev-domain` | Use nip.io development domain |
| `--ssl` | Provision SSL certificate (requires `--domain`) |
| `--ssl-email TEXT` | Admin email for Let's Encrypt registration |
| `--source PATH` | Source directory to deploy |
| `--no-install` | Skip dependency installation |
| `--no-start` | Don't start service after provisioning |

```bash
hostkit provision myapp --runtime python --with-db --with-auth --domain myapp.example.com --ssl
```

---

### validate

Run pre-flight checks on a project before deployment. Checks entrypoint, dependencies, environment variables, database connectivity, port conflicts, and service status.

```bash
hostkit validate <project>
```

| Flag | Description |
|------|-------------|
| `--fix` | Attempt to auto-fix common issues (coming soon) |

```bash
hostkit validate myapp
```

---

### capabilities

Expose HostKit capabilities for AI agents. Shows available commands, services, runtimes, and project-scoped details.

```bash
hostkit capabilities [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-p, --project TEXT` | Scope to a project's enabled services |
| `--commands` | Show only commands |
| `--services` | Show only services |
| `--runtimes` | Show only runtimes |
| `--version-only` | Show only version |

```bash
hostkit capabilities
hostkit capabilities --project myapp
```

---

### status

Show system status overview or project details. Without arguments, shows overall system health. With a project name, shows that project's detailed status.

```bash
hostkit status [<project>] [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-r, --resources` | Show detailed resource metrics (CPU, memory, disk, database) |
| `--vps` | Show VPS-level status (resources, limits, health) |
| `-w, --watch INT` | Continuously monitor resources every N seconds (requires `--resources`) |

```bash
hostkit status                    # System overview
hostkit status myapp              # Project details
hostkit status myapp --resources  # Project resource metrics
hostkit status --vps              # VPS health
```

---

## Deployment

### deploy

Deploy code to a project. Syncs source to the project's app directory, optionally builds and installs dependencies, and restarts the service.

```bash
hostkit deploy <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-s, --source PATH` | Source directory to deploy (default: `./app`) |
| `-g, --git TEXT` | Git repository URL |
| `-b, --branch TEXT` | Git branch to checkout |
| `-t, --tag TEXT` | Git tag to checkout (overrides `--branch`) |
| `-c, --commit TEXT` | Git commit to checkout (overrides `--branch` and `--tag`) |
| `-e, --env TEXT` | Environment to deploy to (e.g., staging, production) |
| `-i, --install` | Install dependencies after sync |
| `--build` | Build the app (runs `npm install && npm run build` for Node/Next.js) |
| `--with-secrets` | Inject secrets from vault into .env |
| `--restart/--no-restart` | Restart service after deploy (default: yes) |
| `--override-ratelimit` | Bypass rate limit checks |

```bash
hostkit deploy myapp --source ./dist --install
hostkit deploy myapp --git https://github.com/user/repo.git --branch main --install --build
hostkit deploy myapp --install --build --with-secrets
```

---

### rollback

Roll back a project to a previous release. Without options, rolls back code only. Use `--full` to also restore database and environment variables.

```bash
hostkit rollback <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--list` | List available releases |
| `--to TEXT` | Roll back to a specific release by name |
| `--full` | Full rollback: code + database + environment variables |
| `--dry-run` | Preview without making changes |

```bash
hostkit rollback myapp                # Roll back to previous release
hostkit rollback myapp --list         # List available releases
hostkit rollback myapp --to release-3 # Roll back to specific release
hostkit rollback myapp --full         # Code + database + env
```

---

### deploys

View deployment history for a project.

```bash
hostkit deploys <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-s, --since TEXT` | Show deploys since time (1h, 24h, 7d, 30m) |
| `-n, --limit INT` | Maximum entries to show (default: 20) |

```bash
hostkit deploys myapp
hostkit deploys myapp --since 24h
```

---

### resume

Resume a paused project. When a project is auto-paused due to repeated failures, use this to resume deployments.

```bash
hostkit resume <project> [--force]
```

| Flag | Description |
|------|-------------|
| `--force` | Also reset failure history to prevent immediate re-pause |

```bash
hostkit resume myapp
hostkit resume myapp --force
```

---

## Services

### service

Manage systemd services for projects.

```bash
hostkit service list [--project <name>]
hostkit service status <name>
hostkit service start <name>
hostkit service stop <name>
hostkit service restart <name>
hostkit service enable <name>
hostkit service disable <name>
hostkit service logs <name> [-n 100] [-f] [--systemd] [--error]
hostkit service create-worker <project> [--app-module app] [--concurrency 2]
hostkit service delete-worker <project> [--force]
```

#### logs

| Flag | Description |
|------|-------------|
| `-n, --lines INT` | Number of lines to show (default: 100) |
| `-f, --follow` | Follow log output |
| `--systemd` | Show journalctl/systemd logs instead of app logs |
| `--error` | Show only error.log (stderr) |

```bash
hostkit service logs myapp -n 50 -f
hostkit service logs myapp --systemd
```

---

### auth

Manage per-project authentication services. Supports OAuth (Google, Apple), email/password, magic links, and anonymous sessions.

```bash
hostkit auth enable <project> [OPTIONS]
hostkit auth disable <project> --force
hostkit auth config <project> [OPTIONS]
hostkit auth status [<project>]
hostkit auth users <project> [-n 50] [--verified-only] [--provider email|google|apple|anonymous]
hostkit auth logs <project> [-n 100] [-f]
hostkit auth sync <project>
hostkit auth export-key <project> [--env-format] [--update-env]
```

#### enable

| Flag | Description |
|------|-------------|
| `--google-client-id TEXT` | Google OAuth client ID (native iOS/Android) |
| `--google-web-client-id TEXT` | Google OAuth web client ID |
| `--google-client-secret TEXT` | Google OAuth client secret |
| `--apple-client-id TEXT` | Apple Sign-In client ID |
| `--apple-team-id TEXT` | Apple Developer Team ID |
| `--apple-key-id TEXT` | Apple Sign-In key ID |
| `--no-email` | Disable email/password auth |
| `--no-magic-link` | Disable magic link auth |
| `--no-anonymous` | Disable anonymous sessions |

#### config

All `enable` flags plus:

| Flag | Description |
|------|-------------|
| `--set KEY=VALUE` | Set config value (repeatable) |
| `--base-url TEXT` | Base URL for OAuth callbacks |
| `--email/--no-email` | Toggle email/password auth |
| `--magic-link/--no-magic-link` | Toggle magic links |
| `--anonymous/--no-anonymous` | Toggle anonymous sessions |
| `--from-secrets` | Read OAuth credentials from secrets vault |
| `--from-platform` | Read OAuth credentials from platform config |
| `--no-restart` | Don't restart auth service after changes |

```bash
hostkit auth enable myapp --google-client-id xxx --google-client-secret yyy
hostkit auth config myapp --magic-link --anonymous
hostkit auth export-key myapp --update-env
```

---

### payments

Manage per-project payment services via Stripe Connect Express.

```bash
hostkit payments enable <project>
hostkit payments disable <project> --force
hostkit payments status <project>
hostkit payments logs <project> [-n 100] [-f]
```

```bash
hostkit payments enable myapp   # Returns Stripe onboarding URL
hostkit payments status myapp
```

---

### sms

Manage per-project SMS services via Twilio. Supports transactional messaging, templates, consent tracking, and conversational AI.

```bash
hostkit sms enable <project> [--phone-number TEXT] [--ai] [--agent TEXT]
hostkit sms disable <project> --force
hostkit sms status <project>
hostkit sms send <project> --to +1234567890 [--template NAME|--body TEXT] [--vars JSON]
hostkit sms template <action> <project> [<name>] [--body TEXT] [--category transactional|marketing|otp]
hostkit sms logs <project> [-n 100] [-f]
```

```bash
hostkit sms enable myapp --ai
hostkit sms send myapp --to +15551234567 --template welcome --vars '{"name": "Alice"}'
hostkit sms template create myapp welcome --body "Hi {{name}}, welcome!" --category transactional
```

---

### voice

Manage voice calling service. AI-powered phone calls via Twilio Media Streams with real-time streaming (Deepgram STT, Cartesia TTS, OpenAI LLM).

```bash
hostkit voice enable <project>
hostkit voice disable <project> --force
hostkit voice status <project>
hostkit voice agent <create|list> <project> [<name>]
hostkit voice call initiate <project> <agent> --to +1234567890 [--context JSON]
hostkit voice logs <project> [-n 100] [-f]
```

```bash
hostkit voice enable myapp
hostkit voice agent create myapp receptionist
hostkit voice call initiate myapp receptionist --to +15551234567
```

---

### booking

Manage per-project booking/scheduling services. Appointment scheduling with provider pooling, room management, and automated reminders.

```bash
hostkit booking enable <project>
hostkit booking disable <project> --force
hostkit booking status <project>
hostkit booking seed <project> [--providers 3] [--services 5]
hostkit booking upgrade <project> [--dry-run] [--force]
hostkit booking logs <project> [-n 100] [-f]
```

```bash
hostkit booking enable myapp
hostkit booking seed myapp --providers 5 --services 10
```

---

### chatbot

Manage per-project AI chatbot services with embeddable widgets, conversation history, and SSE streaming.

```bash
hostkit chatbot enable <project>
hostkit chatbot disable <project> --force
hostkit chatbot status <project> [--show-key]
hostkit chatbot config <project> [OPTIONS]
hostkit chatbot stats <project>
hostkit chatbot logs <project> [-n 100] [-f]
```

#### config

| Flag | Description |
|------|-------------|
| `--name TEXT` | Chatbot display name |
| `--system-prompt TEXT` | System prompt for the AI |
| `--suggested-questions JSON` | Suggested questions (JSON array) |
| `--position POSITION` | Widget position (bottom-right, bottom-left, top-right, top-left) |
| `--primary-color HEX` | Primary color (e.g., #6366f1) |
| `--theme THEME` | Widget theme (light, dark) |
| `--cta-text TEXT` | Call-to-action button text |
| `--cta-url URL` | Call-to-action button URL |
| `--cta-after INT` | Show CTA after N messages |
| `--model TEXT` | LLM model to use |

```bash
hostkit chatbot enable myapp
hostkit chatbot config myapp --name "Support Bot" --model claude-sonnet-4-20250514 --theme dark
```

---

### claude

Claude AI integration service. Enable AI capabilities for projects using the VPS owner's Anthropic subscription.

```bash
hostkit claude setup --api-key <key> [--force]
hostkit claude status
hostkit claude enable <project>
hostkit claude disable <project> [--force]
hostkit claude grant <project> --tools <tools>
hostkit claude revoke <project> --tools <tools>
hostkit claude tools <project>
hostkit claude usage [<project>] [--detailed] [--all-projects]
```

**Tool tiers:**

| Tier | Tools |
|------|-------|
| Read-only | logs, health, db:read, env:read, vector:search |
| State change | db:write, env:write, service, cache:flush |
| High-risk | deploy, rollback, migrate |

```bash
hostkit claude enable myapp
hostkit claude grant myapp --tools logs,health,db:read
hostkit claude usage myapp --detailed
```

---

## Database

### db

Manage PostgreSQL databases for projects.

```bash
hostkit db create <project>
hostkit db delete <project> --force
hostkit db list
hostkit db info <project>
hostkit db backup <project>
hostkit db restore <project> <backup_path>
hostkit db shell <project> [-c "SQL"]
hostkit db enable-extension <project> <extension>
hostkit db extensions <project>
hostkit db migrate <project> [-f file.sql | -d migrations/]
```

**Supported extensions:** vector, postgis, pg_trgm, uuid-ossp, pgcrypto, hstore, citext, unaccent, fuzzystrmatch, tablefunc

```bash
hostkit db create myapp
hostkit db shell myapp -c "SELECT * FROM users LIMIT 5"
hostkit db enable-extension myapp vector
hostkit db migrate myapp --dir ./migrations/
```

---

### migrate

Run database migrations with automatic framework detection. Creates a checkpoint before running (restorable if migrations fail).

```bash
hostkit migrate <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--django` | Force Django migrations |
| `--alembic` | Force Alembic migrations |
| `--prisma` | Force Prisma migrations |
| `--cmd TEXT` | Custom migration command |
| `--dry-run` | Show command without executing |
| `--checkpoint/--no-checkpoint` | Create checkpoint before migrating (default: yes) |

```bash
hostkit migrate myapp                  # Auto-detect framework
hostkit migrate myapp --prisma         # Force Prisma
hostkit migrate myapp --dry-run        # Preview only
hostkit migrate myapp --no-checkpoint  # Skip safety checkpoint
```

---

### checkpoint

Create and manage point-in-time database snapshots for safe rollbacks.

```bash
hostkit checkpoint create <project> [-l "label"]
hostkit checkpoint list <project> [-n 20] [--type manual|pre_migration|pre_restore|auto]
hostkit checkpoint info <project> <id>
hostkit checkpoint restore <project> <id> [--force] [--no-safety]
hostkit checkpoint delete <project> <id> --force
hostkit checkpoint latest <project> [--type TYPE]
hostkit checkpoint cleanup
```

**Retention policy:**

| Type | Retention |
|------|-----------|
| manual | Never expires |
| pre_migration | 30 days |
| pre_restore | 7 days |
| auto | 7 days |

```bash
hostkit checkpoint create myapp --label "before schema change"
hostkit checkpoint restore myapp 5 --force
hostkit checkpoint cleanup
```

---

### query

Query HostKit documentation with natural language. Designed for AI agents to quickly find commands and configuration details.

```bash
hostkit query "<question>" [-n 5] [--raw]
```

| Flag | Description |
|------|-------------|
| `-n, --limit INT` | Number of documentation chunks to retrieve (default: 5) |
| `--raw` | Return raw chunks without LLM processing |

```bash
hostkit query "how do I enable payments"
hostkit query "what environment variables does auth set" --raw
```

---

## Environment & Secrets

### env

Manage environment variables for projects. Sensitive values are redacted by default.

```bash
hostkit env list <project> [--show-secrets]
hostkit env get <project> <key>
hostkit env set <project> KEY=VALUE [--restart]
hostkit env unset <project> <key> [--restart]
hostkit env import <project> <file> [--force]
hostkit env sync <project> <file>
```

`import` replaces all existing variables. `sync` only adds new variables without overwriting existing ones.

```bash
hostkit env set myapp DEBUG=true --restart
hostkit env sync myapp ./defaults.env
```

---

### secrets

Manage encrypted secrets (AES-256-GCM at rest). Values never appear in logs or command output.

```bash
hostkit secrets init [--force]
hostkit secrets list <project>
hostkit secrets set <project> <key> [--stdin] [--provider TEXT] [--description TEXT]
hostkit secrets delete <project> <key> [--force]
hostkit secrets import <project> [<file>] [--stdin] [--no-overwrite]
hostkit secrets define <project> [<key>] [--from .env.example] [--required|--optional]
hostkit secrets undefine <project> <key> [--delete-value]
hostkit secrets verify <project>
hostkit secrets portal <project> [--expires 24h] [--revoke] [--url-only]
hostkit secrets clear <project> [--delete-values]
hostkit secrets audit <project> [--limit 50]
```

```bash
hostkit secrets define myapp --from .env.example
hostkit secrets portal myapp --expires 1h          # Generate magic link
hostkit secrets verify myapp                        # Check all required secrets are set
echo "sk_live_xxx" | hostkit secrets set myapp STRIPE_API_KEY --stdin
```

---

### environment

Manage project environments (staging, production, etc.). Each environment is a fully isolated instance with its own Linux user, port, and service. Maximum 5 environments per project.

```bash
hostkit environment create <project> <env> [--with-db|--share-db]
hostkit environment list [<project>]
hostkit environment info <project> <env>
hostkit environment delete <project> <env> --force
hostkit environment promote <project> <source> <target> [--with-db] [--dry-run]
hostkit environment start <project> <env>
hostkit environment stop <project> <env>
hostkit environment restart <project> <env>
```

Environment variables are not copied during promotion (they differ by design).

```bash
hostkit environment create myapp staging --with-db
hostkit environment promote myapp staging production --dry-run
```

---

## Networking

### nginx

Manage Nginx reverse proxy configurations for projects.

```bash
hostkit nginx list
hostkit nginx add <project> <domain> [--skip-dns]
hostkit nginx remove <project> <domain>
hostkit nginx info <project>
hostkit nginx test
hostkit nginx reload
hostkit nginx update-wildcard
```

```bash
hostkit nginx add myapp myapp.example.com
hostkit nginx test && hostkit nginx reload
```

---

### ssl

Manage Let's Encrypt SSL certificates.

```bash
hostkit ssl list
hostkit ssl status <domain>
hostkit ssl provision <domain> [--email TEXT]
hostkit ssl renew [--all|--domain TEXT] [--force]
hostkit ssl auto-renewal
hostkit ssl enable-auto-renewal
hostkit ssl test-renewal [--domain TEXT]
```

```bash
hostkit ssl provision myapp.example.com --email admin@example.com
hostkit ssl renew --all
```

---

### dns

Manage DNS records via Cloudflare and nip.io.

```bash
hostkit dns config [--token TEXT] [--zone TEXT] [--show]
hostkit dns list [--type A|CNAME|MX|...]
hostkit dns add <name> [--content IP] [--type A] [--ttl 300] [--proxied]
hostkit dns remove <name> [--type A] [-y]
hostkit dns dev <project>
hostkit dns ip [--refresh]
hostkit dns setup <project> [-s subdomain]
hostkit dns check <domain>
```

```bash
hostkit dns config --token cf_xxx --zone example.com
hostkit dns add myapp                      # A record pointing to VPS IP
hostkit dns setup myapp                    # DNS + Nginx in one step
hostkit dns check myapp.example.com
```

---

### ratelimit

Configure deployment rate limits to prevent AI agents from entering deploy-crash-deploy loops.

**Defaults:** 10 deploys per 60-minute window, 5-minute cooldown after 3 consecutive failures.

```bash
hostkit ratelimit show <project>
hostkit ratelimit set <project> [--max INT] [--window DURATION] [--cooldown DURATION] [--failure-limit INT]
hostkit ratelimit enable <project> [--max 10] [--window 1h]
hostkit ratelimit disable <project>
hostkit ratelimit reset <project> [--history] [--force]
```

Duration formats: `30m`, `1h`, `2d`, or plain number (minutes).

```bash
hostkit ratelimit disable myapp          # Unlimited deploys
hostkit ratelimit set myapp --max 20 --window 2h
hostkit ratelimit reset myapp --history --force
```

---

## Monitoring & Diagnostics

### health

Check the health of a project. Performs comprehensive checks including process status, HTTP endpoint, database connectivity, and auth service status.

```bash
hostkit health <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-e, --endpoint PATH` | HTTP endpoint to check (default: `/health`) |
| `-w, --watch INT` | Continuously monitor at N second intervals |
| `--expect TEXT` | Expected content in response body |
| `-t, --timeout INT` | HTTP timeout in seconds (default: 10) |
| `-v, --verbose` | Show detailed output including response body |
| `-a, --alert-on-failure` | Send alerts to configured channels on failure |

```bash
hostkit health myapp
hostkit health myapp --watch 30 --alert-on-failure
hostkit health myapp --endpoint /api/health --expect "ok"
```

---

### diagnose

Diagnose project failures and suggest fixes. Analyzes deployment history, service logs, and system status to detect common failure patterns.

**Patterns detected:** deploy-crash loops, missing modules, port conflicts, database failures, OOM, permission errors, syntax errors, file not found.

```bash
hostkit diagnose <project> [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-v, --verbose` | Include raw log excerpts |
| `--check-db` | Also test database connectivity |
| `-q, --quick` | Quick status check only (no log analysis) |
| `--run-test` | Run entrypoint directly and capture startup output |
| `--timeout INT` | Timeout for `--run-test` in seconds (default: 10) |
| `--no-restart` | Don't restart service after `--run-test` |

```bash
hostkit diagnose myapp
hostkit diagnose myapp --run-test --timeout 30
hostkit diagnose myapp --check-db --verbose
```

---

### log

View, search, and export logs from projects.

```bash
hostkit log show <project> [-n 100] [-f] [-l ERROR] [-s app.log] [--since 1h] [--until now]
hostkit log search <project> <pattern> [-c 2] [-f file] [-i]
hostkit log export <project> <output> [--since TEXT] [--until TEXT] [--compress] [--include-journal]
hostkit log files <project>
hostkit log stats <project>
hostkit log clear <project> [--older-than DAYS] --force
hostkit log setup <project>
hostkit log setup-rotation
```

#### show

| Flag | Description |
|------|-------------|
| `-n, --lines INT` | Number of lines (default: 100) |
| `-f, --follow` | Follow log output |
| `-l, --level LEVEL` | Minimum level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `-s, --source SOURCE` | Log sources to include (app.log, error.log, journal; repeatable) |
| `--since TEXT` | Show logs since time (e.g., 1h, 24h, 7d, 2025-12-15) |
| `--until TEXT` | Show logs until time |

#### search

| Flag | Description |
|------|-------------|
| `-c, --context INT` | Lines of context around matches (default: 2) |
| `-f, --file FILE` | Specific log files to search (repeatable) |
| `-i, --ignore-case` | Case insensitive (default: yes) |
| `--case-sensitive` | Case sensitive search |

```bash
hostkit log show myapp --since 1h --level ERROR
hostkit log search myapp "Exception.*timeout" --context 5
hostkit log export myapp ./logs.txt.gz --since "1 day ago"
```

---

### metrics

Collect and view project metrics. Track CPU, memory, disk, requests, and error rates over time.

```bash
hostkit metrics show <project> [--since 1h] [-n 10]
hostkit metrics history <project> [--since 1h] [-n 50]
hostkit metrics enable <project>
hostkit metrics disable <project>
hostkit metrics config <project> [--show] [--interval INT] [--retention INT] ...
hostkit metrics collect [<project>|--all]
hostkit metrics setup-timer
hostkit metrics cleanup [<project>|--all] [--force]
```

#### config

| Flag | Description |
|------|-------------|
| `--show` | Show current configuration |
| `--interval INT` | Collection interval in seconds |
| `--retention INT` | Retention period in days |
| `--cpu-warning FLOAT` | CPU warning threshold (%) |
| `--cpu-critical FLOAT` | CPU critical threshold (%) |
| `--memory-warning FLOAT` | Memory warning threshold (%) |
| `--memory-critical FLOAT` | Memory critical threshold (%) |
| `--error-rate-warning FLOAT` | Error rate warning threshold (%) |
| `--error-rate-critical FLOAT` | Error rate critical threshold (%) |

```bash
hostkit metrics enable myapp
hostkit metrics config myapp --interval 30 --cpu-warning 75 --cpu-critical 90
hostkit metrics show myapp --since 24h
```

---

### alert

Configure notifications for deployment, migration, and health check events. Supports webhook, email, and Slack channels.

```bash
hostkit alert channel add <project> <webhook|email|slack> [OPTIONS]
hostkit alert channel list <project>
hostkit alert channel remove <project> <name> [--force]
hostkit alert channel test <project> <name>
hostkit alert channel enable <project> <name>
hostkit alert channel disable <project> <name>
hostkit alert history <project> [-n 20] [--type deploy|migrate|health|test]
hostkit alert mute <project> [-d 1h] [-c channel]
hostkit alert unmute <project> [-c channel]
```

#### channel add

| Channel | Flags |
|---------|-------|
| webhook | `--url URL`, `--secret TEXT` (HMAC signing) |
| email | `--to EMAIL` (repeatable), `--from TEXT`, `--subject-prefix TEXT` |
| slack | `--webhook-url URL` |

All channels: `--name TEXT` (default: "default")

```bash
hostkit alert channel add myapp webhook --url https://example.com/hook --secret s3cret
hostkit alert channel add myapp slack --webhook-url https://hooks.slack.com/services/...
hostkit alert mute myapp --duration 2h
```

---

### events

View structured records of HostKit operations (deploys, health checks, migrations, etc.).

```bash
hostkit events list <project> [-c category] [-l level] [--since 1h] [--until TEXT] [-n 50]
hostkit events show <event_id>
hostkit events stats <project> [--since 24h]
hostkit events cleanup [--older-than 30] [--force]
```

Categories: deploy, health, auth, migrate, and more. Comma-separate for multiple.

```bash
hostkit events list myapp --category deploy,health --since 24h
hostkit events stats myapp --since 7d
```

---

## Storage

### storage / minio

Manage MinIO S3-compatible object storage. Each project gets a bucket (`hostkit-{project}`) with isolated credentials. Commands are available under both `hostkit storage` and `hostkit minio`.

```bash
hostkit storage enable <project> [--public]
hostkit storage disable <project> --force
hostkit storage status
hostkit storage setup [--root-password TEXT]
hostkit storage list
hostkit storage create-bucket <bucket> [<project>]
hostkit storage delete-bucket <bucket> --force
hostkit storage credentials <project> [--regenerate] [--env-format]
hostkit storage usage
hostkit storage policy <bucket> [<policy>] [--prefix TEXT] [--show]
hostkit storage proxy <domain> [--ssl]
```

Policies: private, public-read, public-write, public-read-write.

```bash
hostkit storage enable myapp --public
hostkit storage credentials myapp --env-format
hostkit storage policy hostkit-myapp public-read
```

---

### backup

Create, list, restore, and manage backups. Supports local storage and Cloudflare R2 cloud sync.

**Backup types:** full (db + files + env), db, files, credentials.

**Retention:** 7 daily + 4 weekly (local), 30 daily + 12 weekly (R2).

```bash
hostkit backup create <project> [--type full|db|files|credentials] [--full] [--r2]
hostkit backup list [<project>] [--all] [--r2]
hostkit backup restore <project> <backup_id> [--db] [--files] [--env] [--from-r2] [--force]
hostkit backup verify <backup_id>
hostkit backup delete <backup_id> [--force]
hostkit backup rotate [<project>|--all]
hostkit backup export <backup_id> <destination>
hostkit backup stats [<project>]
hostkit backup credentials <project>
hostkit backup setup-timer [--time HH:MM] [--r2]
hostkit backup run-all [--type full|db|files] [--rotate] [--r2]
hostkit backup r2 sync <backup_id>
hostkit backup r2 list [<project>]
hostkit backup r2 rotate [<project>|--all]
hostkit backup r2 download <backup_id> [<destination>]
hostkit backup r2 status
```

```bash
hostkit backup create myapp --full --r2
hostkit backup restore myapp bk_20250101_020000 --force
hostkit backup setup-timer --time 02:00 --r2
```

---

### r2

Manage Cloudflare R2 object storage. S3-compatible with zero egress fees. Each project gets bucket `hostkit-{project}`.

```bash
hostkit r2 enable <project>
hostkit r2 disable <project> --force
hostkit r2 status <project>
hostkit r2 upload <project> <local_path> <remote_key> [--content-type TEXT]
hostkit r2 download <project> <remote_key> <local_path>
hostkit r2 list <project> [--prefix TEXT] [--max-keys INT]
hostkit r2 delete <project> <key> --force
hostkit r2 presign <project> <key> [--expires 3600] [--method GET|PUT]
hostkit r2 usage
hostkit r2 credentials <project> [--env-format]
```

```bash
hostkit r2 enable myapp
hostkit r2 upload myapp ./photo.jpg uploads/photo.jpg
hostkit r2 presign myapp uploads/photo.jpg --expires 3600 --method GET
```

---

### vector

Vector embedding and semantic search service. Document ingestion, chunking, embeddings, and similarity search.

```bash
hostkit vector setup [--force]
hostkit vector status
hostkit vector enable <project>
hostkit vector disable <project> --force
hostkit vector key <project> [--regenerate]
hostkit vector collections <project>
hostkit vector create-collection <project> <name> [-d description]
hostkit vector delete-collection <project> <name> --force
hostkit vector collection-info <project> <collection>
hostkit vector ingest <project> <collection> <source> [--url] [--stdin] [--name TEXT] [--wait]
hostkit vector search <project> <collection> "<query>" [-n 5] [-t 0.0]
hostkit vector jobs <project> [--status queued|processing|completed|failed]
hostkit vector job <project> <job_id>
hostkit vector usage <project>
```

```bash
hostkit vector enable myapp
hostkit vector create-collection myapp docs -d "Product documentation"
hostkit vector ingest myapp docs https://example.com/docs --url --wait
hostkit vector search myapp docs "how to reset password" -n 3
```

---

### image

AI image generation using Black Forest Labs Flux models.

**Models:** flux-1.1-pro ($0.04/image, 256-1440px), flux-1.1-pro-ultra ($0.06/image, aspect ratio).

**Rate limits:** 100/hour, 500/day per project.

```bash
hostkit image generate <project> "<prompt>" [-m model] [-w width] [-h height] [-a ratio]
hostkit image models
hostkit image usage <project>
hostkit image history <project> [-n 10]
hostkit image config [--set-key KEY]
```

Valid aspect ratios (ultra model): 21:9, 16:9, 3:2, 4:3, 1:1, 3:4, 2:3, 9:16, 9:21.

```bash
hostkit image generate myapp "A sunset over mountains" -w 1024 -h 768
hostkit image generate myapp "Product photo" -m flux-1.1-pro-ultra -a 16:9
```

---

## Background Jobs

### worker

Manage Celery background workers. Workers run as systemd services.

**Requirements:** Redis running, Celery installed in project virtualenv.

```bash
hostkit worker add <project> [-n name] [-c 2] [-q queues] [-A app] [-l info]
hostkit worker remove <project> [-n name] [-f]
hostkit worker list <project>
hostkit worker status <project>
hostkit worker start <project> [-n name]
hostkit worker stop <project> [-n name]
hostkit worker restart <project> [-n name]
hostkit worker scale <project> <concurrency> [-n name]
hostkit worker logs <project> [-n name] [-l 50] [-f]
hostkit worker beat enable <project> [-A app] [-l info]
hostkit worker beat disable <project>
hostkit worker beat status <project>
hostkit worker beat logs <project> [-l 50] [-f]
```

```bash
hostkit worker add myapp --concurrency 4 --queues emails,notifications
hostkit worker scale myapp 8 --name email-worker
hostkit worker beat enable myapp
```

---

### cron

Manage scheduled tasks using systemd timers.

**Schedule formats:** cron expressions (`"0 3 * * *"`), shortcuts (`@daily`, `@hourly`, `@weekly`), systemd OnCalendar (`"*-*-* 03:00:00"`).

```bash
hostkit cron add <project> <name> <schedule> <command> [-d description]
hostkit cron list <project>
hostkit cron remove <project> <name> [-f]
hostkit cron run <project> <name>
hostkit cron logs <project> <name> [-n 50] [-f]
hostkit cron enable <project> <name>
hostkit cron disable <project> <name>
hostkit cron info <project> <name>
```

```bash
hostkit cron add myapp cleanup "0 3 * * *" "python manage.py cleanup" -d "Daily cleanup"
hostkit cron add myapp sync @hourly "python sync_data.py"
hostkit cron run myapp cleanup    # Run immediately
```

---

## Access & Security

### ssh

Manage SSH access for projects.

```bash
hostkit ssh add-key <project> [KEY] [--github USER] [--file PATH]
hostkit ssh remove-key <project> <fingerprint>
hostkit ssh list-keys <project>
hostkit ssh sessions <project>
hostkit ssh kick <project> [<session_id>|--all]
hostkit ssh enable <project>
hostkit ssh disable <project> [--force]
hostkit ssh status [<project>]
```

```bash
hostkit ssh add-key myapp --github octocat
hostkit ssh kick myapp --all
```

---

### permissions

Manage sudoers and access control. Root only.

```bash
hostkit permissions gaps
hostkit permissions show <project>
hostkit permissions sync [<project>|--all] [--dry-run]
hostkit permissions verify <project> "<command>"
```

```bash
hostkit permissions sync --all
hostkit permissions verify myapp "deploy myapp"
```

---

### operator

Manage HostKit operators (AI agent users). Root only.

```bash
hostkit operator setup [-u ai-operator]
hostkit operator add-key [KEY] [-u user] [--github USER] [--file PATH]
hostkit operator test [-u ai-operator]
hostkit operator revoke [-u user] [-f]
hostkit operator list
hostkit operator set-project-key [KEY] [--file PATH] [--github USER] [--clear]
hostkit operator show-project-keys
hostkit operator sync-project-keys
```

```bash
hostkit operator setup
hostkit operator add-key --github myuser
hostkit operator set-project-key --github myuser   # Auto-add to new projects
hostkit operator sync-project-keys                  # Retroactively add to existing
```

---

### limits

Configure CPU, memory, and disk limits for projects using Linux cgroups via systemd.

**Defaults:** 100% CPU (1 core), 512MB memory (hard), 384MB memory (soft), 100 tasks, 2048MB disk.

```bash
hostkit limits show <project>
hostkit limits set <project> [OPTIONS]
hostkit limits reset <project>
hostkit limits apply <project>
hostkit limits disk <project>
```

| Flag | Description |
|------|-------------|
| `--cpu INT` | CPU quota as percentage (100 = 1 core) |
| `--memory SIZE` | Hard memory limit (e.g., 256M, 1G) |
| `--memory-high SIZE` | Soft memory limit / throttle threshold |
| `--tasks INT` | Max processes/threads |
| `--disk SIZE` | Disk quota (e.g., 1G, 2048M) |
| `--enabled/--disabled` | Enable or disable limits |
| `--unlimited` | Clear all limits |
| `--apply/--no-apply` | Apply to running service (default: yes) |

```bash
hostkit limits set myapp --cpu 200 --memory 1G --disk 5G
hostkit limits set myapp --unlimited
```

---

### sandbox

Manage temporary, isolated project clones for safe experimentation. Maximum 3 per project, auto-expire after 24h by default. Root only.

```bash
hostkit sandbox create <project> [--ttl 24h] [--no-db]
hostkit sandbox list [<project>] [--all]
hostkit sandbox info <sandbox_name>
hostkit sandbox delete <sandbox_name> --force
hostkit sandbox promote <sandbox_name> [--dry-run]
hostkit sandbox extend <sandbox_name> [--hours 24]
hostkit sandbox cleanup
```

```bash
hostkit sandbox create myapp --ttl 48h
hostkit sandbox promote myapp-sandbox-a3f9 --dry-run
hostkit sandbox cleanup    # Delete all expired
```

---

### exec

Execute a command in a project's context (as the project user, in the app directory, with .env loaded).

```bash
hostkit exec <project> <command...> [-w workdir] [--no-env-file] [-t 300]
```

| Flag | Description |
|------|-------------|
| `-w, --workdir PATH` | Working directory (default: project's app directory) |
| `--env-file/--no-env-file` | Source project's .env file (default: yes) |
| `-t, --timeout INT` | Command timeout in seconds (default: 300) |

```bash
hostkit exec myapp npx prisma migrate deploy
hostkit exec myapp python scripts/cleanup.py
hostkit exec myapp -- node -e "console.log('hello')"
```

---

## Utilities

### git

Configure git repository settings for deploy-from-git workflows.

```bash
hostkit git config <project> [--repo URL] [--branch TEXT] [--ssh-key PATH] [--show] [--clear]
hostkit git cache [--list] [--clear PROJECT]
```

```bash
hostkit git config myapp --repo https://github.com/user/repo.git --branch main
hostkit git config myapp --show
```

---

### docs

Manage the HostKit documentation search index.

```bash
hostkit docs index [--force]
hostkit docs status
```

```bash
hostkit docs index --force    # Rebuild search index
```

---

### mail

Manage mail server (Postfix + Dovecot) and per-project mailboxes. Supports DKIM signing and generates required DNS records.

#### Global commands

```bash
hostkit mail status
hostkit mail setup --hostname <mail.example.com>
hostkit mail domains
hostkit mail add-domain <domain> [--selector default]
hostkit mail remove-domain <domain> [--force]
hostkit mail mailboxes [--domain TEXT]
hostkit mail add-address <address> <project> [-p password]
hostkit mail remove-address <address>
hostkit mail queue
hostkit mail flush [--id TEXT]
hostkit mail purge --force
hostkit mail dns-records <domain>
hostkit mail dns-check <domain>
```

#### Project commands

```bash
hostkit mail enable <project>
hostkit mail disable <project> [--force]
hostkit mail add <project> <local_part> [-p password]
hostkit mail remove <project> <local_part>
hostkit mail list <project>
hostkit mail credentials <project> [<local_part>] [--reset-password]
hostkit mail send-test <project> <to_email> [--from noreply]
```

```bash
hostkit mail enable myapp
hostkit mail add myapp support
hostkit mail send-test myapp user@example.com
hostkit mail dns-check example.com
```

---

### redis

Manage Redis cache for projects. Each project is assigned a Redis database (0-49) for isolation.

```bash
hostkit redis info
hostkit redis keys <project> [-p pattern] [-l 100]
hostkit redis flush <project> --force
```

```bash
hostkit redis info
hostkit redis keys myapp --pattern "cache:*"
hostkit redis flush myapp --force
```

---

### autopause

Auto-pause projects after repeated failures to prevent resource waste.

**Defaults:** disabled, 5 failure threshold, 10 minute window.

```bash
hostkit autopause show <project>
hostkit autopause set <project> [--enabled|--disabled] [--threshold INT] [--window DURATION]
```

```bash
hostkit autopause set myapp --enabled --threshold 3 --window 5m
```

---

## Quick Reference

### Common Operations

| Task | Command |
|------|---------|
| Create project | `project create myapp --python --with-db` |
| Deploy | `deploy myapp --install --build` |
| Rollback | `rollback myapp` |
| Health check | `health myapp` |
| View logs | `log show myapp -f` |
| Diagnose failures | `diagnose myapp --run-test` |
| Enable service | `{service} enable myapp` |
| Enable storage | `storage enable myapp --public` |
| Backup | `backup create myapp --full --r2` |
| Add domain | `nginx add myapp example.com` |
| Set env var | `env set myapp KEY=VALUE --restart` |

### Service Ports

Each service runs at a fixed offset from the project's base port:

| Service | Offset | Example (base 8001) |
|---------|--------|---------------------|
| Auth | +1000 | 9001 |
| Payments | +2000 | 10001 |
| SMS | +3000 | 11001 |
| Booking | +4000 | 12001 |
| Chatbot | +5000 | 13001 |
| Voice | 8900 (central) | 8900 |

### Runtimes

| Flag | Start Command |
|------|---------------|
| `--python` | `venv/bin/python -m app` |
| `--node` | `node app/index.js` |
| `--nextjs` | `npm start` |
| `--static` | Nginx serves directly |
