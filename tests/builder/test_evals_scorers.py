"""Every scorer, the deterministic checks and the judging helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals import scorers as S  # noqa: N812


def obs(**data):
    return {"text": "", "files": {}, "data": data, "question": None}


def exp(**data):
    return {"files": {}, "data": data, "text": {}}


CASE = {"case_id": "c1"}


def test_field_match_exact_and_mismatch() -> None:
    e = exp(**{"r.json": {"a": 1, "b": {"c": "x"}, "d": [1, 2]}})
    ok = S.field_match(CASE, obs(**{"r.json": {"a": 1, "b": {"c": "x"}, "d": [1, 2]}}), e, {})
    assert ok.passed and ok.score == 1.0
    bad = S.field_match(CASE, obs(**{"r.json": {"a": 2, "b": {"c": "x"}, "d": [1, 3]}}), e, {})
    assert bad.passed is False
    assert {m["field"] for m in bad.details["mismatches"]} == {"a", "d[1]"}
    assert bad.score == pytest.approx(2 / 4)


def test_field_match_tolerance_threshold_fields_ignore_ci() -> None:
    e = exp(**{"r.json": {"amt": 100.0, "name": "Zenith", "note": "skip"}})
    o = obs(**{"r.json": {"amt": 100.4, "name": "zenith ", "note": "other"}})
    assert S.field_match(
        CASE, o, e, {"tolerance": 0.5, "case_insensitive": True, "ignore": ["note"]}
    ).passed
    assert not S.field_match(CASE, o, e, {"tolerance": 0.1, "ignore": ["note"]}).passed
    assert S.field_match(CASE, o, e, {"fields": ["amt"], "tolerance": 0.5}).passed
    t = S.field_match(CASE, o, e, {"threshold": 0.3, "tolerance": 0.5, "case_insensitive": True})
    assert t.passed and t.score == pytest.approx(2 / 3)
    assert not S.field_match(
        CASE, o, e, {"threshold": 0.9, "tolerance": 0.5, "case_insensitive": True}
    ).passed


def test_field_match_rounding_flag_and_missing() -> None:
    e = exp(**{"r.json": {"amt": 10.0}})
    r = S.field_match(CASE, obs(**{"r.json": {"amt": 10.6}}), e, {})
    assert r.details["mismatches"][0]["rounding"] is True
    assert S.field_match(CASE, obs(), e, {}).passed is False
    assert S.field_match(CASE, obs(), exp(), {}).details["error"]


def test_field_match_picks_named_file() -> None:
    e = exp(**{"a.json": {"x": 1}, "b.json": {"y": 2}})
    o = obs(**{"out/b.json": {"y": 2}, "out/a.json": {"x": 9}})
    assert S.field_match(CASE, o, e, {"file": "b.json"}).passed
    assert not S.field_match(CASE, o, e, {"file": "a.json"}).passed


def test_rubric_is_pending() -> None:
    r = S.rubric(CASE, obs(), exp(), {"criteria": ["tone"]})
    assert r.pending and r.passed is None and r.details["criteria"] == ["tone"]


def test_label_match() -> None:
    e = exp(**{"l.json": {"label": "Acme-Tax", "doc_types": ["w2", "1099"]}})
    assert S.label_match(CASE, obs(**{"l.json": {"label": "acme-tax "}}), e, {}).passed
    assert not S.label_match(CASE, obs(**{"l.json": {"label": "zenith"}}), e, {}).passed
    assert not S.label_match(CASE, obs(), e, {}).passed
    dt = {"check_doc_types": True}
    assert S.label_match(
        CASE, obs(**{"l.json": {"label": "acme-tax", "doc_types": ["1099", "W2"]}}), e, dt
    ).passed
    assert not S.label_match(
        CASE, obs(**{"l.json": {"label": "acme-tax", "doc_types": ["w2"]}}), e, dt
    ).passed


def test_confusion_matrix() -> None:
    m = S.confusion_matrix([("a", "a"), ("a", "b"), ("b", "b"), ("b", None)])
    assert m["matrix"]["a"] == {"a": 1, "b": 1}
    assert m["per_label"]["a"]["recall"] == 0.5
    assert m["per_label"]["b"]["precision"] == 0.5
    assert "(none)" in m["labels"]


def test_boundary_match() -> None:
    e = exp(**{"l.json": {"boundaries": [1, 4, 9]}})
    assert S.boundary_match(
        CASE, obs(**{"l.json": {"boundaries": [[1, 3], [4, 8], [9, 9]]}}), e, {}
    ).passed
    miss = S.boundary_match(CASE, obs(**{"l.json": {"boundaries": [1, 4]}}), e, {})
    assert miss.passed is False and 0 < miss.score < 1
    assert S.boundary_match(
        CASE, obs(**{"l.json": {"boundaries": [1, 4]}}), e, {"threshold": 0.7}
    ).passed
    assert S.boundary_match(
        CASE, obs(**{"l.json": {"boundaries": [{"start": 1}, {"start": 4}, {"start": 9}]}}), e, {}
    ).passed


def test_outcome_match_classes() -> None:
    e0 = exp(**{"e.json": {"outcome_code": 0}})
    assert S.outcome_match(CASE, obs(**{"r.json": {"outcome_code": 0}}), e0, {}).passed
    assert not S.outcome_match(CASE, obs(**{"r.json": {"outcome_code": 1}}), e0, {}).passed
    e_ref = exp(**{"e.json": {"outcome_code": -1}})
    assert S.outcome_match(CASE, obs(**{"r.json": {"outcome_code": -2}}), e_ref, {}).passed
    assert not S.outcome_match(
        CASE, obs(**{"r.json": {"outcome_code": -2}}), e_ref, {"exact": True}
    ).passed
    assert not S.outcome_match(CASE, obs(), e0, {}).passed
    assert S.outcome_class(7) == "unknown" and S.outcome_class("x") == "unknown"


def test_state_match() -> None:
    e = exp(**{"e.json": {"state": {"status": "updated", "amount": 10}}})
    o = obs()
    o["readback"] = {"target": {"status": "updated", "amount": 10}}
    assert S.state_match(CASE, o, e, {}).passed
    o["readback"] = {"target": {"status": "new", "amount": 10}}
    r = S.state_match(CASE, o, e, {})
    assert not r.passed and r.details["mismatches"][0]["field"] == "status"
    assert S.state_match(CASE, o, exp(), {}).passed is False
    assert S.state_match(CASE, o, e, {"fields": ["amount"]}).passed


def test_collateral() -> None:
    e = exp(**{"e.json": {"allowed_changes": ["r3"]}})
    o = obs()
    o["snapshot"] = {"others": {"r2": {"v": 1}, "r3": {"v": 1}}}
    o["readback"] = {"others": {"r2": {"v": 1}, "r3": {"v": 2}}}
    assert S.collateral(CASE, o, e, {}).passed
    o["readback"] = {"others": {"r2": {"v": 5}, "r3": {"v": 1}}}
    r = S.collateral(CASE, o, e, {})
    assert not r.passed and r.details["changed"][0]["field"] == "r2.v"
    assert not S.collateral(CASE, obs(), e, {}).passed


def test_script_scorer(tmp_path: Path) -> None:
    d = tmp_path / "harness/evals/scorers"
    d.mkdir(parents=True)
    (d / "ok.py").write_text(
        "def score(case, observed, expected):\n    return {'passed': True, 'details': {'n': 1}}\n"
    )
    (d / "bad.py").write_text("x = 1\n")
    (d / "ret.py").write_text("def score(c, o, e):\n    return 5\n")
    r = S.score_case(tmp_path, CASE, obs(), exp(), ["script:ok"])[0]
    assert r.passed and r.score == 1.0 and r.scorer == "script:ok"
    for name in ("bad", "ret", "missing"):
        with pytest.raises(UsageError):
            S.run_script_scorer(tmp_path, name, CASE, obs(), exp())
    with pytest.raises(UsageError):
        S.run_script_scorer(tmp_path, "../x", CASE, obs(), exp())


def test_score_case_rejects_unknown_scorer(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        S.score_case(tmp_path, CASE, obs(), exp(), ["nope"])


def test_summary_has_no_details() -> None:
    r = S.field_match(CASE, obs(**{"r.json": {"a": 2}}), exp(**{"r.json": {"a": 1}}), {})
    assert set(r.summary()) == {"scorer", "passed", "score", "pending"}


@pytest.mark.parametrize(
    ("rule", "data", "ok"),
    [
        ({"type": "present", "field": "a"}, {"a": 1}, True),
        ({"type": "present", "field": "a"}, {"a": ""}, False),
        ({"type": "equals", "field": "a", "value": 3}, {"a": 3}, True),
        ({"type": "range", "field": "a", "min": 1, "max": 2}, {"a": 3}, False),
        ({"type": "range", "field": "a", "min": 1}, {"a": 3}, True),
        ({"type": "regex", "field": "a", "pattern": "^EIN-\\d+$"}, {"a": "EIN-12"}, True),
        ({"type": "regex", "field": "a", "pattern": "^x"}, {"a": 4}, False),
    ],
)
def test_deterministic_checks(rule: dict, data: dict, ok: bool) -> None:
    r = S.evaluate_check({"check_id": "k", "rule": rule}, obs(**{"o.json": data}))
    assert r.passed is ok and not r.pending


def test_judged_check_pending_and_bad_rule() -> None:
    assert S.evaluate_check({"check_id": "j", "kind": "judged", "rubric": "r"}, obs()).pending
    with pytest.raises(RefusedError):
        S.evaluate_check({"check_id": "k", "rule": {"type": "magic"}}, obs())


def test_load_checks(tmp_path: Path) -> None:
    d = tmp_path / "harness/checks"
    d.mkdir(parents=True)
    (d / "a.yaml").write_text("severity: error\nrule: {type: present, field: x}\n")
    (d / "b.yaml").write_text("check_id: beta\nkind: judged\nrubric: r\n")
    assert [c["check_id"] for c in S.load_checks(tmp_path)] == ["a", "beta"]
    assert [c["check_id"] for c in S.load_checks(tmp_path, ["beta"])] == ["beta"]
    assert S.load_checks(tmp_path / "none") == []


def test_judging_helpers() -> None:
    p = S.build_judging_packet("r1", [{"case_id": "c1"}])
    assert p["run_id"] == "r1" and p["items"] == [{"case_id": "c1"}]
    assert S.apply_judged_scores({"scores": {"a": 1, "b": 0.6}}).passed
    low = S.apply_judged_scores({"scores": {"a": 1, "b": 0.2}})
    assert not low.passed and low.score == pytest.approx(0.6)
    assert S.apply_judged_scores({"scores": {"a": 0}, "passed": True}).passed
    assert not S.apply_judged_scores({"scores": {}}).passed
