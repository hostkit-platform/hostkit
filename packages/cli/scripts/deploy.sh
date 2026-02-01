#!/bin/bash
# HostKit Deployment Script
# Builds and deploys hostkit to VPS

set -e

# Configuration
VPS_HOST="${VPS_HOST:-root@YOUR_VPS_IP}"
VPS_PYTHON="${VPS_PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Guard against deploying with placeholder IP
if [[ "$VPS_HOST" == *"YOUR_VPS_IP"* ]]; then
    echo_error "VPS_HOST is not configured. Set VPS_HOST environment variable:"
    echo_error "  export VPS_HOST=root@<your-vps-ip>"
    echo_error "  $0"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

echo_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check for required tools
check_requirements() {
    echo_info "Checking requirements..."

    if ! command -v python3 &> /dev/null; then
        echo_error "python3 is required but not installed"
        exit 1
    fi

    if ! command -v ssh &> /dev/null; then
        echo_error "ssh is required but not installed"
        exit 1
    fi

    if ! command -v scp &> /dev/null; then
        echo_error "scp is required but not installed"
        exit 1
    fi
}

# Build the wheel package
build_wheel() {
    echo_info "Building wheel package..."
    cd "$PROJECT_DIR"

    # Clean previous builds
    rm -rf dist/ build/ src/*.egg-info

    # Install build if needed and build
    python3 -m pip install --quiet build
    python3 -m build --wheel

    # Find the built wheel
    WHEEL_FILE=$(ls dist/*.whl 2>/dev/null | head -1)
    if [ -z "$WHEEL_FILE" ]; then
        echo_error "Failed to build wheel package"
        exit 1
    fi

    echo_info "Built: $WHEEL_FILE"
}

# Deploy to VPS
deploy_to_vps() {
    echo_info "Deploying to VPS ($VPS_HOST)..."

    # Get just the filename
    WHEEL_BASENAME=$(basename "$WHEEL_FILE")

    # Copy wheel to VPS
    echo_info "Copying wheel to VPS..."
    scp "$WHEEL_FILE" "$VPS_HOST:/tmp/$WHEEL_BASENAME"

    # Copy templates to VPS
    echo_info "Copying templates to VPS..."
    rsync -av --delete "$PROJECT_DIR/templates/" "$VPS_HOST:/var/lib/hostkit/templates/"

    # Copy CLAUDE.md for docs indexing
    echo_info "Copying CLAUDE.md to VPS..."
    scp "$PROJECT_DIR/CLAUDE.md" "$VPS_HOST:/var/lib/hostkit/CLAUDE.md"

    # Install on VPS
    echo_info "Installing on VPS..."
    ssh "$VPS_HOST" << REMOTE_SCRIPT
        set -e

        # Install pip if needed
        if ! command -v pip3 &> /dev/null; then
            apt-get update && apt-get install -y python3-pip
        fi

        # Uninstall old version if present
        pip3 uninstall -y hostkit 2>/dev/null || true

        # Install new version (force reinstall to handle same-version updates)
        pip3 install --break-system-packages --force-reinstall --no-deps "/tmp/$WHEEL_BASENAME"

        # Create config directory if needed
        mkdir -p /etc/hostkit

        # Create default config if it doesn't exist
        if [ ! -f /etc/hostkit/config.yaml ]; then
            cat > /etc/hostkit/config.yaml << 'CONFIG'
# HostKit Configuration
data_dir: /var/lib/hostkit
log_dir: /var/log/hostkit
backup_dir: /backups
db_path: /var/lib/hostkit/hostkit.db

# Service defaults
postgres_host: localhost
postgres_port: 5432
redis_host: localhost
redis_port: 6379

# Project defaults
default_runtime: python
base_port: 8000
max_projects: 50
CONFIG
        fi

        # Ensure directories exist
        mkdir -p /var/lib/hostkit
        mkdir -p /var/log/hostkit
        mkdir -p /backups

        # Set permissions
        chown -R hostkit:hostkit /var/lib/hostkit 2>/dev/null || true
        chown -R hostkit:hostkit /var/log/hostkit 2>/dev/null || true

        # Verify installation
        echo ""
        echo "Installation complete!"
        hostkit --version

        # Auto-reindex documentation for hostkit query
        echo ""
        echo "Reindexing documentation..."
        if hostkit docs index --force 2>/dev/null; then
            echo "Documentation index updated."
        else
            echo "Note: Could not reindex docs (vector service may not be running)"
        fi
REMOTE_SCRIPT

    echo_info "Deployment complete!"
}

# Verify deployment
verify_deployment() {
    echo_info "Verifying deployment..."

    ssh "$VPS_HOST" << 'VERIFY_SCRIPT'
        echo "=== Version ==="
        hostkit --version

        echo ""
        echo "=== Help ==="
        hostkit --help

        echo ""
        echo "=== Status ==="
        hostkit status
VERIFY_SCRIPT
}

# Main
main() {
    echo "========================================"
    echo "   HostKit Deployment"
    echo "========================================"
    echo ""

    check_requirements
    build_wheel
    deploy_to_vps

    echo ""
    echo "========================================"
    echo "   Deployment Successful!"
    echo "========================================"
    echo ""
    echo "Run 'ssh $VPS_HOST hostkit status' to verify"
}

# Parse arguments
case "${1:-}" in
    --verify)
        verify_deployment
        ;;
    --build-only)
        check_requirements
        build_wheel
        echo_info "Wheel built at: $WHEEL_FILE"
        ;;
    --help)
        echo "Usage: $0 [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  --verify      Verify existing deployment"
        echo "  --build-only  Build wheel without deploying"
        echo "  --help        Show this help"
        echo ""
        echo "Environment variables:"
        echo "  VPS_HOST      SSH target (e.g., root@203.0.113.1)"
        echo "  VPS_PYTHON    Python command on VPS (default: python3)"
        ;;
    *)
        main
        ;;
esac
