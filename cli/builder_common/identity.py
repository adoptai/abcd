"""Who is acting: ONE helper for every Builder command (PLAN.md sections 6 and 14).

Order: ``cloud:<process>`` in a cloud run (see ``cloudmode.cloud_mode``: an environment variable
alone must not let a cloud run pose as an FDE), else ``ABCD_FDE``, else the workspace's git
``user.email``, else ``$USER``. ``slug`` turns a text into a safe path segment (the one slug
helper of the CLI).
"""

from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

from cli.builder_common.cloudmode import cloud_mode

__all__ = ["cloud_mode", "current_user", "slug"]


def _git_email(ws: Path) -> str:
    from cli.builder_common import repo  # lazy: repo imports ``slug`` from here

    proc = repo.run_git(ws, "config", "user.email", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def current_user(ws: Path | None = None) -> str:
    if cloud_mode(ws):
        return "cloud:" + (os.environ.get("ABCD_CLOUD_PROCESS", "").strip() or "abcd-runner")
    named = os.environ.get("ABCD_FDE", "").strip()
    if named:
        return named
    if ws is not None and ws.is_dir():
        email = _git_email(ws)
        if email:
            return email
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def slug(text: str, *, strict: bool = False, maxlen: int = 128) -> str:
    """Default (an identity): ``jo@x.com`` -> ``jo-x.com``; ``cloud:abcd-runner`` ->
    ``cloud-abcd-runner``; matches ``[A-Za-z0-9][A-Za-z0-9._-]*``, ``unknown`` when empty.
    ``strict`` (names, branches): lower case, runs of anything but ``[a-z0-9]`` become one ``-``,
    empty stays empty."""
    if strict:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:maxlen].strip("-")
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return out[:maxlen] or "unknown"
