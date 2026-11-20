"""Helpers shared by the end-to-end tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cli.abcd import build_registry


def has_command(name: str) -> bool:
    """True when ``abcd <name>`` (for example ``"hitl resolve"``) is in the command registry."""
    return build_registry().resolve(name.split()) is not None


def require_command(*names: str) -> None:
    """Skip the calling test until every named command is registered.

    Wave-2 commands land in parallel; a scenario written against one skips cleanly until its
    command exists and runs unchanged after integration.
    """
    absent = [n for n in names if not has_command(n)]
    if absent:
        pytest.skip(f"command not registered yet: {', '.join('abcd ' + n for n in absent)}")


def git(cwd: Path, *args: str) -> str:
    """Run git (local repos only) and return stdout; raises with stderr on failure."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr}")
    return proc.stdout


def bare_repo(root: Path, name: str = "acme.git") -> Path:
    """A local bare repository with ``main`` as its default branch."""
    path = root / name
    path.mkdir(parents=True)
    git(path, "init", "--bare", "--initial-branch=main", ".")
    return path


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def event_names(envelopes: list[dict[str, Any]]) -> list[str]:
    return [e["event"]["event_name"] for e in envelopes if e.get("event")]


def block_types(envelopes: list[dict[str, Any]]) -> list[str]:
    return [
        e["event"]["data"]["block"]["type"]
        for e in envelopes
        if e.get("event", {}).get("event_name") == "block_delta"
    ]


def single_run_dir(workspace: Path) -> Path:
    runs = sorted((workspace / "harness" / "traces").iterdir())
    assert len(runs) == 1, runs
    return runs[0]
