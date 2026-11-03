"""``abcd gap``, ``abcd instructions`` and ``abcd build-exit``: close context gaps (PLAN.md 11).

A blind run that misses a case for lack of context opens a gap. The FDE or SME answers it, the
answer becomes a generic instruction page (leak-checked, vetted), the instruction compiles into
the ``<agent>-instructions`` skill and a blind re-run verifies the gap. Gap files show the golden
output by path and hash only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli import builder_eval as be
from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common import gaps as G  # noqa: N812
from cli.builder_common import instructions as I  # noqa: N812,E741
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals import runner
from cli.builder_common.evals.metrics import RunResults, open_gaps_by_owner
from cli.builder_common.evals.protocols import sha256_hex
from cli.builder_common.release_approval import refuse_self_asserted_in_cloud
from cli.core.context import Context
from cli.core.output import Result
from cli.core.registry import Registry


def _row_update(svc: be.Services, gap_key: str, fields: dict[str, Any]) -> None:
    svc.rows.update("bld_context_gaps", gap_key, fields)


def _gap_line(g: dict[str, Any]) -> str:
    return f"{g['gap_key']}  {g['status']:<8} {g['owner']:<3} {g['agent']}/{g['case_id']}"


# --------------------------------------------------------------------------- gap commands


def _cmd_list(ctx: Context) -> Result:
    a = ctx.args
    gs = G.list_gaps(ctx.env_dir, agent=a.agent, status=a.status)
    if a.owner:
        gs = [g for g in gs if g["owner"] == a.owner]
    data = {
        "gaps": [
            {k: g.get(k) for k in (*G.ROW_FIELDS, "client", "waived_by", "source_run")} for g in gs
        ],
        "open_by_owner": open_gaps_by_owner(gs),
    }
    return Result.done(data, "\n".join(_gap_line(g) for g in gs) or "No gaps.")


def _cmd_open(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    state = runner.load_state(ws, a.from_run)
    results = be.load_results(ws, state["agent"], a.from_run)
    ds = D.load_dataset(ws, state["dataset"])
    classifications: dict[str, str] = {}
    if a.classify:
        classifications = json.loads(Path(a.classify).read_text())
    refs = {cid: c["expected"][0] for cid, c in ds.cases.items() if c.get("expected")}
    if ctx.dry_run_notice(f"open gaps from {a.from_run}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    out = G.gaps_from_run(
        ws,
        results,
        client=a.client or str(ds.manifest.get("client") or ""),
        classifications=classifications,
        expected_refs=refs,
        store=svc.store,
        rows=svc.rows,
    )
    opened = [g["gap_key"] for g in out["opened"]]
    text = f"Opened {len(opened)} gap(s): {', '.join(opened) or 'none'}; {len(out['build_plan'])} build plan item(s)."
    return Result.done({"opened": opened, "build_plan": out["build_plan"]}, text)


def _cmd_ask(ctx: Context) -> Result:
    a = ctx.args
    refuse_self_asserted_in_cloud(ctx.env_dir, by=a.by)
    if ctx.dry_run_notice(f"send {a.gap} to the {a.to}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    g = G.ask(ctx.env_dir, a.gap, by=be.actor(a, ctx.env_dir), owner=a.to, rows=svc.rows)
    where = (
        f"builder-sme-{g['client']}/gaps/{g['gap_key']}.md" if g.get("client") else "the gap file"
    )
    return Result.done(
        {"gap": g["gap_key"], "status": g["status"], "owner": g["owner"]},
        f"Asked {a.to}: see {where}.",
    )


def _cmd_answer(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    refuse_self_asserted_in_cloud(ws, by=a.by)
    g = G.get_gap(ws, a.gap)
    src = Path(a.file)
    if not src.is_file():
        raise UsageError(f"file not found: {src}")
    if not g.get("client"):
        raise UsageError(f"gap {a.gap} has no client, so no builder-sme store to hold the answer")
    if g["status"] not in ("open", "asked"):
        raise RefusedError(f"gap {a.gap} is {g['status']}; it cannot be answered")
    if ctx.dry_run_notice(f"record the answer to {a.gap}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    data = src.read_bytes()
    path = f"builder-sme-{g['client']}/answers/{g['gap_key']}/{sha256_hex(data)[:8]}-{src.name}"
    sha = svc.store.put(path, data)
    out = G.answer(ws, a.gap, answer_path=path, by=be.actor(a, ctx.env_dir), rows=svc.rows)
    _row_update(svc, a.gap, {"answer_sha256": sha})
    return Result.done(
        {"gap": a.gap, "status": out["status"], "answer_path": path}, f"Answer stored at {path}."
    )


def _cmd_encode(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    refuse_self_asserted_in_cloud(ws, by=a.by, vetted_by=a.vetted_by)
    g = G.get_gap(ws, a.gap)
    if g["status"] != "answered":
        raise RefusedError(f"gap {a.gap} is {g['status']}; only an answered gap can be encoded")
    page = Path(a.page)
    if not page.is_file():
        raise UsageError(f"file not found: {page}")
    name = a.name or g["gap_key"]
    if ctx.dry_run_notice(f"encode {a.gap} as instruction {name}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    provenance = [{"source": "gap", "ref": g["gap_key"]}]
    if g.get("answer_path"):
        provenance.append({"source": "answer", "ref": g["answer_path"]})
    rec = I.save_instruction(
        ws,
        svc.store,
        agent=g["agent"],
        name=name,
        scope=a.scope,
        body=page.read_text(),
        gaps=[g["gap_key"]],
        provenance=provenance,
        client=g.get("client", "") if a.scope == "client" else "",
        rows=svc.rows,
    )
    case_ids, refs, inputs = be.datasets_of_agent_refs(ws)
    rec = I.run_leak_check(
        ws, svc.store, g["agent"], rec["instruction_key"],
        case_ids=case_ids, expected_refs=refs, input_names=inputs, rows=svc.rows,
    )  # fmt: skip
    if rec["leak_check"]["status"] != "clean":
        kinds = sorted({f["kind"] for f in rec["leak_check"]["findings"]})
        return Result.refused(
            f"leak check flagged {rec['instruction_key']}: {kinds}; the gap stays answered. "
            f"Rewrite the page as a generic rule and encode again with --name {name}",
            {"instruction": rec["instruction_key"], "findings": rec["leak_check"]["findings"]},
        )
    if a.vetted_by:
        rec = I.vet(ws, g["agent"], rec["instruction_key"], by=a.vetted_by, rows=svc.rows)
    out = G.encode(
        ws, a.gap, instruction=rec["instruction_key"], by=be.actor(a, ctx.env_dir), rows=svc.rows
    )
    return Result.done(
        {"gap": a.gap, "status": out["status"], "instruction": rec["instruction_key"],
         "instruction_status": rec["status"], "page_path": rec["page_path"]},
        f"Encoded {a.gap} as {rec['instruction_key']} ({rec['status']}).",
    )  # fmt: skip


def _cmd_waive(ctx: Context) -> Result:
    a = ctx.args
    refuse_self_asserted_in_cloud(ctx.env_dir, lead=a.lead)
    if ctx.dry_run_notice(f"waive {a.gap}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    g = G.waive(ctx.env_dir, a.gap, lead=a.lead, reason=a.reason, rows=svc.rows)
    _row_update(svc, a.gap, {"waive_reason": a.reason})
    return Result.done(
        {"gap": a.gap, "status": g["status"], "waived_by": a.lead},
        f"Waived {a.gap} ({a.lead}); the scorecard counts it.",
    )


def _cmd_verify(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    g = G.get_gap(ws, a.gap)
    rerun = be.load_results(ws, g["agent"], a.run)
    base_id = a.baseline or g.get("source_run")
    if not base_id:
        raise UsageError("the gap has no source run; pass --baseline RUN")
    baseline = be.load_results(ws, g["agent"], base_id)
    if ctx.dry_run_notice(f"verify {a.gap} against {a.run}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    out = G.verify_by_blind_rerun(ws, a.gap, rerun, baseline, rows=svc.rows)
    ok = out["status"] == "verified"
    msg = f"Gap {a.gap} is {out['status']}" + (
        f" (run {a.run})." if ok else f": reopened with new evidence from {a.run}."
    )
    data = {"gap": a.gap, "status": out["status"], "run": a.run}
    return Result.done(data, msg) if ok else Result(1, data, msg, f"gap {a.gap} not verified")


# --------------------------------------------------------------------------- instructions


def _cmd_compile(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    if ctx.dry_run_notice(f"compile {a.agent}-instructions"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    case_ids, refs, inputs = be.datasets_of_agent_refs(ws)
    out = I.compile_instruction_skill(
        ws,
        svc.store,
        a.agent,
        case_ids=case_ids,
        expected_refs=refs,
        input_names=inputs,
        rows=svc.rows,
    )
    done = be.commit_work(
        ws, f"{a.agent}: compile instruction skill {out['skill_version']}",
        [f"harness/skills/{a.agent}-instructions"],
        topic=f"{a.agent}-instructions",
    )  # fmt: skip
    out["commit"], out["branch"] = done.sha, done.branch
    return Result.done(
        out,
        f"Compiled {out['skill_name']} {out['skill_version']}: {len(out['included'])} instruction(s)."
        + done.note,
    )


def _sync_workstreams(ws: Path, client: str, extra: list[str]) -> list[str]:
    names = list(extra)
    if f"{client}-staging" in bws.load_builder_config(ws)["workstreams"]:
        names.append(f"{client}-staging")
    for dname in D.list_datasets(ws):
        ds = D.load_dataset(ws, dname)
        if ds.manifest.get("client") != client:
            continue
        if ds.manifest["layout"] == "shared":
            names.append(ds.workstream(""))
        else:
            names += [ds.workstream(c) for c in ds.active_case_ids()]
    return sorted(set(names))


def _cmd_sync(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    agents = (
        [a.agent]
        if a.agent
        else sorted(p.stem for p in (ws / ".builder" / "instructions").glob("*.json"))
        if (ws / ".builder" / "instructions").exists()
        else []
    )
    workstreams = _sync_workstreams(ws, a.client, a.workstream or [])
    plan: list[dict[str, str]] = []
    for agent in agents:
        plan += I.client_sync_plan(ws, agent, a.client, workstreams)
    if not plan:
        return Result.done(
            {"plan": [], "written": []},
            "Nothing to sync (no vetted, clean client-scope instructions).",
        )
    if ctx.dry_run_notice(f"sync {len(plan)} file(s) to {len(workstreams)} workstream(s)"):
        return Result.done({"dry_run": True, "plan": [p["target"] for p in plan]})
    svc = be.get_services(ctx)
    written = I.apply_sync_plan(plan, svc.store)
    return Result.done(
        {"plan": [p["target"] for p in plan], "written": written},
        f"Synced {len(written)} file(s) for {a.client}.",
    )


# --------------------------------------------------------------------------- build-exit


def _cmd_build_exit(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    run_id = a.run or be.latest_blind_run(ws, a.agent)
    instr: list[dict[str, Any]] = list(I.load_index(ws, a.agent).values())
    target = a.repro_target
    if target is None:
        crit = be.read_yaml(be.agent_dir(ws, a.agent) / "release.yaml", {"criteria": []})[
            "criteria"
        ]
        target = next((float(c["target"]) for c in crit if c["metric"] == "blind_repro_rate"), 1.0)
    if run_id is None:
        status = G.build_exit_status(
            ws,
            a.agent,
            RunResults("", a.agent, "", 0, True, []),
            instructions=instr,
            repro_target=target,
        )
        status["ready"] = False
        status["checks"].insert(
            0, {"check": "a completed blind run exists", "passed": False, "detail": None}
        )
        return Result.refused("no completed blind run for this agent", status)
    results = be.load_results(ws, a.agent, run_id)
    if not results.blind:
        raise RefusedError(f"run {run_id} is not blind; the build exit needs a blind run")
    status = G.build_exit_status(ws, a.agent, results, instructions=instr, repro_target=target)
    status["run_id"] = run_id
    lines = [
        f"Build exit for {a.agent} (run {run_id}): " + ("READY" if status["ready"] else "NOT READY")
    ]
    lines += [f"  [{'ok' if c['passed'] else 'FAIL'}] {c['check']}" for c in status["checks"]]
    if status["ready"]:
        return Result.done(status, "\n".join(lines))
    return Result(1, status, "\n".join(lines), "build exit criteria not met")


# --------------------------------------------------------------------------- registration


def register(registry: Registry) -> None:
    g = registry.group("gap", "Context gaps from blind evals")
    p = g.command("list", _cmd_list, help="List gaps")
    p.add_argument("--agent")
    p.add_argument("--status", choices=list(G.STATUSES))
    p.add_argument("--owner", choices=list(G.OWNERS))
    p = g.command(
        "open", _cmd_open, help="Open gaps for the missing-context misses of a run", writes=True
    )
    p.add_argument("--from-run", required=True, metavar="RUN")
    p.add_argument("--classify", help="JSON {case_id: cause} from the context-gaps skill")
    p.add_argument("--client")
    p = g.command("ask", _cmd_ask, help="Send a gap to the SME (or FDE)", writes=True)
    p.add_argument("gap")
    p.add_argument("--to", default="sme", choices=list(G.OWNERS))
    p.add_argument("--by")
    p = g.command("answer", _cmd_answer, help="Record the answer to a gap", writes=True)
    p.add_argument("gap")
    p.add_argument("--file", required=True)
    p.add_argument("--by")
    p = g.command(
        "encode", _cmd_encode, help="Turn the answer into a generic instruction page", writes=True
    )
    p.add_argument("gap")
    p.add_argument("--page", required=True, help="Markdown page with the generic rule")
    p.add_argument("--name", help="Instruction name (default: the gap key)")
    p.add_argument("--scope", default="agent", choices=list(I.SCOPES))
    p.add_argument("--vetted-by", help="FDE or SME who vetted the page")
    p.add_argument("--by")
    p = g.command("waive", _cmd_waive, help="Waive a gap (a lead's recorded reason)", writes=True)
    p.add_argument("gap")
    p.add_argument("--lead", required=True)
    p.add_argument("--reason", required=True)
    p = g.command(
        "verify", _cmd_verify, help="Verify a gap with a blind full-set re-run", writes=True
    )
    p.add_argument("gap")
    p.add_argument("--run", required=True, help="The blind re-run")
    p.add_argument(
        "--baseline", help="Run to compare regressions against (default: the source run)"
    )

    g = registry.group("instructions", "Generic instructions")
    p = g.command(
        "compile", _cmd_compile, help="Leak-check and compile the instruction skill", writes=True
    )
    p.add_argument("agent")
    p = g.command(
        "sync", _cmd_sync, help="Sync client-scope instructions into workstreams", writes=True
    )
    p.add_argument("client")
    p.add_argument("--agent")
    p.add_argument("--workstream", action="append", help="Extra workstream name (repeat)")

    p = registry.command(
        "build-exit", _cmd_build_exit, help="Build-phase exit criteria for an agent"
    )
    p.add_argument("agent")
    p.add_argument("--run", help="Blind run to judge (default: the latest complete one)")
    p.add_argument("--repro-target", type=float)
