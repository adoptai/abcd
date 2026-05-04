#!/usr/bin/env python3
"""
Trigger a single uhy-suralink-ingest-per-zip child pipeline run for testing.

Creates a dedicated workstream (with hitl_resolution_agent_action_id) and
immediately triggers the child pipeline for the given zip file.

Usage:
  python cli/trigger_single_child.py
  python cli/trigger_single_child.py --zip "Benzinga TY 2025.zip"
  python cli/trigger_single_child.py --zip "Benzinga TY 2025.zip" --test-mode
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cli.auth import get_bearer_token
from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client

CHILD_PIPELINE_REMOTE_ID = "7739948409864f7a"
RESOLUTION_AGENT_ACTION_ID = "390fb432-22e0-4efe-9001-02eb17fbce7c"
DEFAULT_ZIP = "Benzinga TY 2025.zip"
DEFAULT_LANDING_PREFIX = "uhy-staging/2686439/suralink"
DEFAULT_S3_URL = "s3://adopt-dev-uploads/uhy-staging/2686439/suralink/Benzinga_TY_2025.zip"
DEFAULT_AUDIT_ID = "2686439"
DEFAULT_RUN_ID = "test-single"

API_BASE = "https://staging-adopt-backend-adopt-dev-ws-8000.adopt.ai"
ORG_ID = "79908b84-b61c-4a4c-ab85-5180c5acba0d"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request(url: str, token: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


def create_workstream(token: str, zip_filename: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = zip_filename.replace(".zip", "").replace(" ", "_")
    name = f"Suralink TEST - {safe} - {ts}"[:100]

    result = _request(
        f"{API_BASE}/v1/org/workstreams",
        token,
        method="POST",
        body={
            "name": name,
            "description": f"Test workstream for single-child trigger ({zip_filename})",
            "custom_properties": {"hitl_resolution_agent_action_id": RESOLUTION_AGENT_ACTION_ID},
        },
    )
    ws_id = result.get("id") or result.get("workstream_id") or result.get("data", {}).get("id")
    if not ws_id:
        raise RuntimeError(f"Could not find id in workstream creation response: {result}")
    print(f"  Created workstream: {ws_id}  ({name})")
    return ws_id


def trigger_child_pipeline(
    token: str, zip_filename: str, workstream_id: str, test_mode: bool
) -> dict:
    client = get_pipeline_client()

    run_id = f"single-{uuid.uuid4().hex[:8]}"
    safe_slug = zip_filename.replace(" ", "_").replace(".zip", "")
    s3_url = f"s3://adopt-dev-uploads/{DEFAULT_LANDING_PREFIX}/{safe_slug}.zip"

    workflow_params = {
        "auth_token": token,
        "audit_id": DEFAULT_AUDIT_ID,
        "run_id": run_id,
        "zip_filename": zip_filename,
        "safe_slug": safe_slug,
        "s3_url": s3_url,
        "landing_prefix": DEFAULT_LANDING_PREFIX,
        "trigger_source": "trigger_single_child",
        "workstream_id": workstream_id,
    }

    # Load child WDL for client-side param substitution (required by /test-run)
    child_wdl_path = (
        REPO_ROOT
        / "workspaces"
        / "uhy-staging"
        / "pipelines"
        / "uhy-suralink-ingest-per-zip"
        / "widdle.json"
    )
    with open(child_wdl_path) as f:
        child_wdl = json.load(f)

    print(f"  Triggering child pipeline {CHILD_PIPELINE_REMOTE_ID}...")
    print(f"    zip_filename  = {zip_filename}")
    print(f"    workstream_id = {workstream_id}")
    print(f"    test_mode     = {test_mode}")

    result = client.test_run(
        pipeline_id=CHILD_PIPELINE_REMOTE_ID,
        wdl=child_wdl,
        workflow_params=workflow_params,
        test_mode=test_mode,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Trigger a single child pipeline run for testing")
    parser.add_argument(
        "--zip", default=DEFAULT_ZIP, help=f"Zip filename (default: {DEFAULT_ZIP!r})"
    )
    parser.add_argument(
        "--test-mode", action="store_true", help="Run in test_mode=True (safe, no side-effects)"
    )
    parser.add_argument("--workstream-id", help="Reuse an existing workstream ID (skip creation)")
    args = parser.parse_args()

    ensure_env()
    token = get_bearer_token()
    print(f"\nToken obtained. Starting single child trigger for: {args.zip!r}")

    if args.workstream_id:
        ws_id = args.workstream_id
        print(f"  Using existing workstream: {ws_id}")
    else:
        print("\n1. Creating workstream...")
        ws_id = create_workstream(token, args.zip)

    print("\n2. Triggering child pipeline...")
    result = trigger_child_pipeline(token, args.zip, ws_id, args.test_mode)

    print("\n✓ Pipeline triggered!")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
