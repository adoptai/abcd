#!/usr/bin/env python3
"""Claude Code session hook for the Adopt Builder (PLAN.md R16).

Usage (from hooks.json): ``abcd_session_hook.py start|end``. Claude Code sends the hook
payload as JSON on stdin (``session_id``, ``transcript_path``, ``cwd``). When an abcd
workspace with ``builder.json`` is active, this runs::

    abcd events session start|end --transcript <path> --env <env> --json

It never blocks or fails the session: every error is logged to
``<workspace>/.builder/hooks.log`` (an ignored folder) and the exit code is always 0.
Standard library only, so it runs wherever Claude Code does (Windows included).

Environment:
  ABCD_HOOK_CMD        command prefix to run instead of ``abcd`` (shell-style, e.g. a test double)
  ABCD_HOOKS_DISABLE   set to 1 to turn the hook off
  ABCD_WORKSPACES_DIR  workspaces folder override, as for the abcd CLI
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

TIMEOUT_SECONDS = 25
KINDS = ("start", "end")


def _active_env(workspaces: Path) -> str | None:
    try:
        name = (workspaces / ".active_env").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def find_workspace(cwd: Path, environ: Mapping[str, str]) -> Path | None:
    """The active workspace (a folder with builder.json), or None when the Builder is not in use."""
    override = environ.get("ABCD_WORKSPACES_DIR")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    for parent in [cwd, *cwd.parents]:
        if (parent / "builder.json").is_file() and (parent / "env.json").is_file():
            return parent  # cwd is inside a workspace
        candidates.append(parent / "workspaces")
    for workspaces in candidates:
        env = _active_env(workspaces)
        if env and (workspaces / env / "builder.json").is_file():
            return workspaces / env
    return None


def build_command(environ: Mapping[str, str]) -> list[str] | None:
    override = environ.get("ABCD_HOOK_CMD")
    if override:
        return shlex.split(override, posix=os.name != "nt")
    found = shutil.which("abcd")
    return [found] if found else None


def _log(workspace: Path | None, message: str) -> None:
    if workspace is None:
        return
    try:
        folder = workspace / ".builder"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "hooks.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except OSError:
        pass


def run(kind: str, payload: dict, environ: Mapping[str, str]) -> int:
    """Run the hook. Always returns 0."""
    workspace: Path | None = None
    try:
        if environ.get("ABCD_HOOKS_DISABLE") == "1" or kind not in KINDS:
            return 0
        cwd = Path(str(payload.get("cwd") or os.getcwd()))
        workspace = find_workspace(cwd, environ)
        if workspace is None:
            return 0
        prefix = build_command(environ)
        if prefix is None:
            _log(workspace, f"{kind}: abcd is not on PATH; skipped")
            return 0
        cmd = [*prefix, "events", "session", kind]
        transcript = payload.get("transcript_path")
        if transcript:
            cmd += ["--transcript", str(transcript)]
        cmd += ["--env", workspace.name, "--json"]
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            cwd=str(cwd) if cwd.is_dir() else None,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **environ},
        )
        if proc.returncode != 0:
            _log(workspace, f"{kind}: exit {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
    except Exception as exc:  # the hook must never fail the session
        _log(workspace, f"{kind}: {type(exc).__name__}: {exc}")
    return 0


def main(argv: list[str] | None = None, stdin: object = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    kind = args[0] if args else ""
    payload: dict = {}
    try:
        stream = sys.stdin if stdin is None else stdin
        raw = stream.read() if hasattr(stream, "read") else ""
        data = json.loads(raw) if raw and raw.strip() else {}
        payload = data if isinstance(data, dict) else {}
    except Exception:
        payload = {}
    return run(kind, payload, os.environ)


if __name__ == "__main__":
    sys.exit(main())
