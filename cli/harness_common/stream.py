#!/usr/bin/env python3
"""
NDJSON turn-stream reader: pretty-prints harness events live and persists the
raw stream to disk. This is the "go turn by turn" visibility FDEs don't get
from a local Claude Code session -- every tool call, tool result, and error
the harness actually produced, not what we'd guess it would produce.

Wire shape (confirmed against a real adoptwebui stream, NOT the internal
HarnessEvent taxonomy narrative in agent_harness_architecture.md -- that doc
describes the Temporal-internal event stream; the wire envelope the proxy
actually forwards looks like this):

    {
      "source": "redis" | "transcript" | "keepalive",
      "redis_id" | "cursor": ...,
      "event": {
        "event_name": "block_delta" | "harness_turn_complete" | "harness_error"
                       | "harness_step_complete" | "harness_context_usage"
                       | "harness_sandbox_ready" | ...,
        "type": "response",   # constant envelope-kind marker -- NOT the discriminator
        "data": {...}         # event_name == "block_delta" nests a "block" dict here,
                               # whose OWN "type" is text_delta/tool_use/tool_result/chip/final
      }
    }

So: dispatch on ``event["event_name"]``, not ``event["type"]``. For
``block_delta``, dispatch again on ``data["block"]["type"]``.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_TERMINAL_EVENT_NAMES = {"harness_turn_complete", "harness_error"}


def _format_block(block: dict[str, Any]) -> str | None:
    btype = block.get("type")

    if btype in ("text_delta", "thinking_delta"):
        return None  # printed inline by the caller, not as a status line
    if btype == "tool_use":
        return f"  \U0001f527 tool_use: {block.get('name')}"
    if btype == "tool_result":
        marker = "❌" if block.get("is_error") else "✅"
        return f"  {marker} tool_result"
    if btype == "chip":
        return f"  · chip: {block.get('title')} [{block.get('state')}]"
    if btype == "final":
        return "  \U0001f3c1 final answer rendered"
    if btype:
        return f"  · block:{btype}"
    return None


def _format_event(envelope: dict[str, Any]) -> str | None:
    event = envelope.get("event") or {}
    name = event.get("event_name")
    data = event.get("data") or {}

    if name == "block_delta":
        block = data.get("block") or {}
        return _format_block(block)
    if name == "harness_sandbox_ready":
        return "  \U0001f4e6 sandbox ready"
    if name == "harness_error":
        return f"  \U0001f6d1 harness_error: {data}"
    if name == "harness_turn_complete":
        return f"  \U0001f3c1 turn_complete: stop_reason={data.get('stop_reason')}"
    if name in ("harness_context_usage", "harness_step_complete"):
        return f"  \U0001f4ca {name}: {data}"
    if name:
        return f"  · {name}"
    return None


def _inline_text(envelope: dict[str, Any]) -> str | None:
    """Return delta text to print inline (streamed token-by-token), or None."""
    event = envelope.get("event") or {}
    if event.get("event_name") != "block_delta":
        return None
    block = (event.get("data") or {}).get("block") or {}
    if block.get("type") == "text_delta":
        return block.get("text", "")
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
            name = event.get("event_name")
            data = event.get("data") or {}

            inline = _inline_text(envelope)
            if inline is not None and not quiet:
                print(inline, end="", flush=True)
                have_pending_text = True
                continue

            line = _format_event(envelope)
            if line and not quiet:
                if have_pending_text:
                    print()
                    have_pending_text = False
                print(line)

            if name in _TERMINAL_EVENT_NAMES:
                terminal_data = {"event_name": name, **data}

    if have_pending_text and not quiet:
        print()

    return terminal_data
