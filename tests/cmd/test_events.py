"""The command-event sink (R16), abcd events flush and abcd events session."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import cli.abcd as abcd_main
import cli.builder_events as be
from cli.builder_common.orgdb import InsertResult
from cli.core import events as core_events
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


def _event(n: int, **kw: Any) -> dict[str, Any]:
    return {
        "ts": f"2026-10-27T09:{n:02d}:00+00:00",
        "command": "eval run",
        "env": "acme",
        "mode": "local",
        "ok": True,
        "exit_code": 0,
        "duration_ms": 1500,
        "args": {"agent": "prep", "token": "***"},
        **kw,
    }


def _buffer(ops: OpsEnv, *events: dict[str, Any]) -> None:
    path = be.events_path(ops.ws)
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_sink_is_registered_and_buffers_through_the_core_writer(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    abcd_main.build_registry()
    assert be._sink in core_events._sinks
    code, _ = run("kg", "impact", "nothing")  # any command: the core wrapper writes the line
    lines = be.events_path(ops.ws).read_text().splitlines()
    assert json.loads(lines[-1])["command"] == "kg impact"
    assert (
        ops.bff.rows[ops.db.table_id("bld_dev_events")] == []
    )  # below the threshold: stays buffered


def test_sink_flushes_a_batch_once_enough_events_pile_up(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABCD_EVENTS_UPLOAD", "1")
    monkeypatch.setattr(be, "SINK_THRESHOLD", 3)
    monkeypatch.setattr(be, "_client_for_env", lambda env: ops.db)
    _buffer(ops, _event(1), _event(2))
    be._sink({"env": "acme"})
    assert ops.bff.rows[ops.db.table_id("bld_dev_events")] == []
    _buffer(ops, _event(3))
    be._sink({"env": "acme"})
    rows = ops.db.list_all("bld_dev_events")
    assert len(rows) == 3
    first = rows[0]
    assert first["actor"] == "dana" and first["ran_in"] == "local" and first["workspace"] == "acme"
    assert first["kind"] == "eval" and first["step"] == 2 and first["result"] == "ok"
    assert first["payload"]["duration_ms"] == 1500 and first["payload"]["agent"] == "prep"
    assert "token" not in json.dumps(first["payload"])
    assert be.pending_lines(ops.ws) == []
    be._sink({"env": "acme"})  # nothing pending: no second upload
    assert len(ops.db.list_all("bld_dev_events")) == 3


def test_sink_never_raises_even_when_the_upload_fails(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABCD_EVENTS_UPLOAD", "1")
    monkeypatch.setattr(be, "SINK_THRESHOLD", 1)

    def boom(env: str) -> Any:
        raise RuntimeError("platform down")

    monkeypatch.setattr(be, "_client_for_env", boom)
    _buffer(ops, _event(1))
    be._sink({"env": "acme"})  # must not raise
    be._sink({})  # a malformed event neither
    assert len(be.pending_lines(ops.ws)) == 1  # kept for the next flush


def test_a_failing_sink_does_not_fail_the_command(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad(event: dict[str, Any]) -> None:
        raise RuntimeError("sink exploded")

    monkeypatch.setattr(core_events, "_sinks", [bad])
    code, doc = run("kg", "impact", "nothing")
    assert (
        code == 1 and "no knowledge graph node" in doc["error"]
    )  # the command's own result, untouched


def test_sink_skips_a_workspace_without_credentials(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABCD_EVENTS_UPLOAD", "1")
    monkeypatch.setattr(be, "SINK_THRESHOLD", 1)
    monkeypatch.setattr(be, "_client_for_env", lambda env: pytest.fail("must not connect"))
    (ops.ws / ".env").unlink()
    _buffer(ops, _event(1))
    be._sink({"env": "acme"})
    assert len(be.pending_lines(ops.ws)) == 1


def test_flush_command_is_batched_and_idempotent(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(be, "BATCH_SIZE", 2)
    calls: list[int] = []
    real = ops.db.insert_rows_raw

    def counting(table_id: str, rows: Any) -> InsertResult:
        calls.append(len(rows))
        return real(table_id, rows)

    monkeypatch.setattr(
        type(ops.db), "insert_rows_raw", lambda self, tid, rows: counting(tid, rows)
    )
    _buffer(
        ops,
        *[_event(i) for i in range(5)],
        _event(9, ok=False, exit_code=3, mode="cloud", conversation_id="conv-7", cost_usd=0.25),
    )
    code, doc = run("events", "flush")
    assert code == 0, doc
    assert doc["data"]["sent"] == 6 and doc["data"]["pending"] == 0
    assert calls == [2, 2, 2]
    rows = ops.db.list_all("bld_dev_events")
    failed = [r for r in rows if r["result"] == "fail"][0]
    assert (
        failed["ran_in"] == "cloud"
        and failed["conversation_id"] == "conv-7"
        and failed["cost_usd"] == 0.25
    )
    # a second flush has nothing to send; re-sending the same lines is a duplicate, not an error
    code, doc = run("events", "flush")
    assert doc["data"]["sent"] == 1  # only the first flush command's own event line
    be.write_offset(ops.ws, 0)
    code, doc = run("events", "flush")
    assert code == 0 and doc["data"]["duplicates"] >= 6


def test_flush_skips_damaged_lines_and_keeps_partial_ones(ops: OpsEnv) -> None:
    path = be.events_path(ops.ws)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(_event(1)) + "\nnot json\n" + '{"ts": "x"')  # last line unfinished
    sent: list[dict[str, Any]] = []

    def insert(rows: list[dict[str, Any]]) -> InsertResult:
        sent.extend(rows)
        return InsertResult(inserted=len(rows))

    res = be.flush(ops.ws, insert)
    assert res["sent"] == 1 and res["skipped"] == 1 and res["pending"] == 0
    assert path.read_text().endswith('{"ts": "x"')
    assert be.read_offset(ops.ws) == len(path.read_bytes()) - len(b'{"ts": "x"')


def test_flush_failure_keeps_the_cursor(ops: OpsEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    _buffer(ops, _event(1))
    monkeypatch.setattr(
        type(ops.db),
        "insert_rows_raw",
        lambda self, tid, rows: InsertResult(failed=[{"index": 0, "error": "500", "status": 500}]),
    )
    code, doc = run("events", "flush")
    assert code == 3 and "not written" in doc["error"]
    assert len(be.pending_lines(ops.ws)) >= 1


def test_session_start_and_end_with_transcript(ops: OpsEnv, tmp_path: Path) -> None:
    code, doc = run("events", "session", "start", "--session-id", "sess-1")
    assert code == 0, doc
    row = ops.db.find_rows("bld_dev_sessions", "session_id", "sess-1")[0]
    assert row["user_id"] == "dana" and row["coding_agent"] == "claude_code"
    transcript = tmp_path / "t.jsonl"
    secret = "ghp_" + "b" * 30
    transcript.write_text(json.dumps({"role": "user", "text": f"token {secret}"}) + "\n")
    code, doc = run("events", "session", "end", "--transcript", str(transcript))
    assert code == 0, doc
    day = row["started_at"][:10]
    rel = f"sessions/dana/{day}/sess-1/transcript.jsonl"
    assert doc["data"]["transcript_path"] == f"builder-ops/{rel}"
    body = ops.docs.read("store-1", rel)
    assert secret not in body and "***" in body
    done = ops.db.find_rows("bld_dev_sessions", "session_id", "sess-1")[0]
    assert done["transcript_path"] == f"builder-ops/{rel}" and done["ended_at"]
    assert not (ops.ws / ".builder" / be.SESSION_FILE).exists()
    # later events carry the session id
    _buffer(ops, _event(1))
    be.write_offset(ops.ws, 0)
    (ops.ws / ".builder" / be.SESSION_FILE).write_text(json.dumps({"session_id": "sess-2"}))
    run("events", "flush")
    assert {r["session_id"] for r in ops.db.list_all("bld_dev_events") if r["step"] == 2} == {
        "sess-2"
    }


def test_session_end_without_start_inserts_a_row_and_needs_an_id(
    ops: OpsEnv, tmp_path: Path
) -> None:
    code, doc = run("events", "session", "end")
    assert code == 2 and "no session" in doc["error"]
    t = tmp_path / "t.jsonl"
    t.write_text("{}\n")
    code, doc = run(
        "events",
        "session",
        "end",
        "--session-id",
        "hook-9",
        "--transcript",
        str(t),
        "--coding-agent",
        "codex",
    )
    assert code == 0, doc
    row = ops.db.find_rows("bld_dev_sessions", "session_id", "hook-9")[0]
    assert row["coding_agent"] == "codex" and row["transcript_path"]


def test_session_start_survives_an_unreachable_platform(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Any) -> Any:
        raise RuntimeError("offline")

    monkeypatch.setattr("cli.core.context.Context.harness_client", boom)
    code, doc = run("events", "session", "start")
    assert code == 0 and doc["data"]["recorded"] is False and "offline" in doc["data"]["warning"]
    assert (ops.ws / ".builder" / be.SESSION_FILE).is_file()
