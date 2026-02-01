# hostkit-context MCP Server

A local MCP server that gives AI coding agents full control of a HostKit VPS. Connects to the server over SSH and exposes 15 tools for project management, deployment, database operations, and more.

Built on the [Model Context Protocol](https://modelcontextprotocol.io/) SDK.

## Installation

### Global install (recommended)

```bash
npm install -g hostkit-context
```

### From source

```bash
cd packages/mcp-server
npm install
npm run build
```

### Via setup script

The [local setup script](../../install/setup-local.sh) installs the MCP server and configures everything:

```bash
bash install/setup-local.sh --vps-ip YOUR_VPS_IP
```

## Requirements

- Node.js >= 20
- SSH access to a HostKit VPS (key-based auth)
- Python 3.11+ (for embedding generation only)

## Configuration

### Config file

Create `~/.hostkit-context/config.json`:

```json
{
  "vps": {
    "host": "YOUR_VPS_IP",
    "port": 22,
    "user": "ai-operator",
    "keyPath": "~/.ssh/id_ed25519"
  },
  "cache": {
    "projectsTtl": 60000,
    "healthTtl": 30000,
    "projectTtl": 30000
  }
}
```

### Environment variables

Alternatively, configure via environment variables (see `.env.example`):

```bash
HOSTKIT_VPS_HOST=YOUR_VPS_IP
HOSTKIT_VPS_PORT=22
HOSTKIT_SSH_USER=ai-operator
HOSTKIT_SSH_KEY_PATH=~/.ssh/id_ed25519
```

## Setup with Claude Code

Add to your project's `.claude.json` or Claude Code MCP settings:

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

If installed globally:

```json
{
  "mcpServers": {
    "hostkit-context": {
      "command": "hostkit-context"
    }
  }
}
```

## Setup with OpenClaw

Use [MCPorter](https://github.com/openclaw/mcporter) to bridge MCP tools into OpenClaw:

```bash
# Install MCPorter
npm install -g mcporter

# Bridge the HostKit MCP server
mcporter bridge hostkit-context -- node /path/to/hostkit/packages/mcp-server/dist/index.js
```

Then configure your OpenClaw agent to use the bridged tools.

## Tools

### Search & Documentation

| Tool | Description |
|------|-------------|
| `hostkit_search` | Semantic search over HostKit documentation. Query with natural language. |
| `hostkit_capabilities` | Discover all available commands, services, flags, and runtimes. |
| `hostkit_auth_guide` | Get runtime-specific auth integration code and warnings. Call before implementing auth. |

### State & Execution

| Tool | Description |
|------|-------------|
| `hostkit_state` | Live VPS state: projects, health, resources. Cached for performance. |
| `hostkit_execute` | Execute any HostKit CLI command on the VPS. Validates safety before running. |
| `hostkit_wait_healthy` | Block until a project is healthy. Use after deploys or restarts. |
| `hostkit_validate` | Pre-flight checks: entrypoint, dependencies, environment, database, ports. |

### Deployment

| Tool | Description |
|------|-------------|
| `hostkit_deploy_local` | Rsync local files to VPS, deploy, and wait for healthy. |

### Environment

| Tool | Description |
|------|-------------|
| `hostkit_env_set` | Set environment variables (batch, with optional service restart). |
| `hostkit_env_get` | Read environment variables (specific keys or all). |

### Database

| Tool | Description |
|------|-------------|
| `hostkit_db_schema` | Get tables, columns, indexes, and foreign keys. |
| `hostkit_db_query` | Run SQL queries (SELECT by default, opt-in for writes). |
| `hostkit_db_verify` | Check migrations, indexes, constraints, seed data. |

### Operations

| Tool | Description |
|------|-------------|
| `hostkit_fix_permissions` | Detect and fix sudoers permission gaps. Self-healing. |
| `hostkit_solutions` | Cross-project learning: search and record solutions to common problems. |

## Syncing documentation

When the HostKit `CLAUDE.md` changes, rebuild the search index:

```bash
npm run build
npm run sync
npm run build-embeddings
```

Embedding generation requires Python 3.11+ with `sentence-transformers`.

## Architecture

```
src/
├── index.ts           # MCP server entry point
├── config.ts          # Configuration (env vars + config file)
├── types.ts           # TypeScript type definitions
├── sync.ts            # Documentation sync script
├── tools/             # Tool implementations (one per file)
│   ├── index.ts       # Tool registry and dispatch
│   ├── search.ts      # Semantic documentation search
│   ├── state.ts       # VPS state queries
│   ├── execute.ts     # Command execution with safety checks
│   ├── permissions.ts # Sudoers gap detection and fix
│   ├── solutions.ts   # Cross-project solution database
│   ├── database.ts    # DB schema, query, verify
│   ├── deploy-local.ts# Local file deployment via rsync
│   ├── convenience.ts # capabilities, wait_healthy, env, validate
│   └── auth-guide.ts  # Auth integration guidance
├── services/
│   ├── ssh.ts         # SSH connection management
│   └── cache.ts       # Response caching (30-60s TTL)
├── indexers/
│   └── claudemd.ts    # CLAUDE.md section parser
└── utils/
    └── logger.ts      # Structured logging

embeddings/
├── generator.py       # Build-time embedding generation (sentence-transformers)
└── query.py           # Runtime embedding queries
```

## Development

```bash
cd packages/mcp-server
npm install

# Build
npm run build

# Type check
npm run typecheck

# Test
npm test

# Build + run
npm run dev
```
