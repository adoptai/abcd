"""The ten Builder skills, the plugin version and the R16 session hook (all offline)."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cli.harness_skill import _lint_frontmatter

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "abcd-builder"
NEW_SKILLS = [
    "workspace-setup",
    "build-plan",
    "agent-builder",
    "context-and-kg",
    "dataset-builder",
    "eval-runner",
    "context-gaps",
    "release-manager",
    "explain-and-train",
    "session-debug",
]
HOOK_PATH = PLUGIN / "hooks" / "abcd_session_hook.py"


def _skill(name: str) -> Path:
    return PLUGIN / "skills" / name / "SKILL.md"


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_frontmatter_lints_strictly(name: str) -> None:
    path = _skill(name)
    meta = _lint_frontmatter(path.read_text(encoding="utf-8"), path)
    assert meta["name"] == name
    assert isinstance(meta["description"], str) and 100 < len(meta["description"]) <= 1024
    assert "Use when the user says" in meta["description"]


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_has_the_required_sections(name: str) -> None:
    text = _skill(name).read_text(encoding="utf-8")
    assert "## When to use" in text
    assert "## Hand-offs" in text
    assert "## Rules" in text
    assert "--json" in text
    assert "--yes" in text
    rules = text.split("## Rules", 1)[1].split("## Hand-offs", 1)[0]
    for phrase in ("default branch", "deploy-default", "BFF"):
        assert phrase in rules, f"{name}: Rules must mention {phrase!r}"
    assert "client" in rules.lower()


def test_handoffs_name_real_skills() -> None:
    real = {p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md")}
    for name in NEW_SKILLS:
        text = _skill(name).read_text(encoding="utf-8")
        hand = text.split("## Hand-offs", 1)[1]
        mentioned = set(re.findall(r"`([a-z]+(?:-[a-z]+)+)`", hand)) & set(
            re.findall(r"`([a-z]+(?:-[a-z]+)+)`", " ".join(real))
        )
        mentioned |= {r for r in real if f"`{r}`" in hand}
        assert mentioned, f"{name}: Hand-offs reference no skill"
        assert name not in mentioned or len(mentioned) > 1


def test_approval_rules_are_stated_where_they_apply() -> None:
    assert "lead" in _skill("release-manager").read_text(encoding="utf-8")
    for name in ("agent-builder", "release-manager", "session-debug"):
        assert "browser skill" in _skill(name).read_text(encoding="utf-8")
    assert "on the member's behalf" in _skill("session-debug").read_text(encoding="utf-8")


def test_plugin_manifest_and_marketplace() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["version"] == "0.2.0"
    assert "Builder" in manifest["description"]
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(e for e in market["plugins"] if e["name"] == "abcd-builder")
    assert "Builder" in entry["description"]


def test_hooks_json_follows_the_plugin_format() -> None:
    data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"SessionStart", "SessionEnd"}
    for event, kind in (("SessionStart", "start"), ("SessionEnd", "end")):
        (group,) = data["hooks"][event]
        (hook,) = group["hooks"]
        assert hook["type"] == "command"
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/abcd_session_hook.py" in hook["command"]
        assert hook["command"].count(f" {kind}") == 2  # python3 first, python as fallback
        assert isinstance(hook["timeout"], int)


# --- the hook script, against a fake abcd -----------------------------------------------------


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("abcd_session_hook", HOOK_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAKE_ABCD = """\
import json, os, sys
with open(os.environ["FAKE_ABCD_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stderr.write("boom\\n")
sys.exit(int(os.environ.get("FAKE_ABCD_EXIT", "0")))
"""


@pytest.fixture
def rig(tmp_path: Path) -> dict:
    fake = tmp_path / "fake_abcd.py"
    fake.write_text(FAKE_ABCD)
    workspaces = tmp_path / "root" / "workspaces"
    ws = workspaces / "acme"
    ws.mkdir(parents=True)
    (workspaces / ".active_env").write_text("acme\n")
    log = tmp_path / "calls.log"
    environ = {
        "ABCD_HOOK_CMD": f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}",
        "FAKE_ABCD_LOG": str(log),
        "ABCD_WORKSPACES_DIR": str(workspaces),
    }
    return {"ws": ws, "log": log, "environ": environ, "cwd": tmp_path / "root", "fake": fake}


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_hook_runs_events_session_when_builder_is_active(hook: ModuleType, rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    payload = {"cwd": str(rig["cwd"]), "transcript_path": "/tmp/t.jsonl", "session_id": "s1"}
    assert hook.run("start", payload, rig["environ"]) == 0
    assert hook.run("end", payload, rig["environ"]) == 0
    assert _calls(rig["log"]) == [
        ["events", "session", "start", "--transcript", "/tmp/t.jsonl", "--env", "acme", "--json"],
        ["events", "session", "end", "--transcript", "/tmp/t.jsonl", "--env", "acme", "--json"],
    ]


def test_hook_does_nothing_without_builder_json(hook: ModuleType, rig: dict) -> None:
    assert hook.run("start", {"cwd": str(rig["cwd"])}, rig["environ"]) == 0
    assert _calls(rig["log"]) == []
    assert not (rig["ws"] / ".builder").exists()


def test_hook_does_nothing_when_disabled_or_unknown_kind(hook: ModuleType, rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    assert (
        hook.run("start", {"cwd": str(rig["cwd"])}, {**rig["environ"], "ABCD_HOOKS_DISABLE": "1"})
        == 0
    )
    assert hook.run("stop", {"cwd": str(rig["cwd"])}, rig["environ"]) == 0
    assert _calls(rig["log"]) == []


def test_hook_finds_the_workspace_from_inside_it(hook: ModuleType, rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    (rig["ws"] / "env.json").write_text("{}")
    inner = rig["ws"] / "harness" / "skills"
    inner.mkdir(parents=True)
    environ = {k: v for k, v in rig["environ"].items() if k != "ABCD_WORKSPACES_DIR"}
    assert hook.run("start", {"cwd": str(inner)}, environ) == 0
    assert _calls(rig["log"]) == [["events", "session", "start", "--env", "acme", "--json"]]


def test_hook_exits_zero_and_logs_when_abcd_fails(hook: ModuleType, rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    environ = {**rig["environ"], "FAKE_ABCD_EXIT": "3"}
    assert hook.run("end", {"cwd": str(rig["cwd"])}, environ) == 0
    text = (rig["ws"] / ".builder" / "hooks.log").read_text()
    assert "end: exit 3" in text and "boom" in text


def test_hook_exits_zero_when_abcd_is_missing(hook: ModuleType, rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    environ = {**rig["environ"], "ABCD_HOOK_CMD": "no-such-abcd-binary-xyz"}
    assert hook.run("start", {"cwd": str(rig["cwd"])}, environ) == 0
    assert "FileNotFoundError" in (rig["ws"] / ".builder" / "hooks.log").read_text()


def test_hook_script_end_to_end_survives_bad_stdin(rig: dict) -> None:
    (rig["ws"] / "builder.json").write_text("{}")
    import os

    env = {**os.environ, **rig["environ"]}
    for stdin in ("", "not json", "[1, 2]"):
        res = subprocess.run(
            [sys.executable, str(HOOK_PATH), "start"],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            cwd=rig["cwd"],
            timeout=60,
        )
        assert res.returncode == 0, res.stderr
    assert len(_calls(rig["log"])) == 3
    res = subprocess.run(
        [sys.executable, str(HOOK_PATH)], input="{}", capture_output=True, text=True, env=env
    )
    assert res.returncode == 0
