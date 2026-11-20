"""The eval report (PLAN.md section 10): six sections, as JSON and as one self-contained HTML.

1 header, 2 release criteria against live and staged, 3 regressions, 4 misses by cause,
5 cost and performance, 6 what changed and why. The report holds ids, counts and scores,
never expected values, so it is safe to publish to the client status workstream. The HTML has
inline CSS only and no external URL.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any

from cli.builder_common.evals.gate import GateDecision, regressions
from cli.builder_common.evals.metrics import (
    RunResults,
    cost_and_performance,
    metric_value,
    misses_by_cause,
    pass_rate,
)
from cli.builder_common.evals.models import STATUS_ERROR, STATUS_PENDING
from cli.builder_common.evals.protocols import ArtifactStore, sha256_hex
from cli.builder_common.evals.runner import ops_path

SECTIONS = ("header", "criteria", "regressions", "misses_by_cause", "cost_performance", "changes")


def build_report(
    results: RunResults,
    *,
    decision: GateDecision | None = None,
    live: RunResults | None = None,
    changes: list[dict[str, str]] | None = None,
    accepted_regressions: dict[str, str] | None = None,
    generated_at: str | None = None,
    forced_reason: str | None = None,
) -> dict[str, Any]:
    header = {
        "agent": results.agent,
        "version": results.version,
        "run_id": results.run_id,
        "live_run_id": live.run_id if live else None,
        "dataset": results.dataset,
        "dataset_version": results.dataset_version,
        "tier": results.tier,
        "blind": results.blind,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "cases": len(results.cases),
        "passed": sum(1 for c in results.cases if c.passed is True),
        "errored": sum(1 for c in results.cases if c.status == STATUS_ERROR),
        "pending": sum(1 for c in results.cases if c.status == STATUS_PENDING),
        "pass_rate": pass_rate(results.cases),
        "open_gaps": results.open_gaps,
        "verdict": None if decision is None else ("pass" if decision.passed else "fail"),
        "forced_reason": forced_reason,
    }
    criteria = []
    for r in decision.results if decision else []:
        criteria.append(
            {
                "criteria_key": r.criteria_key,
                "metric": r.metric,
                "comparator": r.comparator,
                "target": r.target,
                "live": r.live_actual,
                "staged": r.actual,
                "passed": r.passed,
                "detail": r.detail,
            }
        )
    regs = regressions(results, live, accepted_regressions) if live else []
    misses = misses_by_cause(results.cases)
    miss_cases: dict[str, list[str]] = {}
    for c in results.cases:
        if c.passed is True or c.status == STATUS_PENDING:
            continue
        key = "error" if c.status == STATUS_ERROR else (c.miss_cause or "unclassified")
        miss_cases.setdefault(key, []).append(c.case_id)
    live_perf = cost_and_performance(live.cases) if live else None
    return {
        "header": header,
        "criteria": criteria,
        "regressions": {
            "compared_with": live.run_id if live else None,
            "cases": regs,
            "live_pass_rate": metric_value(live, "pass_rate") if live else None,
        },
        "misses_by_cause": {
            "counts": misses,
            "cases": {k: sorted(v) for k, v in miss_cases.items()},
        },
        "cost_performance": {"staged": cost_and_performance(results.cases), "live": live_perf},
        "changes": list(changes or []),
    }


def _esc(v: Any) -> str:
    if v is None:
        return "&ndash;"
    if isinstance(v, float):
        return html.escape(f"{v:.4g}")
    return html.escape(str(v))


def _table(head: list[str], rows: list[list[Any]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


_CSS = """
:root{--bg:#fff;--fg:#1d2433;--mut:#5b6474;--line:#d8dce5;--ok:#1a7f4b;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e6e9f0;--mut:#98a1b3;--line:#2c3342;--ok:#59c98d;--bad:#ff8a80}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto}
th,td{text-align:left;padding:4px 10px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}.pass{color:var(--ok)}.fail{color:var(--bad)}.mut{color:var(--mut)}
"""


def render_html(report: dict[str, Any]) -> str:
    h = report["header"]
    verdict = h["verdict"]
    vcls = {"pass": "pass", "fail": "fail"}.get(str(verdict), "mut")
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Eval report {html.escape(str(h['agent']))}</title><style>{_CSS}</style></head><body>",
        f"<h1>Eval report: {html.escape(str(h['agent']))} {html.escape(str(h['version']))}</h1>",
        f'<p class="{vcls}"><strong>Gate: {html.escape(str(verdict or "not evaluated"))}</strong>'
        + (f" (forced: {html.escape(str(h['forced_reason']))})" if h["forced_reason"] else "")
        + "</p>",
        "<h2>1. Header</h2>",
        _table(
            [
                "Run",
                "Dataset",
                "Tier",
                "Blind",
                "Cases",
                "Passed",
                "Errored",
                "Pending",
                "Pass rate",
                "Open gaps",
                "Generated",
            ],
            [
                [
                    h["run_id"],
                    f"{h['dataset']} v{h['dataset_version']}",
                    h["tier"],
                    "yes" if h["blind"] else "no",
                    h["cases"],
                    h["passed"],
                    h["errored"],
                    h["pending"],
                    h["pass_rate"],
                    h["open_gaps"],
                    h["generated_at"],
                ]
            ],
        ),
        "<h2>2. Release criteria: live and staged</h2>",
        _table(
            ["Criterion", "Metric", "Target", "Live", "Staged", "Result"],
            [
                [
                    c["criteria_key"],
                    c["metric"],
                    f"{c['comparator']} {c['target']:.4g}" if c["target"] is not None else "",
                    c["live"],
                    c["staged"],
                    "pass" if c["passed"] else "FAIL",
                ]
                for c in report["criteria"]
            ],
        ),
        "<h2>3. Regressions</h2>",
    ]
    reg = report["regressions"]
    if reg["compared_with"] is None:
        parts.append('<p class="mut">No live run to compare with.</p>')
    elif not reg["cases"]:
        parts.append(f'<p class="pass">None against {html.escape(str(reg["compared_with"]))}.</p>')
    else:
        parts.append(
            _table(["Case that passed on live and fails now"], [[c] for c in reg["cases"]])
        )
    parts.append("<h2>4. Misses by cause</h2>")
    parts.append(
        _table(
            ["Cause", "Count", "Cases"],
            [
                [k, v, ", ".join(report["misses_by_cause"]["cases"].get(k, []))]
                for k, v in report["misses_by_cause"]["counts"].items()
            ],
        )
        if report["misses_by_cause"]["counts"]
        else '<p class="pass">No misses.</p>'
    )
    perf = report["cost_performance"]
    keys = [
        "cases",
        "total_cost_usd",
        "avg_cost_usd",
        "total_duration_s",
        "avg_duration_s",
        "max_duration_s",
        "total_tokens",
        "total_steps",
        "hitl_count",
    ]
    parts.append("<h2>5. Cost and performance</h2>")
    parts.append(
        _table(
            ["Measure", "Staged", "Live"],
            [[k, perf["staged"][k], (perf["live"] or {}).get(k)] for k in keys],
        )
    )
    parts.append("<h2>6. What changed and why</h2>")
    parts.append(
        _table(["Change", "Why"], [[c.get("summary"), c.get("reason")] for c in report["changes"]])
        if report["changes"]
        else '<p class="mut">No changes recorded.</p>'
    )
    parts.append("</body></html>")
    return "".join(parts)


def write_report(store: ArtifactStore, report: dict[str, Any], run_id: str) -> dict[str, str]:
    """Store report JSON and HTML under builder-ops at a content-addressed (never reused) path."""
    blob = json.dumps(report, indent=2, sort_keys=True).encode()
    tag = sha256_hex(blob)[:12]
    jpath = ops_path(run_id, f"eval_report-{tag}.json")
    hpath = ops_path(run_id, f"eval_report-{tag}.html")
    store.put(jpath, blob)
    store.put(hpath, render_html(report).encode())
    return {"json": jpath, "html": hpath}
