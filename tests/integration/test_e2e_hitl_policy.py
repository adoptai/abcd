"""``abcd hitl policy`` against the fake BFF, up to the policy-held turn and its decision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.integration import fake_agent as fa
from tests.integration.conftest import write_env_file
from tests.integration.fake_bff import DEFAULT_CLIENT_ID, DEFAULT_SECRET, FakeBFFServer

pytestmark = pytest.mark.integration


@pytest.fixture
def agent(cli, bff: FakeBFFServer, fixture_skill: Path) -> dict[str, str]:
    assert cli("skill", "push", str(fixture_skill)).code == 0
    assert cli("workstream", "ensure", "ops").code == 0
    assert cli("workstream", "ensure", "other").code == 0
    created = cli("agent", "create", "triage", "Invoice triage", "--component",
                  "skill:invoice-triage", "--enable", "--json")  # fmt: skip
    assert created.code == 0, created.text
    pid = created.json["data"]["id"]
    ws = {w["name"]: w["id"] for w in bff.state.workstreams.values()}
    assert cli("agent", "set-workstreams", pid, ws["ops"], ws["other"]).code == 0
    return {"process_id": pid, "ops": ws["ops"], "other": ws["other"]}


def _create(cli, *extra: str) -> Any:
    return cli("hitl", "policy", "create", "--name", "hold-list", "--tool", "ws_list",
               "--workstream", "ops", "--json", *extra)  # fmt: skip


def test_create_is_a_scoped_draft_and_dry_run_sends_nothing(cli, bff, agent) -> None:
    dry = _create(cli, "--dry-run")
    assert dry.code == 0 and dry.json["data"]["then_activate"] is False
    assert dry.json["data"]["payload"]["bindings"] == [
        {"scope_kind": "workstream", "scope_id": agent["ops"], "hooks": ["pre_tool_use"]}
    ]
    assert bff.state.policies == {}
    res = _create(cli)
    assert res.code == 0, res.text
    pol = res.json["data"]
    assert pol["status"] == "draft" and pol["binding_count"] == 1
    assert bff.state.policies[pol["id"]]["bindings"][0]["scope_id"] == agent["ops"]


def test_create_validates_locally(cli, bff, agent) -> None:
    no_scope = cli("hitl", "policy", "create", "--name", "p", "--tool", "t")
    assert no_scope.code == 2 and "scope" in no_scope.text
    no_tool = cli("hitl", "policy", "create", "--name", "p", "--workstream", "ops")
    assert no_tool.code == 2 and "--tool" in no_tool.text
    ghost = cli("hitl", "policy", "create", "--name", "p", "--tool", "t", "--workstream", "nope")
    assert ghost.code == 2 and "not found" in ghost.text
    org = cli("hitl", "policy", "create", "--name", "p", "--tool", "t", "--org-wide")
    assert org.code == 1 and "--yes" in org.text
    assert bff.state.policies == {}


def test_list_get_enable_disable_roundtrip(cli, bff, agent) -> None:
    pid = _create(cli, "--enable").json["data"]["id"]
    assert bff.state.policies[pid]["status"] == "active"
    listed = cli("hitl", "policy", "list", "--json")
    assert [p["id"] for p in listed.json["data"]["policies"]] == [pid]
    assert cli("hitl", "policy", "list", "--workstream", "ops", "--json").json["data"]["total"] == 1
    assert (
        cli("hitl", "policy", "list", "--workstream", "other", "--json").json["data"]["total"] == 0
    )
    got = cli("hitl", "policy", "get", pid, "--json").json["data"]
    assert got["rules_json"]["rules"][0]["then"]["action"] == "escalate"
    off = cli("hitl", "policy", "disable", pid, "--json")
    assert off.code == 0 and bff.state.policies[pid]["status"] == "disabled"
    assert cli("hitl", "policy", "list", "--status", "active", "--json").json["data"]["total"] == 0
    assert cli("hitl", "policy", "enable", pid, "--dry-run").code == 0
    assert bff.state.policies[pid]["status"] == "disabled"
    assert cli("hitl", "policy", "enable", pid).code == 0
    assert cli("hitl", "policy", "get", "missing").code == 2


def test_non_admin_gets_a_friendly_refusal_but_can_read(cli, bff, agent, acme: Path) -> None:
    pid = _create(cli).json["data"]["id"]
    bff.state.add_pat("rev", "rev-secret", roles=["Reviewer"])
    write_env_file(acme, bff.url, "rev", "rev-secret")
    res = _create(cli)
    assert res.code == 1 and "needs an Admin" in res.text and "Traceback" not in res.text
    assert cli("hitl", "policy", "disable", pid).code == 1
    assert bff.state.policies[pid]["status"] == "draft"
    assert cli("hitl", "policy", "list").code == 0 and cli("hitl", "policy", "get", pid).code == 0
    write_env_file(acme, bff.url, DEFAULT_CLIENT_ID, DEFAULT_SECRET)


def test_create_escalation_decide_disable_loop(cli, bff: FakeBFFServer, agent) -> None:
    pid = _create(cli, "--enable").json["data"]["id"]
    bff.state.script([fa.text("Listing. "), fa.tool_use("ws_list")], process="triage",
                     message="list")  # fmt: skip

    # outside the policy's workstream the tool runs freely
    other = cli("session", "start", "--workstream", "other", "--process", "triage",
                "--message", "list", "--json")  # fmt: skip
    assert other.code == 0 and bff.state.escalations == {}

    started = cli("session", "start", "--workstream", "ops", "--process", "triage",
                  "--message", "list", "--json")  # fmt: skip
    assert started.code == 0, started.text
    conv = started.json["data"]["conversation_id"]
    (bid,) = bff.state.escalations
    assert bid.startswith("policy-") and bff.state.escalations_meta[bid]["policy_id"] == pid
    listed = cli("hitl", "list", "--conversation", conv, "--json")
    assert any(e["id"] == bid for e in listed.json["data"]["escalations"])

    turns_before = len(bff.state.conversations[conv]["turns"])
    decided = cli("hitl", "decide", bid, "--decision", "reject", "--note", "no", "--yes")
    assert decided.code == 0, decided.text
    esc = bff.state.escalations[bid]
    assert esc["status"] == "resolved" and esc["decision"] == "reject"
    assert len(bff.state.conversations[conv]["turns"]) == turns_before + 1  # platform follow-up

    assert cli("hitl", "policy", "disable", pid).code == 0
    again = cli("session", "start", "--workstream", "ops", "--process", "triage",
                "--message", "list", "--json")  # fmt: skip
    assert again.code == 0 and len(bff.state.escalations) == 1  # disabled: no new hold
