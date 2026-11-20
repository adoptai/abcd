"""Context gaps (PLAN.md section 11): a case a blind run could not reproduce for lack of context.

Lifecycle: open -> asked -> answered -> encoded -> verified, or waived (a lead's recorded
reason). Gap records live in ``<ws>/.builder/gaps/<gap_key>.json`` and are mirrored to
``bld_context_gaps``. The gap *file* (``gaps/<gap_id>.md`` in ``builder-sme-<client>``) shows
the golden output by path and hash only: the SME and the Context agent never get its values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.metrics import UNRESOLVED_GAP_STATUSES, RunResults, blind_repro_rate
from cli.builder_common.evals.models import STATUS_ASKED, STATUS_ERROR, CaseResult
from cli.builder_common.evals.protocols import ArtifactStore, RowStore

STATUSES = ("open", "asked", "answered", "encoded", "verified", "waived")
OWNERS = ("fde", "sme")
ALLOWED: dict[str, tuple[str, ...]] = {
    "open": ("asked", "answered", "waived"),
    "asked": ("answered", "waived"),
    "answered": ("encoded", "waived"),
    "encoded": ("verified", "open", "waived"),
    "verified": (),
    "waived": (),
}
ROW_FIELDS = (
    "gap_key",
    "agent",
    "dataset",
    "case_id",
    "hypothesis",
    "question",
    "evidence_path",
    "owner",
    "answer_path",
    "instruction",
    "status",
    "verifying_run",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def gaps_dir(ws: Path) -> Path:
    return ws / ".builder" / "gaps"


def gap_file_path(client: str, gap_key: str) -> str:
    return f"builder-sme-{client}/gaps/{gap_key}.md"


def _save(ws: Path, gap: dict[str, Any]) -> dict[str, Any]:
    gaps_dir(ws).mkdir(parents=True, exist_ok=True)
    (gaps_dir(ws) / f"{gap['gap_key']}.json").write_text(json.dumps(gap, indent=2) + "\n")
    return gap


def get_gap(ws: Path, gap_key: str) -> dict[str, Any]:
    p = gaps_dir(ws) / f"{gap_key}.json"
    if not p.exists():
        raise UsageError(f"no gap {gap_key!r}")
    gap: dict[str, Any] = json.loads(p.read_text())
    return gap


def list_gaps(
    ws: Path, *, agent: str | None = None, status: str | None = None, case_id: str | None = None
) -> list[dict[str, Any]]:
    out = []
    if gaps_dir(ws).exists():
        for p in sorted(gaps_dir(ws).glob("*.json")):
            g = json.loads(p.read_text())
            if (
                (agent is None or g["agent"] == agent)
                and (status is None or g["status"] == status)
                and (case_id is None or g["case_id"] == case_id)
            ):
                out.append(g)
    return out


def render_gap_file(
    gap: dict[str, Any],
    *,
    input_excerpt: str = "",
    observed_excerpt: str = "",
    reasoning: str = "",
) -> str:
    """The SME-facing gap file. The golden output appears by path and hash only."""
    ref = gap.get("expected_ref") or {}
    lines = [
        f"# Gap {gap['gap_key']}",
        "",
        f"- Agent: {gap['agent']}",
        f"- Dataset: {gap['dataset']}",
        f"- Case: {gap['case_id']}",
        f"- Owner: {gap['owner']}",
        f"- Status: {gap['status']}",
        "",
        "## What the Builder thinks is missing",
        "",
        gap["hypothesis"] or "(no hypothesis yet)",
        "",
        "## Question",
        "",
        gap["question"] or "(none)",
        "",
        "## Input excerpt",
        "",
        input_excerpt or "(none)",
        "",
        "## Agent output",
        "",
        observed_excerpt or "(none)",
        "",
        "## Agent reasoning",
        "",
        reasoning or "(none)",
        "",
        "## Golden output",
        "",
        "Held by the FDE; shown here by location only. Do not ask for its values.",
        "",
        f"- Path: `{ref.get('path', '')}`",
        f"- SHA-256: `{ref.get('sha256', '')}`",
        "",
    ]
    return "\n".join(lines)


def mirror_row(gap: dict[str, Any]) -> dict[str, Any]:
    """The ``bld_context_gaps`` columns of a gap record (no expected value, no answer text)."""
    return {
        "gap_key": gap["gap_key"],
        "agent_key": gap.get("agent"),
        "case_key": f"{gap.get('dataset')}:{gap.get('case_id')}",
        "hypothesis": gap.get("hypothesis"),
        "question": gap.get("question"),
        "evidence_path": gap.get("evidence_path"),
        "assigned_to": gap.get("owner"),
        "answer_path": gap.get("answer_path"),
        "instruction_key": gap.get("instruction"),
        "status": gap.get("status"),
        "verified_by_run": gap.get("verifying_run"),
    }


def _mirror(rows: RowStore | None, gap: dict[str, Any]) -> None:
    if rows is not None:
        rows.insert("bld_context_gaps", mirror_row(gap))
        rows.update("bld_context_gaps", gap["gap_key"], mirror_row(gap))


def open_gap(
    ws: Path,
    *,
    agent: str,
    dataset: str,
    case_id: str,
    hypothesis: str,
    question: str,
    expected_ref: dict[str, str] | None = None,
    run_id: str = "",
    client: str = "",
    owner: str = "fde",
    input_excerpt: str = "",
    observed_excerpt: str = "",
    reasoning: str = "",
    store: ArtifactStore | None = None,
    rows: RowStore | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Open a gap, or add evidence to the unresolved gap this case already has."""
    if owner not in OWNERS:
        raise UsageError(f"owner must be one of {OWNERS}")
    at = now or _now()
    for g in list_gaps(ws, agent=agent, case_id=case_id):
        if g["dataset"] == dataset and g["status"] in UNRESOLVED_GAP_STATUSES:
            g["history"].append(
                {
                    "status": g["status"],
                    "at": at,
                    "by": "builder",
                    "note": f"seen again in {run_id}",
                }
            )
            return _save(ws, g)
    n = len(list_gaps(ws, agent=agent, case_id=case_id)) + 1
    tag = hashlib.sha256(f"{agent}:{dataset}:{case_id}".encode()).hexdigest()[:8]
    gap: dict[str, Any] = {
        "gap_key": f"gap-{tag}-{n}",
        "agent": agent,
        "dataset": dataset,
        "case_id": case_id,
        "client": client,
        "hypothesis": hypothesis,
        "question": question,
        "expected_ref": expected_ref,
        "owner": owner,
        "answer_path": None,
        "instruction": None,
        "status": "open",
        "verifying_run": None,
        "waive_reason": None,
        "waived_by": None,
        "source_run": run_id,
        "opened_at": at,
        "history": [{"status": "open", "at": at, "by": "builder", "note": f"from {run_id}"}],
    }
    gap["evidence_path"] = gap_file_path(client, gap["gap_key"]) if client else None
    if store is not None and gap["evidence_path"]:
        store.put(
            gap["evidence_path"],
            render_gap_file(
                gap,
                input_excerpt=input_excerpt,
                observed_excerpt=observed_excerpt,
                reasoning=reasoning,
            ).encode(),
        )
    _save(ws, gap)
    _mirror(rows, gap)
    return gap


def _transition(
    ws: Path,
    gap_key: str,
    to: str,
    *,
    by: str,
    note: str = "",
    rows: RowStore | None = None,
    **fields: Any,
) -> dict[str, Any]:
    gap = get_gap(ws, gap_key)
    if to not in ALLOWED[gap["status"]]:
        raise RefusedError(f"gap {gap_key}: cannot go from {gap['status']} to {to}")
    gap.update(fields)
    gap["status"] = to
    gap["history"].append({"status": to, "at": _now(), "by": by, "note": note})
    _save(ws, gap)
    _mirror(rows, gap)
    return gap


def ask(
    ws: Path, gap_key: str, *, by: str, owner: str = "sme", rows: RowStore | None = None
) -> dict[str, Any]:
    if owner not in OWNERS:
        raise UsageError(f"owner must be one of {OWNERS}")
    return _transition(ws, gap_key, "asked", by=by, rows=rows, owner=owner)


def answer(
    ws: Path, gap_key: str, *, answer_path: str, by: str, rows: RowStore | None = None
) -> dict[str, Any]:
    if not answer_path:
        raise UsageError("an answer needs its path in builder-sme")
    return _transition(ws, gap_key, "answered", by=by, rows=rows, answer_path=answer_path)


def encode(
    ws: Path, gap_key: str, *, instruction: str, by: str, rows: RowStore | None = None
) -> dict[str, Any]:
    if not instruction:
        raise UsageError("encoding needs the instruction key")
    return _transition(ws, gap_key, "encoded", by=by, rows=rows, instruction=instruction)


def waive(
    ws: Path, gap_key: str, *, lead: str, reason: str, rows: RowStore | None = None
) -> dict[str, Any]:
    """A waived gap needs a lead's recorded reason; the scorecard counts waived gaps."""
    if not (lead and lead.strip()) or not (reason and reason.strip()):
        raise RefusedError("waiving a gap needs a lead and a recorded reason")
    return _transition(
        ws, gap_key, "waived", by=lead, note=reason, rows=rows, waive_reason=reason, waived_by=lead
    )


def verify_by_blind_rerun(
    ws: Path,
    gap_key: str,
    rerun: RunResults,
    baseline: RunResults,
    *,
    rows: RowStore | None = None,
) -> dict[str, Any]:
    """Close the gap only if a blind full-set run reproduces its case without regressing another.

    Otherwise the gap reopens with the new evidence.
    """
    gap = get_gap(ws, gap_key)
    if gap["status"] != "encoded":
        raise RefusedError(f"gap {gap_key} is {gap['status']}; only an encoded gap can be verified")
    if not rerun.blind:
        raise RefusedError("verification needs a blind run")
    if rerun.dataset != baseline.dataset or rerun.dataset_version != baseline.dataset_version:
        raise RefusedError("verification compares runs on the same dataset version only")
    missing = {c.case_id for c in baseline.cases} - {c.case_id for c in rerun.cases}
    if missing:
        raise RefusedError(f"verification needs the full set re-run; missing {sorted(missing)}")
    case = rerun.case(gap["case_id"])
    regressed = sorted(
        c.case_id
        for c in baseline.cases
        if c.passed is True
        and c.case_id != gap["case_id"]
        and not (rerun.case(c.case_id) or CaseResult(c.case_id)).passed
    )
    reproduced = case is not None and case.passed is True
    if reproduced and not regressed:
        return _transition(
            ws,
            gap_key,
            "verified",
            by="builder",
            note=f"reproduced blind in {rerun.run_id}",
            rows=rows,
            verifying_run=rerun.run_id,
        )
    why = (
        f"regressed {regressed} in {rerun.run_id}"
        if reproduced
        else f"case still not reproduced in {rerun.run_id}"
    )
    return _transition(ws, gap_key, "open", by="builder", note=why, rows=rows)


# --------------------------------------------------------------------------- from runs


def gaps_from_run(
    ws: Path,
    results: RunResults,
    *,
    client: str = "",
    classifications: dict[str, str] | None = None,
    expected_refs: dict[str, dict[str, str]] | None = None,
    store: ArtifactStore | None = None,
    rows: RowStore | None = None,
    candidates: Iterable[dict[str, str]] | None = None,
) -> dict[str, list[Any]]:
    """Open a gap for every missing-context miss; everything else is a build plan item.

    ``classifications`` is the context-gaps skill's verdict per case id. An agent question in a
    blind run is already a missing-context miss (a gap candidate).
    """
    classifications = classifications or {}
    questions = {
        c["case_id"]: c["question"] for c in (candidates or results.meta.get("gap_candidates", []))
    }
    opened: list[dict[str, Any]] = []
    plan: list[dict[str, str]] = []
    for c in results.cases:
        if c.passed is True:
            continue
        if c.status == STATUS_ERROR and c.case_id not in classifications:
            plan.append({"case_id": c.case_id, "cause": "error", "item": "fix the run error"})
            continue
        cause = classifications.get(c.case_id) or c.miss_cause
        if c.status == STATUS_ASKED:
            cause = "missing_context"
        if cause == "missing_context":
            q = questions.get(c.case_id) or c.question or "What context was missing for this case?"
            opened.append(
                open_gap(
                    ws,
                    agent=results.agent,
                    dataset=results.dataset,
                    case_id=c.case_id,
                    hypothesis="The agent lacked context it needed"
                    + (" and asked for it." if c.question else "."),
                    question=q,
                    expected_ref=(expected_refs or {}).get(c.case_id),
                    run_id=results.run_id,
                    client=client,
                    store=store,
                    rows=rows,
                    observed_excerpt=c.observed_path or "",
                )
            )
        else:
            plan.append(
                {"case_id": c.case_id, "cause": cause or "unclassified", "item": "build plan item"}
            )
    return {"opened": opened, "build_plan": plan}


def build_exit_status(
    ws: Path,
    agent: str,
    results: RunResults,
    *,
    instructions: Iterable[dict[str, Any]] = (),
    repro_target: float = 1.0,
) -> dict[str, Any]:
    """Build-phase exit criteria (PLAN.md section 11)."""
    gaps = list_gaps(ws, agent=agent)
    waived_cases = {g["case_id"] for g in gaps if g["status"] == "waived"}
    unreproduced = sorted(
        c.case_id for c in results.cases if c.passed is not True and c.case_id not in waived_cases
    )
    unresolved = sorted(g["gap_key"] for g in gaps if g["status"] in UNRESOLVED_GAP_STATUSES)
    flagged = sorted(
        str(i.get("instruction_key"))
        for i in instructions
        if (i.get("leak_check") or {}).get("status") == "flagged"
    )
    rate = blind_repro_rate(results)
    checks = [
        {
            "check": "every active case reproduces blind, or its gap is waived",
            "passed": not unreproduced,
            "detail": unreproduced,
        },
        {
            "check": "no gaps are open, asked, answered or encoded",
            "passed": not unresolved,
            "detail": unresolved,
        },
        {
            "check": "no instruction has a flagged leak check",
            "passed": not flagged,
            "detail": flagged,
        },
        {
            "check": f"blind reproduction rate >= {repro_target:g}",
            "passed": rate is not None and rate >= repro_target,
            "detail": rate,
        },
    ]
    return {
        "agent": agent,
        "ready": all(c["passed"] for c in checks),
        "checks": checks,
        "waived": sorted(waived_cases),
    }
