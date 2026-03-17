#!/usr/bin/env bash
# Healthcheck for wolfsclaw-telegram bot
set -euo pipefail

CONTAINER="wolfsclaw-telegram"

echo "=== wolfsclaw-telegram Healthcheck ==="

# Check container is running
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "✅ Container is running"
    echo ""
    echo "--- Last 20 log lines ---"
    docker logs --tail 20 "$CONTAINER"
    echo ""
    echo "--- Container stats ---"
    docker stats --no-stream "$CONTAINER"
else
    echo "❌ Container is NOT running"
    echo ""
    echo "--- Last 50 log lines ---"
    docker logs --tail 50 "$CONTAINER" 2>/dev/null || echo "  No logs available"
    echo ""
    echo "Restart with: docker compose up -d"
    exit 1
fi
