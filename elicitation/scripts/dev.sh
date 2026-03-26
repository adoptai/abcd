#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."
BACKEND_DIR="${ROOT_DIR}/backend"
VENV_DIR="${ROOT_DIR}/.venv"

echo "=== Elicitation Agent Backend ==="
echo ""

# Create venv if it doesn't exist
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment with uv..."
    uv venv "$VENV_DIR" --python 3.11
    echo ""
fi

# Install/update deps
echo "Installing dependencies..."
VIRTUAL_ENV="$VENV_DIR" uv pip install -e "${BACKEND_DIR}[dev]" -q
echo ""

# Activate for uvicorn
source "$VENV_DIR/bin/activate"

cd "$BACKEND_DIR"

echo "Starting uvicorn on http://localhost:8000"
echo "Press Ctrl+C to stop"
echo ""

uvicorn app.main:app --reload --port 8000
