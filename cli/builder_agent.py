"""``abcd agent new|push|pull|diff`` and the plumbing the release commands share.

``abcd agent`` also holds the existing ``list|get|create|update|delete|workstreams|
set-workstreams|run`` (``cli/harness_process.py``); this module only adds the Builder verbs.

The shared half (used by ``cli/builder_release.py``):

* :class:`Services` and :func:`get_services`: every platform, Data Store, Doc Store, driver
  and GitHub dependency in one object, so tests inject fakes in one place
  (``SERVICES_FACTORY``);
* object discovery (which skills and plugins an agent uses), upload payloads and hashes;
* ``upload_*`` / ``verify_*``: push one skill or plugin under a given name and read it back;
* :func:`sync_process`: create or patch a process, its workstreams and its write grants.

Safety rules kept here (PLAN.md sections 4 and 7):

* a staged agent is assigned only to staging and dataset workstreams (``is_staging_workstream``);
  its write grants are cut down to those, so a staged run cannot land a file in a client one;
* ``recording_bundle.json`` (full HAR and cookies) is used for the browser gate but never uploaded;
* every browser skill passes the gate on every push, staged or live.
"""

from __future__ import annotations

import base64
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common import agent_bundle as ab
from cli.builder_common import browser_gate as bg
from cli.builder_common import repo
from cli.builder_common import workspace as bws
from cli.builder_common.cloudmode import cloud_mode
from cli.builder_common.datasets import recorded_workstream_ids
from cli.builder_common.docstore import DocStore, DocStoreArtifacts
from cli.builder_common.drift import compare_skill, fetch_live_skill
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.evals.bff_driver import BffHarnessDriver
from cli.builder_common.evals.protocols import ArtifactStore, HarnessDriver
from cli.builder_common.identity import current_user
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.platform import BuilderPlatformClient
from cli.builder_common.staging import STAGED_AGENT_SUFFIX, staged_name
from cli.builder_common.versions import (
    RowStore,
    StagedLock,
    VersionLedger,
    VersionRecord,
    bundle_digest,
    pin_components,
    process_record,
    sha256_bytes,
    skill_md_digest,
    skill_record,
)
from cli.core.output import Result
from cli.harness_common.api_client import HarnessAPIError
from cli.harness_common.secret_scan import scan_bytes
from cli.harness_skill import (
    _SLUG_RE,
    _collect_aux_files,
    _is_excluded,
    run_skill_audit,
    scan_upload,
)

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

#: Files that are read for the gate but never uploaded (full HAR and the app's cookies).
UPLOAD_NEVER = frozenset({"recording_bundle.json"})
T_AGENTS = "bld_agents"
T_RELEASES = "bld_releases"
T_DECISIONS = "bld_decisions"


# -- services -----------------------------------------------------------------------------


@dataclass
class Services:
    """Everything a Builder command talks to. Tests build this with fakes."""

    client: Any  # BuilderPlatformClient or a fake with the same methods
    rows: RowStore
    docs: ArtifactStore
    driver: HarnessDriver | None = None
    github: Callable[[str], Any] | None = None  # remote url -> object with create_pr/create_release


def default_services(ctx: Context, ws: Path, cfg: dict[str, Any]) -> Services:
    client = BuilderPlatformClient.wrap(ctx.harness_client())
    return Services(
        client=client,
        rows=OrgDBClient.from_client(client),
        docs=DocStoreArtifacts(DocStore(client, cfg.get("stores", {}))),
        driver=BffHarnessDriver(
            client,
            workstream_ids=recorded_workstream_ids(ws),
            model=getattr(ctx.args, "model", None),
        ),
        github=repo.github_backend,
    )


#: Replaced by tests (``monkeypatch.setattr(builder_agent, "SERVICES_FACTORY", ...)``).
SERVICES_FACTORY: Callable[[Context, Path, dict[str, Any]], Services] = default_services


def git_identity(ws: Path) -> tuple[str, str] | None:
    """Fallback committer when git has none configured (cloud runners, fresh machines)."""
    proc = repo.run_git(ws, "config", "user.email", check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return None
    return ("abcd", "abcd@localhost")


@dataclass
class Work:
    """One command's view of the workspace and the platform."""

    ctx: Context
    ws: Path
    cfg: dict[str, Any]
    svc: Services
    who: str
    ledger: VersionLedger
    lock: StagedLock

    @property
    def harness(self) -> Path:
        return bws.harness_dir(self.ws)

    @property
    def client(self) -> Any:
        return self.svc.client

    @property
    def name_to_id(self) -> dict[str, str]:
        return dict(self.cfg.get("workstreams", {}))

    def note(self, *parts: Any) -> None:
        self.ctx.log(*parts)


def open_work(ctx: Context) -> Work:
    ws = ctx.env_dir
    bws.assert_not_default(ws)
    if not ws.is_dir():
        raise UsageError(f"workspace {ctx.env!r} not found at {ws}")
    cfg = bws.load_builder_config(ws)
    svc = SERVICES_FACTORY(ctx, ws, cfg)
    return Work(ctx, ws, cfg, svc, current_user(ws), VersionLedger(svc.rows), StagedLock(svc.rows))


# -- bundles and objects -------------------------------------------------------------------


def load_agent_bundle(ws: Path, agent: str) -> ab.AgentBundle:
    d = bws.agent_dir(ws, agent)
    if not d.is_dir():
        raise UsageError(f"no agent bundle at {d}; create one with `abcd agent new {agent}`")
    bundle = ab.load_bundle(d)
    if bundle.name != agent:
        raise UsageError(f"{d}/process.yaml names {bundle.name!r}, not {agent!r}")
    return bundle


@dataclass(frozen=True)
class Obj:
    """A skill or plugin an agent uses: its live name and where its source is."""

    kind: str  # skill | plugin
    name: str
    src: Path

    def inner_skills(self) -> list[str]:
        if self.kind != "plugin":
            return [self.name]
        return sorted(p.parent.name for p in (self.src / "skills").glob("*/SKILL.md"))


def find_object(harness: Path, ref: str, prefer: str | None = None) -> Obj | None:
    skill = harness / "skills" / ref
    plugin = harness / "plugins" / ref
    found = []
    if (skill / "SKILL.md").is_file():
        found.append(Obj("skill", ref, skill))
    if (plugin / ".claude-plugin" / "plugin.json").is_file():
        found.append(Obj("plugin", ref, plugin))
    if prefer:
        found.sort(key=lambda o: o.kind != prefer)
    return found[0] if found else None


def objects_for(harness: Path, bundle: ab.AgentBundle) -> tuple[list[Obj], list[str]]:
    """Org components that have source in the repo, and those that do not (``type:ref``)."""
    objs: list[Obj] = []
    missing: list[str] = []
    for t, s, r in bundle.components:
        if s != "org":
            continue
        obj = find_object(harness, r, prefer=t)
        if obj is None:
            missing.append(f"{t}:{r}")
        elif obj not in objs:
            objs.append(obj)
    return objs, missing


def rename_map_for(objs: Sequence[Obj]) -> dict[str, str]:
    """Live name -> staged name for every object and every skill inside a plugin."""
    out: dict[str, str] = {}
    for o in objs:
        out[o.name] = staged_name(o.name)
        if o.kind == "plugin":
            for s in o.inner_skills():
                out[s] = staged_name(s)
    return out


def is_staging_workstream(name: str) -> bool:
    """Workstreams a staged agent may be assigned to: staging and dataset ones only."""
    return name.endswith("-staging") or name.startswith(("ds:", "ds-"))


def staging_workstream_names(w: Work, assigned_ids: Sequence[str] = ()) -> list[str]:
    """Staging/dataset workstreams known to builder.json, plus ones the staged process already
    has that are still staging/dataset. A client workstream is never in the result."""
    names = [n for n in w.name_to_id if is_staging_workstream(n)]
    by_id = {v: k for k, v in w.name_to_id.items()}
    for i in assigned_ids:
        n = by_id.get(i)
        if n and is_staging_workstream(n) and n not in names:
            names.append(n)
    return sorted(names)


# -- hashing and upload payloads -----------------------------------------------------------


def skill_payload(skill_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """``(SKILL.md text, aux files)`` exactly as a push would upload them."""
    md = (skill_dir / "SKILL.md").read_text()
    aux, _skipped = _collect_aux_files(skill_dir)
    return md, [a for a in aux if a["path"].rsplit("/", 1)[-1] not in UPLOAD_NEVER]


def hash_payload(md: str, aux: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for a in aux:
        data = base64.b64decode(a["content_b64"])
        entries.append({"path": a["path"], "size": len(data), "sha256": sha256_bytes(data)})
    entries.sort(key=lambda e: e["path"])
    md_sha = skill_md_digest(md)
    return {
        "skill_md_sha256": md_sha,
        "aux": entries,
        "bundle_sha256": bundle_digest(md_sha, entries),
    }


def hash_skill_upload(skill_dir: Path) -> dict[str, Any]:
    md, aux = skill_payload(skill_dir)
    return hash_payload(md, aux)


def secret_findings(md: str, aux: Sequence[dict[str, str]], prefix: str = "") -> list[str]:
    return [str(f) for f in scan_upload(md, list(aux), prefix)]


def audit_skill_dir(skill_dir: Path) -> tuple[int, str]:
    return run_skill_audit(skill_dir)


def skill_dirs_of(obj: Obj) -> list[Path]:
    if obj.kind == "skill":
        return [obj.src]
    return sorted(p.parent for p in (obj.src / "skills").glob("*/SKILL.md"))


def plugin_zip(plugin_dir: Path) -> tuple[bytes, list[str], list[str]]:
    """Zip a plugin like ``harness_skill.build_plugin_zip`` but never ship capture bundles.

    Returns ``(zip bytes, skill names, secret findings)``.
    """
    from cli.harness_skill import build_plugin_zip

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / plugin_dir.name
        shutil.copytree(plugin_dir, copy, ignore=shutil.ignore_patterns(*UPLOAD_NEVER))
        try:
            data, _meta, skills, findings = build_plugin_zip(copy)
        except ValueError as e:
            raise UsageError(str(e)) from e
    return data, skills, [str(f) for f in findings]


def hash_plugin_skills(plugin_dir: Path) -> dict[str, dict[str, Any]]:
    """Per inner skill: the upload hashes (what the catalog can be asked about)."""
    return {
        d.name: hash_skill_upload(d) for d in sorted(skill_dirs_of(Obj("plugin", "", plugin_dir)))
    }


# -- the browser gate, audit and secret scan over one object -------------------------------


@dataclass
class GateOptions:
    local_approval: bool = False
    approver: str | None = None


def run_browser_gate(
    client: Any, obj: Obj, opts: GateOptions, who: str, *, src: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Gate every skill of ``obj``. Returns ``{skill: {mode, fingerprint, approver}}`` for the
    browser ones. Raises ``GateRefusedError`` (exit 1) on the first refusal."""
    out: dict[str, dict[str, Any]] = {}
    root = src or obj.src
    dirs = skill_dirs_of(Obj(obj.kind, obj.name, root))
    for d in dirs:
        res = bg.check_browser_skill(
            d,
            client,
            local_approval=opts.local_approval,
            approver=opts.approver if opts.local_approval else None,
        )
        if res.browser:
            out[d.name] = {
                "mode": res.mode,
                "fingerprint": res.fingerprint,
                "approver": res.approver,
                "conversation_id": res.conversation_id,
            }
    return out


def check_object(
    client: Any,
    obj: Obj,
    opts: GateOptions,
    who: str,
    *,
    strict: bool,
    force_audit: bool = False,
) -> dict[str, Any]:
    """Audit, secret scan and browser gate for one source object.

    ``strict`` (stage): any audit exit code but 0 refuses. Otherwise (push) only 2 does.
    """
    audit_codes: dict[str, int] = {}
    for d in skill_dirs_of(obj):
        code, output = audit_skill_dir(d)
        audit_codes[d.name] = code
        if code == 2 and not force_audit:
            raise RefusedError(f"{d.name}: critical audit findings\n{output.strip()}")
        if strict and code != 0:
            raise RefusedError(
                f"{d.name}: audit exited {code}; stage needs a clean audit\n{output.strip()}"
            )
    findings: list[str] = []
    for d in skill_dirs_of(obj):
        md, aux = skill_payload(d)
        findings += secret_findings(md, aux, f"{obj.name}/" if obj.kind == "plugin" else "")
    if findings:
        raise RefusedError(
            f"{obj.name}: secret scan found {len(findings)} likely credential(s): "
            + "; ".join(findings[:5])
        )
    gate = run_browser_gate(client, obj, opts, who)
    return {"audit": audit_codes, "gate": gate}


def check_bundle_secrets(agent_dir: Path) -> list[str]:
    findings: list[str] = []
    for p in sorted(agent_dir.rglob("*")):
        if p.is_file() and not _is_excluded(p.relative_to(agent_dir).parts):
            findings += [
                str(f) for f in scan_bytes(p.relative_to(agent_dir).as_posix(), p.read_bytes())
            ]
    return findings


# -- the platform: skills --------------------------------------------------------------------


def org_skill_names(client: Any) -> set[str]:
    return {
        str(s["name"]) for s in client.list_catalog() if s.get("source") == "org" and s.get("name")
    }


def plugin_exists(client: Any, name: str) -> bool:
    try:
        client.get_plugin(name)
    except HarnessAPIError as e:
        if e.status_code == 404:
            return False
        raise
    return True


def upload_skill_dir(client: Any, name: str, skill_dir: Path, *, exists: bool) -> dict[str, Any]:
    """Upload ``skill_dir`` as ``name``. A new name goes without ``replace`` so the platform's
    own default-tier collision guard fires (PLAN.md section 4). Returns the upload hashes."""
    md, aux = skill_payload(skill_dir)
    b64 = base64.b64encode(md.encode()).decode("ascii")
    try:
        if exists:
            client.upload_skill(name, b64, aux, replace=True)
        else:
            client.upload_skill_first(name, b64, aux)
    except HarnessAPIError as e:
        if e.status_code == 409:
            raise RefusedError(
                f"{name}: the platform refused the upload (name taken): {e.body}"
            ) from e
        raise PlatformError(f"upload of skill {name} failed: {e}") from e
    return hash_payload(md, aux)


def upload_plugin_dir(client: Any, name: str, plugin_dir: Path, *, exists: bool) -> list[str]:
    data, skills, findings = plugin_zip(plugin_dir)
    if findings:
        raise RefusedError(f"{name}: secret scan found {len(findings)} credential(s)")
    try:
        client.upload_plugin(data, f"{name}.zip", replace=exists)
    except HarnessAPIError as e:
        if e.status_code == 409:
            raise RefusedError(
                f"{name}: the platform refused the upload (name taken): {e.body}"
            ) from e
        raise PlatformError(f"upload of plugin {name} failed: {e}") from e
    return skills


def verify_skill(client: Any, name: str, hashed: Mapping[str, Any]) -> list[str]:
    """Read ``name`` back and compare with ``hashed``. Returns mismatches (empty = identical)."""
    live = fetch_live_skill(client, name, fetch_aux=True)
    rec = skill_record(name, hashed, version_id=f"{name}@v0")
    rep = compare_skill(rec, live, name)
    return [f"{i.kind} {i.name}" for i in rep.items if i.severity in ("warning", "error")]


# -- the platform: processes ---------------------------------------------------------------


@dataclass
class ProcessSync:
    process_id: str
    created: bool
    patched: list[str] = field(default_factory=list)
    workstreams_set: bool = False
    grants_set: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _process_id(res: Mapping[str, Any]) -> str:
    pid = res.get("id") or res.get("process_id")
    if not pid:
        raise PlatformError(f"process response has no id: {sorted(res)}")
    return str(pid)


def find_process_id(w: Work, name: str, hint: str | None = None) -> str | None:
    if hint:
        return hint
    found = w.client.find_process_by_name(name)
    return str(found["id"]) if found else None


def sync_process(
    client: Any,
    bundle: ab.AgentBundle,
    name_to_id: Mapping[str, str],
    *,
    process_id: str | None = None,
) -> ProcessSync:
    """Create or patch the process for ``bundle``, then its workstreams and write grants."""
    ws_ids = ab.workstream_ids(bundle, name_to_id)  # raises on an unknown workstream name
    grants = ab.write_grants_payload(bundle, name_to_id)
    pid = process_id or (lambda f: str(f["id"]) if f else None)(
        client.find_process_by_name(bundle.name)
    )
    if pid is None:
        res = client.create_process(**ab.to_create_payload(bundle))
        sync = ProcessSync(_process_id(res), True, sorted(ab.to_create_payload(bundle)))
    else:
        current = client.get_process(pid)
        fields = ab.to_patch_payload(bundle, current)
        if fields:
            client.update_process(pid, **fields)
        sync = ProcessSync(pid, False, sorted(fields))
    have = client.get_process_workstreams(sync.process_id)
    if sorted(have.get("workstream_ids") or []) != sorted(ws_ids):
        client.set_process_workstreams(sync.process_id, ws_ids)
        sync.workstreams_set = True
    cur = client.get_write_workstreams(sync.process_id)
    cur_ids = sorted(cur.get("workstream_ids") or [])
    cur_prefix = cur.get("folder_prefix") or ""
    if cur_ids != sorted(grants["workstream_ids"]) or (
        cur_ids and cur_prefix != grants["folder_prefix"]
    ):
        client.set_write_workstreams(
            sync.process_id, grants["workstream_ids"], grants["folder_prefix"]
        )
        sync.grants_set = True
    return sync


def verify_process(
    client: Any, bundle: ab.AgentBundle, name_to_id: Mapping[str, str], process_id: str
) -> list[str]:
    diffs = ab.diff_bundle_vs_platform(
        bundle,
        client.get_process(process_id),
        name_to_id,
        assigned_workstream_ids=client.get_process_workstreams(process_id).get("workstream_ids")
        or [],
        write_grants=client.get_write_workstreams(process_id),
    )
    return [d.field for d in diffs]


def missing_components(client: Any, bundle: ab.AgentBundle) -> list[str]:
    """Org components the platform does not have (a process can't point at them)."""
    names = org_skill_names(client)
    plugins: set[str] | None = None
    out = []
    for t, s, r in bundle.components:
        if s != "org":
            continue
        if t == "skill" and r not in names:
            out.append(f"skill:{r}")
        elif t == "plugin":
            if plugins is None:
                data = client.list_plugins()
                items = (
                    data.get("plugins", data.get("items", [])) if isinstance(data, dict) else data
                )
                plugins = {str(p.get("name")) for p in items or [] if isinstance(p, dict)}
            if r not in plugins:
                out.append(f"plugin:{r}")
    return out


# -- bld_agents row ------------------------------------------------------------------------


def agent_row(w: Work, agent: str) -> dict[str, Any]:
    rows = w.svc.rows.find_rows(T_AGENTS, "agent_key", agent)
    return rows[0] if rows else {}


def update_agent_row(w: Work, agent: str, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if v is not None or k.startswith("staged_lock")}
    if agent_row(w, agent):
        w.svc.rows.update_row(T_AGENTS, "agent_key", agent, fields)
    else:
        w.svc.rows.insert_rows(T_AGENTS, [{"agent_key": agent, "phase": "building", **fields}])


def recorded_process_ids(w: Work, agent: str) -> tuple[str | None, str | None]:
    """``(live id, staged id)`` from bld_agents, then builder.json."""
    row, entry = agent_row(w, agent), w.cfg.get("agents", {}).get(agent, {})
    live = row.get("process_id") or entry.get("live_process_id")
    staged = row.get("staged_process_id") or entry.get("staged_process_id")
    return (str(live) if live else None, str(staged) if staged else None)


# -- pushing the staged agent ----------------------------------------------------------------


def latest_reusable(
    ledger: VersionLedger, kind: str, name: str, content_hash: str, pinned: Any = None
) -> VersionRecord | None:
    rec = ledger.latest(kind, name)
    if rec and rec.state in ("draft", "staged") and rec.content_hash == content_hash:
        if pinned is None or rec.pinned_components == pinned:
            return rec
    return None


def staged_agent_bundle(w: Work, bundle: ab.AgentBundle, objs: Sequence[Obj]) -> ab.AgentBundle:
    """The ``<agent>-staged`` bundle, assigned to staging and dataset workstreams only."""
    _live, staged_id = recorded_process_ids(w, bundle.name)
    assigned: list[str] = []
    if staged_id:
        try:
            assigned = list(w.client.get_process_workstreams(staged_id).get("workstream_ids") or [])
        except HarnessAPIError:
            assigned = []
    staging = staging_workstream_names(w, assigned)
    if not staging:
        raise UsageError(
            "builder.json lists no staging or dataset workstream (a name ending '-staging' or "
            "starting 'ds:'); a staged agent has nowhere safe to run. Run `abcd builder setup`."
        )
    clash = sorted(set(staging) & set(bundle.workstreams))
    if clash:
        raise RefusedError(
            f"staging workstream(s) {clash} are also client workstreams of the agent"
        )
    out = ab.staged_bundle(bundle, rename_map_for(objs), staging)
    assert set(out.workstreams) <= set(staging), "staged agent assigned outside staging workstreams"
    return out


def push_agent_staged(
    w: Work,
    bundle: ab.AgentBundle,
    objs: Sequence[Obj],
    skill_versions: Mapping[str, str],
    *,
    git_sha: str | None,
    take: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Lock, then create/patch ``<agent>-staged``, verify, and record the version."""
    staged = staged_agent_bundle(w, bundle, objs)
    missing = missing_components(w.client, staged) if not dry_run else []
    if missing:
        raise RefusedError(
            f"the staged agent points at {missing}, which the platform doesn't have; "
            f"push them first (`abcd push {bundle.name} --staged`)"
        )
    info: dict[str, Any] = {"agent": bundle.name, "staged_name": staged.name, "dry_run": dry_run}
    if dry_run:
        info["workstreams"] = list(staged.workstreams)
        info["write_workstreams"] = list(staged.write_workstreams)
        return info
    lock = w.lock.acquire(bundle.name, w.who, git_sha or "", take=take)
    if lock.notice:
        w.note(f"NOTICE: {lock.notice}")
        info["lock_notice"] = lock.notice
    _live, staged_hint = recorded_process_ids(w, bundle.name)
    sync = sync_process(w.client, staged, w.name_to_id, process_id=staged_hint)
    bad = verify_process(w.client, staged, w.name_to_id, sync.process_id)
    if bad:
        raise PlatformError(f"{staged.name}: platform differs from what was pushed in {bad}")
    spec = w.client.get_process(sync.process_id)
    pinned = pin_components(
        bundle.component_dicts(),
        w.client.list_skills(),
        org_versions=skill_versions,
        client=w.client,
    )
    rec = latest_reusable(
        w.ledger,
        "agent",
        bundle.name,
        process_record(bundle.name, spec, version_id="x").content_hash,
        pinned,
    )
    if rec is None:
        rec = w.ledger.record_push(
            process_record(
                bundle.name,
                spec,
                version_id="",
                git_sha=git_sha,
                author=w.who,
                pinned_components=pinned,
            )
        )
    update_agent_row(
        w,
        bundle.name,
        staged_process_id=sync.process_id,
        staged_version=rec.version_id,
        bundle_path=f"harness/agents/{bundle.name}",
        name=bundle.display_name,
        owner_user_id=w.who,
    )
    info.update(
        process_id=sync.process_id,
        created=sync.created,
        patched=sync.patched,
        version_id=rec.version_id,
        pinned=pinned,
        workstreams=list(staged.workstreams),
        write_workstreams=list(staged.write_workstreams),
    )
    return info


# -- commands --------------------------------------------------------------------------------


def _cmd_new(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    bws.assert_not_default(ws)
    if not _SLUG_RE.match(a.agent):
        raise UsageError(f"agent name {a.agent!r} must match ^[a-z0-9][a-z0-9-]*$")
    d = bws.agent_dir(ws, a.agent)
    files = [d / "process.yaml", d / "instructions.md", d / "release.yaml"]
    if d.exists() and any(p.exists() for p in files):
        raise RefusedError(f"{d} already has a bundle")
    if ctx.dry_run_notice(f"would create the bundle skeleton at {d}"):
        return Result.done({"agent": a.agent, "path": str(d), "dry_run": True}, "")
    bws.new_agent_skeleton(
        ws, a.agent, display_name=a.display_name or a.agent, description=a.description or ""
    )
    (d / "instructions.md").write_text(
        f"# {a.display_name or a.agent}\n\nDescribe the process in general terms: steps, rules, "
        "what to ask a person. Never put a case id, name or expected value here.\n"
    )
    (d / "release.yaml").write_text(f'agent: {a.agent}\ndataset: ""\ncriteria: []\n')
    # blind evals send this and nothing else; without the file `eval run --blind` refuses
    (d / "prompts.yaml").write_text(
        "# Generic prompts for blind evals and the training kit.\n"
        "# Never put a case id, a name or an expected value here.\n"
        'blind: "Process the files in this workstream and produce the work product."\n'
    )
    paths = [str(p.relative_to(ws)) for p in sorted(d.iterdir())]
    return Result.done({"agent": a.agent, "files": paths}, f"Created {', '.join(paths)}")


def _staged_flag_check(ctx: Context, staged: bool) -> None:
    if not staged and cloud_mode(ctx.env_dir):
        raise RefusedError(
            "`agent push` without --staged patches the LIVE process; in a cloud run live changes "
            "go through `abcd release` (which needs a human's approval), never a direct push"
        )
    if not staged and not ctx.yes:
        raise RefusedError(
            "`agent push` without --staged patches the LIVE process and skips the release gate; "
            "use `abcd release`, or pass --yes if you mean it"
        )


def _cmd_push(ctx: Context) -> Result:
    a = ctx.args
    _staged_flag_check(ctx, a.staged)
    w = open_work(ctx)
    bundle = load_agent_bundle(w.ws, a.agent)
    objs, unresolved = objects_for(w.harness, bundle)
    sha = repo.current_sha(w.ws) if (w.ws / ".git").exists() else None
    if a.staged:
        skill_versions = {}
        for o in objs:
            for sk in o.inner_skills():
                rec = w.ledger.latest("skill", sk)
                if rec:
                    skill_versions[sk] = rec.version_id
        info = push_agent_staged(
            w, bundle, objs, skill_versions, git_sha=sha, take=a.take, dry_run=ctx.dry_run
        )
        info["unresolved_components"] = unresolved
        text = (
            f"Would push {info['staged_name']} to {info['workstreams']}"
            if ctx.dry_run
            else f"Pushed {info['staged_name']} ({info['process_id']}) as {info['version_id']}"
        )
        return Result.done(info, text)
    if ctx.dry_run_notice(f"would create or patch the live process {bundle.name}"):
        return Result.done({"agent": a.agent, "dry_run": True}, "")
    missing = missing_components(w.client, bundle)
    if missing:
        raise RefusedError(f"the platform doesn't have {missing}; release them first")
    live_id, _ = recorded_process_ids(w, a.agent)
    sync = sync_process(w.client, bundle, w.name_to_id, process_id=live_id)
    bad = verify_process(w.client, bundle, w.name_to_id, sync.process_id)
    if bad:
        raise PlatformError(f"{a.agent}: platform differs from the bundle in {bad}")
    bws.set_agent_process_ids(w.ws, a.agent, live_process_id=sync.process_id)
    return Result.done(
        {"agent": a.agent, **sync.to_dict()},
        f"{'Created' if sync.created else 'Patched'} live process {sync.process_id}",
    )


def _platform_process(w: Work, agent: str, staged: bool) -> tuple[str, dict[str, Any]]:
    live_id, staged_id = recorded_process_ids(w, agent)
    pid = find_process_id(
        w, agent + (STAGED_AGENT_SUFFIX if staged else ""), staged_id if staged else live_id
    )
    if pid is None:
        raise UsageError(f"no {'staged ' if staged else ''}process for {agent!r} on the platform")
    return pid, w.client.get_process(pid)


def _names_by_id(w: Work) -> dict[str, str]:
    out = {v: k for k, v in w.name_to_id.items()}
    return out


def _cmd_diff(ctx: Context) -> Result:
    a = ctx.args
    w = open_work(ctx)
    bundle = load_agent_bundle(w.ws, a.agent)
    if a.staged:
        objs, _ = objects_for(w.harness, bundle)
        bundle = staged_agent_bundle(w, bundle, objs)
    pid, proc = _platform_process(w, a.agent, a.staged)
    diffs = ab.diff_bundle_vs_platform(
        bundle,
        proc,
        w.name_to_id,
        assigned_workstream_ids=w.client.get_process_workstreams(pid).get("workstream_ids") or [],
        write_grants=w.client.get_write_workstreams(pid),
    )
    data = {
        "agent": a.agent,
        "process_id": pid,
        "staged": bool(a.staged),
        "in_sync": not diffs,
        "diffs": [{"field": d.field, "local": d.local, "platform": d.platform} for d in diffs],
    }
    text = "In sync." if not diffs else "Differs in: " + ", ".join(d.field for d in diffs)
    return Result.done(data, text)


def _cmd_pull(ctx: Context) -> Result:
    a = ctx.args
    w = open_work(ctx)
    pid, proc = _platform_process(w, a.agent, False)
    names = _names_by_id(w)
    ws_ids = list(w.client.get_process_workstreams(pid).get("workstream_ids") or [])
    grants = w.client.get_write_workstreams(pid)
    wanted = set(ws_ids) | set(grants.get("workstream_ids") or [])
    unknown = [i for i in wanted if i not in names and i != ab.ALL_WORKSTREAMS]
    if unknown:
        listing = w.client.list_workstreams()
        items = (
            listing.get("workstreams", listing.get("items", []))
            if isinstance(listing, dict)
            else listing
        )
        for item in items or []:
            if isinstance(item, dict) and item.get("id") in unknown:
                names[str(item["id"])] = str(item.get("name"))
    bundle = ab.from_platform(proc, names, assigned_workstream_ids=ws_ids, write_grants=grants)
    d = bws.agent_dir(w.ws, a.agent)
    changed = []
    if d.is_dir() and (d / "process.yaml").is_file():
        local = ab.load_bundle(d)
        changed = [
            x.field
            for x in ab.diff_bundle_vs_platform(
                local, proc, w.name_to_id, assigned_workstream_ids=ws_ids, write_grants=grants
            )
        ]
    data = {"agent": a.agent, "process_id": pid, "changed": changed, "dry_run": ctx.dry_run}
    if ctx.dry_run_notice(f"would write {d}/process.yaml (differs in {changed or 'nothing'})"):
        return Result.done(data, "")
    ab.write_bundle(bundle, d)
    bws.set_agent_process_ids(w.ws, a.agent, live_process_id=pid)
    return Result.done(data, f"Wrote {d.relative_to(w.ws)} from the live process {pid}")


def register(registry: Registry) -> None:
    g = registry.group("agent", "Agents (processes) in the org")

    p = g.command("new", _cmd_new, help="Create an agent bundle skeleton", writes=True)
    p.add_argument("agent")
    p.add_argument("--display-name")
    p.add_argument("--description")

    p = g.command(
        "push",
        _cmd_push,
        help="Create or patch the process from harness/agents/<agent>/ (--staged: the shadow)",
        writes=True,
    )
    p.add_argument("agent")
    p.add_argument("--staged", action="store_true", help="Push the <agent>-staged shadow")
    p.add_argument("--take", action="store_true", help="Take the staged lock from another FDE")

    p = g.command("pull", _cmd_pull, help="Write the live process into the bundle", writes=True)
    p.add_argument("agent")

    p = g.command("diff", _cmd_diff, help="Compare the bundle with the platform process")
    p.add_argument("agent")
    p.add_argument("--staged", action="store_true", help="Compare with the staged shadow")
