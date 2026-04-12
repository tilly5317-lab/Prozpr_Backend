#!/bin/bash
set -e

echo "=== BeforeInstall: Preparing environment ==="

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    yum update -y
    yum install -y docker
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ec2-user
fi

# Install Docker Compose v2 plugin if not present
if ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose plugin..."
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# Ensure Docker daemon is running
if ! systemctl is-active --quiet docker; then
    echo "Starting Docker daemon..."
    systemctl start docker
fi

# Stop and remove existing containers if running
DEPLOY_DIR="/home/ec2-user/prozpr"
if [ -d "$DEPLOY_DIR" ] && [ -f "$DEPLOY_DIR/docker-compose.yml" ]; then
    echo "Stopping existing containers..."
    cd "$DEPLOY_DIR"
    docker compose down --remove-orphans || true
fi

# Clean up old deployment files (CodeDeploy will place new ones)
if [ -d "$DEPLOY_DIR" ]; then
    echo "Cleaning up old deployment..."
    rm -rf "$DEPLOY_DIR"
fi

mkdir -p "$DEPLOY_DIR"

echo "=== BeforeInstall complete ==="
