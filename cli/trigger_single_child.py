#!/usr/bin/env python3
"""
Trigger ONE run of a child (per-item) pipeline for testing, optionally in a fresh workstream.

Fan-out parents start a child pipeline once per item; testing the child alone means faking
the parent's params. This runs the child's local WDL (workspaces/<env>/pipelines/<pipeline>/,
remote id from pipeline.json) with the params you pass, after optionally creating a
dedicated workstream (e.g. carrying the HITL resolution agent's action id).

Usage:
  python cli/trigger_single_child.py --pipeline <child> --param item=foo.zip --param audit_id=1
  python cli/trigger_single_child.py --pipeline <child> --param item=foo.zip --test-mode \
      --create-workstream "Child test" --workstream-property hitl_resolution_agent_action_id=<id>
  python cli/trigger_single_child.py --pipeline <child> --param item=foo.zip --workstream-id <id>

The bearer token is added to the params as `auth_token` (children that call back into the
platform need it); a unique `run_id` and `trigger_source` are added unless you pass them.
Workstream creation uses ADOPT_WEBUI_ENDPOINT from the workspace .env.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cli.auth import get_bearer_token
from cli.trigger_hitl import parse_params
from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client
from cli.wdl_common.workspace_manager import WORKSPACES_DIR


def _request(url: str, token: str, method: str = "GET", body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


def create_workstream(token: str, api_base: str, name: str, properties: dict[str, str]) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    full_name = f"{name} - {ts}"[:100]
    result = _request(
        f"{api_base.rstrip('/')}/v1/org/workstreams",
        token,
        method="POST",
        body={
            "name": full_name,
            "description": "Test workstream for a single child-pipeline run",
            "custom_properties": properties,
        },
    )
    ws_id = result.get("id") or result.get("workstream_id") or result.get("data", {}).get("id")
    if not ws_id:
        raise RuntimeError(f"Could not find id in workstream creation response: {result}")
    print(f"  Created workstream: {ws_id}  ({full_name})")
    return str(ws_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger one child pipeline run for testing")
    parser.add_argument("--pipeline", required=True, help="Local child pipeline name")
    parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE", help="Workflow param"
    )
    parser.add_argument("--test-mode", action="store_true", help="test_mode=True (no side effects)")
    parser.add_argument("--workstream-id", help="Reuse an existing workstream")
    parser.add_argument("--create-workstream", metavar="NAME", help="Create a workstream first")
    parser.add_argument(
        "--workstream-property",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="custom_properties for --create-workstream",
    )
    args = parser.parse_args()

    env_name = ensure_env()
    try:
        params = parse_params(args.param)
        properties = parse_params(args.workstream_property)
    except ValueError as e:
        parser.error(str(e))

    base = WORKSPACES_DIR / env_name / "pipelines" / args.pipeline
    wdl = json.loads((base / "widdle.json").read_text())
    remote_id = json.loads((base / "pipeline.json").read_text()).get("remote_pipeline_id")
    if not remote_id:
        print(f"❌ {base}/pipeline.json has no remote_pipeline_id -- save the draft first")
        return 1

    token = get_bearer_token()
    workstream_id = args.workstream_id or ""
    if args.create_workstream:
        api_base = os.environ.get("ADOPT_WEBUI_ENDPOINT", "")
        if not api_base:
            parser.error("--create-workstream needs ADOPT_WEBUI_ENDPOINT in the workspace .env")
        workstream_id = create_workstream(token, api_base, args.create_workstream, properties)

    params.setdefault("run_id", f"single-{uuid.uuid4().hex[:8]}")
    params.setdefault("trigger_source", "trigger_single_child")
    params["auth_token"] = token
    if workstream_id:
        params.setdefault("workstream_id", workstream_id)

    print(f"  Triggering child pipeline {remote_id} (test_mode={args.test_mode})...")
    result = get_pipeline_client().test_run(
        pipeline_id=remote_id,
        wdl=wdl,
        workflow_params=params,
        test_mode=args.test_mode,
        workstream_id=workstream_id,
    )
    print("\n✓ Pipeline triggered!")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
