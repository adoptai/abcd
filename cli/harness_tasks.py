#!/usr/bin/env python3
"""
List, bulk-delete and restore agent "tasks" (admin only).

A task is what the agent UI counts as waiting on a person: an open escalation
(Tasks tab, "Needs review (N Tasks)" on the Agents list) or a conversation whose
last turn ended on a question card. Old test runs leave hundreds of them and
bury current work; this removes them by filter, no UI needed.

Delete is a soft delete: the tasks vanish from every task list and count, and
nothing is sent to a workflow (no run is resumed or notified). A new question
asked later in a conversation shows up again. `delete` always shows the preview
first and asks before removing; the ids it removed are saved so `restore` can
bring them back.

Filters (at least one is required; they combine with AND):
    --before / --after   ISO date or datetime, UTC (2026-11-23 = midnight UTC)
    --user               user id or email of whoever started the conversation
    --workstream-id, --process-id (the agent), --conversation-id
    --category           task category: its key or label (missing_statements, "Tax inputs",
                         release_decision, ...); the Task type filter of the Tasks tab.
                         Selects escalations only: waiting questions have no category
    --ids                explicit task ids (a question wait is q:<conversation_id>)
    --status             open (default) | resolved | skipped | dismissed | any

Usage:
    python cli/harness_tasks.py list [filters] [--page N] [--page-size N] [--env ENV] [--json]
    python cli/harness_tasks.py delete [filters] [--dry-run] [--yes] [--env ENV] [--json]
    python cli/harness_tasks.py restore (--ids ID [ID ...] | --ids-file PATH) [--env ENV] [--json]

Examples:
    # Everything created before 2026-11-23
    python cli/harness_tasks.py delete --before 2026-11-23
    # Everything of one person (by email or user id)
    python cli/harness_tasks.py delete --user jane@client.com
    # Everything in one workstream
    python cli/harness_tasks.py delete --workstream-id <workstream_id>
    # One category, before a date (waiting questions are never part of it)
    python cli/harness_tasks.py delete --category missing_statements --before 2026-11-01
    # See what a category holds first
    python cli/harness_tasks.py list --category "Tax inputs"
    # One agent, before a date
    python cli/harness_tasks.py delete --process-id <agent_id> --before 2026-11-23
    # Look first, change nothing
    python cli/harness_tasks.py delete --user jane@client.com --dry-run
    # Undo a delete
    python cli/harness_tasks.py restore --ids-file workspaces/<env>/harness/task_cleanup/removed-<time>.json
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env
from cli.harness_common.workspace import active_env_name, task_cleanup_dir


def parse_when(value: str) -> str:
    """An ISO date or datetime as an ISO datetime with offset; naive means UTC."""
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date or datetime: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def build_filters(args: argparse.Namespace) -> dict[str, Any]:
    filters = {
        "created_before": args.before,
        "created_after": args.after,
        "user": args.user,
        "workstream_id": args.workstream_id,
        "process_id": args.process_id,
        "conversation_id": args.conversation_id,
        "task_category": args.category,
        "ids": args.ids or None,
    }
    filters = {k: v for k, v in filters.items() if v}
    filters["status"] = args.status
    if not {k for k in filters if k != "status"}:
        raise ValueError(
            "Give at least one filter: --before, --after, --user, --workstream-id, "
            "--process-id, --conversation-id, --category or --ids."
        )
    return filters


def _clip(text: Any, width: int) -> str:
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def print_preview(preview: dict[str, Any]) -> None:
    items = preview.get("items", [])
    by_kind = preview.get("by_kind", {})
    print(
        f"📋 {preview.get('total', 0)} task(s): {by_kind.get('escalation', 0)} escalation(s), "
        f"{by_kind.get('question_wait', 0)} waiting question(s)"
    )
    for note in preview.get("notes") or []:
        print(f"   Note: {note}")
    if not items:
        return
    print(f"   {'created':<17} {'kind':<14} {'category':<22} {'agent':<24} {'workstream':<24} {'started by':<24} title")
    for t in items:
        created = (t.get("created_at") or "?")[:16].replace("T", " ")
        print(
            f"   {created:<17} {t.get('kind', '?'):<14} {_clip((t.get('category') or {}).get('label') or '-', 22):<22} {_clip(t.get('process_name') or t.get('process_id'), 24):<24} "
            f"{_clip(t.get('workstream_name') or t.get('workstream_id'), 24):<24} "
            f"{_clip(t.get('created_by') or t.get('created_by_user_id'), 24):<24} {_clip(t.get('title'), 60)}"
        )
    shown = len(items)
    if shown < preview.get("total", 0):
        print(f"   ... showing {shown} of {preview['total']} (page {preview.get('page', 1)})")


def _client(env: str | None):
    try:
        return get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return None


def list_tasks(args: argparse.Namespace) -> int:
    try:
        filters = build_filters(args)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    client = _client(args.env)
    if client is None:
        return 1
    try:
        preview = client.preview_tasks(filters, page=args.page, per_page=args.page_size)
    except HarnessAPIError as e:
        print(f"❌ Failed to list tasks: {e}")
        return 1
    if args.json:
        print(json.dumps(preview, indent=2, default=str))
    else:
        print_preview(preview)
    return 0


def delete_tasks(args: argparse.Namespace) -> int:
    try:
        filters = build_filters(args)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    client = _client(args.env)
    if client is None:
        return 1
    try:
        preview = client.preview_tasks(filters, page=1, per_page=args.show)
    except HarnessAPIError as e:
        print(f"❌ Failed to preview tasks: {e}")
        return 1
    total = int(preview.get("total", 0))
    if not args.json:
        print_preview(preview)
    if total == 0:
        if args.json:
            print(json.dumps({"preview": preview, "removed": 0}, indent=2, default=str))
        else:
            print("Nothing matches; nothing to delete.")
        return 0
    if args.dry_run:
        if args.json:
            print(json.dumps({"preview": preview, "removed": 0, "dry_run": True}, indent=2, default=str))
        else:
            print("Dry run: nothing was changed.")
        return 0

    if not args.yes:
        print(f"\n⚠️  This removes {total} task(s) from every task list and count (no workflow is resumed or notified).")
        try:
            answer = input(f"Type {total} to confirm, anything else cancels: ").strip()
        except EOFError:
            answer = ""
        if answer != str(total):
            print("Cancelled; nothing was changed.")
            return 1

    try:
        result = client.delete_tasks(filters, expected_count=total)
    except HarnessAPIError as e:
        if e.status_code == 409:
            print(f"❌ The set changed since the preview. Nothing was changed. Run the command again.\n   {e.body}")
        else:
            print(f"❌ Failed to delete tasks: {e}")
        return 1

    ids = result.get("ids", [])
    saved = None
    if ids:
        saved = task_cleanup_dir(args.env) / f"removed-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        saved.write_text(json.dumps({"env": active_env_name(args.env), "ids": ids}, indent=2))
    if args.json:
        print(json.dumps({**result, "saved_to": str(saved) if saved else None}, indent=2, default=str))
    else:
        print(f"✅ Removed {result.get('removed', 0)} task(s).")
        if saved:
            print(f"   Ids saved to {saved}")
            print(f"   Undo: python cli/harness_tasks.py restore --ids-file {saved}")
    return 0


def restore_tasks(args: argparse.Namespace) -> int:
    ids = list(args.ids or [])
    if args.ids_file:
        try:
            data = json.loads(Path(args.ids_file).read_text())
        except (OSError, ValueError) as e:
            print(f"❌ Cannot read {args.ids_file}: {e}")
            return 1
        ids += data["ids"] if isinstance(data, dict) else data
    if not ids:
        print("❌ Give --ids or --ids-file.")
        return 1
    client = _client(args.env)
    if client is None:
        return 1
    try:
        result = client.restore_tasks(ids)
    except HarnessAPIError as e:
        print(f"❌ Failed to restore tasks: {e}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"✅ Restored {result.get('restored', 0)} of {len(ids)} task(s).")
        if result.get("restored", 0) < len(ids):
            print("   The rest were not removed by this cleanup, or are already back.")
    return 0


def add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--before", type=parse_when, help="Created before this ISO date/datetime (UTC)")
    p.add_argument("--after", type=parse_when, help="Created on or after this ISO date/datetime (UTC)")
    p.add_argument("--user", help="User id or email of whoever started the conversation")
    p.add_argument("--workstream-id")
    p.add_argument("--process-id", help="Agent (process) id")
    p.add_argument("--conversation-id")
    p.add_argument(
        "--category",
        help='Task category, key or label (e.g. missing_statements or "Missing statements"). '
        "Selects escalations only; waiting questions have no category",
    )
    p.add_argument("--ids", nargs="+", help="Explicit task ids (a question wait is q:<conversation_id>)")
    p.add_argument("--status", default="open", choices=["open", "resolved", "skipped", "dismissed", "any"])
    p.add_argument("--env", help="Environment to use (defaults to active env)")
    p.add_argument("--json", action="store_true", help="Print raw JSON instead of a summary")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="Show the tasks a filter matches (changes nothing)")
    add_filter_args(list_p)
    list_p.add_argument("--page", type=int, default=1)
    list_p.add_argument("--page-size", type=int, default=50)

    del_p = sub.add_parser("delete", help="Preview, confirm, then remove the tasks a filter matches")
    add_filter_args(del_p)
    del_p.add_argument("--dry-run", action="store_true", help="Only preview")
    del_p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    del_p.add_argument("--show", type=int, default=20, help="Rows to print in the preview (default 20)")

    res_p = sub.add_parser("restore", help="Bring back tasks a delete removed")
    res_p.add_argument("--ids", nargs="+", help="Ids a delete returned")
    res_p.add_argument("--ids-file", help="The file a delete saved its ids to")
    res_p.add_argument("--env")
    res_p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    handlers = {"list": list_tasks, "delete": delete_tasks, "restore": restore_tasks}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
