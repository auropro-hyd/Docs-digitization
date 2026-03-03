#!/usr/bin/env bash
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "Pulling Ollama models from $OLLAMA_HOST..."

models=(
    "gemma2:9b"
    "gemma2:2b"
)

for model in "${models[@]}"; do
    echo "Pulling $model..."
    curl -s "$OLLAMA_HOST/api/pull" -d "{\"name\": \"$model\"}" | tail -1
    echo " Done."
done

echo "All models pulled."
