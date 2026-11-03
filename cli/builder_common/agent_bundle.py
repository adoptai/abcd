"""``harness/agents/<agent>/`` <-> the process API (PLAN.md sections 5 and 7).

Bundle files:

- ``process.yaml``: ``name``, ``display_name``, ``description``, ``components``
  (``[{type: skill|plugin, source: org|default, ref}]``), ``workstreams`` and
  ``write_workstreams`` (workstream names), ``is_enabled``, and ``write_folder_prefix``
  (required by ``PUT /v1/org/processes/{id}/write-workstreams`` whenever there are write
  grants: adoptwebui ``ProcessService._normalise_folder_prefix``);
- ``instructions.md``: ``agent_instructions``;
- ``flow.json``: ``compiled_flow`` (when the agent has gated steps).

Workstream names are resolved to ids through a mapping the caller passes in.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from cli.builder_common.errors import UsageError
from cli.builder_common.staging import (
    rewrite_flow,
    rewrite_text,
    staged_agent_name,
)
from cli.harness_skill import _SLUG_RE

PROCESS_FILE = "process.yaml"
INSTRUCTIONS_FILE = "instructions.md"
FLOW_FILE = "flow.json"
STAGED_DISPLAY_SUFFIX = " (staged)"
ALL_WORKSTREAMS = "*"
#: adoptwebui ``routes/process.py`` ``_COMPILED_FLOW_MAX_BYTES``.
COMPILED_FLOW_MAX_BYTES = 512_000

_KEYS = {
    "name", "display_name", "description", "components", "workstreams",
    "write_workstreams", "write_folder_prefix", "is_enabled",
}  # fmt: skip
_COMPONENT_TYPES = ("skill", "plugin")
_COMPONENT_SOURCES = ("org", "default")


class BundleError(UsageError):
    """A bundle that is malformed or can't be mapped to the platform."""


@dataclass(frozen=True)
class AgentBundle:
    name: str
    display_name: str
    description: str = ""
    components: tuple[tuple[str, str, str], ...] = ()  # (type, source, ref)
    workstreams: tuple[str, ...] = ()
    write_workstreams: tuple[str, ...] = ()
    write_folder_prefix: str = ""
    is_enabled: bool = False
    instructions: str | None = None
    flow: dict[str, Any] | None = field(default=None, hash=False)

    def component_dicts(self) -> list[dict[str, str]]:
        return [{"type": t, "source": s, "ref": r} for t, s, r in self.components]


@dataclass(frozen=True)
class FieldDiff:
    field: str
    local: Any
    platform: Any


def _norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.rstrip()
    return stripped or None


def validate(bundle: AgentBundle) -> None:
    if not _SLUG_RE.match(bundle.name):
        raise BundleError(f"agent name {bundle.name!r} must match ^[a-z0-9][a-z0-9-]*$")
    if not bundle.display_name.strip():
        raise BundleError("display_name is required")
    seen: set[tuple[str, str]] = set()
    for t, s, r in bundle.components:
        if t not in _COMPONENT_TYPES:
            raise BundleError(f"component type {t!r} must be one of {_COMPONENT_TYPES}")
        if s not in _COMPONENT_SOURCES:
            raise BundleError(f"component source {s!r} must be one of {_COMPONENT_SOURCES}")
        if not _SLUG_RE.match(r):
            raise BundleError(f"component ref {r!r} must match ^[a-z0-9][a-z0-9-]*$")
        if (t, r) in seen:
            raise BundleError(f"duplicate component {t}:{r}")
        seen.add((t, r))
    for label, names in (
        ("workstreams", bundle.workstreams),
        ("write_workstreams", bundle.write_workstreams),
    ):
        if len(set(names)) != len(names):
            raise BundleError(f"{label} has duplicates")
    if ALL_WORKSTREAMS in bundle.write_workstreams and len(bundle.write_workstreams) > 1:
        raise BundleError("'*' cannot be combined with specific write_workstreams")
    if bundle.write_workstreams and not bundle.write_folder_prefix.strip():
        raise BundleError("write_workstreams needs write_folder_prefix")
    if bundle.flow is not None:
        if not isinstance(bundle.flow, dict):
            raise BundleError("flow.json must hold a JSON object")
        if len(json.dumps(bundle.flow)) > COMPILED_FLOW_MAX_BYTES:
            raise BundleError(f"flow.json exceeds {COMPILED_FLOW_MAX_BYTES} bytes serialized")


def _str_list(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise BundleError(f"{label} must be a list of non-empty strings")
    return tuple(value)


def load_bundle(agent_dir: Path) -> AgentBundle:
    agent_dir = Path(agent_dir)
    path = agent_dir / PROCESS_FILE
    if not path.is_file():
        raise BundleError(f"{agent_dir}: no {PROCESS_FILE}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise BundleError(f"{path}: not valid YAML ({e})") from e
    if not isinstance(raw, dict):
        raise BundleError(f"{path}: must be a mapping")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise BundleError(f"{path}: unknown keys {unknown}")
    comps: list[tuple[str, str, str]] = []
    for c in raw.get("components") or []:
        if not isinstance(c, dict) or set(c) - {"type", "source", "ref"}:
            raise BundleError(f"{path}: component {c!r} must be {{type, source, ref}}")
        comps.append((str(c.get("type")), str(c.get("source", "org")), str(c.get("ref"))))
    if not isinstance(raw.get("is_enabled", False), bool):
        raise BundleError(f"{path}: is_enabled must be true or false")
    instructions = None
    ipath = agent_dir / INSTRUCTIONS_FILE
    if ipath.is_file():
        instructions = _norm_text(ipath.read_text())
    flow = None
    fpath = agent_dir / FLOW_FILE
    if fpath.is_file():
        try:
            flow = json.loads(fpath.read_text())
        except json.JSONDecodeError as e:
            raise BundleError(f"{fpath}: not valid JSON ({e})") from e
    bundle = AgentBundle(
        name=str(raw.get("name") or ""),
        display_name=str(raw.get("display_name") or ""),
        description=str(raw.get("description") or ""),
        components=tuple(comps),
        workstreams=_str_list(raw.get("workstreams"), "workstreams"),
        write_workstreams=_str_list(raw.get("write_workstreams"), "write_workstreams"),
        write_folder_prefix=str(raw.get("write_folder_prefix") or ""),
        is_enabled=bool(raw.get("is_enabled", False)),
        instructions=instructions,
        flow=flow,
    )
    validate(bundle)
    return bundle


def write_bundle(bundle: AgentBundle, agent_dir: Path) -> None:
    """Write ``process.yaml`` (plus ``instructions.md`` / ``flow.json`` when present)."""
    validate(bundle)
    agent_dir = Path(agent_dir)
    agent_dir.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {
        "name": bundle.name,
        "display_name": bundle.display_name,
        "description": bundle.description,
        "components": bundle.component_dicts(),
        "workstreams": list(bundle.workstreams),
        "write_workstreams": list(bundle.write_workstreams),
    }
    if bundle.write_folder_prefix:
        doc["write_folder_prefix"] = bundle.write_folder_prefix
    doc["is_enabled"] = bundle.is_enabled
    (agent_dir / PROCESS_FILE).write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=10**9)
    )
    ipath, fpath = agent_dir / INSTRUCTIONS_FILE, agent_dir / FLOW_FILE
    if bundle.instructions is not None:
        ipath.write_text(bundle.instructions.rstrip() + "\n")
    elif ipath.exists():
        ipath.unlink()
    if bundle.flow is not None:
        fpath.write_text(json.dumps(bundle.flow, indent=2, ensure_ascii=False) + "\n")
    elif fpath.exists():
        fpath.unlink()


# -- Payloads -------------------------------------------------------------


def to_create_payload(bundle: AgentBundle) -> dict[str, Any]:
    """Keyword arguments for ``HarnessAPIClient.create_process``."""
    validate(bundle)
    return {
        "name": bundle.name,
        "display_name": bundle.display_name,
        "description": bundle.description or None,
        "components": bundle.component_dicts(),
        "agent_instructions": bundle.instructions,
        "is_enabled": bundle.is_enabled,
        "compiled_flow": bundle.flow,
    }


def _comparable(process: Mapping[str, Any]) -> dict[str, Any]:
    comps = [
        (str(c.get("type")), str(c.get("source") or "org"), str(c.get("ref")))
        for c in process.get("components") or []
        if isinstance(c, dict)
    ]
    return {
        "display_name": process.get("display_name"),
        "description": _norm_text(process.get("description")),
        "components": comps,
        "agent_instructions": _norm_text(process.get("agent_instructions")),
        "is_enabled": bool(process.get("is_enabled")),
        "compiled_flow": process.get("compiled_flow") or None,
    }


def _local_comparable(bundle: AgentBundle) -> dict[str, Any]:
    return {
        "display_name": bundle.display_name,
        "description": _norm_text(bundle.description),
        "components": list(bundle.components),
        "agent_instructions": _norm_text(bundle.instructions),
        "is_enabled": bundle.is_enabled,
        "compiled_flow": bundle.flow or None,
    }


def to_patch_payload(
    bundle: AgentBundle, platform_process: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Fields for ``update_process``. With ``platform_process``, only what differs.

    ``name`` is immutable and never sent. A bundle without a flow or instructions sends
    ``None`` for them when the platform has one (to clear it).
    """
    validate(bundle)
    full: dict[str, Any] = {
        "display_name": bundle.display_name,
        "description": bundle.description or None,
        "components": bundle.component_dicts(),
        "agent_instructions": bundle.instructions,
        "is_enabled": bundle.is_enabled,
        "compiled_flow": bundle.flow,
    }
    if platform_process is None:
        return full
    local, remote = _local_comparable(bundle), _comparable(platform_process)
    return {k: v for k, v in full.items() if local[k] != remote[k]}


def _ids(
    names: Sequence[str], name_to_id: Mapping[str, str], label: str, *, allow_all: bool = False
) -> list[str]:
    out: list[str] = []
    missing: list[str] = []
    for n in names:
        if allow_all and n == ALL_WORKSTREAMS:
            out.append(n)
        elif n in name_to_id:
            out.append(name_to_id[n])
        else:
            missing.append(n)
    if missing:
        raise BundleError(f"{label}: unknown workstream name(s) {sorted(missing)}")
    return out


def workstream_ids(bundle: AgentBundle, name_to_id: Mapping[str, str]) -> list[str]:
    """Ids for ``set_process_workstreams``."""
    return _ids(bundle.workstreams, name_to_id, "workstreams")


def write_grants_payload(bundle: AgentBundle, name_to_id: Mapping[str, str]) -> dict[str, Any]:
    """Arguments for ``BuilderPlatformClient.set_write_workstreams``."""
    ids = _ids(bundle.write_workstreams, name_to_id, "write_workstreams", allow_all=True)
    return {"workstream_ids": ids, "folder_prefix": bundle.write_folder_prefix if ids else ""}


# -- From the platform ----------------------------------------------------


def from_platform(
    process: Mapping[str, Any],
    workstream_names_by_id: Mapping[str, str],
    *,
    assigned_workstream_ids: Sequence[str] = (),
    write_grants: Mapping[str, Any] | None = None,
) -> AgentBundle:
    """Bundle for a platform process (``ws pull``). ``assigned_workstream_ids`` is
    ``get_process_workstreams()['workstream_ids']``; ``write_grants`` is
    ``get_write_workstreams()``."""

    def names(ids: Sequence[str], allow_all: bool = False) -> tuple[str, ...]:
        out = []
        for i in ids:
            if allow_all and i == ALL_WORKSTREAMS:
                out.append(i)
            elif i in workstream_names_by_id:
                out.append(workstream_names_by_id[i])
            else:
                raise BundleError(f"workstream id {i!r} has no known name")
        return tuple(out)

    grants = write_grants or {}
    prefix = grants.get("folder_prefix")
    comps = tuple(
        (str(c.get("type")), str(c.get("source") or "org"), str(c.get("ref")))
        for c in process.get("components") or []
        if isinstance(c, dict)
    )
    bundle = AgentBundle(
        name=str(process.get("name") or ""),
        display_name=str(process.get("display_name") or ""),
        description=str(process.get("description") or ""),
        components=comps,
        workstreams=names(list(assigned_workstream_ids)),
        write_workstreams=names(list(grants.get("workstream_ids") or []), allow_all=True),
        write_folder_prefix=str(prefix or ""),
        is_enabled=bool(process.get("is_enabled")),
        instructions=_norm_text(process.get("agent_instructions")),
        flow=process.get("compiled_flow") or None,
    )
    return bundle


def diff_bundle_vs_platform(
    bundle: AgentBundle,
    process: Mapping[str, Any],
    name_to_id: Mapping[str, str],
    *,
    assigned_workstream_ids: Sequence[str] = (),
    write_grants: Mapping[str, Any] | None = None,
) -> list[FieldDiff]:
    """Every field where the bundle and the live process differ (empty = in sync)."""
    local, remote = _local_comparable(bundle), _comparable(process)
    diffs = [FieldDiff(k, local[k], remote[k]) for k in local if local[k] != remote[k]]
    want_ws = sorted(workstream_ids(bundle, name_to_id))
    have_ws = sorted(assigned_workstream_ids)
    if want_ws != have_ws:
        diffs.append(FieldDiff("workstreams", want_ws, have_ws))
    want = write_grants_payload(bundle, name_to_id)
    grants = write_grants or {}
    have_ids = sorted(grants.get("workstream_ids") or [])
    if sorted(want["workstream_ids"]) != have_ids:
        diffs.append(FieldDiff("write_workstreams", sorted(want["workstream_ids"]), have_ids))
    elif have_ids and want["folder_prefix"] != (grants.get("folder_prefix") or None):
        diffs.append(
            FieldDiff("write_folder_prefix", want["folder_prefix"], grants.get("folder_prefix"))
        )
    return diffs


# -- Staged variant -------------------------------------------------------


def staged_bundle(
    bundle: AgentBundle,
    rename_map: Mapping[str, str],
    staging_workstreams: Sequence[str],
) -> AgentBundle:
    """The ``<agent>-staged`` shadow of a bundle.

    Components for renamed org skills/plugins point at their ``--staged`` names (default
    tier refs never change). It is enabled (a turn needs an enabled process) but assigned
    only to ``staging_workstreams``; write grants are cut down to those workstreams (a
    wildcard is dropped), so a staged run can't land files in a client workstream.
    """
    full_map = {**rename_map, bundle.name: staged_agent_name(bundle.name)}
    comps = tuple(
        (t, s, rename_map.get(r, r) if s == "org" else r) for t, s, r in bundle.components
    )
    allowed = set(staging_workstreams)
    writes = tuple(w for w in bundle.write_workstreams if w in allowed)
    out = replace(
        bundle,
        name=full_map[bundle.name],
        display_name=f"{bundle.display_name}{STAGED_DISPLAY_SUFFIX}",
        components=comps,
        workstreams=tuple(staging_workstreams),
        write_workstreams=writes,
        write_folder_prefix=bundle.write_folder_prefix if writes else "",
        is_enabled=True,
        instructions=(
            rewrite_text(bundle.instructions, full_map) if bundle.instructions is not None else None
        ),
        flow=rewrite_flow(bundle.flow, full_map) if bundle.flow is not None else None,
    )
    validate(out)
    return out
