"""Gate and metrics: criteria, no_regression, error refusal, blind requirement."""

from __future__ import annotations

import pytest

from cli.builder_common.errors import RefusedError
from cli.builder_common.evals import gate as G  # noqa: N812
from cli.builder_common.evals import metrics as M  # noqa: N812
from cli.builder_common.evals.models import STATUS_ERROR, STATUS_PENDING, CaseResult


def case(cid: str, passed: bool | None = True, **kw) -> CaseResult:
    status = kw.pop("status", STATUS_PENDING if passed is None else "scored")
    return CaseResult(cid, passed=passed, status=status, **kw)


def run(cases, *, blind=True, version=3, open_gaps=0, rid="r1") -> M.RunResults:
    return M.RunResults(rid, "acme-tax", "returns", version, blind, cases, open_gaps=open_gaps)


CRIT = [
    {"criteria_key": "a:rate", "metric": "blind_repro_rate", "comparator": ">=", "target": 0.9},
    {"criteria_key": "a:gaps", "metric": "open_gaps", "comparator": "<=", "target": 0},
]


def test_passes_when_every_criterion_holds() -> None:
    d = G.evaluate_criteria(CRIT, run([case("a"), case("b")]))
    assert d.passed and [r.passed for r in d.results] == [True, True]
    assert d.to_dict()["criteria"][0]["actual"] == 1.0


def test_failing_criterion_is_a_result_not_a_refusal() -> None:
    d = G.evaluate_criteria(CRIT, run([case("a"), case("b", False)], open_gaps=2))
    assert not d.passed
    assert [r.criteria_key for r in d.failed()] == ["a:rate", "a:gaps"]
    assert "is not" in d.failed()[0].detail


@pytest.mark.parametrize(
    ("cmp_", "target", "expect"),
    [(">=", 0.5, True), (">", 0.5, False), ("<=", 0.5, True), ("<", 0.5, False), ("==", 0.5, True)],
)
def test_comparators(cmp_: str, target: float, expect: bool) -> None:
    d = G.evaluate_criteria(
        [{"metric": "pass_rate", "comparator": cmp_, "target": target}],
        run([case("a"), case("b", False)]),
    )
    assert d.passed is expect


def test_refuses_on_errored_case_with_all_reasons() -> None:
    bad = case("b", False, status=STATUS_ERROR, error="turn failed")
    with pytest.raises(G.GateRefusedError) as ei:
        G.evaluate_criteria(CRIT, run([case("a"), bad]))
    assert isinstance(ei.value, RefusedError)
    assert any("errored" in r and "turn failed" in r for r in ei.value.reasons)


def test_refuses_on_pending_judgement() -> None:
    with pytest.raises(G.GateRefusedError, match="pending"):
        G.evaluate_criteria(CRIT, run([case("a", None)]))


def test_refuses_when_not_blind_unless_allowed() -> None:
    with pytest.raises(G.GateRefusedError, match="not blind"):
        G.evaluate_criteria(CRIT[1:], run([case("a")], blind=False))
    assert G.evaluate_criteria(CRIT[1:], run([case("a")], blind=False), require_blind=False).passed


def test_refuses_unknown_metric_comparator_target_and_missing_gap_count() -> None:
    with pytest.raises(G.GateRefusedError) as ei:
        G.evaluate_criteria(
            [
                {"criteria_key": "x", "metric": "vibes", "target": 1},
                {"criteria_key": "y", "metric": "pass_rate", "comparator": "~", "target": 1},
                {"criteria_key": "z", "metric": "pass_rate"},
            ],
            run([case("a")]),
        )
    assert len(ei.value.reasons) >= 3
    with pytest.raises(G.GateRefusedError, match="open_gaps"):
        G.evaluate_criteria(CRIT[1:], run([case("a")], open_gaps=None))  # type: ignore[arg-type]


def test_refuses_dataset_version_mismatch() -> None:
    crit = [{"metric": "pass_rate", "target": 1, "dataset_version": 2}]
    with pytest.raises(G.GateRefusedError, match="dataset version"):
        G.evaluate_criteria(crit, run([case("a")]))


def test_refuses_empty_run() -> None:
    with pytest.raises(G.GateRefusedError, match="no cases"):
        G.evaluate_criteria([], run([]))


NR = [{"criteria_key": "a:nr", "metric": "no_regression"}]


def test_no_regression_passes_and_fails() -> None:
    live = run([case("a"), case("b"), case("c", False)], rid="live")
    ok = G.evaluate_criteria(NR, run([case("a"), case("b"), case("c")]), live)
    assert ok.passed
    bad = G.evaluate_criteria(NR, run([case("a"), case("b", False), case("c")]), live)
    assert not bad.passed and bad.results[0].regressions == ["b"]
    assert "b" in bad.results[0].detail


def test_no_regression_missing_case_counts() -> None:
    live = run([case("a"), case("b")], rid="live")
    d = G.evaluate_criteria(NR, run([case("a")]), live)
    assert d.results[0].regressions == ["b"]


def test_no_regression_recorded_reason_excuses() -> None:
    live = run([case("a"), case("b")], rid="live")
    now = run(
        [case("a"), case("b", False, regression_reason="client changed the rule (decision d-4)")]
    )
    assert G.evaluate_criteria(NR, now, live).passed
    now2 = run([case("a"), case("b", False, regression_reason="   ")])
    assert not G.evaluate_criteria(NR, now2, live).passed
    assert G.evaluate_criteria(
        NR, run([case("a"), case("b", False)]), live, accepted_regressions={"b": "retired rule"}
    ).passed


def test_no_regression_needs_live_on_same_dataset_version() -> None:
    with pytest.raises(G.GateRefusedError, match="live run"):
        G.evaluate_criteria(NR, run([case("a")]))
    with pytest.raises(G.GateRefusedError, match="same version"):
        G.evaluate_criteria(NR, run([case("a")]), run([case("a")], version=2))


def test_errored_live_run_also_refuses() -> None:
    live = run([case("a", False, status=STATUS_ERROR, error="x")], rid="live")
    with pytest.raises(G.GateRefusedError, match="live run"):
        G.evaluate_criteria(NR, run([case("a")]), live)


def test_live_actual_is_reported() -> None:
    live = run([case("a"), case("b", False)], rid="live")
    d = G.evaluate_criteria(
        [{"metric": "pass_rate", "target": 0.9}], run([case("a"), case("b")]), live
    )
    assert d.results[0].live_actual == 0.5 and d.results[0].actual == 1.0


# --------------------------------------------------------------------------- metrics


def test_pass_rate_filters_and_counts_non_passes() -> None:
    cs = [
        case("a", tags=["known"], source="known"),
        case("b", False, tags=["synthetic"], source="synthetic"),
        case("c", False, status=STATUS_ERROR, tags=["known"], source="known"),
    ]
    assert M.pass_rate(cs) == pytest.approx(1 / 3)
    assert M.pass_rate(cs, tag="known") == 0.5
    assert M.pass_rate(cs, source="synthetic") == 0.0
    assert M.pass_rate([]) is None
    r = run(cs)
    assert M.metric_value(r, "pass_rate:tag=known") == 0.5
    assert M.metric_value(r, "pass_rate:source=synthetic") == 0.0
    assert M.metric_value(r, "pass_rate:bogus") is None


def test_blind_repro_rate_only_for_blind_runs() -> None:
    assert M.blind_repro_rate(run([case("a"), case("b", False)])) == 0.5
    assert M.blind_repro_rate(run([case("a")], blind=False)) is None


def test_open_gaps_counts_unresolved_by_owner() -> None:
    gaps = [
        {"status": "open", "owner": "fde"},
        {"status": "asked", "owner": "sme"},
        {"status": "answered", "owner": "sme"},
        {"status": "encoded", "owner": "fde"},
        {"status": "verified", "owner": "fde"},
        {"status": "waived", "owner": "sme"},
    ]
    assert M.open_gaps(gaps) == 4
    assert M.open_gaps(gaps, owner="sme") == 2
    assert M.open_gaps_by_owner(gaps) == {"fde": 2, "sme": 2}


def test_time_to_close_gap() -> None:
    g = {
        "status": "verified",
        "history": [
            {"status": "open", "at": "2026-10-01T10:00:00+00:00"},
            {"status": "verified", "at": "2026-10-01T12:30:00+00:00"},
        ],
    }
    assert M.time_to_close_gap(g) == 9000
    assert M.time_to_close_gap({**g, "status": "open"}) is None


def test_cost_performance_and_misses() -> None:
    cs = [
        case("a", cost_usd=0.1, duration_s=10, tokens=5, steps=2, hitl_count=1),
        case("b", False, cost_usd=0.3, duration_s=30, miss_cause="rounding"),
        case("c", False, status=STATUS_ERROR),
        case("d", False),
        case("e", None),
    ]
    p = M.cost_and_performance(cs)
    assert (
        p["total_cost_usd"] == pytest.approx(0.4)
        and p["max_duration_s"] == 30
        and p["hitl_count"] == 1
    )
    assert M.misses_by_cause(cs) == {"error": 1, "rounding": 1, "unclassified": 1}
    assert M.metric_value(run(cs), "avg_cost_usd") == pytest.approx(0.08)
    assert M.instructions_per_agent(
        [{"agent": "x", "gaps": ["g1", "g2"]}, {"agent": "x", "gaps": []}]
    ) == {"x": {"count": 2, "gaps_closed": 2}}


def test_metric_rows_are_value_free() -> None:
    rows = M.metric_rows(run([case("a")]))
    assert {r["metric_key"] for r in rows} >= {"pass_rate", "blind_repro_rate"}
