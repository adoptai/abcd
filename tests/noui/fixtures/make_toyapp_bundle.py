#!/usr/bin/env python3
"""Regenerate ``toyapp_notes_bundle.json`` -- a real, public HAR capture bundle.

Records an actual Chromium session against the bundled toy app
(tests/noui/toyapp) with Playwright: sign in, open the dashboard, create a note,
and then exercise the JSON API in-page the way a single-page app would (list,
read, delete), plus a /health keepalive that a real app's poller would make --
the noise the generalize pass is expected to prune.

The result has the shape Tabby drains into a capture bundle
(``{recording_mode, har, click_events, url_events}``) and contains no customer
data: the host is normalized to ``https://toyapp.example.test`` and the random
session token to a fixed placeholder, so the fixture is stable across runs.

Needs the ``ce`` group (playwright + a cached Chromium) and fastapi/uvicorn:

    poetry install --with ce,test && poetry run pip install fastapi uvicorn
    poetry run python tests/noui/fixtures/make_toyapp_bundle.py
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "toyapp_notes_bundle.json"
PUBLIC_ORIGIN = "https://toyapp.example.test"
FIXED_SESSION = "toyapp-session-fixture"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(port: int) -> None:
    import uvicorn

    sys.path.insert(0, str(HERE.parent))
    from toyapp.app import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record() -> dict:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    origin = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)

    clicks: list[dict] = []
    urls: list[dict] = []
    seq = 0

    def nxt() -> int:
        nonlocal seq
        seq += 1
        return seq

    with tempfile.TemporaryDirectory() as tmp:
        har_path = Path(tmp) / "capture.har"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(record_har_path=str(har_path), record_har_content="embed")
            page = ctx.new_page()
            last = "about:blank"

            def nav_event() -> None:
                nonlocal last
                if page.url != last:
                    urls.append(
                        {"from_url": last, "to_url": page.url, "seq": nxt(), "timestamp": _now()}
                    )
                    last = page.url

            def act(kind: str, selector: str, text: str = "", value: str | None = None) -> None:
                ev = {
                    "event_type": kind,
                    "selector": selector,
                    "tag_name": selector.split("#")[0].split("[")[0].upper() or "INPUT",
                    "text_content": text,
                    "url": page.url,
                    "seq": nxt(),
                    "timestamp": _now(),
                    "event_time": _now(),
                }
                if value is not None:
                    ev["value"] = value
                clicks.append(ev)

            page.goto(f"{origin}/login")
            nav_event()
            act("click", "input#username")
            page.fill("#username", "testuser")
            act("change", "input#username", value="testuser")
            page.fill("#password", "testpass")
            act("change", "input#password", value="<redacted>")
            act("click", "button[type=submit]", text="Sign In")
            page.click("button[type=submit]")
            page.wait_for_url(f"{origin}/")
            nav_event()

            act("click", "a[href='/notes/new']", text="New Note")
            page.click("a[href='/notes/new']")
            page.wait_for_url(f"{origin}/notes/new")
            nav_event()
            page.fill("#title", "Quarterly plan")
            act("change", "input#title", value="Quarterly plan")
            page.fill("#body", "Draft the quarterly plan")
            act("change", "textarea#body", value="Draft the quarterly plan")
            act("click", "button[type=submit]", text="Save Note")
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
            nav_event()

            # The SPA-style JSON calls a real front end makes after the form posts.
            notes = page.evaluate("fetch('/api/notes').then(r => r.json())")
            note_id = notes["notes"][-1]["id"]
            page.evaluate(f"fetch('/api/notes/{note_id}').then(r => r.json())")
            page.evaluate("fetch('/health').then(r => r.json())")
            page.evaluate("fetch('/health').then(r => r.json())")
            page.evaluate(
                f"fetch('/api/notes/{note_id}', {{method: 'DELETE'}}).then(r => r.json())"
            )
            page.wait_for_timeout(300)
            ctx.close()  # flushes the HAR
            browser.close()
        har = json.loads(har_path.read_text())

    bundle = {
        "recording_mode": "workflow",
        "har": har,
        "click_events": clicks,
        "url_events": urls,
        "download_events": [],
    }
    text = json.dumps(bundle)
    text = text.replace(origin, PUBLIC_ORIGIN).replace(f"127.0.0.1:{port}", "toyapp.example.test")
    text = text.replace("127.0.0.1", "203.0.113.10")  # RFC 5737 documentation address
    text = re.sub(r"toyapp_session=[0-9a-f]{64}", f"toyapp_session={FIXED_SESSION}", text)
    text = re.sub(r'("value":\s*")[0-9a-f]{64}(")', rf"\g<1>{FIXED_SESSION}\g<2>", text)
    return json.loads(text)


def main() -> int:
    bundle = record()
    entries = bundle["har"]["log"]["entries"]
    OUT.write_text(json.dumps(bundle, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(entries)} HAR entries, {len(bundle['click_events'])} interactions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
