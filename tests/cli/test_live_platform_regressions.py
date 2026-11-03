"""Regressions found by running abcd against the real staging BFF (live-test, 2026-10-29)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

import cli.harness_conversations as hc

Capsys = pytest.CaptureFixture[str]


class _Client:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def list_conversations(self, **kw: Any) -> Any:
        self.kwargs = kw
        return self.response


def _row(cid: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    # Row shape observed live: no "id" key, activity under last_message_at.
    return {"conversation_id": cid, "title": "t", "created_at": "2020-01-01T00:00:00+00:00",
            "last_message_at": now}  # fmt: skip


def test_conversations_list_reads_the_real_items_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    client = _Client({"items": [_row("c1"), _row("c2")], "developer_trace_default": True})
    monkeypatch.setattr(hc, "get_harness_client_for_env", lambda env: client)
    assert hc.list_recent(24, None, None, None, True, False, None) == 0
    shown = json.loads(capsys.readouterr().out)
    # created_at is years old; last_message_at keeps the row inside the window.
    assert {c["conversation_id"] for c in shown} == {"c1", "c2"}
    assert client.kwargs["limit"] == 100


def test_conversations_list_still_accepts_legacy_shapes(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    for response in ({"conversations": [_row("a")]}, [_row("a")]):
        monkeypatch.setattr(hc, "get_harness_client_for_env", lambda env, r=response: _Client(r))
        assert hc.list_recent(24, None, None, None, True, False, None) == 0
        assert len(json.loads(capsys.readouterr().out)) == 1


def test_conversations_list_rejects_garbage(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    monkeypatch.setattr(hc, "get_harness_client_for_env", lambda env: _Client({"nope": 1}))
    assert hc.list_recent(24, None, None, None, True, False, None) == 1


def test_skill_audit_of_a_missing_dir_is_a_usage_error(capsys: Capsys, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from cli.abcd import build_registry

    code = build_registry().run(["skill", "audit", str(tmp_path / "nope"), "--json"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def _env(name: str, **data: Any) -> dict[str, Any]:
    return {"source": "redis", "event": {"event_name": name, "type": "response", "data": data}}


def _block_env(btype: str, **block: Any) -> dict[str, Any]:
    return _env("block_delta", block={"type": btype, **block})


def test_a_second_turns_stream_replays_the_first_turn_and_must_not_leak_into_it() -> None:
    """Observed live: turn 2's stream starts with turn 1's retained Redis events."""
    from cli.builder_session import classify_turn, summarize_events, turn_segment

    turn1 = [
        _block_env("tool_use", name="fetch_skill"),
        _block_env("text_delta", text="hello"),
        _env("harness_turn_complete", turn_id="t1", stop_reason="end_turn", iterations_used=3),
        _env("harness_conversation_title", turn_id="t1"),
        {"source": "transcript", "cursor": 0, "event": {"kind": "user_message", "turn_id": "t1"}},
    ]
    turn2 = [
        _block_env("text_delta", text="second"),
        _env("harness_turn_complete", turn_id="t2", stop_reason="end_turn", iterations_used=1),
    ]
    stream = turn1 + turn2
    c = classify_turn(turn_segment(stream, "t2"))
    assert c["text"] == "second" and c["tool_calls"] == [] and c["iterations_used"] == 1
    assert classify_turn(turn_segment(stream, "t1"))["tool_calls"] == ["fetch_skill"]
    assert summarize_events(turn_segment(stream, "t2"))["tool_call_total"] == 0
    assert turn_segment(stream, None) == stream  # unknown turn: unchanged


def test_an_earlier_turns_failure_does_not_fail_the_next_turn() -> None:
    from cli.builder_session import classify_turn, turn_segment

    stream = [
        _env("harness_error", turn_id="t1", message="boom"),
        _block_env("text_delta", text="ok"),
        _env("harness_turn_complete", turn_id="t2", stop_reason="end_turn", completed=True),
    ]
    assert classify_turn(turn_segment(stream, "t2"))["status"] == "completed"


def test_a_running_turn_keeps_only_events_after_the_last_foreign_terminal() -> None:
    from cli.builder_session import classify_turn, turn_segment

    stream = [
        _block_env("text_delta", text="old"),
        _env("harness_turn_complete", turn_id="t1"),
        _block_env("text_delta", text="new"),
    ]
    c = classify_turn(turn_segment(stream, "t2"))
    assert c["text"] == "new" and c["status"] == "unknown"


def test_status_explains_running_with_terminal_true(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    import cli.builder_session as bs

    class _C:
        def turn_status(self, turn_id: str, conversation_id: str) -> dict[str, Any]:
            return {"status": "RUNNING", "stage": "saving_outputs", "terminal": True}

    class _Ctx:
        class args:  # noqa: N801
            conversation = "c1"
            turn = "t1"

    monkeypatch.setattr(bs, "get_client", lambda ctx: _C())
    monkeypatch.setattr(bs, "_load_session", lambda ctx, c: {})
    monkeypatch.setattr(bs, "_transcript", lambda client, c: [])
    res = bs._cmd_status(_Ctx())  # type: ignore[arg-type]
    assert "only saving outputs" in (res.text or "")


class _RunsClient:
    def __init__(self) -> None:
        self.kw: dict[str, Any] = {}
        self.detail_id = ""

    def list_processes(self) -> dict[str, Any]:
        return {"processes": [{"id": "p1", "name": "abcd-test-agent", "display_name": "abcd test"}]}

    def list_workstreams(self, search: str | None = None) -> dict[str, Any]:
        return {"items": [{"id": "w1", "name": "abcd-test-ws"}]}

    def list_runs(self, **kw: Any) -> dict[str, Any]:
        self.kw = kw
        return {"runs": [], "total": 0}

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        self.detail_id = run_id
        return {"id": run_id}


def test_runs_list_accepts_agent_name_and_workstream_name(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    import cli.harness_runs as hr

    client = _RunsClient()
    monkeypatch.setattr(hr, "get_harness_client_for_env", lambda env: client)
    assert (
        hr.list_runs(None, None, None, "abcd-test-ws", "abcd-test-agent", None, 1, 25, None, True)
        == 0
    )
    # the BFF filters on the agent's display name and on the workstream id
    assert client.kw["agent"] == "abcd test" and client.kw["workstream_id"] == "w1"


def test_runs_detail_accepts_a_bare_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import cli.harness_runs as hr

    client = _RunsClient()
    monkeypatch.setattr(hr, "get_harness_client_for_env", lambda env: client)
    hr.run_detail("0123456789abcdef0123456789abcdef", None, True)
    assert client.detail_id == "cv:0123456789abcdef0123456789abcdef"
    hr.run_detail("pr:1234", None, True)
    assert client.detail_id == "pr:1234"


def test_stream_and_persist_ignores_a_replayed_earlier_terminal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from cli.harness_common.stream import stream_and_persist

    events = [
        _env("harness_turn_complete", turn_id="t1", stop_reason="end_turn"),
        _block_env("text_delta", text="x"),
    ]
    assert stream_and_persist(iter(events), tmp_path / "a.ndjson", quiet=True, turn_id="t2") == {}
    done = stream_and_persist(iter(events), tmp_path / "b.ndjson", quiet=True, turn_id="t1")
    assert done["stop_reason"] == "end_turn"


def test_hitl_value_k_v_is_coerced_by_field_type() -> None:
    """Live card: approved is a `checkbox`; `--value approved=true` must submit a boolean."""
    from cli.builder_session import build_submission

    card = {
        "blocks": [
            {
                "id": "b",
                "title": "T",
                "fields": [
                    {"name": "approved", "field_type": "checkbox", "required": True},
                    {"name": "amount", "field_type": "number"},
                    {"name": "note", "field_type": "text"},
                ],
            }
        ]
    }
    vals = build_submission(card, {"approved": "true", "amount": "12", "note": "true"})[0]["values"]
    assert vals == {"approved": True, "amount": 12, "note": "true"}
    assert build_submission(card, {"approved": "false"})[0]["values"]["approved"] is False
    assert build_submission(card, {"approved": True})[0]["values"]["approved"] is True


def test_a_user_cancelled_turn_is_cancelled_not_failed_and_errors_are_deduped() -> None:
    """Live `session stop`: completed false + user_cancelled, the same tool_result emitted twice."""
    from cli.builder_session import classify_turn, summarize_events, turn_result

    stopped = _block_env(
        "tool_result", tool_use_id="tu1", is_error=True, content="Tool execution stopped by user."
    )
    stream = [
        _block_env("tool_use", name="bash"),
        stopped,
        stopped,
        _env(
            "harness_turn_complete",
            turn_id="t1",
            stop_reason="user_cancelled",
            completed=False,
            failure="",
        ),
    ]
    c = classify_turn(stream)
    assert c["status"] == "cancelled" and len(c["errors"]) == 1
    assert summarize_events(stream)["status"] == "cancelled"
    res = turn_result({**c, "conversation_id": "c", "turn_id": "t1"})
    assert res.code == 1 and "cancelled" in (res.message or "")


def test_logs_summary_takes_its_status_from_the_last_turn_not_the_concatenation() -> None:
    """Live 2026-10-29: turn 1 asked (builder card), turn 2 answered and completed, yet
    `session logs` said needs_input because the concatenated stream still held the card."""
    from cli.builder_session import summarize_turns

    card = _block_env(
        "builder", builder_id="b1", title="Base", blocks=[{"id": "x", "fields": [{"name": "n"}]}]
    )
    turn1 = [
        _block_env("tool_use", name="render_builder"),
        card,
        _env("harness_turn_complete", turn_id="t1", stop_reason="end_turn", iterations_used=2),
    ]
    turn2 = [
        _block_env("tool_use", name="save_output"),
        _env("harness_turn_complete", turn_id="t2", stop_reason="end_turn", iterations_used=5),
    ]
    both = summarize_turns([turn1, turn2])
    assert both["status"] == "completed" and both["iterations_used"] == 5
    assert both["tool_calls"] == {"render_builder": 1, "save_output": 1}
    assert summarize_turns([turn1])["status"] == "needs_input"
    assert summarize_turns([])["status"] == "unknown"


def test_workstream_ensure_json_carries_the_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    """`workstream ensure --json` printed `data: null`; the workstream and store ids are data."""
    import cli.harness_workstream as hw
    from cli.abcd import build_registry

    monkeypatch.setenv("ABCD_REQUIRE_ENV", "1")
    monkeypatch.setattr(
        hw, "load_workstreams_cache", lambda env: {"ops": {"workstream_id": "w1", "store_id": "s1"}}
    )
    code = build_registry().run(["workstream", "ensure", "ops", "--env", "x", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 0
    assert doc["data"] == {"name": "ops", "workstream_id": "w1", "store_id": "s1"}
