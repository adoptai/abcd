"""Report: six sections, self-contained HTML, no values."""

from __future__ import annotations

import json
import re

import pytest

from cli.builder_common.evals import gate as G  # noqa: N812
from cli.builder_common.evals import report as RP  # noqa: N812
from cli.builder_common.evals.metrics import RunResults
from cli.builder_common.evals.models import STATUS_ERROR, CaseResult

from .evals_fakes import MemStore


def case(cid, passed=True, **kw):
    return CaseResult(cid, passed=passed, status=kw.pop("status", "scored"), **kw)


def runs():
    live = RunResults(
        "live-1",
        "acme-tax",
        "returns",
        3,
        True,
        [case("a"), case("b"), case("c")],
        tier="live",
        version="v2",
    )
    cur = RunResults(
        "stg-1",
        "acme-tax",
        "returns",
        3,
        True,
        [
            case("a", cost_usd=0.1, duration_s=5),
            case("b", False, miss_cause="rounding", cost_usd=0.2, duration_s=9),
            case("c", False, miss_cause="missing_context", regression_reason="rule retired <b>"),
            case("d", False, status=STATUS_ERROR, error="x"),
        ],
        version="v3",
        open_gaps=1,
    )
    return cur, live


def crit():
    return [
        {"criteria_key": "k1", "metric": "pass_rate", "comparator": ">=", "target": 0.2},
        {"criteria_key": "k2", "metric": "no_regression"},
    ]


def test_six_sections_in_json() -> None:
    cur, live = runs()
    cur.cases.pop()  # drop the errored case so the gate can evaluate
    dec = G.evaluate_criteria(crit(), cur, live)
    rep = RP.build_report(
        cur,
        decision=dec,
        live=live,
        changes=[{"summary": "added depreciation rule", "reason": "gap-1 answered"}],
        generated_at="2026-10-27T00:00:00+00:00",
    )
    assert tuple(rep) == RP.SECTIONS
    h = rep["header"]
    assert h["verdict"] == "fail" and h["run_id"] == "stg-1" and h["live_run_id"] == "live-1"
    assert h["cases"] == 3 and h["passed"] == 1 and h["pass_rate"] == pytest.approx(1 / 3)
    assert [c["criteria_key"] for c in rep["criteria"]] == ["k1", "k2"]
    assert rep["criteria"][0]["live"] == 1.0 and rep["criteria"][0]["staged"] == pytest.approx(
        1 / 3
    )
    assert rep["regressions"]["cases"] == ["b"], "c has a recorded reason"
    assert rep["misses_by_cause"]["counts"] == {"missing_context": 1, "rounding": 1}
    assert rep["misses_by_cause"]["cases"]["rounding"] == ["b"]
    perf = rep["cost_performance"]
    assert perf["staged"]["total_cost_usd"] == pytest.approx(0.3) and perf["live"]["cases"] == 3
    assert rep["changes"][0]["reason"] == "gap-1 answered"
    json.dumps(rep)


def test_report_without_live_or_decision() -> None:
    cur, _ = runs()
    rep = RP.build_report(cur)
    assert rep["header"]["verdict"] is None and rep["criteria"] == []
    assert rep["regressions"] == {"compared_with": None, "cases": [], "live_pass_rate": None}
    assert rep["cost_performance"]["live"] is None
    page = RP.render_html(rep)
    assert "not evaluated" in page and "No live run" in page


def test_html_is_self_contained_and_escaped() -> None:
    cur, live = runs()
    cur.cases.pop()
    rep = RP.build_report(
        cur,
        decision=G.evaluate_criteria(crit(), cur, live),
        live=live,
        changes=[{"summary": "<script>alert(1)</script>", "reason": "a & b"}],
    )
    page = RP.render_html(rep)
    for n in range(1, 7):
        assert re.search(rf"<h2>{n}\. ", page)
    assert not re.search(r"https?://|//cdn|<link|<script|src=|@import|url\(", page)
    assert "&lt;script&gt;" in page and "a &amp; b" in page
    assert page.startswith("<!doctype html>") and "<style>" in page
    assert "Gate: fail" in page


def test_forced_release_is_flagged() -> None:
    cur, _ = runs()
    cur.cases.pop()
    rep = RP.build_report(cur, forced_reason="client deadline, lead lena")
    assert rep["header"]["forced_reason"] == "client deadline, lead lena"
    assert "forced: client deadline, lead lena" in RP.render_html(rep)


def test_write_report_uses_fresh_content_addressed_paths() -> None:
    cur, _ = runs()
    store = MemStore()
    p1 = RP.write_report(store, RP.build_report(cur, generated_at="t1"), "stg-1")
    p2 = RP.write_report(store, RP.build_report(cur, generated_at="t2"), "stg-1")
    assert p1["json"] != p2["json"] and len(store.files) == 4
    assert p1["html"].startswith("builder-ops/evals/stg-1/eval_report-")
    assert json.loads(store.files[p1["json"]])["header"]["run_id"] == "stg-1"
