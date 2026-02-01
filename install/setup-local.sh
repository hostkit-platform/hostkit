#!/usr/bin/env bash
# HostKit Local Machine Setup
# Configures your Mac or Linux machine to manage a HostKit VPS via Claude Code.
#
# Usage:
#   bash setup-local.sh --vps-ip 203.0.113.1
#
# What this does:
#   1. Checks prerequisites (Node.js >= 20, Python 3.11+)
#   2. Generates SSH key pair if needed
#   3. Copies public key to VPS ai-operator user
#   4. Installs the MCP server (hostkit-context)
#   5. Creates MCP server config at ~/.hostkit-context/config.json
#   6. Sets up agent template in your workspace
#   7. Prints Claude Code configuration snippet

set -euo pipefail

# ─── Colors & Helpers ───────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}${BOLD}==> $*${NC}"; }

# ─── Argument Parsing ───────────────────────────────────────────────────────

VPS_IP=""
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
SSH_USER="ai-operator"
WORKSPACE="${WORKSPACE:-$(pwd)}"
SKIP_SSH_COPY=false
SKIP_MCP_INSTALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vps-ip)           VPS_IP="$2"; shift 2 ;;
    --ssh-key)          SSH_KEY_PATH="$2"; shift 2 ;;
    --ssh-user)         SSH_USER="$2"; shift 2 ;;
    --workspace)        WORKSPACE="$2"; shift 2 ;;
    --skip-ssh-copy)    SKIP_SSH_COPY=true; shift ;;
    --skip-mcp-install) SKIP_MCP_INSTALL=true; shift ;;
    --help)
      echo "Usage: setup-local.sh --vps-ip IP [OPTIONS]"
      echo ""
      echo "Required:"
      echo "  --vps-ip IP            VPS public IP address"
      echo ""
      echo "Options:"
      echo "  --ssh-key PATH         SSH key path (default: ~/.ssh/id_ed25519)"
      echo "  --ssh-user USER        SSH user on VPS (default: ai-operator)"
      echo "  --workspace DIR        Agent workspace directory (default: current dir)"
      echo "  --skip-ssh-copy        Skip ssh-copy-id step"
      echo "  --skip-mcp-install     Skip MCP server installation"
      echo "  --help                 Show this help"
      exit 0
      ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$VPS_IP" ]]; then
  log_error "VPS IP is required. Usage: setup-local.sh --vps-ip 203.0.113.1"
  exit 1
fi

# ─── Prerequisites ──────────────────────────────────────────────────────────

log_step "Checking prerequisites"

PREREQS_OK=true

# Node.js >= 20
if command -v node &>/dev/null; then
  NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
  if [[ "$NODE_VERSION" -ge 20 ]]; then
    log_info "Node.js $(node -v)"
  else
    log_error "Node.js >= 20 required, found $(node -v)"
    PREREQS_OK=false
  fi
else
  log_error "Node.js not found. Install Node.js 20+: https://nodejs.org"
  PREREQS_OK=false
fi

# npm or pnpm
PACKAGE_MANAGER=""
if command -v pnpm &>/dev/null; then
  PACKAGE_MANAGER="pnpm"
  log_info "pnpm $(pnpm -v)"
elif command -v npm &>/dev/null; then
  PACKAGE_MANAGER="npm"
  log_info "npm $(npm -v)"
else
  log_error "npm or pnpm not found"
  PREREQS_OK=false
fi

# Python 3.11+
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
  if [[ "$PY_MINOR" -ge 11 ]]; then
    log_info "Python $PY_VERSION"
  else
    log_warn "Python 3.11+ recommended, found $PY_VERSION (MCP server embeddings may not work)"
  fi
else
  log_warn "Python 3 not found (needed for MCP server embeddings only)"
fi

# SSH client
if ! command -v ssh &>/dev/null; then
  log_error "SSH client not found"
  PREREQS_OK=false
else
  log_info "SSH client available"
fi

if [[ "$PREREQS_OK" == false ]]; then
  log_error "Prerequisites not met. Fix the above issues and re-run."
  exit 1
fi

# ─── SSH Key ────────────────────────────────────────────────────────────────

log_step "Setting up SSH key"

if [[ -f "$SSH_KEY_PATH" ]]; then
  log_info "SSH key already exists: $SSH_KEY_PATH"
else
  log_info "Generating new Ed25519 SSH key..."
  ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "hostkit-$(whoami)@$(hostname -s)"
  log_info "SSH key generated: $SSH_KEY_PATH"
fi

SSH_PUBKEY_PATH="${SSH_KEY_PATH}.pub"
if [[ ! -f "$SSH_PUBKEY_PATH" ]]; then
  log_error "Public key not found at $SSH_PUBKEY_PATH"
  exit 1
fi

# ─── Copy SSH Key to VPS ────────────────────────────────────────────────────

if [[ "$SKIP_SSH_COPY" == false ]]; then
  log_step "Copying SSH key to VPS"
  log_info "You may be prompted for the ai-operator password or root password."
  log_info "If ai-operator doesn't have a password yet, run on the VPS first:"
  echo -e "  ${DIM}sudo mkdir -p /home/ai-operator/.ssh"
  echo -e "  sudo cp ~/.ssh/authorized_keys /home/ai-operator/.ssh/"
  echo -e "  sudo chown -R ai-operator:ai-operator /home/ai-operator/.ssh${NC}"
  echo ""

  if ssh-copy-id -i "$SSH_PUBKEY_PATH" "${SSH_USER}@${VPS_IP}" 2>/dev/null; then
    log_info "SSH key copied to ${SSH_USER}@${VPS_IP}"
  else
    log_warn "ssh-copy-id failed. You may need to add the key manually."
    log_warn "Public key: $(cat "$SSH_PUBKEY_PATH")"
  fi

  # Test connection
  log_info "Testing SSH connection..."
  if ssh -o ConnectTimeout=10 -o BatchMode=yes -i "$SSH_KEY_PATH" "${SSH_USER}@${VPS_IP}" "echo 'Connection successful'" 2>/dev/null; then
    log_info "SSH connection to ${SSH_USER}@${VPS_IP} verified"
  else
    log_warn "SSH connection test failed. Verify the key was added correctly."
  fi
else
  log_info "Skipping SSH key copy (--skip-ssh-copy)"
fi

# ─── Install MCP Server ────────────────────────────────────────────────────

if [[ "$SKIP_MCP_INSTALL" == false ]]; then
  log_step "Installing MCP server (hostkit-context)"

  if [[ "$PACKAGE_MANAGER" == "pnpm" ]]; then
    pnpm install -g hostkit-context 2>/dev/null \
      || log_warn "hostkit-context not yet on npm. Install from source: cd packages/mcp-server && pnpm install && pnpm build"
  else
    npm install -g hostkit-context 2>/dev/null \
      || log_warn "hostkit-context not yet on npm. Install from source: cd packages/mcp-server && npm install && npm run build"
  fi
else
  log_info "Skipping MCP server installation (--skip-mcp-install)"
fi

# ─── MCP Server Configuration ──────────────────────────────────────────────

log_step "Configuring MCP server"

CONFIG_DIR="$HOME/.hostkit-context"
CONFIG_FILE="$CONFIG_DIR/config.json"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
  log_info "Config already exists at $CONFIG_FILE"
  log_info "Updating VPS host to $VPS_IP..."
  # Use a temp file approach for portability (works without jq)
  if command -v jq &>/dev/null; then
    jq --arg ip "$VPS_IP" --arg key "$SSH_KEY_PATH" \
      '.vps.host = $ip | .vps.keyPath = $key' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" \
      && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
  else
    log_warn "jq not installed. Overwriting config with fresh values."
    # Fall through to write new config
    rm "$CONFIG_FILE"
  fi
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" << JSON
{
  "vps": {
    "host": "$VPS_IP",
    "port": 22,
    "user": "$SSH_USER",
    "keyPath": "$SSH_KEY_PATH"
  },
  "dataDir": "$CONFIG_DIR",
  "cache": {
    "projectsTtl": 60000,
    "healthTtl": 30000,
    "projectTtl": 30000
  },
  "logging": {
    "level": "info",
    "debug": false
  }
}
JSON
  chmod 600 "$CONFIG_FILE"
  log_info "Created MCP config: $CONFIG_FILE"
fi

# ─── Agent Template ─────────────────────────────────────────────────────────

log_step "Setting up agent workspace"

# Determine where the template files are (handle both monorepo dev and installed)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_TEMPLATE_DIR="$SCRIPT_DIR/../packages/agent"

if [[ -d "$AGENT_TEMPLATE_DIR" ]]; then
  # Copy CLAUDE.md.template and replace placeholder
  if [[ -f "$AGENT_TEMPLATE_DIR/CLAUDE.md.template" ]]; then
    if [[ ! -f "$WORKSPACE/CLAUDE.md" ]]; then
      sed "s/{{VPS_IP}}/$VPS_IP/g" "$AGENT_TEMPLATE_DIR/CLAUDE.md.template" > "$WORKSPACE/CLAUDE.md"
      log_info "Created CLAUDE.md in $WORKSPACE (VPS IP: $VPS_IP)"
    else
      log_info "CLAUDE.md already exists in $WORKSPACE — not overwriting"
    fi
  fi

  # Copy Claude settings template
  if [[ -f "$AGENT_TEMPLATE_DIR/claude-settings.json" ]]; then
    mkdir -p "$WORKSPACE/.claude"
    if [[ ! -f "$WORKSPACE/.claude/settings.local.json" ]]; then
      cp "$AGENT_TEMPLATE_DIR/claude-settings.json" "$WORKSPACE/.claude/settings.local.json"
      log_info "Created .claude/settings.local.json"
    else
      log_info ".claude/settings.local.json already exists — not overwriting"
    fi
  fi

  # Copy skill commands
  if [[ -d "$AGENT_TEMPLATE_DIR/commands" ]]; then
    mkdir -p "$WORKSPACE/.claude/commands"
    cp -n "$AGENT_TEMPLATE_DIR/commands/"* "$WORKSPACE/.claude/commands/" 2>/dev/null || true
    log_info "Copied skill commands to .claude/commands/"
  fi
else
  log_warn "Agent template not found at $AGENT_TEMPLATE_DIR"
  log_warn "You can set up the agent template manually from the hostkit repo."
fi

# ─── Print Claude Code MCP Config ──────────────────────────────────────────

log_step "Claude Code MCP configuration"

# Determine MCP server command and args
MCP_COMMAND=""
MCP_ARGS=""
if command -v hostkit-context &>/dev/null; then
  # Globally installed — invoke the binary directly
  MCP_COMMAND="hostkit-context"
  MCP_ARGS=""
elif [[ -f "$SCRIPT_DIR/../packages/mcp-server/dist/index.js" ]]; then
  # Local dev build — invoke via node
  MCP_COMMAND="node"
  MCP_ARGS="$(cd "$SCRIPT_DIR/../packages/mcp-server" && pwd)/dist/index.js"
else
  MCP_COMMAND="node"
  MCP_ARGS="/path/to/hostkit-context/dist/index.js"
fi

echo ""
echo -e "${BOLD}Add this to your Claude Code MCP settings:${NC}"
echo -e "${DIM}(~/.claude.json or project .claude.json under \"mcpServers\")${NC}"
echo ""
echo -e "${GREEN}"
if [[ -n "$MCP_ARGS" ]]; then
cat << MCPJSON
{
  "mcpServers": {
    "hostkit-context": {
      "command": "$MCP_COMMAND",
      "args": ["$MCP_ARGS"],
      "env": {
        "HOSTKIT_VPS_HOST": "$VPS_IP",
        "HOSTKIT_SSH_USER": "$SSH_USER",
        "HOSTKIT_SSH_KEY_PATH": "$SSH_KEY_PATH"
      }
    }
  }
}
MCPJSON
else
cat << MCPJSON
{
  "mcpServers": {
    "hostkit-context": {
      "command": "$MCP_COMMAND",
      "args": [],
      "env": {
        "HOSTKIT_VPS_HOST": "$VPS_IP",
        "HOSTKIT_SSH_USER": "$SSH_USER",
        "HOSTKIT_SSH_KEY_PATH": "$SSH_KEY_PATH"
      }
    }
  }
}
MCPJSON
fi
echo -e "${NC}"

# ─── Summary ────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Local Setup Complete${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  VPS:           ${BOLD}${SSH_USER}@${VPS_IP}${NC}"
echo -e "  SSH Key:       $SSH_KEY_PATH"
echo -e "  MCP Config:    $CONFIG_DIR/config.json"
echo -e "  Workspace:     $WORKSPACE"
echo ""
echo -e "${BOLD}Quick test:${NC}"
echo "  ssh -i $SSH_KEY_PATH ${SSH_USER}@${VPS_IP} 'sudo hostkit status'"
echo ""
echo -e "${BOLD}Start Claude Code:${NC}"
echo "  cd $WORKSPACE"
echo "  claude"
echo ""
