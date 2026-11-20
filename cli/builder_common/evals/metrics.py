"""Metrics over eval results (PLAN.md sections 10 and 11).

All functions take plain `CaseResult` lists and return numbers or ``None`` when a value cannot
be computed (so the gate can refuse instead of guessing).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cli.builder_common.evals.models import (
    MISS_CAUSES,
    STATUS_ERROR,
    STATUS_PENDING,
    CaseResult,
)

# Gap statuses that still need work. The plan's exit criteria name open, asked and encoded;
# an answered-but-not-yet-encoded gap is just as unfinished, so it counts too.
UNRESOLVED_GAP_STATUSES = ("open", "asked", "answered", "encoded")


@dataclass
class RunResults:
    """The scored outcome of one run, as the gate and the report consume it."""

    run_id: str
    agent: str
    dataset: str
    dataset_version: int
    blind: bool
    cases: list[CaseResult]
    tier: str = "staged"
    version: str = ""
    open_gaps: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def case(self, case_id: str) -> CaseResult | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


def pass_rate(
    cases: Iterable[CaseResult], *, tag: str | None = None, source: str | None = None
) -> float | None:
    """Passed / total. Errors, asked and pending cases count as not passed."""
    sel = [
        c
        for c in cases
        if (tag is None or tag in c.tags) and (source is None or c.source == source)
    ]
    if not sel:
        return None
    return sum(1 for c in sel if c.passed is True) / len(sel)


def blind_repro_rate(results: RunResults) -> float | None:
    """Share of cases reproduced by a blind run. None for a run that was not blind."""
    if not results.blind:
        return None
    return pass_rate(results.cases)


def open_gaps(gaps: Iterable[dict[str, Any]], owner: str | None = None) -> int:
    return sum(
        1
        for g in gaps
        if g.get("status") in UNRESOLVED_GAP_STATUSES and (owner is None or g.get("owner") == owner)
    )


def open_gaps_by_owner(gaps: Iterable[dict[str, Any]]) -> dict[str, int]:
    out: Counter[str] = Counter()
    for g in gaps:
        if g.get("status") in UNRESOLVED_GAP_STATUSES:
            out[str(g.get("owner") or "fde")] += 1
    return dict(sorted(out.items()))


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def time_to_close_gap(gap: dict[str, Any]) -> float | None:
    """Seconds from opening to the verified transition (None while not verified)."""
    if gap.get("status") != "verified":
        return None
    hist = gap.get("history", [])
    closed = next((h for h in reversed(hist) if h.get("status") == "verified"), None)
    opened = hist[0]["at"] if hist else gap.get("opened_at")
    if not closed or not opened:
        return None
    return (_parse(closed["at"]) - _parse(opened)).total_seconds()


def instructions_per_agent(instructions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i in instructions:
        slot = out.setdefault(str(i.get("agent", "")), {"count": 0, "gaps_closed": 0})
        slot["count"] += 1
        slot["gaps_closed"] += len(i.get("gaps", []))
    return dict(sorted(out.items()))


def total_cost(cases: Iterable[CaseResult]) -> float:
    return round(sum(c.cost_usd for c in cases), 6)


def total_duration(cases: Iterable[CaseResult]) -> float:
    return round(sum(c.duration_s for c in cases), 3)


def cost_and_performance(cases: list[CaseResult]) -> dict[str, Any]:
    n = len(cases)
    durs = sorted(c.duration_s for c in cases)
    return {
        "cases": n,
        "total_cost_usd": total_cost(cases),
        "avg_cost_usd": round(total_cost(cases) / n, 6) if n else None,
        "total_duration_s": total_duration(cases),
        "avg_duration_s": round(total_duration(cases) / n, 3) if n else None,
        "max_duration_s": durs[-1] if durs else None,
        "total_tokens": sum(c.tokens for c in cases),
        "total_steps": sum(c.steps for c in cases),
        "hitl_count": sum(c.hitl_count for c in cases),
    }


def misses_by_cause(cases: Iterable[CaseResult]) -> dict[str, int]:
    """Failed cases by cause; unclassified misses are listed as ``unclassified``."""
    out: Counter[str] = Counter()
    for c in cases:
        if c.passed is True or c.status == STATUS_PENDING:
            continue
        cause = c.miss_cause if c.miss_cause in MISS_CAUSES else "unclassified"
        out["error" if c.status == STATUS_ERROR else cause] += 1
    return dict(sorted(out.items()))


def metric_value(results: RunResults, metric: str) -> float | None:
    """Resolve a criteria metric name. ``pass_rate:tag=known`` and ``pass_rate:source=x`` filter."""
    base, _, flt = metric.partition(":")
    if base == "pass_rate":
        tag = source = None
        if flt.startswith("tag="):
            tag = flt[4:]
        elif flt.startswith("source="):
            source = flt[7:]
        elif flt:
            return None
        return pass_rate(results.cases, tag=tag, source=source)
    if flt:
        return None
    if base == "blind_repro_rate":
        return blind_repro_rate(results)
    if base == "open_gaps":
        return None if results.open_gaps is None else float(results.open_gaps)
    perf = cost_and_performance(results.cases)
    if base in ("avg_cost_usd", "total_cost_usd", "avg_duration_s", "max_duration_s"):
        v = perf[base]
        return None if v is None else float(v)
    return None


def metric_rows(results: RunResults) -> list[dict[str, Any]]:
    """``bld_metric_values`` rows: the computed value per run, no answers."""
    names = ["pass_rate", "avg_cost_usd", "avg_duration_s"]
    if results.blind:
        names.append("blind_repro_rate")
    computed_at = datetime.now(UTC).isoformat()
    rows = []
    for n in names:
        v = metric_value(results, n)
        if v is not None:
            rows.append(
                {
                    "value_key": f"{results.run_id}:{n}",
                    "metric_key": n,
                    "agent_key": results.agent,
                    "version_id": results.version,
                    "scope": "eval_run",
                    "scope_ref": results.run_id,
                    "value": v,
                    "computed_at": computed_at,
                }
            )
    return rows
