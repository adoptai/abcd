#!/usr/bin/env python3
"""
Fetch a turn's status/trace/temporal-history on demand, or run the §2.5
performance-checklist review over an already-persisted run.

Usage:
    python cli/harness_trace.py fetch TURN_ID [--env ENV] [--save DIR]
    python cli/harness_trace.py review RUN_ID [--env ENV]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import (
    UNSCOPED_WORKSTREAM_ID,
    HarnessAPIError,
    get_harness_client_for_env,
)
from cli.harness_common.trace_review import print_review, review_run
from cli.harness_common.workspace import run_dir as harness_run_dir


def fetch(
    turn_id: str,
    conversation_id: str,
    workstream_id: str | None,
    env: str | None,
    save: Path | None,
) -> int:
    """
    Fetch a turn_id's status/trace/temporal-history on demand -- for a turn
    you already have (e.g. from an FDE bug report), not necessarily one
    `harness_run.py` started (those are already persisted under
    workspaces/{env}/harness/traces/<run_id>/ automatically).

    conversation_id is required by every one of these endpoints (the webui
    binds every turn read to a conversation the caller owns); pass the same
    workstream_id the turn was started with, or omit it for an unscoped turn.
    """
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    effective_workstream_id = workstream_id or UNSCOPED_WORKSTREAM_ID

    results: dict[str, object] = {}
    fns = {
        "status": lambda: client.turn_status(turn_id, conversation_id),
        "trace": lambda: client.turn_trace(turn_id, conversation_id, effective_workstream_id),
        "temporal_history": lambda: client.turn_temporal_history(turn_id, conversation_id),
    }
    for label, fn in fns.items():
        try:
            results[label] = fn()
        except HarnessAPIError as e:
            print(f"⚠️  {label} fetch failed: {e}")
            results[label] = None

    print(json.dumps(results, indent=2))

    if save:
        save.mkdir(parents=True, exist_ok=True)
        for label, data in results.items():
            if data is not None:
                (save / f"{label}.json").write_text(json.dumps(data, indent=2))
        print(f"\n💾 Saved to {save}")

    return 0


def review(run_id: str, env: str | None) -> int:
    """
    Run the AUTHORING_SKILLS_AND_PLUGINS.md section 2.5 performance
    checklist over a run's persisted turn-*.ndjson files.
    """
    directory = harness_run_dir(run_id, env)
    ndjson_files = sorted(directory.glob("turn-*.ndjson"))
    if not ndjson_files:
        print(f"❌ No turn-*.ndjson files found in {directory}")
        return 1

    print(f"🔍 Reviewing {len(ndjson_files)} turn(s) in {directory}")
    print_review(review_run(directory))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="Fetch a turn's status/trace/temporal-history")
    fetch_p.add_argument("turn_id")
    fetch_p.add_argument(
        "--conversation-id",
        required=True,
        help="Conversation the turn belongs to (required by the API)",
    )
    fetch_p.add_argument(
        "--workstream-id", help="Workstream the turn ran in (omit for an unscoped/general turn)"
    )
    fetch_p.add_argument("--env", help="Environment to use (defaults to active env)")
    fetch_p.add_argument(
        "--save",
        type=Path,
        help="Directory to write status.json/trace.json/temporal_history.json into",
    )

    review_p = sub.add_parser("review", help="Run the §2.5 performance checklist over a run")
    review_p.add_argument("run_id")
    review_p.add_argument("--env", help="Environment to use (defaults to active env)")

    args = parser.parse_args()

    if args.command == "fetch":
        sys.exit(fetch(args.turn_id, args.conversation_id, args.workstream_id, args.env, args.save))
    elif args.command == "review":
        sys.exit(review(args.run_id, args.env))


if __name__ == "__main__":
    main()
