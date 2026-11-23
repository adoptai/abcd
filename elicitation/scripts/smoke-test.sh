#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

echo "Smoke test against: ${BASE_URL}"
echo "================================"
echo ""

# Test 1: Health check
echo "1. GET /health"
HEALTH=$(curl -s -w "\n%{http_code}" "${BASE_URL}/health")
HTTP_CODE=$(echo "$HEALTH" | tail -1)
BODY=$(echo "$HEALTH" | head -1)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "   PASS (200) — ${BODY}"
else
    echo "   FAIL (${HTTP_CODE}) — ${BODY}"
    exit 1
fi

echo ""

# Test 2: Ping endpoint
echo "2. POST /sessions/ping"
PING=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/sessions/ping" \
    -H "Content-Type: application/json" \
    -d '{"message": "smoke test"}')
HTTP_CODE=$(echo "$PING" | tail -1)
BODY=$(echo "$PING" | head -1)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "   PASS (200) — ${BODY}"
else
    echo "   FAIL (${HTTP_CODE}) — ${BODY}"
    exit 1
fi

echo ""
echo "================================"
echo "All smoke tests passed."
