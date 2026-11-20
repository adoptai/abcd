"""Gap lifecycle, gap files, verify-by-blind-rerun, build-phase exit criteria."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.builder_common import gaps as G  # noqa: N812
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.metrics import RunResults
from cli.builder_common.evals.models import STATUS_ASKED, STATUS_ERROR, CaseResult
from cli.builder_common.tables import validate_row

from .evals_fakes import MemRows, MemStore

REF = {"path": "ds-returns/expected/c1/return.json", "sha256": "a" * 64}


def rr(cases: dict[str, bool | None], *, blind=True, rid="r", version=2, **kw) -> RunResults:
    cs = [
        CaseResult(c, passed=p, status="scored" if p is not None else "pending_judgement", **kw)
        for c, p in cases.items()
    ]
    return RunResults(rid, "acme-tax", "returns", version, blind, cs)


def opened(ws: Path, case_id="c1", **kw):
    return G.open_gap(
        ws,
        agent="acme-tax",
        dataset="returns",
        case_id=case_id,
        hypothesis="depreciation method unknown",
        question="Which method?",
        expected_ref=REF,
        run_id="r0",
        client="acme",
        **kw,
    )


def to_encoded(ws: Path, key: str) -> None:
    G.ask(ws, key, by="fde-1")
    G.answer(ws, key, answer_path="builder-sme-acme/answers/a1.md", by="sme-1")
    G.encode(ws, key, instruction="acme-tax:depreciation", by="fde-1")


def test_full_lifecycle_to_verified(tmp_path: Path) -> None:
    rows = MemRows()
    g = opened(tmp_path, rows=rows)
    key = g["gap_key"]
    assert g["status"] == "open" and rows.tables["bld_context_gaps"][key]["status"] == "open"
    G.ask(tmp_path, key, by="fde-1", rows=rows)
    assert G.get_gap(tmp_path, key)["owner"] == "sme"
    G.answer(tmp_path, key, answer_path="builder-sme-acme/answers/a1.md", by="sme-1", rows=rows)
    G.encode(tmp_path, key, instruction="acme-tax:depreciation", by="fde-1", rows=rows)
    base = rr({"c1": False, "c2": True})
    out = G.verify_by_blind_rerun(
        tmp_path, key, rr({"c1": True, "c2": True}, rid="r9"), base, rows=rows
    )
    assert out["status"] == "verified" and out["verifying_run"] == "r9"
    row = rows.tables["bld_context_gaps"][key]
    assert row["status"] == "verified" and row["instruction_key"] == "acme-tax:depreciation"
    assert [h["status"] for h in out["history"]] == [
        "open",
        "asked",
        "answered",
        "encoded",
        "verified",
    ]


def test_fde_can_answer_directly_from_open(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    G.answer(tmp_path, key, answer_path="builder-sme-acme/answers/a.md", by="fde-1")
    assert G.get_gap(tmp_path, key)["status"] == "answered"


@pytest.mark.parametrize(
    "step",
    [
        lambda w, k: G.encode(w, k, instruction="i", by="x"),  # open -> encoded
        lambda w, k: G.verify_by_blind_rerun(
            w, k, rr({"c1": True}), rr({"c1": False})
        ),  # open -> verified
    ],
)
def test_illegal_transitions_refused(tmp_path: Path, step) -> None:
    key = opened(tmp_path)["gap_key"]
    with pytest.raises(RefusedError):
        step(tmp_path, key)


def test_cannot_ask_after_answered_or_leave_terminal(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    to_encoded(tmp_path, key)
    with pytest.raises(RefusedError, match="answered|asked"):
        G.ask(tmp_path, key, by="x")
    G.verify_by_blind_rerun(tmp_path, key, rr({"c1": True}), rr({"c1": False}))
    with pytest.raises(RefusedError):
        G.waive(tmp_path, key, lead="lena", reason="whatever")


def test_answer_and_encode_need_their_references(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    with pytest.raises(UsageError):
        G.answer(tmp_path, key, answer_path="", by="x")
    G.answer(tmp_path, key, answer_path="p", by="x")
    with pytest.raises(UsageError):
        G.encode(tmp_path, key, instruction="", by="x")


def test_waive_requires_lead_and_reason(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    for lead, reason in (("", "r"), ("lena", ""), ("lena", "   "), ("  ", "r")):
        with pytest.raises(RefusedError, match="lead and a recorded reason"):
            G.waive(tmp_path, key, lead=lead, reason=reason)
    assert G.get_gap(tmp_path, key)["status"] == "open"
    rows = MemRows()
    g = G.waive(
        tmp_path, key, lead="lena", reason="client confirmed the case is an outlier", rows=rows
    )
    assert g["status"] == "waived" and g["waived_by"] == "lena"
    assert g["history"][-1]["note"].startswith("client confirmed")
    assert rows.tables["bld_context_gaps"][key]["status"] == "waived"


def test_verify_reopens_when_case_still_fails(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    to_encoded(tmp_path, key)
    g = G.verify_by_blind_rerun(
        tmp_path, key, rr({"c1": False, "c2": True}, rid="r5"), rr({"c1": False, "c2": True})
    )
    assert g["status"] == "open" and "still not reproduced in r5" in g["history"][-1]["note"]
    assert g["verifying_run"] is None


def test_verify_reopens_on_regression_of_another_case(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    to_encoded(tmp_path, key)
    base = rr({"c1": False, "c2": True, "c3": True})
    g = G.verify_by_blind_rerun(
        tmp_path, key, rr({"c1": True, "c2": False, "c3": True}, rid="r6"), base
    )
    assert g["status"] == "open" and "regressed ['c2']" in g["history"][-1]["note"]


def test_verify_needs_blind_full_set_same_dataset_version(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    to_encoded(tmp_path, key)
    base = rr({"c1": False, "c2": True})
    with pytest.raises(RefusedError, match="blind"):
        G.verify_by_blind_rerun(tmp_path, key, rr({"c1": True, "c2": True}, blind=False), base)
    with pytest.raises(RefusedError, match="full set"):
        G.verify_by_blind_rerun(tmp_path, key, rr({"c1": True}), base)
    with pytest.raises(RefusedError, match="same dataset version"):
        G.verify_by_blind_rerun(tmp_path, key, rr({"c1": True, "c2": True}, version=3), base)
    assert G.get_gap(tmp_path, key)["status"] == "encoded", "refusals change nothing"


def test_gap_file_shows_golden_output_by_path_only(tmp_path: Path) -> None:
    store = MemStore()
    g = opened(
        tmp_path,
        store=store,
        input_excerpt="Schedule L line 4",
        observed_excerpt="method: MACRS",
        reasoning="I assumed MACRS",
    )
    text = store.files[g["evidence_path"]].decode()
    assert g["evidence_path"] == f"builder-sme-acme/gaps/{g['gap_key']}.md"
    for needle in (
        "Schedule L line 4",
        "method: MACRS",
        "I assumed MACRS",
        "depreciation method unknown",
        "Which method?",
    ):
        assert needle in text
    assert f"`{REF['path']}`" in text and f"`{REF['sha256']}`" in text
    assert "Do not ask for its values" in text
    assert text.index("## Golden output") > text.index("## Agent reasoning")
    # render never takes golden values, so there is nothing to leak
    import inspect

    assert "expected_values" not in inspect.signature(G.render_gap_file).parameters


def test_open_gap_dedupes_unresolved_and_allows_new_after_verified(tmp_path: Path) -> None:
    a = opened(tmp_path)
    b = opened(tmp_path)
    assert a["gap_key"] == b["gap_key"] and len(G.list_gaps(tmp_path)) == 1
    assert "seen again" in G.get_gap(tmp_path, a["gap_key"])["history"][-1]["note"]
    to_encoded(tmp_path, a["gap_key"])
    G.verify_by_blind_rerun(tmp_path, a["gap_key"], rr({"c1": True}), rr({"c1": False}))
    c = opened(tmp_path)
    assert c["gap_key"] != a["gap_key"] and c["gap_key"].endswith("-2")


def test_rows_hold_no_golden_values(tmp_path: Path) -> None:
    rows = MemRows()
    opened(tmp_path, rows=rows)
    row = next(iter(rows.tables["bld_context_gaps"].values()))
    validate_row("bld_context_gaps", row)
    assert "expected_ref" not in row and row["case_key"] == "returns:c1"


def test_list_filters(tmp_path: Path) -> None:
    a = opened(tmp_path)
    opened(tmp_path, "c2")
    G.ask(tmp_path, a["gap_key"], by="x")
    assert len(G.list_gaps(tmp_path, agent="acme-tax")) == 2
    assert [g["case_id"] for g in G.list_gaps(tmp_path, status="asked")] == ["c1"]
    assert [g["case_id"] for g in G.list_gaps(tmp_path, case_id="c2")] == ["c2"]
    with pytest.raises(UsageError):
        G.get_gap(tmp_path, "nope")
    with pytest.raises(UsageError):
        opened(tmp_path, "c9", owner="bob")


def test_gaps_from_run_opens_only_missing_context(tmp_path: Path) -> None:
    store = MemStore()
    cs = [
        CaseResult(
            "c1",
            passed=False,
            status=STATUS_ASKED,
            miss_cause="missing_context",
            question="Which method?",
        ),
        CaseResult("c2", passed=False, miss_cause="rounding"),
        CaseResult("c3", passed=False),
        CaseResult("c4", passed=False, status=STATUS_ERROR, error="boom"),
        CaseResult("c5", passed=True),
    ]
    res = RunResults(
        "r1",
        "acme-tax",
        "returns",
        2,
        True,
        cs,
        meta={"gap_candidates": [{"case_id": "c1", "question": "Which method?"}]},
    )
    out = G.gaps_from_run(
        tmp_path,
        res,
        client="acme",
        classifications={"c3": "missing_context"},
        expected_refs={"c1": REF},
        store=store,
    )
    assert sorted(g["case_id"] for g in out["opened"]) == ["c1", "c3"]
    assert {p["case_id"]: p["cause"] for p in out["build_plan"]} == {
        "c2": "rounding",
        "c4": "error",
    }
    assert G.list_gaps(tmp_path, case_id="c1")[0]["question"] == "Which method?"
    assert len(store.files) == 2


def test_build_exit_criteria(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    clean = [{"instruction_key": "i1", "leak_check": {"status": "clean"}}]
    res = rr({"c1": False, "c2": True})
    st = G.build_exit_status(tmp_path, "acme-tax", res, instructions=clean)
    assert not st["ready"] and [c["passed"] for c in st["checks"]] == [False, False, True, False]

    # waiving needs a lead; once waived the case is excused, but the rate criterion still binds
    G.waive(tmp_path, key, lead="lena", reason="outlier")
    st = G.build_exit_status(tmp_path, "acme-tax", res, instructions=clean, repro_target=0.5)
    assert st["ready"] and st["waived"] == ["c1"]
    assert not G.build_exit_status(tmp_path, "acme-tax", res, instructions=clean)[
        "ready"
    ]  # 0.5 < 1.0

    flagged = [{"instruction_key": "i2", "leak_check": {"status": "flagged"}}]
    st = G.build_exit_status(tmp_path, "acme-tax", res, instructions=flagged, repro_target=0.5)
    assert not st["ready"] and st["checks"][2]["detail"] == ["i2"]
    assert not G.build_exit_status(
        tmp_path, "acme-tax", rr({"c1": True}, blind=False), repro_target=0
    )["ready"]


def test_exit_criteria_count_answered_gap_as_unfinished(tmp_path: Path) -> None:
    key = opened(tmp_path)["gap_key"]
    G.answer(tmp_path, key, answer_path="p", by="fde")
    st = G.build_exit_status(tmp_path, "acme-tax", rr({"c1": True}))
    assert st["checks"][1]["passed"] is False and st["checks"][1]["detail"] == [key]
