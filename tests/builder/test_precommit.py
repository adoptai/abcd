"""Tests for the workspace pre-commit hook (cli.builder_common.precommit)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cli.builder_common import precommit, repo
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError

ID = ("Test User", "test@example.test")
GH_TOKEN = "ghp_" + "Z9y8X7w6V5" * 4


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "acme"
    d.mkdir()
    (d / "env.json").write_text(json.dumps({"env_id": "acme", "name": "Acme"}))
    repo.init_repo(d, "main")
    bws.init_layout(d)
    precommit.install_hook(d)
    return d


def stage(ws: Path, rel: str, content: str | bytes) -> None:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    subprocess.run(["git", "-C", str(ws), "add", "-f", "--", rel], check=True)


def try_commit(ws: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "m"],
        cwd=ws, capture_output=True, text=True,
    )  # fmt: skip


def test_hook_accepts_clean_commit(ws: Path) -> None:
    stage(ws, "harness/skills/s1/SKILL.md", "---\nname: s1\n---\nhello\n")
    stage(
        ws, "adopt_profile.json",
        json.dumps({"base_url": "", "security_params": {}, "workflow_params": {}}),
    )  # fmt: skip
    stage(ws, "builder.json", (ws / "builder.json").read_text())
    proc = try_commit(ws)
    assert proc.returncode == 0, proc.stderr
    assert precommit.check_staged(ws) == []


def test_hook_refuses_secret(ws: Path) -> None:
    stage(ws, "harness/skills/s1/scripts/run.py", f'TOKEN = "{GH_TOKEN}"\n')
    proc = try_commit(ws)
    assert proc.returncode != 0
    assert "commit refused" in proc.stderr and "GitHub token" in proc.stderr
    assert GH_TOKEN not in proc.stderr + proc.stdout
    assert "run.py:1" in proc.stderr


def test_hook_reads_index_not_worktree(ws: Path) -> None:
    stage(ws, "a.py", f'K = "{GH_TOKEN}"\n')
    (ws / "a.py").write_text("clean = 1\n")  # worktree is clean, the index is not
    assert any("a.py" in p for p in precommit.check_staged(ws))


def test_refuses_cookie_bearing_bundle(ws: Path) -> None:
    bundle = {
        "session_id": "s",
        "click_events": [],
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "headers": [{"name": "Cookie", "value": "sid=abc123def456"}],
                        }
                    }
                ]
            }
        },
    }
    stage(ws, "harness/mcp_servers/x/capture.json", json.dumps(bundle))
    problems = precommit.check_staged(ws)
    assert any("cookies or Authorization" in p for p in problems)
    assert "abc123def456" not in " ".join(problems)
    assert try_commit(ws).returncode != 0


def test_refuses_unredacted_sensitive_events(ws: Path) -> None:
    bundle = {
        "click_events": [{"field_role": "password", "is_redacted": False, "value": "hunter2xx"}],
        "har": {"log": {"entries": []}},
    }
    stage(ws, "harness/mcp_servers/x/events.json", json.dumps(bundle))
    problems = precommit.check_staged(ws)
    assert any("unredacted password/otp" in p for p in problems)
    assert "hunter2xx" not in " ".join(problems)


def test_accepts_redacted_bundle_shape(ws: Path) -> None:
    bundle = {
        "click_events": [
            {"field_role": "password", "is_redacted": True, "value": "[REDACTED]"},
            {"field_role": "text", "value": "hello"},
        ],
        "har": {
            "log": {
                "entries": [{"request": {"headers": [{"name": "Cookie", "value": "[REDACTED]"}]}}]
            }
        },
    }
    stage(ws, "harness/mcp_servers/x/ok.json", json.dumps(bundle))
    assert precommit.check_staged(ws) == []


def test_refuses_security_params_values(ws: Path) -> None:
    stage(ws, "adopt_profile.json", json.dumps({"security_params": {"cookie": "v"}}))
    problems = precommit.check_staged(ws)
    assert problems == ["adopt_profile.json: security_params holds values; cookies belong in .env"]
    assert try_commit(ws).returncode != 0
    stage(ws, "adopt_profile.json", json.dumps({"security_params": {"cookie": ""}}))
    assert precommit.check_staged(ws) == []


@pytest.mark.parametrize(
    "rel",
    [
        ".env",
        "sub/.env.local",
        "harness/.token_cache.json",
        "harness/bundles/b1.json",
        "harness/skills/s1/recording_bundle.json",
        "harness/other/recording_bundle.json",
        "capture.har",
        "harness/traces/r1/run.json",
        "agents/a/traces/t.json",
        "keys/server.pem",
        "context/pull.md",
        ".builder/scratch.json",
    ],
)
def test_refuses_ignored_paths(ws: Path, rel: str) -> None:
    stage(ws, rel, "{}")
    problems = precommit.check_staged(ws)
    assert any("ignore list" in p and rel in p for p in problems), problems
    assert try_commit(ws).returncode != 0


@pytest.mark.parametrize(
    "rel",
    [
        "harness/skills/s1/SKILL.md",
        "harness/skills/s1/operations.json",
        "harness/agents/a/process.yaml",
        "harness/bundles.lock.json",
        "agents/a/widdle.json",
        "env.json",
        "ADOPT.md",
    ],
)
def test_allows_source_paths(rel: str) -> None:
    assert not precommit.is_ignored_path(rel)


def test_secret_in_zip_member_refused(ws: Path) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner.txt", f"key = {GH_TOKEN}\n")
    stage(ws, "harness/skills/s1/aux.zip", buf.getvalue())
    assert any("aux.zip!inner.txt" in p for p in precommit.check_staged(ws))


def test_hook_script_content_and_foreign_hook(ws: Path, tmp_path: Path) -> None:
    hook = ws / ".git" / "hooks" / "pre-commit"
    text = hook.read_text()
    assert "-m cli.builder_common.precommit" in text and "PYTHONPATH=" in text
    assert hook.stat().st_mode & 0o111
    assert precommit.install_hook(ws) == hook  # idempotent for our own hook
    hook.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(RefusedError):
        precommit.install_hook(ws)
    precommit.install_hook(ws, force=True)
    assert precommit.HOOK_MARKER in hook.read_text()


def test_main_exit_codes(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert precommit.main([str(ws)]) == 0
    stage(ws, ".env", "A=1")
    assert precommit.main([str(ws)]) == 1
    assert "commit refused" in capsys.readouterr().err
    assert precommit.main([str(ws.parent / "missing")]) == 1


def test_hook_works_from_unrelated_cwd_and_without_pythonpath(ws: Path) -> None:
    """The hook uses the installing interpreter; the source-root fallback needs no PYTHONPATH."""
    import os
    import subprocess

    (ws / "notes.txt").write_text("hello\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run(["git", "-C", str(ws), "add", "notes.txt"], check=True, env=env)
    proc = subprocess.run(
        ["git", "-C", str(ws), "-c", "user.name=T", "-c", "user.email=t@e.test",
         "commit", "-q", "-m", "x"],
        cwd=ws.parent, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    text = (ws / ".git" / "hooks" / "pre-commit").read_text()
    assert "import cli.builder_common.precommit" in text
