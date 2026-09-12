#!/usr/bin/env python3
"""
analyze_run_trace.py -- measure where a real harness turn spent its time and its steps.

Static review estimates the step floor a skill's text forces. This measures what runs
actually did, from exported Temporal histories of ``AgentHarnessTurnWorkflow``. Pass one
file per turn; a multi-turn job is only diagnosable across all of its turns.

Two cost axes, and they are NOT the same:

  STEPS      one assistant response = one step against the 100 ceiling.
  TOKENS     time spent generating output. On a real production run this was 59% of
             total agent time -- 200k output tokens, two-thirds of it Python the agent
             typed because the skill did not ship it. Cutting steps does not cut this.

A skill can be step-efficient and still slow, so both are reported.

Get the histories
    temporal workflow show --workflow-id <agent-harness-...> --output json > turn1.json
  or export from the Temporal UI ("Download" on the workflow page).

Usage
    python3 analyze_run_trace.py turn1.json [turn2.json ...] [--format json|text]
                                            [--top N] [--min-repeat-tokens N]

Exit codes
    0  parsed
    3  unreadable / not a Temporal history
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Cost model. Fitted on a real production run (n=269, outliers excluded):
#   step duration ~= 2.23s + 10.7ms per output token
# Used only where a measured duration is unavailable; measured always wins.
# ---------------------------------------------------------------------------
FIXED_CALL_OVERHEAD_S = 2.23
SECONDS_PER_OUTPUT_TOKEN = 0.0107

STEP_CEILING = 100
CLEAR_TRIGGER_PCT = 60
COMPACT_TRIGGER_PCT = 80

READER_RE = re.compile(r"\b(grep|sed|cat|head|tail|wc|find|ls|awk|nl|strings)\b")
STAGED_SCRIPT_RE = re.compile(r"(skill_assets/[^\s'\"|;&]*?\.(?:py|sh))")
RUN_SCRIPT_RE = re.compile(r"\b(?:python3?|sh|bash)\s+[^\s|;&]*skill_assets/[^\s|;&]*\.(?:py|sh)")
INLINE_PY_RE = re.compile(r"\bpython3?\s+-c\b")
HEREDOC_RE = re.compile(r"<<\s*['\"]?(?:PY|EOF|SH)")

NARRATIVE_TOOLS = {"set_plan", "start_phase", "end_phase", "start_chip", "reflect", "suggest"}
PROCESS_TOOLS = {"start_process", "update_process_step", "start_inline_process"}
LIFECYCLE_TOOLS = NARRATIVE_TOOLS | PROCESS_TOOLS
NOTE_TOOLS = {"memory", "reflect"}

# Rough token estimate for a text blob when the log gives no count.
def est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def _maybe_json(s):
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t or t[0] not in "{[":
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def payload_objects(node) -> list:
    """Decode a Temporal payload container.

    Real exports use ``metadata.encoding = json/plain`` with ``data`` as a RAW JSON
    string; others base64 it. Try JSON first, then base64 -- and tolerate missing
    padding, which some exporters strip.
    """
    out = []
    payloads = None
    for key in ("payloads", "Payloads"):
        if isinstance(node, dict) and isinstance(node.get(key), list):
            payloads = node[key]
            break
    if payloads is None:
        return out
    for p in payloads:
        if not isinstance(p, dict):
            continue
        data = p.get("data", p.get("Data"))
        if data is None:
            continue
        if isinstance(data, (dict, list)):
            out.append(data)
            continue
        if not isinstance(data, str):
            continue
        parsed = _maybe_json(data)
        if parsed is not None:
            out.append(parsed)
            continue
        for pad in ("", "=", "=="):
            try:
                raw = base64.b64decode(data + pad)
            except Exception:  # noqa: BLE001
                continue
            parsed = _maybe_json(raw.decode("utf-8", "replace"))
            if parsed is not None:
                out.append(parsed)
            break
    return out


def parse_ts(s: str) -> float | None:
    if not isinstance(s, str):
        return None
    t = s.rstrip("Z")
    if "." in t:
        head, frac = t.split(".", 1)
        frac = (frac + "000000")[:6]
        t = f"{head}.{frac}"
    try:
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def attrs_of(ev: dict, suffix: str) -> dict | None:
    for k, v in ev.items():
        if isinstance(v, dict) and k.lower().endswith(suffix.lower()):
            return v
    return None


def classify_bash(cmd: str) -> str:
    if INLINE_PY_RE.search(cmd) or HEREDOC_RE.search(cmd):
        return "inline_code"
    if RUN_SCRIPT_RE.search(cmd):
        return "ran_script"
    if STAGED_SCRIPT_RE.search(cmd) and READER_RE.search(cmd):
        return "read_script"
    return "other_bash"


# ---------------------------------------------------------------------------
# Per-turn extraction
# ---------------------------------------------------------------------------

def load_events(path: Path) -> list:
    doc = json.loads(path.read_text("utf-8"))
    if isinstance(doc, list):
        return doc
    for key in ("events", "Events"):
        if isinstance(doc.get(key), list):
            return doc[key]
    for d in walk(doc):
        if isinstance(d.get("events"), list):
            return d["events"]
    raise ValueError(f"{path.name}: no events array -- is this a Temporal history?")


def analyze_turn(path: Path) -> dict:
    events = load_events(path)

    sched: dict[int, tuple[str, float | None]] = {}
    started: dict[int, float] = {}
    steps: list[dict] = []
    tool_calls: list[dict] = []
    durations: list[tuple[str, float]] = []
    first_ts = last_ts = None
    cur_step = [0]  # boxed so the event loop can bump it

    for ev in events:
        ts = parse_ts(ev.get("eventTime") or "")
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

        a = attrs_of(ev, "ActivityTaskScheduledEventAttributes")
        if a:
            name = (a.get("activityType") or {}).get("name") or ""
            sched[ev.get("eventId")] = (name, ts)
            # The workflow schedules a step's tool dispatches AFTER that step's
            # llm_step_activity, so event order attributes each call to the step
            # that emitted it. That is what makes "a step spent on nothing but a
            # progress note" countable rather than a guess.
            if name == "llm_step_activity":
                cur_step[0] += 1
            elif name == "dispatch_tool_activity":
                for pl in payload_objects(a.get("input") or {}):
                    for d in walk(pl):
                        if d.get("kind") == "tool_use" and d.get("name"):
                            tool_calls.append({"name": d["name"],
                                               "input": d.get("input") or {},
                                               "step": cur_step[0]})
                            break
            continue

        s = attrs_of(ev, "ActivityTaskStartedEventAttributes")
        if s and s.get("scheduledEventId") is not None and ts is not None:
            started[s["scheduledEventId"]] = ts
            continue

        c = attrs_of(ev, "ActivityTaskCompletedEventAttributes")
        if not c:
            continue
        sid = c.get("scheduledEventId")
        name = (sched.get(sid) or ("", None))[0]
        if sid in started and ts is not None:
            durations.append((name, ts - started[sid]))
        if name != "llm_step_activity":
            continue
        for pl in payload_objects(c.get("result") or {}):
            if isinstance(pl, dict) and ("output_tokens" in pl or "stop_reason" in pl):
                steps.append({
                    "output_tokens": pl.get("output_tokens"),
                    "input_tokens": pl.get("input_tokens"),
                    "cache_read": pl.get("cache_read_input_tokens"),
                    "cache_creation": pl.get("cache_creation_input_tokens"),
                    "context_percent": pl.get("context_percent"),
                    "stop_reason": pl.get("stop_reason"),
                    "duration_s": (ts - started[sid]) if sid in started and ts is not None else None,
                })
                break

    llm_steps = sum(1 for n, _ in sched.values() if n == "llm_step_activity")
    out_tokens = [s["output_tokens"] for s in steps if isinstance(s["output_tokens"], int)]
    step_durs = [s["duration_s"] for s in steps if isinstance(s["duration_s"], (int, float))]
    tool_durs = [d for n, d in durations if n == "dispatch_tool_activity"]

    # Generation time: measured step time is the honest number; the token model
    # explains how much of it is attributable to typing output.
    modelled_gen = sum(FIXED_CALL_OVERHEAD_S + SECONDS_PER_OUTPUT_TOKEN * t for t in out_tokens)
    token_only = sum(SECONDS_PER_OUTPUT_TOKEN * t for t in out_tokens)

    # bash classification
    bash_buckets = Counter()
    bash_examples: dict[str, list[str]] = defaultdict(list)
    hot_files = Counter()
    big_bodies: dict[str, dict] = {}
    for c in tool_calls:
        if c["name"] != "bash":
            continue
        cmd = str((c["input"] or {}).get("command") or "")
        b = classify_bash(cmd)
        bash_buckets[b] += 1
        if len(bash_examples[b]) < 5:
            bash_examples[b].append(cmd.strip()[:200])
        for m in STAGED_SCRIPT_RE.finditer(cmd):
            if READER_RE.search(cmd):
                hot_files[m.group(1).rsplit("/", 1)[-1]] += 1
        # A large command body re-emitted verbatim is the model retyping code.
        if len(cmd) >= 1200:
            key = hashlib.sha1(re.sub(r"\s+", " ", cmd).encode()).hexdigest()[:12]
            rec = big_bodies.setdefault(key, {"chars": len(cmd), "count": 0,
                                              "sample": cmd.strip()[:160]})
            rec["count"] += 1

    # Progress-note / narration volume
    note_chars = 0
    note_calls = 0
    for c in tool_calls:
        if c["name"] not in NOTE_TOOLS:
            continue
        blob = json.dumps(c["input"] or {})
        note_chars += len(blob)
        note_calls += 1

    # Per-step shape: the wasted-step numbers everyone quotes come from here.
    per_step: dict[int, list[str]] = defaultdict(list)
    for c in tool_calls:
        per_step[c.get("step", 0)].append(c["name"])
    lifecycle_only_steps = sorted(
        i for i, names in per_step.items()
        if names and all(n in LIFECYCLE_TOOLS for n in names)
    )
    single_call_steps = sum(1 for names in per_step.values() if len(names) == 1)
    steps_with_calls = len(per_step)

    by_tool = Counter(c["name"] for c in tool_calls)
    rsf_paths = Counter(
        f"{(c['input'] or {}).get('skill_name', '?')}/{(c['input'] or {}).get('path', '?')}"
        for c in tool_calls if c["name"] == "read_skill_file"
    )

    return {
        "file": path.name,
        "llm_steps": llm_steps,
        "tool_calls": len(tool_calls),
        "wall_s": (last_ts - first_ts) if (first_ts and last_ts) else None,
        "measured_step_s": sum(step_durs) if step_durs else None,
        "tool_exec_s": sum(tool_durs) if tool_durs else None,
        "output_tokens": sum(out_tokens) if out_tokens else 0,
        "max_output_tokens_step": max(out_tokens) if out_tokens else 0,
        "modelled_generation_s": modelled_gen,
        "token_typing_s": token_only,
        "fixed_overhead_s": FIXED_CALL_OVERHEAD_S * len(out_tokens),
        "final_stop_reason": steps[-1]["stop_reason"] if steps else None,
        "stop_reasons": Counter(s["stop_reason"] for s in steps if s["stop_reason"]),
        "max_context_percent": max((s["context_percent"] for s in steps
                                    if isinstance(s["context_percent"], (int, float))), default=None),
        "cache_creation_total": sum(s["cache_creation"] for s in steps
                                    if isinstance(s["cache_creation"], int)),
        "by_tool": by_tool,
        "bash_buckets": bash_buckets,
        "bash_examples": {k: v for k, v in bash_examples.items()},
        "hot_files": hot_files,
        "repeated_bodies": {k: v for k, v in big_bodies.items() if v["count"] > 1},
        "note_calls": note_calls,
        "note_chars": note_chars,
        "read_skill_file_dupes": {k: v for k, v in rsf_paths.items() if v > 1},
        "read_skill_file_calls": sum(rsf_paths.values()),
        "lifecycle_calls": sum(v for k, v in by_tool.items() if k in LIFECYCLE_TOOLS),
        "steps_with_calls": steps_with_calls,
        "single_call_steps": single_call_steps,
        "lifecycle_only_steps": len(lifecycle_only_steps),
        "lifecycle_only_step_ids": lifecycle_only_steps[:20],
    }


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def verdicts(turns: list[dict], min_repeat_tokens: int) -> list[str]:
    V: list[str] = []
    total_steps = sum(t["llm_steps"] for t in turns)
    total_calls = sum(t["tool_calls"] for t in turns)
    total_out = sum(t["output_tokens"] for t in turns)
    typing = sum(t["token_typing_s"] for t in turns)
    fixed = sum(t["fixed_overhead_s"] for t in turns)
    wall = sum(t["wall_s"] or 0 for t in turns)
    cpstep = round(total_calls / total_steps, 2) if total_steps else None

    capped = [t for t in turns if t["final_stop_reason"] == "max_tool_iterations"
              or t["llm_steps"] >= STEP_CEILING]
    if capped:
        V.append(
            f"CEILING: {len(capped)} turn(s) hit the {STEP_CEILING}-step cap "
            f"({', '.join(t['file'] for t in capped)}). Nothing resumes a capped turn -- a human "
            "must send another message, and the continuation inherits a compaction summary rather "
            "than the skill's contracts, so it reopens in discovery."
        )

    trunc = sum(t["stop_reasons"].get("max_tokens", 0) for t in turns)
    if trunc:
        V.append(
            f"TRUNCATED CALLS: {trunc} step(s) ended on max_tokens. The harness does NOT dispatch a "
            "half-formed call -- it runs a continuation step instead, so that step was spent and the "
            "call wrote nothing. Bound the per-call payload width, not just the step count."
        )

    if cpstep is not None and cpstep < 1.3 and total_steps >= 20:
        V.append(
            f"NOT BATCHING: {cpstep} tool calls per step across {total_steps} steps. A step costs the "
            "same whether it carries one call or thirty, and work-tool calls in one response fan out "
            "in parallel. Near 1.0 means almost everything ran one at a time."
        )

    if wall and typing + fixed:
        share = (typing + fixed) / wall
        if share > 0.4:
            V.append(
                f"TOKEN-BOUND, NOT STEP-BOUND: ~{int(typing + fixed)}s of ~{int(wall)}s "
                f"({share:.0%}) is model generation -- {total_out:,} output tokens plus "
                f"{int(fixed)}s of fixed per-call overhead. Cutting steps alone will NOT fix this. "
                "Look at what the model is TYPING: code the skill should ship as a script, payloads "
                "that should travel as files, and progress notes restating figures already on disk."
            )

    reps = [(k, v) for t in turns for k, v in t["repeated_bodies"].items()
            if est_tokens("x" * v["chars"]) >= min_repeat_tokens]
    if reps:
        worst = max(reps, key=lambda kv: kv[1]["chars"] * kv[1]["count"])
        wasted = sum(v["chars"] * (v["count"] - 1) for _, v in reps) // 4
        V.append(
            f"RETYPED CODE: {len(reps)} large command body(ies) emitted more than once "
            f"(worst: {worst[1]['count']}x at ~{est_tokens('x' * worst[1]['chars']):,} tokens each) "
            f"-- roughly {wasted:,} output tokens spent retyping work already done. Anything the "
            "agent authors should be persisted (save_output / ws_add) and re-used, and anything it "
            "authors TWICE should ship as a script in the skill."
        )

    notes = sum(t["note_chars"] for t in turns)
    ncalls = sum(t["note_calls"] for t in turns)
    if ncalls and notes // 4 > 4000:
        V.append(
            f"VERBOSE NOTES: {ncalls} progress/memory call(s) carrying ~{notes // 4:,} output tokens. "
            "A note should say what is done, which file holds the state, and the next command -- and "
            "name the file that has the figures rather than restating them."
        )

    hot = Counter()
    for t in turns:
        hot.update(t["hot_files"])
    if hot and hot.most_common(1)[0][1] >= 5:
        f, n = hot.most_common(1)[0]
        V.append(
            f"DISCOVERY ON ONE FILE: `{f}` was read/grepped {n} times. The agent cannot see script "
            "source in its context, so an undocumented contract must be rediscovered every run. "
            "Document what that script reads, writes, prints and exits with -- and make it REFUSE "
            "with a named error instead of failing two scripts downstream."
        )

    bb = Counter()
    for t in turns:
        bb.update(t["bash_buckets"])
    bash_total = sum(bb.values())
    disc = bb["read_script"] + bb["inline_code"]
    if bash_total and disc / bash_total > 0.4:
        V.append(
            f"DISCOVERY BURN: {disc} of {bash_total} bash calls read the skill's own scripts or "
            "reimplemented them inline rather than running them. Reimplementing a script also "
            "bypasses the validation that stops bad output being shipped."
        )

    dupes = {}
    for t in turns:
        dupes.update(t["read_skill_file_dupes"])
    if dupes:
        V.append(
            f"RE-STAGING: {len(dupes)} file(s) staged more than once "
            f"({sum(dupes.values())} calls). Staging is idempotent and already-present files are "
            "skipped, so a repeat is a wasted step."
        )

    fetches = sum(t["by_tool"].get("fetch_skill", 0) for t in turns)
    if fetches > len(turns):
        V.append(
            f"RE-FETCH: fetch_skill called {fetches} times across {len(turns)} turn(s). Each costs a "
            "step and re-injects the whole SKILL.md, accelerating the context growth that triggers "
            f"clearing at {CLEAR_TRIGGER_PCT}% and compaction at {COMPACT_TRIGGER_PCT}%."
        )

    wasted = sum(t["lifecycle_only_steps"] for t in turns)
    with_calls = sum(t["steps_with_calls"] for t in turns)
    if wasted:
        V.append(
            f"WASTED STEPS: {wasted} of {with_calls} steps carried NOTHING but narrative/process "
            "bookkeeping. start_chip and reflect are free riding along with real work and cost a "
            "full step alone. The platform now forbids this in the tool descriptions themselves, so "
            "a skill needs no wording for it -- but a run still doing it is losing "
            f"{wasted / with_calls:.0%} of its budget to nothing."
        )

    singles = sum(t["single_call_steps"] for t in turns)
    if with_calls and singles / with_calls > 0.7 and with_calls >= 20:
        V.append(
            f"ONE CALL PER STEP: {singles} of {with_calls} steps carried exactly one tool call. "
            "Independent work in one response fans out in parallel for free; this run took the "
            "sequential path almost everywhere."
        )

    maxctx = [t["max_context_percent"] for t in turns if t["max_context_percent"] is not None]
    if maxctx and max(maxctx) >= COMPACT_TRIGGER_PCT:
        cc = sum(t["cache_creation_total"] for t in turns)
        V.append(
            f"COMPACTION: context reached {max(maxctx):.0f}% of the window (compaction fires at "
            f"{COMPACT_TRIGGER_PCT}%, clearing at {CLEAR_TRIGGER_PCT}%). "
            f"{cc:,} cache-creation tokens is the recompaction bill. A continuation after this "
            "inherits a summary, not the contracts -- hand state forward explicitly."
        )

    if not V:
        V.append("No budget or cost red flags in these turns.")
    return V


def render_text(report: dict, top: int) -> str:
    s = report["summary"]
    L = ["Harness run analysis", ""]
    L.append(f"  turns              {s['turns']}")
    L.append(f"  LLM steps          {s['llm_steps']}"
             + (f"  (cap {STEP_CEILING}/turn)" if s["llm_steps"] else ""))
    L.append(f"  tool calls         {s['tool_calls']}  ({s['calls_per_step']} per step)")
    if s["wall_s"]:
        L.append(f"  wall clock         {s['wall_s'] / 60:.1f} min")
    L.append(f"  output tokens      {s['output_tokens']:,}")
    if s["wall_s"]:
        L.append(f"  generation share   {s['generation_share']}  "
                 f"(~{int(s['token_typing_s'])}s typing + "
                 f"~{int(s['fixed_overhead_s'])}s fixed per-call overhead)")
    if s["tool_exec_s"]:
        L.append(f"  tool execution     {int(s['tool_exec_s'])}s")
    L += ["", "Verdicts"]
    L += [f"  - {v}" for v in report["verdicts"]]

    L += ["", "Per turn"]
    L.append(f"  {'file':34} {'steps':>6} {'calls':>6} {'out tok':>9} {'wall':>7}  stop")
    for t in report["turns"]:
        wall = f"{t['wall_s'] / 60:.1f}m" if t["wall_s"] else "-"
        L.append(f"  {t['file'][:34]:34} {t['llm_steps']:>6} {t['tool_calls']:>6} "
                 f"{t['output_tokens']:>9,} {wall:>7}  {t['final_stop_reason'] or '-'}")

    bb = Counter()
    for t in report["turns"]:
        bb.update(t["bash_buckets"])
    if bb:
        L += ["", "bash buckets"]
        for k, v in bb.most_common():
            L.append(f"  {k:14} {v}")

    hot = Counter()
    for t in report["turns"]:
        hot.update(t["hot_files"])
    if hot:
        L += ["", "files read/grepped (discovery)"]
        for k, v in hot.most_common(top):
            L.append(f"  {v:4}x  {k}")

    reps = [(k, v) for t in report["turns"] for k, v in t["repeated_bodies"].items()]
    if reps:
        L += ["", "large command bodies emitted more than once"]
        for _, v in sorted(reps, key=lambda kv: -kv[1]["chars"] * kv[1]["count"])[:top]:
            L.append(f"  {v['count']}x  ~{est_tokens('x' * v['chars']):,} tok  {v['sample'][:90]}")

    tools = Counter()
    for t in report["turns"]:
        tools.update(t["by_tool"])
    L += ["", f"tools (top {top})"]
    for k, v in tools.most_common(top):
        L.append(f"  {k:28} {v}")

    dupes = {}
    for t in report["turns"]:
        dupes.update(t["read_skill_file_dupes"])
    if dupes:
        L += ["", "re-staged files"]
        for k, v in sorted(dupes.items(), key=lambda kv: -kv[1]):
            L.append(f"  {v}x  {k}")

    for bucket in ("read_script", "inline_code"):
        ex = []
        for t in report["turns"]:
            ex += t["bash_examples"].get(bucket) or []
        if ex:
            L += ["", f"examples -- {bucket}"]
            L += [f"  {e[:150]}" for e in ex[:5]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("history", nargs="+", help="exported Temporal history JSON (one per turn)")
    ap.add_argument("--format", choices=("json", "text"), default="text")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-repeat-tokens", type=int, default=300,
                    help="ignore repeated command bodies smaller than this (default 300 tokens)")
    args = ap.parse_args()

    turns = []
    for raw in args.history:
        p = Path(raw).expanduser()
        try:
            turns.append(analyze_turn(p))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"could not analyze {p}: {e}", file=sys.stderr)
            return 3
    if not turns:
        return 3

    total_steps = sum(t["llm_steps"] for t in turns)
    total_calls = sum(t["tool_calls"] for t in turns)
    wall = sum(t["wall_s"] or 0 for t in turns)
    typing = sum(t["token_typing_s"] for t in turns)
    fixed = sum(t["fixed_overhead_s"] for t in turns)

    report = {
        "summary": {
            "turns": len(turns),
            "llm_steps": total_steps,
            "tool_calls": total_calls,
            "calls_per_step": round(total_calls / total_steps, 2) if total_steps else None,
            "wall_s": wall or None,
            "output_tokens": sum(t["output_tokens"] for t in turns),
            "token_typing_s": typing,
            "fixed_overhead_s": fixed,
            "generation_share": f"{(typing + fixed) / wall:.0%}" if wall else None,
            "tool_exec_s": sum(t["tool_exec_s"] or 0 for t in turns) or None,
            "cost_model": f"step ~= {FIXED_CALL_OVERHEAD_S}s + {SECONDS_PER_OUTPUT_TOKEN * 1000}ms "
                          f"per output token",
        },
        "verdicts": verdicts(turns, args.min_repeat_tokens),
        "turns": turns,
    }

    if args.format == "json":
        print(json.dumps(report, indent=None, default=lambda o: dict(o)
                         if isinstance(o, Counter) else str(o)))
    else:
        print(render_text(report, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
