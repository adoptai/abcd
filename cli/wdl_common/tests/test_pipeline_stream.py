#!/usr/bin/env python3
"""Offline tests for ``pipeline_stream.stream_test_run``.

No network and no real ``httpx`` are used: a fake httpx object feeds
pre-scripted NDJSON connections to the streamer so we can assert behaviour
deterministically — including the reconnect-on-"Stream not found" path that
makes the pipeline test-run streaming actually work while the workflow
session is still initialising.

Run directly (no pytest required):

    python cli/wdl_common/tests/test_pipeline_stream.py

It is also pytest-compatible (``test_*`` functions with asserts).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Make ``cli`` importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from cli.wdl_common import pipeline_stream as ps

# ---------------------------------------------------------------------------
# Fake httpx
# ---------------------------------------------------------------------------


class _FakeResponse:
    """One NDJSON connection: an async context manager yielding ``lines``."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b"error body"


class _FakeClient:
    def __init__(self, connections: list[_FakeResponse]) -> None:
        self._connections = connections

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def stream(self, method: str, url: str, headers: dict | None = None) -> _FakeResponse:
        assert self._connections, "stream() called more times than scripted connections"
        return self._connections.pop(0)


class _FakeHttpx:
    """Minimal stand-in for the ``httpx`` module used by pipeline_stream."""

    def __init__(self, connections: list[_FakeResponse]) -> None:
        # Shared across every AsyncClient() created during the retry loop.
        self._connections = connections
        self.ReadTimeout = type("ReadTimeout", (Exception,), {})
        self.RemoteProtocolError = type("RemoteProtocolError", (Exception,), {})

    def Timeout(self, **kwargs: object) -> dict:  # noqa: N802 - mirror httpx API
        return kwargs

    def AsyncClient(self, **kwargs: object) -> _FakeClient:  # noqa: N802 - mirror httpx API
        return _FakeClient(self._connections)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _run(connections: list[_FakeResponse]) -> dict | None:
    """Run stream_test_run against scripted connections, with sleeps stubbed."""
    fake = _FakeHttpx(connections)
    orig_httpx = ps.httpx  # type: ignore[attr-defined]
    orig_sleep = asyncio.sleep

    async def _no_sleep(_seconds: float) -> None:  # avoid the 3s reconnect wait
        return None

    ps.httpx = fake  # type: ignore[attr-defined,assignment]
    asyncio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        return asyncio.run(
            ps.stream_test_run(
                "http://fake/stream/pipe-1",
                headers={"Authorization": "Bearer x"},
                pipeline_id="pipe-1",
                timeout=30,
                log=lambda _m: None,
            )
        )
    finally:
        ps.httpx = orig_httpx  # type: ignore[attr-defined]
        asyncio.sleep = orig_sleep  # type: ignore[assignment]


def _final_output(status: str, result: object) -> str:
    return json.dumps(
        {"event_name": "scheduling-test-run-output", "status": status, "result": result}
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_returns_final_output() -> None:
    """Step-progress events then a terminal output → returns the final payload."""
    conn = _FakeResponse(
        [
            json.dumps(
                {"event_name": "scheduling-test-run-step-progress", "activity": "step_started"}
            ),
            ":keepalive",
            _final_output("passed", {"rows": 3}),
        ]
    )
    final = _run([conn])
    assert final is not None, "expected a final result"
    assert final["status"] == "passed"
    assert final["result"] == {"rows": 3}


def test_chunked_output_is_reassembled() -> None:
    """status=streaming chunks are concatenated then JSON-parsed on the terminal event."""
    conn = _FakeResponse(
        [
            _final_output("streaming", '{"part":'),
            _final_output("streaming", ' "value"}'),
            _final_output("passed", None),
        ]
    )
    final = _run([conn])
    assert final is not None
    assert final["status"] == "passed"
    assert final["result"] == {"part": "value"}, f"got {final['result']!r}"


def test_reconnects_on_stream_not_found() -> None:
    """First connection returns a transient 'Stream not found', second succeeds.

    This is the behaviour the uncommitted change added: without the reconnect
    loop the streamer would bail before the workflow session is ready.
    """
    conn_not_ready = _FakeResponse([json.dumps({"error": "Stream not found"})])
    conn_ready = _FakeResponse([_final_output("passed", {"ok": True})])
    final = _run([conn_not_ready, conn_ready])
    assert final is not None, "expected reconnect to recover the final result"
    assert final["status"] == "passed"
    assert final["result"] == {"ok": True}


def test_real_error_raises() -> None:
    """A non-'not found' error is surfaced, not retried."""
    conn = _FakeResponse([json.dumps({"error": "boom", "message": "kaboom"})])
    try:
        _run([conn])
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for a real stream error")


def test_server_end_event_returns_accumulated() -> None:
    """A __end event ends the stream and returns whatever was accumulated (None here)."""
    conn = _FakeResponse([json.dumps({"event_name": "__end", "reason": "done"})])
    final = _run([conn])
    assert final is None


def main() -> int:
    tests = [
        test_happy_path_returns_final_output,
        test_chunked_output_is_reassembled,
        test_reconnects_on_stream_not_found,
        test_real_error_raises,
        test_server_end_event_returns_accumulated,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:  # noqa: BLE001 - test runner
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
