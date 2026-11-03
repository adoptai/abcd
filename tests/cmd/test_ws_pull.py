"""ws pull --check / pull / --adopt against a fake platform."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.builder_common import repo
from tests.builder.fakes import FakePlatform
from tests.cmd.ws_fixtures import WsBFF, git, make_workspace, run, use_client, workspaces_env


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


@pytest.fixture
def world(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, WsBFF]:
    """An initialised workspace with one skill already in the repo, and a platform with more."""
    ws = make_workspace(ws_dir)
    existing = ws / "harness" / "skills" / "known"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("known\n")
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0

    platform = FakePlatform()
    platform.add_org_skill("known", "known\n")
    platform.add_org_skill(
        "acme-intake",
        "---\nname: acme-intake\n---\nIntake.\n",
        aux={"scripts/run.py": "print('x')\n", "refs/big.md": "x" * 40, "img/logo.png": b"\x89PNG"},
        truncated=["refs/big.md"],
    )
    platform.add_org_skill("bundled", "b\n", plugin="some-plugin")
    platform.add_org_skill("acme-intake--staged", "staged\n")
    platform.add_default_skill("builtin", "default\n")
    platform.add_process(
        "p1",
        "intake-agent",
        display_name="Intake agent",
        description="Reads the inbox",
        components=[{"type": "skill", "source": "org", "ref": "acme-intake"}],
        agent_instructions="Be careful.",
        compiled_flow={"steps": []},
    )
    platform.add_process("p2", "intake-agent-staged")
    bff = WsBFF(platform).install(monkeypatch)
    wid = bff.add_workstream("claims")
    bff.process_ws["p1"] = [wid]
    use_client(monkeypatch)
    return ws, bff


def test_pull_check_writes_nothing(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _ = world
    heads = git(ws, "branch", "--list")
    code, doc = run(capsys, "ws", "pull", "--check", "--env", "acme")
    assert code == 0, doc
    names = {(i["kind"], i["name"]) for i in doc["data"]["import"]}
    assert names == {("skill", "acme-intake"), ("process", "intake-agent")}
    assert doc["data"]["already_in_repo"] == ["skill:known"]
    assert {s["name"] for s in doc["data"]["skipped"]} == {
        "bundled",
        "acme-intake--staged",
        "intake-agent-staged",
    }
    partial = [f for i in doc["data"]["import"] for f in i["files"] if f["status"] == "partial"]
    assert {(f["path"], f["reason"]) for f in partial} == {
        ("refs/big.md", "truncated"),
        ("img/logo.png", "binary"),
    }
    assert git(ws, "branch", "--list") == heads
    assert not (ws / "harness" / "skills" / "acme-intake").exists()
    assert not (ws / "harness" / "agents" / "intake-agent").exists()


def test_pull_writes_a_drift_branch(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _ = world
    main_sha = repo.current_sha(ws)
    code, doc = run(capsys, "ws", "pull", "--env", "acme")
    assert code == 0, doc
    d = doc["data"]
    branch = d["branch"]
    assert branch.startswith("drift/")
    # the working branch is where it was, and untouched
    assert repo.current_branch(ws) == "main" and repo.current_sha(ws) == main_sha
    assert not (ws / "harness" / "skills" / "acme-intake").exists()

    assert git(ws, "show", f"{branch}:harness/skills/acme-intake/SKILL.md").startswith("---")
    assert git(ws, "show", f"{branch}:harness/skills/acme-intake/scripts/run.py") == "print('x')"
    with pytest.raises(Exception):  # noqa: B017  partial files are not written
        git(ws, "show", f"{branch}:harness/skills/acme-intake/img/logo.png")
    assert {(p["path"], p["reason"]) for p in d["partial"]} == {
        ("refs/big.md", "truncated"),
        ("img/logo.png", "binary"),
    }
    proc = git(ws, "show", f"{branch}:harness/agents/intake-agent/process.yaml")
    assert "intake-agent" in proc and "claims" in proc and "acme-intake" in proc
    assert git(ws, "show", f"{branch}:harness/agents/intake-agent/instructions.md") == "Be careful."
    assert json.loads(git(ws, "show", f"{branch}:harness/agents/intake-agent/flow.json")) == {
        "steps": []
    }
    assert "partial: acme-intake/img/logo.png (binary)" in git(
        ws, "log", "-1", "--format=%B", branch
    )
    assert d["agents_imported"] == ["intake-agent"]
    assert git(ws, "diff", "--stat", "main", branch)  # only additions on the drift branch

    # a second pull starts a fresh branch (the first is not reused)
    code, doc2 = run(capsys, "ws", "pull", "--env", "acme")
    assert code == 0 and doc2["data"]["branch"] != branch


def test_pull_dry_run_equals_check(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _ = world
    code, doc = run(capsys, "ws", "pull", "--dry-run", "--env", "acme")
    assert code == 0 and doc["data"]["check"] is True
    assert "drift/" not in git(ws, "branch", "--list")


def test_pull_nothing_to_import(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0
    WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, doc = run(capsys, "ws", "pull", "--env", "acme")
    assert code == 0 and doc["data"]["branch"] is None and doc["data"]["written"] == []


def test_pull_adopt_records_v0_baseline(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    assert run(capsys, "builder", "setup", "--env", "acme")[0] == 0

    code, doc = run(capsys, "ws", "pull", "--adopt", "--check", "--env", "acme")
    assert code == 0 and doc["data"]["adopt"] == {"would_record": True}
    vtable = next(t["id"] for t in bff.tables.values() if t["table_name"] == "bld_versions")
    assert bff.rows[vtable] == []  # --check records nothing

    code, doc = run(capsys, "ws", "pull", "--adopt", "--env", "acme")
    assert code == 0, doc
    adopt = doc["data"]["adopt"]
    assert "intake-agent@v0" in adopt["tags"] and "acme-intake@v0" in adopt["recorded"]
    rows = {r["version_id"]: r for r in bff.rows[vtable]}
    assert rows["intake-agent@v0"]["state"] == "live" and rows["acme-intake@v0"]["state"] == "live"
    assert "known@v0" in rows

    # again: nothing re-recorded
    before = len(bff.rows[vtable])
    code, doc = run(capsys, "ws", "pull", "--adopt", "--env", "acme")
    assert code == 0 and doc["data"]["adopt"]["recorded"] == []
    assert len(bff.rows[vtable]) == before


def test_pull_adopt_text_prints_the_tag_command(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    from cli.abcd import main

    assert main(["builder", "setup", "--env", "acme"]) == 0
    capsys.readouterr()
    assert main(["ws", "pull", "--adopt", "--env", "acme"]) == 0
    out = capsys.readouterr().out
    assert "git tag -a intake-agent@v0" in out and "Partial" in out


def test_pull_refuses_a_secret_and_returns_to_the_branch(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    bff.platform.add_org_skill(
        "leaky", "key AKIA" + "IOSFODNN7EXAMPLE and token ghp_" + "a" * 36 + "\n"
    )
    code, doc = run(capsys, "ws", "pull", "--env", "acme")
    assert code == 1 and "refused" in doc["error"]
    assert repo.current_branch(ws) == "main"
