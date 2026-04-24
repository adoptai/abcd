#!/usr/bin/env python3
"""HTTP NDJSON streaming helper for pipeline test runs.

Streams events from the BFF's unified ``GET /stream/{pipeline_id}`` endpoint
(``application/x-ndjson``) and yields each parsed event to the caller.

Wire shape (subset, source-agnostic):

    {"event_name": "scheduling-test-run-step-progress",
     "activity":   "step_started",
     "timestamp":  "...",
     "conversation_id": "...",
     "source":     "pusher" | "redis",
     ...}

The caller must already have triggered the test run (e.g. via
``client.test_run(...)``) before invoking this; events buffer briefly
server-side so subscribing immediately after triggering is fine.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - dependency check
    httpx = None  # type: ignore[assignment]


SUBSCRIBE_TIMEOUT_S = 10
DEFAULT_TIMEOUT_S = 300


class StreamUnavailableError(RuntimeError):
    """Raised when ``httpx`` is not installed."""


def _ensure_httpx() -> None:
    if httpx is None:
        raise StreamUnavailableError(
            "The 'httpx' library is required for real-time streaming.\n"
            "Install it with: pip install httpx"
        )


async def stream_test_run(
    stream_url: str,
    headers: dict[str, str],
    pipeline_id: str,
    timeout: int = DEFAULT_TIMEOUT_S,
    log: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    """Stream a pipeline test run's events from the BFF ``/stream`` endpoint.

    Returns the final ``scheduling-test-run-output`` payload (the test result)
    or ``None`` if the stream timed out / closed before completion.
    """
    _ensure_httpx()

    chunks: list[str] = []
    final_result: dict[str, Any] | None = None

    request_timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    async with (
        httpx.AsyncClient(timeout=request_timeout) as client,
        client.stream("GET", stream_url, headers=headers) as response,
    ):
        if response.status_code != 200:
            body = await response.aread()
            raise RuntimeError(
                f"Stream connect failed: HTTP {response.status_code} "
                f"{body.decode('utf-8', errors='replace')[:300]}"
            )

        log(f"   📡 Subscribed to stream for pipeline {pipeline_id}, waiting for events…")

        deadline = asyncio.get_event_loop().time() + timeout
        try:
            async for raw_line in response.aiter_lines():
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    log("   ⏰ Stream timed out before completion")
                    return None
                if not raw_line:
                    continue
                if raw_line.startswith(":"):
                    # NDJSON keepalive comment, ignore.
                    continue

                try:
                    msg = json.loads(raw_line)
                except (ValueError, TypeError):
                    continue

                if not isinstance(msg, dict):
                    continue

                if msg.get("error"):
                    raise RuntimeError(
                        f"Stream error: {msg.get('error')}: {msg.get('message') or msg.get('details') or ''}"
                    )
                if msg.get("event_name") == "__end":
                    log(f"   ⏹️  Stream ended by server: {msg.get('reason', 'unknown')}")
                    return final_result

                event_name = msg.get("event_name") or msg.get("activity") or ""
                payload = msg

                # Pusher-relayed payloads sometimes nest the actual data under
                # ``data`` (when the producer wraps with metadata); unwrap once
                # for known event names so the chunked-output assembly logic
                # below works with both shapes.
                inner = msg.get("data")
                if isinstance(inner, dict) and (
                    "status" in inner or "result" in inner or "step_id" in inner
                ):
                    payload = inner

                if event_name in (
                    "scheduling-test-run-step-progress",
                    "wdl_step_progress",
                ):
                    _log_step_progress(payload, log)
                    continue

                if event_name in (
                    "wdl_execution_test_mode",
                    "wdl_execution_completed",
                    "wdl_execution_failed",
                ):
                    final_result = payload if isinstance(payload, dict) else {"result": payload}
                    _log_final_result(final_result, log)
                    return final_result

                if event_name == "scheduling-test-run-output":
                    status = payload.get("status") if isinstance(payload, dict) else None
                    if status in ("started", "streaming"):
                        if isinstance(payload, dict):
                            chunks.append(str(payload.get("result") or ""))
                        continue
                    if chunks:
                        assembled = "".join(chunks)
                        try:
                            payload["result"] = json.loads(assembled)
                        except (ValueError, TypeError):
                            payload["result"] = assembled
                        chunks.clear()
                    final_result = payload if isinstance(payload, dict) else {"result": payload}
                    _log_final_result(final_result, log)
                    return final_result

        except (httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            log(f"   🔌 Stream closed by server: {exc}")
            return final_result

    return final_result


def _log_step_progress(d: Any, log: Callable[[str], None]) -> None:
    if not isinstance(d, dict):
        return
    status = str(d.get("status", ""))
    label = d.get("step_label") or d.get("operation") or d.get("step_id") or ""
    idx = d.get("step_index", "?")
    total = d.get("total_steps", "?")

    icon = {
        "completed": "✅",
        "failed": "❌",
        "running": "⏳",
        "started": "▶️",
    }.get(status, "•")

    log(f"   {icon} Step {idx}/{total}: {label} [{status}]")
    if status == "failed" and d.get("error"):
        log(f"      ↳ {d['error']}")


def _log_final_result(payload: dict[str, Any], log: Callable[[str], None]) -> None:
    status = payload.get("status", "?")
    icon = "✅" if status in ("passed", "success", "completed") else "❌"
    log(f"\n   {icon} Test run finished — status={status}")
    output = payload.get("output") or payload.get("result")
    if output:
        rendered = output if isinstance(output, str) else json.dumps(output, indent=2)
        if len(rendered) > 2000:
            rendered = rendered[:2000] + "\n   … [truncated]"
        log(f"   Output:\n{rendered}")
    if payload.get("error"):
        log(f"   Error: {payload['error']}")


def run_stream_blocking(
    stream_url: str,
    headers: dict[str, str],
    pipeline_id: str,
    timeout: int = DEFAULT_TIMEOUT_S,
    log: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    """Synchronous wrapper around :func:`stream_test_run` for CLI use."""
    try:
        return asyncio.run(
            stream_test_run(
                stream_url,
                headers=headers,
                pipeline_id=pipeline_id,
                timeout=timeout,
                log=log,
            )
        )
    except StreamUnavailableError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # pragma: no cover - surfaced to caller
        print(f"⚠️  Stream error: {exc}", file=sys.stderr)
        return None
