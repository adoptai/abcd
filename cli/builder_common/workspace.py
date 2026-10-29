"""Builder paths and files on top of the existing workspace layout (PLAN.md section 5).

Functions take an explicit workspace ``Path`` so tests can point at a temp dir;
``workspace_for_env`` resolves an env name (None = active env) to ``workspaces/<env>/``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cli.builder_common import template_loader as templates
from cli.builder_common.errors import RefusedError, UsageError
from cli.harness_common.workspace import env_path
from cli.wdl_common.workspace_manager import DEFAULT_ENV

BUILDER_JSON = "builder.json"
ADOPT_MD = "ADOPT.md"
BUNDLES_LOCK = "bundles.lock.json"

_STR_FIELDS = ("org_id", "tenant", "webui_endpoint", "repo_remote", "default_branch")
_MAP_FIELDS = ("workstreams", "stores", "tables")
#: Which kind of org this workspace's PAT belongs to: the client's org, or the Builder home org
#: (PLAN.md section 6). Optional: absent until ``builder setup`` or ``cloud setup --home`` sets it.
ROLES = ("home", "client")
_AGENT_KEYS = ("live_process_id", "staged_process_id")

# Directories init_layout creates (relative to the workspace). Empty tracked ones get a .gitkeep.
TRACKED_DIRS = (
    "harness/skills",
    "harness/plugins",
    "harness/agents",
    "harness/checks",
    "harness/datasets",
    "harness/evals/scorers",
    "harness/evals/reports",
)
LOCAL_DIRS = ("context", ".builder")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def workspace_for_env(env_name: str | None = None) -> Path:
    """``workspaces/<env>/`` for an env name (None = active env)."""
    return env_path(env_name)


def builder_json_path(ws: Path) -> Path:
    return ws / BUILDER_JSON


def harness_dir(ws: Path) -> Path:
    return ws / "harness"


def agents_dir(ws: Path) -> Path:
    """Harness agent bundles (NOT the WDL ``agents/`` folder)."""
    return harness_dir(ws) / "agents"


def agent_dir(ws: Path, agent: str) -> Path:
    return agents_dir(ws) / _safe_name(agent, "agent")


def plugins_dir(ws: Path) -> Path:
    return harness_dir(ws) / "plugins"


def checks_dir(ws: Path) -> Path:
    return harness_dir(ws) / "checks"


def datasets_dir(ws: Path) -> Path:
    return harness_dir(ws) / "datasets"


def dataset_dir(ws: Path, dataset: str) -> Path:
    return datasets_dir(ws) / _safe_name(dataset, "dataset")


def dataset_manifest_path(ws: Path, dataset: str) -> Path:
    return dataset_dir(ws, dataset) / "manifest.json"


def dataset_cases_dir(ws: Path, dataset: str) -> Path:
    return dataset_dir(ws, dataset) / "cases"


def evals_dir(ws: Path) -> Path:
    return harness_dir(ws) / "evals"


def scorers_dir(ws: Path) -> Path:
    return evals_dir(ws) / "scorers"


def reports_dir(ws: Path) -> Path:
    return evals_dir(ws) / "reports"


def bundles_lock_path(ws: Path) -> Path:
    return harness_dir(ws) / BUNDLES_LOCK


def context_dir(ws: Path) -> Path:
    return ws / "context"


def builder_state_dir(ws: Path) -> Path:
    return ws / ".builder"


def _safe_name(name: str, what: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise UsageError(f"Invalid {what} name: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def assert_not_default(env: str | Path) -> None:
    """Refuse the tracked ``default`` example workspace (it is never made a repo)."""
    name = env.name if isinstance(env, Path) else env
    if name == DEFAULT_ENV:
        raise RefusedError(
            f"Refusing to use the '{DEFAULT_ENV}' workspace: it is abcd's tracked example "
            "and is never a client repo. Create or clone a client workspace instead."
        )


def _read_env_json(ws: Path) -> dict[str, Any] | None:
    p = ws / "env.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def env_json_matches(ws: Path) -> bool:
    """True when ``env.json`` exists and its ``env_id`` equals the folder name."""
    data = _read_env_json(ws)
    return data is not None and data.get("env_id") == ws.name


def require_env_json_match(ws: Path) -> None:
    data = _read_env_json(ws)
    if data is None:
        raise UsageError(f"{ws}: env.json is missing or unreadable")
    if data.get("env_id") != ws.name:
        raise UsageError(
            f"{ws}: env.json env_id is {data.get('env_id')!r} but the folder is {ws.name!r}"
        )


# ---------------------------------------------------------------------------
# .env template
# ---------------------------------------------------------------------------


def env_template(name: str, target: str = "staging") -> str:
    """The ``.env`` text ``WorkspaceManager.create_env`` writes (kept equal by a test)."""
    return f"""# Environment: {name}
# Target: {target}

# Required: AdoptAI API Authentication (WDL actions -- cli/*.py)
# Configure these before using discovery or API features
ADOPT_CLIENT_ID=your-client-id-here
ADOPT_CLIENT_SECRET=your-client-secret-here

# API Endpoints (defaults to production, uncomment to override)
# ADOPT_API_ENDPOINT=https://connect.adopt.ai
# ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai

# Required: Agent-harness debug loop (cli/harness_*.py) -- separate credentials,
# see cli/harness_common/auth.py for how to mint these.
# ADOPT_WEBUI_ENDPOINT=https://your-adopt-webui-host
# ADOPT_HARNESS_PAT_CLIENT_ID=your-harness-pat-client-id
# ADOPT_HARNESS_PAT_SECRET=your-harness-pat-secret

# Optional: NoUI capture/compile (cli/noui_workspace.py). The harness PAT above doubles
# as NoUI's platform credential (platform_jwt, cloud Tabby); for a local Tabby run
# `python cli/tabby_bootstrap.py up --env <this env> --write-env`.
# TABBY_API_URL=https://your-tabby-host
# NOUI_TABBY_AUTH_MODE=platform_jwt
"""


def write_env_template(ws: Path, *, overwrite: bool = False) -> bool:
    """Write ``.env`` from the template. Never replaces an existing ``.env`` unless asked.

    Name and target come from ``env.json`` when present. Returns True if written.
    """
    target_file = ws / ".env"
    if target_file.exists() and not overwrite:
        return False
    meta = _read_env_json(ws) or {}
    name = str(meta.get("name") or ws.name)
    target = str(meta.get("target") or "staging")
    target_file.write_text(env_template(name, target), encoding="utf-8")
    target_file.chmod(0o600)  # it will hold credentials; the template itself was 0644
    return True


# ---------------------------------------------------------------------------
# builder.json
# ---------------------------------------------------------------------------


def default_builder_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "org_id": "",
        "tenant": "",
        "webui_endpoint": "",
        "repo_remote": "",
        "default_branch": "main",
        "workstreams": {},
        "stores": {},
        "tables": {},
        "agents": {},
    }
    cfg.update(overrides)
    return validate_builder_config(cfg)


def validate_builder_config(data: Any) -> dict[str, Any]:
    """Check shape and types; returns the config with every known key present."""
    if not isinstance(data, dict):
        raise UsageError("builder.json must be a JSON object")
    out: dict[str, Any] = dict(data)
    for key in _STR_FIELDS:
        value = out.get(key, "main" if key == "default_branch" else "")
        if not isinstance(value, str):
            raise UsageError(f"builder.json: '{key}' must be a string")
        out[key] = value
    if not out["default_branch"]:
        raise UsageError("builder.json: 'default_branch' must not be empty")
    if "role" in out and out["role"] not in ROLES:
        raise UsageError(f"builder.json: 'role' must be one of {', '.join(ROLES)}")
    for key in _MAP_FIELDS:
        value = out.get(key, {})
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise UsageError(f"builder.json: '{key}' must map names to id strings")
        out[key] = value
    agents = out.get("agents", {})
    if not isinstance(agents, dict):
        raise UsageError("builder.json: 'agents' must be an object")
    for agent, entry in agents.items():
        if not isinstance(entry, dict):
            raise UsageError(f"builder.json: agents.{agent} must be an object")
        for k, v in entry.items():
            if k not in _AGENT_KEYS:
                raise UsageError(f"builder.json: agents.{agent} has unknown key '{k}'")
            if v is not None and not isinstance(v, str):
                raise UsageError(f"builder.json: agents.{agent}.{k} must be a string or null")
        for k in _AGENT_KEYS:
            entry.setdefault(k, None)
    out["agents"] = agents
    return out


def load_builder_config(ws: Path) -> dict[str, Any]:
    p = builder_json_path(ws)
    if not p.is_file():
        raise UsageError(f"{p} not found; run `abcd ws init` first")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"{p} is not valid JSON: {e}") from e
    return validate_builder_config(data)


def save_builder_config(ws: Path, cfg: dict[str, Any]) -> None:
    valid = validate_builder_config(cfg)
    builder_json_path(ws).write_text(json.dumps(valid, indent=2) + "\n", encoding="utf-8")


def update_builder_config(ws: Path, **fields: Any) -> dict[str, Any]:
    """Set scalar fields and merge the map fields (workstreams, stores, tables, agents)."""
    cfg = load_builder_config(ws)
    for key, value in fields.items():
        if key in _MAP_FIELDS or key == "agents":
            if not isinstance(value, dict):
                raise UsageError(f"builder.json: '{key}' must be an object")
            merged = dict(cfg[key])
            for k, v in value.items():
                if key == "agents" and isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            cfg[key] = merged
        elif key in _STR_FIELDS or key == "role":
            cfg[key] = value
        else:
            raise UsageError(f"builder.json: unknown field '{key}'")
    save_builder_config(ws, cfg)
    return load_builder_config(ws)


def set_agent_process_ids(
    ws: Path,
    agent: str,
    *,
    live_process_id: str | None = None,
    staged_process_id: str | None = None,
) -> dict[str, Any]:
    """Record an agent's live and/or staged process ids (None leaves a value unchanged)."""
    entry = {
        k: v
        for k, v in (
            ("live_process_id", live_process_id),
            ("staged_process_id", staged_process_id),
        )
        if v is not None
    }
    return update_builder_config(ws, agents={_safe_name(agent, "agent"): entry})


# ---------------------------------------------------------------------------
# bundles.lock.json
# ---------------------------------------------------------------------------


def load_bundles_lock(ws: Path) -> dict[str, Any]:
    """``{"version": 1, "entries": {path: {doc_store_path, sha256, size}}}``."""
    p = bundles_lock_path(ws)
    if not p.is_file():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"{p} is not valid JSON: {e}") from e
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise UsageError(f"{p}: 'entries' must be an object")
    for path, entry in entries.items():
        if not isinstance(entry, dict) or not {"doc_store_path", "sha256", "size"} <= set(entry):
            raise UsageError(f"{p}: entry '{path}' needs doc_store_path, sha256 and size")
    return {"version": data.get("version", 1), "entries": entries}


def save_bundles_lock(ws: Path, lock: dict[str, Any]) -> None:
    p = bundles_lock_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": lock.get("version", 1), "entries": dict(sorted(lock["entries"].items()))}
    p.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def record_bundle(ws: Path, rel_path: str, doc_store_path: str) -> dict[str, Any]:
    """Hash a bundle file (path relative to the workspace) and record it in the lock."""
    src = ws / rel_path
    if not src.is_file():
        raise UsageError(f"Bundle not found: {rel_path}")
    data = src.read_bytes()
    entry = {
        "doc_store_path": doc_store_path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    lock = load_bundles_lock(ws)
    lock["entries"][Path(rel_path).as_posix()] = entry
    save_bundles_lock(ws, lock)
    return entry


# ---------------------------------------------------------------------------
# ADOPT.md
# ---------------------------------------------------------------------------


def list_agents(ws: Path) -> list[str]:
    d = agents_dir(ws)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def render_agents_table(ws: Path, cfg: dict[str, Any]) -> str:
    on_disk = list_agents(ws)
    names = sorted(set(on_disk) | set(cfg.get("agents", {})))
    if not names:
        return "_No agents yet._"
    rows = ["| Agent | Live process | Staged process | Bundle |", "|---|---|---|---|"]
    for name in names:
        entry = cfg.get("agents", {}).get(name, {})
        bundle = f"`harness/agents/{name}/`" if name in on_disk else "-"
        live = entry.get("live_process_id") or "-"
        staged = entry.get("staged_process_id") or "-"
        rows.append(f"| {name} | {live} | {staged} | {bundle} |")
    return "\n".join(rows)


def render_adopt_md(ws: Path) -> str:
    cfg = load_builder_config(ws) if builder_json_path(ws).is_file() else default_builder_config()
    meta = _read_env_json(ws) or {}
    return templates.render_template(
        templates.ADOPT_MD,
        env=ws.name,
        client=str(meta.get("client") or meta.get("name") or ws.name),
        org_id=cfg["org_id"] or "(not set)",
        webui_endpoint=cfg["webui_endpoint"] or "(not set)",
        repo_remote=cfg["repo_remote"] or "(not set)",
        default_branch=cfg["default_branch"],
        agents_table=render_agents_table(ws, cfg),
    )


def refresh_adopt_md(ws: Path) -> Path:
    """Regenerate ``ADOPT.md`` (overwrites; ``abcd release`` calls this)."""
    p = ws / ADOPT_MD
    p.write_text(render_adopt_md(ws), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# init_layout
# ---------------------------------------------------------------------------


def _ensure_gitignore(ws: Path) -> bool:
    """Create the workspace .gitignore, or append missing patterns. True if changed."""
    p = ws / ".gitignore"
    if not p.exists():
        p.write_text(templates.load_template(templates.GITIGNORE), encoding="utf-8")
        return True
    text = p.read_text(encoding="utf-8")
    existing = {line.strip() for line in text.splitlines()}
    missing = [pat for pat in templates.gitignore_patterns() if pat not in existing]
    if not missing:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# Added by abcd ws init\n" + "\n".join(missing) + "\n"
    p.write_text(text, encoding="utf-8")
    return True


def init_layout(ws: Path, **config: Any) -> list[str]:
    """Create the Builder dirs and files idempotently.

    Existing files (WDL, NoUI, builder.json, ADOPT.md) are never overwritten; the
    ``.gitignore`` only gains missing patterns. ``config`` seeds a new builder.json.
    Returns the workspace-relative paths created or changed.
    """
    assert_not_default(ws)
    if not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    changed: list[str] = []

    for rel in (*TRACKED_DIRS, *LOCAL_DIRS):
        d = ws / rel
        if not d.is_dir():
            d.mkdir(parents=True)
            changed.append(rel + "/")
    for rel in TRACKED_DIRS:
        d = ws / rel
        if not any(d.iterdir()):
            (d / ".gitkeep").touch()

    if not builder_json_path(ws).exists():
        meta = _read_env_json(ws) or {}
        seed = {"tenant": str(meta.get("client") or ""), **config}
        save_builder_config(ws, default_builder_config(**seed))
        changed.append(BUILDER_JSON)
    if _ensure_gitignore(ws):
        changed.append(".gitignore")
    if not bundles_lock_path(ws).exists():
        save_bundles_lock(ws, {"version": 1, "entries": {}})
        changed.append(f"harness/{BUNDLES_LOCK}")
    if not (ws / ADOPT_MD).exists():
        (ws / ADOPT_MD).write_text(render_adopt_md(ws), encoding="utf-8")
        changed.append(ADOPT_MD)
    return changed


def new_agent_skeleton(
    ws: Path, agent: str, *, display_name: str = "", description: str = ""
) -> Path:
    """Write ``harness/agents/<agent>/process.yaml`` from the skeleton (never overwrites)."""
    d = agent_dir(ws, agent)
    p = d / "process.yaml"
    if p.exists():
        raise RefusedError(f"{p} already exists")
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(
        templates.render_template(
            templates.PROCESS_YAML,
            name=agent,
            display_name=json.dumps(display_name or agent),
            description=json.dumps(description or f"{agent} agent"),
        ),
        encoding="utf-8",
    )
    return p
