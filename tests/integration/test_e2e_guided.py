"""Scenario (g): the guided-building path with only a platform agent token (contract C4).

No PAT anywhere: ``ADOPT_HARNESS_BEARER_TOKEN`` is the agent token the platform injects. The skill
is scaffolded and pushed, the agent is pushed DISABLED, and the platform's own refusals (enable,
delete) are what the fake BFF enforces for that issuer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from tests.integration import fake_agent as fa
from tests.integration.conftest import run_inproc
from tests.integration.fake_bff import FakeBFFServer, make_agent_token

pytestmark = pytest.mark.integration

CONV = "conv-guided-e2e"


@pytest.fixture
def agent_env(bff: FakeBFFServer, ws_dir: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Env of a harness sandbox for the builder agent: a bearer, an endpoint, no PAT."""
    token = make_agent_token(CONV)
    for name in ("ABCD_PAT_CLIENT_ID", "ABCD_PAT_SECRET", "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADOPT_HARNESS_BEARER_TOKEN", token)
    monkeypatch.setenv("ABCD_WEBUI_ENDPOINT", bff.url)
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", CONV)
    res = run_inproc(
        "cloud", "bootstrap", "--platform-only", "--abcd-root", str(ws_dir.parent), "--json"
    )
    assert res.code == 0, res.text
    return token


def _cli(*argv: str):  # type: ignore[no-untyped-def]
    return run_inproc(*argv, "--env", "cloud", "--json")


def test_bootstrap_and_doctor_work_with_the_bearer_alone(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    env_file = (ws_dir / "cloud" / ".env").read_text()
    assert env_file == f"ADOPT_WEBUI_ENDPOINT={bff.url}\n"  # no PAT, and the token is not on disk
    assert agent_env not in env_file
    assert (ws_dir / "cloud" / ".builder" / "cloud.json").is_file()

    doctor = run_inproc("cloud", "doctor", "--env", "cloud", "--json")
    assert doctor.code == 0, doctor.text
    ident = doctor.json["data"]["identity"]
    assert ident["kind"] == "agent" and ident["sub"] == f"agent:abcd:{CONV}"
    assert ident["roles"] == ["Admin"] and ident["tenant_id"]
    assert agent_env not in doctor.out
    human = run_inproc("cloud", "doctor", "--env", "cloud")
    assert "identity: platform agent token (sub=agent:abcd:…)" in human.out
    assert CONV not in human.out
    assert bff.state.exchanges == []  # no PAT exchange happened


def test_guided_skill_push_with_the_bearer_only(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    new = _cli("skill", "new", "guided-demo", "--description", "Sort: incoming invoices")
    assert new.code == 0, new.text
    skill_dir = Path(new.json["data"]["dir"])
    assert skill_dir == ws_dir / "cloud" / "harness" / "skills" / "guided-demo"
    assert _cli("skill", "audit", str(skill_dir)).code == 0
    dry = _cli("skill", "push", str(skill_dir), "--dry-run")
    assert dry.code == 0, dry.text
    assert "guided-demo" not in bff.state.org_skills  # a dry run uploads nothing
    pushed = _cli("skill", "push", str(skill_dir))
    assert pushed.code == 0, pushed.text
    assert "guided-demo" in bff.state.org_skills
    assert _cli("skill", "verify", "guided-demo").code == 0
    assert bff.state.exchanges == [] and bff.forbidden_calls() == []
    # every request carried the agent token, never a PAT-derived one
    sent = [r for r in bff.state.requests if r["path"].startswith("/v1/end-user")]
    assert sent and {r["token_iss"] for r in sent} == {"adopt-agent"}


def _setup_agent(bff: FakeBFFServer, ws_dir: Path) -> None:
    """Skill pushed, a target workstream, and a draft agent bundle that names that target."""
    assert _cli("skill", "new", "guided-demo").code == 0
    skill_dir = ws_dir / "cloud" / "harness" / "skills" / "guided-demo"
    assert _cli("skill", "push", str(skill_dir)).code == 0
    assert _cli("workstream", "ensure", "guided-ops").code == 0
    new = _cli(
        "agent", "new", "guided-agent", "--display-name", "Guided agent", "--skill", "guided-demo",
        "--workstream", "guided-ops", "--instructions", "Triage each invoice; ask a person to approve.",
    )  # fmt: skip
    assert new.code == 0, new.text


def _proc(bff: FakeBFFServer) -> dict:
    (proc,) = [p for p in bff.state.processes.values() if p["name"] == "guided-agent"]
    return proc


def test_draft_is_tested_in_the_builder_workstream_then_published_disabled(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    builder = bff.state.add_builder_workstream()
    _setup_agent(bff, ws_dir)
    (target,) = [w["id"] for w in bff.state.workstreams.values() if w["name"] == "guided-ops"]

    # 1. draft: created disabled, assigned ONLY to the Builder workstream
    pushed = _cli("agent", "push", "guided-agent", "--yes")
    assert pushed.code == 0, pushed.text
    proc = _proc(bff)
    assert pushed.json["data"]["mode"] == "test-in-builder"
    assert proc["is_enabled"] is False and proc["created_by"] == f"agent:abcd:{CONV}"
    assert [c["ref"] for c in proc["components"]] == ["guided-demo"]
    assert bff.state.process_ws[proc["id"]] == [builder["id"]]

    # 2. enable it for the test (allowed: Builder alone), then run a session in the Builder
    assert _cli("agent", "update", proc["id"], "--enable").code == 0
    assert proc["is_enabled"] is True
    bff.state.script([fa.text("Sorted 3 invoices.")], process="guided-agent")
    session = _cli(
        "session", "start", "--workstream", "Builder", "--process", "guided-agent",
        "--message", "triage the sample invoices",
    )  # fmt: skip
    assert session.code == 0, session.text
    assert bff.state.turn_requests[-1]["workstream_id"] == builder["id"]

    # 3. publish: assign the target workstream; the platform disables it (a person enables it)
    published = _cli("agent", "push", "guided-agent", "--yes", "--publish")
    assert published.code == 0, published.text
    assert bff.state.process_ws[proc["id"]] == [target]
    assert proc["is_enabled"] is False
    assert published.json["data"]["is_enabled"] is False
    assert "Studio" in published.json["data"]["enable_hint"]
    assert bff.forbidden_calls() == []


def test_enabling_with_a_non_builder_assignment_is_refused_by_abcd_and_the_platform(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    builder = bff.state.add_builder_workstream()
    _setup_agent(bff, ws_dir)
    assert _cli("agent", "push", "guided-agent", "--yes", "--publish").code == 0
    proc = _proc(bff)
    assert proc["is_enabled"] is False
    assert _cli("agent", "update", proc["id"], "--enable").code == 1  # abcd checks first
    assert _cli("agent", "create", "other-agent", "Other", "--enable").code == 1
    auth = {"Authorization": f"Bearer {agent_env}"}
    url = f"{bff.url}/v1/org/processes/{proc['id']}"
    # the platform says no too, with a raw call that skips abcd's check
    assert (
        requests.patch(url, json={"is_enabled": True}, headers=auth, timeout=10).status_code == 403
    )
    # Builder plus another workstream is not Builder-alone either
    target = bff.state.process_ws[proc["id"]][0]
    bff.state.process_ws[proc["id"]] = sorted([builder["id"], target])
    assert (
        requests.patch(url, json={"is_enabled": True}, headers=auth, timeout=10).status_code == 403
    )
    assert proc["is_enabled"] is False
    # deletes stay forbidden under the agent token
    assert requests.delete(url, headers=auth, timeout=10).status_code == 403


def test_the_platform_disables_an_enabled_draft_when_it_is_assigned_beyond_the_builder(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    builder = bff.state.add_builder_workstream()
    _setup_agent(bff, ws_dir)
    assert _cli("agent", "push", "guided-agent", "--yes").code == 0
    proc = _proc(bff)
    assert _cli("agent", "update", proc["id"], "--enable").code == 0 and proc["is_enabled"]
    (target,) = [w["id"] for w in bff.state.workstreams.values() if w["name"] == "guided-ops"]
    r = requests.put(
        f"{bff.url}/v1/org/processes/{proc['id']}/workstreams",
        json={"workstream_ids": [builder["id"], target]},
        headers={"Authorization": f"Bearer {agent_env}"},
        timeout=10,
    )
    assert r.status_code == 200 and proc["is_enabled"] is False
