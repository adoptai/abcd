#!/bin/bash
# =============================================================================
# One-time setup for the AdoptAI Chrome Extension test harness.
# Run this from the ce_harness directory.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up AdoptAI Extension test harness..."

# Check prerequisites
command -v node >/dev/null 2>&1 || { echo "Error: Node.js is required."; exit 1; }
command -v google-chrome >/dev/null 2>&1 || { echo "Error: Google Chrome is required."; exit 1; }

# Init npm and install deps
if [ ! -f package.json ]; then
  npm init -y > /dev/null 2>&1
fi

npm install playwright ws 2>&1 | tail -3

# Make start.sh executable
chmod +x start.sh 2>/dev/null || true

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Set ADOPT_EXTENSION_PATH env var (or edit start.sh) to your extension's dist/ directory"
echo "  2. Run start.sh from YOUR terminal (not from Claude)"
echo "  3. Run: python cli/ce_test.py status"
