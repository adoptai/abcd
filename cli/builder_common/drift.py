"""Drift audit, ``ws pull`` planning and the ``--adopt`` v0 baseline (PLAN sections 4, 5).

Platform facts this relies on (verified in adoptwebui / adoptai-workflows):

* ``GET /skills/{name}`` returns ``{name, source, description, body, aux_files:
  [{path, size}]}`` and nothing else: no hash, no version, no process info
  (adoptai-workflows ``api/app/routers/agent_harness.py:1199-1216``).
* ``GET /skills/{name}/files?path=`` returns ``{path, content}``: text only,
  truncated at 256 KiB with a marker (``skills.py:71,556,1345``), and 415 for a
  binary file (``agent_harness.py:1282``). So aux files that are binary or
  truncated can be listed (path, size) but not read back: they are *partial*,
  never drift.
* ``GET /skills`` is the merged catalog; an org skill with a default's name
  hides the default entirely (``skills.py:609-615``), so shadowing cannot be
  seen in the catalog and needs a known list of default names or the
  operator-only ``default-skills/{name}`` probe.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.builder_common.errors import PlatformError
from cli.builder_common.versions import (
    PROCESS_FIELDS,
    STAGED_AGENT_SUFFIX,
    STAGED_SKILL_SUFFIX,
    HashedSkill,
    VersionLedger,
    VersionRecord,
    bundle_digest,
    catalog_items,
    detail_digest,
    hash_process,
    normalize_process,
    pin_components,
    probe_default_skill,
    process_field_hashes,
    process_record,
    sha256_text,
    skill_record,
)
from cli.harness_common.api_client import HarnessAPIError

TRUNCATION_MARKER = "[... truncated: only the first "  # skills.py:556
MAX_FILE_BYTES = 256 * 1024  # skills.py:71

SEV_INFO = "info"
SEV_WARNING = "warning"
SEV_ERROR = "error"

LiveSkill = HashedSkill


@dataclass
class DriftItem:
    kind: str
    name: str
    expected: Any
    actual: Any
    severity: str = SEV_ERROR
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass
class DriftReport:
    items: list[DriftItem] = field(default_factory=list)
    partial: list[dict[str, Any]] = field(default_factory=list)  # unverifiable aux files: not drift
    checked: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(i.severity in (SEV_WARNING, SEV_ERROR) for i in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "checked": list(self.checked),
            "items": [i.to_dict() for i in self.items],
            "partial": list(self.partial),
        }


# -- reading live objects -----------------------------------------------------------


def _is_truncated(content: str) -> bool:
    return TRUNCATION_MARKER in content


def fetch_live_skill(client: Any, name: str, *, fetch_aux: bool = False) -> LiveSkill | None:
    """Read a skill back: SKILL.md hash and aux listing; None when it does not exist (404).

    With ``fetch_aux`` each text aux file is read and hashed. A 415 (binary), a
    truncated body, or a decoded length that disagrees with the listed size
    marks the file ``partial`` (no sha256).
    """
    try:
        detail = client.get_skill(name)
    except HarnessAPIError as exc:
        if exc.status_code == 404:
            return None
        raise PlatformError(str(exc)) from exc
    body = str(detail.get("body") or "")
    aux: list[dict[str, Any]] = []
    for item in detail.get("aux_files") or []:
        entry: dict[str, Any] = {
            "path": item["path"],
            "size": int(item.get("size") or 0),
            "partial": False,
        }
        if fetch_aux:
            content = _read_aux(client, name, item["path"])
            if (
                content is None
                or _is_truncated(content)
                or len(content.encode("utf-8")) != entry["size"]
            ):
                entry["partial"] = True
            else:
                entry["sha256"] = sha256_text(content)
        aux.append(entry)
    aux.sort(key=lambda a: a["path"])
    return LiveSkill(name=name, skill_md_sha256=detail_digest(detail), aux=aux, body=body)


def _read_aux(client: Any, name: str, path: str) -> str | None:
    try:
        resp = client._request(
            "GET", f"/v1/end-user/agent-harness/skills/{name}/files", params={"path": path}
        )
    except HarnessAPIError as exc:
        if exc.status_code in (404, 415):
            return None
        raise PlatformError(str(exc)) from exc
    return str(resp.get("content", ""))


def list_processes(client: Any) -> list[dict[str, Any]]:
    data = client.list_processes()
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    for key in ("items", "processes", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return [p for p in data[key] if isinstance(p, dict)]
    return []


# -- comparing ------------------------------------------------------------------------


def compare_skill(
    record: VersionRecord, live: LiveSkill | None, name: str | None = None
) -> DriftReport:
    """Compare a live (or staged shadow) skill with what ``record`` says was uploaded."""
    label = name or record.object_name
    rep = DriftReport(checked=[label])
    if live is None:
        rep.items.append(DriftItem("skill_missing", label, record.version_id, None, SEV_ERROR))
        return rep
    if record.skill_md_sha256 and live.skill_md_sha256 != record.skill_md_sha256:
        rep.items.append(
            DriftItem(
                "skill_md_changed",
                label,
                record.skill_md_sha256,
                live.skill_md_sha256,
                SEV_ERROR,
                record.version_id,
            )
        )
    rec_aux = {a["path"]: a for a in record.aux}
    live_aux = {a["path"]: a for a in live.aux}
    for path in sorted(rec_aux.keys() - live_aux.keys()):
        rep.items.append(
            DriftItem("aux_removed", f"{label}:{path}", rec_aux[path]["size"], None, SEV_ERROR)
        )
    for path in sorted(live_aux.keys() - rec_aux.keys()):
        rep.items.append(
            DriftItem("aux_added", f"{label}:{path}", None, live_aux[path]["size"], SEV_WARNING)
        )
    for path in sorted(rec_aux.keys() & live_aux.keys()):
        exp, act = rec_aux[path], live_aux[path]
        if int(exp["size"]) != int(act["size"]):
            rep.items.append(
                DriftItem(
                    "aux_size_changed", f"{label}:{path}", exp["size"], act["size"], SEV_ERROR
                )
            )
            continue
        if act.get("partial"):
            rep.partial.append({"name": label, "path": path, "reason": "binary_or_truncated"})
        elif act.get("sha256") and exp.get("sha256"):
            if act["sha256"] != exp["sha256"]:
                rep.items.append(
                    DriftItem(
                        "aux_content_changed",
                        f"{label}:{path}",
                        exp["sha256"],
                        act["sha256"],
                        SEV_ERROR,
                    )
                )
        else:
            # same size, content not read back: a changed file of equal size is not detectable (PLAN section 4)
            rep.partial.append({"name": label, "path": path, "reason": "content_not_verified"})
    return rep


def compare_process(
    record: VersionRecord, live: Mapping[str, Any] | None, name: str | None = None
) -> DriftReport:
    label = name or record.object_name
    rep = DriftReport(checked=[label])
    if live is None:
        rep.items.append(DriftItem("process_missing", label, record.version_id, None, SEV_ERROR))
        return rep
    if hash_process(live) == record.content_hash:
        return rep
    expected = record.field_hashes
    actual = process_field_hashes(live)
    norm = normalize_process(live)
    changed = (
        [f for f in PROCESS_FIELDS if expected.get(f) != actual[f]]
        if expected
        else list(PROCESS_FIELDS)
    )
    for f in changed:
        rep.items.append(
            DriftItem(
                "process_field_changed",
                f"{label}.{f}",
                expected.get(f),
                actual[f],
                SEV_ERROR,
                str(norm[f])[:200],
            )
        )
    return rep


def _merge(into: DriftReport, other: DriftReport) -> None:
    into.items.extend(other.items)
    into.partial.extend(other.partial)
    into.checked.extend(other.checked)


def find_default_shadows(
    org_skill_names: Iterable[str],
    *,
    known_default_names: Iterable[str] = (),
    client: Any = None,
) -> list[DriftItem]:
    """Org skills whose names match a default-tier skill (the override is total).

    ``known_default_names`` is checked first; with ``client`` each remaining
    name is probed through the operator-only default-skills route (403 is
    skipped: unknown, not clean). ``--staged`` shadows are not checked.
    """
    known = set(known_default_names)
    out: list[DriftItem] = []
    for name in sorted(set(org_skill_names)):
        if name.endswith(STAGED_SKILL_SUFFIX):
            continue
        pointer: dict[str, Any] | None = None
        hit = name in known
        if not hit and client is not None:
            try:
                pointer = probe_default_skill(client, name)
            except PlatformError:
                pointer = None
            hit = pointer is not None
        if hit:
            out.append(
                DriftItem(
                    "default_shadow",
                    name,
                    "default-tier skill visible",
                    "org skill overrides it for the whole org",
                    SEV_WARNING,
                    str((pointer or {}).get("bundle_version") or ""),
                )
            )
    return out


def check_default_pins(record: VersionRecord, current: Mapping[str, str | None]) -> list[DriftItem]:
    """Default-tier ``bundle_version`` moved since the agent version was recorded."""
    out: list[DriftItem] = []
    pinned = (record.pinned_components or {}).get("default_skills", {})
    for skill, version in sorted(pinned.items()):
        now = current.get(skill)
        if version is not None and now is not None and now != version:
            out.append(
                DriftItem(
                    "default_bundle_moved", skill, version, now, SEV_WARNING, record.version_id
                )
            )
    return out


def build_drift_report(
    records: Sequence[VersionRecord],
    live_skills: Mapping[str, LiveSkill | None],
    live_processes: Mapping[str, Mapping[str, Any] | None],
    *,
    shadows: Sequence[DriftItem] = (),
) -> DriftReport:
    """Pure comparison. Only ``live`` and ``staged`` records are audited.

    ``live_skills`` / ``live_processes`` are keyed by the *platform* name: the
    plain name for live records, ``<name>--staged`` / ``<agent>-staged`` for staged
    ones. Records of one object compare against the newest record in each state.
    """
    rep = DriftReport()
    newest: dict[tuple[str, str, str], VersionRecord] = {}
    for r in sorted(records, key=lambda r: r.number):
        if r.state in ("live", "staged"):
            newest[(r.object_kind, r.object_name, r.state)] = r
    for (kind, name, state), rec in sorted(newest.items()):
        if kind == "agent":
            pname = name + (STAGED_AGENT_SUFFIX if state == "staged" else "")
            _merge(rep, compare_process(rec, live_processes.get(pname), pname))
        else:
            pname = name + (STAGED_SKILL_SUFFIX if state == "staged" else "")
            _merge(rep, compare_skill(rec, live_skills.get(pname), pname))
    rep.items.extend(shadows)
    return rep


def audit_drift(
    client: Any,
    ledger: VersionLedger,
    *,
    fetch_aux: bool = False,
    known_default_names: Iterable[str] = (),
    probe_defaults: bool = False,
) -> DriftReport:
    """Fetch live objects for every recorded live/staged version and compare."""
    records = [VersionRecord.from_row(r) for r in ledger.store.list_all("bld_versions")]
    wanted = [r for r in records if r.state in ("live", "staged")]
    live_skills: dict[str, LiveSkill | None] = {}
    live_procs: dict[str, Mapping[str, Any] | None] = {}
    procs_by_name = (
        {str(p.get("name")): p for p in list_processes(client)}
        if any(r.object_kind == "agent" for r in wanted)
        else {}
    )
    for r in wanted:
        if r.object_kind == "agent":
            pname = r.object_name + (STAGED_AGENT_SUFFIX if r.state == "staged" else "")
            live_procs[pname] = procs_by_name.get(pname)
        else:
            pname = r.object_name + (STAGED_SKILL_SUFFIX if r.state == "staged" else "")
            if pname not in live_skills:
                live_skills[pname] = fetch_live_skill(client, pname, fetch_aux=fetch_aux)
    catalog = catalog_items(client.list_skills())
    org_names = [c["name"] for c in catalog if c.get("source") == "org" and not c.get("plugin")]
    shadows = find_default_shadows(
        org_names,
        known_default_names=known_default_names,
        client=client if probe_defaults else None,
    )
    return build_drift_report(records, live_skills, live_procs, shadows=shadows)


# -- ws pull planning ---------------------------------------------------------------------


@dataclass
class PullFile:
    path: str
    size: int
    status: str  # "text" | "partial"
    reason: str = ""  # binary | truncated | size_mismatch
    content: str | None = None


@dataclass
class PullItem:
    kind: str  # skill | process
    name: str
    files: list[PullFile] = field(default_factory=list)
    skill_md: str | None = None
    process: dict[str, Any] | None = None

    @property
    def partial(self) -> bool:
        return any(f.status == "partial" for f in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "partial": self.partial,
            "files": [
                {"path": f.path, "size": f.size, "status": f.status, "reason": f.reason}
                for f in self.files
            ],
        }


@dataclass
class PullPlan:
    items: list[PullItem] = field(default_factory=list)
    already_in_repo: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "import": [i.to_dict() for i in self.items],
            "already_in_repo": list(self.already_in_repo),
            "skipped": list(self.skipped),
        }


def pull_plan(client: Any, harness_dir: Path | str, *, with_content: bool = True) -> PullPlan:
    """What ``abcd ws pull`` would import: org skills and processes absent from the repo.

    ``harness_dir`` is the workspace's ``harness/`` folder (``skills/<name>/SKILL.md``,
    ``agents/<agent>/``). Staged shadows (``--staged`` skills, ``-staged``
    processes) and skills bundled in plugins are skipped. Writes nothing.
    """
    root = Path(harness_dir)
    plan = PullPlan()
    for entry in catalog_items(client.list_skills()):
        name = str(entry.get("name"))
        if entry.get("source") != "org":
            continue
        if entry.get("plugin"):
            plan.skipped.append({"name": name, "reason": f"bundled in plugin {entry['plugin']}"})
            continue
        if name.endswith(STAGED_SKILL_SUFFIX):
            plan.skipped.append({"name": name, "reason": "staged shadow"})
            continue
        if (root / "skills" / name / "SKILL.md").is_file():
            plan.already_in_repo.append(f"skill:{name}")
            continue
        live = fetch_live_skill(client, name)
        if live is None:
            plan.skipped.append({"name": name, "reason": "listed but not readable"})
            continue
        item = PullItem("skill", name, skill_md=live.body)
        for aux in live.aux:
            content = _read_aux(client, name, aux["path"])
            if content is None:
                item.files.append(PullFile(aux["path"], aux["size"], "partial", "binary"))
            elif _is_truncated(content):
                item.files.append(PullFile(aux["path"], aux["size"], "partial", "truncated"))
            elif len(content.encode("utf-8")) != aux["size"]:
                item.files.append(PullFile(aux["path"], aux["size"], "partial", "size_mismatch"))
            else:
                item.files.append(
                    PullFile(
                        aux["path"], aux["size"], "text", content=content if with_content else None
                    )
                )
        plan.items.append(item)
    for proc in list_processes(client):
        name = str(proc.get("name"))
        if name.endswith(STAGED_AGENT_SUFFIX):
            plan.skipped.append({"name": name, "reason": "staged shadow"})
            continue
        if (root / "agents" / name).is_dir():
            plan.already_in_repo.append(f"agent:{name}")
            continue
        plan.items.append(PullItem("process", name, process=normalize_process(proc)))
    return plan


# -- --adopt baseline ------------------------------------------------------------------------


def baseline_records(
    live_skills: Iterable[LiveSkill],
    live_processes: Iterable[Mapping[str, Any]],
    *,
    author: str,
    catalog: Any = None,
    default_versions: Mapping[str, str | None] | None = None,
    git_sha: str | None = None,
    client: Any = None,
) -> list[VersionRecord]:
    """v0 records (state live) for what is on the platform, hashes as pulled.

    Skill aux entries carry sizes (and sha256 only where content was read), so
    their bundle digest is a manifest digest, not a source-tree hash.
    """
    out: list[VersionRecord] = []
    skill_versions: dict[str, str] = {}
    for s in live_skills:
        vid = f"{s.name}@v0"
        skill_versions[s.name] = vid
        rec = skill_record(
            s.name,
            {
                "skill_md_sha256": s.skill_md_sha256,
                "aux": [{k: v for k, v in a.items() if k != "partial"} for a in s.aux],
                "bundle_sha256": bundle_digest(s.skill_md_sha256, s.aux),
            },
            version_id=vid,
            state="live",
            git_sha=git_sha,
            author=author,
            reason="adopt-baseline",
        )
        out.append(rec)
    for p in live_processes:
        name = str(p.get("name"))
        pinned = pin_components(
            p.get("components") or [],
            catalog,
            org_versions=skill_versions,
            default_versions=default_versions,
            client=client,
        )
        out.append(
            process_record(
                name,
                p,
                version_id=f"{name}@v0",
                state="live",
                git_sha=git_sha,
                author=author,
                reason="adopt-baseline",
                pinned_components=pinned,
            )
        )
    return out


def adopt_baseline(
    client: Any,
    *,
    author: str,
    git_sha: str | None = None,
    fetch_aux: bool = False,
    default_versions: Mapping[str, str | None] | None = None,
    probe_defaults: bool = False,
) -> list[VersionRecord]:
    """Read everything live (org skills, processes, not staged shadows) and return v0 records.

    Pure with respect to the Data Store: the caller inserts the records with
    ``VersionLedger.record_live``.
    """
    catalog = client.list_skills()
    skills: list[LiveSkill] = []
    for entry in catalog_items(catalog):
        name = str(entry.get("name"))
        if (
            entry.get("source") != "org"
            or entry.get("plugin")
            or name.endswith(STAGED_SKILL_SUFFIX)
        ):
            continue
        live = fetch_live_skill(client, name, fetch_aux=fetch_aux)
        if live is not None:
            skills.append(live)
    procs = [
        p for p in list_processes(client) if not str(p.get("name")).endswith(STAGED_AGENT_SUFFIX)
    ]
    return baseline_records(
        skills,
        procs,
        author=author,
        catalog=catalog,
        default_versions=default_versions,
        git_sha=git_sha,
        client=client if probe_defaults else None,
    )
