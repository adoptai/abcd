"""End-to-end workspace flow against local bare repos only (no network)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cli.builder_common import precommit, repo
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError

ID = ("Test User", "test@example.test")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def test_full_workspace_flow(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    workspaces = tmp_path / "A" / "workspaces"
    workspaces.mkdir(parents=True)
    ws = workspaces / "acme"
    ws.mkdir()
    (ws / "env.json").write_text(json.dumps({"env_id": "acme", "name": "Acme"}))

    # init_layout -> init_repo -> install_hook
    assert bws.init_layout(ws, repo_remote=str(bare))
    repo.init_repo(ws, "main")
    hook = precommit.install_hook(ws)
    assert hook.is_file()
    repo.add_remote(ws, str(bare))

    # first commit goes to the default branch (allowed only with allow_default)
    first = repo.commit(ws, "init workspace", identity=ID)
    with pytest.raises(RefusedError):
        repo.push(ws, "main")
    repo.push(ws, "main", allow_default=True)

    # the hook still guards commits
    (ws / ".env").write_text("A=1\n")
    git(ws, "add", "-f", ".env")
    with pytest.raises(RefusedError):
        repo.commit(ws, "bad", identity=ID)
    git(ws, "reset", "-q", ".env")
    (ws / ".env").unlink()

    # feature branch: commit, push, default branch push refused
    repo.create_branch(ws, "feature/a")
    (ws / "harness" / "note.md").write_text("note\n")
    feat = repo.commit(ws, "add note", identity=ID)
    repo.push(ws)  # current branch = feature/a
    assert git(bare, "rev-parse", "refs/heads/feature/a") == feat
    repo.checkout(ws, "main")
    with pytest.raises(RefusedError):
        repo.push(ws)

    # tag at a sha; not on default until merged into the remote's main
    repo.create_tag(ws, "a@v1", feat, identity=ID)
    repo.push_tag(ws, "a@v1")
    assert "a@v1" in repo.list_tags(ws)
    assert not repo.is_on_default_branch(ws, feat)
    assert repo.is_on_default_branch(ws, first)

    # merge feature/a into the bare remote's main (simulating a PR merge)
    other = tmp_path / "merger"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    git(other, "checkout", "-q", "main")
    git(other, "merge", "-q", "--no-ff", "-m", "merge", "origin/feature/a")
    git(other, "push", "-q", "origin", "main")
    assert repo.is_on_default_branch(ws, feat)
    assert repo.is_on_default_branch(ws, "a@v1")

    # clone into a second WORKSPACES dir: env.json must match the folder name
    ws2_root = tmp_path / "B" / "workspaces"
    cloned = repo.clone(str(bare), ws2_root / "acme")
    assert bws.env_json_matches(cloned)
    assert (cloned / "harness" / "note.md").is_file()
    assert (cloned / ".env").is_file()  # fresh .env template

    with pytest.raises(UsageError):
        repo.clone(str(bare), ws2_root / "other-name")
    assert not (ws2_root / "other-name").exists()
