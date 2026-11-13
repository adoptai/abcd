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
from collections.abc import Callable, Mapping
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

PLATFORM_ONLY_ENV = "cloud"

# Hooks for tests: a fake PAT exchange and a git runner. None means the real ones.
EXCHANGE_PAT: cloud.ExchangePat | None = None
EXCHANGE_CLAIMS: cloud.ExchangeClaims | None = None
RUN_GIT: cloud.RunGit | None = None

NIGHTLY_STEPS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ws pull --check", ("ws", "pull", "--check"), False),
    ("audit drift", ("audit", "drift"), False),
    ("status publish", ("status", "publish"), True),
    ("report daily", ("report", "daily"), True),
)
_PRIORITY = {3: 3, 2: 2, 1: 1}


NO_POINTER_MESSAGE = (
    f"no {cloud.WORKSPACE_FILE_NAME}: run in the abcd-<env> workstream for repo work, or "
    "`abcd cloud bootstrap --platform-only` for skill/agent pushes"
)


def locate_workspace_file(
    explicit: str | None, search_roots: tuple[Path, ...] = (Path("/workspace"),)
) -> Path | None:
    """``--workspace-file``, ``$ABCD_WORKSPACE_FILE``, ``./abcd-workspace.json`` or the file the
    harness copied; None when there is none."""
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
    return None


def find_workspace_file(
    explicit: str | None, search_roots: tuple[Path, ...] = (Path("/workspace"),)
) -> Path:
    found = locate_workspace_file(explicit, search_roots)
    if found is None:
        raise UsageError(NO_POINTER_MESSAGE)
    return found


def abcd_root_for(ctx: Context, given: str | None) -> Path:
    if given:
        return Path(given)
    wd = ctx.workspaces_dir
    return wd.parent if wd.name == "workspaces" else REPO_ROOT


def _cmd_bootstrap_platform_only(ctx: Context) -> Result:
    env = ctx.env_arg or PLATFORM_ONLY_ENV
    root = abcd_root_for(ctx, ctx.args.abcd_root)
    if ctx.dry_run_notice(f"would write a platform-only workspace {root / 'workspaces' / env}"):
        return Result.done({"dry_run": True, "platform_only": True, "env": env})
    try:
        res = cloud.bootstrap_platform_only(root, env, exchange_pat=EXCHANGE_PAT)
    except cloud.MissingVariablesError as exc:
        return Result(exc.code, {"missing": exc.missing}, None, str(exc))
    text = (
        f"Platform-only workspace {res.env} ready at {res.workspace_dir} (no repo cloned); "
        f"PAT verified. Pass --env {res.env} to the skill / agent / session commands."
    )
    return Result.done(res.to_dict(), text)


def _cmd_bootstrap(ctx: Context) -> Result:
    a = ctx.args
    if a.platform_only:
        return _cmd_bootstrap_platform_only(ctx)
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


def _present(environ: Mapping[str, str], *names: str) -> bool:
    return any((environ.get(n) or "").strip() for n in names)


def doctor_report(
    ctx: Context,
    *,
    environ: Mapping[str, str] | None = None,
    exchange: cloud.ExchangeClaims | None = None,
) -> dict[str, Any]:
    """What a cloud run has, by variable NAME only (never a value)."""
    env = os.environ if environ is None else environ
    groups: tuple[tuple[str, tuple[str, ...], bool], ...] = (
        (cloud.VAR_PAT_CLIENT_ID, (cloud.VAR_PAT_CLIENT_ID,), True),
        (cloud.VAR_PAT_SECRET, (cloud.VAR_PAT_SECRET,), True),
        (
            f"{cloud.VAR_WEBUI_ENDPOINT} or {cloud.VAR_APP_BASE_URL}",
            (cloud.VAR_WEBUI_ENDPOINT, cloud.VAR_APP_BASE_URL),
            True,
        ),
        (cloud.VAR_CONVERSATION_ID, (cloud.VAR_CONVERSATION_ID,), False),
        (cloud.VAR_GIT_CREDENTIAL, (cloud.VAR_GITHUB_TOKEN, cloud.VAR_GITHUB_DEPLOY_KEY), False),
    )
    variables = [
        {
            "name": label,
            "present": _present(env, *names),
            "needed_for": "platform" if req else "repo",
        }
        for label, names, req in groups
    ]
    pointer = locate_workspace_file(None)
    wd = ctx.workspaces_dir
    bootstrapped: list[str] = []
    try:
        for d in sorted(wd.iterdir()) if wd.is_dir() else []:
            if (d / "env.json").is_file() and (d / ".env").is_file():
                bootstrapped.append(d.name)
    except OSError:
        pass
    platform_ready = all(v["present"] for v in variables if v["needed_for"] == "platform")
    pat: dict[str, Any] = {"checked": False}
    if platform_ready:
        endpoint = env.get(cloud.VAR_WEBUI_ENDPOINT) or env.get(cloud.VAR_APP_BASE_URL) or ""
        try:
            claims = (exchange or EXCHANGE_CLAIMS or cloud.exchange_pat_claims_http)(
                endpoint.strip().rstrip("/"),
                (env.get(cloud.VAR_PAT_CLIENT_ID) or "").strip(),
                (env.get(cloud.VAR_PAT_SECRET) or "").strip(),
            )
            pat = {"checked": True, "ok": True, **claims}
        except Exception as e:  # report, never raise: doctor is a diagnosis
            pat = {"checked": True, "ok": False, "error": str(e)[:200]}
    return {
        "variables": variables,
        "pointer_file": str(pointer) if pointer else None,
        "bootstrapped_workspaces": bootstrapped,
        "bootstrapped": bool(bootstrapped),
        "platform_ready": platform_ready,
        "repo_ready": bool(pointer) and all(v["present"] for v in variables),
        "pat": pat,
    }


def _cmd_doctor(ctx: Context) -> Result:
    rep = doctor_report(ctx)
    lines = [
        f"  {'present' if v['present'] else 'MISSING'}: {v['name']} (for {v['needed_for']})"
        for v in rep["variables"]
    ]
    lines.append(f"  pointer file: {rep['pointer_file'] or 'not found'}")
    lines.append(
        "  bootstrapped workspace(s): " + (", ".join(rep["bootstrapped_workspaces"]) or "none")
    )
    pat = rep["pat"]
    if pat.get("ok"):
        lines.append(f"  PAT exchange: ok, tenant {pat['tenant_id'] or '?'}, roles {pat['roles']}")
    elif pat.get("checked"):
        lines.append(f"  PAT exchange: FAILED ({pat['error']})")
    else:
        lines.append("  PAT exchange: not checked (platform variables missing)")
    if not rep["pointer_file"]:
        lines.append("  next: " + NO_POINTER_MESSAGE)
    text = "Cloud doctor:\n" + "\n".join(lines)
    ok = bool(pat.get("ok"))
    return Result(0 if ok else 1, rep, text, None if ok else "platform is not ready (see above)")


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
    p.add_argument(
        "--platform-only",
        action="store_true",
        help="No repo: write workspaces/<env>/ (default env 'cloud') from the injected PAT "
        "variables, for skill/agent/session commands",
    )
    g.command(
        "doctor",
        _cmd_doctor,
        help="Read-only: which variables, pointer file and workspace exist; PAT tenant and roles",
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
