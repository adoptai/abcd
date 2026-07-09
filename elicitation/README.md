# Web API Spec Elicitation Agent

A Chrome extension + Python backend for recording enriched browser sessions (HAR + voice + structured events) that feed into the Specification Creation Assistant for API specification elicitation.

## Project Structure

```
├── backend/          Python (FastAPI) backend service
├── extension/        Chrome extension (Manifest V3)
└── scripts/          Dev and test scripts
```

## Quick Start

### 1. Start the backend

The backend runs inside the abcd Poetry venv — no separate install needed:

```bash
# From the abcd repo root
python cli/elicit.py backend start
```

Verify: `curl http://localhost:8000/health`

> For standalone setup (outside abcd) see [LOCAL_RUNBOOK.md](LOCAL_RUNBOOK.md).

### 2. Install the Chrome extension

1. Open Chrome and navigate to `chrome://extensions`
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked**
4. Select the `extension/` directory from this project

### 3. Test the connection

1. Click the extension icon in the Chrome toolbar
2. Click **Test Connection**
3. You should see: "Connected! Backend responded: pong"

### Smoke test (backend only)

```bash
./scripts/smoke-test.sh
```

## Development

### Backend

- Framework: FastAPI with uvicorn
- Python 3.11+
- Tests: `cd backend && pytest`

### Extension

- Manifest V3
- No build step required (vanilla JS)
- Reload extension from `chrome://extensions` after changes

## License

MIT
