"""``abcd pipeline`` commands against a fake BFF."""

from __future__ import annotations

import pytest

from cli import builder_pipeline
from cli.abcd import main
from tests.cmd.session_fixtures import (
    FakeBFF,
    run,
)

PID = "0123456789abcdef"

pytest_plugins = ["tests.cmd.session_fixtures"]


def test_create_cron_payload_and_owner_warning(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    bff.on(
        "POST", "/v1/pipelines", {"id": PID, "name": "n", "state": "draft", "schedule_type": "cron"}
    )
    code, doc, err = run(
        capsys,
        "pipeline",
        "create",
        "--process",
        "acme-agent",
        "--schedule",
        "cron",
        "--cron",
        "0 6  * * 1",
    )
    assert code == 0
    assert bff.sent("POST", "/v1/pipelines")[0]["json"] == {
        "name": "acme-agent-cron",
        "process_id": "proc-1",
        "schedule_type": "cron",
        "state": "draft",
        "cron_expr": "0 6 * * 1",
    }
    assert "PAT token id as its owner" in doc["data"]["warnings"][0]
    assert "PAT token id as its owner" in err
    assert "from a browser" in doc["data"]["warnings"][0]


def test_create_manual_running_and_validation(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    bff.on("POST", "/v1/pipelines", {"id": PID, "state": "running"})
    code, _, _ = run(
        capsys,
        "pipeline",
        "create",
        "--process",
        "proc-1",
        "--schedule",
        "manual",
        "--state",
        "running",
    )
    body = bff.sent("POST", "/v1/pipelines")[0]["json"]
    assert (
        code == 0
        and body["schedule_type"] == "manual"
        and body["state"] == "running"
        and "cron_expr" not in body
    )
    code, doc, _ = run(capsys, "pipeline", "create", "--process", "proc-1", "--schedule", "cron")
    assert code == 2
    code, doc, _ = run(
        capsys, "pipeline", "create", "--process", "proc-1", "--schedule", "cron", "--cron", "daily"
    )
    assert code == 2
    assert len(bff.sent("POST", "/v1/pipelines")) == 1


def test_create_refuses_disabled_process(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    bff.on(
        "GET",
        "/v1/org/processes",
        {"processes": [{"id": "p2", "name": "off", "is_enabled": False}]},
    )
    code, doc, _ = run(capsys, "pipeline", "create", "--process", "off", "--schedule", "manual")
    assert code == 1 and "not enabled" in doc["error"]
    assert not bff.sent("POST", "/v1/pipelines")


def test_list_get_and_runs(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    item = {
        "id": PID,
        "name": "nightly",
        "state": "running",
        "schedule_type": "cron",
        "process_id": "proc-1",
    }
    bff.on("GET", "/v1/pipelines", {"items": [item], "total": 1})
    bff.on("GET", f"/v1/pipelines/{PID}", {**item, "created_by": "tok-1"})
    bff.on("GET", f"/v1/pipelines/{PID}/runs", {"items": [{"id": "r1", "status": "failed"}]})
    code, doc, _ = run(capsys, "pipeline", "list", "--process", "acme-agent")
    assert code == 0 and doc["data"]["items"][0]["id"] == PID
    code, doc, _ = run(capsys, "pipeline", "get", "nightly")  # by name -> search
    assert code == 0 and doc["data"]["created_by"] == "tok-1"
    code, doc, _ = run(capsys, "pipeline", "runs", PID, "--status", "failed")
    assert code == 0 and doc["data"]["items"][0]["id"] == "r1"
    assert bff.sent("GET", f"/v1/pipelines/{PID}/runs")[0]["params"]["status"] == "failed"


def test_activate_and_pause_post_state(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    path = f"/v1/pipelines/{PID}/update"
    bff.on("POST", path, lambda kw: {"id": PID, "state": kw["json"]["state"]})
    code, doc, _ = run(capsys, "pipeline", "activate", PID)
    assert code == 0 and doc["data"]["pipeline"]["state"] == "running"
    code, doc, _ = run(capsys, "pipeline", "pause", PID)
    assert code == 0 and doc["data"]["pipeline"]["state"] == "paused"
    assert [c["json"] for c in bff.sent("POST", path)] == [
        {"state": "running"},
        {"state": "paused"},
    ]


def test_delete_refuses_without_yes(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    bff.on("DELETE", f"/v1/pipelines/{PID}", {})
    code, doc, _ = run(capsys, "pipeline", "delete", PID)
    assert code == 1 and not bff.sent("DELETE", f"/v1/pipelines/{PID}")
    code, doc, _ = run(capsys, "pipeline", "delete", PID, "--yes")
    assert code == 0 and doc["data"]["deleted"] is True


def test_run_now_uses_test_run_route_with_stored_wdl(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    bff.on("POST", "/v1/pipelines/workflows/test-run", {"workflow_id": "w1"})
    code, _, _ = run(capsys, "pipeline", "run-now", PID)
    assert code == 1 and not bff.sent("POST", "/v1/pipelines/workflows/test-run")
    code, doc, _ = run(capsys, "pipeline", "run-now", PID, "--yes")
    assert code == 0
    assert bff.sent("POST", "/v1/pipelines/workflows/test-run")[0]["json"] == {
        "pipeline_id": PID,
        "test_mode": False,
    }


def test_human_output_without_json(
    bff: FakeBFF, ws_root, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    bff.on("GET", "/v1/pipelines", {"items": [{"id": PID, "name": "nightly", "state": "paused"}]})
    assert main(["pipeline", "list", "--env", "acme"]) == 0
    assert PID in capsys.readouterr().out


def test_runs_text_names_the_conversations_a_process_run_started(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live: a RUN_PROCESS run keeps its conversations under metadata.process_runs."""
    row = {
        "id": "r1",
        "status": "success",
        "metadata": {"process_runs": [{"conversation_id": "cv9", "workstream_id": "w"}]},
    }
    bff.on("GET", f"/v1/pipelines/{PID}/runs", {"items": [row]})
    code, doc, _ = run(capsys, "pipeline", "runs", PID)
    assert code == 0
    assert doc["data"]["items"][0]["metadata"]["process_runs"][0]["conversation_id"] == "cv9"
    assert "conversation=cv9" in builder_pipeline._conversations(row)
