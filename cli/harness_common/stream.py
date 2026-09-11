#!/usr/bin/env python3
"""
NDJSON turn-stream reader: pretty-prints harness events live and persists the
raw stream to disk. This is the "go turn by turn" visibility FDEs don't get
from a local Claude Code session -- every tool call, tool result, and error
the harness actually produced, not what we'd guess it would produce.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_TERMINAL_EVENT_TYPES = {"harness_turn_complete", "harness_error"}


def _format_event(envelope: dict[str, Any]) -> str | None:
    event = envelope.get("event") or {}
    etype = event.get("type")
    data = event.get("data") or {}

    if etype in ("text_delta", "thinking_delta"):
        return None  # printed inline by the caller, not as a status line

    if etype == "tool_use":
        name = data.get("name") or event.get("name")
        return f"  \U0001f527 tool_use: {name}"
    if etype == "tool_result":
        name = data.get("name") or event.get("name")
        marker = "❌" if data.get("is_error") else "✅"
        return f"  {marker} tool_result: {name}"
    if etype == "artifact":
        return f"  \U0001f4ce artifact: {data.get('name', data)}"
    if etype == "harness_status":
        return f"  ℹ️  status: {data}"
    if etype == "harness_error":
        return f"  \U0001f6d1 harness_error: {data}"
    if etype == "harness_turn_complete":
        return f"  \U0001f3c1 turn_complete: stop_reason={data.get('stop_reason')}"
    if etype in (
        "harness_context_usage",
        "harness_context_cleared",
        "harness_context_compacted",
    ):
        return f"  \U0001f4ca {etype}: {data}"
    if etype:
        return f"  · {etype}"
    return None


def stream_and_persist(
    events: Iterator[dict[str, Any]],
    ndjson_path: Path,
    quiet: bool = False,
) -> dict[str, Any]:
    """
    Consume a turn's NDJSON event iterator: print a live summary, persist the
    raw envelopes to ``ndjson_path``, and return the terminal event's data
    (stop_reason etc.) once seen.

    Returns an empty dict if the stream ended without a terminal event
    (connection dropped, etc.) -- callers should treat that as "unknown, go
    check /status".
    """
    ndjson_path.parent.mkdir(parents=True, exist_ok=True)
    terminal_data: dict[str, Any] = {}
    have_pending_text = False

    with ndjson_path.open("w") as raw_out:
        for envelope in events:
            raw_out.write(json.dumps(envelope) + "\n")

            event = envelope.get("event") or {}
            etype = event.get("type")
            data = event.get("data") or {}

            if etype == "text_delta" and not quiet:
                print(data.get("text", ""), end="", flush=True)
                have_pending_text = True
                continue

            line = _format_event(envelope)
            if line and not quiet:
                if have_pending_text:
                    print()
                    have_pending_text = False
                print(line)

            if etype in _TERMINAL_EVENT_TYPES:
                terminal_data = {"type": etype, **data}

    if have_pending_text and not quiet:
        print()

    return terminal_data
