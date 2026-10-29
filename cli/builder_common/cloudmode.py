"""Is this a cloud (harness sandbox) run? One answer for every Builder command.

True when ANY of these holds, so unsetting a single variable is not enough to leave it:

- ``ADOPT_CONVERSATION_ID`` is set (the harness injects it);
- ``ABCD_PAT_CLIENT_ID`` or ``ADOPT_APP_BASE_URL`` is set (sandbox-injected names: a local FDE
  uses ``ADOPT_HARNESS_PAT_*`` in the workspace ``.env``);
- the workspace (the given directory, or the workspace holding it or the cwd, i.e. the nearest
  parent with an ``env.json``; never a sibling workspace) has the marker ``.builder/cloud.json``
  that ``abcd cloud bootstrap`` writes. A found marker is remembered for the process.

This is defence in depth against mistakes, not a security boundary: an agent with a PAT and a
shell can unset variables, delete the marker (it is a plain file in a git-ignored folder) or call
the BFF directly. See ``release_approval`` and PLAN.md sections 6 and 21 for where the real
boundary comes from (the platform).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MARKER_REL = Path(".builder") / "cloud.json"
CLOUD_ENV_VARS = ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL")


def _env_set() -> bool:
    return any(os.environ.get(v, "").strip() for v in CLOUD_ENV_VARS)


def _workspace_dir(start: Path) -> Path | None:
    """The workspace directory containing ``start``: the first of it and its parents that has
    an ``env.json``."""
    for d in (start, *start.parents):
        try:
            if (d / "env.json").is_file():
                return d
        except OSError:
            continue
    return None


def _candidates(ws: Path | None) -> list[Path]:
    """Marker paths to look at: the given directory, the workspace that holds it and the
    workspace that holds the cwd. Never a sibling workspace (a marker next to this one says
    nothing about it)."""
    bases: list[Path] = []
    if ws is not None:
        bases.append(Path(ws))
        bases.append(_workspace_dir(Path(ws)) or Path(ws))
    try:
        cwd = Path.cwd()
        bases.append(_workspace_dir(cwd) or cwd)
    except OSError:
        pass
    seen: list[Path] = []
    for base in bases:
        p = base / MARKER_REL
        if p not in seen:
            seen.append(p)
    return seen


# A marker once found stays found for this process (per workspace path): deleting the file
# mid-run does not turn a cloud run into a local one. Only hits are cached.
_FOUND: dict[str, Path] = {}


def find_marker(ws: Path | None = None) -> Path | None:
    try:
        key = f"{Path(ws).absolute() if ws is not None else ''}|{Path.cwd()}"
    except OSError:
        key = f"{ws}|"
    if key in _FOUND:
        return _FOUND[key]
    for p in _candidates(ws):
        try:
            if p.is_file():
                _FOUND[key] = p
                return p
        except OSError:
            continue
    return None


def read_marker(ws: Path | None = None) -> dict[str, Any] | None:
    """The marker's content, ``{}`` when it exists but is unreadable, None when absent."""
    p = find_marker(ws)
    if p is None:
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cloud_mode(ws: Path | None = None) -> bool:
    return _env_set() or find_marker(ws) is not None


def write_marker(ws: Path, conversation_id: str) -> Path:
    path = Path(ws) / MARKER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"conversation_id": conversation_id, "by": "abcd cloud bootstrap"}))
    return path
