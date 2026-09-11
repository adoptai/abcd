#!/usr/bin/env python3
"""
Start or continue a real agent-harness turn, streamed live -- and always stop
and hand control back to the FDE after the turn completes.

This never auto-continues to a next turn. It prints what the turn actually
did (stop_reason, tool calls, errors) and exits. You decide what happens
next:
    - looks wrong  -> go fix the local SKILL.md, `harness_skill.py push
                       --replace`, before touching this conversation again
    - looks right  -> `harness_run.py continue <run_id> --message "..."` to
                       post the next turn on the same conversation

Every turn's raw NDJSON stream + trace + temporal-history is persisted under
workspaces/{env}/harness/traces/<run_id>/, so pausing to fix the skill never
loses history -- resuming later just posts the next turn, it doesn't replay
anything.

Usage:
    python cli/harness_run.py start --workstream NAME --message "..." \\
        [--model MODEL] [--process PROCESS_ID] [--env ENV]
    python cli/harness_run.py continue RUN_ID --message "..." [--env ENV]
"""

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import (
    UNSCOPED_WORKSTREAM_ID,
    HarnessAPIError,
    get_harness_client_for_env,
)
from cli.harness_common.stream import stream_and_persist
from cli.harness_common.workspace import load_run, load_workstreams_cache, run_dir, save_run

_DEFAULT_MODEL = "claude-sonnet-4-6"


def _resolve_workstream_id(workstream: str, env: str | None) -> str:
    """Accept either a cached workstream *name* (from `harness_workstream.py
    ensure`) or a raw workstream_id -- whichever wasn't found in the cache is
    assumed to already be a real id."""
    cache = load_workstreams_cache(env)
    if workstream in cache:
        return cache[workstream]["workstream_id"]
    return workstream


def _post_turn(
    run_id: str,
    conversation_id: str,
    message: str,
    model: str,
    workstream_id: str | None,
    process_id: str | None,
    env: str | None,
) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    payload = {
        "user_message": message,
        "model": model,
        "conversation_id": conversation_id,
        "workstream_id": workstream_id,
        "process_id": process_id,
    }
    print(f"\n▶️  Starting turn (conversation={conversation_id})...")
    try:
        start_resp = client.start_turn(payload)
    except HarnessAPIError as e:
        print(f"❌ Failed to start turn: {e}")
        return 1

    turn_id = start_resp.get("turn_id")
    if not turn_id:
        print(f"❌ No turn_id in response: {start_resp}")
        return 1
    print(f"   turn_id={turn_id}")

    run = load_run(run_id, env) or {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "workstream_id": workstream_id,
        "process_id": process_id,
        "model": model,
        "created_at": datetime.now(UTC).isoformat(),
        "turns": [],
    }
    seq = len(run["turns"]) + 1
    ndjson_path = run_dir(run_id, env) / f"turn-{seq:03d}.ndjson"

    effective_workstream_id = workstream_id or UNSCOPED_WORKSTREAM_ID

    print("—" * 60)
    try:
        terminal = stream_and_persist(
            client.stream_turn(turn_id, conversation_id, workstream_id), ndjson_path
        )
    except HarnessAPIError as e:
        print(f"\n❌ Stream failed: {e}")
        terminal = {}
    print("—" * 60)

    stop_reason = terminal.get("stop_reason")

    trace_path = run_dir(run_id, env) / f"turn-{seq:03d}-trace.json"
    history_path = run_dir(run_id, env) / f"turn-{seq:03d}-temporal-history.json"
    try:
        trace = client.turn_trace(turn_id, conversation_id, effective_workstream_id)
        trace_path.write_text(json.dumps(trace, indent=2))
    except HarnessAPIError as e:
        print(f"⚠️  Could not fetch trace: {e}")
    try:
        history = client.turn_temporal_history(turn_id, conversation_id)
        history_path.write_text(json.dumps(history, indent=2))
    except HarnessAPIError as e:
        print(f"⚠️  Could not fetch temporal history: {e}")

    run["turns"].append(
        {
            "seq": seq,
            "turn_id": turn_id,
            "user_message": message,
            "stop_reason": stop_reason,
            "ndjson": ndjson_path.name,
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    save_run(run_id, run, env)

    print(f"\n🏁 Turn {seq} complete. stop_reason={stop_reason!r}")
    print(f"   Persisted: {run_dir(run_id, env)}")
    if stop_reason != "end_turn":
        print("   ⚠️  Non-clean stop_reason -- inspect the trace before continuing.")
    print("\n   Control is back with you. Next:")
    print(f'     - keep going:  python cli/harness_run.py continue {run_id} --message "..."')
    print("     - pause to fix the skill, then push --replace before continuing")

    return 0 if stop_reason == "end_turn" else 2


def start(
    workstream: str | None,
    message: str,
    model: str,
    process_id: str | None,
    env: str | None,
) -> int:
    run_id = uuid.uuid4().hex
    workstream_id = _resolve_workstream_id(workstream, env) if workstream else None
    return _post_turn(run_id, run_id, message, model, workstream_id, process_id, env)


def continue_run(run_id: str, message: str, env: str | None) -> int:
    run = load_run(run_id, env)
    if run is None:
        print(
            f"❌ No run found: {run_id} "
            f"(looked in workspaces/{{env}}/harness/traces/{run_id}/run.json)"
        )
        return 1
    return _post_turn(
        run_id,
        run["conversation_id"],
        message,
        run["model"],
        run.get("workstream_id"),
        run.get("process_id"),
        env,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="Start a new run (new conversation)")
    start_p.add_argument(
        "--workstream",
        help="Workstream name (from `harness_workstream.py ensure`) or a raw workstream_id",
    )
    start_p.add_argument("--message", required=True, help="User message for the first turn")
    start_p.add_argument("--model", default=_DEFAULT_MODEL)
    start_p.add_argument(
        "--process",
        dest="process_id",
        help="Bind the turn to an org process id, for deterministic skill binding",
    )
    start_p.add_argument("--env", help="Environment to use (defaults to active env)")

    continue_p = sub.add_parser("continue", help="Post the next turn on an existing run")
    continue_p.add_argument("run_id")
    continue_p.add_argument("--message", required=True)
    continue_p.add_argument("--env", help="Environment to use (defaults to active env)")

    args = parser.parse_args()

    if args.command == "start":
        sys.exit(start(args.workstream, args.message, args.model, args.process_id, args.env))
    elif args.command == "continue":
        sys.exit(continue_run(args.run_id, args.message, args.env))


if __name__ == "__main__":
    main()
