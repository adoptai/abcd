"""Scenario (a): a client workspace is a private git repo (local bare remotes only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.conftest import run_inproc, run_subprocess, write_env_file
from tests.integration.fake_bff import DEFAULT_CLIENT_ID, DEFAULT_SECRET, FakeBFFServer
from tests.integration.helpers import bare_repo, git, require_command

pytestmark = pytest.mark.integration


def test_ws_create_list_show_use_delete(ws_dir: Path) -> None:
    created = run_inproc("ws", "create", "--id", "acme", "--name", "Acme Corp", "--client", "acme")
    assert created.code == 0, created.text
    env_dir = ws_dir / "acme"
    meta = json.loads((env_dir / "env.json").read_text())
    assert meta["env_id"] == "acme" and meta["client"] == "acme"
    assert (
        env_dir / ".env"
    ).exists()  # the credentials template: filled in by hand, never committed

    assert run_inproc("ws", "create", "--id", "acme", "--name", "Again").code == 1  # exists
    listed = run_inproc("ws", "list", "--json")
    assert "acme" in [e["env_id"] for e in listed.json["data"]["environments"]]
    shown = run_inproc("ws", "show", "acme", "--json")
    assert shown.code == 0 and shown.json["data"]["env_id"] == "acme"
    assert run_inproc("ws", "use", "acme").code == 0
    assert (ws_dir / ".active_env").read_text().strip() == "acme"
    # the active workspace is the default for --env
    assert run_inproc("ws", "show", "acme").code == 0

    assert run_inproc("ws", "delete", "acme").code == 1  # never prompts: refuses without --yes
    assert env_dir.exists()
    assert run_inproc("ws", "delete", "acme", "--yes").code == 0
    assert not env_dir.exists()


def test_ws_commands_in_a_real_subprocess(tmp_path: Path) -> None:
    root = tmp_path / "ws-root"
    root.mkdir()
    created = run_subprocess(root, "ws", "create", "--id", "acme", "--name", "Acme")
    assert created.code == 0, created.text
    assert (root / "acme" / "env.json").exists()
    listed = run_subprocess(root, "ws", "list", "--json")
    assert listed.code == 0 and json.loads(listed.out.strip().splitlines()[-1])["ok"] is True
    unknown = run_subprocess(root, "ws", "show", "nope")
    assert unknown.code != 0


def test_workspace_repo_flow_init_status_push_and_clone(
    tmp_path: Path, ws_dir: Path, bff: FakeBFFServer
) -> None:
    require_command("ws init", "ws status", "ws clone")
    bare = bare_repo(tmp_path / "remotes")
    assert (
        run_inproc("ws", "create", "--id", "acme", "--name", "Acme", "--client", "acme").code == 0
    )
    env_dir = ws_dir / "acme"
    write_env_file(env_dir, bff.url, DEFAULT_CLIENT_ID, DEFAULT_SECRET)

    dry = run_inproc("ws", "init", "--remote", str(bare), "--env", "acme", "--dry-run")
    assert dry.code == 0 and not (env_dir / ".git").exists()

    init = run_inproc("ws", "init", "--remote", str(bare), "--env", "acme")
    assert init.code == 0, init.text
    assert (env_dir / ".git").is_dir()
    for builder_file in ("builder.json", "ADOPT.md", ".gitignore"):
        assert (env_dir / builder_file).exists(), builder_file
    assert git(env_dir, "ls-files", ".env").strip() == ""  # credentials are never tracked
    assert ".env" in (env_dir / ".gitignore").read_text()
    assert "main" in git(bare, "branch", "--list")  # the first commit was pushed

    status = run_inproc("ws", "status", "--env", "acme", "--json")
    assert status.code == 0, status.text
    assert status.json["ok"] is True

    # a branch commit and push (no command commits to the default branch except init)
    git(env_dir, "checkout", "-b", "feat/invoice-triage")
    skill = env_dir / "harness" / "skills" / "invoice-triage"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: invoice-triage\ndescription: Triage invoices.\n---\n\n# x\n"
    )
    git(env_dir, "add", "-A")
    git(env_dir, "commit", "-m", "Add invoice-triage skill")
    git(env_dir, "push", "-u", "origin", "feat/invoice-triage")
    assert "feat/invoice-triage" in git(bare, "branch", "--list")
    behind = run_inproc("ws", "status", "--env", "acme", "--json")
    assert behind.code == 0 and "feat/invoice-triage" in behind.text

    # no secret ever reached the remote
    pushed = git(bare, "log", "--all", "-p")
    assert DEFAULT_SECRET not in pushed and bff.url not in pushed.replace("ADOPT.md", "")

    # clone into a second workspaces dir on "another machine"
    second = tmp_path / "second-machine"
    second.mkdir()
    cloned = run_subprocess(second, "ws", "clone", str(bare), "--env", "acme")
    assert cloned.code == 0, cloned.text
    clone_dir = second / "acme"
    assert (clone_dir / "builder.json").exists() and (clone_dir / "ADOPT.md").exists()
    assert json.loads((clone_dir / "env.json").read_text())["env_id"] == "acme"
    assert not (
        clone_dir / "harness" / "skills" / "invoice-triage"
    ).exists()  # that is on the branch
    assert git(clone_dir, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (clone_dir / ".git" / "hooks" / "pre-commit").exists()  # the workspace hook is installed
    assert run_subprocess(second, "ws", "status", "--env", "acme").code == 0


def test_ws_init_refuses_the_default_workspace(ws_dir: Path, tmp_path: Path) -> None:
    require_command("ws init")
    (ws_dir / "default").mkdir()
    (ws_dir / "default" / "env.json").write_text(json.dumps({"env_id": "default"}))
    res = run_inproc("ws", "init", "--remote", str(bare_repo(tmp_path / "r")), "--env", "default")
    assert res.code != 0
    assert not (ws_dir / "default" / ".git").exists()


def test_ws_init_refuses_a_remote_with_credentials(ws_dir: Path, bff: FakeBFFServer) -> None:
    require_command("ws init")
    assert run_inproc("ws", "create", "--id", "acme", "--name", "Acme").code == 0
    res = run_inproc(
        "ws", "init", "--remote", "https://user:tok3n@example.test/acme/acme.git", "--env", "acme"
    )
    assert res.code == 1
    assert "tok3n" not in res.text
