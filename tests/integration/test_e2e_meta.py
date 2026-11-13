"""Meta agents end to end: install on a branch, push live, maker-checker, and the home org setup.

One fake BFF stands in for both orgs. ``acme`` is the client workspace; ``acme-home`` is a second
workspace folder whose PAT belongs to the client's Builder home org.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.integration.conftest import FakeGitHub, write_env_file
from tests.integration.fake_bff import DEFAULT_CLIENT_ID, DEFAULT_SECRET, FakeBFFServer
from tests.integration.helpers import git, require_command

pytestmark = pytest.mark.integration


@pytest.fixture
def client_ws(cli, acme: Path, bff: FakeBFFServer, fake_github: FakeGitHub) -> Path:
    require_command("ws init", "builder setup", "meta install", "meta maker-checker")
    assert cli("ws", "init", "--remote", fake_github.url).code == 0
    setup = cli("builder", "setup")
    assert setup.code == 0, setup.text
    return acme


def _process(bff: FakeBFFServer, name: str) -> dict:
    (proc,) = [p for p in bff.state.processes.values() if p["name"] == name]
    return proc


def test_client_meta_agents_install_on_a_branch_and_push_live(
    cli, client_ws: Path, bff: FakeBFFServer
) -> None:
    assert json.loads((client_ws / "builder.json").read_text())["role"] == "client"
    main = git(client_ws, "rev-parse", "main").strip()
    res = cli("meta", "install", "status-agent", "context-agent", "context-curator", "--json")
    assert res.code == 0, res.text
    data = res.json["data"]
    assert data["client"] == "acme" and data["branch"].startswith("builder/meta-install-")
    assert git(client_ws, "rev-parse", "main").strip() == main
    assert bff.state.processes == {}  # install never touches the platform

    for name in ("status-agent", "context-agent", "context-curator"):
        pushed = cli("agent", "push", name, "--yes", "--json")
        assert pushed.code == 0, pushed.text
    ws_ids = {w["name"]: w["id"] for w in bff.state.workstreams.values()}
    status = _process(bff, "status-agent")
    assert bff.state.process_ws[status["id"]] == [ws_ids["builder-status-acme"]]
    assert bff.state.write_ws.get(status["id"], {"workstream_ids": []})["workstream_ids"] == []
    sme = _process(bff, "context-agent")
    assert bff.state.write_ws[sme["id"]] == {
        "workstream_ids": [ws_ids["builder-sme-acme"]],
        "folder_prefix": "answers",
    }
    curator = _process(bff, "context-curator")
    assert bff.state.process_ws[curator["id"]] == [ws_ids["builder-context"]]
    assert "bld_decisions" in curator["agent_instructions"]
    # `builder setup` already linked the platform-written tables to the client workstreams
    assert bff.forbidden_calls() == []


def test_maker_checker_ships_as_a_step_of_a_live_agent(
    cli, client_ws: Path, bff: FakeBFFServer
) -> None:
    assert cli("agent", "new", "triage", "--display-name", "Invoice triage").code == 0
    agent_dir = client_ws / "harness" / "agents" / "triage"
    proc = yaml.safe_load((agent_dir / "process.yaml").read_text())
    proc.update(components=[], is_enabled=True)
    (agent_dir / "process.yaml").write_text(yaml.safe_dump(proc, sort_keys=False))
    added = cli(
        "check", "add", "triage", "bucket-set", "--rule-type", "regex", "--field", "bucket",
        "--pattern", "^(approve|hold)$",
    )  # fmt: skip
    assert added.code == 0, added.text

    gen = cli("meta", "maker-checker", "triage", "--json")
    assert gen.code == 0, gen.text
    skill_dir = client_ws / "harness" / "skills" / "triage-maker-checker"
    assert "bld_check_results" in (skill_dir / "SKILL.md").read_text()
    assert cli("skill", "audit", str(skill_dir)).code == 0

    assert cli("skill", "push", str(skill_dir)).code == 0
    pushed = cli("agent", "push", "triage", "--yes", "--json")
    assert pushed.code == 0, pushed.text
    live = _process(bff, "triage")
    assert {"type": "skill", "source": "org", "ref": "triage-maker-checker"} in live["components"]
    assert "triage-maker-checker" in bff.state.org_skills


def test_home_org_setup_end_to_end(cli, client_ws: Path, bff: FakeBFFServer, ws_dir: Path) -> None:
    require_command("cloud setup")
    home = ws_dir / "acme-home"
    home.mkdir()
    (home / "env.json").write_text(json.dumps({"env_id": "acme-home", "client": "acme"}))
    (home / "harness").mkdir()
    (home / "builder.json").write_text(json.dumps({"default_branch": "main"}))
    write_env_file(home, bff.url, DEFAULT_CLIENT_ID, DEFAULT_SECRET)

    # the client org's workspace is refused; so is `builder setup` on the home workspace
    refused = cli(
        "cloud", "setup", "--home", "--dry-run", "--workspace-env", "acme", "--json", env="acme"
    )
    assert refused.code == 1 and "client org" in refused.json["error"]
    assert bff.state.org_skills == {}

    dry = cli(
        "cloud",
        "setup",
        "--home",
        "--workspace-env",
        "acme",
        "--dry-run",
        "--json",
        env="acme-home",
    )
    assert dry.code == 0, dry.text
    plan = dry.json["data"]["plan"]
    assert plan["pointer"]["content"]["repo"] == "acme/acme"  # from the client's repo_remote
    assert "abcd" not in bff.state.org_skills

    real = cli("cloud", "setup", "--home", "--workspace-env", "acme", "--json", env="acme-home")
    assert real.code == 0, real.text
    out = real.json["data"]
    assert out["role"] == "home" and out["workstream"]["name"] == "abcd-acme"
    # the real bundle (built by `abcd cloud bundle`'s builder) is the skill's one aux file
    import io
    import zipfile

    raw = bff.state.org_skills["abcd"]["aux"]["abcd-bundle.zip"]
    names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
    assert "pyproject.toml" in names and any(n.startswith("cli/") for n in names)
    assert bff.state.default_skills == {}  # never the default tier
    assert {p["name"] for p in bff.state.processes.values()} >= {
        "abcd-runner",
        "abcd-nightly",
        "abcd-weekly",
    }
    assert {s["name"] for s in out["manual_steps"]["vault_secrets"]} >= {"SANDBOX_GITHUB_TOKEN"}
    assert json.loads((home / "builder.json").read_text())["role"] == "home"
    assert bff.state.pipelines == {}  # pipelines are created from a browser

    again = cli("builder", "setup", "--json", env="acme-home")
    assert again.code == 1 and "home org" in again.json["error"]

    # a second run changes nothing
    skills_posts = len(bff.state.calls("POST", "/v1/end-user/agent-harness/skills"))
    procs = {p["id"] for p in bff.state.processes.values()}
    second = cli("cloud", "setup", "--home", "--workspace-env", "acme", "--json", env="acme-home")
    assert second.code == 0, second.text
    assert {s["state"] for s in second.json["data"]["skills"]} == {"unchanged"}
    assert second.json["data"]["pointer"]["state"] == "unchanged"
    assert len(bff.state.calls("POST", "/v1/end-user/agent-harness/skills")) == skills_posts
    assert {p["id"] for p in bff.state.processes.values()} == procs
    assert bff.forbidden_calls() == []
