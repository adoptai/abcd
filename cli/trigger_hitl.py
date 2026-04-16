#!/usr/bin/env python3
"""
Trigger the UHY HITL Task Review pipeline for a specific preparation + task.

Supports two modes:
  - Default (form): Classic HITL with structured field inputs
  - Chat (--chat):  AI agent-assisted resolution via chat interface

Usage:
    poetry run python cli/trigger_hitl.py <preparation_id> <task_id> --client <name> --year <year>
    poetry run python cli/trigger_hitl.py <preparation_id> <task_id> --chat --agent-action-id <id>

Example:
    poetry run python cli/trigger_hitl.py "Complete Automation-2025" task-001 --client "Complete Automation" --year 2025
    poetry run python cli/trigger_hitl.py "Complete Automation-2025" task-001 --chat --client "Complete Automation" --year 2025
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client
from cli.auth import get_bearer_token


PIPELINE_FORM = "uhy-hitl-task-review"
PIPELINE_CHAT = "uhy-hitl-task-review-chat"


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger the UHY HITL Task Review pipeline")
    parser.add_argument("preparation_id", nargs="?", help="UHY preparation ID (e.g. 'Complete Automation-2025')")
    parser.add_argument("task_id",        nargs="?", help="UHY task ID")
    parser.add_argument("--client",       required=False, help="Client name")
    parser.add_argument("--year",         required=False, type=int, help="Tax year")
    parser.add_argument("--chat",         action="store_true", help="Use chat-assisted (AI agent) resolution mode")
    parser.add_argument("--agent-action-id", required=False, help="Override the agent action ID for chat mode")
    parser.add_argument("--workstream-id", required=False, help="Workstream ID to scope the run (required for HITL task visibility)")
    parser.add_argument("--token-only",   action="store_true", help="Print JWT token and exit")
    parser.add_argument("--dry-run",      action="store_true", help="Print the JSON payload without sending")
    args = parser.parse_args()

    ensure_env()

    if args.token_only:
        print(get_bearer_token())
        return 0

    if not args.preparation_id or not args.task_id:
        parser.print_help()
        return 1

    prep_id = args.preparation_id.strip()
    task_id = args.task_id.strip()
    client_name = args.client or prep_id.rsplit("-", 1)[0]
    tax_year = args.year or int(prep_id.rsplit("-", 1)[-1]) if "-" in prep_id else 2025

    pipeline_local_id = PIPELINE_CHAT if args.chat else PIPELINE_FORM
    mode_label = "chat-assisted" if args.chat else "form"

    base = Path(__file__).parent.parent / "workspaces/uhy-staging/pipelines" / pipeline_local_id
    wdl_path = base / "widdle.json"
    meta_path = base / "pipeline.json"

    wdl = json.loads(wdl_path.read_text())
    meta = json.loads(meta_path.read_text())
    remote_id = meta.get("remote_pipeline_id")

    workflow_params = {
        "preparation_id": prep_id,
        "task_id": task_id,
        "client_name": client_name,
        "tax_year": str(tax_year),
    }

    if args.dry_run:
        payload = {
            "pipeline_id": remote_id,
            "test_mode": False,
            "wdl": wdl,
            "workflow_params": workflow_params,
        }
        out_path = Path("/tmp/hitl_payload.json")
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"Dry run ({mode_label}) — payload written to {out_path}")
        print(f"  pipeline_id:    {remote_id}")
        print(f"  preparation_id: {prep_id}")
        print(f"  task_id:        {task_id}")
        print(f"  client_name:    {client_name}")
        print(f"  tax_year:       {tax_year}")
        return 0

    workstream_id = args.workstream_id or ""

    client = get_pipeline_client()
    result = client.test_run(
        remote_id, wdl=wdl, test_mode=False,
        workflow_params=workflow_params,
        workstream_id=workstream_id,
    )

    wf_id = result.get("workflow_id", "?")
    status = result.get("status", "?")

    print(f"HITL pipeline triggered ({mode_label})!")
    print(f"   mode:           {mode_label}")
    print(f"   pipeline_id:    {remote_id}")
    print(f"   preparation_id: {prep_id}")
    print(f"   task_id:        {task_id}")
    print(f"   client_name:    {client_name}")
    print(f"   tax_year:       {tax_year}")
    print(f"   workflow_id:    {wf_id}")
    print(f"   status:         {status}")
    print()
    print(f"The pipeline will pause at the ESCALATE step.")
    if args.chat:
        print(f"An AI agent will assist with resolution in the chat panel.")
    else:
        print(f"Use the structured form to provide corrections and resolution.")
    print(f"Review and resolve at:")
    print(f"   https://staging-adopt-frontend-adopt-dev-ws-3000.adopt.ai/pipelines/{remote_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
