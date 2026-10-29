"""ws init / clone / status / hook against local bare repos (no network)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.abcd import main
from cli.builder_common import repo
from tests.cmd.ws_fixtures import (
    WsBFF,
    bare_repo,
    git,
    make_workspace,
    run,
    use_client,
    workspaces_env,
)


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


def test_init_status_branch_commit_push(
    ws_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = bare_repo(tmp_path)
    ws = make_workspace(ws_dir)
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare))
    assert code == 0, doc
    data = doc["data"]
    assert data["committed"] and data["pushed"] and data["branch"] == "main"
    assert git(bare, "rev-parse", "main") == data["sha"]
    for rel in ("builder.json", "ADOPT.md", ".gitignore", "harness/bundles.lock.json"):
        assert (ws / rel).is_file()
    assert (ws / ".git" / "hooks" / "pre-commit").is_file()
    assert "repo_remote" in (ws / "builder.json").read_text()
    assert str(bare) in (ws / "builder.json").read_text()

    # idempotent: nothing new, same commit
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare))
    assert code == 0
    assert doc["data"]["created"] == [] and doc["data"]["committed"] is False
    assert doc["data"]["sha"] == data["sha"]

    code, doc = run(capsys, "ws", "status", "--env", "acme")
    assert code == 0
    st = doc["data"]
    assert (st["branch"], st["ahead"], st["behind"], st["dirty"]) == ("main", 0, 0, False)

    # a new Builder file is reported as untracked
    skill = ws / "harness" / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\n---\n")
    _, doc = run(capsys, "ws", "status", "--env", "acme")
    assert doc["data"]["untracked_builder"] == ["harness/skills/x/SKILL.md"]
    assert doc["data"]["dirty"] is True

    # branch commit and push: the branch goes, the default branch is refused
    repo.create_branch(ws, "feature/x")
    repo.commit(ws, "add skill x")
    repo.push(ws)
    assert git(bare, "rev-parse", "feature/x") == repo.current_sha(ws)
    _, doc = run(capsys, "ws", "status", "--env", "acme")
    assert doc["data"]["branch"] == "feature/x"
    assert doc["data"]["ahead"] == 0 and doc["data"]["upstream"] == "origin/feature/x"
    repo.checkout(ws, "main")
    from cli.builder_common.errors import RefusedError

    with pytest.raises(RefusedError):
        repo.push(ws, "main")


def test_init_refuses_default_workspace(ws_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_workspace(ws_dir, "default")
    code, doc = run(capsys, "ws", "init", "--env", "default")
    assert code == 1
    assert "default" in doc["error"]
    assert not (ws_dir / "default" / ".git").exists()


def test_init_dry_run_changes_nothing(ws_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(ws_dir)

    def names() -> list[str]:  # .builder is the framework's own event log
        return sorted(p.name for p in ws.iterdir() if p.name != ".builder")

    before = names()
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--dry-run")
    assert code == 0 and doc["data"]["dry_run"] is True
    assert names() == before and not (ws / ".git").exists()


def test_init_rejects_remote_with_credentials(
    ws_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_workspace(ws_dir)
    code, _ = run(
        capsys, "ws", "init", "--env", "acme", "--remote", "https://u:p@example.test/a.git"
    )
    assert code == 1


def test_init_commit_is_refused_when_a_secret_is_staged(
    ws_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(ws_dir)
    (ws / "adopt_profile.json").write_text('{"security_params": {"cookie": "abc123"}}')
    code, doc = run(capsys, "ws", "init", "--env", "acme")
    assert code == 1 and "security_params" in doc["error"]


def test_clone_restores_a_workspace(
    ws_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = bare_repo(tmp_path)
    make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare))[0] == 0
    shutil.rmtree(ws_dir / "acme")

    code, doc = run(capsys, "ws", "clone", str(bare), "--env", "acme")
    assert code == 0, doc
    ws = ws_dir / "acme"
    assert (ws / ".env").is_file() and (ws / "builder.json").is_file()
    assert (ws / ".git" / "hooks" / "pre-commit").is_file()
    assert not (ws / ".env").read_text().count("eyJ")

    # an env.json that doesn't match the folder name is refused and cleaned up
    code, doc = run(capsys, "ws", "clone", str(bare), "--env", "other")
    assert code == 2 and not (ws_dir / "other").exists()


def test_clone_dry_run_and_default_name(
    ws_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, doc = run(capsys, "ws", "clone", str(tmp_path / "acme.git"), "--dry-run")
    assert code == 0 and doc["data"]["dest"].endswith("workspaces/acme")
    assert not (ws_dir / "acme").exists()
    code, _ = run(capsys, "ws", "clone", str(tmp_path / "default.git"))
    assert code == 1


def test_hook_install(ws_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(ws_dir)
    code, _ = run(capsys, "ws", "hook", "install", "--env", "acme")
    assert code == 2  # not a repo yet
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0
    hook = ws / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho foreign\n")
    code, doc = run(capsys, "ws", "hook", "install", "--env", "acme")
    assert code == 1  # a foreign hook is not replaced
    code, doc = run(capsys, "ws", "hook", "install", "--env", "acme", "--force")
    assert code == 0 and "abcd-builder-precommit" in hook.read_text()


def test_status_requires_a_repo(ws_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_workspace(ws_dir)
    code, doc = run(capsys, "ws", "status", "--env", "acme")
    assert code == 2 and "ws init" in doc["error"]


def test_status_platform_degrades_without_a_pat(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0

    def no_pat(self: object) -> None:
        raise ValueError("ADOPT_WEBUI_ENDPOINT is required")

    monkeypatch.setattr("cli.core.context.Context.harness_client", no_pat)
    code, doc = run(capsys, "ws", "status", "--env", "acme", "--platform")
    assert code == 0
    assert doc["data"]["platform"]["available"] is False
    assert "ADOPT_WEBUI_ENDPOINT" in doc["data"]["platform"]["reason"]


def test_status_platform_drift_summary(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)

    # no Builder tables yet: degrades with the reason, still exit 0
    code, doc = run(capsys, "ws", "status", "--env", "acme", "--platform")
    assert code == 0 and doc["data"]["platform"]["available"] is False

    assert run(capsys, "builder", "setup", "--env", "acme")[0] == 0
    code, doc = run(capsys, "ws", "status", "--env", "acme", "--platform")
    assert code == 0
    assert doc["data"]["platform"]["available"] is True
    assert doc["data"]["platform"]["has_drift"] is False
    assert bff.calls


def test_init_with_a_new_remote_says_builder_json_needs_a_commit(
    ws_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live 2026-10-29: moving the origin to GitHub left builder.json dirty and said nothing."""
    first = bare_repo(tmp_path)
    ws = make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme", "--remote", str(first))[0] == 0
    second = bare_repo(tmp_path / "other")
    # the new remote already has the default branch: init must not push it
    git(first, "push", str(second), "main")
    capsys.readouterr()
    assert main(["ws", "init", "--env", "acme", "--remote", str(second)]) == 0
    out = capsys.readouterr().out
    assert "Not pushed" in out and repo.remote_url(ws) == str(second)
    assert "builder.json changed" in out and "feature branch" in out
    assert main(["ws", "init", "--env", "acme", "--remote", str(second), "--dry-run"]) == 0
    assert "never on a re-run" in capsys.readouterr().out
