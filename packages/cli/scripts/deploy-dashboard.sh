#!/bin/bash
# Dashboard Deployment Script
# Builds and deploys the Next.js dashboard to VPS

set -e

# Configuration
VPS_HOST="${VPS_HOST:-root@YOUR_VPS_IP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DASHBOARD_DIR="$PROJECT_DIR/dashboard"

# Guard against deploying with placeholder IP
if [[ "$VPS_HOST" == *"YOUR_VPS_IP"* ]]; then
    echo -e "\033[0;31m[ERROR]\033[0m VPS_HOST is not configured. Set VPS_HOST environment variable:"
    echo -e "\033[0;31m[ERROR]\033[0m   export VPS_HOST=root@<your-vps-ip>"
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

    if ! command -v npm &> /dev/null; then
        echo_error "npm is required but not installed"
        exit 1
    fi

    if ! command -v ssh &> /dev/null; then
        echo_error "ssh is required but not installed"
        exit 1
    fi

    if ! command -v rsync &> /dev/null; then
        echo_error "rsync is required but not installed"
        exit 1
    fi
}

# Build the Next.js app
build_dashboard() {
    echo_info "Building dashboard..."
    cd "$DASHBOARD_DIR"

    # Install dependencies if needed
    if [ ! -d "node_modules" ]; then
        echo_info "Installing dependencies..."
        npm install
    fi

    # Build
    npm run build

    # Check for standalone output
    # Note: Next.js creates nested path due to workspace detection
    # The path includes the workspace path from Documents/**HostKit**/dashboard/
    STANDALONE_DIR="$DASHBOARD_DIR/.next/standalone"

    # Find the actual app directory (handles the nested workspace path)
    APP_SOURCE=""
    if [ -d "$STANDALONE_DIR/Documents" ]; then
        # Nested workspace path exists
        APP_SOURCE=$(find "$STANDALONE_DIR" -type d -name "dashboard" | head -1)
    fi

    if [ -z "$APP_SOURCE" ] || [ ! -d "$APP_SOURCE" ]; then
        # Fallback to direct path
        if [ -d "$STANDALONE_DIR" ]; then
            APP_SOURCE="$STANDALONE_DIR"
        else
            echo_error "Failed to find standalone build output"
            exit 1
        fi
    fi

    echo_info "Build complete: $APP_SOURCE"
}

# Deploy to VPS
deploy_to_vps() {
    echo_info "Deploying dashboard to VPS ($VPS_HOST)..."
    cd "$DASHBOARD_DIR"

    # Find the standalone app source
    STANDALONE_DIR="$DASHBOARD_DIR/.next/standalone"
    APP_SOURCE=""

    if [ -d "$STANDALONE_DIR/Documents" ]; then
        # Handle nested workspace path (Documents/**HostKit**/dashboard/)
        APP_SOURCE=$(find "$STANDALONE_DIR" -type d -name "dashboard" | head -1)
    fi

    if [ -z "$APP_SOURCE" ] || [ ! -d "$APP_SOURCE" ]; then
        APP_SOURCE="$STANDALONE_DIR"
    fi

    # Deploy standalone build
    echo_info "Syncing standalone build..."
    rsync -avz --delete "$APP_SOURCE/" "$VPS_HOST:/home/dashboard/app/"

    # Deploy static files
    echo_info "Syncing static files..."
    rsync -avz --delete "$DASHBOARD_DIR/.next/static/" "$VPS_HOST:/home/dashboard/app/.next/static/"

    # Restart service
    echo_info "Restarting dashboard service..."
    ssh "$VPS_HOST" "systemctl restart hostkit-dashboard"

    echo_info "Deployment complete!"
}

# Verify deployment
verify_deployment() {
    echo_info "Verifying deployment..."

    ssh "$VPS_HOST" << 'VERIFY_SCRIPT'
        echo "=== Service Status ==="
        systemctl status hostkit-dashboard --no-pager | head -20

        echo ""
        echo "=== Health Check ==="
        curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health || echo "failed"
        echo ""
VERIFY_SCRIPT
}

# Main
main() {
    echo "========================================"
    echo "   Dashboard Deployment"
    echo "========================================"
    echo ""

    check_requirements
    build_dashboard
    deploy_to_vps

    echo ""
    echo "========================================"
    echo "   Deployment Successful!"
    echo "========================================"
    echo ""
    echo "Dashboard URL: https://dashboard.hostkit.dev"
}

# Parse arguments
case "${1:-}" in
    --verify)
        verify_deployment
        ;;
    --build-only)
        check_requirements
        build_dashboard
        echo_info "Build complete"
        ;;
    --deploy-only)
        check_requirements
        deploy_to_vps
        ;;
    --help)
        echo "Usage: $0 [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  --verify       Verify existing deployment"
        echo "  --build-only   Build without deploying"
        echo "  --deploy-only  Deploy without rebuilding"
        echo "  --help         Show this help"
        echo ""
        echo "Environment variables:"
        echo "  VPS_HOST      SSH target (e.g., root@203.0.113.1)"
        ;;
    *)
        main
        ;;
esac
