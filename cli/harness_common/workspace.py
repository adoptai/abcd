#!/usr/bin/env python3
"""
Harness workspace layout -- mirrors cli/wdl_common/workspace_manager.py's
env-first model, but for agent-harness skills/workstreams/traces instead of
WDL actions.

    workspaces/{env}/harness/
        .token_cache.json              # cached bearer JWT (see harness_common/auth.py)
        skills/<skill-name>/           # local skill source (SKILL.md + aux) -- optional,
                                        # you can also point harness_skill.py push at any
                                        # local directory outside the workspace
        workstreams.json               # cache: name -> {workstream_id, store_id}
        traces/<run_id>/               # one dir per harness_run.py conversation
            run.json                   # run metadata + turn history
            turn-<seq>.ndjson          # raw NDJSON stream for that turn
            turn-<seq>-trace.json
            turn-<seq>-temporal-history.json
"""

import json
from pathlib import Path
from typing import Any

from cli.wdl_common.workspace_manager import DEFAULT_ENV, WORKSPACES_DIR, get_workspace_manager


def active_env_name(env_name: str | None = None) -> str:
    manager = get_workspace_manager()
    return env_name or manager.active_env or DEFAULT_ENV


def env_path(env_name: str | None = None) -> Path:
    return WORKSPACES_DIR / active_env_name(env_name)


def harness_root(env_name: str | None = None) -> Path:
    root = env_path(env_name) / "harness"
    root.mkdir(parents=True, exist_ok=True)
    return root


def skills_dir(env_name: str | None = None) -> Path:
    d = harness_root(env_name) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def skill_dir(skill_name: str, env_name: str | None = None) -> Path:
    return skills_dir(env_name) / skill_name


def traces_root(env_name: str | None = None) -> Path:
    d = harness_root(env_name) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(run_id: str, env_name: str | None = None) -> Path:
    d = traces_root(env_name) / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_metadata_path(run_id: str, env_name: str | None = None) -> Path:
    return run_dir(run_id, env_name) / "run.json"


def load_run(run_id: str, env_name: str | None = None) -> dict[str, Any] | None:
    p = run_metadata_path(run_id, env_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_run(run_id: str, data: dict[str, Any], env_name: str | None = None) -> None:
    run_metadata_path(run_id, env_name).write_text(json.dumps(data, indent=2))


def workstreams_cache_path(env_name: str | None = None) -> Path:
    return harness_root(env_name) / "workstreams.json"


def load_workstreams_cache(env_name: str | None = None) -> dict[str, Any]:
    p = workstreams_cache_path(env_name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_workstreams_cache(cache: dict[str, Any], env_name: str | None = None) -> None:
    workstreams_cache_path(env_name).write_text(json.dumps(cache, indent=2))
