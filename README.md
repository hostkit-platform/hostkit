# HostKit

[![CI](https://github.com/hostkit-platform/hostkit/actions/workflows/ci.yml/badge.svg)](https://github.com/hostkit-platform/hostkit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![npm](https://img.shields.io/npm/v/hostkit-context)](https://www.npmjs.com/package/hostkit-context)
[![PyPI](https://img.shields.io/pypi/v/hostkit)](https://pypi.org/project/hostkit/)

AI-agent-native VPS management. A platform that lets Claude Code agents (and other AI coding agents) deploy and operate full-stack projects on a single Linux VPS.

## Why HostKit?

AI coding agents can write code, but they can't deploy it. HostKit bridges that gap. It gives agents a set of MCP tools to manage a real Linux server -- creating projects, enabling services, deploying code, running database queries, and diagnosing issues -- all without manual SSH.

**One VPS. One agent. Full-stack production apps.**

- No Docker, no Kubernetes, no YAML pipelines
- Each project gets its own Linux user, systemd service, subdomain, and SSL
- Services (auth, payments, SMS, storage) are one command away
- Agents share solutions across projects so problems are solved once

## Architecture

```
┌─────────────────────┐     stdio      ┌─────────────────────┐     SSH      ┌─────────────────┐
│                     │ ◄────────────► │                     │ ◄──────────► │                 │
│   Claude Code /     │                │   MCP Server        │              │   VPS           │
│   OpenClaw Agent    │                │   (hostkit-context)  │              │   (hostkit CLI)  │
│                     │                │                     │              │                 │
│  Reads CLAUDE.md    │                │  15 tools:          │              │  Python CLI     │
│  Uses MCP tools     │                │  search, state,     │              │  systemd, nginx │
│  Builds & deploys   │                │  execute, deploy,   │              │  PostgreSQL     │
│                     │                │  db, auth, ...      │              │  Redis, MinIO   │
└─────────────────────┘                └─────────────────────┘              └─────────────────┘
       Your machine                          Your machine                      Remote server
```

## Packages

| Package | Path | Description |
|---------|------|-------------|
| **CLI** | [`packages/cli`](packages/cli/) | Python CLI installed on the VPS. Manages projects, databases, services, deployments, backups, and more. |
| **MCP Server** | [`packages/mcp-server`](packages/mcp-server/) | TypeScript MCP server that runs locally. Gives AI agents 15 tools for managing the VPS over SSH. |
| **Agent Template** | [`packages/agent`](packages/agent/) | Starter `CLAUDE.md` and permissions config for new projects. |

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Ubuntu 22.04+ VPS** (2+ GB RAM) | Hetzner, DigitalOcean, Vultr, Linode all work |
| **Domain name on Cloudflare** | HostKit uses wildcard subdomains (`*.yourdomain.com`) so each project automatically gets `project.yourdomain.com`. This requires Cloudflare DNS with a wildcard A record pointing to your VPS. |
| **Node.js 20+** (local machine) | Runs the MCP server |

### Cloudflare DNS setup

HostKit's automatic subdomain routing requires a wildcard DNS record managed by Cloudflare:

1. Add your domain to [Cloudflare](https://dash.cloudflare.com/) (free plan works)
2. Create a wildcard A record: `*.yourdomain.com → YOUR_VPS_IP` (proxied or DNS-only)
3. Create a root A record: `yourdomain.com → YOUR_VPS_IP`
4. Set the `domain` field in `/etc/hostkit/config.yaml` to your domain after bootstrap

Without Cloudflare, you can still use HostKit — but you'll need to manually configure DNS for each project instead of getting automatic `project.yourdomain.com` subdomains.

## Quick Start

### 1. Provision a VPS

Any Ubuntu 22.04+ VPS with 2+ GB RAM. Hetzner, DigitalOcean, Vultr, Linode all work.

### 2. Bootstrap the VPS

```bash
# SSH in and run the bootstrap script
ssh root@YOUR_VPS_IP
curl -fsSL https://raw.githubusercontent.com/hostkit-platform/hostkit/main/install/bootstrap.sh | bash
```

Or use the [cloud-init config](install/cloud-init.yaml) when creating the VPS for a hands-off setup.

### 3. Set up your local machine

```bash
# Run from your local machine
bash install/setup-local.sh --vps-ip YOUR_VPS_IP
```

This installs the MCP server, copies your SSH key, and sets up the agent template.

### 4. Configure Claude Code

Add the MCP server to your Claude Code config (`.claude.json` or project MCP settings):

```json
{
  "mcpServers": {
    "hostkit-context": {
      "command": "node",
      "args": ["/path/to/hostkit/packages/mcp-server/dist/index.js"]
    }
  }
}
```

### 5. Build something

```bash
claude
# "Create a new Python project called myapp with auth and a database"
```

The agent uses MCP tools to create the project, enable services, deploy code, and verify health -- all without manual SSH.

## What agents can do

Through the MCP server, agents have access to:

- **Project lifecycle** -- create, deploy, rollback, health check
- **Services** -- auth (OAuth + magic links), payments (Stripe), SMS (Twilio), chatbot (Claude/GPT), voice, booking
- **Infrastructure** -- PostgreSQL, Redis, MinIO object storage, R2 backups, nginx, SSL
- **Operations** -- environment variables, logs, database queries, schema inspection, diagnostics
- **Knowledge** -- semantic search over docs, cross-project solution sharing

Each project is isolated as a Linux user with its own systemd service, port allocation, and `{project}.hostkit.dev` subdomain with automatic SSL.

## Works with

- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** -- native MCP support via stdio
- **[OpenClaw](https://github.com/openclaw)** -- connect via [MCPorter](https://github.com/openclaw/mcporter) to bridge MCP tools

## Install scripts

| Script | Runs on | Purpose |
|--------|---------|---------|
| [`install/bootstrap.sh`](install/bootstrap.sh) | VPS | Full server setup (packages, users, PostgreSQL, Redis, nginx, firewall) |
| [`install/setup-local.sh`](install/setup-local.sh) | Local machine | SSH keys, MCP server install, agent template |
| [`install/cloud-init.yaml`](install/cloud-init.yaml) | VPS (at creation) | Unattended bootstrap via cloud provider user-data |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup across all three packages.

## License

[MIT](LICENSE)
