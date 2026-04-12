#!/bin/bash
set -e

echo "=== ApplicationStart: Building and starting containers ==="

DEPLOY_DIR="/home/ec2-user/prozpr"
cd "$DEPLOY_DIR"

# Build fresh images and start containers
docker compose build --no-cache
docker compose up -d

echo "Waiting for containers to become healthy..."

# Wait up to 120 seconds for backend health check
MAX_RETRIES=24
RETRY_INTERVAL=5
RETRIES=0

while [ $RETRIES -lt $MAX_RETRIES ]; do
    if docker compose ps --format json | grep -q '"Health":"healthy"'; then
        echo "Backend container is healthy!"
        break
    fi

    RETRIES=$((RETRIES + 1))
    echo "Waiting for backend to be healthy... (attempt $RETRIES/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

if [ $RETRIES -eq $MAX_RETRIES ]; then
    echo "WARNING: Backend did not become healthy within timeout"
    echo "Container status:"
    docker compose ps
    echo "Backend logs:"
    docker compose logs backend --tail 50
fi

echo "Running containers:"
docker compose ps

echo "=== ApplicationStart complete ==="
