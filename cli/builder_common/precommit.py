"""Workspace pre-commit hook logic (PLAN.md section 5, "Committed versus ignored").

Refuses a commit when a staged file:
- matches the workspace ignore list (bundles, ``recording_bundle.json``, ``*.har``, ``.env``...);
- fails abcd's content secret scan;
- is bundle-shaped JSON with unredacted password/otp events or cookie/auth headers;
- is ``adopt_profile.json`` with non-empty ``security_params`` values.

Findings name the path and kind, never the matched value.
"""

from __future__ import annotations

import fnmatch
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from cli.builder_common import template_loader as templates
from cli.builder_common.errors import RefusedError, UsageError
from cli.harness_common.secret_scan import scan_bytes

HOOK_MARKER = "# abcd-builder-precommit"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTH_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}


def _count_sensitive_unredacted(bundle: dict[str, Any]) -> int:
    try:
        from noui_core.capture.bundle import count_sensitive_unredacted
    except ImportError:
        noui_root = str(_REPO_ROOT / "cli" / "noui")
        if noui_root not in sys.path:
            sys.path.insert(0, noui_root)
        from noui_core.capture.bundle import count_sensitive_unredacted
    return int(count_sensitive_unredacted(bundle))


def is_ignored_path(rel: str) -> bool:
    """Whether a workspace-relative path matches the workspace ignore list."""
    rel = rel.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    parts = rel.split("/")
    for pattern in templates.gitignore_patterns():
        is_dir = pattern.endswith("/")
        pat = pattern.rstrip("/")
        if "/" in pat:  # anchored to the workspace root
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            if is_dir:
                prefixes = prefixes[:-1]
            if any(fnmatch.fnmatchcase(c, pat) for c in prefixes):
                return True
        else:
            names = parts[:-1] if is_dir else parts
            if any(fnmatch.fnmatchcase(n, pat) for n in names):
                return True
    return False


def _has_auth_material(node: Any) -> bool:
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str) and name.lower() in _AUTH_HEADERS:
            value = node.get("value")
            if isinstance(value, str) and value not in ("", "[REDACTED]"):
                return True
        cookies = node.get("cookies")
        if isinstance(cookies, list) and cookies:
            return True
        return any(_has_auth_material(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_auth_material(v) for v in node)
    return False


def check_file(rel: str, data: bytes) -> list[str]:
    """Violations (strings, no secret values) for one staged file."""
    problems: list[str] = []
    base = rel.rsplit("/", 1)[-1]
    if is_ignored_path(rel) or base == "recording_bundle.json":
        problems.append(f"{rel}: path is on the workspace ignore list (never commit it)")
    for finding in scan_bytes(rel, data):
        problems.append(f"{finding}: secret scan")
    if rel.endswith(".json") and b"\x00" not in data[:4096]:
        try:
            doc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            doc = None
        if isinstance(doc, dict):
            if base == "adopt_profile.json":
                params = doc.get("security_params")
                if isinstance(params, dict) and any(
                    v not in (None, "", {}, []) for v in params.values()
                ):
                    problems.append(f"{rel}: security_params holds values; cookies belong in .env")
            if "har" in doc or "click_events" in doc:
                if _count_sensitive_unredacted(doc) > 0:
                    problems.append(f"{rel}: bundle has unredacted password/otp events")
                if _has_auth_material(doc.get("har")):
                    problems.append(f"{rel}: bundle carries cookies or Authorization headers")
    return problems


def _git(ws: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise UsageError(
            f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def staged_files(ws: Path) -> list[str]:
    out = _git(ws, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.decode("utf-8", errors="surrogateescape").split("\0") if p]


def check_staged(ws: Path) -> list[str]:
    """All violations across the staged files of the workspace repo (index content)."""
    problems: list[str] = []
    for rel in staged_files(ws):
        problems.extend(check_file(rel, _git(ws, "show", f":{rel}")))
    return problems


def hook_script(ws: Path) -> str:
    """POSIX sh hook: run with the interpreter that installed abcd.

    Works when abcd is an installed package (``cli`` importable); otherwise falls back to
    ``PYTHONPATH=<abcd root>`` (a source checkout).
    """
    py = shlex.quote(sys.executable)
    root = shlex.quote(str(_REPO_ROOT))
    wsq = shlex.quote(str(ws))
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"if {py} -c 'import cli.builder_common.precommit' >/dev/null 2>&1; then\n"
        f"  exec {py} -m cli.builder_common.precommit {wsq}\n"
        "fi\n"
        f'PYTHONPATH={root}"${{PYTHONPATH:+:$PYTHONPATH}}" '
        f"exec {py} -m cli.builder_common.precommit {wsq}\n"
    )


def install_hook(ws: Path, *, force: bool = False) -> Path:
    """Write ``.git/hooks/pre-commit``. Refuses to replace a foreign hook unless forced."""
    hooks = Path(_git(ws, "rev-parse", "--git-path", "hooks").decode().strip())
    if not hooks.is_absolute():
        hooks = ws / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    if hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
        if not force:
            raise RefusedError(f"{hook} exists and is not an abcd hook; pass force to replace it")
    hook.write_text(hook_script(ws), encoding="utf-8")
    hook.chmod(0o755)
    return hook


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ws = Path(args[0]) if args else Path.cwd()
    try:
        problems = check_staged(ws)
    except UsageError as e:
        print(f"abcd pre-commit: {e}", file=sys.stderr)
        return 1
    if not problems:
        return 0
    print("abcd pre-commit: commit refused.", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print("Unstage the files above or fix them, then commit again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
