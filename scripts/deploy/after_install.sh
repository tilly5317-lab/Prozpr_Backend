#!/bin/bash
set -e

echo "=== AfterInstall: Setting up application ==="

DEPLOY_DIR="/home/ec2-user/prozpr"
cd "$DEPLOY_DIR"

# Set correct ownership
chown -R ec2-user:ec2-user "$DEPLOY_DIR"

# Ensure the .env file exists for the backend
if [ ! -f "$DEPLOY_DIR/backend/.env" ]; then
    if [ -f "$DEPLOY_DIR/backend/.env.production" ]; then
        cp "$DEPLOY_DIR/backend/.env.production" "$DEPLOY_DIR/backend/.env"
        echo "Copied .env.production -> .env"
    elif [ -f "/home/ec2-user/.env.prozpr" ]; then
        cp "/home/ec2-user/.env.prozpr" "$DEPLOY_DIR/backend/.env"
        echo "Copied env from /home/ec2-user/.env.prozpr -> backend/.env"
    else
        echo "WARNING: No .env file found for backend!"
        echo "Place your environment file at /home/ec2-user/.env.prozpr on the EC2 instance."
        exit 1
    fi
fi

# Make all deploy scripts executable
chmod +x "$DEPLOY_DIR/scripts/deploy/"*.sh

# Prune old Docker images to free disk space
echo "Pruning unused Docker images..."
docker image prune -f || true

echo "=== AfterInstall complete ==="
