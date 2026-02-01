# HostKit CLI

Python CLI for managing projects, services, and infrastructure on a HostKit VPS. Runs on the server and is called by the MCP server over SSH (or directly by operators).

## Installation

### Via bootstrap script (recommended)

The [bootstrap script](../../install/bootstrap.sh) installs the CLI as part of full VPS setup:

```bash
curl -fsSL https://raw.githubusercontent.com/hostkit-platform/hostkit/main/install/bootstrap.sh | bash
```

### Via pip

```bash
pip install hostkit
```

### From source

```bash
cd packages/cli
pip install -e .
```

## Requirements

- Python 3.11+
- Ubuntu 22.04+ (server-side)
- PostgreSQL, Redis, nginx (installed by bootstrap)

## Usage

```bash
# System overview
hostkit status
hostkit status --json

# Project lifecycle
hostkit project create myapp --python --with-db --with-auth
hostkit deploy myapp --install
hostkit rollback myapp
hostkit health myapp

# Services
hostkit auth enable myapp
hostkit payments enable myapp
hostkit sms enable myapp
hostkit chatbot enable myapp
hostkit minio enable myapp --public

# Database
hostkit db create myapp
hostkit db migrate myapp
hostkit backup create myapp --r2

# Operations
hostkit service logs myapp
hostkit env set myapp KEY=value
hostkit diagnose myapp
hostkit validate myapp
```

Every command supports `--json` for machine-readable output.

## Command Reference

| Command | Description |
|---------|-------------|
| `status` | System overview (projects, health, resources) |
| `project create` | Create a new project (Linux user, systemd service, port, nginx) |
| `project list` | List all projects |
| `deploy` | Deploy a project (install deps, swap symlink, restart) |
| `rollback` | Roll back to previous release |
| `health` | Health check a project |
| `diagnose` | Deep diagnostics (service, ports, logs, config) |
| `validate` | Pre-flight validation (entrypoint, deps, env, db) |
| `service` | Start, stop, restart, logs for project services |
| `env` | Get/set environment variables |
| `db` | Create, migrate, backup, restore databases |
| `backup` | Create and manage backups (local or R2) |
| `nginx` | Add domains, manage reverse proxy rules |
| `ssl` | Manage Let's Encrypt certificates |
| `auth` | Enable/configure OAuth + magic link auth service |
| `payments` | Enable/configure Stripe payments service |
| `sms` | Enable/configure Twilio SMS service |
| `chatbot` | Enable/configure AI chatbot service |
| `voice` | Enable/configure voice service |
| `booking` | Enable/configure booking service |
| `minio` | Enable/configure MinIO object storage |
| `r2` | Manage Cloudflare R2 backups |
| `vector` | Enable/configure vector/RAG service |
| `mail` | Configure email services |
| `ratelimit` | Enable/disable deploy rate limiting |
| `capabilities` | List all available commands and services |

## Configuration

The CLI reads configuration from `/etc/hostkit/config.yaml`:

```yaml
domain: yourdomain.com       # Your Cloudflare-managed domain
data_dir: /var/lib/hostkit
log_dir: /var/log/hostkit
backup_dir: /backups
```

> **Note:** The `domain` field controls automatic subdomain routing. Each project gets `{project}.{domain}`. This requires a wildcard DNS record (`*.yourdomain.com`) pointing to your VPS, managed through [Cloudflare](https://dash.cloudflare.com/) (free plan works). See the [root README](../../README.md#cloudflare-dns-setup) for setup instructions.

## Project layout on disk

Each project maps to:

```
/home/{project}/
├── releases/           # Timestamped deploys
├── app -> releases/x/  # Current release (symlink)
├── shared/             # Persistent data across deploys
├── .env                # Environment variables
├── .auth/              # Auth service (if enabled)
├── .payments/          # Payments service (if enabled)
└── venv/ or node_modules/
```

## Runtimes

| Flag | Entry point |
|------|-------------|
| `--python` | `venv/bin/python -m app` |
| `--node` | `node app/index.js` |
| `--nextjs` | `npm start` |
| `--static` | nginx serves directly |

## Service port allocation

Each project gets a base port (starting at 8001). Services use offsets:

| Service | Offset | Example (base 8001) |
|---------|--------|---------------------|
| Main app | +0 | 8001 |
| Auth | +1000 | 9001 |
| Payments | +2000 | 10001 |
| SMS | +3000 | 11001 |
| Booking | +4000 | 12001 |
| Chatbot | +5000 | 13001 |

## Development

```bash
cd packages/cli
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Lint
ruff check src/

# Type check
mypy src/hostkit/

# Test
pytest
```

To deploy changes to a live VPS:

```bash
VPS_HOST=root@YOUR_VPS_IP ./scripts/deploy.sh
```
