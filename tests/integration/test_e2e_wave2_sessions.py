"""Wave-2 session, HITL and pipeline commands against the fake BFF (skipped until they land)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration import fake_agent as fa
from tests.integration.fake_bff import FakeBFFServer
from tests.integration.helpers import require_command

pytestmark = pytest.mark.integration

JWT_LIKE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


@pytest.fixture
def agent(cli, bff: FakeBFFServer, fixture_skill: Path) -> dict[str, str]:
    assert cli("skill", "push", str(fixture_skill)).code == 0
    assert cli("workstream", "ensure", "ops").code == 0
    created = cli("agent", "create", "triage", "Invoice triage", "--component",
                  "skill:invoice-triage", "--enable", "--json")  # fmt: skip
    assert created.code == 0, created.text
    pid = created.json["data"]["id"]
    wsid = next(iter(bff.state.workstreams))
    assert cli("agent", "set-workstreams", pid, wsid).code == 0
    return {"process_id": pid, "workstream_id": wsid}


def _only_conversation(bff: FakeBFFServer) -> dict[str, Any]:
    assert len(bff.state.conversations) == 1
    return next(iter(bff.state.conversations.values()))


def _gate_script(bff: FakeBFFServer) -> None:
    bff.state.script(
        [fa.text("Draft ready."), fa.builder("bld-1", step_id="approve", title="Approve")],
        process="triage", message="triage",
    )  # fmt: skip


# -- session -------------------------------------------------------------------------------
def test_session_start_streams_and_session_logs_download(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    require_command("session start", "session logs", "session status")
    bff.state.script(
        [fa.text("Working. "), fa.tool_use("read_skill_file"), fa.text("Done.")], process="triage"
    )
    res = cli(
        "session",
        "start",
        "--workstream",
        "ops",
        "--process",
        "triage",
        "--message",
        "go",
        "--json",
    )
    assert res.code == 0, res.text
    conv = _only_conversation(bff)
    assert conv["process_id"] == agent["process_id"]  # a process *name* resolves to its id
    assert bff.state.turn_requests[0]["workstream_id"] == agent["workstream_id"]

    status = cli("session", "status", conv["id"], "--json")
    assert status.code == 0, status.text
    assert "COMPLETED" in status.text

    logs = cli("session", "logs", conv["id"], "--trace")
    assert logs.code == 0, logs.text
    written = [p for p in acme.rglob("*") if p.is_file() and conv["id"] in str(p)]
    assert any(p.suffix == ".ndjson" for p in written), written
    assert any("transcript" in p.name for p in written), written
    assert bff.forbidden_calls() == []


def test_session_outputs_lists_and_downloads_artifacts(
    cli, agent, bff: FakeBFFServer, tmp_path: Path
) -> None:
    require_command("session start", "session outputs")
    bff.state.script(
        [fa.artifact("outputs/triage.csv", "invoice,bucket\nA-1,approve\n"), fa.text("ok")],
        process="triage",
    )
    assert (
        cli(
            "session", "start", "--workstream", "ops", "--process", "triage", "--message", "go"
        ).code
        == 0
    )
    conv = _only_conversation(bff)
    out = tmp_path / "downloaded"
    res = cli("session", "outputs", conv["id"], "--path", "outputs/triage.csv", "--out", str(out))
    assert res.code == 0, res.text
    got = list(out.rglob("triage.csv"))
    assert got and got[0].read_text() == "invoice,bucket\nA-1,approve\n"


def test_session_stop_sends_the_conversation_id(cli, agent, bff: FakeBFFServer) -> None:
    require_command("session send", "session stop")
    bff.state.script([fa.text("long job")], process="triage", hold=True)
    started = cli(
        "session",
        "start",
        "--workstream",
        "ops",
        "--process",
        "triage",
        "--message",
        "go",
        "--no-wait",
    )
    assert started.code == 0, started.text
    conv = _only_conversation(bff)
    turn_id = conv["turns"][-1]
    assert bff.state.turns[turn_id]["status"] == "RUNNING"
    stopped = cli("session", "stop", conv["id"], turn_id)
    assert stopped.code == 0, stopped.text
    stops = bff.state.calls("POST", f"/v1/end-user/agent-harness/turns/{turn_id}/stop")
    assert stops and stops[-1]["body_keys"] == ["conversation_id"] and stops[-1]["status"] == 200
    assert bff.state.turns[turn_id]["stop_requested"] is True


# -- HITL ----------------------------------------------------------------------------------
def test_hitl_list_shows_a_held_card_and_resolve_advances_the_step(
    cli, agent, bff: FakeBFFServer
) -> None:
    require_command("session start", "hitl list", "hitl resolve")
    _gate_script(bff)
    assert (
        cli(
            "session",
            "start",
            "--workstream",
            "ops",
            "--process",
            "triage",
            "--message",
            "triage it",
        ).code
        == 0
    )
    conv = _only_conversation(bff)
    assert conv["steps"]["approve"]["status"] == "review"

    held = cli("hitl", "list", "--json")
    assert held.code == 0, held.text
    assert "bld-1" in held.text  # the pending card is found from the transcript/stream

    forged = cli(
        "hitl",
        "resolve",
        conv["id"],
        "--builder-id",
        "bld-forged",
        "--step-id",
        "approve",
        "--value",
        "approved=true",
    )
    assert conv["steps"]["approve"]["status"] == "review", (
        forged.text
    )  # the BFF refuses to advance it

    ok = cli(
        "hitl",
        "resolve",
        conv["id"],
        "--builder-id",
        "bld-1",
        "--step-id",
        "approve",
        "--value",
        "approved=true",
    )
    assert ok.code == 0, ok.text
    assert conv["steps"]["approve"]["status"] == "done"
    sub = conv["submissions"][-1]
    assert sub["builder_id"] == "bld-1" and sub["step_id"] == "approve"
    values = sub["submission"]["blocks"][0]["values"]
    assert values == {"approved": True} or values == {"approved": "true"}
    assert bff.forbidden_calls() == []


def test_hitl_resolve_with_a_credential_needs_the_explicit_ack(
    cli, agent, bff: FakeBFFServer
) -> None:
    require_command("session start", "hitl resolve")
    _gate_script(bff)
    assert (
        cli(
            "session",
            "start",
            "--workstream",
            "ops",
            "--process",
            "triage",
            "--message",
            "triage it",
        ).code
        == 0
    )
    conv = _only_conversation(bff)
    before = len(bff.state.turns)
    res = cli(
        "hitl",
        "resolve",
        conv["id"],
        "--builder-id",
        "bld-1",
        "--step-id",
        "approve",
        "--value",
        f"approved={JWT_LIKE}",
    )
    assert len(bff.state.turns) == before  # the BFF's 200 requires_confirmation started nothing
    assert conv["steps"]["approve"]["status"] == "review"
    assert res.code != 0 or "confirm" in res.text.lower()
    assert JWT_LIKE not in res.text  # never echoed back
    acked = cli("hitl", "resolve", conv["id"], "--builder-id", "bld-1", "--step-id", "approve",
                "--value", f"approved={JWT_LIKE}", "--ack-credential-warning")  # fmt: skip
    assert acked.code == 0, acked.text
    assert conv["steps"]["approve"]["status"] == "done"


def test_hitl_answer_posts_a_plain_turn(cli, agent, bff: FakeBFFServer) -> None:
    require_command("session start", "hitl answer")
    bff.state.script(
        [fa.question("Which ledger?", ["GL-1", "GL-2"])], process="triage", message="triage"
    )
    assert (
        cli(
            "session",
            "start",
            "--workstream",
            "ops",
            "--process",
            "triage",
            "--message",
            "triage it",
        ).code
        == 0
    )
    conv = _only_conversation(bff)
    assert conv["questions"][0]["text"] == "Which ledger?"
    res = cli("hitl", "answer", conv["id"], "--text", "GL-2")
    assert res.code == 0, res.text
    assert bff.state.turn_requests[-1]["user_message"] == "GL-2"
    assert bff.state.turn_requests[-1]["conversation_id"] == conv["id"]


def test_hitl_decide_records_a_policy_escalation_once(cli, agent, bff: FakeBFFServer) -> None:
    require_command("session start", "hitl decide")
    bff.state.script(
        [fa.builder("policy-pay-1", title="Approve payment")], process="triage", message="pay"
    )
    assert (
        cli(
            "session", "start", "--workstream", "ops", "--process", "triage", "--message", "pay now"
        ).code
        == 0
    )
    refused = cli("hitl", "decide", "policy-pay-1", "--decision", "approve")
    assert refused.code == 1 and bff.state.escalations["policy-pay-1"]["status"] == "open"
    res = cli(
        "hitl", "decide", "policy-pay-1", "--decision", "approve", "--note", "checked", "--yes"
    )
    assert res.code == 0, res.text
    esc = bff.state.escalations["policy-pay-1"]
    assert esc["status"] == "resolved" and esc["decision"] == "approve" and esc["note"] == "checked"
    again = cli("hitl", "decide", "policy-pay-1", "--decision", "reject", "--yes")
    assert again.code != 0 and esc["decision"] == "approve"  # 409: already resolved


def test_hitl_approve_skill_needs_yes_and_sends_a_token(cli, agent, bff: FakeBFFServer) -> None:
    require_command("session start", "hitl approve-skill")
    assert (
        cli(
            "session",
            "start",
            "--workstream",
            "ops",
            "--process",
            "triage",
            "--message",
            "install the skill",
        ).code
        == 0
    )
    conv = _only_conversation(bff)
    fp = "abcdef0123456789abcdef0123456789"
    refused = cli("hitl", "approve-skill", conv["id"], "--fingerprint", fp)
    assert refused.code == 1 and bff.state.approvals == []  # never prompts, never self-approves
    ok = cli("hitl", "approve-skill", conv["id"], "--fingerprint", fp, "--yes")
    assert ok.code == 0, ok.text
    assert [a["fingerprint"] for a in bff.state.approvals] == [fp]
    assert "adopt:skill-approval" in bff.state.turn_requests[-1]["user_message"]


# -- pipelines -----------------------------------------------------------------------------
def test_pipeline_lifecycle_for_a_process(cli, agent, bff: FakeBFFServer) -> None:
    require_command("pipeline create", "pipeline activate", "pipeline pause", "pipeline list",
                    "pipeline runs", "pipeline delete")  # fmt: skip
    created = cli(
        "pipeline",
        "create",
        "--process",
        "triage",
        "--schedule",
        "cron",
        "--cron",
        "0 6 * * *",
        "--json",
    )
    assert created.code == 0, created.text
    pipe = next(iter(bff.state.pipelines.values()))
    assert pipe["state"] == "draft" and pipe["process_id"] == agent["process_id"]
    assert pipe["wdl"][0]["operation"] == "RUN_PROCESS"
    assert pipe["created_by"].startswith("pat-")  # a PAT records its token id as owner

    assert cli("pipeline", "activate", pipe["id"]).code == 0
    assert pipe["state"] == "running"
    assert cli("pipeline", "pause", pipe["id"]).code == 0
    assert pipe["state"] == "paused"
    listed = cli("pipeline", "list", "--state", "paused", "--json")
    assert listed.code == 0 and pipe["id"] in listed.text
    bff.state.add_pipeline_run(pipe["id"], status="failed", error="boom")
    runs = cli("pipeline", "runs", pipe["id"], "--json")
    assert runs.code == 0 and "boom" in runs.text

    refused = cli("pipeline", "delete", pipe["id"])
    assert refused.code == 1 and pipe["state"] != "deleted"
    assert cli("pipeline", "delete", pipe["id"], "--yes").code == 0
    assert pipe["state"] == "deleted"


def test_pipeline_for_a_disabled_process_is_refused_by_the_platform(
    cli, agent, bff: FakeBFFServer
) -> None:
    require_command("pipeline create")
    assert cli("agent", "update", agent["process_id"], "--disable").code == 0
    res = cli("pipeline", "create", "--process", "triage", "--schedule", "manual")
    assert res.code != 0
    assert bff.state.pipelines == {}


def test_pipeline_run_now_needs_yes(cli, agent, bff: FakeBFFServer) -> None:
    require_command("pipeline create", "pipeline run-now")
    assert cli("pipeline", "create", "--process", "triage", "--schedule", "manual").code == 0
    pid = next(iter(bff.state.pipelines))
    assert cli("pipeline", "run-now", pid).code == 1
    assert bff.state.pipeline_runs.get(pid, []) == []
    assert cli("pipeline", "run-now", pid, "--yes").code == 0
    assert len(bff.state.pipeline_runs[pid]) == 1


def test_runs_and_hitl_list_see_a_pipeline_escalation(cli, agent, bff: FakeBFFServer) -> None:
    require_command("hitl list", "pipeline create")
    assert cli("pipeline", "create", "--process", "triage", "--schedule", "manual").code == 0
    pid = next(iter(bff.state.pipelines))
    run = bff.state.add_pipeline_run(pid)
    bff.state.add_pipeline_escalation(pid, run["id"], "Which account?")
    res = cli("hitl", "list", "--json")
    assert res.code == 0 and "Which account?" in res.text
    assert json.loads(res.out.strip().splitlines()[-1])["ok"] is True


def test_second_turn_result_excludes_the_replayed_first_turn(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    """Live finding: turn 2's stream replays turn 1; tool calls/text must be turn 2's only."""
    bff.state.script([fa.text("One. "), fa.tool_use("read_skill_file")], process="triage",
                     message="first")  # fmt: skip
    first = cli("session", "start", "--workstream", "ops", "--process", "triage",
                "--message", "first", "--json")  # fmt: skip
    assert first.code == 0, first.text
    conv = first.json["data"]["conversation_id"]
    bff.state.script([fa.text("Two.")], process="triage", message="second")
    second = cli("session", "send", conv, "--message", "second", "--json")
    assert second.code == 0, second.text
    data = second.json["data"]
    assert data["tool_calls"] == [] and data["text"] == "Two."
    logs = cli("session", "logs", conv, "--json")
    assert logs.json["data"]["summary"]["tool_call_total"] == 1  # not 2: no double counting


def test_hitl_list_for_one_conversation_filters_the_org_wide_lists(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    """Live finding: --conversation still dumped 25 org-wide tasks; rows hid the card title."""
    _gate_script(bff)
    started = cli("session", "start", "--workstream", "ops", "--process", "triage",
                  "--message", "triage", "--json")  # fmt: skip
    conv = started.json["data"]["conversation_id"]
    res = cli("hitl", "list", "--conversation", conv, "--json")
    assert res.code == 0, res.text
    data = res.json["data"]
    assert [e["conversation_id"] for e in data["escalations"]] == [conv]
    assert all(r["id"] == f"cv:{conv}" for r in data["runs"])
    text = cli("hitl", "list", "--conversation", conv).out
    assert "builder=bld-1" in text and "step=approve" in text and "Approve" in text
