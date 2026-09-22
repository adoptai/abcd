#!/usr/bin/env python3
"""
Performance-review checklist for a harness run's persisted turn NDJSON files.

Applies the concrete, validated rubric from adoptai-workflows/docs/agent-harness/
AUTHORING_SKILLS_AND_PLUGINS.md section 2.5 ("Moving data without burning
tokens") -- the same patterns that took one real skill from 720s/38k output
tokens (truncated, inline data) down to 119s/2.2k tokens:

  1. `db_query` without `save_to_file: true`, whose tool_result came back
     large -- data that should have routed through a file, not the model.
  2. `render_ui` calls with a large inline payload instead of `file_ref`.
  3. The same tool name called several times in a row -- a possible missed
     batching opportunity (independent calls should be issued in the same
     assistant message/step, not serially).
  4. Iteration count against the harness's `MAX_TOOL_ITERATIONS=100` cap.
  5. Context-percent growth across the turn (`harness_context_usage` events).

This is a heuristic pass over the raw event stream, not a model -- every
finding cites the tool_use/tool_result it's based on so it reads as evidence,
not a guess. It only ever under-reports (missing/unexpected field names just
mean a check doesn't fire) -- it never fabricates a finding.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LARGE_RESULT_CHARS = 2000
_ITERATION_WARN_THRESHOLD = 60
_MAX_TOOL_ITERATIONS = 100
_CONTEXT_PCT_WARN = 80


@dataclass
class Finding:
    category: str
    summary: str
    evidence: str = ""


@dataclass
class TurnReview:
    ndjson_file: str
    tool_use_count: int = 0
    findings: list[Finding] = field(default_factory=list)


def _load_envelopes(ndjson_path: Path) -> list[dict[str, Any]]:
    envelopes = []
    for line in ndjson_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            envelopes.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return envelopes


def review_turn(ndjson_path: Path) -> TurnReview:
    envelopes = _load_envelopes(ndjson_path)
    review = TurnReview(ndjson_file=ndjson_path.name)

    tool_uses: list[dict[str, Any]] = []
    tool_results_by_id: dict[str, dict[str, Any]] = {}
    context_pcts: list[int] = []

    # Wire shape (see stream.py's module docstring for the full rationale):
    # dispatch on event["event_name"], NOT event["type"] (always "response").
    # Content blocks (tool_use/tool_result/...) nest under
    # event["data"]["block"] on "block_delta" events.
    for envelope in envelopes:
        event = envelope.get("event") or {}
        name = event.get("event_name")
        data = event.get("data") or {}

        if name == "block_delta":
            block = data.get("block") or {}
            btype = block.get("type")
            if btype == "tool_use":
                tool_uses.append(block)
            elif btype == "tool_result":
                tool_id = block.get("tool_use_id") or block.get("id")
                if tool_id:
                    tool_results_by_id[tool_id] = block
        elif name == "harness_context_usage":
            pct = data.get("context_percent")
            if isinstance(pct, int | float):
                context_pcts.append(int(pct))

    review.tool_use_count = len(tool_uses)
    _check_inline_data(review, tool_uses, tool_results_by_id)
    _check_batching(review, tool_uses)
    _check_iteration_count(review)
    _check_context_growth(review, context_pcts)

    return review


def _check_inline_data(
    review: TurnReview,
    tool_uses: list[dict[str, Any]],
    tool_results_by_id: dict[str, dict[str, Any]],
) -> None:
    for tu in tool_uses:
        name = tu.get("name")
        tool_input = tu.get("input") or {}
        tool_id = tu.get("id") or tu.get("tool_use_id")
        result = tool_results_by_id.get(tool_id) if tool_id else None
        result_len = len(json.dumps(result.get("content"))) if result else 0

        if (
            name == "db_query"
            and not tool_input.get("save_to_file")
            and result_len > _LARGE_RESULT_CHARS
        ):
            review.findings.append(
                Finding(
                    category="inline-data",
                    summary=(
                        f"db_query result ({result_len} chars) returned inline without "
                        "save_to_file -- the model had to read/re-type this instead of a "
                        "script consuming the CSV path directly"
                    ),
                    evidence=f"tool_use input={json.dumps(tool_input)[:200]}",
                )
            )

        input_len = len(json.dumps(tool_input))
        if name == "render_ui" and "file_ref" not in tool_input and input_len > _LARGE_RESULT_CHARS:
            review.findings.append(
                Finding(
                    category="inline-data",
                    summary=(
                        f"render_ui called with a large inline payload ({input_len} chars) "
                        "instead of file_ref -- have the script write the payload file and "
                        "pass file_ref"
                    ),
                    evidence=f"tool_use input keys={list(tool_input.keys())}",
                )
            )


def _check_batching(review: TurnReview, tool_uses: list[dict[str, Any]]) -> None:
    prev_name = None
    repeat_run = 0

    def _flush() -> None:
        if repeat_run >= 2:
            review.findings.append(
                Finding(
                    category="batching",
                    summary=(
                        f"'{prev_name}' called {repeat_run + 1} times in a row -- if these "
                        "were independent, the SKILL.md should say to issue them in the same "
                        "response"
                    ),
                )
            )

    for tu in tool_uses:
        name = tu.get("name")
        if name == prev_name:
            repeat_run += 1
        else:
            _flush()
            repeat_run = 0
        prev_name = name
    _flush()


def _check_iteration_count(review: TurnReview) -> None:
    if review.tool_use_count >= _ITERATION_WARN_THRESHOLD:
        review.findings.append(
            Finding(
                category="iteration-count",
                summary=(
                    f"{review.tool_use_count} tool calls this turn -- approaching the "
                    f"harness's {_MAX_TOOL_ITERATIONS}-iteration cap"
                ),
            )
        )


def _check_context_growth(review: TurnReview, context_pcts: list[int]) -> None:
    if context_pcts and max(context_pcts) >= _CONTEXT_PCT_WARN:
        review.findings.append(
            Finding(
                category="context-growth",
                summary=f"context usage reached {max(context_pcts)}% during this turn",
                evidence=f"progression: {context_pcts}",
            )
        )


def review_run(run_dir: Path) -> list[TurnReview]:
    return [review_turn(p) for p in sorted(run_dir.glob("turn-*.ndjson"))]


def print_review(reviews: Iterable[TurnReview]) -> None:
    any_findings = False
    for review in reviews:
        print(f"\n{review.ndjson_file}: {review.tool_use_count} tool call(s)")
        if not review.findings:
            print("  ✅ no rubric hits")
            continue
        any_findings = True
        for f in review.findings:
            print(f"  ⚠️  [{f.category}] {f.summary}")
            if f.evidence:
                print(f"       {f.evidence}")
    if not any_findings:
        print("\n✅ No performance anti-patterns found against the §2.5 checklist.")
