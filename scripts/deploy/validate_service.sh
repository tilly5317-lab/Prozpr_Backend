#!/bin/bash
set -e

echo "=== ValidateService: Checking application health ==="

MAX_RETRIES=12
RETRY_INTERVAL=5
RETRIES=0

# Check that the frontend (Nginx) is responding on port 80
while [ $RETRIES -lt $MAX_RETRIES ]; do
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:80/ || echo "000")

    if [ "$HTTP_STATUS" = "200" ]; then
        echo "Frontend is responding (HTTP $HTTP_STATUS)"
        break
    fi

    RETRIES=$((RETRIES + 1))
    echo "Frontend not ready (HTTP $HTTP_STATUS), retrying... ($RETRIES/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

if [ "$HTTP_STATUS" != "200" ]; then
    echo "FAILED: Frontend is not responding after $MAX_RETRIES attempts"
    docker compose -f /home/ec2-user/prozpr/docker-compose.yml logs frontend --tail 30
    exit 1
fi

# Check that the backend health endpoint responds through the Nginx proxy
BACKEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:80/api/v1/health || echo "000")

if [ "$BACKEND_STATUS" = "200" ]; then
    echo "Backend health check passed (HTTP $BACKEND_STATUS)"
else
    echo "WARNING: Backend health via proxy returned HTTP $BACKEND_STATUS"
    echo "Checking backend directly on port 8000..."
    DIRECT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health || echo "000")
    if [ "$DIRECT_STATUS" = "200" ]; then
        echo "Backend is healthy on port 8000 (HTTP $DIRECT_STATUS)"
    else
        echo "FAILED: Backend health check failed (HTTP $DIRECT_STATUS)"
        docker compose -f /home/ec2-user/prozpr/docker-compose.yml logs backend --tail 30
        exit 1
    fi
fi

echo "=== ValidateService: All checks passed ==="
