"""The release gate (PLAN.md section 10): every criterion must pass; any error refuses.

`evaluate_criteria` returns a `GateDecision` when it could evaluate everything (a failing
criterion is a normal ``passed=False`` result). It raises `GateRefusedError` when it cannot trust
the run: a case errored or is pending judgement, the run is not blind, the dataset versions
disagree, a metric is not computable, or a criterion is malformed.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cli.builder_common.errors import RefusedError
from cli.builder_common.evals.metrics import RunResults, metric_value
from cli.builder_common.evals.models import STATUS_ERROR, STATUS_PENDING

COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}
NO_REGRESSION = "no_regression"


class GateRefusedError(RefusedError):
    """The gate refuses on any error; ``reasons`` lists every one."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("gate refused: " + "; ".join(reasons))


@dataclass
class CriterionResult:
    criteria_key: str
    metric: str
    comparator: str
    target: float | None
    actual: float | None
    passed: bool
    detail: str = ""
    live_actual: float | None = None
    regressions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class GateDecision:
    passed: bool
    results: list[CriterionResult]
    dataset_version: int
    run_id: str

    def failed(self) -> list[CriterionResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "run_id": self.run_id,
            "dataset_version": self.dataset_version,
            "criteria": [r.to_dict() for r in self.results],
        }


def regressions(
    results: RunResults, live: RunResults, accepted: dict[str, str] | None = None
) -> list[str]:
    """Cases that passed on live and do not pass now, without a recorded reason."""
    accepted = accepted or {}
    out = []
    for lc in live.cases:
        if lc.passed is not True:
            continue
        now = results.case(lc.case_id)
        if now is not None and now.passed is True:
            continue
        reason = (now.regression_reason if now else None) or accepted.get(lc.case_id)
        if not (reason and reason.strip()):
            out.append(lc.case_id)
    return sorted(out)


def _errors(
    criteria: list[dict[str, Any]],
    results: RunResults,
    live: RunResults | None,
    require_blind: bool,
) -> list[str]:
    errs: list[str] = []
    for label, run in (("staged", results), ("live", live)):
        if run is None:
            continue
        for c in run.cases:
            if c.status == STATUS_ERROR:
                errs.append(f"{label} run: case {c.case_id} errored ({c.error or 'no detail'})")
            elif c.status == STATUS_PENDING or c.passed is None:
                if c.status != STATUS_ERROR:
                    errs.append(f"{label} run: case {c.case_id} is still pending judgement")
    if not results.cases:
        errs.append("staged run has no cases")
    if require_blind and not results.blind:
        errs.append("the gated run is not blind (every gated eval must be)")
    for crit in criteria:
        key = str(crit.get("criteria_key") or crit.get("metric") or "?")
        metric, cmp_ = crit.get("metric"), crit.get("comparator", ">=")
        if not isinstance(metric, str) or not metric:
            errs.append(f"criterion {key}: metric is missing")
            continue
        dv = crit.get("dataset_version")
        if dv is not None and int(dv) != results.dataset_version:
            errs.append(
                f"criterion {key}: dataset version {dv} differs from the run's "
                f"v{results.dataset_version}"
            )
        if metric == NO_REGRESSION:
            if live is None:
                errs.append(f"criterion {key}: no_regression needs a live run to compare against")
            elif live.dataset_version != results.dataset_version:
                errs.append(
                    f"criterion {key}: live ran dataset v{live.dataset_version}, staged "
                    f"v{results.dataset_version}; releases compare only on the same version"
                )
            continue
        if cmp_ not in COMPARATORS:
            errs.append(f"criterion {key}: unknown comparator {cmp_!r}")
        try:
            float(crit["target"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"criterion {key}: target is missing or not a number")
        if metric_value(results, metric) is None:
            errs.append(f"criterion {key}: metric {metric!r} cannot be computed from this run")
    return errs


def evaluate_criteria(
    criteria: list[dict[str, Any]],
    results: RunResults,
    live_results: RunResults | None = None,
    *,
    accepted_regressions: dict[str, str] | None = None,
    require_blind: bool = True,
) -> GateDecision:
    """Evaluate release criteria against a staged run (and a live run for no_regression)."""
    errs = _errors(criteria, results, live_results, require_blind)
    if errs:
        raise GateRefusedError(errs)
    out: list[CriterionResult] = []
    for crit in criteria:
        metric = str(crit["metric"])
        key = str(crit.get("criteria_key") or metric)
        if metric == NO_REGRESSION:
            assert live_results is not None
            regs = regressions(results, live_results, accepted_regressions)
            out.append(
                CriterionResult(
                    key,
                    metric,
                    "==",
                    0.0,
                    float(len(regs)),
                    not regs,
                    detail="no case that passed on live fails now"
                    if not regs
                    else f"regressed: {', '.join(regs)}",
                    regressions=regs,
                )
            )
            continue
        cmp_, target = str(crit.get("comparator", ">=")), float(crit["target"])
        actual = metric_value(results, metric)
        assert actual is not None
        live_actual = metric_value(live_results, metric) if live_results else None
        ok = COMPARATORS[cmp_](actual, target)
        out.append(
            CriterionResult(
                key,
                metric,
                cmp_,
                target,
                actual,
                ok,
                detail=f"{actual:.4g} {cmp_} {target:.4g}"
                if ok
                else f"{actual:.4g} is not {cmp_} {target:.4g}",
                live_actual=live_actual,
            )
        )
    return GateDecision(all(r.passed for r in out), out, results.dataset_version, results.run_id)
