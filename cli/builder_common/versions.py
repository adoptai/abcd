"""Content hashes, ``bld_versions`` records, the staged lock, default-tier pinning.

The platform keeps no versions or hashes for org skills or processes (PLAN
section 3), so abcd records its own at every push (section 4):

* skills: SHA-256 of ``SKILL.md`` plus a manifest of aux files (path, size,
  sha256), and a deterministic bundle hash over both;
* processes: a hash over the normalized patchable fields, plus a hash per field
  so drift can name the field that changed.

Everything that touches the Data Store goes through a *store*: any object with
the ``RowStore`` methods, which ``OrgDBClient`` implements and tests replace by
an in-memory fake.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.harness_common.api_client import HarnessAPIError

SKILL_MD = "SKILL.md"
IGNORED_DIRS = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_SUFFIXES = (".pyc", ".pyo")

# The fields PATCH /v1/org/processes accepts (api_client.update_process).
PROCESS_FIELDS = (
    "display_name",
    "description",
    "components",
    "agent_instructions",
    "is_enabled",
    "compiled_flow",
)

STAGED_SKILL_SUFFIX = "--staged"
STAGED_AGENT_SUFFIX = "-staged"

T_VERSIONS = "bld_versions"
T_AGENTS = "bld_agents"


class RowStore(Protocol):
    """What the ledger and the lock need from the Data Store."""

    def insert_rows(
        self, table: str, rows: Sequence[Mapping[str, Any]], *, validate: bool = True
    ) -> Any: ...
    def find_rows(self, table: str, column: str, value: str) -> list[dict[str, Any]]: ...
    def update_row(self, table: str, key_column: str, key: str, data: Mapping[str, Any]) -> Any: ...
    def list_all(self, table: str) -> list[dict[str, Any]]: ...


# -- hashing ------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def live_skill_md_digest(description: str, body: str) -> str:
    """Digest of a skill as the platform serves it back.

    ``GET /skills/{name}`` returns the frontmatter's ``description`` and the ``body`` with the
    frontmatter (and the blank lines after it) stripped, never the raw SKILL.md (live,
    2026-10-29), so a whole-file hash can never be compared with a read-back.
    """
    return sha256_text(canonical_json({"description": description.strip(), "body": body.strip()}))


def skill_md_digest(md: str) -> str:
    """The digest :func:`live_skill_md_digest` gives for this SKILL.md text once uploaded."""
    from cli.builder_common.staging import split_frontmatter

    try:
        meta, body = split_frontmatter(md)
    except UsageError:
        return live_skill_md_digest("", md)
    return live_skill_md_digest(str(meta.get("description") or ""), body)


def detail_digest(detail: Mapping[str, Any]) -> str:
    """Digest of a ``GET /skills/{name}`` response (also a raw-SKILL.md ``body``)."""
    body = str(detail.get("body") or "")
    if body.lstrip().startswith("---"):  # a platform build that serves the raw file
        return skill_md_digest(body)
    return live_skill_md_digest(str(detail.get("description") or ""), body)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ignored(rel: Path) -> bool:
    return (
        any(part in IGNORED_DIRS for part in rel.parts[:-1])
        or rel.name in IGNORED_FILES
        or rel.name.endswith(IGNORED_SUFFIXES)
    )


def bundle_digest(skill_md_sha256: str, aux: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic digest over SKILL.md hash and the (path, size, sha256) manifest."""
    entries = sorted(
        ({"path": a["path"], "size": int(a["size"]), "sha256": a.get("sha256") or ""} for a in aux),
        key=lambda e: e["path"],
    )
    return sha256_text(canonical_json({"skill_md_sha256": skill_md_sha256, "aux": entries}))


def hash_skill_dir(path: Path | str) -> dict[str, Any]:
    """Hash a skill directory: ``{skill_md_sha256, aux: [{path,size,sha256}], bundle_sha256}``.

    Deterministic: aux paths are POSIX, sorted; ``__pycache__``, ``.git``, ``.pyc``
    and ``.DS_Store`` are ignored. Symlinks are not followed.
    """
    root = Path(path)
    md = root / SKILL_MD
    if not md.is_file():
        raise UsageError(f"{root}: no {SKILL_MD}")
    skill_sha = skill_md_digest(md.read_text(encoding="utf-8"))
    aux: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.as_posix() == SKILL_MD or _ignored(rel):
            continue
        data = p.read_bytes()
        aux.append({"path": rel.as_posix(), "size": len(data), "sha256": sha256_bytes(data)})
    aux.sort(key=lambda a: a["path"])
    return {
        "skill_md_sha256": skill_sha,
        "aux": aux,
        "bundle_sha256": bundle_digest(skill_sha, aux),
    }


def _norm_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace("\r\n", "\n").rstrip()


def normalize_process(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The patchable process fields, normalized so equal processes hash equal.

    Missing and empty descriptions/instructions are both ``None``; component
    entries keep only ``type``, ``source``, ``ref`` (in order, since order is
    the agent's); ``is_enabled`` is a bool; ``compiled_flow`` passes through.
    """
    components = []
    for c in spec.get("components") or []:
        components.append({"type": c.get("type"), "source": c.get("source"), "ref": c.get("ref")})
    return {
        "display_name": _norm_text(spec.get("display_name")),
        "description": _norm_text(spec.get("description")) or None,
        "components": components,
        "agent_instructions": _norm_text(spec.get("agent_instructions")) or None,
        "is_enabled": bool(spec.get("is_enabled", False)),
        "compiled_flow": spec.get("compiled_flow") or None,
    }


def process_field_hashes(spec: Mapping[str, Any]) -> dict[str, str]:
    norm = normalize_process(spec)
    return {k: sha256_text(canonical_json(norm[k])) for k in PROCESS_FIELDS}


def hash_process(spec: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(normalize_process(spec)))


def staged_name(kind: str, name: str) -> str:
    """Name of the staged shadow on the platform (PLAN section 7)."""
    if kind == "agent":
        return name + STAGED_AGENT_SUFFIX
    return name + STAGED_SKILL_SUFFIX


# -- version records ----------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class VersionRecord:
    """One ``bld_versions`` row."""

    version_id: str
    object_kind: str
    object_name: str
    content_hash: str
    state: str = "draft"
    git_sha: str | None = None
    parent_version_id: str | None = None
    skill_md_sha256: str | None = None
    manifest: dict[str, Any] | None = None
    pinned_components: dict[str, Any] | None = None
    replay_fingerprint: str | None = None
    author_user_id: str | None = None
    reason_ref: str | None = None
    notes_path: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> VersionRecord:
        data: dict[str, Any] = {k: row.get(k) for k in cls.__dataclass_fields__}
        for key in ("manifest", "pinned_components"):
            if isinstance(data[key], str):
                data[key] = json.loads(data[key])
        return cls(**data)

    @property
    def number(self) -> int:
        return _version_number(self.version_id)

    @property
    def aux(self) -> list[dict[str, Any]]:
        return list((self.manifest or {}).get("aux", []))

    @property
    def field_hashes(self) -> dict[str, str]:
        return dict((self.manifest or {}).get("fields", {}))


def _version_number(version_id: str) -> int:
    try:
        return int(version_id.rsplit("@v", 1)[1])
    except (IndexError, ValueError):
        return -1


def skill_record(
    name: str,
    hashed: Mapping[str, Any],
    *,
    version_id: str,
    state: str = "draft",
    git_sha: str | None = None,
    parent: str | None = None,
    author: str | None = None,
    reason: str | None = None,
    replay_fingerprint: str | None = None,
    kind: str = "skill",
) -> VersionRecord:
    return VersionRecord(
        version_id=version_id,
        object_kind=kind,
        object_name=name,
        content_hash=hashed["bundle_sha256"],
        state=state,
        git_sha=git_sha,
        parent_version_id=parent,
        skill_md_sha256=hashed["skill_md_sha256"],
        manifest={"aux": [dict(a) for a in hashed["aux"]]},
        replay_fingerprint=replay_fingerprint,
        author_user_id=author,
        reason_ref=reason,
    )


def process_record(
    name: str,
    spec: Mapping[str, Any],
    *,
    version_id: str,
    state: str = "draft",
    git_sha: str | None = None,
    parent: str | None = None,
    author: str | None = None,
    reason: str | None = None,
    pinned_components: dict[str, Any] | None = None,
) -> VersionRecord:
    return VersionRecord(
        version_id=version_id,
        object_kind="agent",
        object_name=name,
        content_hash=hash_process(spec),
        state=state,
        git_sha=git_sha,
        parent_version_id=parent,
        manifest={"fields": process_field_hashes(spec)},
        pinned_components=pinned_components,
        author_user_id=author,
        reason_ref=reason,
    )


class VersionLedger:
    """Reads and writes ``bld_versions`` through a store."""

    def __init__(self, store: RowStore) -> None:
        self.store = store

    def history(self, kind: str, name: str) -> list[VersionRecord]:
        rows = self.store.find_rows(T_VERSIONS, "object_name", name)
        recs = [VersionRecord.from_row(r) for r in rows if r.get("object_kind") == kind]
        return sorted(recs, key=lambda r: r.number)

    def get(self, version_id: str) -> VersionRecord | None:
        rows = self.store.find_rows(T_VERSIONS, "version_id", version_id)
        return VersionRecord.from_row(rows[0]) if rows else None

    def latest(self, kind: str, name: str, state: str | None = None) -> VersionRecord | None:
        recs = [r for r in self.history(kind, name) if state is None or r.state == state]
        return recs[-1] if recs else None

    def next_version_id(self, kind: str, name: str) -> str:
        """Next ``<name>@vN``. ``version_id`` is the table's unique key and carries no kind,
        so the number is taken over every kind sharing the name (a skill and an agent
        called alike must not collide)."""
        rows = self.store.find_rows(T_VERSIONS, "object_name", name)
        nums = [_version_number(str(r.get("version_id", ""))) for r in rows]
        return f"{name}@v{(max(nums) + 1) if nums and max(nums) >= 0 else 1}"

    def _insert(self, rec: VersionRecord) -> VersionRecord:
        result = self.store.insert_rows(T_VERSIONS, [rec.to_row()])
        failed = getattr(result, "failed", None)
        if failed:
            raise PlatformError(f"could not record {rec.version_id}: {failed[0]['error']}")
        if getattr(result, "duplicates", 0):
            raise RefusedError(f"{rec.version_id} is already recorded; versions are immutable")
        return rec

    def record_push(self, rec: VersionRecord) -> VersionRecord:
        """Record a push (state ``draft``). A ``version_id`` of ``""`` is assigned the next number."""
        if not rec.version_id:
            rec.version_id = self.next_version_id(rec.object_kind, rec.object_name)
        if rec.parent_version_id is None:
            prev = self.latest(rec.object_kind, rec.object_name)
            rec.parent_version_id = prev.version_id if prev else None
        rec.state = "draft"
        return self._insert(rec)

    def _set_state(self, version_id: str, state: str) -> None:
        self.store.update_row(T_VERSIONS, "version_id", version_id, {"state": state})

    def record_stage(self, version_id: str) -> VersionRecord:
        """draft -> staged. Earlier staged versions of the same object are superseded."""
        rec = self._require(version_id)
        if rec.state not in ("draft", "staged"):
            raise RefusedError(f"{version_id} is {rec.state}; only a draft can be staged")
        for other in self.history(rec.object_kind, rec.object_name):
            if other.state == "staged" and other.version_id != version_id:
                self._set_state(other.version_id, "superseded")
        self._set_state(version_id, "staged")
        rec.state = "staged"
        return rec

    def record_release(self, version_id: str) -> VersionRecord:
        """staged -> live. The previous live version becomes superseded (the rollback target)."""
        rec = self._require(version_id)
        if rec.state not in ("staged", "draft", "superseded", "live"):
            raise RefusedError(f"{version_id} is {rec.state}")
        for other in self.history(rec.object_kind, rec.object_name):
            if other.state == "live" and other.version_id != version_id:
                self._set_state(other.version_id, "superseded")
        self._set_state(version_id, "live")
        rec.state = "live"
        return rec

    def record_live(self, rec: VersionRecord) -> VersionRecord:
        """Insert a record directly as live (used by the v0 baseline)."""
        rec.state = "live"
        return self._insert(rec)

    def _require(self, version_id: str) -> VersionRecord:
        rec = self.get(version_id)
        if rec is None:
            raise UsageError(f"unknown version {version_id!r}")
        return rec


# -- staged lock (bld_agents) ---------------------------------------------------


@dataclass
class LockResult:
    acquired: bool
    holder: str
    previous_holder: str | None = None
    notice: str | None = None  # set when a lock was taken: tell the previous holder


@dataclass
class LockInfo:
    holder: str | None
    sha: str | None
    since: str | None


class StagedLock:
    """One staged slot per agent, advisory, held in ``bld_agents`` (PLAN section 4)."""

    def __init__(self, store: RowStore, now: Any = _now) -> None:
        self.store = store
        self._now = now

    def _row(self, agent_key: str) -> dict[str, Any] | None:
        rows = self.store.find_rows(T_AGENTS, "agent_key", agent_key)
        return rows[0] if rows else None

    def info(self, agent_key: str) -> LockInfo:
        row = self._row(agent_key) or {}
        return LockInfo(
            row.get("staged_lock_holder"), row.get("staged_lock_sha"), row.get("staged_lock_since")
        )

    def acquire(self, agent_key: str, holder: str, sha: str, *, take: bool = False) -> LockResult:
        """Take the slot. Another holder -> ``RefusedError`` naming them, unless ``take``."""
        row = self._row(agent_key)
        if row is None:
            self.store.insert_rows(
                T_AGENTS,
                [
                    {
                        "agent_key": agent_key,
                        "phase": "building",
                        "staged_lock_holder": holder,
                        "staged_lock_sha": sha,
                        "staged_lock_since": self._now(),
                    }
                ],
            )
            return LockResult(True, holder)
        current = row.get("staged_lock_holder")
        if not current or current == holder:
            since = row.get("staged_lock_since") if current == holder else None
            self.store.update_row(
                T_AGENTS,
                "agent_key",
                agent_key,
                {
                    "staged_lock_holder": holder,
                    "staged_lock_sha": sha,
                    "staged_lock_since": since or self._now(),
                },
            )
            return LockResult(True, holder)
        if not take:
            raise RefusedError(
                f"{agent_key}: staged slot held by {current} since {row.get('staged_lock_since')} "
                f"(commit {row.get('staged_lock_sha')}); use --take to move it"
            )
        self.store.update_row(
            T_AGENTS,
            "agent_key",
            agent_key,
            {
                "staged_lock_holder": holder,
                "staged_lock_sha": sha,
                "staged_lock_since": self._now(),
            },
        )
        return LockResult(
            True,
            holder,
            previous_holder=current,
            notice=f"{holder} took the staged slot of {agent_key} from {current}; their staged push is overwritten",
        )

    def release(self, agent_key: str, holder: str, *, force: bool = False) -> None:
        row = self._row(agent_key)
        if row is None or not row.get("staged_lock_holder"):
            return
        if row["staged_lock_holder"] != holder and not force:
            raise RefusedError(
                f"{agent_key}: staged slot is held by {row['staged_lock_holder']}, not {holder}"
            )
        self.store.update_row(
            T_AGENTS,
            "agent_key",
            agent_key,
            {"staged_lock_holder": None, "staged_lock_sha": None, "staged_lock_since": None},
        )


# -- default-tier pinning ---------------------------------------------------------


def probe_default_skill(client: Any, name: str) -> dict[str, Any] | None:
    """``GET /v1/end-user/agent-harness/default-skills/{name}`` -> version pointer, or None if absent.

    An absent name answers 200 ``{published: false, bundle_version: null}`` (live, 2026-10-29),
    not 404; both mean "no default skill" and give None.

    The route is Adopt-operator only (routes/end_user_agent_harness.py:2566,
    ``require_adopt_user``); a 403 raises ``PlatformError`` so callers can treat
    the version as unknown rather than as "no default skill".
    """
    try:
        data = client._request("GET", f"/v1/end-user/agent-harness/default-skills/{name}")
    except HarnessAPIError as exc:
        if exc.status_code == 404:
            return None
        if exc.status_code == 403:
            raise PlatformError(f"default-skills/{name}: operator-only route (403)") from exc
        raise
    if not isinstance(data, dict) or data.get("published") is False:
        return None
    return data


def catalog_items(catalog: Any) -> list[dict[str, Any]]:
    """Entries of a ``GET /skills`` response (``{"items": [...]}``) or a bare list."""
    if isinstance(catalog, list):
        return [c for c in catalog if isinstance(c, dict)]
    if isinstance(catalog, dict):
        for key in ("items", "skills", "data"):
            if isinstance(catalog.get(key), list):
                return [c for c in catalog[key] if isinstance(c, dict)]
    return []


def pin_components(
    components: Iterable[Mapping[str, Any]],
    catalog: Any,
    *,
    org_versions: Mapping[str, str] | None = None,
    default_versions: Mapping[str, str | None] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Build ``bld_versions.pinned_components`` for an agent.

    Every component keeps ``{type, source, ref}``. Org components get the
    ``version_id`` from ``org_versions``. Default-tier components get a
    ``bundle_version`` from, in order: the catalog entry (if the platform ever
    includes one), ``default_versions``, then a probe of the operator-only
    default-skills route when ``client`` is given. ``None`` means unknown.
    """
    by_name = {c.get("name"): c for c in catalog_items(catalog)}
    pinned: list[dict[str, Any]] = []
    defaults: dict[str, str | None] = {}
    for comp in components:
        entry: dict[str, Any] = {
            "type": comp.get("type"),
            "source": comp.get("source"),
            "ref": comp.get("ref"),
        }
        ref = str(comp.get("ref"))
        if comp.get("source") == "default":
            version = (by_name.get(ref) or {}).get("bundle_version")
            if version is None and default_versions is not None:
                version = default_versions.get(ref)
            if version is None and client is not None:
                try:
                    pointer = probe_default_skill(client, ref)
                except PlatformError:
                    pointer = None
                version = (pointer or {}).get("bundle_version")
            entry["bundle_version"] = version
            defaults[ref] = version
        elif org_versions and ref in org_versions:
            entry["version_id"] = org_versions[ref]
        pinned.append(entry)
    return {"components": pinned, "default_skills": defaults}


@dataclass
class HashedSkill:
    """A skill as read back from the platform (no aux content hashes unless fetched)."""

    name: str
    skill_md_sha256: str
    aux: list[dict[str, Any]] = field(default_factory=list)  # {path, size, sha256?, partial?}
    body: str = ""

    def manifest(self) -> dict[str, Any]:
        return {"aux": [{k: v for k, v in a.items() if k != "partial"} for a in self.aux]}
