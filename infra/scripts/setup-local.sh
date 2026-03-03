#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

echo "=== Auto Transcription - Local Dev Setup ==="

# 1. Start Docker services
echo "[1/4] Starting PostgreSQL and Ollama via Docker Compose..."
cd "$BACKEND_DIR"
docker compose up -d

# 2. Wait for PostgreSQL
echo "[2/4] Waiting for PostgreSQL..."
until docker compose exec -T postgres pg_isready -U postgres 2>/dev/null; do
    sleep 1
done
echo "PostgreSQL ready."

# 3. Pull Ollama models
echo "[3/4] Pulling Ollama models (this may take a while)..."
docker compose exec ollama ollama pull gemma2:9b || echo "Ollama model pull skipped (can be done manually)"

# 4. Install Python dependencies
echo "[4/4] Installing Python dependencies..."
cd "$BACKEND_DIR"
pip install -e ".[dev]"

echo ""
echo "=== Setup complete! ==="
echo "Start the backend:  cd backend && uvicorn app.main:app --reload"
echo "Start the frontend: cd frontend && npm run dev"
