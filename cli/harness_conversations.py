#!/usr/bin/env python3
"""
List Agents-tab conversations from the last N hours (default 24).

The list-conversations API has no date-range param and is strictly
owner-scoped by created_by_user_id (see cli/harness_common/api_client.py) --
this only ever returns conversations created by the same Frontegg user
whose PAT is configured for the active environment, not every conversation
on the platform. There is no admin bypass.

Usage:
    abcd conversations list [--hours 24] [--process-id ID]
        [--workstream-id ID] [--env ENV] [--json]
    abcd conversations list --hours 24 --transcripts --save DIR
    (the old `python cli/harness_conversations.py list ...` form still works)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.core.registry import Registry, legacy_main
from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

if TYPE_CHECKING:
    from cli.core.context import Context

# The API has returned timestamps under different keys across endpoints;
# try them in order rather than guessing one.
_TIMESTAMP_KEYS = ("last_message_at", "created_at", "started_at", "updated_at")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _conversation_timestamp(conversation: dict) -> datetime | None:
    for key in _TIMESTAMP_KEYS:
        value = conversation.get(key)
        if value:
            parsed = _parse_timestamp(value)
            if parsed:
                return parsed
    return None


def list_recent(
    hours: float,
    process_id: str | None,
    workstream_id: str | None,
    env: str | None,
    as_json: bool,
    transcripts: bool,
    save: Path | None,
    limit: int = 100,
) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        response = client.list_conversations(
            process_id=process_id, workstream_id=workstream_id, limit=limit
        )
    except HarnessAPIError as e:
        print(f"❌ Failed to list conversations: {e}")
        return 1

    # The real BFF answers {"items": [...], "developer_trace_default": bool}; older/legacy
    # shapes used "conversations" or a bare list.
    conversations: object = response
    if isinstance(response, dict):
        conversations = response.get("items", response.get("conversations"))
    if not isinstance(conversations, list):
        print(f"❌ Unexpected response shape: {response}")
        return 1

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    recent = []
    undated = 0
    for conversation in conversations:
        ts = _conversation_timestamp(conversation)
        if ts is None:
            undated += 1
            continue
        if ts >= cutoff:
            recent.append((ts, conversation))
    recent.sort(key=lambda pair: pair[0], reverse=True)

    if transcripts:
        for _ts, conversation in recent:
            conversation_id = conversation.get("id") or conversation.get("conversation_id")
            if not conversation_id:
                continue
            try:
                conversation["transcript"] = client.get_conversation_transcript(conversation_id)
            except HarnessAPIError as e:
                print(f"⚠️  transcript fetch failed for {conversation_id}: {e}")

    if as_json:
        print(json.dumps([c for _, c in recent], indent=2, default=str))
    else:
        print(
            f"📋 {len(recent)} conversation(s) in the last {hours:g}h (owner-scoped to this PAT's user)"
        )
        if undated:
            print(f"   ({undated} conversation(s) had no recognizable timestamp and were skipped)")
        for ts, conversation in recent:
            conversation_id = conversation.get("id") or conversation.get("conversation_id") or "?"
            title = conversation.get("title") or conversation.get("name") or ""
            print(f"   {ts.isoformat()}  {conversation_id}  {title}")

    if save:
        save.mkdir(parents=True, exist_ok=True)
        out_file = save / f"conversations_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        out_file.write_text(json.dumps([c for _, c in recent], indent=2, default=str))
        print(f"💾 Saved to {out_file}")

    return 0


def _cmd_list(ctx: Context) -> int:
    a = ctx.args
    return list_recent(
        a.hours, a.process_id, a.workstream_id, a.env, a.json, a.transcripts, a.save, a.limit
    )


def register(registry: Registry) -> None:
    """Register ``abcd conversations list`` (and the flat ``harness_conversations.py`` CLI)."""
    group = registry.group("conversations", "Agents-tab conversations")
    p = group.command(
        "list",
        _cmd_list,
        help="List conversations active in the last N hours",
        legacy_name="list",
    )
    p.add_argument("--hours", type=float, default=24, help="Look-back window in hours (default 24)")
    p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Most conversations to ask the BFF for (it allows 1..100, default 100)",
    )
    p.add_argument("--process-id", help="Filter to a single process")
    p.add_argument("--workstream-id", help="Filter to a single workstream")
    p.add_argument(
        "--transcripts", action="store_true", help="Also fetch each conversation's full transcript"
    )
    p.add_argument("--save", type=Path, help="Directory to write a timestamped JSON dump into")


def main(argv: list[str] | None = None) -> int:
    return legacy_main("harness_conversations.py", register, __doc__, argv)


if __name__ == "__main__":
    sys.exit(main())
