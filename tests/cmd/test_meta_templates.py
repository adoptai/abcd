"""The meta agent templates lint, render, and carry no client names, secrets or URLs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from cli.builder_common import agent_bundle as ab
from cli.builder_common import meta_templates as mt
from cli.harness_skill import _lint_frontmatter, scan_bytes

ALL_AGENTS = list(mt.AGENTS)


def _write(files: dict[str, str], root: Path) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_registry_covers_the_plan_table() -> None:
    assert set(mt.AGENTS) == {
        "status-agent",
        "context-agent",
        "context-curator",
        "abcd-runner",
        "abcd-nightly",
        "abcd-weekly",
    }
    assert {n for n, a in mt.AGENTS.items() if a.where == mt.HOME} == {
        "abcd-runner",
        "abcd-nightly",
        "abcd-weekly",
    }
    for agent in mt.AGENTS.values():  # every listed template exists on disk
        assert (mt.META_DIR / agent.name / "process.yaml").is_file()
        assert (mt.META_DIR / agent.name / "instructions.md").is_file()


@pytest.mark.parametrize("name", ALL_AGENTS)
def test_agent_renders_and_loads_as_a_bundle(name: str, tmp_path: Path) -> None:
    _write(mt.agent_files(name, {"client": "zeta", "env": "zeta-prod"}), tmp_path)
    bundle = ab.load_bundle(tmp_path)  # validates names, components, write prefix
    assert bundle.name == name
    assert bundle.is_enabled and bundle.instructions
    if mt.AGENTS[name].where == mt.HOME:
        assert bundle.workstreams == ("abcd-zeta-prod",)
        assert ("skill", "org", "abcd") in bundle.components
    else:
        assert all(w.endswith("-zeta") or w == "builder-context" for w in bundle.workstreams)


def test_client_agents_write_scopes() -> None:
    values = {"client": "zeta", "env": "e"}

    def doc(name: str) -> dict:
        return yaml.safe_load(mt.agent_files(name, values)["process.yaml"])

    assert doc("status-agent")["write_workstreams"] == []  # read only
    assert doc("context-agent")["write_workstreams"] == ["builder-sme-zeta"]
    assert doc("context-agent")["write_folder_prefix"] == "answers"
    assert doc("context-curator")["write_workstreams"] == ["builder-context"]
    assert doc("context-curator")["write_folder_prefix"] == "proposed"
    for n in mt.HOME_AGENTS:
        assert doc(n)["write_workstreams"] == []


def test_runner_has_a_gated_release_step(tmp_path: Path) -> None:
    files = mt.skill_files("abcd-release-gate", {})
    meta = _lint_frontmatter(files["SKILL.md"], tmp_path / "SKILL.md")
    steps = meta["process"]["steps"]
    gated = [s for s in steps if s.get("gate")]
    assert [s["id"] for s in gated] == ["approve"] and gated[0]["assignee_role"] == "human"
    runner = yaml.safe_load(
        mt.agent_files("abcd-runner", {"client": "", "env": "e"})["process.yaml"]
    )
    assert {"type": "skill", "source": "org", "ref": "abcd-release-gate"} in runner["components"]


def test_home_instructions_say_what_each_run_does() -> None:
    v = {"client": "", "env": "e"}
    nightly = mt.agent_files("abcd-nightly", v)["instructions.md"]
    weekly = mt.agent_files("abcd-weekly", v)["instructions.md"]
    runner = mt.agent_files("abcd-runner", v)["instructions.md"]
    assert "abcd cloud nightly" in nightly
    assert "abcd status publish" in weekly and "--freeze" in weekly
    assert "abcd report scorecard" in weekly
    assert "abcd release" in runner and "gated" in runner.lower()


def test_instructions_name_their_tables() -> None:
    v = {"client": "zeta", "env": "e"}
    ctx = mt.agent_files("context-agent", v)["instructions.md"]
    cur = mt.agent_files("context-curator", v)["instructions.md"]
    assert "bld_context_gaps" in ctx and "bld_case_notes" in ctx and "db_insert_rows" in ctx
    assert "bld_decisions" in cur and "bld_open_items" in cur and "proposed/" in cur


@pytest.mark.parametrize("name", ALL_AGENTS)
def test_every_agent_states_the_common_rules(name: str) -> None:
    text = mt.agent_files(name, {"client": "zeta", "env": "e"})["instructions.md"].lower()
    assert "never print" in text and "secret" in text
    assert "default branch" in text
    assert "own message" in text and "approval" in text
    if mt.AGENTS[name].where == mt.HOME:
        assert "through abcd (bff and pat)" in text


def _sensitive_findings(files: list[Path]) -> list[str]:
    """The repo's own client-name check (hashed denylist), never a plain-text name list."""
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "sensitive_check", root / ".github" / "scripts" / "sensitive_check.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rel = [str(p.resolve().relative_to(root)) for p in files]
    return list(mod.scan(rel, mod.load_denylist()))


def test_templates_hold_no_client_names_urls_or_secrets() -> None:
    files = mt.raw_template_files()
    assert not _sensitive_findings(files)
    assert len(files) >= 14
    placeholder = re.compile(r"\$\{(\w+)\}")
    for path in files:
        text = path.read_text()
        low = text.lower()
        assert "http://" not in low and "https://" not in low, path
        assert not scan_bytes(path.name, text.encode()), f"secret-looking text in {path}"
        assert set(placeholder.findall(text)) <= {
            "client",
            "env",
            "agent",
            "checks_table",
            "criteria_note",
        }


def test_rendering_only_changes_placeholders() -> None:
    a = mt.agent_files("status-agent", {"client": "zzone", "env": "e"})
    b = mt.agent_files("status-agent", {"client": "yytwo", "env": "e"})
    assert a.keys() == b.keys()
    for rel in a:
        assert a[rel].replace("zzone", "yytwo") == b[rel]
    assert "zzone" in a["process.yaml"]


def test_unresolved_placeholder_is_an_error() -> None:
    with pytest.raises(mt.UsageError):
        mt.agent_files("status-agent", {"env": "e"})
    with pytest.raises(mt.UsageError):
        mt.agent_files("nope", {})
