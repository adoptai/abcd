#!/usr/bin/env python3
"""
Trigger a local HITL (ESCALATE) pipeline for one item, and print where to review it.

Runs the pipeline's local WDL (workspaces/<env>/pipelines/<pipeline>/widdle.json, remote id
from pipeline.json) with test_mode=False, so it pauses at its ESCALATE step for a human.
Workflow params come from --param KEY=VALUE (repeatable), so the script works for any
HITL pipeline instead of one engagement's.

Supports two pipelines per use case:
  - default (form): classic HITL with structured field inputs   (--pipeline)
  - chat (--chat):  AI-agent-assisted resolution                (--chat-pipeline)

Usage:
    poetry run python cli/trigger_hitl.py --pipeline <name> --param item_id=123 --param year=2025
    poetry run python cli/trigger_hitl.py --pipeline <name> --chat --chat-pipeline <name>-chat \
        --param item_id=123 --workstream-id <id>
    poetry run python cli/trigger_hitl.py --pipeline <name> --param item_id=123 --dry-run
    poetry run python cli/trigger_hitl.py --token-only

Set ADOPT_FRONTEND_URL in the workspace .env to print a clickable review link.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.auth import get_bearer_token
from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client
from cli.wdl_common.workspace_manager import WORKSPACES_DIR


def parse_params(pairs: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--param expects KEY=VALUE, got {pair!r}")
        params[key.strip()] = value
    return params


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger a local HITL pipeline for one item")
    parser.add_argument("--pipeline", help="Local pipeline name (form mode)")
    parser.add_argument("--chat", action="store_true", help="Use the chat-assisted pipeline")
    parser.add_argument("--chat-pipeline", help="Local pipeline name for --chat")
    parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE", help="Workflow param"
    )
    parser.add_argument(
        "--workstream-id", default="", help="Workstream to scope the run (HITL task visibility)"
    )
    parser.add_argument("--token-only", action="store_true", help="Print JWT token and exit")
    parser.add_argument("--dry-run", action="store_true", help="Write the payload, do not send")
    args = parser.parse_args()

    env_name = ensure_env()

    if args.token_only:
        print(get_bearer_token())
        return 0

    pipeline_name = args.chat_pipeline if args.chat else args.pipeline
    if not pipeline_name:
        parser.error("--chat needs --chat-pipeline" if args.chat else "--pipeline is required")
    try:
        workflow_params = parse_params(args.param)
    except ValueError as e:
        parser.error(str(e))

    mode_label = "chat-assisted" if args.chat else "form"
    base = WORKSPACES_DIR / env_name / "pipelines" / pipeline_name
    wdl = json.loads((base / "widdle.json").read_text())
    remote_id = json.loads((base / "pipeline.json").read_text()).get("remote_pipeline_id")
    if not remote_id:
        print(f"❌ {base}/pipeline.json has no remote_pipeline_id -- save the draft first")
        return 1

    if args.dry_run:
        payload = {
            "pipeline_id": remote_id,
            "test_mode": False,
            "wdl": wdl,
            "workflow_params": workflow_params,
        }
        out_path = Path(tempfile.gettempdir()) / "hitl_payload.json"
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"Dry run ({mode_label}) -- payload written to {out_path}")
        print(f"  pipeline_id: {remote_id}")
        for k, v in workflow_params.items():
            print(f"  {k}: {v}")
        return 0

    result = get_pipeline_client().test_run(
        remote_id,
        wdl=wdl,
        test_mode=False,
        workflow_params=workflow_params,
        workstream_id=args.workstream_id,
    )

    print(f"HITL pipeline triggered ({mode_label})!")
    print(f"   pipeline_id: {remote_id}")
    for k, v in workflow_params.items():
        print(f"   {k}: {v}")
    print(f"   workflow_id: {result.get('workflow_id', '?')}")
    print(f"   status:      {result.get('status', '?')}")
    print()
    print("The pipeline will pause at the ESCALATE step.")
    print(
        "An AI agent will assist with resolution in the chat panel."
        if args.chat
        else "Use the structured form to provide corrections and resolution."
    )
    frontend = os.environ.get("ADOPT_FRONTEND_URL", "").rstrip("/")
    print("Review and resolve at:")
    print(f"   {frontend}/pipelines/{remote_id}" if frontend else f"   /pipelines/{remote_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
