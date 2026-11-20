"""Scorers (PLAN.md section 10) and deterministic client checks.

Shapes passed to every scorer:

``observed``  ``{"text": str, "files": {name: sha256}, "data": {name: parsed JSON},
               "question": str | None, "snapshot": ..., "readback": ...}``
``expected``  ``{"files": {rel_name: sha256}, "data": {rel_name: parsed JSON}, "text": {...}}``

A scorer returns a `ScorerResult`. Details may contain expected values, so they are only
ever written to the ``ds-<dataset>`` store (diff file), never to rows or the workspace.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.builder_common.datasets import normalize_scorer
from cli.builder_common.errors import RefusedError, UsageError

Json = Any


@dataclass
class ScorerResult:
    scorer: str
    passed: bool | None  # None while a judged rubric is pending
    score: float | None
    details: dict[str, Any] = field(default_factory=dict)
    pending: bool = False

    def summary(self) -> dict[str, Any]:
        """The value-free summary that is safe to persist in state and rows."""
        return {
            "scorer": self.scorer,
            "passed": self.passed,
            "score": self.score,
            "pending": self.pending,
        }


# --------------------------------------------------------------------------- helpers


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def flatten(obj: Json, prefix: str = "") -> dict[str, Json]:
    if isinstance(obj, dict):
        out: dict[str, Json] = {}
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(obj, list):
        out = {}
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: obj}


def _first(d: dict[str, Any]) -> tuple[str | None, Any]:
    for k in sorted(d):
        return k, d[k]
    return None, None


def _pick(
    side: dict[str, Any], file: str | None, *, single_fallback: bool = True
) -> tuple[str | None, Any]:
    data = side.get("data", {})
    if file:
        if file in data:
            return file, data[file]
        for k, v in data.items():
            if k.rsplit("/", 1)[-1] == file.rsplit("/", 1)[-1]:
                return k, v
        return None, None
    return _first(data) if single_fallback else (None, None)


def pair(expected: dict[str, Any], observed: dict[str, Any], file: str | None) -> tuple[Any, Any]:
    """The expected and observed JSON objects to compare."""
    ename, eobj = _pick(expected, file, single_fallback=True)
    _, oobj = _pick(observed, file or ename)
    if oobj is None:
        data = observed.get("data", {})
        if len(data) == 1:
            oobj = next(iter(data.values()))
    return eobj, oobj


def find_key(observed: dict[str, Any], key: str) -> Any:
    for v in observed.get("data", {}).values():
        if isinstance(v, dict) and key in v:
            return v[key]
    return None


def _equal(e: Any, o: Any, tol: float, ci: bool) -> tuple[bool, bool]:
    """Returns (equal, rounding_only_difference)."""
    if _is_num(e) and _is_num(o):
        diff = abs(e - o)
        return diff <= tol, 0 < diff <= 1.0
    if isinstance(e, str) and isinstance(o, str):
        a, b = e.strip(), o.strip()
        return (a.casefold() == b.casefold() if ci else a == b), False
    return e == o, False


def compare_leaves(
    exp: Json,
    obs: Json,
    *,
    fields: list[str] | None = None,
    ignore: list[str] | None = None,
    tolerance: float = 0.0,
    case_insensitive: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Compare expected leaves against observed. Returns (mismatches, total expected leaves)."""
    eflat = flatten(exp)
    oflat = flatten(obs) if obs is not None else {}
    mismatches: list[dict[str, Any]] = []
    total = 0
    for k, ev in eflat.items():
        if fields and not any(
            k == f or k.startswith(f + ".") or k.startswith(f + "[") for f in fields
        ):
            continue
        if ignore and any(k == i or k.startswith(i + ".") for i in ignore):
            continue
        total += 1
        ov = oflat.get(k)
        ok, rounding = _equal(ev, ov, tolerance, case_insensitive)
        if not ok:
            mismatches.append({"field": k, "expected": ev, "observed": ov, "rounding": rounding})
    return mismatches, total


# --------------------------------------------------------------------------- scorers


def field_match(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    eobj, oobj = pair(expected, observed, config.get("file"))
    if eobj is None:
        return ScorerResult("field_match", False, 0.0, {"error": "no expected data to compare"})
    mism, total = compare_leaves(
        eobj,
        oobj,
        fields=config.get("fields"),
        ignore=config.get("ignore"),
        tolerance=float(config.get("tolerance", 0.0)),
        case_insensitive=bool(config.get("case_insensitive", False)),
    )
    score = 1.0 if total == 0 else (total - len(mism)) / total
    passed = score >= float(config["threshold"]) if "threshold" in config else not mism
    return ScorerResult(
        "field_match",
        passed,
        score,
        {"mismatches": mism, "total": total, "matched": total - len(mism)},
    )


def rubric(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    """Judged: the coding agent scores a packet (see `build_judging_packet`)."""
    return ScorerResult(
        "rubric", None, None, {"criteria": list(config.get("criteria", []))}, pending=True
    )


def _norm_label(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(str(x).strip().casefold() for x in v)
    return None if v is None else str(v).strip().casefold()


def label_match(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    key = config.get("key", "label")
    eobj, oobj = pair(expected, observed, config.get("file"))
    e = eobj.get(key) if isinstance(eobj, dict) else eobj
    o = oobj.get(key) if isinstance(oobj, dict) else oobj
    ok = e is not None and _norm_label(e) == _norm_label(o)
    extra: dict[str, Any] = {}
    if isinstance(eobj, dict) and "doc_types" in eobj and config.get("check_doc_types", False):
        dt_ok = _norm_label(eobj.get("doc_types")) == _norm_label(
            oobj.get("doc_types") if isinstance(oobj, dict) else None
        )
        extra["doc_types_ok"] = dt_ok
        ok = ok and dt_ok
    return ScorerResult(
        "label_match",
        ok,
        1.0 if ok else 0.0,
        {"expected_label": e, "observed_label": o, "client": case.get("case_id"), **extra},
    )


def confusion_matrix(pairs: Iterable[tuple[Any, Any]]) -> dict[str, Any]:
    """Confusion matrix over (expected, observed) label pairs, with per-label precision/recall."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    labels: set[str] = set()
    for e, o in pairs:
        es, os_ = str(e), "(none)" if o is None else str(o)
        matrix[es][os_] += 1
        labels.update([es, os_])
    per: dict[str, dict[str, float]] = {}
    for lab in sorted(labels):
        tp = matrix[lab][lab]
        support = sum(matrix[lab].values())
        predicted = sum(matrix[e].get(lab, 0) for e in matrix)
        per[lab] = {
            "support": support,
            "precision": tp / predicted if predicted else 0.0,
            "recall": tp / support if support else 0.0,
        }
    return {
        "labels": sorted(labels),
        "matrix": {e: dict(row) for e, row in sorted(matrix.items())},
        "per_label": per,
    }


def _starts(v: Any) -> set[int]:
    out: set[int] = set()
    for item in v or []:
        if isinstance(item, list | tuple) and item:
            out.add(int(item[0]))
        elif isinstance(item, dict) and "start" in item:
            out.add(int(item["start"]))
        elif _is_num(item):
            out.add(int(item))
    return out


def boundary_match(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    key = config.get("key", "boundaries")
    eobj, oobj = pair(expected, observed, config.get("file"))
    es = _starts(eobj.get(key) if isinstance(eobj, dict) else None)
    os_ = _starts(oobj.get(key) if isinstance(oobj, dict) else None)
    tp = len(es & os_)
    prec = tp / len(os_) if os_ else 0.0
    rec = tp / len(es) if es else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    passed = f1 >= float(config["threshold"]) if "threshold" in config else es == os_
    return ScorerResult(
        "boundary_match",
        passed,
        f1,
        {
            "expected_starts": sorted(es),
            "observed_starts": sorted(os_),
            "precision": prec,
            "recall": rec,
        },
    )


def outcome_class(code: Any) -> str:
    """0 updated, 1 updated with an overwrite warning, below 0 refused."""
    if not _is_num(code):
        return "unknown"
    if code < 0:
        return "refused"
    return {0: "updated", 1: "overwrite_warning"}.get(int(code), "unknown")


def outcome_match(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    key = config.get("key", "outcome_code")
    eobj, _ = pair(expected, observed, config.get("file"))
    ecode = eobj.get(key) if isinstance(eobj, dict) else None
    ocode = find_key(observed, key)
    ec, oc = outcome_class(ecode), outcome_class(ocode)
    ok = ec != "unknown" and ec == oc
    if config.get("exact"):
        ok = ok and ecode == ocode
    return ScorerResult(
        "outcome_match",
        ok,
        1.0 if ok else 0.0,
        {
            "expected_code": ecode,
            "observed_code": ocode,
            "expected_class": ec,
            "observed_class": oc,
        },
    )


def _readback_target(observed: dict[str, Any]) -> Any:
    rb = observed.get("readback")
    if isinstance(rb, dict):
        return rb.get("target", rb)
    return find_key(observed, "state")


def state_match(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    eobj, _ = pair(expected, observed, config.get("file"))
    estate = eobj.get("state", eobj) if isinstance(eobj, dict) else eobj
    if estate is None:
        return ScorerResult("state_match", False, 0.0, {"error": "no expected state"})
    mism, total = compare_leaves(
        estate,
        _readback_target(observed),
        fields=config.get("fields"),
        ignore=config.get("ignore"),
        tolerance=float(config.get("tolerance", 0.0)),
    )
    score = 1.0 if total == 0 else (total - len(mism)) / total
    return ScorerResult("state_match", not mism, score, {"mismatches": mism, "total": total})


def collateral(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    """Other rows in scope must be unchanged between snapshot and read-back."""
    key = config.get("key", "others")
    snap, rb = observed.get("snapshot"), observed.get("readback")
    if not isinstance(snap, dict) or not isinstance(rb, dict):
        return ScorerResult("collateral", False, 0.0, {"error": "missing snapshot or readback"})
    eobj, _ = pair(expected, observed, config.get("file"))
    allowed = list(eobj.get("allowed_changes", [])) if isinstance(eobj, dict) else []
    mism, total = compare_leaves(snap.get(key, {}), rb.get(key, {}), ignore=allowed)
    return ScorerResult(
        "collateral", not mism, 1.0 if not mism else 0.0, {"changed": mism, "checked": total}
    )


def _json_nodes(value: Any, depth: int = 0) -> Iterable[Any]:
    """Every dict in a transcript value, looking inside JSON held in strings (tool results)."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _json_nodes(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _json_nodes(v, depth + 1)
    elif isinstance(value, str) and depth < 6:
        text = value.strip()
        if text[:1] in "{[" and text[-1:] in "}]":
            try:
                parsed = json.loads(text)
            except ValueError:
                return
            yield from _json_nodes(parsed, depth + 1)


def _transcript_nodes(transcript: Any) -> list[dict[str, Any]]:
    if isinstance(transcript, str):
        events: list[Any] = []
        for line in transcript.splitlines():
            line = line.strip()
            if line[:1] in "{[":
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    elif isinstance(transcript, list):
        events = transcript
    else:
        events = []
    return [n for ev in events for n in _json_nodes(ev)]


def goal_signals(transcript: Any, goal: dict[str, Any]) -> dict[str, Any]:
    """What a transcript shows about one skill goal, with the signals NoUI's replay uses.

    The goal is ``{operation, kind}``. Evidence is read the way ``noui_core.verify.replay`` reads
    it: an operation result ``{name, goal_reached}`` (the last one for the goal's operation
    wins), and for a ``download`` goal also a ``list_downloads`` result that lists a file.
    Nothing from the transcript beyond booleans and counts is returned.
    """
    operation = str(goal.get("operation") or "")
    nodes = _transcript_nodes(transcript)
    verdicts = [
        n["goal_reached"]
        for n in nodes
        if isinstance(n.get("goal_reached"), bool)
        and operation
        and operation in (n.get("name"), n.get("operation"))
    ]
    downloads = 0
    for n in nodes:
        listed = n.get("downloads")
        if isinstance(listed, list):
            downloads = max(downloads, sum(1 for r in listed if isinstance(r, dict)))
    kind = str(goal.get("kind") or "read")
    reached = verdicts[-1] if verdicts else False
    if kind == "download" and not verdicts:
        reached = downloads > 0
    elif kind == "download":
        reached = reached and downloads > 0
    return {
        "reached": bool(reached),
        "operation": operation,
        "kind": kind,
        "verdicts": len(verdicts),
        "downloads_listed": downloads,
        "transcript_events": len(nodes),
    }


def goal_reached(
    case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any], config: dict[str, Any]
) -> ScorerResult:
    """Passes when the transcript shows the skill's goal reached (config: ``operation``, ``kind``)."""
    if not config.get("operation"):
        return ScorerResult("goal_reached", False, 0.0, {"error": "no operation configured"})
    transcript = observed.get("transcript")
    if not transcript:
        return ScorerResult("goal_reached", False, 0.0, {"error": "no transcript to read"})
    sig = goal_signals(transcript, config)
    return ScorerResult("goal_reached", sig["reached"], 1.0 if sig["reached"] else 0.0, sig)


BUILTIN: dict[str, Callable[..., ScorerResult]] = {
    "field_match": field_match,
    "rubric": rubric,
    "label_match": label_match,
    "boundary_match": boundary_match,
    "outcome_match": outcome_match,
    "state_match": state_match,
    "collateral": collateral,
    "goal_reached": goal_reached,
}


def run_script_scorer(
    ws: Path, name: str, case: dict[str, Any], observed: dict[str, Any], expected: dict[str, Any]
) -> ScorerResult:
    """`script:<name>` loads harness/evals/scorers/<name>.py; contract:
    ``score(case, observed, expected) -> {"passed": bool, "score": float, "details": {...}}``."""
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
        raise UsageError(f"bad scorer script name {name!r}")
    path = ws / "harness" / "evals" / "scorers" / f"{name}.py"
    if not path.exists():
        raise UsageError(f"scorer script not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_abcd_scorer_{name}", path)
    if spec is None or spec.loader is None:
        raise UsageError(f"cannot load scorer script {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "score", None)
    if not callable(fn):
        raise UsageError(f"{path} must define score(case, observed, expected) -> dict")
    res = fn(case, observed, expected)
    if not isinstance(res, dict) or "passed" not in res:
        raise UsageError(f"{path}: score() must return a dict with at least 'passed'")
    passed = bool(res["passed"])
    score = float(res.get("score", 1.0 if passed else 0.0))
    return ScorerResult(f"script:{name}", passed, score, dict(res.get("details", {})))


def score_case(
    ws: Path,
    case: dict[str, Any],
    observed: dict[str, Any],
    expected: dict[str, Any],
    scorers: list[Any],
) -> list[ScorerResult]:
    results = []
    for spec in scorers:
        name, cfg = normalize_scorer(spec)
        if name.startswith("script:"):
            results.append(run_script_scorer(ws, name[7:], case, observed, expected))
        else:
            results.append(BUILTIN[name](case, observed, expected, cfg))
    return results


# --------------------------------------------------------------------------- client checks


@dataclass
class CheckResult:
    check_id: str
    passed: bool | None
    severity: str = "error"
    pending: bool = False
    detail: str = ""


def load_checks(
    ws: Path, ids: list[str] | None = None, *, include_drafts: bool = False
) -> list[dict[str, Any]]:
    """The workspace checks. A check with ``status: draft`` is not scored until it is approved."""
    import yaml

    root = ws / "harness" / "checks"
    out = []
    if root.exists():
        for p in sorted(root.glob("*.yaml")):
            c = yaml.safe_load(p.read_text()) or {}
            c.setdefault("check_id", p.stem)
            if c.get("status") == "draft" and not include_drafts:
                continue
            if ids is None or c["check_id"] in ids:
                out.append(c)
    return out


def evaluate_check(check: dict[str, Any], observed: dict[str, Any]) -> CheckResult:
    """Deterministic rule types: present, equals, range, regex. Judged checks stay pending."""
    cid = str(check.get("check_id", "?"))
    sev = str(check.get("severity", "error"))
    if check.get("kind") == "goal_reached":
        res = goal_reached({}, observed, {}, dict(check.get("goal") or {}))
        return CheckResult(cid, res.passed, sev, detail=str(res.details.get("operation", "")))
    if check.get("kind") == "judged" or "rubric" in check:
        return CheckResult(cid, None, sev, pending=True)
    rule = check.get("rule") or {}
    typ = rule.get("type")
    flat: dict[str, Any] = {}
    for v in observed.get("data", {}).values():
        flat.update(flatten(v))
    val = flat.get(rule.get("field", ""))
    if typ == "present":
        ok = val not in (None, "")
    elif typ == "equals":
        ok = val == rule.get("value")
    elif typ == "range":
        ok = _is_num(val) and rule.get("min", float("-inf")) <= val <= rule.get("max", float("inf"))
    elif typ == "regex":
        ok = isinstance(val, str) and re.search(str(rule.get("pattern", "")), val) is not None
    else:
        raise RefusedError(f"check {cid}: unknown rule type {typ!r}")
    return CheckResult(cid, bool(ok), sev, detail=f"{rule.get('field')}")


# --------------------------------------------------------------------------- judging


def build_judging_packet(run_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Packet for the coding agent. ``items``: case_id, criteria, observed, expected.

    It carries expected values, so it goes to the ds- store (and the local session), never the
    workspace repo. The agent answers with a scores file: ``{case_id: {"scores": {criterion:
    0..1 | bool}, "passed": bool?, "notes": str}}``.
    """
    return {
        "run_id": run_id,
        "instructions": "Score each criterion 0..1 (or true/false) against the expected output.",
        "items": items,
        "scores_file_shape": {"<case_id>": {"scores": {"<criterion>": 1}, "notes": ""}},
    }


def apply_judged_scores(entry: dict[str, Any], threshold: float = 0.5) -> ScorerResult:
    """Turn one scores-file entry into a ScorerResult."""
    scores = entry.get("scores", {})
    vals = [float(v) for v in scores.values()] if scores else []
    avg = sum(vals) / len(vals) if vals else 0.0
    passed = bool(entry["passed"]) if "passed" in entry else bool(vals) and min(vals) >= threshold
    return ScorerResult("rubric", passed, avg, {"scores": scores, "notes": entry.get("notes", "")})
