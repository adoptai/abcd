#!/usr/bin/env python3
"""
List/inspect Studio "Runs" (org-wide) -- pipeline runs AND agent conversations,
unlike cli/harness_conversations.py which only ever sees the PAT's own user.

/v1/runs is org-scoped via the JWT's org_id, with no created_by_user_id
filter (backend/app/routes/runs.py, dependencies/harness_caller.py) -- an
admin PAT can pull any run in the org here, including conversations another
human started. Use this, not harness_conversations.py, when you need to see
someone else's chat/run.

Usage:
    python cli/harness_runs.py list [--range 24h|7d|30d|all] [--status ...]
        [--source pipeline|conversation] [--workstream-id ID] [--agent NAME]
        [--search TEXT] [--page N] [--page-size N] [--env ENV] [--json]
    python cli/harness_runs.py detail --run-id cv:<id>|pr:<id> [--env ENV] [--json]
    python cli/harness_runs.py trace --conversation-id ID [--env ENV] [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env


def list_runs(
    time_range: str | None,
    status: str | None,
    source: str | None,
    workstream_id: str | None,
    agent: str | None,
    search: str | None,
    page: int,
    page_size: int,
    env: str | None,
    as_json: bool,
) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        response = client.list_runs(
            status=status, source=source, workstream_id=workstream_id, agent=agent,
            search=search, time_range=time_range, page=page, page_size=page_size,
        )
    except HarnessAPIError as e:
        print(f"❌ Failed to list runs: {e}")
        return 1

    runs = response.get("runs", [])
    if as_json:
        print(json.dumps(response, indent=2, default=str))
        return 0

    total = response.get("total", len(runs))
    print(f"📋 {len(runs)}/{total} run(s) (org-wide, page {response.get('page', page)})")
    for run in runs:
        started = run.get("started_at") or "?"
        initiated_by = run.get("initiated_by") or "?"
        print(
            f"   {started}  {run.get('id', '?'):<24}  {run.get('status', '?'):<14}  "
            f"by {initiated_by:<20}  {run.get('agent_name', '')}"
        )
    return 0


def run_detail(run_id: str, env: str | None, as_json: bool) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        detail = client.get_run_detail(run_id)
    except HarnessAPIError as e:
        print(f"❌ Failed to fetch run detail for {run_id}: {e}")
        return 1

    if as_json:
        print(json.dumps(detail, indent=2, default=str))
        return 0

    print(f"🔎 {detail.get('id')}  [{detail.get('status')}]  {detail.get('agent_name')}")
    print(f"   initiated_by={detail.get('initiated_by')}  workstream={detail.get('workstream_name')}")
    if detail.get("error_message"):
        print(f"   error: {detail['error_message']}")
    for turn in detail.get("conversation_turns", []):
        print(f"   turn {turn.get('seq')}: {turn.get('turn_id')}  {turn.get('status')}")
    return 0


def conversation_trace(conversation_id: str, env: str | None, as_json: bool) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        trace = client.get_conversation_trace(conversation_id)
    except HarnessAPIError as e:
        print(f"❌ Failed to fetch conversation trace for {conversation_id}: {e}")
        return 1

    print(json.dumps(trace, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List runs across the whole org")
    list_p.add_argument("--range", dest="time_range", help="24h | 7d | 30d | all")
    list_p.add_argument("--status", help="succeeded | active | waiting_on_hitl | failed | cancelled")
    list_p.add_argument("--source", help="pipeline | conversation")
    list_p.add_argument("--workstream-id")
    list_p.add_argument("--agent")
    list_p.add_argument("--search")
    list_p.add_argument("--page", type=int, default=1)
    list_p.add_argument("--page-size", type=int, default=25)
    list_p.add_argument("--env", help="Environment to use (defaults to active env)")
    list_p.add_argument("--json", action="store_true", help="Print raw JSON instead of a summary")

    detail_p = sub.add_parser("detail", help="Get one run's detail, by source-prefixed run id")
    detail_p.add_argument("--run-id", required=True, help="e.g. cv:<conversation_id> or pr:<pipeline_run_id>")
    detail_p.add_argument("--env")
    detail_p.add_argument("--json", action="store_true")

    trace_p = sub.add_parser("trace", help="Get every cached turn trace for a conversation")
    trace_p.add_argument("--conversation-id", required=True, help="Bare conversation UUID, no cv: prefix")
    trace_p.add_argument("--env")
    trace_p.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "list":
        sys.exit(
            list_runs(
                args.time_range, args.status, args.source, args.workstream_id, args.agent,
                args.search, args.page, args.page_size, args.env, args.json,
            )
        )
    elif args.command == "detail":
        sys.exit(run_detail(args.run_id, args.env, args.json))
    elif args.command == "trace":
        sys.exit(conversation_trace(args.conversation_id, args.env, args.json))


if __name__ == "__main__":
    main()
