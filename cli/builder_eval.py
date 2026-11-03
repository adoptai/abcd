"""``abcd eval ...``: run, judge, report and gate evals; plus the platform adapters and helpers
shared by the other quality commands (dataset, metric/check/criteria, gap).

Adapters (PLAN.md sections 9 to 11):

* The ``ArtifactStore`` is the shared ``builder_common.docstore`` client (``DocStoreArtifacts``);
  the first path segment names the store, and :func:`dataset_route` re-routes dataset inputs.
* The ``RowStore`` is ``builder_common.rowstore.OrgRowStore`` over the Data Store. The library
  emits value-free rows in the ``bld_*`` column shape and the store validates each one against
  ``tables.validate_row``, so a row can never carry an answer.
* :class:`BffOps` creates dataset workstreams, resolves and links stores, assigns agents.
* :class:`LocalOutputsDriver` serves ``--tier local``: it scores outputs already on disk.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import yaml

from cli.builder_common import release_file
from cli.builder_common import repo as repo_mod
from cli.builder_common import workspace as bws
from cli.builder_common.cloudmode import cloud_mode
from cli.builder_common.datasets import list_datasets, load_dataset, recorded_workstream_ids
from cli.builder_common.docstore import DocStore, DocStoreArtifacts, Route, default_route
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.evals import runner
from cli.builder_common.evals.bff_driver import BffHarnessDriver
from cli.builder_common.evals.blind import generic_prompt_rows
from cli.builder_common.evals.gate import GateRefusedError, evaluate_criteria
from cli.builder_common.evals.metrics import UNRESOLVED_GAP_STATUSES, RunResults, metric_rows
from cli.builder_common.evals.models import STATUS_ERROR, STATUS_PENDING
from cli.builder_common.evals.protocols import (
    TURN_COMPLETED,
    TURN_FAILED,
    ArtifactStore,
    HarnessDriver,
    RowStore,
    TurnHandle,
    TurnStatus,
)
from cli.builder_common.gaps import list_gaps
from cli.builder_common.identity import current_user
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.release_approval import CLOUD_REFUSAL
from cli.builder_common.rowstore import OrgRowStore
from cli.core.context import Context
from cli.core.output import Result
from cli.core.registry import Registry
from cli.harness_common.api_client import HarnessAPIClient

OPS_WORKSTREAM = "builder-ops"
_IDENTITY = ("abcd-builder", "builder@abcd.invalid")

# Overridable in tests.
sleep: Callable[[float], None] = time.sleep


# --------------------------------------------------------------------------- shared helpers


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def actor(args: Any = None, ws: Path | None = None) -> str:
    """Who is acting: ``--by``, else the shared identity (``builder_common.identity``)."""
    by = getattr(args, "by", None) if args is not None else None
    if by and cloud_mode(ws):
        raise RefusedError(f"{CLOUD_REFUSAL} (got --by)")
    return str(by or current_user(ws))


class CommitResult(NamedTuple):
    """``sha`` of the commit (None when nothing was committed), the new work ``branch`` (None when
    the current one was kept) and the ``note`` a command prints when it moved off the default branch."""

    sha: str | None
    branch: str | None
    note: str


def _branch_note(branch: str | None) -> str:
    if not branch:
        return ""
    return (
        f"\nCommitted on new branch {branch}: the workspace was on the default branch and abcd "
        "never commits there. Push it and open a pull request."
    )


def commit_work(ws: Path, message: str, paths: list[str], *, topic: str = "change") -> CommitResult:
    """Commit ``paths`` (workspace-relative) if the workspace is a git repo and they changed.

    Only ``paths`` are committed (never other staged files). On the default branch (or a detached
    HEAD) the work moves first to a new ``builder/<topic>-<yyyymmdd>-<short>`` branch: no abcd
    command commits on the default branch. ``note`` is the line to append to the command's text.
    """
    if not (ws / ".git").exists():
        return CommitResult(None, None, "")
    existing = [p for p in paths if (ws / p).exists()]
    if not existing:
        return CommitResult(None, None, "")
    dirty = repo_mod.run_git(ws, "status", "--porcelain", "--", *existing).stdout.strip()
    if not dirty:
        return CommitResult(None, None, "")
    branch = repo_mod.ensure_work_branch(ws, topic)
    has_user = repo_mod.run_git(ws, "config", "user.name", check=False).returncode == 0
    sha = repo_mod.commit(ws, message, paths=existing, identity=None if has_user else _IDENTITY)
    return CommitResult(sha, branch, _branch_note(branch))


def agent_dir(ws: Path, agent: str) -> Path:
    d = bws.agent_dir(ws, agent)
    if not d.is_dir():
        raise UsageError(f"agent {agent!r} has no bundle at {d}")
    return d


def read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    data = yaml.safe_load(path.read_text())
    return default if data is None else data


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def process_id_for(ws: Path, agent: str, *, staged: bool, rows: Any = None) -> str:
    """The agent's process id: ``builder.json``, else its ``bld_agents`` row.

    ``push --staged`` records the staged process in ``bld_agents`` (not in the committed
    ``builder.json``, which would dirty the tree ``stage`` insists on), so the row is the
    source a fresh push leaves behind (live finding, 2026-10-29).
    """
    entry = bws.load_builder_config(ws)["agents"].get(agent) or {}
    key = "staged_process_id" if staged else "live_process_id"
    pid = entry.get(key) or (None if staged else entry.get("staged_process_id"))
    if not pid and rows is not None:
        found = rows.query("bld_agents", {"agent_key": agent})
        row = found[0] if found else {}
        pid = row.get("staged_process_id") if staged else row.get("process_id")
        pid = pid or (None if staged else row.get("staged_process_id"))
    if not pid:
        raise UsageError(
            f"agent {agent!r} has no {'staged' if staged else 'live'} process id "
            f"(run `abcd push {agent} --staged` first)"
        )
    return str(pid)


def datasets_of_agent_refs(ws: Path) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Every case id, expected ref and input file name across the workspace's datasets."""
    case_ids: list[str] = []
    refs: list[dict[str, str]] = []
    inputs: list[str] = []
    for name in list_datasets(ws):
        ds = load_dataset(ws, name)
        for case in ds.cases.values():
            case_ids.append(case["case_id"])
            refs.extend(case["expected"])
            inputs.extend(f["path"] for f in case["inputs"].get("files", []))
    return case_ids, refs, inputs


def unresolved_gap_count(ws: Path, agent: str) -> int:
    return sum(1 for g in list_gaps(ws, agent=agent) if g["status"] in UNRESOLVED_GAP_STATUSES)


# --------------------------------------------------------------------------- platform adapters


class PlatformOps(Protocol):
    """Workstream, store and assignment operations (PLAN.md section 9)."""

    def ensure_workstream(
        self, name: str, description: str = "", properties: dict[str, Any] | None = None
    ) -> str: ...

    def workstream_id(self, name: str) -> str | None: ...

    def store_id(self, store_name: str) -> str: ...

    def assign_process(self, process_id: str, workstream_ids: list[str]) -> list[str]: ...

    def archive_workstream(self, name: str) -> None: ...


class BffOps:
    """``PlatformOps`` over the BFF; ids are cached in ``builder.json``."""

    def __init__(self, client: HarnessAPIClient, ws: Path) -> None:
        self.client = client
        self.ws = ws

    def workstream_id(self, name: str) -> str | None:
        return bws.load_builder_config(self.ws)["workstreams"].get(name)

    def ensure_workstream(
        self, name: str, description: str = "", properties: dict[str, Any] | None = None
    ) -> str:
        known = self.workstream_id(name)
        if known:
            return known
        found = self.client.list_workstreams(search=name)
        items = found.get("items") or found.get("workstreams") or []
        match = next((w for w in items if w.get("name") == name), None)
        if match is None:
            body: dict[str, Any] = {"name": name, "description": description}
            if properties:
                body["custom_properties"] = properties
            match = self.client._request("POST", "/v1/org/workstreams", json=body)
        wid = str(match.get("id") or match.get("workstream_id"))
        bws.update_builder_config(self.ws, workstreams={name: wid})
        return wid

    def store_id(self, store_name: str) -> str:
        cfg = bws.load_builder_config(self.ws)
        if store_name in cfg["stores"]:
            return cfg["stores"][store_name]
        link = (
            cfg["workstreams"].get(OPS_WORKSTREAM)
            if store_name.startswith("ds-")
            else cfg["workstreams"].get(store_name)
        )
        if store_name.startswith("ds-") and not link:
            raise UsageError("builder-ops workstream is not in builder.json; run builder setup")
        # Resolve by NAME only. Live 2026-10-29: with `workstream_id` the platform returns a
        # store already linked to that workstream whatever its name, so every `ds-<dataset>`
        # collapsed onto one shared store (the second dataset's `manifests/v1.json` then collided
        # with the first's). The link is added below instead.
        resp = self.client._request(
            "POST", "/v1/docstore/stores/resolve", json={"name": store_name}
        )
        if resp.get("name") and resp["name"] != store_name:
            body: dict[str, Any] = {"name": store_name}
            if link:
                body["workstream_id"] = link
            resp = self.client._request("POST", "/v1/docstore/stores", json=body)
        sid = str(resp.get("id") or resp.get("store_id"))
        if link and link not in (resp.get("workstream_ids") or []):
            self.client.link_store_to_workstreams(sid, [link])
        bws.update_builder_config(self.ws, stores={store_name: sid})
        return sid

    def assign_process(self, process_id: str, workstream_ids: list[str]) -> list[str]:
        cur = self.client.get_process_workstreams(process_id)
        merged = sorted({*cur.get("workstream_ids", []), *workstream_ids})
        self.client.set_process_workstreams(process_id, merged)
        return merged

    def archive_workstream(self, name: str) -> None:
        wid = self.workstream_id(name)
        if wid:
            self.client.delete_workstream(wid)  # soft delete


def dataset_route(ws: Path) -> Route:
    """Per-case datasets keep their inputs in the case's own workstream store."""
    pattern = re.compile(r"^(ds:[^/:]+)/inputs/([^/]+)/(.+)$")

    def route(path: str) -> tuple[str, str]:
        m = pattern.match(path)
        if m:
            name = m.group(1)[3:]
            try:
                layout = load_dataset(ws, name).manifest["layout"]
            except UsageError:
                layout = "shared"
            if layout == "per_case":
                return f"{m.group(1)}:{m.group(2)}", f"inputs/{m.group(3)}"
            return m.group(1), f"inputs/{m.group(2)}/{m.group(3)}"
        return default_route(path)

    return route


class LocalOutputsDriver:
    """``--tier local``: no platform turn; outputs already on disk are the agent's output.

    Outputs live in ``<root>/<case_id>/...`` (per-case datasets only).
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _case(handle: TurnHandle) -> str:
        return handle.workstream.rsplit(":", 1)[-1]

    def start_turn(
        self,
        *,
        workstream: str,
        process_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnHandle:
        case = workstream.rsplit(":", 1)[-1]
        return TurnHandle(f"local-{case}", "local", workstream)

    def turn_status(self, handle: TurnHandle) -> TurnStatus:
        d = self.root / self._case(handle)
        if d.is_dir():
            return TurnStatus(state=TURN_COMPLETED)
        return TurnStatus(state=TURN_FAILED, error=f"no local output directory {d}")

    def fetch_outputs(self, handle: TurnHandle) -> dict[str, bytes]:
        d = self.root / self._case(handle)
        return {
            p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
        }

    def fetch_transcript(self, handle: TurnHandle) -> str:
        return ""

    def stop_turn(self, handle: TurnHandle) -> None:
        return None


@dataclass
class Services:
    store: ArtifactStore
    rows: RowStore
    ops: PlatformOps
    driver: HarnessDriver | None = None


def get_services(ctx: Context) -> Services:
    """Real services for the context's workspace. Tests replace this function."""
    ws = ctx.env_dir
    client = ctx.harness_client()
    ops = BffOps(client, ws)
    docs = DocStore(client, resolve=ops.store_id, route=dataset_route(ws))
    return Services(
        store=DocStoreArtifacts(docs, replace_when=lambda path: "/_builder/" in path),
        rows=OrgRowStore(OrgDBClient.from_client(client)),
        ops=ops,
        driver=BffHarnessDriver(
            client,
            workstream_ids=recorded_workstream_ids(ws),
            model=getattr(ctx.args, "model", None),
        ),
    )


def mirror_rows(
    rows: RowStore, table: str, items: list[dict[str, Any]], key: str, *, refresh: bool = True
) -> None:
    """Insert (idempotent) and refresh each row, so a changed definition updates its row."""
    for item in items:
        rows.insert(table, item)
        if refresh:
            rows.update(table, str(item[key]), item)


# --------------------------------------------------------------------------- results helpers


def run_ids(ws: Path) -> list[str]:
    root = ws / ".builder" / "evals"
    return (
        sorted(p.name for p in root.iterdir() if (p / "state.json").exists())
        if root.exists()
        else []
    )


def load_results(ws: Path, agent: str, run_id: str) -> RunResults:
    state = runner.load_state(ws, run_id)
    if state["status"] == "running":
        raise UsageError(f"run {run_id} is still running; use `abcd eval run --resume {run_id}`")
    return runner.results_from_state(state, unresolved_gap_count(ws, agent))


def latest_blind_run(ws: Path, agent: str) -> str | None:
    best: tuple[str, str] | None = None
    for rid in run_ids(ws):
        st = runner.load_state(ws, rid)
        if st["agent"] == agent and st["blind"] and st["status"] == "complete":
            if best is None or st["created_at"] > best[0]:
                best = (st["created_at"], rid)
    return best[1] if best else None


def criteria_for_gate(ws: Path, agent: str) -> list[dict[str, Any]]:
    d = agent_dir(ws, agent)
    data = read_yaml(d / "release.yaml", {"criteria": []})
    formulas = {
        m["name"]: m["formula"]
        for m in read_yaml(d / "metrics.yaml", {"metrics": []}).get("metrics", [])
        if m.get("formula")
    }
    out = []
    for c in data.get("criteria", []):
        op = c["comparator"]
        crit: dict[str, Any] = {
            "criteria_key": c["criteria_key"],
            "metric": formulas.get(c["metric"], c["metric"]),
            "comparator": ">=" if op == "no_regression" else op,
            "dataset_version": c.get("dataset_version"),
        }
        if c.get("target") is not None:
            crit["target"] = c["target"]
        out.append(crit)
    return out


def _summ(results: RunResults) -> dict[str, Any]:
    cases = results.cases
    return {
        "run_id": results.run_id,
        "agent": results.agent,
        "dataset": results.dataset,
        "dataset_version": results.dataset_version,
        "blind": results.blind,
        "tier": results.tier,
        "cases": len(cases),
        "passed": sum(1 for c in cases if c.passed is True),
        "pending_judgement": sum(1 for c in cases if c.status == STATUS_PENDING),
        "errors": sum(1 for c in cases if c.status == STATUS_ERROR),
        "asked": [c.case_id for c in cases if c.status == "asked"],
        "failed": [c.case_id for c in cases if c.passed is False],
    }


def _summ_text(s: dict[str, Any]) -> str:
    return (
        f"Run {s['run_id']} ({s['agent']} on {s['dataset']} v{s['dataset_version']}, "
        f"{'blind' if s['blind'] else 'open'}, {s['tier']}): "
        f"{s['passed']}/{s['cases']} passed, {s['pending_judgement']} pending judgement, "
        f"{s['errors']} errors"
    )


# --------------------------------------------------------------------------- handlers


def _cmd_run(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    if a.resume and a.submit_only:
        raise UsageError("--resume and --submit-only cannot be combined")
    tier_arg = a.tier
    svc = get_services(ctx)
    if a.resume:
        state = runner.load_state(ws, a.resume)
        if state["agent"] != a.agent:
            raise UsageError(f"run {a.resume} belongs to agent {state['agent']!r}")
        driver = _driver_for(ctx, svc, state["tier"], state["dataset"])
        run_id = a.resume
    else:
        if not a.dataset:
            raise UsageError("--dataset is required for a new run")
        tier = "staged" if a.staged and tier_arg == "platform" else tier_arg
        ds = load_dataset(ws, a.dataset)
        if tier_arg == "local" and (
            ds.manifest["layout"] != "per_case" or ds.kind != "work_product"
        ):
            raise UsageError("--tier local supports per-case work_product datasets only")
        driver = _driver_for(ctx, svc, tier, a.dataset)
        probe = ds.manifest.get("probe_agent")
        probe_pid = process_id_for(ws, probe, staged=a.staged, rows=svc.rows) if probe else None
        process_id = (
            "local"
            if tier_arg == "local"
            else process_id_for(ws, a.agent, staged=a.staged, rows=svc.rows)
        )
        if ctx.dry_run_notice(f"submit a {tier} eval of {a.agent} on {a.dataset}"):
            return Result.done({"dry_run": True})
        for r in generic_prompt_rows(ws, a.agent):
            svc.rows.insert("bld_generic_prompts", r)
        sha = ""
        if (ws / ".git").exists():
            sha = repo_mod.current_sha(ws)[:12]
        state = runner.submit_run(
            ws,
            dataset=a.dataset,
            agent=a.agent,
            process_id=process_id,
            driver=driver,
            store=svc.store,
            rows=svc.rows,
            blind=a.blind,
            case_ids=a.case or None,
            tier=tier,
            where="local"
            if tier_arg == "local" or not os.environ.get("ADOPT_CONVERSATION_ID")
            else "cloud",
            version=sha,
            probe_process_id=probe_pid,
        )
        run_id = state["run_id"]
        if a.submit_only:
            turns = runner._turn_list(state)
            return Result.done(
                {"run_id": run_id, "status": state["status"], "turns": turns},
                f"Submitted {run_id}: {len(turns)} turn(s). "
                f"Resume with: abcd eval run {a.agent} --resume {run_id}",
            )
    waited = 0.0
    while True:
        state = runner.advance(ws, run_id, driver, svc.store, svc.rows)
        if state["status"] != "running":
            break
        if waited >= a.timeout:
            raise PlatformError(
                f"run {run_id} did not finish in {a.timeout}s; resume with --resume {run_id}"
            )
        sleep(a.poll)
        waited += a.poll
    results = runner.collect(
        ws, run_id, svc.store, svc.rows, open_gap_count=unresolved_gap_count(ws, state["agent"])
    )
    packet = (
        runner.build_packet(ws, run_id, svc.store)
        if any(c.status == STATUS_PENDING for c in results.cases)
        else None
    )
    data = _summ(results)
    data["gap_candidates"] = results.meta.get("gap_candidates", [])
    data["packet_path"] = packet
    text = _summ_text(data) + (
        f"\nJudged checks pending: run `abcd eval judge-packet {run_id}`" if packet else ""
    )
    return Result.done(data, text)


def _driver_for(ctx: Context, svc: Services, tier: str, dataset: str) -> HarnessDriver:
    if tier == "local":
        root = (
            Path(ctx.args.outputs)
            if getattr(ctx.args, "outputs", None)
            else (ctx.env_dir / ".builder" / "local-outputs" / dataset)
        )
        return LocalOutputsDriver(root)
    if svc.driver is None:
        raise UsageError("no harness driver available")
    return svc.driver


def _cmd_judge_packet(ctx: Context) -> Result:
    ws = ctx.env_dir
    svc = get_services(ctx)
    path = runner.build_packet(ws, ctx.args.run, svc.store)
    if path is None:
        return Result.done({"packet_path": None}, "Nothing is pending judgement.")
    local = ws / ".builder" / "evals" / ctx.args.run / "packet.json"
    local.write_bytes(
        svc.store.get(path)
    )  # .builder/ is git-ignored: expected values never enter git
    return Result.done(
        {"packet_path": path, "local_path": str(local)},
        f"Judging packet: {local}\n(stored at {path}; it holds expected values, never commit it)",
    )


def _cmd_judge_apply(ctx: Context) -> Result:
    ws = ctx.env_dir
    scores = json.loads(Path(ctx.args.scores).read_text())
    if not isinstance(scores, dict):
        raise UsageError("--scores must be a JSON object {case_id: {...}}")
    if ctx.dry_run_notice(f"apply judgements to {ctx.args.run}"):
        return Result.done({"dry_run": True})
    svc = get_services(ctx)
    state = runner.apply_judgements(ws, ctx.args.run, scores, threshold=ctx.args.threshold)
    results = runner.collect(
        ws,
        ctx.args.run,
        svc.store,
        svc.rows,
        open_gap_count=unresolved_gap_count(ws, state["agent"]),
    )
    for row in runner.result_rows(results):  # the first collect stored them as pending
        svc.rows.update("bld_eval_results", row["result_key"], row)
    for row in metric_rows(results):
        svc.rows.update("bld_metric_values", row["value_key"], row)
    data = _summ(results)
    return Result.done(data, _summ_text(data))


def _cmd_report(ctx: Context) -> Result:
    from cli.builder_common.evals import report as rep

    ws = ctx.env_dir
    state = runner.load_state(ws, ctx.args.run)
    agent = state["agent"]
    results = load_results(ws, agent, ctx.args.run)
    live = load_results(ws, agent, ctx.args.live) if ctx.args.live else None
    decision = None
    criteria = criteria_for_gate(ws, agent) if (ws / "harness" / "agents" / agent).is_dir() else []
    if criteria:
        try:
            decision = evaluate_criteria(criteria, results, live)
        except GateRefusedError:
            decision = None
    report = rep.build_report(results, decision=decision, live=live)
    if ctx.dry_run_notice(f"write the eval report for {ctx.args.run}"):
        return Result.done({"dry_run": True})
    svc = get_services(ctx)
    paths = rep.write_report(svc.store, report, ctx.args.run)
    # Local copies go to the git-ignored .builder/: a committed report is the release branch's job
    # (harness/evals/reports/<agent>/<version>.html), and an untracked file here makes the tree
    # dirty, which stage and release refuse (live 2026-10-29).
    out_dir = Path(ctx.args.out) if ctx.args.out else ws / ".builder" / "evals" / ctx.args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, hp = out_dir / f"{ctx.args.run}.json", out_dir / f"{ctx.args.run}.html"
    jp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    hp.write_text(rep.render_html(report))
    state["report_path"] = paths["html"]
    runner.save_state(ws, state)
    svc.rows.update("bld_eval_runs", ctx.args.run, {"report_path": paths["html"]})
    return Result.done(
        {"store": paths, "files": {"json": str(jp), "html": str(hp)}, "report": report["header"]},
        f"Report: {hp}\n        {jp}\n(stored: {paths['html']})",
    )


def _cmd_gate(ctx: Context) -> Result:
    ws = ctx.env_dir
    a = ctx.args
    results = load_results(ws, a.agent, a.run)
    if results.agent != a.agent:
        raise UsageError(f"run {a.run} belongs to agent {results.agent!r}")
    live = load_results(ws, a.agent, a.live) if a.live else None
    criteria = criteria_for_gate(ws, a.agent)
    if not criteria:
        raise RefusedError(f"agent {a.agent!r} has no release criteria; run `abcd criteria set`")
    try:
        decision = evaluate_criteria(criteria, results, live)
    except GateRefusedError as e:
        return Result.refused(str(e), {"passed": False, "reasons": e.reasons, "run_id": a.run})
    lines = [
        f"Gate for {a.agent} on run {a.run} (dataset v{decision.dataset_version}): "
        + ("PASS" if decision.passed else "FAIL")
    ]
    lines += [
        f"  [{'ok' if r.passed else 'FAIL'}] {r.criteria_key}: {r.detail}" for r in decision.results
    ]
    data = decision.to_dict()
    if decision.passed:
        return Result.done(data, "\n".join(lines))
    return Result(
        1,
        data,
        "\n".join(lines),
        "gate failed: " + ", ".join(r.criteria_key for r in decision.failed()),
    )


# --------------------------------------------------------------------------- registration


def register(registry: Registry) -> None:
    g = registry.group("eval", "Run evals, judge, report and gate")
    p = g.command("run", _cmd_run, help="Run an eval (submit, poll, score)", writes=True)
    p.add_argument("agent")
    p.add_argument("--dataset", help="Dataset to run (required unless --resume)")
    p.add_argument("--blind", action="store_true", help="Blind run: generic prompt, inputs only")
    p.add_argument("--tier", choices=["local", "platform"], default="platform")
    p.add_argument(
        "--staged", action="store_true", help="Run the staged process (exact staged hashes)"
    )
    p.add_argument("--resume", metavar="RUN_ID", help="Poll and finish a submitted run")
    p.add_argument("--submit-only", action="store_true", help="Submit the turns and return")
    p.add_argument("--case", action="append", help="Only this case id (repeatable)")
    p.add_argument("--outputs", help="--tier local: directory of <case_id>/ outputs")
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--model", help="Model id for the turns (default: the org's default model)")
    p = g.command("judge-packet", _cmd_judge_packet, help="Write the packet of judged checks")
    p.add_argument("run")
    p = g.command(
        "judge-apply", _cmd_judge_apply, help="Apply the coding agent's judged scores", writes=True
    )
    p.add_argument("run")
    p.add_argument("--scores", required=True, help="JSON file {case_id: {scores, checks, notes}}")
    p.add_argument("--threshold", type=float, default=0.5)
    p = g.command("report", _cmd_report, help="Write the eval report (HTML + JSON)", writes=True)
    p.add_argument("run")
    p.add_argument("--live", metavar="RUN", help="Live run to compare against")
    p.add_argument(
        "--out", help="Directory for the local copies (default: .builder/evals/<run>/, git-ignored)"
    )
    p = g.command("gate", _cmd_gate, help="Evaluate the release criteria against a run")
    p.add_argument("agent")
    p.add_argument("--run", required=True)
    p.add_argument("--live", metavar="RUN", help="Live run (for no_regression)")


def criteria_referencing(ws: Path, dataset: str) -> list[str]:
    """Criteria keys (``agent:criteria_key``) in any agent's release.yaml that use the dataset."""
    out = []
    for agent in bws.list_agents(ws):
        data = read_yaml(bws.agent_dir(ws, agent) / "release.yaml", {"criteria": []})
        if release_file.split_dataset(data.get("dataset"))[0] == dataset:
            out += [f"{agent}:{c['criteria_key']}" for c in data.get("criteria", [])]
    return out
