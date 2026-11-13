"""``abcd push --staged | stage | test-staged | release | rollback | audit drift``.

The most safety-critical Builder module (PLAN.md sections 4, 5, 7 and 10):

* ``push --staged`` builds ``--staged`` shadows from the workspace (``staging``), runs the audit, the
  secret scan and the browser gate on every browser skill, uploads (the first upload of a name
  without ``replace``), takes the staged lock, records versions, and pushes ``<agent>-staged``
  assigned only to staging and dataset workstreams.
* ``stage`` refuses on a dirty tree, an unpushed commit, a non-zero audit, a secret, a failing
  gate or a lock held by someone else; then records the staged hashes and tags ``<agent>@v<n>-rc``.
* ``test-staged`` runs the blind eval on the staged agent (resumable).
* ``release`` follows section 7's nine steps in order. Steps 1 to 4 only read; nothing changes under
  the live names until they all pass. It never commits to the default branch: what it generates
  goes to ``release/<agent>@v<n>`` (built in a separate git worktree) with a pull request.
* ``rollback`` and ``release --redeploy`` re-upload a tag's bundles under the live names.

Every write honours ``--dry-run``, and nothing prompts: approval is ``--yes``.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cli.builder_agent import (
    T_DECISIONS,
    T_RELEASES,
    GateOptions,
    Obj,
    Work,
    check_bundle_secrets,
    check_object,
    find_object,
    git_identity,
    hash_skill_upload,
    load_agent_bundle,
    objects_for,
    open_work,
    org_skill_names,
    plugin_exists,
    push_agent_staged,
    recorded_process_ids,
    rename_map_for,
    skill_dirs_of,
    staged_agent_bundle,
    sync_process,
    update_agent_row,
    upload_plugin_dir,
    upload_skill_dir,
    verify_process,
    verify_skill,
)
from cli.builder_common import agent_bundle as ab
from cli.builder_common import explain, gaps, gate_card, train
from cli.builder_common import repo as rp
from cli.builder_common import workspace as bws
from cli.builder_common.cloudmode import cloud_mode
from cli.builder_common.datasets import load_dataset
from cli.builder_common.drift import (
    DriftItem,
    DriftReport,
    audit_drift,
    build_drift_report,
    compare_process,
    compare_skill,
    fetch_live_skill,
    list_processes,
)
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.evals.gate import GateDecision, GateRefusedError, evaluate_criteria
from cli.builder_common.evals.metrics import RunResults, open_gaps, pass_rate
from cli.builder_common.evals.report import build_report, render_html
from cli.builder_common.evals.runner import (
    advance,
    collect,
    load_state,
    results_from_state,
    run_eval,
)
from cli.builder_common.release_approval import (
    refuse_self_asserted_in_cloud,
    require_release_approval,
)
from cli.builder_common.rowstore import OrgRowStore
from cli.builder_common.staging import (
    build_staged_plugin,
    build_staged_skill,
    live_upload_dir,
    staged_agent_name,
    staged_name,
)
from cli.builder_common.versions import (
    T_VERSIONS,
    VersionRecord,
    hash_process,
    probe_default_skill,
    process_field_hashes,
    skill_record,
)
from cli.core.output import Result
from cli.harness_common.api_client import HarnessAPIError

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

NO_REGRESSION = "no_regression"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


# -- argument helpers -------------------------------------------------------------------------


def _gate_args(p: Any) -> None:
    p.add_argument(
        "--local-approval",
        action="store_true",
        help="Accept a browser-skill replay approved outside a harness chat (needs --approver)",
    )
    p.add_argument("--approver", help="Who approved the replay (recorded in bld_versions)")


def _gate_opts(ctx: Context) -> GateOptions:
    a = ctx.args
    refuse_self_asserted_in_cloud(
        ctx.env_dir,
        local_approval=getattr(a, "local_approval", False),
        approver=getattr(a, "approver", None),
    )
    return GateOptions(bool(getattr(a, "local_approval", False)), getattr(a, "approver", None))


# -- targets for push -------------------------------------------------------------------------


@dataclass
class Targets:
    agents: list[tuple[ab.AgentBundle, list[Obj], list[str]]] = field(default_factory=list)
    objects: list[Obj] = field(default_factory=list)


def resolve_targets(w: Work, names: Sequence[str]) -> Targets:
    """Agents, skills and plugins named on the command line (all of them when none are named).

    Naming an agent also selects every skill and plugin it uses: the staged agent's components
    point at their ``--staged`` copies, so those must exist.
    """
    t = Targets()
    wanted = list(names) or [
        *bws.list_agents(w.ws),
        *sorted(p.name for p in (w.harness / "skills").glob("*/") if (p / "SKILL.md").is_file()),
        *sorted(
            p.name
            for p in (w.harness / "plugins").glob("*/")
            if (p / ".claude-plugin" / "plugin.json").is_file()
        ),
    ]

    def add_obj(o: Obj) -> None:
        if o not in t.objects:
            t.objects.append(o)

    for n in dict.fromkeys(wanted):
        hits: list[str] = []
        if (bws.agent_dir(w.ws, n) / "process.yaml").is_file():
            hits.append("agent")
        obj = find_object(w.harness, n)
        if obj is not None:
            hits.append(obj.kind)
        if not hits:
            raise UsageError(f"{n!r} is not an agent, skill or plugin in harness/")
        if len(hits) > 1 and names:
            raise UsageError(f"{n!r} is ambiguous: it is both {' and '.join(hits)}")
        if "agent" in hits:
            bundle = load_agent_bundle(w.ws, n)
            objs, missing = objects_for(w.harness, bundle)
            t.agents.append((bundle, objs, missing))
            for o in objs:
                add_obj(o)
        else:
            assert obj is not None
            add_obj(obj)
    return t


def check_lock(w: Work, agent: str, *, take: bool) -> None:
    info = w.lock.info(agent)
    if info.holder and info.holder != w.who and not take:
        raise RefusedError(
            f"{agent}: staged slot held by {info.holder} since {info.since} (commit {info.sha}); "
            "use --take to move it"
        )


# -- staged objects ---------------------------------------------------------------------------


def _approval_manifest(gate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not gate:
        return None
    return {
        "mode": gate.get("mode"),
        "approver": gate.get("approver"),
        "conversation_id": gate.get("conversation_id"),
    }


def _record_skill_push(
    w: Work,
    live_name: str,
    hashed: Mapping[str, Any],
    sha: str | None,
    gate: Mapping[str, Any] | None,
) -> VersionRecord:
    rec = w.ledger.latest("skill", live_name)
    if rec and rec.state in ("draft", "staged") and rec.content_hash == hashed["bundle_sha256"]:
        return rec
    new = skill_record(
        live_name,
        hashed,
        version_id="",
        git_sha=sha,
        author=w.who,
        replay_fingerprint=(gate or {}).get("fingerprint"),
    )
    approval = _approval_manifest(gate)
    if approval and new.manifest is not None:
        new.manifest["approval"] = approval
    return w.ledger.record_push(new)


def push_object_staged(
    w: Work,
    obj: Obj,
    rename_map: Mapping[str, str],
    sha: str | None,
    gates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Upload ``obj``'s staged copy, read it back, and record its version(s)."""
    client = w.client
    out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        if obj.kind == "skill":
            st = build_staged_skill(obj.src, Path(tmp) / "skill", rename_map)
            exists = st.staged_name in org_skill_names(client)
            hashed = upload_skill_dir(client, st.staged_name, st.out_dir, exists=exists)
            bad = verify_skill(client, st.staged_name, hashed)
            if bad:
                raise PlatformError(f"{st.staged_name}: platform differs after upload: {bad}")
            rec = _record_skill_push(w, obj.name, hashed, sha, gates.get(obj.name))
            out.append(
                {
                    "kind": "skill",
                    "name": obj.name,
                    "staged_name": st.staged_name,
                    "version_id": rec.version_id,
                    "created": not exists,
                }
            )
            return out
        sp = build_staged_plugin(obj.src, Path(tmp) / "plugin", rename_map)
        exists = plugin_exists(client, sp.staged_name)
        upload_plugin_dir(client, sp.staged_name, sp.out_dir, exists=exists)
        for live_skill, staged_skill in sorted(sp.skills.items()):
            hashed = hash_skill_upload(sp.out_dir / "skills" / staged_skill)
            bad = verify_skill(client, staged_skill, hashed)
            if bad:
                raise PlatformError(f"{staged_skill}: platform differs after upload: {bad}")
            rec = _record_skill_push(w, live_skill, hashed, sha, gates.get(live_skill))
            out.append(
                {
                    "kind": "plugin-skill",
                    "name": live_skill,
                    "plugin": obj.name,
                    "staged_name": staged_skill,
                    "version_id": rec.version_id,
                    "created": not exists,
                }
            )
    return out


def _staged_hashes_of(obj: Obj, rename_map: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """``{live skill name: upload hashes}`` of the staged copy of ``obj`` as built now."""
    with tempfile.TemporaryDirectory() as tmp:
        if obj.kind == "skill":
            st = build_staged_skill(obj.src, Path(tmp) / "s", rename_map)
            return {obj.name: hash_skill_upload(st.out_dir)}
        sp = build_staged_plugin(obj.src, Path(tmp) / "p", rename_map)
        return {
            live: hash_skill_upload(sp.out_dir / "skills" / staged)
            for live, staged in sp.skills.items()
        }


def bundle_spec(b: ab.AgentBundle) -> dict[str, Any]:
    """The fields a process hash covers, from a bundle (same shape as the platform's)."""
    return {
        "display_name": b.display_name,
        "description": b.description or None,
        "components": b.component_dicts(),
        "agent_instructions": b.instructions,
        "is_enabled": b.is_enabled,
        "compiled_flow": b.flow,
    }


# -- push --staged ----------------------------------------------------------------------------


def _cmd_push(ctx: Context) -> Result:
    a = ctx.args
    if not a.staged:
        raise RefusedError(
            "abcd push only writes the staged shadows; live objects change only through "
            "`abcd release`. Use --staged."
        )
    w = open_work(ctx)
    opts = _gate_opts(ctx)
    t = resolve_targets(w, a.names)
    sha = rp.current_sha(w.ws) if (w.ws / ".git").exists() else None
    warnings: list[str] = []
    if sha and rp.status(w.ws).dirty:
        warnings.append("uncommitted changes: `abcd stage` needs a clean, pushed commit")
    # 1. Everything local first (audit, secret scan, browser gate): a refusal changes nothing.
    checks: dict[str, Any] = {}
    gates: dict[str, dict[str, Any]] = {}
    for obj in t.objects:
        res = check_object(w.client, obj, opts, w.who, strict=False)
        checks[obj.name] = res["audit"]
        gates.update(res["gate"])
    for bundle, _objs, missing in t.agents:
        found = check_bundle_secrets(bws.agent_dir(w.ws, bundle.name))
        if found:
            raise RefusedError(f"{bundle.name}: secret scan failed: {'; '.join(found[:5])}")
        if missing:
            warnings.append(f"{bundle.name}: no source in the repo for {missing}")
    for bundle, _objs, _m in t.agents:
        check_lock(w, bundle.name, take=a.take)
    all_objs = list(t.objects)
    rename_map = rename_map_for(all_objs)
    plan: dict[str, Any] = {
        "objects": [
            {"kind": o.kind, "name": o.name, "staged_name": staged_name(o.name)} for o in all_objs
        ],
        "agents": [
            {"agent": b.name, "staged_name": staged_agent_name(b.name)} for b, _o, _m in t.agents
        ],
    }
    if ctx.dry_run:
        for bundle, objs, _m in t.agents:
            staged = staged_agent_bundle(w, bundle, objs)
            plan.setdefault("assigned", {})[bundle.name] = list(staged.workstreams)
        ctx.dry_run_notice(
            f"would push {len(all_objs)} staged object(s) and {len(t.agents)} staged agent(s)"
        )
        return Result.done({**plan, "audit": checks, "warnings": warnings, "dry_run": True}, "")
    # 2. Upload and record.
    pushed: list[dict[str, Any]] = []
    versions: dict[str, str] = {}
    for obj in all_objs:
        for item in push_object_staged(w, obj, rename_map, sha, gates):
            pushed.append(item)
            versions[item["name"]] = item["version_id"]
    agents_out: list[dict[str, Any]] = []
    for bundle, objs, _m in t.agents:
        mine = {k: v for o in objs for k in o.inner_skills() for v in [versions.get(k)] if v}
        agents_out.append(
            push_agent_staged(w, bundle, objs, mine, git_sha=sha, take=a.take, dry_run=False)
        )
    lines = [f"Pushed {len(pushed)} staged object(s)"]
    lines += [f"  {p['staged_name']} -> {p['version_id']}" for p in pushed]
    lines += [f"Pushed {x['staged_name']} as {x['version_id']}" for x in agents_out]
    lines += [f"WARNING: {x}" for x in warnings]
    return Result.done(
        {"objects": pushed, "agents": agents_out, "audit": checks, "warnings": warnings},
        "\n".join(lines),
    )


# -- stage ------------------------------------------------------------------------------------


@dataclass
class StagedView:
    """The records a staged agent stands on, checked against the source and the platform."""

    agent_rec: VersionRecord
    skill_recs: dict[str, VersionRecord]


def _unreleased(w: Work, kind: str, name: str) -> VersionRecord | None:
    rec = w.ledger.latest(kind, name)
    return rec if rec and rec.state in ("draft", "staged") else None


def _newer_push(agent: str, name: str) -> RefusedError:
    return RefusedError(
        f"a newer staged push exists; run `abcd stage {agent}` again "
        f"({name} has a draft newer than the staged record; release deploys exactly what "
        "`stage` froze)"
    )


def _frozen(w: Work, agent: str, kind: str, name: str) -> VersionRecord | None:
    """The record ``abcd stage`` froze: the latest record of ``name`` in state ``staged``.
    Refuses when a draft (a later ``push --staged``) is newer than it."""
    rec = w.ledger.latest(kind, name)
    if rec is not None and rec.state == "draft":
        raise _newer_push(agent, name)
    return rec if rec is not None and rec.state == "staged" else None


def staged_view(
    w: Work,
    bundle: ab.AgentBundle,
    objs: Sequence[Obj],
    *,
    frozen: VersionRecord | None = None,
) -> StagedView:
    """Refuse unless the latest staged push still equals the source in the working tree.

    With ``frozen`` (the staged agent record, for ``release``) the view is the staged record of
    the agent and of every skill, each checked against the current source's hashes; a draft
    newer than a staged record, or a record that is not the frozen one, is refused.
    """
    rename_map = rename_map_for(objs)
    recs: dict[str, VersionRecord] = {}
    for obj in objs:
        for live_name, hashed in _staged_hashes_of(obj, rename_map).items():
            rec = (
                _frozen(w, bundle.name, "skill", live_name)
                if frozen
                else _unreleased(w, "skill", live_name)
            )
            if rec is None or rec.content_hash != hashed["bundle_sha256"]:
                raise RefusedError(
                    f"{live_name}: the staged copy on the platform is not the current source; "
                    f"run `abcd push {bundle.name} --staged` again"
                )
            recs[live_name] = rec
    arec = (
        _frozen(w, bundle.name, "agent", bundle.name)
        if frozen
        else _unreleased(w, "agent", bundle.name)
    )
    if frozen is not None and (arec is None or arec.version_id != frozen.version_id):
        raise _newer_push(bundle.name, bundle.name)
    staged_b = staged_agent_bundle(w, bundle, objs)
    if arec is None or arec.content_hash != hash_process(bundle_spec(staged_b)):
        raise RefusedError(
            f"{bundle.name}: the staged agent on the platform is not the current bundle; "
            f"run `abcd push {bundle.name} --staged` again"
        )
    return StagedView(arec, recs)


def _check_tree_is_staged(
    w: Work, sha: str, agent: str, objs: Sequence[Obj], *, do_fetch: bool = True
) -> None:
    """What release uploads is the working tree: it must hold, for the agent bundle and every
    skill and plugin directory, exactly the files of the staged commit."""
    if rp.run_git(w.ws, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
        if do_fetch:
            rp.fetch(w.ws)
        if rp.run_git(w.ws, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
            raise RefusedError(f"the staged commit {sha[:12]} is not available; fetch it first")
    paths = [
        p.resolve().relative_to(w.ws.resolve()).as_posix()
        for p in [bws.agent_dir(w.ws, agent), *(o.src for o in objs)]
    ]
    if rp.run_git(w.ws, "diff", "--quiet", sha, "--", *paths, check=False).returncode != 0:
        raise RefusedError(
            f"the files to upload differ from the staged commit {sha[:12]}; a newer staged push "
            f"exists or the tree changed: run `abcd stage {agent}` again"
        )


def shadow_drift(w: Work, view: StagedView) -> list[DriftItem]:
    """The staged shadows on the platform against the recorded staged hashes."""
    items: list[DriftItem] = []
    for name, rec in sorted(view.skill_recs.items()):
        sname = staged_name(name)
        rep = compare_skill(rec, fetch_live_skill(w.client, sname, fetch_aux=True), sname)
        items += rep.items
    staged_id = recorded_process_ids(w, view.agent_rec.object_name)[1]
    proc = None
    if staged_id:
        try:
            proc = w.client.get_process(staged_id)
        except HarnessAPIError as e:
            if e.status_code != 404:
                raise
    items += compare_process(
        view.agent_rec, proc, staged_agent_name(view.agent_rec.object_name)
    ).items
    return [i for i in items if i.severity in ("warning", "error")]


def _require_clean_pushed(w: Work) -> str:
    st = rp.status(w.ws)
    if st.dirty:
        shown = ", ".join((st.changed + st.untracked)[:6])
        raise RefusedError(f"the tree has uncommitted changes ({shown}); commit and push first")
    rp.ensure_pushed(w.ws, st.sha)
    return st.sha


def rc_tag_for(ws: Path, agent: str, number: int, sha: str) -> str:
    """``<agent>@v<n>-rc``, or ``-rc.<k>`` (the next free k) when that tag already names another
    commit, e.g. after a squash merge forced a re-stage. A tag is never moved."""
    base = f"{agent}@v{number}-rc"
    k = 0
    while True:
        tag = base if k == 0 else f"{base}.{k}"
        exists = (
            rp.run_git(
                ws, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False
            ).returncode
            == 0
        )
        if not exists or rp.current_sha(ws, f"refs/tags/{tag}") == rp.current_sha(ws, sha):
            return tag
        k += 1


def staged_rc_tag(rec: VersionRecord) -> str:
    """The rc tag recorded on the staged version row (older rows: the plain ``-rc`` tag)."""
    recorded = (rec.manifest or {}).get("rc_tag")
    return str(recorded) if recorded else f"{rec.object_name}@v{rec.number}-rc"


def _cmd_stage(ctx: Context) -> Result:
    a = ctx.args
    w = open_work(ctx)
    opts = _gate_opts(ctx)
    bundle = load_agent_bundle(w.ws, a.agent)
    objs, missing = objects_for(w.harness, bundle)
    if missing:
        raise RefusedError(f"{a.agent}: no source in the repo for {missing}; add it before staging")
    sha = _require_clean_pushed(w)
    check_lock(w, a.agent, take=False)
    gates: dict[str, Any] = {}
    for obj in objs:
        gates.update(check_object(w.client, obj, opts, w.who, strict=True)["gate"])
    found = check_bundle_secrets(bws.agent_dir(w.ws, a.agent))
    if found:
        raise RefusedError(f"{a.agent}: secret scan failed: {'; '.join(found[:5])}")
    view = staged_view(w, bundle, objs)
    drift = shadow_drift(w, view)
    if drift:
        raise RefusedError(
            "the staged shadows changed since they were pushed: "
            + ", ".join(f"{i.kind} {i.name}" for i in drift)
        )
    tag = rc_tag_for(w.ws, a.agent, view.agent_rec.number, sha)
    data: dict[str, Any] = {
        "agent": a.agent,
        "version_id": view.agent_rec.version_id,
        "tag": tag,
        "git_sha": sha,
        "objects": {n: r.version_id for n, r in sorted(view.skill_recs.items())},
    }
    if ctx.dry_run_notice(f"would record staged hashes and tag {tag} at {sha[:12]}"):
        return Result.done({**data, "dry_run": True}, "")
    w.lock.acquire(a.agent, w.who, sha)
    rp.create_tag(
        w.ws, tag, sha, message=f"{a.agent} staged candidate", identity=git_identity(w.ws)
    )
    rp.push_tag(w.ws, tag)
    for rec in [*view.skill_recs.values(), view.agent_rec]:
        fields: dict[str, Any] = {"git_sha": sha}
        if rec is view.agent_rec:
            fields["manifest"] = {**(rec.manifest or {}), "rc_tag": tag}
        w.svc.rows.update_row(T_VERSIONS, "version_id", rec.version_id, fields)
        w.ledger.record_stage(rec.version_id)
    update_agent_row(
        w, a.agent, staged_version=view.agent_rec.version_id, phase="staged", owner_user_id=w.who
    )
    return Result.done(data, f"Staged {view.agent_rec.version_id} at {sha[:12]}; tagged {tag}")


# -- release criteria and runs ------------------------------------------------------------------


@dataclass
class Criteria:
    dataset: str
    dataset_version: int | None
    criteria: list[dict[str, Any]]


def load_release(agent_dir: Path) -> Criteria:
    """``release.yaml``: ``dataset: name[@version]`` and ``criteria: [{metric, comparator,
    target}]`` (a ``comparator: no_regression`` entry is the ``no_regression`` metric)."""
    p = agent_dir / "release.yaml"
    if not p.is_file():
        raise RefusedError(f"{agent_dir.name}: no release.yaml; set criteria before releasing")
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise UsageError(f"{p}: not valid YAML ({e})") from e
    if not isinstance(doc, dict):
        raise UsageError(f"{p}: must be a mapping")
    ds, _, ver = str(doc.get("dataset") or "").partition("@")
    raw = doc.get("criteria") or []
    if not ds or not raw:
        raise RefusedError(
            f"{agent_dir.name}: release.yaml needs a dataset and at least one criterion"
        )
    version = int(ver) if ver.strip().isdigit() else None
    out: list[dict[str, Any]] = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or not c.get("metric"):
            raise UsageError(f"{p}: criterion {i + 1} needs a metric")
        crit = dict(c)
        if crit.get("comparator") == NO_REGRESSION or crit["metric"] == NO_REGRESSION:
            crit["metric"], crit["comparator"] = NO_REGRESSION, "=="
        crit.setdefault("criteria_key", f"{agent_dir.name}:{crit['metric']}")
        if version is not None:
            crit.setdefault("dataset_version", version)
        out.append(crit)
    return Criteria(ds, version, out)


def _run_states(ws: Path) -> list[dict[str, Any]]:
    root = ws / ".builder" / "evals"
    states = []
    for p in sorted(root.glob("*/state.json")) if root.is_dir() else []:
        try:
            states.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(states, key=lambda s: str(s.get("created_at", "")))


def latest_run(
    ws: Path, agent: str, *, tier: str, dataset: str, version: str | None = None
) -> dict[str, Any] | None:
    hits = [
        s
        for s in _run_states(ws)
        if s.get("agent") == agent
        and s.get("tier") == tier
        and s.get("dataset") == dataset
        and s.get("status") == "complete"
        and (version is None or s.get("version") == version)
        and (tier != "staged" or s.get("blind"))
    ]
    return hits[-1] if hits else None


@dataclass
class GateOutcome:
    agent: str
    run_id: str
    decision: GateDecision | None
    results: RunResults
    live: RunResults | None
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision is not None and self.decision.passed and not self.errors

    def reasons(self) -> list[str]:
        if self.errors:
            return list(self.errors)
        return [
            f"{r.criteria_key}: {r.detail}"
            for r in (self.decision.failed() if self.decision else [])
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "run_id": self.run_id,
            "passed": self.passed,
            "reasons": self.reasons(),
            "decision": self.decision.to_dict() if self.decision else None,
        }


def evaluate_agent_gate(
    w: Work, agent: str, version_id: str, accepted: Mapping[str, str] | None = None
) -> GateOutcome:
    """The gate for one agent against its latest blind staged run on ``version_id``."""
    crit = load_release(bws.agent_dir(w.ws, agent))
    state = latest_run(w.ws, agent, tier="staged", dataset=crit.dataset, version=version_id)
    if state is None:
        raise RefusedError(
            f"{agent}: no completed blind staged run of dataset {crit.dataset!r} on {version_id}; "
            f"run `abcd test-staged {agent}`"
        )
    open_count = open_gaps(gaps.list_gaps(w.ws, agent=agent))
    results = results_from_state(state, open_count)
    live_state = latest_run(w.ws, agent, tier="production", dataset=crit.dataset)
    live = results_from_state(live_state, open_count) if live_state else None
    try:
        decision = evaluate_criteria(
            crit.criteria, results, live, accepted_regressions=dict(accepted or {})
        )
        return GateOutcome(agent, state["run_id"], decision, results, live)
    except GateRefusedError as e:
        return GateOutcome(agent, state["run_id"], None, results, live, list(e.reasons))


# -- test-staged ------------------------------------------------------------------------------


def _probe_process(w: Work, ds: Any) -> str | None:
    probe = ds.manifest.get("probe")
    if not probe:
        return None
    live_id, _ = recorded_process_ids(w, str(probe))
    found = live_id or (w.client.find_process_by_name(str(probe)) or {}).get("id")
    return str(found) if found else None


def _cmd_test_staged(ctx: Context) -> Result:
    a = ctx.args
    w = open_work(ctx)
    if w.svc.driver is None:
        raise UsageError("no harness driver is configured for this workspace")
    bundle = load_agent_bundle(w.ws, a.agent)
    crit_dataset = a.dataset
    if not crit_dataset:
        crit_dataset = load_release(bws.agent_dir(w.ws, a.agent)).dataset
    rec = _unreleased(w, "agent", bundle.name)
    if rec is None:
        raise RefusedError(f"{a.agent} has no staged version; run `abcd push {a.agent} --staged`")
    _live, staged_id = recorded_process_ids(w, a.agent)
    pid = staged_id or (w.client.find_process_by_name(staged_agent_name(a.agent)) or {}).get("id")
    if not pid:
        raise RefusedError(f"{a.agent}-staged is not on the platform")
    rows = OrgRowStore(w.svc.rows)
    ds = load_dataset(w.ws, crit_dataset)
    if ctx.dry_run_notice(
        f"would run the blind eval of {rec.version_id} on dataset {crit_dataset} "
        f"({len(ds.active_cases())} case(s))"
    ):
        return Result.done({"agent": a.agent, "dataset": crit_dataset, "dry_run": True}, "")
    common: dict[str, Any] = {"driver": w.svc.driver, "store": w.svc.docs, "rows": rows}
    if a.resume:
        state = load_state(w.ws, a.resume)
        if state["agent"] != a.agent:
            raise UsageError(f"run {a.resume} is for {state['agent']!r}, not {a.agent!r}")
        import time

        waited = 0.0
        while state["status"] == "running":
            state = advance(w.ws, a.resume, w.svc.driver, w.svc.docs, rows)
            if state["status"] != "running":
                break
            if waited >= a.timeout:
                raise PlatformError(
                    f"run {a.resume} still running after {a.timeout}s; resume again"
                )
            time.sleep(a.poll_interval)
            waited += a.poll_interval
        results = collect(w.ws, a.resume, w.svc.docs, rows, open_gap_count=None)
    else:
        results = run_eval(
            w.ws,
            poll_interval=a.poll_interval,
            timeout=a.timeout,
            dataset=crit_dataset,
            agent=a.agent,
            process_id=str(pid),
            blind=True,
            case_ids=a.case or None,
            tier="staged",
            where="local",
            version=rec.version_id,
            probe_process_id=_probe_process(w, ds),
            **common,
        )
    summary = {
        "run_id": results.run_id,
        "agent": a.agent,
        "version": rec.version_id,
        "dataset": results.dataset,
        "dataset_version": results.dataset_version,
        "cases": len(results.cases),
        "passed": sum(1 for c in results.cases if c.passed is True),
        "pass_rate": pass_rate(results.cases),
    }
    try:
        outcome = evaluate_agent_gate(w, a.agent, rec.version_id)
        summary["gate"] = outcome.to_dict()
    except RefusedError as e:
        summary["gate"] = {"passed": False, "reasons": [str(e)]}
    text = (
        f"Run {results.run_id}: {summary['passed']}/{summary['cases']} case(s) passed "
        f"(gate: {'pass' if summary['gate'].get('passed') else 'not yet'})"
    )
    return Result.done(summary, text)


# -- deploying under the live names -------------------------------------------------------------


@dataclass
class Deployed:
    skill_hashes: dict[str, dict[str, Any]]
    process_id: str
    process_spec: dict[str, Any]
    created_process: bool
    gate: dict[str, dict[str, Any]]


def deploy_live(
    w: Work,
    bundle: ab.AgentBundle,
    objs: Sequence[Obj],
    opts: GateOptions,
) -> Deployed:
    """Upload ``objs`` under their live names from their source dirs, patch the live process
    (workstreams and write grants too), and read everything back. Raises if anything differs.

    Every gate, audit and secret scan runs for all objects before the first upload, so a refusal
    leaves the live names untouched.
    """
    client = w.client
    gates: dict[str, dict[str, Any]] = {}
    for obj in objs:
        for d in skill_dirs_of(obj):
            live_upload_dir(d)
        res = check_object(client, obj, opts, w.who, strict=False)
        gates.update(res["gate"])
    org_names = org_skill_names(client)
    hashes: dict[str, dict[str, Any]] = {}
    try:
        for obj in objs:
            if obj.kind == "skill":
                # A name a lead decided may override a default skill needs replace on its first
                # upload too (without it the platform refuses the collision).
                exists = obj.name in org_names or has_decision(w, f"collision:{obj.name}")
                hashed = upload_skill_dir(client, obj.name, obj.src, exists=exists)
                hashes[obj.name] = hashed
            else:
                upload_plugin_dir(client, obj.name, obj.src, exists=plugin_exists(client, obj.name))
                for d in skill_dirs_of(obj):
                    hashes[d.name] = hash_skill_upload(d)
        for name, hashed in hashes.items():
            bad = verify_skill(client, name, hashed)
            if bad:
                raise PlatformError(f"{name}: live object differs after upload: {bad}")
        live_id = recorded_process_ids(w, bundle.name)[0]
        sync = sync_process(client, bundle, w.name_to_id, process_id=live_id)
        bad = verify_process(client, bundle, w.name_to_id, sync.process_id)
        if bad:
            raise PlatformError(f"{bundle.name}: live process differs after the patch in {bad}")
    except (PlatformError, HarnessAPIError) as e:
        raise PlatformError(
            f"{bundle.name}: the live update stopped part way ({e}); nothing was recorded as "
            "released. Re-run it, or `abcd rollback` to the previous version."
        ) from e
    return Deployed(
        hashes, sync.process_id, client.get_process(sync.process_id), sync.created, gates
    )


def promote_skill(
    w: Work,
    name: str,
    hashed: Mapping[str, Any],
    sha: str | None,
    rec: VersionRecord | None,
) -> VersionRecord:
    """The staged record ``rec`` of ``name`` (one that belongs to the staged version) becomes
    live, carrying the hashes of what is now live. Only a ``staged`` record is promoted."""
    if rec is not None and rec.state != "staged":
        raise RefusedError(f"{rec.version_id} is {rec.state}; only the staged record is promoted")
    if rec is None:
        new = skill_record(
            name,
            hashed,
            version_id=w.ledger.next_version_id("skill", name),
            git_sha=sha,
            author=w.who,
        )
        return w.ledger.record_live(new)
    w.svc.rows.update_row(
        T_VERSIONS,
        "version_id",
        rec.version_id,
        {
            "content_hash": hashed["bundle_sha256"],
            "skill_md_sha256": hashed["skill_md_sha256"],
            "manifest": {"aux": [dict(x) for x in hashed["aux"]], "staged": rec.manifest},
            "git_sha": sha,
        },
    )
    return w.ledger.record_release(rec.version_id)


def promote_agent(
    w: Work, rec: VersionRecord, spec: Mapping[str, Any], sha: str | None
) -> VersionRecord:
    if rec.state != "staged":
        raise RefusedError(f"{rec.version_id} is {rec.state}; only the staged record is promoted")
    w.svc.rows.update_row(
        T_VERSIONS,
        "version_id",
        rec.version_id,
        {
            "content_hash": hash_process(spec),
            "manifest": {"fields": process_field_hashes(spec), "staged": rec.manifest},
            "git_sha": sha,
        },
    )
    return w.ledger.record_release(rec.version_id)


# -- release ----------------------------------------------------------------------------------


def has_decision(w: Work, key: str) -> bool:
    rows = w.svc.rows.find_rows(T_DECISIONS, "decision_key", key)
    return any(r.get("status") == "confirmed" for r in rows)


def default_collisions(w: Work, objs: Sequence[Obj]) -> list[str]:
    defaults = set(w.client.default_skill_names())
    names = sorted({s for o in objs for s in o.inner_skills()})
    out = []
    for n in names:
        hit = n in defaults
        if not hit:
            try:
                hit = probe_default_skill(w.client, n) is not None
            except PlatformError:
                hit = False
        if hit:
            out.append(n)
    return out


def changed_names(w: Work, objs: Sequence[Obj]) -> set[str]:
    """Component refs (skill names, plugin slugs) whose source differs from what is live."""
    changed: set[str] = set()
    for o in objs:
        for d in skill_dirs_of(o):
            live = w.ledger.latest("skill", d.name, "live")
            if live is None or live.content_hash != hash_skill_upload(d)["bundle_sha256"]:
                changed.update({d.name, o.name})
    return changed


def dependents(w: Work, agent: str, changed: set[str]) -> list[str]:
    """Other live agents that use a changed skill or plugin (their criteria gate this release)."""
    out = []
    for other in bws.list_agents(w.ws):
        if other == agent or not (bws.agent_dir(w.ws, other) / "process.yaml").is_file():
            continue
        try:
            b = ab.load_bundle(bws.agent_dir(w.ws, other))
        except UsageError:
            continue
        uses = {r for _t, s, r in b.components if s == "org"}
        if uses & changed and w.ledger.latest("agent", other, "live") is not None:
            out.append(other)
    return sorted(out)


def _check_pins(w: Work, other: str, rec: VersionRecord, changed: set[str]) -> None:
    for comp in (rec.pinned_components or {}).get("components", []):
        ref = comp.get("ref")
        if ref in changed and comp.get("version_id"):
            cur = _unreleased(w, "skill", ref)
            if cur is not None and cur.version_id != comp["version_id"]:
                raise RefusedError(
                    f"{other}: its last staged run used {ref} {comp['version_id']}, but "
                    f"{cur.version_id} is staged now; re-run `abcd test-staged {other}`"
                )


class ReleaseBranch:
    """``release/<agent>@v<n>`` built in a separate git worktree, so the working tree (and the
    default branch) are never touched. It is cut from ``origin/<default_branch>`` after a fetch,
    never from the working HEAD: the pull request holds only the release artifacts, not whatever
    else the FDE's branch carries."""

    def __init__(
        self, ws: Path, branch: str, default_branch: str, *, do_fetch: bool = True
    ) -> None:
        if branch == default_branch:
            raise RefusedError("refusing to build a release on the default branch")
        self.ws, self.branch, self.default_branch = ws, branch, default_branch
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / ws.name  # ADOPT.md names the env after the folder
        self._identity = git_identity(ws)
        if (
            rp.run_git(
                ws, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False
            ).returncode
            == 0
        ):
            raise RefusedError(
                f"branch {branch} already exists; delete it or release a new version"
            )
        if do_fetch:
            rp.fetch(ws)
        base = f"refs/remotes/origin/{default_branch}"
        if rp.run_git(ws, "rev-parse", "-q", "--verify", base, check=False).returncode != 0:
            raise RefusedError(
                f"origin/{default_branch} does not exist; cannot cut {branch} from the default branch"
            )
        rp.run_git(ws, "worktree", "add", "-q", "-b", branch, str(self.path), base)

    def write(self, rel: str, data: bytes | str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)

    def commit(self, message: str) -> str | None:
        rp.run_git(self.path, "add", "-A")
        if not rp.run_git(self.path, "status", "--porcelain").stdout.strip():
            return None
        return rp.commit(self.path, message, identity=self._identity)

    def push(self) -> None:
        rp.push(self.path, self.branch, default_branch=self.default_branch)

    def close(self) -> None:
        rp.run_git(self.ws, "worktree", "remove", "--force", str(self.path), check=False)
        self._tmp.cleanup()


def _status_store_path(cfg: Mapping[str, Any], agent: str, version: str) -> str | None:
    for name in sorted(cfg.get("stores", {})):
        if name.startswith("builder-status-"):
            return f"{name}/agents/{agent}/releases/{version}/eval_report.html"
    return None


def _changes(w: Work, prev_tag: str | None, sha: str) -> list[dict[str, str]]:
    rng = f"{prev_tag}..{sha}" if prev_tag and rp.list_tags(w.ws, prev_tag) else sha
    args = ["log", "--format=%h %s", *(["-n", "30"] if rng == sha else []), rng]
    out = rp.run_git(w.ws, *args, check=False).stdout.strip().splitlines()
    return [
        {"summary": line.partition(" ")[2], "reason": f"commit {line.partition(' ')[0]}"}
        for line in out
    ]


def _explain_info(
    w: Work, agent_dir: Path, version_id: str, outcome: GateOutcome, dataset: str
) -> explain.AgentInfo:
    hist = w.ledger.history("agent", Path(agent_dir).name)
    versions = tuple(
        explain.VersionRow(
            r.version_id, note=r.git_sha or "", live=r.state == "live", staged=r.state == "staged"
        )
        for r in hist
    )
    crit = tuple(
        explain.CriterionRow(
            r.criteria_key,
            target=f"{r.comparator} {r.target}",
            current="" if r.actual is None else f"{r.actual:.4g}",
            met=r.passed,
        )
        for r in (outcome.decision.results if outcome.decision else [])
    )
    n = len(outcome.results.cases)
    ok = sum(1 for c in outcome.results.cases if c.passed is True)
    rate = pass_rate(outcome.results.cases)
    return explain.info_from_bundle(
        agent_dir,
        versions=versions,
        dataset=explain.DatasetInfo(dataset, n, ()),
        criteria=crit,
        last_eval=explain.EvalInfo(
            date=_now()[:10],
            score="" if rate is None else f"{rate:.0%}",
            passed=ok,
            failed=n - ok,
            summary=f"{version_id} blind staged run {outcome.run_id}",
        ),
    )


def _gate_card(ctx: Context) -> Result:
    """``abcd release gate-card <agent> --action release|rollback``: the card the runner opens."""
    a = ctx.args
    if not a.target:
        raise UsageError("abcd release gate-card <agent> --action release|rollback")
    w = open_work(ctx)
    if a.action == "rollback":
        _live, target = _rollback_target(w, a.target)
        release, sha = target.version_id, target.git_sha or ""
    else:
        staged = w.ledger.latest("agent", a.target, "staged")
        if staged is None:
            raise RefusedError(f"{a.target} is not staged; run `abcd stage {a.target}` first")
        release, sha = staged.version_id, staged.git_sha or ""
    if not sha:
        raise RefusedError(f"{release} records no git sha; it cannot be approved")
    spec = gate_card.card_spec(release, sha, a.action)
    lines = [
        f"Open the gate card on step {spec['step_id']!r} ({spec['title']}), submit label "
        f"{spec['submit_label']!r}. Fields (read-only fields carry these values):"
    ]
    for f in spec["block"]["fields"]:
        v = f.get("value")
        opts = f.get("options")
        lines.append(
            f"  {f['name']} ({f['field_type']}{', required' if f['required'] else ''})"
            + (f" = {v}" if v is not None else "")
            + (f" options {opts}" if opts else "")
        )
    lines.append(f"  context: {spec['block']['context']}")
    lines.append(
        f"Then wait for the FDE; run `abcd {a.action if a.action == 'rollback' else 'release'} "
        f"{a.target} --yes` only after they submit decision=approve."
    )
    return Result.done({"agent": a.target, "action": a.action, **spec}, "\n".join(lines))


def _cmd_release(ctx: Context) -> Result:
    if ctx.args.agent == "gate-card":
        return _gate_card(ctx)
    if ctx.args.redeploy:
        return _redeploy(ctx)
    a = ctx.args
    if not a.agent:
        raise UsageError("name the agent to release (or use --redeploy <agent>@v<n>)")
    refuse_self_asserted_in_cloud(
        ctx.env_dir,
        force_reason=a.force_reason,
        lead=a.lead,
        accept_regression=a.accept_regression,
        local_approval=a.local_approval,
    )
    if bool(a.force_reason) != bool(a.lead):
        raise UsageError("a forced release needs both --force-reason and --lead")
    dry = ctx.dry_run
    w = open_work(ctx)
    opts = _gate_opts(ctx)
    log: list[str] = []

    def step(n: int, text: str) -> None:
        log.append(f"{n}. {text}")
        ctx.log(f"[{n}/9] {text}")

    bundle = load_agent_bundle(w.ws, a.agent)
    objs, missing = objects_for(w.harness, bundle)
    if missing:
        raise RefusedError(f"{a.agent}: no source in the repo for {missing}")
    staged_rec = w.ledger.latest("agent", a.agent, "staged")
    if staged_rec is None:
        raise RefusedError(f"{a.agent} is not staged; run `abcd stage {a.agent}` first")
    version_id, sha = staged_rec.version_id, staged_rec.git_sha or ""
    st = rp.status(w.ws)
    if st.dirty:
        raise RefusedError("the tree has uncommitted changes; release uploads the workspace source")
    rp.fetch(w.ws)  # the one fetch of this release; every later git read uses what it brought

    # 1. Shadows still match the staged hashes; live objects have not drifted.
    step(1, "re-check the staged shadows and the live objects for drift")
    view = staged_view(w, bundle, objs, frozen=staged_rec)
    _check_tree_is_staged(w, sha, a.agent, objs, do_fetch=False)
    records = [view.agent_rec, *view.skill_recs.values()]
    live_recs = [
        r
        for r in (
            *(w.ledger.latest("skill", n, "live") for n in view.skill_recs),
            w.ledger.latest("agent", a.agent, "live"),
        )
        if r is not None
    ]
    skills: dict[str, Any] = {}
    procs: dict[str, Mapping[str, Any] | None] = {}
    for r in [*records, *live_recs]:
        if r.object_kind == "skill":
            pname = staged_name(r.object_name) if r.state == "staged" else r.object_name
            skills[pname] = fetch_live_skill(w.client, pname, fetch_aux=True)
    by_name = {str(p.get("name")): p for p in list_processes(w.client)}
    for r in [*records, *live_recs]:
        if r.object_kind == "agent":
            pname = staged_agent_name(r.object_name) if r.state == "staged" else r.object_name
            procs[pname] = by_name.get(pname)
    drift = build_drift_report([*records, *live_recs], skills, procs)
    hard = [i for i in drift.items if i.severity in ("warning", "error")]
    if hard:
        raise RefusedError(
            "drift: " + "; ".join(f"{i.kind} {i.name}" for i in hard) + " (resolve it, then retry)"
        )

    # 2. Default branch and default-tier name collisions.
    step(2, "check the default branch and default-tier name collisions")
    if not rp.is_on_default_branch(w.ws, sha, do_fetch=False) and not has_decision(
        w, f"release-branch:{version_id}"
    ):
        raise RefusedError(
            f"{version_id}: commit {sha[:12]} is not on the default branch "
            f"({w.cfg['default_branch']}); merge the pull request first, or a lead records a "
            f"decision 'release-branch:{version_id}'"
        )
    rc = staged_rc_tag(staged_rec)
    rc_at = rp.run_git(
        w.ws, "rev-parse", "-q", "--verify", f"refs/tags/{rc}^{{commit}}", check=False
    )
    if rc_at.returncode != 0 or rc_at.stdout.strip() != rp.current_sha(w.ws, sha):
        raise RefusedError(
            f"{version_id}: the staged tag {rc} is missing or not at the staged commit "
            f"{sha[:12]}; run `abcd stage {a.agent}` again"
        )
    clashes = [n for n in default_collisions(w, objs) if not has_decision(w, f"collision:{n}")]
    if clashes:
        raise RefusedError(
            f"skill name(s) {clashes} collide with default-tier skills, which an org skill "
            "hides entirely; a lead must record a confirmed decision 'collision:<name>' first"
        )

    # 3. The gate: this agent, and every live agent sharing a changed skill.
    step(3, "check the release criteria against the staged blind run")
    try:
        accepted = dict(x.split("=", 1) for x in a.accept_regression or [])
    except ValueError as e:
        raise UsageError("--accept-regression takes CASE=REASON") from e
    changed = changed_names(w, objs)
    outcomes = [evaluate_agent_gate(w, a.agent, version_id, accepted)]
    for other in dependents(w, a.agent, changed):
        orec = _frozen(w, other, "agent", other)
        if orec is None:
            raise RefusedError(
                f"{other} shares a changed skill and has no staged version; stage and test it, "
                "or release this change from a branch that leaves the skill alone"
            )
        _check_pins(w, other, orec, changed)
        outcomes.append(evaluate_agent_gate(w, other, orec.version_id, accepted))
    failed = [o for o in outcomes if not o.passed]
    forced = bool(failed)
    if failed and not (a.force_reason and a.lead):
        msg = "gate failed: " + " | ".join(f"{o.agent}: {'; '.join(o.reasons())}" for o in failed)
        return Result.refused(msg, {"gate": [o.to_dict() for o in outcomes]})
    primary = outcomes[0]

    # 4. Approval.
    step(4, "approval (--yes)")
    if not ctx.yes and not dry:
        raise RefusedError("release needs --yes (it never prompts)")
    approval_mode = ""
    if not dry:  # cloud: the human's own message or submitted gate card
        approval_mode = require_release_approval(w.client, version_id, sha, w.ws)

    prev_live = w.ledger.latest("agent", a.agent, "live")
    previous_id = prev_live.version_id if prev_live else None
    n = staged_rec.number
    version = f"v{n}"
    changes = _changes(w, previous_id, sha)
    plan = {
        "agent": a.agent,
        "version_id": version_id,
        "previous_version_id": previous_id,
        "gate": [o.to_dict() for o in outcomes],
        "forced": forced,
        "branch": f"release/{version_id}",
        "tag": version_id,
    }
    if dry:
        for n_, text in (
            (
                5,
                "upload each bundle under its live name (replace for names already live), "
                "patch the live process, workstreams and write grants, verify",
            ),
            (
                6,
                f"write the eval report to builder-ops releases/{a.agent}/{version}/, the status "
                "store, and release branch",
            ),
            (7, f"insert the bld_releases row (rollback target {previous_id})"),
            (8, f"tag {version_id} and create the GitHub release"),
            (
                9,
                "regenerate the explainer and training kit, refresh ADOPT.md, open the pull request",
            ),
        ):
            step(n_, f"would {text}")
        return Result.done({**plan, "steps": log, "dry_run": True}, "DRY RUN\n" + "\n".join(log))

    # 5. Upload live, patch, verify.
    step(5, "upload live, patch the live process, verify")
    dep = deploy_live(w, bundle, objs, opts)
    try:
        for name, hashed in dep.skill_hashes.items():
            promote_skill(w, name, hashed, sha, view.skill_recs.get(name))
        promote_agent(w, staged_rec, dep.process_spec, sha)
    except Exception as e:
        raise PlatformError(
            f"{version_id} is live but its version records were not all updated ({e}); "
            "run `abcd audit drift` and re-run the release"
        ) from e
    holder = w.lock.info(a.agent).holder
    if holder:
        w.lock.release(a.agent, holder, force=True)
    update_agent_row(
        w, a.agent, process_id=dep.process_id, live_version=version_id, phase="live",
        staged_lock_holder=None, staged_lock_sha=None, staged_lock_since=None,
    )  # fmt: skip

    flags: list[str] = []
    failures: list[str] = []
    branch: ReleaseBranch | None = None
    try:
        # 6. Evidence: report to builder-ops, the status store, and the release branch.
        step(6, "write the eval report")
        try:
            report = build_report(
                primary.results,
                decision=primary.decision,
                live=primary.live,
                changes=changes,
                accepted_regressions=accepted,
                forced_reason=a.force_reason if forced else None,
            )
            report["header"]["version"] = version
            html = render_html(report)
            base = f"builder-ops/releases/{a.agent}/{version}"
            manifest = {
                "agent": a.agent,
                "version_id": version_id,
                "git_sha": sha,
                "previous_version_id": previous_id,
                "eval_run_id": primary.run_id,
                "gate": [o.to_dict() for o in outcomes],
                "forced": forced,
                "forced_reason": a.force_reason if forced else None,
                "forced_by": a.lead if forced else None,
                "released_by": w.who,
                "released_at": _now(),
                "pinned_components": staged_rec.pinned_components,
                "rc_tag": rc,
                "objects": {k: v["bundle_sha256"] for k, v in dep.skill_hashes.items()},
                "local_approvals": {k: v for k, v in dep.gate.items() if v.get("mode") == "local"},
                "approval_mode": approval_mode or None,
            }
            w.svc.docs.put(
                f"{base}/eval_report.json", json.dumps(report, indent=2, sort_keys=True).encode()
            )
            w.svc.docs.put(f"{base}/eval_report.html", html.encode())
            w.svc.docs.put(
                f"{base}/manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True, default=str).encode(),
            )
            status_path = _status_store_path(w.cfg, a.agent, version)
            if status_path:
                w.svc.docs.put(status_path, html.encode())
            else:
                flags.append(
                    "no builder-status store in builder.json: report not copied to the client"
                )
            branch = ReleaseBranch(
                w.ws, f"release/{version_id}", w.cfg["default_branch"], do_fetch=False
            )
            branch.write(f"harness/evals/reports/{a.agent}/{version}.html", html)
            branch.commit(f"release {version_id}: eval report")
        except Exception as e:
            failures.append(f"step 6 (report): {e}")
            html = ""
        # 7. bld_releases.
        step(7, "record the release")
        try:
            row = {
                "release_id": version_id,
                "agent_key": a.agent,
                "version_id": version_id,
                "previous_version_id": previous_id,
                "eval_run_id": primary.run_id,
                "gate_result": "forced" if forced else "pass",
                "report_path": f"builder-ops/releases/{a.agent}/{version}/eval_report.html",
                "approved_by": a.lead or w.who,
                "forced_reason": a.force_reason if forced else None,
                "released_at": _now(),
            }
            res = w.svc.rows.insert_rows(
                T_RELEASES, [{k: v for k, v in row.items() if v is not None}]
            )
            if getattr(res, "failed", None):
                raise PlatformError(str(res.failed[0]["error"]))
        except Exception as e:
            failures.append(f"step 7 (bld_releases): {e}")
        # 8. Tag and GitHub release.
        step(8, "tag the staged commit and create the GitHub release")
        try:
            rp.create_tag(
                w.ws, version_id, sha, message=f"{a.agent} {version}", identity=git_identity(w.ws)
            )
            rp.push_tag(w.ws, version_id)
        except Exception as e:
            failures.append(f"step 8 (tag): {e}")
        else:
            # The GitHub release is a convenience on top of the tag: no GitHub remote, no `gh`
            # or no token is a flag, never a failed release (a local bare remote has none).
            try:
                if w.svc.github is None:
                    raise PlatformError("no GitHub backend configured")
                gh = w.svc.github(w.cfg["repo_remote"])
                with tempfile.TemporaryDirectory() as tmp:
                    asset = Path(tmp) / f"eval_report-{a.agent}-{version}.html"
                    asset.write_text(html)
                    body = f"Eval report for {version_id} (run {primary.run_id})."
                    if forced:
                        body += f"\n\nFORCED by {a.lead}: {a.force_reason}"
                    gh.create_release(
                        version_id,
                        name=version_id,
                        body=body,
                        target=sha,
                        assets=[asset] if html else [],
                        prerelease=forced,
                    )
            except Exception as e:
                flags.append(f"step 8: GitHub release not created: {e}")
        # 9. Explainer, training kit, ADOPT.md, branch and pull request.
        step(
            9, "regenerate the explainer and training kit, refresh ADOPT.md, open the pull request"
        )
        pr: dict[str, Any] | None = None
        try:
            if branch is None:
                raise PlatformError("the release branch was not created")
            agent_dir = branch.path / "harness" / "agents" / a.agent
            info = _explain_info(
                w,
                agent_dir,
                version_id,
                primary,
                load_release(bws.agent_dir(w.ws, a.agent)).dataset,
            )
            for label, gen in (
                ("explainer", explain.generate_explainer(info, agent_dir)),
                (
                    "training kit",
                    train.generate_training_kit(
                        info, agent_dir, whats_new=[c["summary"] for c in changes]
                    ),
                ),
            ):
                if not gen.ok:
                    flags.append(f"{label}: {gen.error}")
            bws.set_agent_process_ids(branch.path, a.agent, live_process_id=dep.process_id)
            bws.refresh_adopt_md(branch.path)
            branch.commit(f"release {version_id}: explainer, training kit, ADOPT.md")
            branch.push()
            if w.svc.github is not None:
                title = f"Release {version_id}" + (" (FORCED)" if forced else "")
                text = f"Eval report, explainer and training kit for {version_id}.\nRun {primary.run_id}."
                if forced:
                    text += f"\n\nFORCED by {a.lead}: {a.force_reason}"
                pr = w.svc.github(w.cfg["repo_remote"]).create_pr(
                    head=branch.branch, base=w.cfg["default_branch"], title=title, body=text
                )
        except Exception as e:
            flags.append(f"step 9: {e}")
    finally:
        if branch is not None:
            branch.close()

    data = {
        **plan,
        "process_id": dep.process_id,
        "report_path": f"builder-ops/releases/{a.agent}/{version}/eval_report.html",
        "pr": pr,
        "flags": flags,
        "failures": failures,
        "steps": log,
        "local_approvals": {k: v for k, v in dep.gate.items() if v.get("mode") == "local"},
        "approval_mode": approval_mode or None,
    }
    text = f"Released {version_id} (live process {dep.process_id})"
    if forced:
        text += f"; FORCED by {a.lead}: {a.force_reason}"
    text += "".join(f"\nFLAG: {x}" for x in flags)
    if failures:
        res = Result.platform_error(
            f"{version_id} is live, but the record is incomplete: " + "; ".join(failures)
        )
        res.data = data
        return res
    return Result.done(data, text)


# -- rollback and redeploy --------------------------------------------------------------------


def extract_tag(ws: Path, tag: str, dest: Path, agent: str) -> Path:
    """``harness/{agents/<agent>,skills,plugins}`` as of ``tag`` into ``dest`` (no checkout)."""
    present = [
        p
        for p in (f"harness/agents/{agent}", "harness/skills", "harness/plugins")
        if rp.run_git(ws, "cat-file", "-e", f"{tag}:{p}", check=False).returncode == 0
    ]
    if f"harness/agents/{agent}" not in present:
        raise RefusedError(f"tag {tag} has no harness/agents/{agent}")
    proc = subprocess.run(
        ["git", "archive", "--format=tar", f"refs/tags/{tag}", "--", *present],
        cwd=ws,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RefusedError(f"git archive {tag} failed: {proc.stderr.decode().strip()}")
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            target = (root / m.name).resolve()
            if root not in target.parents:
                raise RefusedError(f"unsafe path {m.name!r} in tag {tag}")
            target.parent.mkdir(parents=True, exist_ok=True)
            f = tar.extractfile(m)
            assert f is not None
            target.write_bytes(f.read())
    return root / "harness"


def _restore_bundles(ws_harness: Path, tag_harness: Path) -> None:
    """Capture bundles are kept out of git; bring back the ones restored in the workspace."""
    for p in (ws_harness / "skills").glob("*/recording_bundle.json"):
        dest = tag_harness / "skills" / p.parent.name
        if dest.is_dir():
            (dest / p.name).write_bytes(p.read_bytes())


def deploy_tag(
    w: Work, agent: str, tag: str, opts: GateOptions
) -> tuple[Deployed, ab.AgentBundle, list[Obj], list[str]]:
    flags: list[str] = []
    if (
        rp.run_git(w.ws, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode
        != 0
    ):
        rp.fetch(w.ws)
    if (
        rp.run_git(w.ws, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode
        != 0
    ):
        raise RefusedError(f"tag {tag} does not exist in the workspace repo")
    with tempfile.TemporaryDirectory() as tmp:
        harness = extract_tag(w.ws, tag, Path(tmp), agent)
        _restore_bundles(w.harness, harness)
        bundle = ab.load_bundle(harness / "agents" / agent)
        objs, missing = objects_for(harness, bundle)
        if missing:
            flags.append(f"no source at {tag} for {missing}; those components are left as they are")
        dep = deploy_live(w, bundle, objs, opts)
        rec = w.ledger.get(tag)
        if rec is not None and rec.content_hash != hash_process(dep.process_spec):
            flags.append(f"{tag}: live process hash differs from the recorded one")
        return dep, bundle, objs, flags


def _record_restore(
    w: Work,
    agent: str,
    target: VersionRecord,
    dep: Deployed,
    kind: str,
    approver: str,
    from_version: str | None,
) -> str:
    for name, hashed in dep.skill_hashes.items():  # the skills are live again as well
        same = [
            r for r in w.ledger.history("skill", name) if r.content_hash == hashed["bundle_sha256"]
        ]
        if same:
            if same[-1].state != "live":
                w.ledger.record_release(same[-1].version_id)
        else:
            w.ledger.record_live(
                skill_record(
                    name,
                    hashed,
                    version_id=w.ledger.next_version_id("skill", name),
                    author=approver,
                )
            )
    w.ledger.record_release(target.version_id)
    rid = f"{target.version_id}@{kind}-{_stamp()}"
    row = {
        "release_id": rid,
        "agent_key": agent,
        "version_id": target.version_id,
        "previous_version_id": from_version,
        "gate_result": "pass",
        "approved_by": approver,
        "released_at": _now(),
    }
    w.svc.rows.insert_rows(T_RELEASES, [{k: v for k, v in row.items() if v is not None}])
    update_agent_row(
        w, agent, process_id=dep.process_id, live_version=target.version_id, phase="live"
    )
    return rid


def _rollback_target(w: Work, agent: str) -> tuple[VersionRecord, VersionRecord]:
    """``(live, target)``: the live version and the one a rollback re-uploads."""
    live = w.ledger.latest("agent", agent, "live")
    if live is None:
        raise RefusedError(f"{agent} has no live version to roll back")
    target_id = None
    for r in w.svc.rows.find_rows(T_RELEASES, "version_id", live.version_id):
        if r.get("previous_version_id") and "@rollback-" not in str(r.get("release_id")):
            target_id = str(r["previous_version_id"])
    if target_id is None:
        older = [
            r
            for r in w.ledger.history("agent", agent)
            if r.state == "superseded" and r.number < live.number
        ]
        target_id = older[-1].version_id if older else None
    target = w.ledger.get(target_id) if target_id else None
    if target is None:
        raise RefusedError(f"{agent}: {live.version_id} has no previous version to roll back to")
    return live, target


def _cmd_rollback(ctx: Context) -> Result:
    a = ctx.args
    w = open_work(ctx)
    live, target = _rollback_target(w, a.agent)
    data: dict[str, Any] = {"agent": a.agent, "from": live.version_id, "to": target.version_id}
    if ctx.dry_run_notice(f"would re-upload {target.version_id} and patch the live process"):
        return Result.done({**data, "dry_run": True}, "")
    if not ctx.yes:
        raise RefusedError("rollback needs --yes (it never prompts)")
    if cloud_mode(w.ws):
        if not target.git_sha:
            raise RefusedError(
                f"{target.version_id} records no git sha; a cloud rollback cannot be approved"
            )
        # the human's own rollback message naming the target version and its commit
        data["approval_mode"] = (
            require_release_approval(w.client, target.version_id, target.git_sha, w.ws, "rollback")
            or None
        )
    dep, _b, _o, flags = deploy_tag(w, a.agent, target.version_id, _gate_opts(ctx))
    rid = _record_restore(w, a.agent, target, dep, "rollback", w.who, live.version_id)
    return Result.done(
        {**data, "release_id": rid, "flags": flags},
        f"Rolled {a.agent} back from {live.version_id} to {target.version_id}"
        + "".join(f"\nFLAG: {x}" for x in flags),
    )


def _redeploy(ctx: Context) -> Result:
    a = ctx.args
    # A redeploy is approved by a named lead (--lead, required): a self-asserted name, so it is
    # refused outright in a cloud run; a cloud rollback needs the human's token instead.
    refuse_self_asserted_in_cloud(ctx.env_dir, lead=a.lead, local_approval=a.local_approval)
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]*)@v(\d+)", a.redeploy)
    if not m:
        raise UsageError("--redeploy takes <agent>@v<n>")
    agent = m.group(1)
    if not a.lead:
        raise UsageError("--redeploy needs --lead NAME (a lead approves it)")
    w = open_work(ctx)
    target = w.ledger.get(a.redeploy)
    if (
        target is None
        or target.object_kind != "agent"
        or target.state not in ("live", "superseded")
    ):
        raise RefusedError(
            f"{a.redeploy} was never released; redeploy only restores a released tag"
        )
    cur = w.ledger.latest("agent", agent, "live")
    data = {"agent": agent, "version_id": target.version_id, "lead": a.lead}
    if ctx.dry_run_notice(f"would re-upload {target.version_id} under the live names"):
        return Result.done({**data, "dry_run": True}, "")
    if not ctx.yes:
        raise RefusedError("redeploy needs --yes (it never prompts)")
    dep, _b, _o, flags = deploy_tag(w, agent, target.version_id, _gate_opts(ctx))
    rid = _record_restore(
        w, agent, target, dep, "redeploy", a.lead, cur.version_id if cur else None
    )
    return Result.done(
        {**data, "release_id": rid, "flags": flags},
        f"Redeployed {target.version_id} (approved by {a.lead})"
        + "".join(f"\nFLAG: {x}" for x in flags),
    )


# -- audit drift ------------------------------------------------------------------------------


def _agent_names(w: Work, agent: str) -> set[str]:
    bundle = load_agent_bundle(w.ws, agent)
    objs, _ = objects_for(w.harness, bundle)
    names = {agent, staged_agent_name(agent)}
    for o in objs:
        for s in o.inner_skills():
            names.update({s, staged_name(s)})
    return names


def _cmd_drift(ctx: Context) -> Result:
    w = open_work(ctx)
    rep = audit_drift(
        w.client,
        w.ledger,
        fetch_aux=True,
        known_default_names=w.client.default_skill_names(),
    )
    if ctx.args.agent:
        names = _agent_names(w, ctx.args.agent)
        keep = [i for i in rep.items if re.split(r"[:.]", i.name, maxsplit=1)[0] in names]
        rep = DriftReport(
            keep,
            [p for p in rep.partial if p.get("name") in names],
            sorted(set(rep.checked) & names),
        )
    data = rep.to_dict()
    if rep.has_drift:
        return Result.refused(f"drift: {len(rep.items)} item(s)", data)
    return Result.done(data, f"No drift in {len(rep.checked)} object(s).")


# -- registration -----------------------------------------------------------------------------


def register(registry: Registry) -> None:
    p = registry.command(
        "push",
        _cmd_push,
        help="Push staged shadows of skills, plugins and agents (--staged is required)",
        writes=True,
    )
    p.add_argument("names", nargs="*", help="Skills, plugins or agents (default: everything)")
    p.add_argument("--staged", action="store_true", help="Push the --staged shadows")
    p.add_argument("--take", action="store_true", help="Take the staged lock from another FDE")
    _gate_args(p)

    p = registry.command(
        "stage",
        _cmd_stage,
        help="Record staged hashes and tag <agent>@v<n>-rc (refuses on any failed check)",
        writes=True,
    )
    p.add_argument("agent")
    _gate_args(p)

    p = registry.command(
        "test-staged", _cmd_test_staged, help="Run the blind eval on the staged agent", writes=True
    )
    p.add_argument("agent")
    p.add_argument("--dataset", help="Dataset (default: the one in release.yaml)")
    p.add_argument("--resume", metavar="RUN_ID", help="Resume a run instead of starting one")
    p.add_argument("--case", action="append", help="Only this case id (repeatable)")
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--model", help="Model id for the turns (default: the org's default model)")

    p = registry.command(
        "release",
        _cmd_release,
        help="Promote a staged agent to live (the only sanctioned path); --yes is required",
        writes=True,
    )
    p.add_argument("agent", nargs="?", help="The agent, or `gate-card` followed by the agent")
    p.add_argument("target", nargs="?", help="With `gate-card`: the agent")
    p.add_argument(
        "--action", choices=list(gate_card.ACTIONS), default="release", help="With gate-card"
    )
    p.add_argument("--redeploy", metavar="AGENT@vN", help="Re-upload a released tag (needs --lead)")
    p.add_argument("--force-reason", help="Force past a failed gate (needs --lead)")
    p.add_argument("--lead", help="The lead who forces or approves a redeploy")
    p.add_argument("--accept-regression", action="append", metavar="CASE=REASON")
    _gate_args(p)

    p = registry.command(
        "rollback",
        _cmd_rollback,
        help="Re-upload the previous release under the live names",
        writes=True,
    )
    p.add_argument("agent")
    _gate_args(p)

    g = registry.group("audit", "Audits")
    p = g.command(
        "drift", _cmd_drift, help="Report live and staged objects that differ from the last record"
    )
    p.add_argument("--agent", help="Only this agent's objects")
