"""Status and reports (PLAN.md section 15).

* ``abcd status <agent>``: read-only summary (versions, criteria, last eval, open items, drift).
* ``abcd status publish [--freeze]``: render the client's status files with ``status.publish_plan``
  and upload them to ``builder-status-<client>``: pointer files rewritten on purpose, dated copies
  written once.
* ``abcd report daily [--fde NAME]`` and ``abcd report scorecard``: rendered from table rows.
  ``--send slack|gmail`` only prints the message and the integration call it would make.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from cli.builder_common.docstore import ConflictError, DocStore
from cli.builder_common.errors import UsageError
from cli.builder_common.explain import CriterionRow, OpenItem
from cli.builder_common.status import (
    AgentStatus,
    DailyReportInput,
    PublishEntry,
    ReleaseRow,
    ScorecardInput,
    publish_plan,
    render_agent_status_md,
    render_daily_report_md,
    render_scorecard_md,
)
from cli.builder_context import (
    OPS_STORE,
    STATUS_STORE_PREFIX,
    as_float,
    check_name,
    open_db,
    open_docs,
    today,
)

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.output import Result
    from cli.core.registry import Registry

LOOP_STEPS = {
    1: "Build or refine",
    2: "Local evals",
    3: "Review",
    4: "Run on the harness",
    5: "Performance review",
    6: "Blind evals",
    7: "Close context gaps",
    8: "Freeze as staged",
    9: "Test staged",
    10: "Publish",
}
COMMAND_STEP = {
    "context": 1,
    "kg": 1,
    "plan": 1,
    "noui": 1,
    "eval": 2,
    "review": 3,
    "skill": 4,
    "push": 4,
    "run": 4,
    "trace": 5,
    "gap": 7,
    "instructions": 7,
    "stage": 8,
    "test-staged": 9,
    "release": 10,
}
_LOWER_IS_BETTER = ("time", "cost")
MAX_RELEASES = 10


def step_for(command: str) -> int:
    return COMMAND_STEP.get(command.split(" ", 1)[0], 0)


def version_number(version_id: str) -> int:
    m = re.search(r"@v(\d+)$", version_id)
    return int(m.group(1)) if m else 0


def criterion_row(
    crit: Mapping[str, Any], metric: Mapping[str, Any] | None, value: Any
) -> CriterionRow:
    """One release criterion with its current value and whether it is met."""
    metric = metric or {}
    name = str(metric.get("name") or crit.get("metric_key") or crit.get("criteria_key"))
    unit = str(metric.get("unit") or "")
    comparator = str(crit.get("comparator") or "")
    target = as_float(crit.get("target"))
    baseline = as_float(metric.get("baseline"))
    current = as_float(value)
    if comparator == "no_regression":
        target_text = (
            f"no worse than baseline {baseline:g}{unit}"
            if baseline is not None
            else "no regression"
        )
        limit, lower = baseline, str(metric.get("category")) in _LOWER_IS_BETTER
    else:
        target_text = f"{comparator} {target:g}{unit}" if target is not None else comparator
        limit, lower = target, comparator == "<="
    met = False
    if current is not None and limit is not None:
        met = current <= limit if lower else current >= limit
    return CriterionRow(
        name=name,
        target=target_text,
        current="not measured" if current is None else f"{current:g}{unit}",
        met=met,
    )


def latest_row(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any] | None:
    ordered = sorted(rows, key=lambda r: str(r.get(key) or ""))
    return dict(ordered[-1]) if ordered else None


def build_criteria(
    criteria: Sequence[Mapping[str, Any]],
    metrics: Sequence[Mapping[str, Any]],
    values: Sequence[Mapping[str, Any]],
) -> tuple[CriterionRow, ...]:
    by_metric = {str(m.get("metric_key")): m for m in metrics}
    out: list[CriterionRow] = []
    for crit in criteria:
        mk = str(crit.get("metric_key"))
        latest = latest_row([v for v in values if v.get("metric_key") == mk], "computed_at")
        out.append(criterion_row(crit, by_metric.get(mk), latest.get("value") if latest else None))
    return tuple(out)


def open_item_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[OpenItem, ...]:
    return tuple(
        OpenItem(
            title=str(r.get("title") or ""),
            owner="client" if r.get("waiting_on") == "client" else "adopt",
            due=str(r.get("due") or "")[:10],
        )
        for r in rows
        if r.get("status", "open") == "open"
    )


def _group(
    rows: Sequence[Mapping[str, Any]], key: str = "agent_key"
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[str(r.get(key))].append(dict(r))
    return out


def build_scorecard(db: Any, client: str, as_of: str) -> ScorecardInput:
    metrics = db.list_all("bld_metrics")
    values = db.list_all("bld_metric_values")
    criteria = _group(db.list_all("bld_release_criteria"))
    items = _group(db.list_all("bld_open_items"))
    agents = []
    for row in sorted(db.list_all("bld_agents"), key=lambda r: str(r.get("agent_key"))):
        key = str(row.get("agent_key"))
        agent_metrics = [m for m in metrics if m.get("agent_key") == key]
        agent_values = [v for v in values if v.get("agent_key") == key]
        agents.append(
            AgentStatus(
                agent=key,
                display_name=str(row.get("name") or ""),
                live_version=str(row.get("live_version") or ""),
                staged_version=str(row.get("staged_version") or ""),
                criteria=build_criteria(criteria.get(key, []), agent_metrics, agent_values),
                open_items=open_item_rows(items.get(key, [])),
            )
        )
    releases = sorted(
        db.list_all("bld_releases"), key=lambda r: str(r.get("released_at") or ""), reverse=True
    )
    return ScorecardInput(
        client=client,
        as_of=as_of,
        agents=tuple(agents),
        releases=tuple(
            ReleaseRow(
                agent=str(r.get("agent_key")),
                version=str(r.get("version_id") or "").rsplit("@", 1)[-1],
                score=str(r.get("gate_result") or ""),
                date=str(r.get("released_at") or "")[:10],
            )
            for r in releases[:MAX_RELEASES]
        ),
    )


def frozen_agent_copies(card: ScorecardInput) -> dict[str, str]:
    """A dated, frozen copy of every agent's status page (history matters)."""
    return {
        f"agents/{a.agent}/history/{card.as_of}.md": render_agent_status_md(a) for a in card.agents
    }


# ---------------------------------------------------------------------------------------------
# status <agent>
# ---------------------------------------------------------------------------------------------


def agent_summary(db: Any, agent: str) -> dict[str, Any]:
    versions = sorted(
        (
            r
            for r in db.find_rows("bld_versions", "object_name", agent)
            if r.get("object_kind") == "agent"
        ),
        key=lambda r: version_number(str(r.get("version_id"))),
    )
    metrics = db.find_rows("bld_metrics", "agent_key", agent)
    values = db.find_rows("bld_metric_values", "agent_key", agent)
    criteria = build_criteria(
        db.find_rows("bld_release_criteria", "agent_key", agent), metrics, values
    )
    run = latest_row(db.find_rows("bld_eval_runs", "agent_key", agent), "started_at")
    items = open_item_rows(db.find_rows("bld_open_items", "agent_key", agent))
    registry = db.find_rows("bld_agents", "agent_key", agent)
    row = registry[0] if registry else {}
    return {
        "agent": agent,
        "registered": bool(registry),
        "phase": row.get("phase"),
        "live_version": row.get("live_version"),
        "staged_version": row.get("staged_version"),
        "versions": [
            {
                "version_id": v.get("version_id"),
                "state": v.get("state"),
                "git_sha": v.get("git_sha"),
            }
            for v in versions
        ],
        "criteria": [c.__dict__ for c in criteria],
        "last_eval": None
        if run is None
        else {
            k: run.get(k)
            for k in ("eval_run_id", "version_id", "tier", "blind", "status", "started_at")
        },
        "open_items": [i.__dict__ for i in items],
    }


def agent_drift(ctx: Context, db: Any, agent: str) -> dict[str, Any]:
    """Drift of this agent's live and staged objects. Failure to check is reported, not raised."""
    from cli.builder_common.drift import audit_drift
    from cli.builder_common.versions import VersionLedger

    try:
        report = audit_drift(ctx.harness_client(), VersionLedger(db))
    except Exception as exc:
        return {"checked": False, "error": f"{type(exc).__name__}: {exc}", "items": []}
    names = {agent, f"{agent}-staged"}
    mine = [i.to_dict() for i in report.items if i.name in names]
    return {"checked": True, "has_drift": bool(mine), "items": mine}


def _status_text(s: dict[str, Any]) -> str:
    lines = [
        f"{s['agent']}: phase {s.get('phase') or 'unknown'}, live {s.get('live_version') or 'none'}, "
        f"staged {s.get('staged_version') or 'none'}",
        f"  versions: {len(s['versions'])}"
        + "".join(f"\n    {v['version_id']} ({v['state']})" for v in s["versions"][-5:]),
        "  criteria: "
        + (
            ", ".join(
                f"{c['name']} {'met' if c['met'] else 'not met'} ({c['current']})"
                for c in s["criteria"]
            )
            or "none"
        ),
    ]
    ev = s["last_eval"]
    lines.append(
        "  last eval: " + (f"{ev['eval_run_id']} {ev['status']} ({ev['tier']})" if ev else "none")
    )
    lines.append("  open items: " + (", ".join(i["title"] for i in s["open_items"]) or "none"))
    d = s["drift"]
    lines.append(
        "  drift: "
        + (
            "not checked (" + d["error"] + ")"
            if not d.get("checked")
            else (f"{len(d['items'])} item(s)" if d["items"] else "none")
        )
    )
    return "\n".join(lines)


def _cmd_status(ctx: Context) -> Result:
    from cli.core.output import Result

    target = ctx.args.target
    if target == "publish":
        return _publish(ctx)
    agent = check_name(target, "agent")
    db = open_db(ctx)
    summary = agent_summary(db, agent)
    summary["drift"] = (
        {"checked": False, "items": [], "error": "skipped (--no-drift)"}
        if ctx.args.no_drift
        else agent_drift(ctx, db, agent)
    )
    return Result.done(summary, _status_text(summary))


# ---------------------------------------------------------------------------------------------
# status publish
# ---------------------------------------------------------------------------------------------


def _client_slug(ctx: Context) -> str:
    """``--client``, else the workspace's client (env.json ``client``, as ``builder setup`` used
    for the store names), else the env name. The env name alone looked for
    ``builder-status-<env>``, which is not the store ``setup`` made (live, 2026-10-29)."""
    from cli.builder_setup import client_name

    return client_name(ctx.env_dir, ctx.args.client)


def upload_plan(docs: DocStore, client: str, plan: Sequence[PublishEntry]) -> list[dict[str, Any]]:
    store = f"{STATUS_STORE_PREFIX}{client}"
    out: list[dict[str, Any]] = []
    for entry in plan:
        path = f"{store}/{entry.path}"
        try:
            docs.put_text(path, entry.content, replace=not entry.frozen)
            state = "written"
        except ConflictError:
            state = "exists"  # a frozen copy is written once
        out.append({"path": path, "frozen": entry.frozen, "state": state})
    return out


def _publish(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    client = check_name(_client_slug(ctx), "client")
    as_of = a.as_of or today()
    card = build_scorecard(open_db(ctx), client, as_of)
    extra = frozen_agent_copies(card) if a.freeze else None
    plan = publish_plan(card, freeze=a.freeze, extra_frozen=extra)
    store = f"{STATUS_STORE_PREFIX}{client}"
    if ctx.dry_run_notice(
        f"would write {len(plan)} file(s) to {store}: " + ", ".join(e.path for e in plan)
    ):
        return Result.done(
            {
                "dry_run": True,
                "store": store,
                "files": [{"path": e.path, "frozen": e.frozen} for e in plan],
            }
        )
    results = upload_plan(open_docs(ctx), client, plan)
    return Result.done(
        {"store": store, "as_of": as_of, "files": results},
        f"Published {len(results)} file(s) to {store}"
        + "".join(
            f"\n  {r['path']} ({'frozen, ' if r['frozen'] else 'pointer, '}{r['state']})"
            for r in results
        ),
    )


# ---------------------------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------------------------


def payload_of(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return dict(raw) if isinstance(raw, dict) else {}


def build_daily_reports(
    events: Sequence[Mapping[str, Any]],
    releases: Sequence[Mapping[str, Any]],
    day: str,
    fde: str | None,
) -> list[DailyReportInput]:
    """One report per FDE with events on ``day`` (or just ``fde``). Cloud actors are skipped."""
    by_actor: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        who = str(e.get("actor") or "")
        if str(e.get("ts") or "").startswith(day) and who and not who.startswith("cloud:"):
            by_actor[who].append(e)
    names = [fde] if fde else sorted(by_actor)
    reports = []
    for who in names:
        rows = by_actor.get(who, [])
        minutes: dict[str, float] = defaultdict(float)
        agents: set[str] = set()
        stuck: list[str] = []
        spend = {"local": 0.0, "cloud": 0.0}
        for e in rows:
            payload = payload_of(e)
            step = int(e.get("step") or 0)
            minutes[LOOP_STEPS.get(step, "Other")] += float(payload.get("duration_ms") or 0) / 60000
            if payload.get("agent"):
                agents.add(str(payload["agent"]))
            if e.get("result") == "fail":
                line = f"{payload.get('command') or e.get('kind')} failed (exit {payload.get('exit_code', '?')})"
                if line not in stuck:
                    stuck.append(line)
            spend["cloud" if e.get("ran_in") == "cloud" else "local"] += (
                as_float(e.get("cost_usd")) or 0.0
            )
        rel = tuple(
            ReleaseRow(
                agent=str(r.get("agent_key")),
                version=str(r.get("version_id") or "").rsplit("@", 1)[-1],
                score=str(r.get("gate_result") or ""),
                date=day,
            )
            for r in releases
            if str(r.get("released_at") or "").startswith(day) and r.get("approved_by") == who
        )
        reports.append(
            DailyReportInput(
                fde=who,
                day=day,
                agents_touched=tuple(sorted(agents)),
                step_minutes=dict(minutes),
                releases=rel,
                spend_fde_usd=spend["local"],
                spend_cloud_usd=spend["cloud"],
                stuck=tuple(stuck[:5]),
            )
        )
    return reports


def send_preview(
    channel: str | None, subject: str, body: str, to: str | None
) -> dict[str, Any] | None:
    """The integration call ``--send`` would make. Nothing is sent: abcd never calls it."""
    if channel is None:
        return None
    if channel == "slack":
        return {
            "integration": "slack",
            "tool": "slack_send_message",
            "args": {"channel": to or "", "message": body},
        }
    return {
        "integration": "gmail",
        "tool": "send_message",
        "args": {"to": to or "", "subject": subject, "body": body},
    }


def _send_text(call: dict[str, Any] | None, body: str) -> str:
    if call is None:
        return body
    return (
        f"{body}\n---\nNot sent. --send {call['integration']} would call "
        f"{call['integration']}.{call['tool']} with these arguments (message body above)."
    )


def _cmd_report_daily(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    day = a.date or today()
    db = open_db(ctx)
    events = db.list_all("bld_dev_events")
    reports = build_daily_reports(events, db.list_all("bld_releases"), day, a.fde)
    if not reports:
        return Result.done({"day": day, "reports": []}, f"No activity recorded for {day}.")
    docs: DocStore | None = None
    out: list[dict[str, Any]] = []
    texts: list[str] = []
    for rep in reports:
        body = render_daily_report_md(rep)
        path = f"{OPS_STORE}/reports/daily/{day}/{check_name(rep.fde, 'fde')}.md"
        entry: dict[str, Any] = {"fde": rep.fde, "path": path, "uploaded": False, "markdown": body}
        call = send_preview(a.send, f"Daily report: {rep.fde}, {day}", body, a.to)
        if call:
            entry["would_send"] = call
        if not a.no_upload and not ctx.dry_run:
            try:
                docs = docs or open_docs(ctx)
                docs.put_text(path, body)
                entry["uploaded"] = True
            except ConflictError:
                entry["uploaded"] = True  # a daily file is written once
            except UsageError as exc:
                entry["note"] = f"not stored: {exc}"
        out.append(entry)
        texts.append(_send_text(call, body))
    return Result.done({"day": day, "reports": out}, "\n\n".join(texts))


def _cmd_report_scorecard(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    client = check_name(_client_slug(ctx), "client")
    card = build_scorecard(open_db(ctx), client, a.as_of or today())
    body = render_scorecard_md(card)
    call = send_preview(a.send, f"{client}: scorecard {card.as_of}", body, a.to)
    return Result.done(
        {"client": client, "as_of": card.as_of, "markdown": body, "would_send": call},
        _send_text(call, body),
    )


def register(registry: Registry) -> None:
    p = registry.command(
        "status",
        _cmd_status,
        help="Agent status summary, or `status publish` for client status files",
        writes=True,
    )
    p.add_argument("target", help="An agent name, or `publish`")
    p.add_argument(
        "--freeze", action="store_true", help="publish: also write the dated, frozen copies"
    )
    p.add_argument("--client", help="publish: client name (default: the workspace)")
    p.add_argument("--as-of", help="publish: date for the frozen copies (default today)")
    p.add_argument("--no-drift", action="store_true", help="Skip the live drift check")

    g = registry.group("report", "Daily FDE report and client scorecard")
    p = g.command(
        "daily", _cmd_report_daily, help="Daily FDE report from bld_dev_events", writes=True
    )
    p.add_argument("--fde", help="One FDE (default: everyone active that day)")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.add_argument(
        "--send", choices=("slack", "gmail"), help="Print the message and the call it would make"
    )
    p.add_argument("--to", help="Slack channel or email address for --send")
    p.add_argument(
        "--no-upload", action="store_true", help="Do not store the report in builder-ops"
    )
    p = g.command("scorecard", _cmd_report_scorecard, help="Render the client scorecard from rows")
    p.add_argument("--client")
    p.add_argument("--as-of")
    p.add_argument("--send", choices=("slack", "gmail"))
    p.add_argument("--to")
