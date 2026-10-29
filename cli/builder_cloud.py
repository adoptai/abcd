"""Cloud runner commands (PLAN.md section 6).

* ``abcd cloud bootstrap``: clone the workspace repo, write ``.env`` from the injected secrets and
  check the PAT (``builder_common.cloud.bootstrap``). Names every missing variable, exit 2.
* ``abcd cloud bundle``: build ``abcd-bundle.zip`` with ``harness-skills/abcd/build_bundle.py``.
* ``abcd cloud nightly``: ``ws pull --check``, ``audit drift``, ``status publish``, ``report daily``
  in order, through the command registry, as one combined result.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common import cloud
from cli.builder_common.errors import UsageError
from cli.core.output import EXIT_OK, Result

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_SCRIPT = REPO_ROOT / "harness-skills" / "abcd" / "build_bundle.py"
WORKSPACE_FILE_ENV = "ABCD_WORKSPACE_FILE"

# Hooks for tests: a fake PAT exchange and a git runner. None means the real ones.
EXCHANGE_PAT: cloud.ExchangePat | None = None
RUN_GIT: cloud.RunGit | None = None

NIGHTLY_STEPS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ws pull --check", ("ws", "pull", "--check"), False),
    ("audit drift", ("audit", "drift"), False),
    ("status publish", ("status", "publish"), True),
    ("report daily", ("report", "daily"), True),
)
_PRIORITY = {3: 3, 2: 2, 1: 1}


def find_workspace_file(
    explicit: str | None, search_roots: tuple[Path, ...] = (Path("/workspace"),)
) -> Path:
    """``--workspace-file``, ``$ABCD_WORKSPACE_FILE``, ``./abcd-workspace.json`` or the file the harness copied."""
    for given in (explicit, os.environ.get(WORKSPACE_FILE_ENV)):
        if given:
            return Path(given)
    here = Path.cwd() / cloud.WORKSPACE_FILE_NAME
    if here.is_file():
        return here
    for root in search_roots:
        for hit in sorted(root.rglob(cloud.WORKSPACE_FILE_NAME)) if root.is_dir() else []:
            if "abcd" not in hit.relative_to(root).parts[:1]:
                return hit
    raise UsageError(f"no {cloud.WORKSPACE_FILE_NAME} found; pass --workspace-file <path>")


def abcd_root_for(ctx: Context, given: str | None) -> Path:
    if given:
        return Path(given)
    wd = ctx.workspaces_dir
    return wd.parent if wd.name == "workspaces" else REPO_ROOT


def _cmd_bootstrap(ctx: Context) -> Result:
    a = ctx.args
    wfile = find_workspace_file(a.workspace_file)
    root = abcd_root_for(ctx, a.abcd_root)
    if ctx.dry_run_notice(f"would clone the workspace named in {wfile} into {root / 'workspaces'}"):
        return Result.done({"dry_run": True, "workspace_file": str(wfile)})
    try:
        res = cloud.bootstrap(
            wfile, root, exchange_pat=EXCHANGE_PAT, run_git=RUN_GIT, replace=a.replace
        )
    except cloud.MissingVariablesError as exc:
        return Result(exc.code, {"missing": exc.missing}, None, str(exc))
    text = (
        f"Workspace {res.env} ready at {res.workspace_dir} (ref {res.ref}); "
        f"PAT verified; git via {res.git_auth.replace('_', ' ')}."
    )
    text += "".join(f"\nFLAG: {f}" for f in res.flags)
    return Result.done(res.to_dict(), text)


def _load_bundle_builder() -> Any:
    if not BUNDLE_SCRIPT.is_file():
        raise UsageError(f"{BUNDLE_SCRIPT} not found: `abcd cloud bundle` needs a source checkout")
    spec = importlib.util.spec_from_file_location("abcd_build_bundle", BUNDLE_SCRIPT)
    if spec is None or spec.loader is None:
        raise UsageError(f"cannot load {BUNDLE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cmd_bundle(ctx: Context) -> Result:
    builder = _load_bundle_builder()
    out = Path(ctx.args.out) if ctx.args.out else BUNDLE_SCRIPT.parent / "abcd-bundle.zip"
    members = builder.bundle_members(REPO_ROOT)
    if ctx.dry_run_notice(f"would build {out} from {len(members)} tracked file(s)"):
        return Result.done({"dry_run": True, "out": str(out), "files": len(members)})
    out.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        built = builder.build(REPO_ROOT, out)
    return Result.done(
        {"path": str(built), "bytes": built.stat().st_size, "files": len(members)},
        f"Built {built} ({built.stat().st_size} bytes, {len(members)} files)",
    )


# -- nightly ----------------------------------------------------------------------------------


def run_command(argv: list[str]) -> tuple[int, Any, str]:
    """Run one abcd command in-process (``cli.abcd.main``); returns exit code, ``data`` and message."""
    import cli.abcd as abcd_main

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = int(abcd_main.main(argv) or 0)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    data: Any = None
    message = ""
    for line in reversed(buf.getvalue().splitlines()):
        if line.startswith("{"):
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            data = doc.get("data")
            message = str(doc.get("error") or doc.get("message") or "")
            break
    return code, data, message


def nightly_steps(
    ctx: Context, runner: Callable[[list[str]], tuple[int, Any, str]] | None = None
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for label, words, takes_dry_run in NIGHTLY_STEPS:
        if ctx.registry.resolve(list(words)) is None:
            results.append(
                {
                    "step": label,
                    "state": "skipped",
                    "code": EXIT_OK,
                    "message": f"`abcd {label}` is not available in this install (optional module missing)",
                }
            )
            continue
        argv = [*words, "--env", ctx.env, "--json", "--yes"]
        if takes_dry_run and ctx.dry_run:
            argv.append("--dry-run")
        code, data, message = (runner or run_command)(argv)
        results.append(
            {
                "step": label,
                "state": "ok" if code == EXIT_OK else "failed",
                "code": code,
                "message": message,
                "data": data,
            }
        )
    return results


def combine_code(results: list[dict[str, Any]]) -> int:
    codes = [int(r["code"]) for r in results if r["code"] != EXIT_OK]
    if not codes:
        return EXIT_OK
    return max(codes, key=lambda c: _PRIORITY.get(c, 1))


def _cmd_nightly(ctx: Context) -> Result:
    results = nightly_steps(ctx)
    code = combine_code(results)
    lines = [
        f"  {r['step']}: {r['state']}" + (f" ({r['message']})" if r["message"] else "")
        for r in results
    ]
    failed = [r["step"] for r in results if r["state"] == "failed"]
    data = {"steps": results, "failed": failed}
    text = "Nightly run:\n" + "\n".join(lines)
    if code == EXIT_OK:
        return Result.done(data, text)
    return Result(code, data, text, "nightly step(s) failed: " + ", ".join(failed))


def register(registry: Registry) -> None:
    g = registry.group("cloud", "Cloud runner: bootstrap, bundle and the nightly run")
    p = g.command(
        "bootstrap",
        _cmd_bootstrap,
        help="Clone the workspace, write .env, check the PAT",
        writes=True,
    )
    p.add_argument("--workspace-file", help="Path to abcd-workspace.json")
    p.add_argument(
        "--abcd-root",
        help="Where the workspace is cloned under workspaces/ (default: this install)",
    )
    p.add_argument(
        "--replace", action="store_true", help="Re-clone over an existing workspace folder"
    )
    p = g.command(
        "bundle", _cmd_bundle, help="Build abcd-bundle.zip for the harness skill", writes=True
    )
    p.add_argument(
        "--out", help="Where to write the zip (default: harness-skills/abcd/abcd-bundle.zip)"
    )
    g.command(
        "nightly",
        _cmd_nightly,
        help="ws pull --check, audit drift, status publish, report daily",
        writes=True,
    )
