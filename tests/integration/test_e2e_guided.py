"""Scenario (g): the guided-building path with only a platform agent token (contract C4).

No PAT anywhere: ``ADOPT_HARNESS_BEARER_TOKEN`` is the agent token the platform injects. The skill
is scaffolded and pushed, the agent is pushed DISABLED, and the platform's own refusals (enable,
delete) are what the fake BFF enforces for that issuer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
import yaml

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


def test_cloud_agent_push_creates_it_disabled_and_nothing_can_enable_it(
    agent_env: str, bff: FakeBFFServer, ws_dir: Path
) -> None:
    assert _cli("skill", "new", "guided-demo").code == 0
    skill_dir = ws_dir / "cloud" / "harness" / "skills" / "guided-demo"
    assert _cli("skill", "push", str(skill_dir)).code == 0
    assert _cli("workstream", "ensure", "guided-ops").code == 0

    new = _cli(
        "agent", "new", "guided-agent", "--display-name", "Guided agent", "--skill", "guided-demo",
        "--workstream", "guided-ops", "--instructions", "Triage each invoice; ask a person to approve.",
    )  # fmt: skip
    assert new.code == 0, new.text
    # even a bundle that says is_enabled: true is created disabled in a cloud run
    pyaml = ws_dir / "cloud" / "harness" / "agents" / "guided-agent" / "process.yaml"
    doc = yaml.safe_load(pyaml.read_text())
    doc["is_enabled"] = True
    pyaml.write_text(yaml.safe_dump(doc, sort_keys=False))

    pushed = _cli("agent", "push", "guided-agent", "--yes")
    assert pushed.code == 0, pushed.text
    data = pushed.json["data"]
    assert data["is_enabled"] is False and "Enabled" in data["enable_hint"]
    (proc,) = [p for p in bff.state.processes.values() if p["name"] == "guided-agent"]
    assert proc["is_enabled"] is False
    assert proc["created_by"] == f"agent:abcd:{CONV}"
    assert [c["ref"] for c in proc["components"]] == ["guided-demo"]
    (ws_id,) = [w["id"] for w in bff.state.workstreams.values() if w["name"] == "guided-ops"]
    assert bff.state.process_ws[proc["id"]] == [ws_id]

    # pushing again keeps it disabled
    again = _cli("agent", "push", "guided-agent", "--yes")
    assert again.code == 0 and proc["is_enabled"] is False

    # abcd refuses to enable, and so would the platform
    assert _cli("agent", "update", proc["id"], "--enable").code == 1
    assert _cli("agent", "create", "other-agent", "Other", "--enable").code == 1
    assert proc["is_enabled"] is False
    r = requests.patch(
        f"{bff.url}/v1/org/processes/{proc['id']}",
        json={"is_enabled": True},
        headers={"Authorization": f"Bearer {agent_env}"},
        timeout=10,
    )
    assert r.status_code == 403 and proc["is_enabled"] is False
    r = requests.delete(
        f"{bff.url}/v1/org/processes/{proc['id']}",
        headers={"Authorization": f"Bearer {agent_env}"},
        timeout=10,
    )
    assert r.status_code == 403
    assert json.loads(json.dumps(proc["is_enabled"])) is False
