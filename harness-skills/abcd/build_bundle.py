#!/usr/bin/env python3
"""Build ``abcd-bundle.zip`` (abcd itself, for the cloud runner) into this folder.

Mirrors ``harness-skills/noui/build_bundle.py``: git-tracked files only, so a
local build cannot pick up a stray ``.env``, key or cache. The zip root is the
repo root (``pyproject.toml`` at top). It holds ``cli/`` (NoUI included),
``abcd-builder/``, ``adopt-skill-review/``, ``pyproject.toml`` and ``README.md``,
and leaves out ``workspaces/``, ``tabby/`` and every dotfile or ``.env``.
Entries are sorted and carry a fixed timestamp, so the same tree gives the same
bytes.

Run:  python harness-skills/abcd/build_bundle.py
"""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # harness-skills/abcd/
_REPO = _HERE.parent.parent
_OUT = _HERE / "abcd-bundle.zip"

INCLUDE = ("cli/", "abcd-builder/", "adopt-skill-review/", "pyproject.toml", "README.md")
_EXCLUDE_DIRS = {
    "workspaces",
    "tabby",
    "tests",
    ".venv",
    "workbench",
    "__pycache__",
    ".git",
    "node_modules",
}
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".har", ".egg-info")
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _tracked_files(root: Path) -> list[str]:
    """Git-tracked paths (relative, posix) under the include list. Raises outside git."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", *INCLUDE],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(
            f"{root} is not a git checkout; the bundle ships tracked files only"
        ) from e
    return [f for f in out.split("\0") if f]


def _excluded(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in _EXCLUDE_DIRS or p.startswith(".") for p in parts):
        # every dotfile or dot-dir: .env*, .envrc, .pytest_cache, .ruff_cache, ...
        return True
    return rel.endswith(_EXCLUDE_SUFFIXES) or any(p.endswith(".egg-info") for p in parts)


def bundle_members(root: Path = _REPO) -> list[str]:
    """Sorted archive names the bundle would contain."""
    return sorted(
        rel for rel in set(_tracked_files(root)) if not _excluded(rel) and (root / rel).is_file()
    )


def build(root: Path = _REPO, out: Path = _OUT) -> Path:
    """Write the zip to ``out`` and return it."""
    members = bundle_members(root)
    if "pyproject.toml" not in members:
        raise SystemExit(f"pyproject.toml not found in {root}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in members:
            info = zipfile.ZipInfo(rel, _FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if (root / rel).stat().st_mode & 0o111 else 0o644) << 16
            zf.writestr(info, (root / rel).read_bytes())
    out.write_bytes(buf.getvalue())
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(members)} files)")
    return out


if __name__ == "__main__":
    build()
