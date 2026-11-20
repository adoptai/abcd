"""Fixtures for the abcd CLI framework tests: an isolated workspaces directory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cli.core.context import (  # noqa: E402
    WORKSPACES_ENV_VAR,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.wdl_common import workspace_manager as wm  # noqa: E402


@pytest.fixture(autouse=True)
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temp workspaces dir wired in through ABCD_WORKSPACES_DIR (restored afterwards)."""
    original = wm.WORKSPACES_DIR
    target = tmp_path / "workspaces"
    target.mkdir()
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(target))
    apply_workspaces_override()
    yield target
    monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
    set_workspaces_dir(original)


@pytest.fixture
def make_env(ws_dir: Path) -> Callable[[str], Path]:
    def _make(name: str = "acme") -> Path:
        d = ws_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "env.json").write_text(json.dumps({"env_id": name}))
        return d

    return _make


def run_cli(ws: Path, *argv: str, script: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``abcd`` (or an old ``cli/<script>.py``) in a fresh process against ``ws``."""
    if script:
        cmd = [sys.executable, str(ROOT / "cli" / script), *argv]
    else:
        cmd = [sys.executable, "-m", "cli.abcd", *argv]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(ROOT),
        "ABCD_WORKSPACES_DIR": str(ws),
        "HOME": str(ws.parent),
    }
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT, check=False)
