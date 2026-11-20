"""Shared helpers for the meta agent tests: a git workspace and the fake BFF server.

The workspace is a real local git repo (no remote). ``home_env`` adds the fake BFF server and a
workspace whose ``.env`` carries a valid PAT, so ``abcd cloud setup --home`` runs against HTTP.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common.workspace import default_builder_config, save_builder_config
from cli.core.context import (
    WORKSPACES_ENV_VAR,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.wdl_common import workspace_manager as wm
from tests.integration.fake_bff import DEFAULT_CLIENT_ID, DEFAULT_SECRET, FakeBFFServer


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=60
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


def make_workspace(root: Path, name: str, *, role: str | None = None, **config: Any) -> Path:
    """``root/<name>`` as a git repo on ``main`` with one commit and a builder.json."""
    ws = root / name
    ws.mkdir()
    (ws / "env.json").write_text(json.dumps({"env_id": name, "client": name}))
    cfg = default_builder_config(**config)
    if role:
        cfg["role"] = role
    save_builder_config(ws, cfg)
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "user.name", "t")
    git(ws, "config", "user.email", "t@example.test")
    (ws / ".gitignore").write_text(".env\n.builder/\ncontext/\n")
    git(ws, "add", ".")
    git(ws, "commit", "-q", "-m", "init")
    return ws


def run_cli(*argv: str) -> tuple[int, dict[str, Any]]:
    """``abcd <argv> --json`` in process; returns the exit code and the JSON document."""
    import cli.abcd as abcd_main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = int(abcd_main.main([*argv, "--json"]) or 0)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    lines = [ln for ln in out.getvalue().splitlines() if ln.startswith("{")]
    return code, json.loads(lines[-1]) if lines else {}


@dataclass
class Env:
    root: Path
    bff: FakeBFFServer | None = None

    def ws(self, name: str, **kw: Any) -> Path:
        return make_workspace(self.root, name, **kw)

    def write_env(self, ws: Path) -> None:
        assert self.bff is not None
        (ws / ".env").write_text(
            f"ADOPT_WEBUI_ENDPOINT={self.bff.url}\n"
            f"ADOPT_HARNESS_PAT_CLIENT_ID={DEFAULT_CLIENT_ID}\n"
            f"ADOPT_HARNESS_PAT_SECRET={DEFAULT_SECRET}\n"
        )


@pytest.fixture
def meta_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """An isolated workspaces dir wired through ABCD_WORKSPACES_DIR, with git identity set."""
    original = wm.WORKSPACES_DIR
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(root))
    monkeypatch.setenv("ABCD_EVENTS_UPLOAD", "0")
    for key, val in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }.items():
        monkeypatch.setenv(key, val)
    for name in (
        "ADOPT_WEBUI_ENDPOINT",
        "ADOPT_HARNESS_PAT_CLIENT_ID",
        "ADOPT_HARNESS_PAT_SECRET",
        "ADOPT_HARNESS_BEARER_TOKEN",
    ):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    apply_workspaces_override()
    try:
        yield Env(root)
    finally:
        monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
        set_workspaces_dir(original)


@pytest.fixture
def home_env(meta_env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """``meta_env`` plus a running fake BFF; the abcd bundle build is replaced by a tiny zip."""
    import zipfile

    from cli import builder_home

    def fake_bundle(out: Path) -> None:
        with zipfile.ZipFile(out, "w") as z:  # fixed timestamp: same input, same bytes
            z.writestr(
                zipfile.ZipInfo("pyproject.toml", (2020, 1, 1, 0, 0, 0)),
                "[tool.poetry]\nname = 'abcd'\n",
            )

    monkeypatch.setattr(builder_home, "BUNDLE_BUILDER", fake_bundle)
    with FakeBFFServer() as bff:
        meta_env.bff = bff
        yield meta_env
