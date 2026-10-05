#!/usr/bin/env python3
"""Build ``noui-bundle.zip`` (the NoUI toolkit) into this folder.

The publishable ``noui`` harness skill ships the toolkit as a single binary aux
zip; the harness stages it into the sandbox and the agent runs ``unzip`` then
``pip install -e .``. The zip's root is the bundle root (``pyproject.toml`` at
top). Excludes the venv/workbench/caches/.env so it stays small and clean.

Run:  python harness-skills/noui/build_bundle.py
"""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # harness-skills/noui/
_BUNDLE = _HERE.parent.parent / "cli" / "noui"  # cli/noui/ (toolkit source)
_OUT = _HERE / "noui-bundle.zip"
_EXCLUDE_DIRS = {".venv", "workbench", "__pycache__", ".git", "noui.egg-info"}
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx")


def _tracked_files() -> list[Path] | None:
    """The bundle's git-tracked files, or None outside a git checkout.

    Shipping only tracked files means a local build cannot pick up whatever is
    lying around the toolkit dir (a .env.local, a key, tool caches)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "."],
            cwd=_BUNDLE,
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        return None
    return [_BUNDLE / f for f in out.split("\0") if f]


def _excluded(rel: Path) -> bool:
    if any(part in _EXCLUDE_DIRS or part.startswith(".") for part in rel.parts):
        # every dotfile/dot-dir: .env*, .envrc, .pytest_cache, .ruff_cache, ...
        return True
    return rel.suffix in _EXCLUDE_SUFFIXES


def build() -> Path:
    if not (_BUNDLE / "pyproject.toml").exists():
        raise SystemExit(f"toolkit bundle not found at {_BUNDLE} (expected pyproject.toml)")
    buf = io.BytesIO()
    manifest: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        files = _tracked_files() or [p for p in _BUNDLE.rglob("*") if p.is_file()]
        for p in sorted(files):
            if not p.is_file():
                continue
            rel = p.relative_to(_BUNDLE)
            # Drop the venv/caches and any env files: in the harness the toolkit
            # is configured by env vars the broker/harness inject, so a shipped
            # .env / .env.example is dead weight and only invites the agent to
            # "configure credentials" (which it must not do).
            if _excluded(rel):
                continue
            zf.write(p, str(rel))
            manifest.append(str(rel))
    _OUT.write_bytes(buf.getvalue())
    print(f"wrote {_OUT} ({_OUT.stat().st_size} bytes, {len(manifest)} files)")
    print("includes pyproject.toml:", "pyproject.toml" in manifest)
    return _OUT


if __name__ == "__main__":
    build()
