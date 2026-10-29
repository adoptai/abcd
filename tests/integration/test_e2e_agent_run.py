"""Scenario (c): an agent built on a skill, assigned to a workstream, run and traced."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.integration import fake_agent as fa
from tests.integration.fake_bff import FakeBFFServer
from tests.integration.helpers import block_types, event_names, read_ndjson, single_run_dir

pytestmark = pytest.mark.integration


@pytest.fixture
def agent(cli, bff: FakeBFFServer, fixture_skill: Path) -> dict[str, str]:
    """Skill pushed, ``ops`` workstream ensured, agent ``triage`` created, enabled and assigned."""
    assert cli("skill", "push", str(fixture_skill)).code == 0
    assert cli("workstream", "ensure", "ops").code == 0
    created = cli(
        "agent", "create", "triage", "Invoice triage", "--component", "skill:invoice-triage",
        "--instructions", "Triage the invoices.", "--enable", "--json",
    )  # fmt: skip
    assert created.code == 0, created.text
    pid = created.json["data"]["id"]
    wsid = next(iter(bff.state.workstreams))
    assigned = cli("agent", "set-workstreams", pid, wsid)
    assert assigned.code == 0, assigned.text
    return {"process_id": pid, "workstream_id": wsid, "name": "triage"}


def _script_invoice_run(bff: FakeBFFServer) -> None:
    bff.state.script(
        [
            fa.text("Reading the invoices. "),
            fa.tool_use("read_skill_file", {"path": "references/rules.md"}),
            fa.tool_result("Approve under 500."),
            fa.artifact("outputs/triage.csv", "invoice,bucket\nA-1,approve\n"),
            fa.text("Done: 1 approved."),
        ],
        process="triage",
    )


def test_agent_with_skill_component_is_created_and_assigned(agent, bff: FakeBFFServer) -> None:
    proc = bff.state.processes[agent["process_id"]]
    assert proc["components"] == [{"type": "skill", "source": "org", "ref": "invoice-triage"}]
    assert proc["is_enabled"] is True
    assert bff.state.process_ws[agent["process_id"]] == [agent["workstream_id"]]
    # the workstream name from `workstream ensure` also resolves in `agent workstreams`
    assert bff.state.workstreams[agent["workstream_id"]]["name"] == "ops"


def test_run_start_streams_and_persists_every_event(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    _script_invoice_run(bff)
    res = cli(
        "run", "start", "--workstream", "ops", "--process", agent["process_id"],
        "--message", "Triage today's invoices",
    )  # fmt: skip
    assert res.code == 0, res.text
    assert "tool_use: read_skill_file" in res.out
    assert "turn_complete: stop_reason=end_turn" in res.out

    run_dir = single_run_dir(acme)
    envelopes = read_ndjson(run_dir / "turn-001.ndjson")
    names = event_names(envelopes)
    assert names[0] == "harness_sandbox_ready" and names[-1] == "harness_turn_complete"
    assert block_types(envelopes) == [
        "text_delta",
        "tool_use",
        "tool_result",
        "artifact",
        "text_delta",
    ]
    assert any(e["source"] == "keepalive" for e in envelopes)
    assert (run_dir / "turn-001-trace.json").exists()
    assert (run_dir / "turn-001-temporal-history.json").exists()
    meta = json.loads((run_dir / "run.json").read_text())
    assert (
        meta["process_id"] == agent["process_id"] and meta["turns"][0]["stop_reason"] == "end_turn"
    )

    sent = bff.state.turn_requests[0]
    assert sent["workstream_id"] == agent["workstream_id"] and sent["conversation_id"]
    assert bff.forbidden_calls() == []  # BFF only: no workflows route, no X-Workflows-Secret


def test_runs_list_detail_and_conversation_trace(cli, agent, bff: FakeBFFServer) -> None:
    _script_invoice_run(bff)
    assert cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
               "--message", "go").code == 0  # fmt: skip
    listed = cli("runs", "list", "--json")
    assert listed.code == 0, listed.text
    rows = listed.json["data"]["runs"]
    assert len(rows) == 1 and rows[0]["agent"] == "triage" and rows[0]["status"] == "succeeded"
    assert rows[0]["workstream_name"] == "ops"
    by_status = cli("runs", "list", "--status", "failed", "--json").json["data"]
    assert by_status["runs"] == []

    detail = cli("runs", "detail", "--run-id", rows[0]["id"], "--json")
    assert detail.code == 0, detail.text
    turns = detail.json["data"]["conversation_turns"]
    assert len(turns) == 1 and turns[0]["status"] == "COMPLETED"

    cid = rows[0]["id"].removeprefix("cv:")
    trace = cli("runs", "trace", "--conversation-id", cid)
    assert trace.code == 0 and '"schemaVersion": 7' in trace.out
    missing = cli("runs", "detail", "--run-id", "cv:nope")
    assert missing.code == 1 and "404" in missing.text


def test_trace_fetch_writes_logs_into_the_workspace(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    _script_invoice_run(bff)
    assert cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
               "--message", "go").code == 0  # fmt: skip
    run_dir = single_run_dir(acme)
    run = json.loads((run_dir / "run.json").read_text())
    turn_id = run["turns"][0]["turn_id"]
    out = run_dir / "fetched"
    res = cli("trace", "fetch", turn_id, "--conversation-id", run["conversation_id"],
              "--workstream-id", agent["workstream_id"], "--save", str(out))  # fmt: skip
    assert res.code == 0, res.text
    assert json.loads((out / "status.json").read_text())["status"] == "COMPLETED"
    trace = json.loads((out / "trace.json").read_text())
    assert trace["schemaVersion"] == 7 and trace["spans"][0]["name"] == "tool:read_skill_file"
    assert (out / "temporal_history.json").exists()
    assert out.is_relative_to(acme)  # logs stay inside the workspace

    review = cli("trace", "review", run_dir.name)
    assert review.code == 0, review.text

    # the BFF has no /turns/{id}/trace (workflows-only); the CLI must use /trace/{id}
    assert not [r for r in bff.requests if r["path"].endswith(f"/turns/{turn_id}/trace")]


def test_run_continue_posts_the_next_turn_on_the_same_conversation(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    assert cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
               "--message", "first").code == 0  # fmt: skip
    run_id = single_run_dir(acme).name
    again = cli("run", "continue", run_id, "--message", "second")
    assert again.code == 0, again.text
    assert (single_run_dir(acme) / "turn-002.ndjson").exists()
    convs = {t["conversation_id"] for t in bff.state.turn_requests}
    assert len(convs) == 1 and len(bff.state.turn_requests) == 2


def test_unscoped_run_without_a_process(cli, bff: FakeBFFServer, acme: Path) -> None:
    res = cli("run", "start", "--message", "just chatting")
    assert res.code == 0, res.text
    assert bff.state.turn_requests[0].get("workstream_id") in (None, "")
    traces = [r for r in bff.requests if "/trace/" in r["path"]]
    assert traces and traces[0]["query"]["workstream_id"] == "unscoped"


@pytest.mark.parametrize("case", ["disabled", "unassigned", "missing"])
def test_process_gating_errors_surface(case, cli, agent, bff: FakeBFFServer) -> None:
    pid = agent["process_id"]
    if case == "disabled":
        assert cli("agent", "update", pid, "--disable").code == 0
    elif case == "unassigned":
        bff.state.process_ws[pid] = []  # an unassigned agent cannot run in a real workstream
    else:
        pid = "00000000-0000-0000-0000-000000000000"
    res = cli("run", "start", "--workstream", "ops", "--process", pid, "--message", "hi")
    assert res.code == 1 and "403" in res.text
    assert bff.state.turns == {}


def test_a_harness_error_ends_the_run_with_exit_2(cli, agent, bff: FakeBFFServer) -> None:
    bff.state.script([fa.text("starting")], process="triage", failure="sandbox crashed")
    res = cli(
        "run", "start", "--workstream", "ops", "--process", agent["process_id"], "--message", "go"
    )
    assert res.code == 2
    assert "harness_error" in res.out and "Non-clean stop_reason" in res.out


def test_credential_in_a_message_starts_nothing(cli, agent, bff: FakeBFFServer) -> None:
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
    res = cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
              "--message", f"use this token {jwt_like}")  # fmt: skip
    assert res.code == 1 and "No turn_id" in res.text
    assert bff.state.turns == {} and bff.state.turn_requests == []


def test_org_test_run_fans_out_and_is_readable_through_runs(cli, agent, bff: FakeBFFServer) -> None:
    bff.state.script([fa.text("kickoff handled")], process="triage")
    res = cli("agent", "run", agent["process_id"])
    assert res.code == 0, res.text
    started = json.loads(res.out[res.out.index("{") :])
    assert started["count"] == 1 and started["failed"] == []
    cid = started["started"][0]["conversation_id"]
    # kickoff conversations are owned by the PAT token id (sub), shared with the workstream
    conv = bff.state.conversations[cid]
    assert conv["created_by_user_id"].startswith("pat-") and conv["shared"] is True
    runs = cli("runs", "list", "--agent", "triage", "--json").json["data"]["runs"]
    assert [r["id"] for r in runs] == [f"cv:{cid}"]
    assert cli("conversations", "list", "--json").code == 0


def test_hitl_gate_holds_and_only_the_matching_builder_id_advances(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    bff.state.script(
        [
            fa.text("Draft ready, needs approval."),
            fa.builder("bld-approve-1", step_id="approve", title="Approve invoices"),
        ],
        process="triage", message="triage",
    )  # fmt: skip
    res = cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
              "--message", "triage the batch")  # fmt: skip
    assert res.code == 0, res.text  # the turn completes: HITL is not a pause
    run_id = single_run_dir(acme).name
    conv = bff.state.conversations[run_id]
    assert conv["steps"]["approve"] == {"status": "review", "builder_id": "bld-approve-1"}
    waiting = cli("runs", "list", "--status", "waiting_on_hitl", "--json").json["data"]
    assert waiting["total"] == 1 and waiting["runs"][0]["needs_you"] is True

    def answer(builder_id: str) -> Any:
        payload = {"submission": {"blocks": [{"id": "b1", "title": "Approve", "values": {"approved": True}}]},
                   "step_id": "approve", "builder_id": builder_id}  # fmt: skip
        return cli(
            "run",
            "continue",
            run_id,
            "--message",
            f"<builder_data>{json.dumps(payload)}</builder_data>",
        )

    assert answer("bld-forged").code == 0
    assert conv["steps"]["approve"]["status"] == "review" and len(conv["rejected_submissions"]) == 1
    assert answer("bld-approve-1").code == 0
    assert conv["steps"]["approve"]["status"] == "done" and len(conv["submissions"]) == 1
    assert cli("conversations", "list", "--json").code == 0
    done = cli("runs", "list", "--json").json["data"]["runs"][0]
    assert done["status"] == "succeeded" and done["has_open_hitl"] is False


def test_skill_approval_token_is_recorded_from_user_messages(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    fp = "0123456789abcdef0123456789abcdef"
    token = f'<!--adopt:skill-approval v="1" fp="{fp}"-->'
    res = cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
              "--message", f"Approved. {token} Go ahead.")  # fmt: skip
    assert res.code == 0, res.text  # the token is not credential-shaped, so no warning gate
    assert [a["fingerprint"] for a in bff.state.approvals] == [fp]


def test_stop_needs_the_conversation_id_and_ends_a_held_turn(
    cli, agent, bff: FakeBFFServer
) -> None:
    from cli.builder_common.platform import BuilderPlatformClient
    from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

    bff.state.script([fa.text("working...")], process="triage", hold=True)
    client = BuilderPlatformClient.wrap(get_harness_client_for_env("acme"))
    cid = "conv-held-1"
    started = client.start_turn({
        "user_message": "long job", "conversation_id": cid, "process_id": agent["process_id"],
        "workstream_id": agent["workstream_id"],
    })  # fmt: skip
    turn_id = started["turn_id"]
    assert client.turn_status(turn_id, cid)["status"] == "RUNNING"
    with pytest.raises(HarnessAPIError) as busy:  # one turn at a time per conversation
        client.start_turn({"user_message": "again", "conversation_id": cid,
                           "process_id": agent["process_id"], "workstream_id": agent["workstream_id"]})  # fmt: skip
    assert busy.value.status_code == 409
    with pytest.raises(HarnessAPIError) as bare:  # the unfixed call shape: no body
        client._request("POST", f"/v1/end-user/agent-harness/turns/{turn_id}/stop")
    assert bare.value.status_code == 422

    seen: list[dict[str, Any]] = []
    reader = threading.Thread(
        target=lambda: seen.extend(client.stream_turn(turn_id, cid)), daemon=True
    )
    reader.start()
    time.sleep(0.2)
    assert client.stop_turn(turn_id, cid)["signalled"] is True
    reader.join(timeout=10)
    assert not reader.is_alive()
    last = seen[-1]["event"]
    assert (
        last["event_name"] == "harness_turn_complete"
        and last["data"]["stop_reason"] == "user_cancelled"
    )
    assert client.turn_status(turn_id, cid)["status"] == "CANCELED"


def test_another_users_chat_is_not_readable_but_runs_routes_are(
    cli, agent, bff: FakeBFFServer
) -> None:
    from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

    assert cli("run", "start", "--workstream", "ops", "--process", agent["process_id"],
               "--message", "mine").code == 0  # fmt: skip
    cid = next(iter(bff.state.conversations))
    bff.state.conversations[cid]["created_by_user_id"] = "someone-else"
    client = get_harness_client_for_env("acme")
    with pytest.raises(HarnessAPIError) as exc:
        client.get_conversation_transcript(cid)
    assert exc.value.status_code == 403
    assert client.get_conversation_trace(cid)["conversation_id"] == cid  # org-scoped: allowed


def test_run_start_omits_the_model_and_resolves_the_process_name(
    cli, agent, bff: FakeBFFServer, acme: Path
) -> None:
    """Live finding: a hard-coded default model overrode the org default; --process was id-only."""
    _script_invoice_run(bff)
    res = cli("run", "start", "--workstream", "ops", "--process", agent["name"], "--message", "go")
    assert res.code == 0, res.text
    sent = bff.state.turn_requests[-1]
    assert "model" not in sent or not sent["model"]
    assert sent["process_id"] == agent["process_id"]
    assert "abcd run continue" in res.out and "python cli/harness_run.py" not in res.out
