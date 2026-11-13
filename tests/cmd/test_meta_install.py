"""``abcd meta list|install|maker-checker``."""

# ruff: noqa: F401, F811
from __future__ import annotations

import json
from pathlib import Path

import yaml

from cli.builder_common import agent_bundle as ab
from cli.builder_common.workspace import update_builder_config
from tests.cmd.meta_fixtures import Env, git, meta_env, run_cli


def _branches(ws: Path) -> list[str]:
    return git(ws, "branch", "--format=%(refname:short)").split()


def test_list_shows_every_template(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    code, doc = run_cli("meta", "list", "--env", "zeta")
    assert code == 0
    names = {r["name"] for r in doc["data"]["agents"]}
    assert len(names) == 6 and "context-agent" in names and "abcd-runner" in names
    assert all(r["installed"] is False for r in doc["data"]["agents"])
    assert (ws / "harness").exists() is False


def test_install_writes_the_bundle_on_a_branch(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    main_sha = git(ws, "rev-parse", "main")
    code, doc = run_cli("meta", "install", "status-agent", "context-agent", "--env", "zeta")
    assert code == 0, doc
    data = doc["data"]
    assert data["client"] == "zeta"
    assert data["branch"].startswith("builder/meta-install-") and data["branch"] != "main"
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == data["branch"]
    assert git(ws, "rev-parse", "main") == main_sha  # the default branch is untouched
    assert git(ws, "rev-parse", "HEAD") == data["commit"]
    assert git(ws, "status", "--porcelain") == ""
    bundle = ab.load_bundle(ws / "harness/agents/status-agent")
    assert bundle.workstreams == ("builder-status-zeta",)
    sme = ab.load_bundle(ws / "harness/agents/context-agent")
    assert sme.write_workstreams == ("builder-sme-zeta",)
    assert "abcd agent push status-agent" in "\n".join(data["next"])
    assert data["pushed"] is False


def test_install_uses_the_client_in_builder_json(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    update_builder_config(ws, workstreams={"builder-status-orbit": "w1"})
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta")
    assert code == 0 and doc["data"]["client"] == "orbit"
    assert "builder-status-orbit" in (ws / "harness/agents/status-agent/process.yaml").read_text()
    code, doc = run_cli(
        "meta", "install", "context-curator", "--client", "Other Co", "--env", "zeta"
    )
    assert code == 0 and doc["data"]["client"] == "other-co"


def test_install_is_idempotent_and_refuses_to_overwrite(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    assert run_cli("meta", "install", "status-agent", "--env", "zeta")[0] == 0
    branches = _branches(ws)
    sha = git(ws, "rev-parse", "HEAD")
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta")
    assert code == 0 and set(doc["data"]["files"].values()) == {"unchanged"}
    assert doc["data"]["commit"] is None
    assert git(ws, "rev-parse", "HEAD") == sha and _branches(ws) == branches
    path = ws / "harness/agents/status-agent/instructions.md"
    path.write_text("my edits\n")
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta")
    assert code == 1 and "--replace" in doc["error"]
    assert path.read_text() == "my edits\n"
    code, _ = run_cli("meta", "install", "status-agent", "--replace", "--env", "zeta")
    assert code == 0 and "Status agent" in path.read_text()


def test_install_stays_on_a_feature_branch(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    git(ws, "checkout", "-q", "-b", "feat/mine")
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta")
    assert code == 0 and doc["data"]["branch"] == "feat/mine"
    assert git(ws, "rev-parse", "main") != git(ws, "rev-parse", "HEAD")


def test_install_dry_run_writes_nothing(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    code, doc = run_cli("meta", "install", "abcd-runner", "--dry-run", "--env", "zeta")
    assert code == 0 and doc["data"]["dry_run"] is True
    states = doc["data"]["files"]
    assert "harness/skills/abcd-release-gate/SKILL.md" in states
    assert set(states.values()) == {"new"}
    assert not (ws / "harness").exists() and git(ws, "branch", "--show-current") == "main"


def test_install_installs_the_runner_with_its_skill(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    code, doc = run_cli(
        "meta", "install", "abcd-runner", "--workspace-env", "zeta-prod", "--env", "zeta"
    )
    assert code == 0, doc
    assert (ws / "harness/skills/abcd-release-gate/SKILL.md").is_file()
    assert "abcd-zeta-prod" in (ws / "harness/agents/abcd-runner/process.yaml").read_text()
    assert any("cloud setup --home" in c for c in doc["data"]["next"])


def test_install_refuses_the_wrong_org_role(meta_env: Env) -> None:
    ws = meta_env.ws("zeta", role="client")
    code, doc = run_cli("meta", "install", "abcd-nightly", "--env", "zeta")
    assert code == 1 and "home org" in doc["error"]
    assert not (ws / "harness").exists()
    home = meta_env.ws("zeta-home", role="home")
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta-home")
    assert code == 1 and "client org" in doc["error"]
    assert not (home / "harness").exists()


def test_install_rejects_unknown_names_and_non_repos(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    code, doc = run_cli("meta", "install", "status-agent", "bogus", "--env", "zeta")
    assert code == 2 and "bogus" in doc["error"]
    import shutil

    shutil.rmtree(ws / ".git")
    code, doc = run_cli("meta", "install", "status-agent", "--env", "zeta")
    assert code == 1 and "not a git repository" in doc["error"]


# -- maker-checker ---------------------------------------------------------------------------


def _agent_with_checks(ws: Path) -> None:
    agent = ws / "harness/agents/triage"
    agent.mkdir(parents=True)
    (agent / "process.yaml").write_text(
        "name: triage\ndisplay_name: Triage\ndescription: ''\ncomponents:\n"
        "- {type: skill, source: org, ref: invoice-triage}\nworkstreams: []\n"
        "write_workstreams: []\nis_enabled: true\n"
    )
    (agent / "instructions.md").write_text("Sort invoices.\n")
    (agent / "release.yaml").write_text(
        yaml.safe_dump(
            {
                "agent": "triage",
                "dataset": "invoices@1",
                "criteria": [
                    {"criteria_key": "k", "metric": "pass_rate", "comparator": ">=", "target": 0.9}
                ],
            }
        )
    )
    checks = ws / "harness/checks"
    checks.mkdir(parents=True)
    (checks / "bucket-set.yaml").write_text(
        yaml.safe_dump(
            {
                "check_id": "bucket-set",
                "agent": "triage",
                "title": "Bucket is set",
                "kind": "deterministic",
                "severity": "error",
                "rule": {"type": "regex", "field": "bucket", "pattern": "^(approve|hold|reject)$"},
            }
        )
    )
    (checks / "tone.yaml").write_text(
        yaml.safe_dump(
            {
                "check_id": "tone",
                "agent": "triage",
                "kind": "judged",
                "severity": "warning",
                "rubric": "The note is polite | and short.",
            }
        )
    )
    (checks / "other.yaml").write_text(
        yaml.safe_dump(
            {
                "check_id": "other",
                "agent": "someone-else",
                "kind": "deterministic",
                "severity": "error",
                "rule": {"type": "present", "field": "x"},
            }
        )
    )


def test_maker_checker_generates_the_skill_and_component(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    _agent_with_checks(ws)
    code, doc = run_cli("meta", "maker-checker", "triage", "--env", "zeta")
    assert code == 0, doc
    assert doc["data"]["checks"] == ["bucket-set", "tone"]
    skill = ws / "harness/skills/triage-maker-checker"
    md = (skill / "SKILL.md").read_text()
    assert md.startswith("---\nname: triage-maker-checker\n")
    assert "`bucket-set` Bucket is set" in md and "blocking" in md and "warning" in md
    assert "polite \\| and short" in md  # the table cell is escaped
    assert "pass_rate >= 0.9" in md and "bld_check_results" in md and "db_insert_rows" in md
    assert "other" not in md.replace("another", "")  # another agent's check is left out
    listed = json.loads((skill / "references/checks.json").read_text())
    assert [c["check_id"] for c in listed] == ["bucket-set", "tone"]
    bundle = ab.load_bundle(ws / "harness/agents/triage")
    assert ("skill", "org", "triage-maker-checker") in bundle.components
    assert ("skill", "org", "invoice-triage") in bundle.components  # existing one kept
    assert bundle.instructions == "Sort invoices."


def test_maker_checker_is_idempotent(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    _agent_with_checks(ws)
    assert run_cli("meta", "maker-checker", "triage", "--env", "zeta")[0] == 0
    first = (ws / "harness/skills/triage-maker-checker/SKILL.md").read_text()
    code, doc = run_cli("meta", "maker-checker", "triage", "--env", "zeta")
    assert code == 0 and doc["data"]["component_added"] is False
    assert (ws / "harness/skills/triage-maker-checker/SKILL.md").read_text() == first
    comps = ab.load_bundle(ws / "harness/agents/triage").components
    assert comps.count(("skill", "org", "triage-maker-checker")) == 1


def test_maker_checker_needs_checks_and_a_bundle(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    code, doc = run_cli("meta", "maker-checker", "ghost", "--env", "zeta")
    assert code == 2 and "ghost" in doc["error"]
    _agent_with_checks(ws)
    for p in (ws / "harness/checks").glob("*.yaml"):
        p.unlink()
    code, doc = run_cli("meta", "maker-checker", "triage", "--env", "zeta")
    assert code == 1 and "no checks" in doc["error"]
    assert not (ws / "harness/skills").exists()


def test_maker_checker_dry_run(meta_env: Env) -> None:
    ws = meta_env.ws("zeta")
    _agent_with_checks(ws)
    code, doc = run_cli("meta", "maker-checker", "triage", "--dry-run", "--env", "zeta")
    assert code == 0 and doc["data"]["dry_run"] is True
    assert not (ws / "harness/skills").exists()
    assert ("skill", "org", "triage-maker-checker") not in ab.load_bundle(
        ws / "harness/agents/triage"
    ).components
