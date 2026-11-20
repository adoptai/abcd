"""``abcd metric``, ``abcd check`` and ``abcd criteria``: what "good" means for an agent.

Definitions live in the agent bundle (``metrics.yaml``, ``release.yaml``) and in
``harness/checks/<check>.yaml`` and are mirrored into ``bld_metrics``, ``bld_checks`` and
``bld_release_criteria`` (PLAN.md section 10). The repo wins when the two disagree.
Lowering a release target (loosening the gate) needs ``--approved-by``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli import builder_eval as be
from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common import release_file as RF  # noqa: N812
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.metrics import metric_value
from cli.builder_common.evals.scorers import load_checks
from cli.builder_common.release_approval import refuse_self_asserted_in_cloud
from cli.core.context import Context
from cli.core.output import Result
from cli.core.registry import Registry

CATEGORIES = ("volume", "time", "quality", "cost")
SOURCES = ("check_results", "hitl_events", "user_actions", "run_timings", "cost")
OPS = (">=", "<=", "no_regression")
BUILTIN_METRICS = (
    "pass_rate",
    "blind_repro_rate",
    "open_gaps",
    "avg_cost_usd",
    "total_cost_usd",
    "avg_duration_s",
    "max_duration_s",
    "no_regression",
)
RULE_TYPES = ("present", "equals", "range", "regex")
SEVERITY_TO_YAML = {"blocking": "error", "warning": "warning"}
SEVERITY_FROM_YAML = {"error": "blocking", "warning": "warning"}
# The plan: every linked agent's criteria include these.
REQUIRED = (("blind_repro_rate", ">=", 1.0), ("open_gaps", "<=", 0.0))


def _num(text: str, what: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise UsageError(f"{what} must be a number, got {text!r}") from None


# --------------------------------------------------------------------------- metrics


def _metrics_path(ws: Path, agent: str) -> Path:
    return be.agent_dir(ws, agent) / "metrics.yaml"


def _metric_row(agent: str, m: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric_key": f"{agent}:{m['name']}@{m['version']}",
        "agent_key": agent,
        "name": m["name"],
        "category": m["category"],
        "unit": m.get("unit"),
        "formula": m.get("formula"),
        "source": m["source"],
        "baseline": m.get("baseline"),
        "is_gate": bool(m.get("gate")),
        "decision_ref": m.get("decision_ref"),
    }


def _cmd_metric_define(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    path = _metrics_path(ws, a.agent)
    data = be.read_yaml(path, {"metrics": []})
    targets: dict[str, float] = {}
    for t in a.target or []:
        phase, sep, val = t.partition("=")
        if not sep or not phase:
            raise UsageError(f"--target expects phase=value, got {t!r}")
        targets[phase] = _num(val, "target")
    old = next((m for m in data["metrics"] if m["name"] == a.name), None)
    metric = {
        "name": a.name,
        "version": (int(old["version"]) + 1) if old else 1,
        "category": a.category,
        "unit": a.unit,
        "formula": a.formula,
        "source": a.source,
        "baseline": a.baseline,
        "targets": targets,
        "gate": bool(a.gate),
        "decision_ref": a.decision_ref,
        "defined_at": be.now_iso(),
    }
    if ctx.dry_run_notice(f"define metric {a.name} v{metric['version']} for {a.agent}"):
        return Result.done({"dry_run": True})
    data["metrics"] = [m for m in data["metrics"] if m["name"] != a.name] + [metric]
    be.write_yaml(path, data)
    svc = be.get_services(ctx)
    row = _metric_row(a.agent, metric)
    svc.rows.insert("bld_metrics", row)
    done = be.commit_work(
        ws,
        f"{a.agent}: metric {a.name} v{metric['version']}",
        [f"harness/agents/{a.agent}"],
        topic=f"{a.agent}-metric-{a.name}",
    )
    return Result.done(
        {
            "metric": metric,
            "metric_key": row["metric_key"],
            "commit": done.sha,
            "branch": done.branch,
        },
        f"Defined {a.name} v{metric['version']} for {a.agent} ({row['metric_key']})." + done.note,
    )


def _cmd_metric_list(ctx: Context) -> Result:
    data = be.read_yaml(_metrics_path(ctx.env_dir, ctx.args.agent), {"metrics": []})
    ms = data["metrics"]
    text = "\n".join(
        f"{m['name']} v{m['version']}  {m['category']}  {m.get('unit') or ''}  "
        f"formula={m.get('formula')}  gate={bool(m.get('gate'))}"
        for m in ms
    )
    return Result.done({"agent": ctx.args.agent, "metrics": ms}, text or "No metrics defined.")


def _cmd_metric_compute(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    results = be.load_results(ws, a.agent, a.run)
    if results.agent != a.agent:
        raise UsageError(f"run {a.run} belongs to agent {results.agent!r}")
    ms = be.read_yaml(_metrics_path(ws, a.agent), {"metrics": []})["metrics"]
    if ctx.dry_run_notice(f"compute {len(ms)} metric(s) from {a.run}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    values = []
    for m in ms:
        key = f"{a.agent}:{m['name']}@{m['version']}"
        v = metric_value(results, m["formula"]) if m.get("formula") else None
        target = (m.get("targets") or {}).get(a.phase)
        values.append({"metric_key": key, "name": m["name"], "value": v, "target": target})
        if v is not None:
            svc.rows.insert(
                "bld_metric_values",
                {
                    "value_key": f"{a.run}:{key}",
                    "metric_key": key,
                    "agent_key": a.agent,
                    "version_id": results.version,
                    "scope": "eval_run",
                    "scope_ref": a.run,
                    "value": v,
                    "computed_at": be.now_iso(),
                },
            )
    text = "\n".join(
        f"{v['name']}: "
        + ("not computable from an eval run" if v["value"] is None else f"{v['value']:.4g}")
        for v in values
    )
    return Result.done({"run_id": a.run, "values": values}, text or "No metrics defined.")


# --------------------------------------------------------------------------- checks


def _check_rule(c: dict[str, Any]) -> dict[str, Any]:
    if c["kind"] == "deterministic":
        return dict(c["rule"])
    if c["kind"] == "goal_reached":
        return dict(c["goal"])
    return {"rubric": c["rubric"]}


def _check_row(agent: str, c: dict[str, Any]) -> dict[str, Any]:
    return {
        "check_key": c["check_id"],
        "agent_key": agent,
        "title": c.get("title"),
        "kind": c["kind"],
        "rule": _check_rule(c),
        "severity": SEVERITY_FROM_YAML[c["severity"]],
        "source_decision": c.get("source_decision"),
    }


def _parse_value(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _build_rule(a: Any) -> dict[str, Any]:
    if not a.rule_type or not a.field:
        raise UsageError("a deterministic check needs --rule-type and --field")
    rule: dict[str, Any] = {"type": a.rule_type, "field": a.field}
    if a.rule_type == "equals":
        if a.value is None:
            raise UsageError("equals needs --value")
        rule["value"] = _parse_value(a.value)
    elif a.rule_type == "range":
        if a.min is None and a.max is None:
            raise UsageError("range needs --min and/or --max")
        if a.min is not None:
            rule["min"] = a.min
        if a.max is not None:
            rule["max"] = a.max
    elif a.rule_type == "regex":
        if not a.pattern:
            raise UsageError("regex needs --pattern")
        rule["pattern"] = a.pattern
    return rule


def _cmd_check_add(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    be.agent_dir(ws, a.agent)
    path = ws / "harness" / "checks" / f"{a.check_id}.yaml"
    if path.exists():
        prev = be.read_yaml(path, {})
        if prev.get("agent") != a.agent:
            raise RefusedError(f"check {a.check_id!r} belongs to agent {prev.get('agent')!r}")
        if not a.replace:
            raise RefusedError(f"check {a.check_id!r} exists; pass --replace to change it")
    check: dict[str, Any] = {
        "check_id": a.check_id,
        "agent": a.agent,
        "title": a.title or a.check_id,
        "kind": a.kind,
        "severity": SEVERITY_TO_YAML[a.severity],
        "source_decision": a.decision,
    }
    if a.kind == "judged":
        if not a.rubric:
            raise UsageError("a judged check needs --rubric")
        check["rubric"] = a.rubric
    else:
        check["rule"] = _build_rule(a)
    if ctx.dry_run_notice(f"add check {a.check_id} to {a.agent}"):
        return Result.done({"dry_run": True})
    be.write_yaml(path, check)
    svc = be.get_services(ctx)
    row = _check_row(a.agent, check)
    svc.rows.insert("bld_checks", {**row, "active_from": be.now_iso()})
    svc.rows.update("bld_checks", a.check_id, row)
    done = be.commit_work(
        ws,
        f"{a.agent}: check {a.check_id}",
        [f"harness/checks/{a.check_id}.yaml"],
        topic=f"{a.agent}-check-{a.check_id}",
    )
    return Result.done(
        {"check": check, "commit": done.sha, "branch": done.branch},
        f"Added {a.kind} check {a.check_id} ({a.severity}). Re-run past evals to re-score them."
        + done.note,
    )


def _cmd_check_list(ctx: Context) -> Result:
    checks = [
        c
        for c in load_checks(ctx.env_dir, include_drafts=True)
        if not ctx.args.agent or c.get("agent") == ctx.args.agent
    ]
    text = "\n".join(
        f"{c['check_id']}  {c.get('kind', 'deterministic')}"
        f"{' (draft)' if c.get('status') == 'draft' else ''}  "
        f"{SEVERITY_FROM_YAML.get(str(c.get('severity', 'error')), c.get('severity'))}  "
        f"agent={c.get('agent')}"
        for c in checks
    )
    return Result.done({"checks": checks}, text or "No checks.")


# --------------------------------------------------------------------------- criteria


def _loosens(old: dict[str, Any], op: str, target: float | None) -> bool:
    if old["comparator"] == "no_regression":
        return op != "no_regression"
    if op == "no_regression" or op != old["comparator"]:
        return True
    old_t, new_t = float(old["target"]), float(target if target is not None else 0.0)
    return new_t < old_t if op == ">=" else new_t > old_t


def _criteria_row(agent: str, dataset: str, c: dict[str, Any]) -> dict[str, Any]:
    return {
        "criteria_key": c["criteria_key"],
        "agent_key": agent,
        "applies_from_version": "",
        "dataset_key": dataset,
        "dataset_version": c["dataset_version"],
        "metric_key": c["metric"],
        "comparator": c["comparator"],
        "target": c.get("target"),
        "set_by": c.get("set_by"),
        "decision_ref": c.get("decision_ref"),
    }


def _read_release(path: Path) -> dict[str, Any]:
    """``release.yaml`` in the one schema; the first (per-criterion ``op``/``dataset``) form is
    rewritten on read."""
    data = be.read_yaml(path, {"criteria": []})
    data.setdefault("criteria", [])
    try:
        return RF.upgrade_legacy(data)
    except ValueError as e:
        raise UsageError(f"{path}: {e}") from e


def _validate_metric(ws: Path, agent: str, metric: str) -> None:
    defined = {
        m["name"] for m in be.read_yaml(_metrics_path(ws, agent), {"metrics": []})["metrics"]
    }
    if metric.partition(":")[0] not in BUILTIN_METRICS and metric not in defined:
        raise UsageError(
            f"unknown metric {metric!r}: define it with `abcd metric define` "
            f"or use one of {', '.join(BUILTIN_METRICS)}"
        )


def _cmd_criteria_set(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    refuse_self_asserted_in_cloud(ws, approved_by=a.approved_by, by=getattr(a, "by", None))
    d = be.agent_dir(ws, a.agent)
    ds = D.load_dataset(ws, a.dataset)
    op = a.op
    metric = "no_regression" if op == "no_regression" else a.metric
    if not metric:
        raise UsageError("--metric is required")
    if op != "no_regression" and a.target is None:
        raise UsageError("--target is required")
    _validate_metric(ws, a.agent, metric)
    target = None if op == "no_regression" else float(a.target)
    path = d / "release.yaml"
    data = _read_release(path)
    cur_name, _ = RF.split_dataset(data.get("dataset"))
    if cur_name and cur_name != a.dataset:
        raise RefusedError(
            f"{a.agent}: release.yaml is for dataset {cur_name!r}; an agent releases against "
            f"one dataset, so criteria on {a.dataset!r} are refused"
        )
    who = be.actor(a, ctx.env_dir)
    approver = a.approved_by.strip() if a.approved_by else None

    def upsert(metric_: str, op_: str, target_: float | None, auto: bool) -> dict[str, Any]:
        key = f"{a.agent}:{a.dataset}:{metric_}"
        old = next((c for c in data["criteria"] if c["criteria_key"] == key), None)
        if old is not None and _loosens(old, op_, target_) and not approver:
            raise RefusedError(
                f"criterion {key} would be loosened ({old['comparator']} {old.get('target')} -> "
                f"{op_} {target_}); pass --approved-by with the lead's name"
            )
        entry = {
            "criteria_key": key,
            "metric": metric_,
            "comparator": op_,
            "target": target_,
            "dataset_version": a.dataset_version or ds.version,
            "set_by": approver or who,
            "decision_ref": a.decision_ref,
            "auto": auto,
            "history": (old or {}).get("history", [])
            + [
                {
                    "at": be.now_iso(),
                    "by": who,
                    "approved_by": approver,
                    "comparator": op_,
                    "target": target_,
                }
            ],
        }
        data["criteria"] = [c for c in data["criteria"] if c["criteria_key"] != key] + [entry]
        return entry

    if ctx.dry_run_notice(f"set criterion {metric} {op} {target} for {a.agent} on {a.dataset}"):
        return Result.done({"dry_run": True})
    entries = [upsert(metric, op, target, False)]
    have = {c["metric"] for c in data["criteria"]}
    for m, o, t in REQUIRED:
        if m not in have:
            entries.append(upsert(m, o, t, True))
            continue
        # An automatic criterion keeps the dataset version of the agent's release.yaml: re-pin
        # it when the dataset has moved on (it was added when the dataset was still empty).
        cur = next(c for c in data["criteria"] if c["metric"] == m)
        if cur.get("auto") and cur.get("dataset_version") != (a.dataset_version or ds.version):
            cur["dataset_version"] = a.dataset_version or ds.version
            entries.append(cur)
    data = {
        "agent": a.agent,
        "dataset": RF.join_dataset(a.dataset, a.dataset_version or ds.version),
        "criteria": data["criteria"],
    }
    be.write_yaml(path, data)
    svc = be.get_services(ctx)
    be.mirror_rows(
        svc.rows,
        "bld_release_criteria",
        [_criteria_row(a.agent, a.dataset, e) for e in entries],
        "criteria_key",
    )
    done = be.commit_work(
        ws,
        f"{a.agent}: release criteria",
        [f"harness/agents/{a.agent}"],
        topic=f"{a.agent}-criteria",
    )
    text = "\n".join(
        f"{e['criteria_key']}: {e['comparator']} {e['target'] if e['target'] is not None else ''}"
        + (" (required for linked agents)" if e["auto"] else "")
        for e in entries
    )
    return Result.done(
        {"criteria": entries, "commit": done.sha, "branch": done.branch}, text + done.note
    )


def _cmd_criteria_show(ctx: Context) -> Result:
    ws = ctx.env_dir
    a = ctx.args
    data = _read_release(be.agent_dir(ws, a.agent) / "release.yaml")
    crit = data["criteria"]
    ds_name, _ = RF.split_dataset(data.get("dataset"))
    missing = (
        [f"{ds_name}:{m}" for m, _, _ in REQUIRED if not any(c["metric"] == m for c in crit)]
        if crit
        else []
    )
    stale = []
    for c in crit:
        try:
            cur = D.load_dataset(ws, ds_name).version
        except UsageError:
            continue
        if c.get("dataset_version") is not None and int(c["dataset_version"]) != cur:
            stale.append(f"{c['criteria_key']} (pinned v{c['dataset_version']}, dataset is v{cur})")
    text = "\n".join(
        f"{c['criteria_key']}: {c['comparator']} "
        f"{c.get('target') if c.get('target') is not None else ''}"
        f"  dataset v{c.get('dataset_version')}"
        for c in crit
    )
    return Result.done(
        {"agent": a.agent, "criteria": crit, "missing_required": missing, "stale": stale},
        (text or "No release criteria.")
        + (f"\nMissing required: {', '.join(missing)}" if missing else "")
        + (f"\nStale dataset version: {'; '.join(stale)}" if stale else ""),
    )


# --------------------------------------------------------------------------- registration


def register(registry: Registry) -> None:
    g = registry.group("metric", "Metric definitions")
    p = g.command("define", _cmd_metric_define, help="Define (or version) a metric", writes=True)
    p.add_argument("agent")
    p.add_argument("name")
    p.add_argument("--category", required=True, choices=list(CATEGORIES))
    p.add_argument("--unit", default="")
    p.add_argument("--formula", help="A built-in metric (e.g. pass_rate, pass_rate:tag=known)")
    p.add_argument("--source", required=True, choices=list(SOURCES))
    p.add_argument("--baseline", type=float)
    p.add_argument("--target", action="append", help="phase=value (repeat)")
    p.add_argument("--gate", action="store_true")
    p.add_argument("--decision-ref", help="Client sign-off decision")
    p = g.command("list", _cmd_metric_list, help="List an agent's metrics")
    p.add_argument("agent")
    p = g.command(
        "compute", _cmd_metric_compute, help="Compute metrics from an eval run", writes=True
    )
    p.add_argument("agent")
    p.add_argument("--run", required=True)
    p.add_argument("--phase", default="build")

    g = registry.group("check", "Client checks")
    p = g.command("add", _cmd_check_add, help="Add a deterministic or judged check", writes=True)
    p.add_argument("agent")
    p.add_argument("check_id")
    p.add_argument("--title")
    p.add_argument("--kind", default="deterministic", choices=["deterministic", "judged"])
    p.add_argument("--severity", default="blocking", choices=list(SEVERITY_TO_YAML))
    p.add_argument("--decision", help="Source decision id")
    p.add_argument("--rule-type", choices=list(RULE_TYPES))
    p.add_argument("--field")
    p.add_argument("--value")
    p.add_argument("--min", type=float)
    p.add_argument("--max", type=float)
    p.add_argument("--pattern")
    p.add_argument("--rubric", help="Judged checks: the rubric text")
    p.add_argument("--replace", action="store_true")
    p = g.command("list", _cmd_check_list, help="List checks")
    p.add_argument("agent", nargs="?")

    g = registry.group("criteria", "Release criteria")
    p = g.command("set", _cmd_criteria_set, help="Set a release criterion", writes=True)
    p.add_argument("agent")
    p.add_argument("--dataset", required=True)
    p.add_argument("--metric")
    p.add_argument("--op", required=True, choices=list(OPS))
    p.add_argument("--target", type=float)
    p.add_argument("--dataset-version", type=int)
    p.add_argument("--decision-ref")
    p.add_argument("--approved-by", help="Lead approving a lowered target")
    p = g.command("show", _cmd_criteria_show, help="Show an agent's release criteria")
    p.add_argument("agent")
