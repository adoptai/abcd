#!/usr/bin/env python3
"""
List Agents-tab conversations from the last N hours (default 24).

The list-conversations API has no date-range param and is strictly
owner-scoped by created_by_user_id (see cli/harness_common/api_client.py) --
this only ever returns conversations created by the same Frontegg user
whose PAT is configured for the active environment, not every conversation
on the platform. There is no admin bypass.

Usage:
    python cli/harness_conversations.py list [--hours 24] [--process-id ID]
        [--workstream-id ID] [--env ENV] [--json]
    python cli/harness_conversations.py list --hours 24 --transcripts --save DIR
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

# The API has returned timestamps under different keys across endpoints;
# try them in order rather than guessing one.
_TIMESTAMP_KEYS = ("created_at", "started_at", "updated_at")


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
) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        response = client.list_conversations(process_id=process_id, workstream_id=workstream_id)
    except HarnessAPIError as e:
        print(f"❌ Failed to list conversations: {e}")
        return 1

    conversations = response.get("conversations", response) if isinstance(response, dict) else response
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
        print(f"📋 {len(recent)} conversation(s) in the last {hours:g}h (owner-scoped to this PAT's user)")
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List conversations from the last N hours")
    list_p.add_argument("--hours", type=float, default=24, help="Look-back window in hours (default 24)")
    list_p.add_argument("--process-id", help="Filter to a single process")
    list_p.add_argument("--workstream-id", help="Filter to a single workstream")
    list_p.add_argument("--env", help="Environment to use (defaults to active env)")
    list_p.add_argument("--json", action="store_true", help="Print raw JSON instead of a summary")
    list_p.add_argument(
        "--transcripts", action="store_true", help="Also fetch each conversation's full transcript"
    )
    list_p.add_argument("--save", type=Path, help="Directory to write a timestamped JSON dump into")

    args = parser.parse_args()

    if args.command == "list":
        sys.exit(
            list_recent(
                args.hours,
                args.process_id,
                args.workstream_id,
                args.env,
                args.json,
                args.transcripts,
                args.save,
            )
        )


if __name__ == "__main__":
    main()
