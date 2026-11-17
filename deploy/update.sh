#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date -Iseconds)] New commits found ($LOCAL -> $REMOTE). Updating..."
    git pull origin main
    docker compose up -d --build
    echo "[$(date -Iseconds)] Update complete."
fi
