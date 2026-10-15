#!/usr/bin/env python3
"""Minimal toy web app for end-to-end autopilot testing.

Features:
  - Login page with username/password (testuser / testpass)
  - Session-cookie auth
  - JSON API for notes CRUD
  - HTML pages rendered server-side
  - Runs on port 9111 by default

Usage:
    .venv/bin/python tests/toyapp/app.py [--port 9111]
"""

from __future__ import annotations

import argparse
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

USERS = {"testuser": "testpass"}

sessions: dict[str, str] = {}  # token -> username
notes: list[dict] = []  # {id, title, body, created_by, created_at}


def _reset_state() -> None:
    """Reset for test isolation."""
    sessions.clear()
    notes.clear()
    notes.append(
        {
            "id": str(uuid.uuid4()),
            "title": "Welcome note",
            "body": "This is a sample note created at startup.",
            "created_by": "system",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


_reset_state()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ToyApp", version="0.1.0")

SESSION_COOKIE = "toyapp_session"


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


def _get_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in sessions:
        return sessions[token]
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    OPEN_PATHS = {"/login", "/api/login", "/api/reset", "/health", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.OPEN_PATHS:
            return await call_next(request)
        user = _get_user(request)
        if not user:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        request.state.user = user
        return await call_next(request)


app.add_middleware(AuthMiddleware)


# ---------------------------------------------------------------------------
# HTML templates (inline for zero dependencies)
# ---------------------------------------------------------------------------

_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - ToyApp</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: system-ui, sans-serif; background: #f5f5f5; color: #333; }}
    .container {{ max-width: 720px; margin: 40px auto; padding: 0 20px; }}
    h1 {{ margin-bottom: 20px; }}
    .card {{ background: #fff; border-radius: 8px; padding: 24px; margin-bottom: 16px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    label {{ display: block; margin-bottom: 6px; font-weight: 600; }}
    input, textarea {{ width: 100%; padding: 8px 12px; border: 1px solid #ccc;
                       border-radius: 4px; margin-bottom: 16px; font-size: 14px; }}
    textarea {{ min-height: 80px; resize: vertical; }}
    button {{ padding: 10px 20px; background: #2563eb; color: #fff; border: none;
              border-radius: 4px; cursor: pointer; font-size: 14px; }}
    button:hover {{ background: #1d4ed8; }}
    .error {{ color: #dc2626; margin-bottom: 12px; }}
    .success {{ color: #16a34a; margin-bottom: 12px; }}
    nav {{ display: flex; gap: 16px; align-items: center; margin-bottom: 24px; }}
    nav a {{ color: #2563eb; text-decoration: none; }}
    .note {{ border-left: 3px solid #2563eb; padding-left: 12px; margin-bottom: 12px; }}
    .note h3 {{ margin-bottom: 4px; }}
    .note small {{ color: #888; }}
    .badge {{ background: #e5e7eb; padding: 2px 8px; border-radius: 12px; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="container">
    {body}
  </div>
</body>
</html>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(_BASE.format(title=title, body=body))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    user = _get_user(request)
    if user:
        return RedirectResponse("/", status_code=302)

    error_html = f'<p class="error">{error}</p>' if error else ""
    return _page(
        "Login",
        f"""
        <div class="card">
          <h1>Login</h1>
          {error_html}
          <form method="post" action="/api/login">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" placeholder="Username"
                   autocomplete="username" required>
            <label for="password">Password</label>
            <input type="password" id="password" name="password" placeholder="Password"
                   autocomplete="current-password" required>
            <button type="submit">Sign In</button>
          </form>
        </div>
        <p style="margin-top:12px; color:#888; font-size:13px;">
          Test credentials: <code>testuser</code> / <code>testpass</code>
        </p>
    """,
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = request.state.user
    notes_html = ""
    for note in reversed(notes):
        notes_html += f"""
        <div class="note">
          <h3>{note["title"]}</h3>
          <p>{note["body"]}</p>
          <small>by {note["created_by"]} at {note["created_at"][:19]}</small>
        </div>"""

    if not notes:
        notes_html = '<p style="color:#888;">No notes yet. Create one!</p>'

    return _page(
        "Dashboard",
        f"""
        <nav>
          <strong>ToyApp</strong>
          <a href="/">Dashboard</a>
          <a href="/notes/new">New Note</a>
          <span class="badge">Logged in as {user}</span>
          <a href="/api/logout" style="margin-left:auto; color:#dc2626;">Logout</a>
        </nav>
        <h1>Notes</h1>
        <div class="card">
          {notes_html}
        </div>
    """,
    )


@app.get("/notes/new", response_class=HTMLResponse)
async def new_note_page(request: Request, success: str = ""):
    user = request.state.user
    success_html = f'<p class="success">{success}</p>' if success else ""
    return _page(
        "New Note",
        f"""
        <nav>
          <strong>ToyApp</strong>
          <a href="/">Dashboard</a>
          <a href="/notes/new">New Note</a>
          <span class="badge">Logged in as {user}</span>
          <a href="/api/logout" style="margin-left:auto; color:#dc2626;">Logout</a>
        </nav>
        <h1>Create Note</h1>
        <div class="card">
          {success_html}
          <form method="post" action="/api/notes">
            <label for="title">Title</label>
            <input type="text" id="title" name="title" placeholder="Note title" required>
            <label for="body">Body</label>
            <textarea id="body" name="body" placeholder="Write your note here..." required></textarea>
            <button type="submit">Save Note</button>
          </form>
        </div>
    """,
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@app.post("/api/login")
async def api_login(username: str = Form(...), password: str = Form(...)):
    if USERS.get(username) != password:
        return RedirectResponse("/login?error=Invalid+credentials", status_code=302)
    token = secrets.token_hex(32)
    sessions[token] = username
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=3600)
    return response


@app.get("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.pop(token, None)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/notes")
async def api_list_notes(request: Request):
    return {"notes": notes, "count": len(notes)}


@app.post("/api/notes")
async def api_create_note(request: Request, title: str = Form(...), body: str = Form(...)):
    user = request.state.user
    note = {
        "id": str(uuid.uuid4()),
        "title": title,
        "body": body,
        "created_by": user,
        "created_at": datetime.now(UTC).isoformat(),
    }
    notes.append(note)
    return RedirectResponse(f"/notes/new?success=Note+created:+{title}", status_code=302)


@app.get("/api/notes/{note_id}")
async def api_get_note(note_id: str, request: Request):
    for note in notes:
        if note["id"] == note_id:
            return note
    raise HTTPException(404, "Note not found")


@app.delete("/api/notes/{note_id}")
async def api_delete_note(note_id: str, request: Request):
    for i, note in enumerate(notes):
        if note["id"] == note_id:
            notes.pop(i)
            return {"deleted": True, "id": note_id}
    raise HTTPException(404, "Note not found")


@app.post("/api/reset")
async def api_reset():
    """Reset all state (for test isolation)."""
    _reset_state()
    return {"reset": True}


@app.get("/health")
async def health():
    return {"status": "ok", "app": "toyapp", "notes_count": len(notes)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="ToyApp — test fixture for NoUI autopilot")
    parser.add_argument("--port", type=int, default=9111, help="Port (default: 9111)")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"ToyApp starting on http://{args.host}:{args.port}")
    print("Login: testuser / testpass")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
