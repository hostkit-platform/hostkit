#!/usr/bin/env bash
# HostKit VPS Bootstrap Script
# Sets up a fresh Ubuntu 22.04/24.04 VPS with all HostKit dependencies.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/hostkit-platform/hostkit/main/install/bootstrap.sh | bash
#   # or
#   bash bootstrap.sh [--vps-ip 203.0.113.1] [--ssh-pubkey "ssh-ed25519 AAAA..."]
#
# This script must be run as root on the target VPS.

set -euo pipefail
trap 'log_error "Bootstrap failed at line $LINENO"; exit 1' ERR

# ─── Configuration ──────────────────────────────────────────────────────────

HOSTKIT_VERSION="${HOSTKIT_VERSION:-latest}"
HOSTKIT_DATA_DIR="/var/lib/hostkit"
HOSTKIT_LOG_DIR="/var/log/hostkit"
HOSTKIT_BACKUP_DIR="/backups"
HOSTKIT_CONFIG_DIR="/etc/hostkit"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}${BOLD}==> $*${NC}"; }

# ─── Argument Parsing ───────────────────────────────────────────────────────

VPS_IP=""
SSH_PUBKEY=""
SKIP_FIREWALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vps-ip)      VPS_IP="$2"; shift 2 ;;
    --ssh-pubkey)  SSH_PUBKEY="$2"; shift 2 ;;
    --skip-firewall) SKIP_FIREWALL=true; shift ;;
    --help)
      echo "Usage: bootstrap.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --vps-ip IP         VPS public IP (auto-detected if omitted)"
      echo "  --ssh-pubkey KEY    SSH public key for ai-operator user"
      echo "  --skip-firewall     Skip UFW configuration"
      echo "  --help              Show this help"
      exit 0
      ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

# ─── Preflight Checks ──────────────────────────────────────────────────────

log_step "Preflight checks"

# Must be root
if [[ $EUID -ne 0 ]]; then
  log_error "This script must be run as root"
  exit 1
fi

# Must be Ubuntu
if [[ ! -f /etc/os-release ]]; then
  log_error "Cannot detect OS. This script requires Ubuntu 22.04 or 24.04."
  exit 1
fi

# shellcheck source=/dev/null
source /etc/os-release

if [[ "$ID" != "ubuntu" ]]; then
  log_error "This script requires Ubuntu. Detected: $ID"
  exit 1
fi

UBUNTU_MAJOR="${VERSION_ID%%.*}"
if [[ "$UBUNTU_MAJOR" -lt 22 ]]; then
  log_error "Ubuntu 22.04+ required. Detected: $VERSION_ID"
  exit 1
fi

log_info "OS: Ubuntu $VERSION_ID"

# Auto-detect VPS IP if not provided
if [[ -z "$VPS_IP" ]]; then
  VPS_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)
  if [[ -z "$VPS_IP" ]]; then
    VPS_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
  fi
  if [[ -z "$VPS_IP" ]]; then
    log_error "Could not auto-detect VPS IP. Pass --vps-ip explicitly."
    exit 1
  fi
fi

log_info "VPS IP: $VPS_IP"

# Disable colors when not in a terminal (e.g., curl | bash)
if [[ ! -t 1 ]]; then
  RED='' GREEN='' YELLOW='' BLUE='' BOLD='' NC=''
fi

# ─── System Packages ───────────────────────────────────────────────────────

log_step "Installing system packages"

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq

apt-get install -y -qq \
  python3 \
  python3-pip \
  python3-venv \
  postgresql \
  postgresql-contrib \
  redis-server \
  nginx \
  certbot \
  python3-certbot-nginx \
  fail2ban \
  ufw \
  git \
  curl \
  wget \
  jq \
  htop \
  unzip \
  rsync \
  acl \
  logrotate \
  > /dev/null

log_info "System packages installed"

# ─── System Users ───────────────────────────────────────────────────────────

log_step "Creating system users"

# hostkit system user (owns platform files, no login)
if ! id -u hostkit &>/dev/null; then
  useradd --system --shell /usr/sbin/nologin --home-dir "$HOSTKIT_DATA_DIR" --create-home hostkit
  log_info "Created system user: hostkit"
else
  log_info "User hostkit already exists"
fi

# ai-operator user (SSH access for MCP server)
if ! id -u ai-operator &>/dev/null; then
  useradd --shell /bin/bash --create-home ai-operator
  log_info "Created user: ai-operator"
else
  log_info "User ai-operator already exists"
fi

# Set up SSH for ai-operator
AI_OP_HOME=$(eval echo ~ai-operator)
mkdir -p "$AI_OP_HOME/.ssh"
chmod 700 "$AI_OP_HOME/.ssh"
touch "$AI_OP_HOME/.ssh/authorized_keys"
chmod 600 "$AI_OP_HOME/.ssh/authorized_keys"
chown -R ai-operator:ai-operator "$AI_OP_HOME/.ssh"

if [[ -n "$SSH_PUBKEY" ]]; then
  if ! grep -qF "$SSH_PUBKEY" "$AI_OP_HOME/.ssh/authorized_keys" 2>/dev/null; then
    echo "$SSH_PUBKEY" >> "$AI_OP_HOME/.ssh/authorized_keys"
    log_info "Added SSH public key for ai-operator"
  else
    log_info "SSH public key already present for ai-operator"
  fi
fi

# ─── Sudoers for ai-operator ───────────────────────────────────────────────

log_step "Configuring sudo rules for ai-operator"

# Write to temp file, validate, then install (prevents broken sudo on syntax error)
SUDOERS_TMP=$(mktemp)
cat > "$SUDOERS_TMP" << 'SUDOERS'
# HostKit AI Operator — allowed to run hostkit commands as root
ai-operator ALL=(root) NOPASSWD: /usr/local/bin/hostkit *
ai-operator ALL=(root) NOPASSWD: /usr/bin/hostkit *

# Allow systemctl for hostkit services
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl status hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl start hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl stop hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl restart hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl reload hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl enable hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/systemctl disable hostkit-*

# Allow journalctl for hostkit service logs (restricted flags)
ai-operator ALL=(root) NOPASSWD: /usr/bin/journalctl -u hostkit-*
ai-operator ALL=(root) NOPASSWD: /usr/bin/journalctl -u hostkit-* -n *
ai-operator ALL=(root) NOPASSWD: /usr/bin/journalctl -u hostkit-* --no-pager
ai-operator ALL=(root) NOPASSWD: /usr/bin/journalctl -u hostkit-* -n * --no-pager

# Allow reading project environment files and app directories
ai-operator ALL=(root) NOPASSWD: /usr/bin/ls /home/*/app
ai-operator ALL=(root) NOPASSWD: /usr/bin/ls /home/*/app/*
ai-operator ALL=(root) NOPASSWD: /usr/bin/cat /home/*/.env
ai-operator ALL=(root) NOPASSWD: /usr/bin/cat /home/*/.hostkit-*
SUDOERS

chmod 0440 "$SUDOERS_TMP"
if visudo -cf "$SUDOERS_TMP"; then
  mv "$SUDOERS_TMP" /etc/sudoers.d/ai-operator
  log_info "Sudoers rules installed"
else
  rm -f "$SUDOERS_TMP"
  log_error "Sudoers validation failed — rules not installed"
  exit 1
fi

# ─── Directory Structure ────────────────────────────────────────────────────

log_step "Creating directory structure"

mkdir -p "$HOSTKIT_DATA_DIR"/{templates,db,docs}
mkdir -p "$HOSTKIT_LOG_DIR"
mkdir -p "$HOSTKIT_BACKUP_DIR"
mkdir -p "$HOSTKIT_CONFIG_DIR"

chown -R hostkit:hostkit "$HOSTKIT_DATA_DIR"
chown -R hostkit:hostkit "$HOSTKIT_LOG_DIR"
chmod 755 "$HOSTKIT_BACKUP_DIR"

log_info "Directories created"

# ─── PostgreSQL ─────────────────────────────────────────────────────────────

log_step "Configuring PostgreSQL"

# Ensure PostgreSQL is running
systemctl enable --now postgresql

# Generate random password for hostkit DB role
PG_PASSWORD_FILE="$HOSTKIT_CONFIG_DIR/.pg_password"
if [[ -f "$PG_PASSWORD_FILE" ]]; then
  PG_PASSWORD=$(cat "$PG_PASSWORD_FILE")
else
  PG_PASSWORD=$(openssl rand -base64 24)
  echo "$PG_PASSWORD" > "$PG_PASSWORD_FILE"
  chmod 600 "$PG_PASSWORD_FILE"
  chown root:root "$PG_PASSWORD_FILE"
fi

# Create hostkit superuser role (idempotent)
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='hostkit'" | grep -q 1; then
  log_info "PostgreSQL role 'hostkit' already exists"
else
  sudo -u postgres createuser --superuser hostkit
  sudo -u postgres psql -c "ALTER USER hostkit WITH PASSWORD '$PG_PASSWORD';"
  log_info "Created PostgreSQL superuser role: hostkit"
fi

# Create default database
if sudo -u postgres psql -lqt | cut -d '|' -f 1 | grep -qw hostkit; then
  log_info "Database 'hostkit' already exists"
else
  sudo -u postgres createdb --owner=hostkit hostkit
  log_info "Created database: hostkit"
fi

# Configure pg_hba for local connections (if not already set)
PG_VERSION=$(pg_lsclusters -h | awk '{print $1}' | head -1)
PG_HBA="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"

if [[ -f "$PG_HBA" ]]; then
  # Check for the exact hostkit auth rule (not just any mention of "hostkit")
  if ! grep -q "^local.*all.*hostkit.*md5" "$PG_HBA"; then
    # Insert before the first uncommented local rule
    sed -i '/^local\s/i local   all             hostkit                                 md5' "$PG_HBA"
    systemctl reload postgresql
    log_info "Updated pg_hba.conf for hostkit user"
  else
    log_info "pg_hba.conf already configured for hostkit"
  fi
fi

# ─── Redis ──────────────────────────────────────────────────────────────────

log_step "Configuring Redis"

REDIS_CONF="/etc/redis/redis.conf"

if [[ -f "$REDIS_CONF" ]]; then
  # Bind to localhost only
  sed -i 's/^bind .*/bind 127.0.0.1 ::1/' "$REDIS_CONF"

  # Enable AOF persistence
  sed -i 's/^appendonly .*/appendonly yes/' "$REDIS_CONF"

  # Set max memory
  if ! grep -q "^maxmemory " "$REDIS_CONF"; then
    echo "maxmemory 512mb" >> "$REDIS_CONF"
    echo "maxmemory-policy allkeys-lru" >> "$REDIS_CONF"
  fi

  systemctl enable --now redis-server
  systemctl restart redis-server
  log_info "Redis configured (bind localhost, AOF, 512MB max)"
else
  log_warn "Redis config not found at $REDIS_CONF — skipping configuration"
fi

# ─── UFW Firewall ───────────────────────────────────────────────────────────

if [[ "$SKIP_FIREWALL" == false ]]; then
  log_step "Configuring UFW firewall"

  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp    # SSH
  ufw allow 80/tcp    # HTTP
  ufw allow 443/tcp   # HTTPS
  ufw --force enable
  log_info "UFW enabled: SSH(22), HTTP(80), HTTPS(443)"
else
  log_info "Skipping firewall configuration (--skip-firewall)"
fi

# ─── Fail2ban ───────────────────────────────────────────────────────────────

log_step "Configuring fail2ban"

cat > /etc/fail2ban/jail.local << 'F2B'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = ssh
logpath = %(sshd_log)s
backend = %(sshd_backend)s
F2B

systemctl enable --now fail2ban
systemctl restart fail2ban
log_info "fail2ban configured for SSH"

# ─── SSH Hardening ──────────────────────────────────────────────────────────

log_step "Hardening SSH"

SSHD_CONFIG="/etc/ssh/sshd_config"
SSHD_CHANGED=false

# Disable password authentication
if grep -q "^PasswordAuthentication yes" "$SSHD_CONFIG" 2>/dev/null; then
  sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' "$SSHD_CONFIG"
  SSHD_CHANGED=true
elif ! grep -q "^PasswordAuthentication" "$SSHD_CONFIG"; then
  echo "PasswordAuthentication no" >> "$SSHD_CONFIG"
  SSHD_CHANGED=true
fi

# Disable root login with password (allow key-based)
if grep -q "^PermitRootLogin yes" "$SSHD_CONFIG" 2>/dev/null; then
  sed -i 's/^PermitRootLogin yes/PermitRootLogin prohibit-password/' "$SSHD_CONFIG"
  SSHD_CHANGED=true
fi

if [[ "$SSHD_CHANGED" == true ]]; then
  sshd -t && systemctl reload sshd
  log_info "SSH hardened: password auth disabled, root login key-only"
else
  log_info "SSH already hardened"
fi

# ─── NTP Time Sync ─────────────────────────────────────────────────────────

log_step "Ensuring NTP time sync"

timedatectl set-ntp true 2>/dev/null || true
log_info "NTP sync enabled (required for JWT verification)"

# ─── Nginx ──────────────────────────────────────────────────────────────────

log_step "Configuring Nginx"

# Remove default site if it exists
rm -f /etc/nginx/sites-enabled/default

# Create HostKit nginx directory for per-project configs
mkdir -p /etc/nginx/hostkit

# Add include for hostkit configs if not present
if ! grep -q "include /etc/nginx/hostkit/" /etc/nginx/nginx.conf; then
  sed -i '/http {/a \    include /etc/nginx/hostkit/*.conf;' /etc/nginx/nginx.conf
fi

# Test and reload
nginx -t 2>/dev/null && systemctl enable --now nginx && systemctl reload nginx
log_info "Nginx configured with /etc/nginx/hostkit/ include"

# ─── Install HostKit CLI ────────────────────────────────────────────────────

log_step "Installing HostKit CLI"

if [[ "$HOSTKIT_VERSION" == "latest" ]]; then
  pip3 install --break-system-packages hostkit 2>/dev/null \
    || pip3 install hostkit \
    || { log_warn "HostKit not yet on PyPI. Install manually with: pip3 install /path/to/hostkit.whl"; }
else
  pip3 install --break-system-packages "hostkit==$HOSTKIT_VERSION" 2>/dev/null \
    || pip3 install "hostkit==$HOSTKIT_VERSION" \
    || { log_warn "Failed to install hostkit $HOSTKIT_VERSION"; }
fi

# Verify installation
if command -v hostkit &>/dev/null; then
  log_info "HostKit CLI installed: $(hostkit --version 2>/dev/null || echo 'unknown version')"
else
  log_warn "HostKit CLI not found in PATH. Install manually after bootstrap."
fi

# ─── HostKit Configuration ──────────────────────────────────────────────────

log_step "Writing HostKit configuration"

if [[ ! -f "$HOSTKIT_CONFIG_DIR/config.yaml" ]]; then
  cat > "$HOSTKIT_CONFIG_DIR/config.yaml" << YAML
# HostKit Configuration
# Generated by bootstrap.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

data_dir: $HOSTKIT_DATA_DIR
log_dir: $HOSTKIT_LOG_DIR
backup_dir: $HOSTKIT_BACKUP_DIR
db_path: $HOSTKIT_DATA_DIR/hostkit.db

# VPS identity
vps_ip: $VPS_IP

# Service defaults
postgres_host: localhost
postgres_port: 5432
postgres_password_file: $HOSTKIT_CONFIG_DIR/.pg_password
redis_host: localhost
redis_port: 6379

# Project defaults
default_runtime: python
base_port: 8000
max_projects: 50
YAML
  log_info "Created $HOSTKIT_CONFIG_DIR/config.yaml"
else
  log_info "Config already exists at $HOSTKIT_CONFIG_DIR/config.yaml"
fi

# ─── Logrotate ──────────────────────────────────────────────────────────────

cat > /etc/logrotate.d/hostkit << 'LOGROTATE'
/var/log/hostkit/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 hostkit hostkit
    sharedscripts
}
LOGROTATE

log_info "Logrotate configured for /var/log/hostkit/"

# ─── Summary ────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  HostKit Bootstrap Complete${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  VPS IP:         ${BOLD}$VPS_IP${NC}"
echo -e "  Config:         $HOSTKIT_CONFIG_DIR/config.yaml"
echo -e "  Data:           $HOSTKIT_DATA_DIR/"
echo -e "  Logs:           $HOSTKIT_LOG_DIR/"
echo -e "  Backups:        $HOSTKIT_BACKUP_DIR/"
echo -e "  PostgreSQL:     running (role: hostkit, password in $PG_PASSWORD_FILE)"
echo -e "  Redis:          running (localhost:6379)"
echo -e "  Nginx:          running"
echo -e "  Firewall:       $(if [[ "$SKIP_FIREWALL" == false ]]; then echo "enabled (22, 80, 443)"; else echo "skipped"; fi)"
echo -e "  ai-operator:    $(if [[ -n "$SSH_PUBKEY" ]]; then echo "SSH key configured"; else echo "created (add SSH key manually)"; fi)"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo ""
if [[ -z "$SSH_PUBKEY" ]]; then
  echo "  1. Add your SSH public key for the ai-operator user:"
  echo "     ssh-copy-id -i ~/.ssh/id_ed25519.pub ai-operator@$VPS_IP"
  echo ""
  echo "  2. Run setup-local.sh on your local machine:"
  echo "     bash setup-local.sh --vps-ip $VPS_IP"
else
  echo "  1. Run setup-local.sh on your local machine:"
  echo "     bash setup-local.sh --vps-ip $VPS_IP"
fi
echo ""
echo -e "${BOLD}DNS setup (required for automatic subdomains):${NC}"
echo ""
echo "  HostKit gives each project a subdomain: project.yourdomain.com"
echo "  This requires Cloudflare DNS with a wildcard A record:"
echo ""
echo "    *.yourdomain.com  →  $VPS_IP  (A record)"
echo "    yourdomain.com    →  $VPS_IP  (A record)"
echo ""
echo "  Then set 'domain: yourdomain.com' in $HOSTKIT_CONFIG_DIR/config.yaml"
echo "  See: https://github.com/hostkit-platform/hostkit#cloudflare-dns-setup"
echo ""
echo "  Docs: https://github.com/hostkit-platform/hostkit"
echo ""
