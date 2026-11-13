"""Eval runner: submit, advance, collect (PLAN.md sections 10 and 11).

A run is a resumable state machine. `submit_run` starts the first turns and writes
``<ws>/.builder/evals/<run_id>/state.json`` plus a ``bld_eval_runs`` row holding the
submitted turn ids. `advance` polls those turns and moves each case on; it can be called from
a fresh process after a restart. `collect` scores, writes files to the stores and rows, and
returns `RunResults`. `run_eval` loops `advance` until the run is complete.

Nothing here puts an answer in state.json or in a row: they hold paths, hashes and scores.
Expected values are fetched from the ``ds-<dataset>`` store at scoring time and the diff that
contains them goes back to that store.

Blind runs send the driver only the agent's generic prompt (plus the case's own input message,
if any). A question the agent asks is recorded as a gap candidate and is never answered.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli.builder_common.datasets import Dataset, load_dataset, normalize_scorer
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.evals.blind import blind_message
from cli.builder_common.evals.metrics import RunResults, metric_rows
from cli.builder_common.evals.models import (
    STATUS_ASKED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SCORED,
    CaseResult,
)
from cli.builder_common.evals.protocols import (
    TURN_COMPLETED,
    TURN_FAILED,
    TURN_NEEDS_INPUT,
    ArtifactStore,
    HarnessDriver,
    RowStore,
    TurnHandle,
    TurnStatus,
    sha256_hex,
)
from cli.builder_common.evals.scorers import (
    CheckResult,
    ScorerResult,
    apply_judged_scores,
    build_judging_packet,
    evaluate_check,
    load_checks,
    score_case,
)

OPS_STORE = "builder-ops"
STATE_VERSION = 1

# case phases
P_SNAPSHOT, P_TURN, P_READBACK, P_TEARDOWN, P_DONE = (
    "snapshot",
    "turn",
    "readback",
    "teardown",
    "done",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- paths and state


def run_dir(ws: Path, run_id: str) -> Path:
    return ws / ".builder" / "evals" / run_id


def state_path(ws: Path, run_id: str) -> Path:
    return run_dir(ws, run_id) / "state.json"


def ops_path(run_id: str, *parts: str) -> str:
    return "/".join([OPS_STORE, "evals", run_id, *parts])


def diff_path(dataset: str, run_id: str, case_id: str) -> str:
    return f"ds-{dataset}/diffs/{run_id}/{case_id}.json"


def packet_path(dataset: str, run_id: str) -> str:
    return f"ds-{dataset}/judging/{run_id}/packet.json"


def load_state(ws: Path, run_id: str) -> dict[str, Any]:
    p = state_path(ws, run_id)
    if not p.exists():
        raise UsageError(f"no eval run {run_id!r} at {p}")
    state: dict[str, Any] = json.loads(p.read_text())
    return state


def save_state(ws: Path, state: dict[str, Any]) -> None:
    p = state_path(ws, state["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, p)


def _turn_list(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if state.get("env_check", {}).get("handle"):
        out.append({"role": "env_check", **state["env_check"]["handle"]})
    if (state.get("batch") or {}).get("handle"):
        out.append({"role": "batch", **state["batch"]["handle"]})
    for cid, c in state["cases"].items():
        for phase, h in c.get("handles", {}).items():
            out.append({"role": phase, "case_id": cid, **h})
    return out


#: Run state status to the ``bld_eval_runs.status`` enum.
_RUN_STATUS = {"running": "running", "complete": "done", "refused": "failed"}
#: Run-state miss causes the ``bld_eval_results.miss_cause`` enum spells differently.
_MISS_CAUSE = {"skill_bug": "real_error"}


def run_row(state: dict[str, Any]) -> dict[str, Any]:
    """The ``bld_eval_runs`` columns of a run (without the unique key)."""
    cost = sum(float(c["result"]["cost_usd"]) for c in state["cases"].values() if c.get("result"))
    return {
        "agent_key": state["agent"],
        "version_id": state.get("version", ""),
        "dataset_version_key": f"{state['dataset']}@{state['dataset_version']}",
        "tier": state["tier"],
        "blind": state["blind"],
        "ran_in": state["where"],
        "status": _RUN_STATUS.get(state["status"], "failed"),
        "submitted_turns": _turn_list(state),
        "cost_usd": round(cost, 6),
        "report_path": state.get("report_path"),
    }


def _sync_row(state: dict[str, Any], rows: RowStore | None, *, insert: bool = False) -> None:
    if rows is None:
        return
    fields = run_row(state)
    if insert:
        rows.insert("bld_eval_runs", {"eval_run_id": state["run_id"], **fields})
    else:
        rows.update("bld_eval_runs", state["run_id"], fields)


# --------------------------------------------------------------------------- messages


def case_message(ws: Path, agent: str, case: dict[str, Any]) -> str:
    """The ONLY text a case turn carries: the generic prompt and the case's own input message."""
    msg = blind_message(ws, agent)
    extra = (case.get("inputs") or {}).get("message")
    return f"{msg}\n\n{extra}" if extra else msg


def _probe_message(action: str, case: dict[str, Any], snapshot: Any = None) -> str:
    body: dict[str, Any] = {"action": action, "setup": case.get("setup") or {}}
    if action == "teardown":
        body["teardown"] = case.get("teardown") or {}
        body["snapshot"] = snapshot
    return json.dumps(body, sort_keys=True)


# --------------------------------------------------------------------------- submit


def _start(
    driver: HarnessDriver,
    *,
    workstream: str,
    process_id: str,
    message: str,
    conversation_id: str | None = None,
) -> dict[str, str]:
    h = driver.start_turn(
        workstream=workstream,
        process_id=process_id,
        message=message,
        conversation_id=conversation_id,
    )
    return h.to_dict()


def _new_case_state() -> dict[str, Any]:
    return {"phase": P_TURN, "handles": {}, "refs": {}, "turn": None, "result": None}


def effective_scorers(ds: Dataset, case: dict[str, Any]) -> list[Any]:
    """The case's scorers, else the dataset's default scorers."""
    return list(case.get("scorers") or ds.manifest.get("default_scorers") or [])


def refuse_unscored(ws: Path, ds: Dataset, cases: list[dict[str, Any]]) -> None:
    """A case with no scorer and no client check would pass whatever the agent did."""
    if load_checks(ws):
        return
    bare = [c["case_id"] for c in cases if not effective_scorers(ds, c)]
    if bare:
        raise UsageError(
            f"dataset {ds.name!r}: case(s) {', '.join(sorted(bare))} have no scorer and there are "
            "no client checks, so every run would pass them. Add a scorer "
            "(`abcd dataset update-case ... --scorer field_match`, or a default scorer with "
            "`abcd dataset create --scorer ...`) or a check (`abcd check add`)"
        )


def submit_run(
    ws: Path,
    *,
    dataset: str,
    agent: str,
    process_id: str,
    driver: HarnessDriver,
    store: ArtifactStore,
    rows: RowStore | None = None,
    blind: bool = True,
    case_ids: list[str] | None = None,
    tier: str = "staged",
    where: str = "local",
    version: str = "",
    probe_process_id: str | None = None,
    answers: dict[str, str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Start a run and persist its state. Returns the state dict (``run_id`` inside)."""
    ds = load_dataset(ws, dataset)
    if blind and answers:
        raise UsageError("a blind run never answers the agent's questions")
    cases = ds.active_cases()
    if case_ids:
        unknown = set(case_ids) - {c["case_id"] for c in cases}
        if unknown:
            raise UsageError(f"unknown or retired case ids: {sorted(unknown)}")
        cases = [c for c in cases if c["case_id"] in case_ids]
    if not cases:
        raise UsageError(f"dataset {dataset!r} has no active cases to run")
    refuse_unscored(ws, ds, cases)
    run_mode = ds.manifest["run_mode"]
    if run_mode == "batch" and ds.kind != "labeled":
        raise UsageError("batch run mode is only for labeled datasets")
    if ds.kind == "read_your_writes":
        if not probe_process_id:
            raise UsageError("read_your_writes datasets need probe_process_id")
        if not ds.manifest.get("target_env"):
            raise UsageError("read_your_writes dataset declares no target_env")
    blind_message(ws, agent)  # fail early: blind and gated runs need the generic prompt

    rid = run_id or f"run-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    if state_path(ws, rid).exists():
        raise UsageError(f"eval run {rid!r} already exists; use advance/collect to resume it")
    state: dict[str, Any] = {
        "state_version": STATE_VERSION,
        "run_id": rid,
        "agent": agent,
        "process_id": process_id,
        "probe_process_id": probe_process_id,
        "dataset": dataset,
        "dataset_version": ds.version,
        "kind": ds.kind,
        "run_mode": run_mode,
        "target_env": ds.manifest.get("target_env"),
        "blind": blind,
        "tier": tier,
        "where": where,
        "version": version,
        "answers": answers or {},
        "created_at": _now(),
        "status": "running",
        "env_check": {"phase": "none"},
        "batch": None,
        "cases": {c["case_id"]: _new_case_state() for c in cases},
        "gap_candidates": [],
        "refusal": None,
    }
    for c in cases:
        c_state = state["cases"][c["case_id"]]
        if ds.kind == "read_your_writes":
            c_state["phase"] = P_SNAPSHOT
    if ds.kind == "read_your_writes":
        h = _start(
            driver,
            workstream=ds.workstream(cases[0]["case_id"]),
            process_id=str(probe_process_id),
            message=json.dumps({"action": "describe_target"}),
        )
        state["env_check"] = {"phase": "running", "handle": h}
    elif run_mode == "batch":
        h = _start(
            driver,
            workstream=ds.workstream(cases[0]["case_id"]),
            process_id=process_id,
            message=blind_message(ws, agent),
        )
        state["batch"] = {"phase": "running", "handle": h}
        for c in cases:
            state["cases"][c["case_id"]]["phase"] = "batch"
    else:
        for c in cases:
            cid = c["case_id"]
            h = _start(
                driver,
                workstream=ds.workstream(cid),
                process_id=process_id,
                message=case_message(ws, agent, c),
            )
            state["cases"][cid]["handles"][P_TURN] = h
            save_state(ws, state)  # persist each handle at once so a crash can resume
    save_state(ws, state)
    _sync_row(state, rows, insert=True)
    return state


# --------------------------------------------------------------------------- observing


def _parse_json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _observed(status: TurnStatus, outputs: dict[str, bytes]) -> dict[str, Any]:
    data = {}
    for name, blob in outputs.items():
        if name.endswith(".json"):
            parsed = _parse_json(blob)
            if parsed is not None:
                data[name] = parsed
    return {
        "text": status.final_text,
        "files": {n: sha256_hex(b) for n, b in sorted(outputs.items())},
        "data": data,
        "question": status.question,
    }


def _probe_data(status: TurnStatus, outputs: dict[str, bytes]) -> dict[str, Any]:
    """What a probe turn reported: its JSON output(s), else JSON in its final text."""
    obs = _observed(status, outputs)
    data = obs["data"]
    if len(data) == 1:
        v = next(iter(data.values()))
        return v if isinstance(v, dict) else {"value": v}
    if data:
        return dict(data)
    parsed = _parse_json(status.final_text.encode())
    if isinstance(parsed, dict):
        return parsed
    return {"text": status.final_text}


def _status_dict(s: TurnStatus) -> dict[str, Any]:
    return {
        "state": s.state,
        "question": s.question,
        "error": s.error,
        "steps": s.steps,
        "tokens": s.tokens,
        "cost_usd": s.cost_usd,
        "duration_s": s.duration_s,
        "hitl_count": s.hitl_count,
    }


def _target_matches(declared: str | dict[str, str], seen: dict[str, Any]) -> bool:
    if isinstance(declared, str):
        return str(seen.get("target_env", seen.get("value", ""))) == declared
    return all(str(seen.get(k)) == str(v) for k, v in declared.items())


# --------------------------------------------------------------------------- scoring


def load_expected(store: ArtifactStore, case: dict[str, Any], dataset: str) -> dict[str, Any]:
    """Fetch and hash-verify a case's expected files at scoring time."""
    prefix = f"ds-{dataset}/expected/{case['case_id']}/"
    exp: dict[str, Any] = {"files": {}, "data": {}, "text": {}}
    for ref in case["expected"]:
        blob = store.get(ref["path"])
        if sha256_hex(blob) != ref["sha256"]:
            raise RefusedError(f"expected file {ref['path']} does not match its recorded sha256")
        rel = ref["path"][len(prefix) :] if ref["path"].startswith(prefix) else ref["path"]
        exp["files"][rel] = ref["sha256"]
        parsed = _parse_json(blob)
        if parsed is not None:
            exp["data"][rel] = parsed
        else:
            exp["text"][rel] = blob.decode("utf-8", errors="replace")
    return exp


def _classify(results: list[ScorerResult]) -> str | None:
    mism = [m for r in results for m in r.details.get("mismatches", [])]
    if mism and all(m.get("rounding") for m in mism):
        return "rounding"
    return None


def _combine(
    case_result: CaseResult,
    scorer_summaries: list[dict[str, Any]],
    checks: list[CheckResult],
) -> None:
    """Fill passed/score/status from scorer summaries and check results."""
    failed_checks = [c.check_id for c in checks if c.passed is False and c.severity == "error"]
    pending_checks = [c.check_id for c in checks if c.pending]
    case_result.scorers = scorer_summaries
    case_result.failed_check_ids = failed_checks
    case_result.pending_check_ids = pending_checks
    scores = [s["score"] for s in scorer_summaries if s["score"] is not None]
    case_result.score = round(sum(scores) / len(scores), 6) if scores else None
    pending = any(s["pending"] for s in scorer_summaries) or bool(pending_checks)
    failed = any(s["passed"] is False for s in scorer_summaries) or bool(failed_checks)
    if failed:
        case_result.passed, case_result.status = False, STATUS_SCORED
    elif pending:
        case_result.passed, case_result.status = None, STATUS_PENDING
    else:
        case_result.passed, case_result.status = True, STATUS_SCORED


def _apply_turn_metrics(res: CaseResult, turn: dict[str, Any] | None, share: float = 1.0) -> None:
    if not turn:
        return
    res.steps = int(turn["steps"])
    res.tokens = int(turn["tokens"])
    res.cost_usd = round(float(turn["cost_usd"]) * share, 6)
    res.duration_s = round(float(turn["duration_s"]) * share, 3)
    res.hitl_count = int(turn["hitl_count"])


def _error_result(case: dict[str, Any], msg: str, turn: dict[str, Any] | None = None) -> CaseResult:
    res = CaseResult(
        case["case_id"],
        passed=False,
        status=STATUS_ERROR,
        error=msg,
        source=case["source"],
        tags=list(case["tags"]),
    )
    _apply_turn_metrics(res, turn)
    return res


def _finish_case(
    ws: Path,
    state: dict[str, Any],
    ds: Dataset,
    case: dict[str, Any],
    observed: dict[str, Any],
    status: TurnStatus,
    store: ArtifactStore,
    *,
    share: float = 1.0,
    transcript: str = "",
    outputs: dict[str, bytes] | None = None,
) -> CaseResult:
    rid, cid = state["run_id"], case["case_id"]
    expected = load_expected(store, case, ds.name)
    # The transcript is evidence for goal_reached only; it is never persisted in observed.json.
    scoring = {**observed, "transcript": transcript} if transcript else observed
    results = score_case(ws, case, scoring, expected, effective_scorers(ds, case))
    checks = [evaluate_check(c, scoring) for c in load_checks(ws)]
    res = CaseResult(cid, source=case["source"], tags=list(case["tags"]))
    _combine(res, [r.summary() for r in results], checks)
    if res.passed is False:
        res.miss_cause = _classify(results)
    _apply_turn_metrics(res, state["cases"][cid]["turn"], share)

    obs_blob = json.dumps(dict(observed), indent=2, sort_keys=True).encode()
    res.observed_path = ops_path(rid, "cases", cid, "observed.json")
    res.observed_sha256 = store.put(res.observed_path, obs_blob)
    for name, blob in (outputs or {}).items():
        store.put(ops_path(rid, "cases", cid, "outputs", name), blob)
    store.put(
        ops_path(rid, "cases", cid, "checks.json"),
        json.dumps([c.__dict__ for c in checks], indent=2).encode(),
    )
    if transcript:
        store.put(ops_path(rid, "cases", cid, "trace.ndjson"), transcript.encode())
    diff = {
        "case_id": cid,
        "scorers": [
            {"scorer": r.scorer, "passed": r.passed, "details": r.details} for r in results
        ],
    }
    res.diff_path = diff_path(ds.name, rid, cid)
    res.diff_sha256 = store.put(res.diff_path, json.dumps(diff, indent=2, default=str).encode())
    return res


# --------------------------------------------------------------------------- advance


def _poll(driver: HarnessDriver, h: dict[str, str]) -> tuple[TurnHandle, TurnStatus]:
    handle = TurnHandle.from_dict(h)
    return handle, driver.turn_status(handle)


def _ask(state: dict[str, Any], cid: str, question: str | None) -> None:
    if state["blind"]:
        state["gap_candidates"].append({"case_id": cid, "question": question or ""})


def _asked_result(case: dict[str, Any], status: TurnStatus, turn: dict[str, Any]) -> CaseResult:
    res = CaseResult(
        case["case_id"],
        passed=False,
        status=STATUS_ASKED,
        miss_cause="missing_context",
        question=status.question,
        source=case["source"],
        tags=list(case["tags"]),
    )
    _apply_turn_metrics(res, turn)
    return res


def _advance_env(
    state: dict[str, Any], ds: Dataset, driver: HarnessDriver, store: ArtifactStore
) -> None:
    env = state["env_check"]
    if env["phase"] != "running":
        return
    handle, status = _poll(driver, env["handle"])
    if status.state == "running":
        return
    if status.state != TURN_COMPLETED:
        raise PlatformError(
            f"probe turn for the target_env check ended {status.state}: {status.error}"
        )
    seen = _probe_data(status, driver.fetch_outputs(handle))
    declared = state["target_env"]
    if _target_matches(declared, seen):
        env["phase"] = "ok"
        return
    env["phase"] = "refused"
    state["status"] = "refused"
    state["refusal"] = (
        f"target_env check failed: the probe reports a target that is not the declared "
        f"{declared!r}; no case was run"
    )
    for cid, c in state["cases"].items():
        case = ds.cases[cid]
        c["result"] = _error_result(case, "target_env mismatch").to_dict()
        c["phase"] = P_DONE


def _start_case_turn(
    ws: Path,
    state: dict[str, Any],
    ds: Dataset,
    cid: str,
    driver: HarnessDriver,
    phase: str,
    snapshot: Any = None,
) -> None:
    case = ds.cases[cid]
    probe = str(state["probe_process_id"])
    if phase == P_SNAPSHOT:
        h = _start(
            driver,
            workstream=ds.workstream(cid),
            process_id=probe,
            message=_probe_message("snapshot", case),
        )
    elif phase == P_TURN:
        h = _start(
            driver,
            workstream=ds.workstream(cid),
            process_id=state["process_id"],
            message=case_message(ws, state["agent"], case),
        )
    elif phase == P_READBACK:
        h = _start(
            driver,
            workstream=ds.workstream(cid),
            process_id=probe,
            message=_probe_message("readback", case),
        )
    else:
        h = _start(
            driver,
            workstream=ds.workstream(cid),
            process_id=probe,
            message=_probe_message("teardown", case, snapshot),
        )
    state["cases"][cid]["phase"] = phase
    state["cases"][cid]["handles"][phase] = h


def _ryw_step(
    ws: Path,
    state: dict[str, Any],
    ds: Dataset,
    cid: str,
    driver: HarnessDriver,
    store: ArtifactStore,
) -> None:
    c = state["cases"][cid]
    case = ds.cases[cid]
    phase = c["phase"]
    if phase == P_SNAPSHOT and P_SNAPSHOT not in c["handles"]:
        _start_case_turn(ws, state, ds, cid, driver, P_SNAPSHOT)
        return
    handle, status = _poll(driver, c["handles"][phase])
    if status.state == "running":
        return
    outputs = driver.fetch_outputs(handle)
    if phase in (P_SNAPSHOT, P_READBACK):
        key = phase
        if status.state != TURN_COMPLETED:
            if phase == P_SNAPSHOT:
                c["result"] = _error_result(case, f"snapshot failed: {status.error}").to_dict()
                c["phase"] = P_DONE
                return
            c["readback_error"] = status.error or "readback failed"
            _start_case_turn(
                ws, state, ds, cid, driver, P_TEARDOWN, _load_ref(store, c["refs"].get("snapshot"))
            )
            return
        data = _probe_data(status, outputs)
        path = ops_path(state["run_id"], "cases", cid, f"{key}.json")
        c["refs"][key] = {
            "path": path,
            "sha256": store.put(path, json.dumps(data, indent=2).encode()),
        }
        if phase == P_SNAPSHOT:
            _start_case_turn(ws, state, ds, cid, driver, P_TURN)
        else:
            _start_case_turn(
                ws, state, ds, cid, driver, P_TEARDOWN, _load_ref(store, c["refs"]["snapshot"])
            )
        return
    if phase == P_TURN:
        c["turn"] = _status_dict(status)
        c["agent_state"] = status.state
        if status.state == TURN_COMPLETED:
            _start_case_turn(ws, state, ds, cid, driver, P_READBACK)
        else:
            if status.state == TURN_NEEDS_INPUT:
                _ask(state, cid, status.question)
            _start_case_turn(
                ws, state, ds, cid, driver, P_TEARDOWN, _load_ref(store, c["refs"].get("snapshot"))
            )
        return
    # teardown finished
    if status.state != TURN_COMPLETED:
        state["teardown_failed"] = True
        c["result"] = _error_result(
            case, f"TEARDOWN FAILED, target left changed: {status.error}", c["turn"]
        ).to_dict()
        c["phase"] = P_DONE
        return
    c["phase"] = P_DONE
    if c.get("agent_state") == TURN_NEEDS_INPUT:
        c["result"] = _asked_result(
            case, TurnStatus(question=(c["turn"] or {}).get("question")), c["turn"]
        ).to_dict()
    elif c.get("agent_state") != TURN_COMPLETED or "readback" not in c["refs"]:
        why = (c["turn"] or {}).get("error") or c.get("readback_error") or "turn failed"
        c["result"] = _error_result(case, str(why), c["turn"]).to_dict()
    else:
        ah = TurnHandle.from_dict(c["handles"][P_TURN])
        ast = driver.turn_status(ah)
        aouts = driver.fetch_outputs(ah)
        observed = _observed(ast, aouts)
        observed["snapshot"] = _load_ref(store, c["refs"].get("snapshot"))
        observed["readback"] = _load_ref(store, c["refs"].get("readback"))
        res = _finish_case(
            ws,
            state,
            ds,
            case,
            observed,
            ast,
            store,
            outputs=aouts,
            transcript=driver.fetch_transcript(ah),
        )
        c["result"] = res.to_dict()


def _load_ref(store: ArtifactStore, ref: dict[str, str] | None) -> Any:
    if not ref:
        return None
    return json.loads(store.get(ref["path"]).decode())


def _plain_step(
    ws: Path,
    state: dict[str, Any],
    ds: Dataset,
    cid: str,
    driver: HarnessDriver,
    store: ArtifactStore,
) -> None:
    c = state["cases"][cid]
    case = ds.cases[cid]
    handle, status = _poll(driver, c["handles"][P_TURN])
    if status.state == "running":
        return
    c["turn"] = _status_dict(status)
    c["phase"] = P_DONE
    if status.state == TURN_NEEDS_INPUT:
        answer = state["answers"].get(cid)
        if answer and not state["blind"]:
            h = _start(
                driver,
                workstream=ds.workstream(cid),
                process_id=state["process_id"],
                message=answer,
                conversation_id=handle.conversation_id,
            )
            c["answers_sent"] = c.get("answers_sent", 0) + 1
            c["handles"][f"turn_q{c['answers_sent']}"] = c["handles"][P_TURN]
            c["handles"][P_TURN] = h
            c["phase"] = P_TURN
            return
        _ask(state, cid, status.question)
        c["result"] = _asked_result(case, status, c["turn"]).to_dict()
        return
    if status.state == TURN_FAILED:
        c["result"] = _error_result(case, status.error or "turn failed", c["turn"]).to_dict()
        return
    outputs = driver.fetch_outputs(handle)
    observed = _observed(status, outputs)
    res = _finish_case(
        ws,
        state,
        ds,
        case,
        observed,
        status,
        store,
        outputs=outputs,
        transcript=driver.fetch_transcript(handle),
    )
    c["result"] = res.to_dict()


def _batch_step(
    ws: Path, state: dict[str, Any], ds: Dataset, driver: HarnessDriver, store: ArtifactStore
) -> None:
    batch = state["batch"]
    if batch["phase"] != "running":
        return
    handle, status = _poll(driver, batch["handle"])
    if status.state == "running":
        return
    batch["phase"] = "done"
    n = len(state["cases"])
    turn = _status_dict(status)
    transcript = driver.fetch_transcript(handle) if status.state != TURN_FAILED else ""
    outputs = driver.fetch_outputs(handle) if status.state != TURN_FAILED else {}
    combined = _parse_json(outputs.get("results.json", b"null")) or {}
    for cid, c in state["cases"].items():
        case = ds.cases[cid]
        c["turn"] = turn
        c["phase"] = P_DONE
        if status.state == TURN_FAILED:
            c["result"] = _error_result(case, status.error or "batch turn failed", turn).to_dict()
            continue
        if status.state == TURN_NEEDS_INPUT:
            _ask(state, cid, status.question)
            c["result"] = _asked_result(case, status, turn).to_dict()
            continue
        own = {k: v for k, v in outputs.items() if k.startswith(f"{cid}/") or k == f"{cid}.json"}
        obs = _observed(status, own)
        if isinstance(combined, dict) and cid in combined:
            obs["data"][f"{cid}.json"] = combined[cid]
        res = _finish_case(
            ws,
            state,
            ds,
            case,
            obs,
            status,
            store,
            share=1.0 / n,
            outputs=own,
            transcript=transcript,
        )
        c["result"] = res.to_dict()


def advance(
    ws: Path,
    run_id: str,
    driver: HarnessDriver,
    store: ArtifactStore,
    rows: RowStore | None = None,
) -> dict[str, Any]:
    """Poll the persisted turns once and move every case as far as it can go."""
    state = load_state(ws, run_id)
    if state["status"] != "running":
        return state
    ds = load_dataset(ws, state["dataset"])
    if state["kind"] == "read_your_writes":
        _advance_env(state, ds, driver, store)
        if state["env_check"]["phase"] == "ok":
            # Sequential: one case at a time on the shared workstream.
            for cid, c in state["cases"].items():
                if c["phase"] == P_DONE:
                    continue
                _ryw_step(ws, state, ds, cid, driver, store)
                save_state(ws, state)
                break
    elif state["run_mode"] == "batch":
        _batch_step(ws, state, ds, driver, store)
    else:
        for cid, c in state["cases"].items():
            if c["phase"] == P_TURN:
                _plain_step(ws, state, ds, cid, driver, store)
                save_state(ws, state)
    if state["status"] == "running" and all(c["phase"] == P_DONE for c in state["cases"].values()):
        state["status"] = "complete"
    save_state(ws, state)
    _sync_row(state, rows)
    return state


# --------------------------------------------------------------------------- collect


def results_from_state(state: dict[str, Any], open_gap_count: int | None = None) -> RunResults:
    cases = [
        CaseResult.from_dict(c["result"])
        for _, c in sorted(state["cases"].items())
        if c.get("result")
    ]
    return RunResults(
        run_id=state["run_id"],
        agent=state["agent"],
        dataset=state["dataset"],
        dataset_version=int(state["dataset_version"]),
        blind=bool(state["blind"]),
        cases=cases,
        tier=state["tier"],
        version=state.get("version", ""),
        open_gaps=open_gap_count,
        meta={"gap_candidates": state["gap_candidates"], "refusal": state.get("refusal")},
    )


def result_rows(results: RunResults) -> list[dict[str, Any]]:
    """``bld_eval_results`` rows: paths, hashes and scores only."""
    return [
        {
            "result_key": f"{results.run_id}:{c.case_id}",
            "eval_run_id": results.run_id,
            "case_key": f"{results.dataset}:{c.case_id}",
            "observed_path": c.observed_path,
            "observed_sha256": c.observed_sha256,
            "diff_path": c.diff_path,
            "diff_sha256": c.diff_sha256,
            "passed": c.passed,
            "score": c.score,
            "failed_checks": list(c.failed_check_ids),
            "miss_cause": _MISS_CAUSE.get(c.miss_cause or "", c.miss_cause),
            "steps": c.steps,
            "tokens": c.tokens,
            "duration_s": int(round(c.duration_s)),
            "hitl_count": c.hitl_count,
        }
        for c in results.cases
    ]


def collect(
    ws: Path,
    run_id: str,
    store: ArtifactStore,
    rows: RowStore | None = None,
    *,
    open_gap_count: int | None = None,
) -> RunResults:
    """Write summary.json and the result/metric rows; return the results of a finished run."""
    state = load_state(ws, run_id)
    if state["status"] == "running":
        raise UsageError(f"run {run_id} is still running; advance it first")
    results = results_from_state(state, open_gap_count)
    summary = {
        "run_id": run_id,
        "agent": state["agent"],
        "dataset": state["dataset"],
        "dataset_version": state["dataset_version"],
        "blind": state["blind"],
        "tier": state["tier"],
        "status": state["status"],
        "refusal": state.get("refusal"),
        "cases": [c.to_dict() for c in results.cases],
        "gap_candidates": state["gap_candidates"],
    }
    n = _summary_count(state)
    save_state(ws, state)
    store.put(ops_path(run_id, f"summary.v{n}.json"), json.dumps(summary, indent=2).encode())
    if rows is not None:
        for r in result_rows(results):
            rows.insert("bld_eval_results", r)
        for m in metric_rows(results):
            rows.insert("bld_metric_values", m)
    if state["status"] == "refused":
        raise RefusedError(str(state["refusal"]))
    return results


def _summary_count(state: dict[str, Any]) -> int:
    """Never overwrite: each collect after a judgement gets a fresh summary path."""
    state["summary_count"] = int(state.get("summary_count", 0)) + 1
    return int(state["summary_count"])


def run_eval(
    ws: Path,
    *,
    driver: HarnessDriver,
    store: ArtifactStore,
    rows: RowStore | None = None,
    poll_interval: float = 2.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    **submit_kwargs: Any,
) -> RunResults:
    """Submit, advance until complete (or timeout), collect."""
    state = submit_run(ws, driver=driver, store=store, rows=rows, **submit_kwargs)
    rid = state["run_id"]
    waited = 0.0
    while True:
        state = advance(ws, rid, driver, store, rows)
        if state["status"] != "running":
            break
        if waited >= timeout:
            raise PlatformError(f"eval run {rid} did not finish in {timeout}s; resume with advance")
        sleep(poll_interval)
        waited += poll_interval
    return collect(ws, rid, store, rows)


# --------------------------------------------------------------------------- judging


def build_packet(ws: Path, run_id: str, store: ArtifactStore) -> str | None:
    """Judging packet for pending rubrics/judged checks. Goes to ds-<dataset>, not the repo."""
    state = load_state(ws, run_id)
    ds = load_dataset(ws, state["dataset"])
    items = []
    checks = {c["check_id"]: c for c in load_checks(ws)}
    for cid, c in state["cases"].items():
        r = c.get("result")
        if not r or r["status"] != STATUS_PENDING:
            continue
        case = ds.cases[cid]
        criteria: list[Any] = []
        for spec in effective_scorers(ds, case):
            name, cfg = normalize_scorer(spec)
            if name == "rubric":
                criteria.extend(cfg.get("criteria", []))
        items.append(
            {
                "case_id": cid,
                "criteria": criteria,
                "judged_checks": {
                    i: checks[i].get("rubric", "") for i in r["pending_check_ids"] if i in checks
                },
                "observed": json.loads(store.get(r["observed_path"]).decode()),
                "expected": load_expected(store, case, ds.name),
            }
        )
    if not items:
        return None
    path = packet_path(ds.name, run_id)
    store.put(path, json.dumps(build_judging_packet(run_id, items), indent=2).encode())
    state["packet_path"] = path
    save_state(ws, state)
    return path


def apply_judgements(
    ws: Path,
    run_id: str,
    scores: dict[str, dict[str, Any]],
    *,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Apply the coding agent's scores file (``{case_id: {scores, passed?, checks?, notes}}``)."""
    state = load_state(ws, run_id)
    for cid, entry in scores.items():
        c = state["cases"].get(cid)
        if not c or not c.get("result") or c["result"]["status"] != STATUS_PENDING:
            raise UsageError(f"case {cid!r} is not pending judgement in run {run_id}")
        res = CaseResult.from_dict(c["result"])
        judged = apply_judged_scores(entry, threshold)
        summaries = [judged.summary() if s["scorer"] == "rubric" else s for s in res.scorers]
        verdicts: dict[str, bool] = {str(k): bool(v) for k, v in entry.get("checks", {}).items()}
        missing = [i for i in res.pending_check_ids if i not in verdicts]
        if missing:
            raise UsageError(f"case {cid}: no verdict for judged checks {missing}")
        pending_checks = [CheckResult(i, verdicts[i]) for i in res.pending_check_ids]
        # failed checks from before stay failed
        pending_checks += [CheckResult(i, False) for i in res.failed_check_ids]
        _combine(res, summaries, pending_checks)
        c["result"] = res.to_dict()
    state["status"] = (
        "complete"
        if all(c["phase"] == P_DONE for c in state["cases"].values())
        else state["status"]
    )
    save_state(ws, state)
    return state


__all__ = [
    "advance",
    "apply_judgements",
    "build_packet",
    "case_message",
    "collect",
    "load_state",
    "results_from_state",
    "run_eval",
    "submit_run",
]
