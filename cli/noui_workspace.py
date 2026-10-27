#!/usr/bin/env python3
"""
Run the NoUI toolkit (cli/noui) inside an abcd workspace.

NoUI's scripts (capture_record, capture_import, compile_workflow, verify_replay,
...) read their configuration from environment variables and write everything
they produce under one "workbench" directory. This bridge makes both
workspace-scoped:

    workspaces/{env}/harness/          <- NOUI_WORKBENCH_DIR
        bundles/<name>-<session>.json   raw capture bundles (the source of truth)
        sessions/<session_id>.json      provision ledger (how a session was opened)
        skills/<skill_id>/              compiled skills -- the same directory
                                        `harness_skill.py push` reads, so a compiled
                                        skill can be pushed to the harness as-is
        mcp_servers/<app>/<server>/     compiled MCP servers
        login_recordings/               compiled login drafts
        traces/ workstreams.json        (harness debug loop, unchanged)

Environment mapping (the workspace .env speaks abcd's dialect; NoUI speaks its
own -- this is the only place they meet):

    NoUI expects            <- taken from the workspace
    ADOPT_API_URL           <- ADOPT_API_URL, else ADOPT_WEBUI_ENDPOINT
    ADOPT_CLIENT_ID/SECRET  <- NOUI_ADOPT_CLIENT_ID/SECRET, else
                               ADOPT_HARNESS_PAT_CLIENT_ID/SECRET (a platform PAT).
                               The WDL ADOPT_CLIENT_ID/SECRET are a different kind
                               of credential and are NEVER passed through: NoUI
                               would auto-select platform_jwt with them.
                               NOUI_IGNORE_DOTENV=1 also stops NoUI loading a
                               .env from the bundle or the current directory
                               (the repo-root .env holds exactly those creds).
    TABBY_*                 <- passed through unchanged
    NOUI_TABBY_AUTH_MODE    <- always set explicitly: the workspace value if given,
                               else agent_token when TABBY_CLIENT_ID/SECRET are set,
                               else platform_jwt when a platform PAT is set, else
                               agent_token (local Tabby; see cli/tabby_bootstrap.py)

Usage:
    python cli/noui_workspace.py [--env ENV] env              # resolved config (secrets masked)
    python cli/noui_workspace.py [--env ENV] paths            # workbench layout
    python cli/noui_workspace.py [--env ENV] list             # bundles, sessions, compiled skills
    python cli/noui_workspace.py [--env ENV] doctor           # config + Tabby reachability checks
    python cli/noui_workspace.py [--env ENV] scripts          # list NoUI scripts
    python cli/noui_workspace.py [--env ENV] run <script> [args...]
    python cli/noui_workspace.py [--env ENV] <script> [args...]   # shorthand for run

    Arguments of the form ws:<relative path> are expanded to a path under the
    workspace harness root, e.g.  verify_replay ws:skills/acme-1a2b3c4d
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOUI_ROOT = PROJECT_ROOT / "cli" / "noui"
NOUI_SCRIPTS = NOUI_ROOT / "scripts"

VALID_AUTH_MODES = ("agent_token", "platform_jwt", "broker")
WORKBENCH_SUBDIRS = ("bundles", "sessions", "skills", "mcp_servers", "login_recordings")

# Keys whose values are never printed.
_SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "PAT_", "API_KEY")
# WDL credentials: same names NoUI uses for its platform PAT, different meaning.
_WDL_CREDENTIAL_KEYS = ("ADOPT_CLIENT_ID", "ADOPT_CLIENT_SECRET")


class NouiWorkspaceError(Exception):
    """A workspace cannot run NoUI as configured."""


@dataclass
class NouiEnv:
    """The environment a NoUI script runs with, for one workspace."""

    env_name: str
    harness_root: Path
    child_env: dict[str, str]
    auth_mode: str
    auth_mode_source: str
    notes: list[str] = field(default_factory=list)

    @property
    def workbench_dir(self) -> Path:
        return Path(self.child_env["NOUI_WORKBENCH_DIR"])


def _workspace_values(env_dir: Path) -> dict[str, str]:
    dotenv = env_dir / ".env"
    if not dotenv.exists():
        return {}
    return {k: v for k, v in dotenv_values(dotenv).items() if v is not None and v != ""}


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return not v or v.startswith("your-") or v.endswith("-here") or v in ("changeme", "xxx")


def resolve_noui_env(
    env_name: str,
    *,
    workspaces_dir: Path,
    base_env: Mapping[str, str] | None = None,
) -> NouiEnv:
    """Build the child-process environment for running NoUI in ``env_name``.

    ``base_env`` is the inherited process environment (defaults to os.environ);
    values in the workspace ``.env`` override it, matching how the rest of abcd
    treats the workspace as the source of configuration.
    """
    env_dir = workspaces_dir / env_name
    if not (env_dir / "env.json").exists():
        raise NouiWorkspaceError(
            f"Workspace '{env_name}' not found at {env_dir} (no env.json). "
            "Create it with the workspace tooling first."
        )

    inherited = dict(os.environ if base_env is None else base_env)
    ws = {k: v for k, v in _workspace_values(env_dir).items() if not _is_placeholder(v)}
    merged = {**inherited, **ws}
    notes: list[str] = []

    child = dict(merged)
    # Never leak the WDL client credentials into NoUI under its PAT names.
    for key in _WDL_CREDENTIAL_KEYS:
        child.pop(key, None)

    # Platform base URL.
    api_url = merged.get("ADOPT_API_URL") or merged.get("ADOPT_WEBUI_ENDPOINT")
    if api_url:
        child["ADOPT_API_URL"] = api_url

    # Platform PAT for platform_jwt (NoUI's ADOPT_CLIENT_ID/SECRET).
    pat_id = merged.get("NOUI_ADOPT_CLIENT_ID") or merged.get("ADOPT_HARNESS_PAT_CLIENT_ID")
    pat_secret = merged.get("NOUI_ADOPT_CLIENT_SECRET") or merged.get("ADOPT_HARNESS_PAT_SECRET")
    has_pat = bool(pat_id and pat_secret)
    if has_pat:
        child["ADOPT_CLIENT_ID"] = str(pat_id)
        child["ADOPT_CLIENT_SECRET"] = str(pat_secret)

    has_agent_creds = bool(merged.get("TABBY_CLIENT_ID") and merged.get("TABBY_CLIENT_SECRET"))

    # Auth mode: explicit beats inferred, and the result is always pinned.
    declared = (
        ws.get("NOUI_TABBY_AUTH_MODE") or inherited.get("NOUI_TABBY_AUTH_MODE") or ""
    ).strip()
    if declared:
        if declared not in VALID_AUTH_MODES:
            raise NouiWorkspaceError(
                f"NOUI_TABBY_AUTH_MODE={declared!r} is not one of {', '.join(VALID_AUTH_MODES)}."
            )
        auth_mode = declared
        source = "workspace .env" if "NOUI_TABBY_AUTH_MODE" in ws else "process environment"
    elif has_agent_creds:
        auth_mode, source = "agent_token", "inferred: TABBY_CLIENT_ID/SECRET are set"
    elif has_pat and merged.get("TABBY_API_URL"):
        auth_mode, source = "platform_jwt", "inferred: platform PAT + TABBY_API_URL are set"
    else:
        auth_mode, source = "agent_token", "default (local Tabby)"
    child["NOUI_TABBY_AUTH_MODE"] = auth_mode

    if auth_mode == "platform_jwt" and not (has_pat and api_url):
        notes.append(
            "platform_jwt needs a platform PAT and base URL: set ADOPT_HARNESS_PAT_CLIENT_ID/"
            "ADOPT_HARNESS_PAT_SECRET and ADOPT_WEBUI_ENDPOINT in the workspace .env."
        )
    if auth_mode == "agent_token" and not has_agent_creds:
        notes.append(
            "agent_token needs TABBY_CLIENT_ID/TABBY_CLIENT_SECRET from your Tabby setup "
            "(run `python cli/tabby_bootstrap.py status` for a local Tabby)."
        )
    if auth_mode == "broker":
        notes.append(
            "broker mode only works inside the Agent Harness sandbox (the broker token is "
            "minted per conversation); it cannot be configured on a workstation."
        )

    harness_root = env_dir / "harness"
    workbench = merged.get("NOUI_WORKBENCH_DIR") if "NOUI_WORKBENCH_DIR" in ws else None
    child["NOUI_WORKBENCH_DIR"] = str(Path(workbench) if workbench else harness_root)

    # The workspace is the whole configuration: stop NoUI from topping it up
    # from a stray .env (abcd's repo-root .env holds WDL creds under the very
    # names NoUI reads as a platform PAT).
    child["NOUI_IGNORE_DOTENV"] = "1"

    # Make `import noui_core` work no matter how a script is launched.
    pythonpath = [str(NOUI_ROOT)]
    if child.get("PYTHONPATH"):
        pythonpath.append(child["PYTHONPATH"])
    child["PYTHONPATH"] = os.pathsep.join(pythonpath)

    return NouiEnv(
        env_name=env_name,
        harness_root=harness_root,
        child_env=child,
        auth_mode=auth_mode,
        auth_mode_source=source,
        notes=notes,
    )


def ensure_workbench(noui_env: NouiEnv) -> None:
    for sub in WORKBENCH_SUBDIRS:
        (noui_env.workbench_dir / sub).mkdir(parents=True, exist_ok=True)


def list_scripts() -> list[str]:
    return sorted(p.stem for p in NOUI_SCRIPTS.glob("*.py") if not p.name.startswith("_"))


def script_path(name: str) -> Path:
    stem = name[:-3] if name.endswith(".py") else name
    path = NOUI_SCRIPTS / f"{stem}.py"
    if stem.startswith("_") or not path.exists():
        raise NouiWorkspaceError(
            f"Unknown NoUI script '{name}'. Available: {', '.join(list_scripts())}"
        )
    return path


def expand_ws_args(args: list[str], noui_env: NouiEnv) -> list[str]:
    out = []
    for a in args:
        if a.startswith("ws:"):
            out.append(str(noui_env.workbench_dir / a[3:]))
        else:
            out.append(a)
    return out


def masked(key: str, value: str) -> str:
    if any(m in key.upper() for m in _SECRET_MARKERS) or key in _WDL_CREDENTIAL_KEYS:
        return "<set>" if value else "<unset>"
    return value


def describe(noui_env: NouiEnv) -> dict[str, object]:
    keys = (
        "NOUI_TABBY_AUTH_MODE",
        "NOUI_WORKBENCH_DIR",
        "TABBY_API_URL",
        "TABBY_CLIENT_ID",
        "TABBY_CLIENT_SECRET",
        "TABBY_ADMIN_TOKEN",
        "ADOPT_API_URL",
        "ADOPT_CLIENT_ID",
        "ADOPT_CLIENT_SECRET",
    )
    return {
        "env": noui_env.env_name,
        "auth_mode": noui_env.auth_mode,
        "auth_mode_source": noui_env.auth_mode_source,
        "variables": {k: masked(k, noui_env.child_env.get(k, "")) for k in keys},
        "notes": noui_env.notes,
    }


def _skill_summary(skill_dir: Path) -> dict[str, object]:
    def _load(name: str) -> dict[str, object]:
        p = skill_dir / name
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    manifest = _load("manifest.json")
    ops = _load("operations.json").get("operations")
    return {
        "name": skill_dir.name,
        "has_skill_md": (skill_dir / "SKILL.md").exists(),
        "operations": len(ops) if isinstance(ops, list) else None,
        "kind": manifest.get("kind") or manifest.get("skill_kind"),
        "provenance": bool(manifest.get("provenance")),
        "replay_report": (skill_dir / "replay_report.json").exists(),
        "replay_approved": (skill_dir / "replay_approval.json").exists(),
    }


def inventory(noui_env: NouiEnv) -> dict[str, object]:
    root = noui_env.workbench_dir

    def names(sub: str, pattern: str) -> list[str]:
        d = root / sub
        return sorted(p.name for p in d.glob(pattern)) if d.exists() else []

    skills_root = root / "skills"
    skills = (
        [_skill_summary(d) for d in sorted(skills_root.iterdir()) if d.is_dir()]
        if skills_root.exists()
        else []
    )
    return {
        "workbench": str(root),
        "bundles": names("bundles", "*.json"),
        "sessions": names("sessions", "*.json"),
        "skills": skills,
        "mcp_servers": names("mcp_servers", "*/*"),
    }


def run_script(noui_env: NouiEnv, name: str, args: list[str]) -> int:
    path = script_path(name)
    ensure_workbench(noui_env)
    cmd = [sys.executable, str(path), *expand_ws_args(args, noui_env)]
    return subprocess.call(cmd, env=noui_env.child_env)


def doctor(noui_env: NouiEnv) -> int:
    print(json.dumps(describe(noui_env), indent=2))
    tabby = noui_env.child_env.get("TABBY_API_URL") or "http://localhost:8000"
    probe = (
        "from noui_core import tabby_client; import sys; "
        "sys.exit(0 if tabby_client.is_alive() else 1)"
    )
    alive = (
        subprocess.call(
            [sys.executable, "-c", probe],
            env=noui_env.child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        == 0
    )
    print(f"Tabby at {tabby}: {'reachable' if alive else 'NOT reachable'}")
    if not alive and noui_env.auth_mode == "agent_token":
        print("  -> start a local Tabby with: python cli/tabby_bootstrap.py up")
    return 0 if alive and not noui_env.notes else 1


def _default_env_name(explicit: str | None) -> str:
    if explicit:
        return explicit
    from cli.wdl_common.workspace_manager import DEFAULT_ENV, get_workspace_manager

    return get_workspace_manager().active_env or DEFAULT_ENV


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Run the NoUI toolkit inside an abcd workspace.",
        usage="noui_workspace.py [--env ENV] {env,paths,list,doctor,scripts,run,<script>} ...",
    )
    parser.add_argument("--env", help="Workspace (defaults to the active env)")
    parser.add_argument("command")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)

    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    try:
        noui_env = resolve_noui_env(_default_env_name(ns.env), workspaces_dir=WORKSPACES_DIR)
        if ns.command == "env":
            print(json.dumps(describe(noui_env), indent=2))
            return 0
        if ns.command == "paths":
            ensure_workbench(noui_env)
            for sub in WORKBENCH_SUBDIRS:
                print(f"{sub:17} {noui_env.workbench_dir / sub}")
            return 0
        if ns.command == "list":
            print(json.dumps(inventory(noui_env), indent=2))
            return 0
        if ns.command == "doctor":
            return doctor(noui_env)
        if ns.command == "scripts":
            print("\n".join(list_scripts()))
            return 0
        if ns.command == "run":
            if not ns.rest:
                parser.error("run needs a script name")
            return run_script(noui_env, ns.rest[0], ns.rest[1:])
        return run_script(noui_env, ns.command, ns.rest)
    except NouiWorkspaceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
