#!/usr/bin/env python3
"""
List/inspect Studio "Runs" (org-wide) -- pipeline runs AND agent conversations,
unlike `abcd conversations` which only ever sees the PAT's own user.

/v1/runs is org-scoped via the JWT's org_id, with no created_by_user_id
filter (backend/app/routes/runs.py, dependencies/harness_caller.py) -- an
admin PAT can pull any run in the org here, including conversations another
human started. Use this, not `abcd conversations`, when you need to see
someone else's chat/run.

Usage:
    abcd runs list [--range 24h|7d|30d|all] [--status ...]
        [--source pipeline|conversation] [--workstream-id ID] [--agent NAME]
        [--search TEXT] [--page N] [--page-size N] [--env ENV] [--json]
    abcd runs detail --run-id cv:<id>|pr:<id> [--env ENV] [--json]
    abcd runs trace --conversation-id ID [--env ENV] [--json]
    (the old `python cli/harness_runs.py <command> ...` form still works)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.core.registry import Registry, legacy_main
from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

if TYPE_CHECKING:
    from cli.core.context import Context


def _agent_display_name(client: object, ref: str) -> str:
    """``--agent`` filters on the agent's *display name*; accept its name or id too."""
    try:
        rows = client.list_processes().get("processes", [])  # type: ignore[attr-defined]
    except (HarnessAPIError, AttributeError):
        return ref
    for row in rows:
        if isinstance(row, dict) and ref in (row.get("id"), row.get("name")):
            return str(row.get("display_name") or ref)
    return ref


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

    from cli.builder_session import resolve_workstream

    if workstream_id:
        workstream_id = resolve_workstream(client, workstream_id, env)
    if agent:
        agent = _agent_display_name(client, agent)
    try:
        response = client.list_runs(
            status=status,
            source=source,
            workstream_id=workstream_id,
            agent=agent,
            search=search,
            time_range=time_range,
            page=page,
            page_size=page_size,
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
    if re.fullmatch(r"[0-9a-f]{32}", run_id):
        run_id = f"cv:{run_id}"  # a bare conversation id (what `session start` prints)
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
    print(
        f"   initiated_by={detail.get('initiated_by')}  workstream={detail.get('workstream_name')}"
    )
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


def _cmd_list(ctx: Context) -> int:
    a = ctx.args
    return list_runs(
        a.time_range,
        a.status,
        a.source,
        a.workstream_id,
        a.agent,
        a.search,
        a.page,
        a.page_size,
        a.env,
        a.json,
    )


def _cmd_detail(ctx: Context) -> int:
    return run_detail(ctx.args.run_id, ctx.env_arg, ctx.as_json)


def _cmd_trace(ctx: Context) -> int:
    return conversation_trace(ctx.args.conversation_id, ctx.env_arg, ctx.as_json)


def register(registry: Registry) -> None:
    """Register ``abcd runs list|detail|trace`` (and the flat ``harness_runs.py`` CLI)."""
    group = registry.group("runs", "Org-wide Studio runs (pipelines and conversations)")
    p = group.command("list", _cmd_list, help="List runs across the whole org", legacy_name="list")
    p.add_argument("--range", dest="time_range", help="24h | 7d | 30d | all")
    p.add_argument("--status", help="succeeded | active | waiting_on_hitl | failed | cancelled")
    p.add_argument("--source", help="pipeline | conversation")
    p.add_argument("--workstream-id", help="Workstream id or name")
    p.add_argument("--agent", help="Agent display name (its name or id is also accepted)")
    p.add_argument("--search")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=25)

    p = group.command(
        "detail",
        _cmd_detail,
        help="Get one run's detail, by source-prefixed run id",
        legacy_name="detail",
    )
    p.add_argument(
        "--run-id",
        required=True,
        help="cv:<conversation_id> or pr:<pipeline_run_id> (a bare conversation id works too)",
    )

    p = group.command(
        "trace",
        _cmd_trace,
        help="Get every cached turn trace for a conversation",
        legacy_name="trace",
    )
    p.add_argument("--conversation-id", required=True, help="Bare conversation UUID, no cv: prefix")


def main(argv: list[str] | None = None) -> int:
    return legacy_main("harness_runs.py", register, __doc__, argv)


if __name__ == "__main__":
    sys.exit(main())
