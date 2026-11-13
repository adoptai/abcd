"""``abcd session`` commands against a fake BFF."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cmd.session_fixtures import (
    _BASE,
    CARD,
    FakeBFF,
    blk,
    complete,
    env_,
    review_state,
    run,
    start_turn_route,
)

pytest_plugins = ["tests.cmd.session_fixtures"]


def test_start_needs_input_lists_every_pending_item(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    start_turn_route(
        bff,
        "t1",
        [
            blk("text_delta", text="Working. "),
            blk("tool_use", name="read_skill_file"),
            blk("builder", **{k: v for k, v in CARD.items() if k != "type"}),
            blk("question", text="Which quarter?", options=["Q1", "Q2"]),
            review_state(),
            complete(),
        ],
    )
    code, doc, err = run(
        capsys,
        "session",
        "start",
        "--workstream",
        "acme-ws",
        "--process",
        "acme-agent",
        "--message",
        "go",
    )
    assert code == 0
    data = doc["data"]
    assert data["status"] == "needs_input"
    card = data["pending"]["builders"][0]
    assert (card["builder_id"], card["step_id"]) == ("b-1", "approve_plan")
    assert card["blocks"][0]["id"] == "blk1" and card["fields"][0]["name"] == "decision"
    assert data["pending"]["questions"] == [{"text": "Which quarter?", "options": ["Q1", "Q2"]}]
    assert data["pending"]["review_steps"][0]["id"] == "approve_plan"
    assert data["tool_calls"] == ["read_skill_file"]
    assert "tool_use: read_skill_file" in err  # live stream goes to stderr
    body = bff.sent("POST", f"{_BASE}/turns")[0]["json"]
    assert body["workstream_id"] == "ws-1" and body["process_id"] == "proc-1"
    assert len(body["conversation_id"]) == 32 and body["user_message"] == "go"
    assert "model" not in body
    assert data["conversation_id"] == body["conversation_id"]


def test_start_completed_and_failed(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    start_turn_route(bff, "t1", [blk("text_delta", text="done"), complete()])
    args = (
        "session",
        "start",
        "--workstream",
        "ws-1",
        "--process",
        "proc-1",
        "--message",
        "go",
        "--model",
        "m1",
    )
    code, doc, _ = run(capsys, *args)
    assert code == 0 and doc["data"]["status"] == "completed"
    assert bff.sent("POST", f"{_BASE}/turns")[0]["json"]["model"] == "m1"

    start_turn_route(bff, "t1", [env_("harness_error", message="boom")])
    code, doc, _ = run(capsys, *args)
    assert code == 3 and doc["data"]["status"] == "failed"
    assert doc["data"]["harness_error"] == {"message": "boom"}


def test_start_refuses_requires_confirmation_unless_acked(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    bff.on(
        "POST",
        f"{_BASE}/turns",
        lambda kw: (
            {
                "requires_confirmation": True,
                "confirmation_type": "credential_in_message",
                "warning": "jwt",
            }
            if not kw["json"].get("credential_warning_acknowledged")
            else {"turn_id": "t1"}
        ),
    )
    bff.streams["t1"] = [complete()]
    args = (
        "session",
        "start",
        "--workstream",
        "ws-1",
        "--process",
        "proc-1",
        "--message",
        "eyJ.x.y",
    )
    code, doc, _ = run(capsys, *args)
    assert code == 1 and "credential" in doc["error"]
    assert len(bff.sent("POST", f"{_BASE}/turns")) == 1
    code, doc, _ = run(capsys, *args, "--ack-credential-warning")
    assert code == 0 and doc["data"]["status"] == "completed"
    assert bff.sent("POST", f"{_BASE}/turns")[-1]["json"]["credential_warning_acknowledged"] is True


def test_send_reuses_saved_conversation_context(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    start_turn_route(bff, "t1", [complete()])
    run(capsys, "session", "start", "--workstream", "ws-1", "--process", "proc-1", "--message", "a")
    conv = bff.sent("POST", f"{_BASE}/turns")[0]["json"]["conversation_id"]
    start_turn_route(bff, "t2", [complete()])
    code, doc, _ = run(capsys, "session", "send", conv, "--message", "b")
    assert code == 0
    second = bff.sent("POST", f"{_BASE}/turns")[1]["json"]
    assert second["conversation_id"] == conv and second["workstream_id"] == "ws-1"
    assert second["process_id"] == "proc-1" and second["user_message"] == "b"


def test_stop_sends_conversation_id_body(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    bff.on("POST", f"{_BASE}/turns/t9/stop", {"signalled": True})
    code, doc, _ = run(capsys, "session", "stop", "conv-1", "t9")
    assert code == 0
    assert bff.sent("POST", f"{_BASE}/turns/t9/stop")[0]["json"] == {"conversation_id": "conv-1"}


def test_status_reports_turn_and_pending(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "hi", "turn_id": "t1"},
                {"kind": "builder", **CARD},
            ]
        },
    )
    bff.on(
        "GET",
        f"{_BASE}/turns/t1/status",
        {"status": "COMPLETED", "stage": "done", "terminal": True},
    )
    code, doc, _ = run(capsys, "session", "status", "c1")
    assert code == 0 and doc["data"]["turn"]["status"] == "COMPLETED"
    assert doc["data"]["pending"]["builders"][0]["builder_id"] == "b-1"


def _seed_logs(bff: FakeBFF) -> None:
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "go", "turn_id": "t1", "workstream_id": "ws-1"}
            ]
        },
    )
    bff.on("GET", "/v1/runs/detail", {"id": "cv:c1", "status": "failed"})
    bff.on("GET", "/v1/runs/conversation-trace", {"traces": []})
    bff.on("GET", f"{_BASE}/trace/t1", {"schemaVersion": 7})
    bff.streams["t1"] = [
        blk("tool_use", name="ws_read_pdf"),
        blk("tool_use", name="ws_read_pdf"),
        blk("tool_result", is_error=True, content="no such file"),
        env_("harness_error", message="sandbox died"),
    ]


def test_logs_writes_files_and_summarises(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _seed_logs(bff)
    out = tmp_path / "logs"
    code, doc, _ = run(capsys, "session", "logs", "c1", "--out", str(out), "--trace")
    assert code == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "conversation-trace.json",
        "run-detail.json",
        "summary.json",
        "transcript.json",
        "turn-001-trace.json",
        "turn-001.ndjson",
    ]
    s = doc["data"]["summary"]
    assert s["harness_error"] == {"message": "sandbox died"}
    assert (
        s["tool_calls"] == {"ws_read_pdf": 2}
        and s["tool_errors"][0]["tool_result"] == "no such file"
    )
    assert s["status"] == "failed"
    assert len((out / "turn-001.ndjson").read_text().splitlines()) == 4
    assert (
        json.loads((out / "summary.json").read_text())["harness_error"]["message"] == "sandbox died"
    )
    assert bff.sent("STREAM", "t1")[0]["workstream_id"] == "ws-1"


def test_logs_without_trace_skips_traces_and_defaults_to_workspace_dir(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str], ws_root: Path
) -> None:
    _seed_logs(bff)
    code, doc, _ = run(capsys, "session", "logs", "c1")
    assert code == 0
    out = ws_root / "acme" / "harness" / "traces" / "c1"
    assert (out / "turn-001.ndjson").exists() and not (out / "turn-001-trace.json").exists()
    assert doc["data"]["dir"] == str(out)


def test_outputs_lists_and_downloads(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "x", "turn_id": "t1", "workstream_id": "ws-1"}
            ]
        },
    )
    bff.streams["t1"] = []
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "x", "turn_id": "t1", "workstream_id": "ws-1"},
                {"kind": "artifact", "path": "reports/out.csv", "artifact_id": "a1"},
            ]
        },
    )
    bff.files["reports/out.csv"] = b"a,b\n1,2\n"
    out = tmp_path / "dl"
    code, doc, _ = run(capsys, "session", "outputs", "c1", "--out", str(out))
    assert code == 0
    assert doc["data"]["outputs"] == [{"path": "reports/out.csv", "artifact_id": "a1"}]
    assert (out / "reports" / "out.csv").read_bytes() == b"a,b\n1,2\n"
    assert bff.sent("DOWNLOAD", "reports/out.csv")[0]["workstream_id"] == "ws-1"
    # listing alone downloads nothing
    bff.calls.clear()
    code, doc, _ = run(capsys, "session", "outputs", "c1")
    assert (
        code == 0
        and "downloaded" not in doc["data"]
        and not bff.sent("DOWNLOAD", "reports/out.csv")
    )


def test_outputs_use_the_platforms_artifact_fields(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The platform's ArtifactEvent has relative_path/filename/sandbox_path, no `path`."""
    bff.streams["t1"] = []
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "x", "turn_id": "t1", "workstream_id": "ws-1"},
                {
                    "kind": "artifact",
                    "artifact_id": "a2",
                    "filename": "out.csv",
                    "relative_path": "reports/q3/out.csv",
                    "sandbox_path": "/workspace/outputs/reports/q3/out.csv",
                },
            ]
        },
    )
    bff.files["reports/q3/out.csv"] = b"x\n"
    out = tmp_path / "dl"
    code, doc, _ = run(capsys, "session", "outputs", "c1", "--out", str(out))
    assert code == 0
    assert doc["data"]["outputs"][0]["path"] == "reports/q3/out.csv"
    assert (out / "reports" / "q3" / "out.csv").read_bytes() == b"x\n"
