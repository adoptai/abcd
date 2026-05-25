"""Adryann's one-off harness to exec-test p1-copy-from-inbox v51 against staging.

Per UNBLOCK_ADRYANN_V51_EXEC_20260525.md (Iain). cli/test_pipeline.py only
injects {workflow_arguments.auth_token}; v51 needs ten more workflow_args
substituted client-side or the SANDBOX env block ships with literal
"{workflow_arguments.audit_id}" strings and the upload_and_poll.py call
breaks at the boto3 download step.

Two phases for safety:

  Phase 1  (--upload):    s3 cp the local Bridge zip + create a throwaway
                          workstream. Writes run_inputs.json checkpoint so
                          the trigger can be inspected before firing.
  Phase 2  (--trigger):   pipeline_client.test_run() with the full
                          workflow_params dict, then poll list_runs until
                          terminal status. Reads run_inputs.json from
                          Phase 1.

Usage:
  python uhy\\ solution/change-2-upload-and-poll/exec_v51_smoke.py --upload \\
      --zip "/Users/adryannmillos/Downloads/Bridge Organics - TY 2025 - AI Test.zip"
  python uhy\\ solution/change-2-upload-and-poll/exec_v51_smoke.py --trigger
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cli.wdl_common.pipeline_client import get_pipeline_client  # noqa: E402

import os

ORG_ID = "c6ff8395-d959-48ab-885e-fc81781b5871"
DOCSTORE_BUCKET = "adopt-org-docstore"
DOCSTORE_KEY = os.environ.get("ADOPT_DOCSTORE_AWS_ACCESS_KEY_ID", "")
DOCSTORE_SECRET = os.environ.get("ADOPT_DOCSTORE_AWS_SECRET_ACCESS_KEY", "")
DOCSTORE_REGION = "us-east-1"
if not DOCSTORE_KEY or not DOCSTORE_SECRET:
    raise SystemExit(
        "ERROR: set ADOPT_DOCSTORE_AWS_ACCESS_KEY_ID + "
        "ADOPT_DOCSTORE_AWS_SECRET_ACCESS_KEY env vars "
        "(see workspaces/uhy-prod/pipelines/p1-copy-from-inbox/widdle.json env block "
        "for the values currently used by the pipeline)"
    )

PIPELINE_WORKSPACE_ID = "p1-copy-from-inbox"
REMOTE_PIPELINE_ID = "2622de32e0f64bae"

WIDDLE_PATH = REPO_ROOT / "workspaces" / "uhy-prod" / "pipelines" / PIPELINE_WORKSPACE_ID / "widdle.json"
CHECKPOINT = Path(__file__).parent / "run_inputs.json"

EXPECTED_SHA256 = "680f9103cf6f2d48743a99e592cd67ac6f8f9d0c91a8632e8cd4330d4557a2a3"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_upload(args: argparse.Namespace) -> int:
    import boto3

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"ERROR zip not found: {zip_path}")
        return 1

    print(f"[1/4] verifying sha256 of {zip_path.name} ...")
    actual = _sha256(zip_path)
    print(f"      sha256 = {actual}")
    if actual != EXPECTED_SHA256:
        print(f"ERROR sha256 mismatch (expected {EXPECTED_SHA256})")
        return 1
    print(f"      MATCH (Iain's UNBLOCK §1)")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_id = "adryann-v51-test"
    suralink_run_id = stamp
    zip_filename = zip_path.name
    source_prefix = f"org_id={ORG_ID}/suralink_inbox/{audit_id}/{suralink_run_id}/"
    s3_key = f"{source_prefix}{zip_filename}"
    s3_uri = f"s3://{DOCSTORE_BUCKET}/{s3_key}"

    print(f"[2/4] uploading -> {s3_uri}")
    s3 = boto3.client(
        "s3",
        aws_access_key_id=DOCSTORE_KEY,
        aws_secret_access_key=DOCSTORE_SECRET,
        region_name=DOCSTORE_REGION,
    )
    s3.upload_file(str(zip_path), DOCSTORE_BUCKET, s3_key)
    head = s3.head_object(Bucket=DOCSTORE_BUCKET, Key=s3_key)
    print(f"      uploaded {head['ContentLength']:,} bytes, etag={head['ETag']}")

    print(f"[3/4] creating throwaway workstream ...")
    pc = get_pipeline_client()
    ws_name = f"Adryann v51 smoke - Bridge Organics - {stamp}"
    ws = pc._req(
        "post",
        "/v1/org/workstreams",
        json={
            "name": ws_name,
            "description": (
                "Throwaway workstream for v51 exec smoke per "
                "UNBLOCK_ADRYANN_V51_EXEC_20260525. Bridge Organics zip, "
                f"sha256 {EXPECTED_SHA256}. Created {stamp}."
            ),
            "custom_properties": {
                "hitl_resolution_agent_action_id": "1215c0d1-e18b-46e6-8e32-7ed3d5ebfcb2"
            },
        },
    )
    ws_id = ws.get("id") or ws.get("workstream_id")
    if not ws_id:
        print(f"ERROR workstream create returned no id: {ws}")
        return 1
    print(f"      workstream_id={ws_id}  name={ws_name!r}")

    workflow_params = {
        "audit_id": audit_id,
        "suralink_run_id": suralink_run_id,
        "zip_filename": zip_filename,
        "workstream_id": ws_id,
        "source_path": "test-adryann-bridge-organics",
        "store_id": "test-store",
        "resolved_client_name": "Bridge Organics",
        "resolved_tax_year": "2025",
        "force_replace": "false",
    }

    checkpoint = {
        "created_at": stamp,
        "zip_local_path": str(zip_path),
        "zip_sha256": actual,
        "zip_size_bytes": zip_path.stat().st_size,
        "s3_uri": s3_uri,
        "s3_etag": head["ETag"],
        "remote_pipeline_id": REMOTE_PIPELINE_ID,
        "workstream_id": ws_id,
        "workstream_name": ws_name,
        "workflow_params": workflow_params,
    }
    print(f"[4/4] writing checkpoint -> {CHECKPOINT}")
    CHECKPOINT.write_text(json.dumps(checkpoint, indent=2))
    print(json.dumps(checkpoint, indent=2))
    print()
    print("OK Phase 1 complete. Review the checkpoint, then run:")
    print(f"  python {Path(__file__).relative_to(REPO_ROOT)} --trigger")
    return 0


def cmd_trigger(args: argparse.Namespace) -> int:
    if not CHECKPOINT.exists():
        print(f"ERROR no checkpoint at {CHECKPOINT}. Run --upload first.")
        return 1
    cp = json.loads(CHECKPOINT.read_text())
    print(f"[1/3] loaded checkpoint  audit_id={cp['workflow_params']['audit_id']}  "
          f"ws={cp['workstream_id']}  s3={cp['s3_uri']}", flush=True)

    if not WIDDLE_PATH.exists():
        print(f"ERROR v51 widdle not found: {WIDDLE_PATH}", flush=True)
        return 1
    wdl = json.loads(WIDDLE_PATH.read_text())
    print(f"      loaded v51 widdle ({len(wdl)} steps) from {WIDDLE_PATH.relative_to(REPO_ROOT)}",
          flush=True)

    pc = get_pipeline_client()
    workflow_params = dict(cp["workflow_params"])
    workflow_params["auth_token"] = pc.bearer_token

    print(f"[2/3] POST /v1/pipelines/workflows/test-run  test_mode=false", flush=True)
    print(f"      (test_mode=true skips _create_pipeline_run_if_applicable in "
          f"adoptai-workflows/.../wdl_execution/workflow.py:310 \u2014 needed for list_runs poll)",
          flush=True)
    result = pc.test_run(
        REMOTE_PIPELINE_ID,
        wdl=wdl,
        test_mode=False,
        workflow_params=workflow_params,
        workstream_id=cp["workstream_id"],
        allow_concurrent_runs=True,
        max_concurrent_runs=25,
    )
    print(f"      response: {json.dumps(result)[:400]}", flush=True)
    triggered_workflow_id = result.get("workflow_id") or result.get("id")
    print(f"      triggered workflow_id={triggered_workflow_id}", flush=True)

    cp["triggered_workflow_id"] = triggered_workflow_id
    cp["triggered_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    CHECKPOINT.write_text(json.dumps(cp, indent=2))

    if args.no_poll:
        print("OK trigger sent. --no-poll set, exiting.")
        return 0

    print(f"[3/3] polling list_runs by workstream_id={cp['workstream_id']} until terminal "
          f"(max {args.timeout_min} min)", flush=True)
    deadline = time.monotonic() + args.timeout_min * 60
    terminal_statuses = {"success", "succeeded", "failed", "awaiting_human", "cancelled"}
    last_status = None
    t_start = time.monotonic()
    while time.monotonic() < deadline:
        runs = pc.list_runs(REMOTE_PIPELINE_ID, page=1, page_size=10).get("items", [])
        match = next((r for r in runs if r.get("workstream_id") == cp["workstream_id"]), None)
        status = (match or {}).get("status")
        if status != last_status:
            elapsed = int(time.monotonic() - t_start)
            run_id_short = (match or {}).get("id", "?")[:18]
            err = (match or {}).get("error_message") or ""
            print(f"      [{elapsed:4}s] run_id={run_id_short}  status={status}  err={err[:150]}",
                  flush=True)
            last_status = status
        if status in terminal_statuses:
            dur_ms = (match or {}).get("duration_ms") or 0
            print(f"      TERMINAL status={status} after {dur_ms / 1000:.1f}s wall-clock",
                  flush=True)
            cp["final_run"] = match
            cp["final_run_id"] = (match or {}).get("id")
            CHECKPOINT.write_text(json.dumps(cp, indent=2, default=str))
            if status not in ("success", "succeeded"):
                print(f"      error_step_id={match.get('error_step_id')}", flush=True)
                print(f"      error_message={match.get('error_message')}", flush=True)
                return 2
            return 0
        time.sleep(10)

    print(f"ERROR poll timeout after {args.timeout_min} min", flush=True)
    return 3


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="phase")

    up = p.add_argument_group("phase 1")
    up.add_argument("--upload", action="store_true", help="Phase 1: upload + create ws")
    up.add_argument("--zip", help="path to local Bridge zip (Phase 1 only)")

    tr = p.add_argument_group("phase 2")
    tr.add_argument("--trigger", action="store_true", help="Phase 2: trigger v51 + poll")
    tr.add_argument("--no-poll", action="store_true", help="Trigger only, don't poll")
    tr.add_argument("--timeout-min", type=int, default=15, help="Poll timeout in minutes (default 15)")

    args = p.parse_args()
    if args.upload and args.trigger:
        print("ERROR pick --upload OR --trigger, not both")
        return 1
    if args.upload:
        if not args.zip:
            print("ERROR --upload requires --zip PATH")
            return 1
        return cmd_upload(args)
    if args.trigger:
        return cmd_trigger(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
