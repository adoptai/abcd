#!/usr/bin/env python3
"""
Trigger a test run (or production run) for a pipeline.

By default runs in test_mode=true (safe for development, no concurrent-run check).
Use --production to run a full production-style run (test_mode=false, returns 409
if another run is already active — same as UI "Run Now").

Usage:
    python cli/test_pipeline.py <pipeline-id>              # test run (safe)
    python cli/test_pipeline.py <pipeline-id> --production # production run
    python cli/test_pipeline.py <pipeline-id> --local-wdl  # send local widdle.json

After a successful test you can either:
  - Mark it as passed manually: python cli/test_pipeline.py <id> --mark-passed
  - Or use publish_pipeline.py which marks it passed automatically before publishing.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client
from cli.wdl_common.workspace_manager import get_workspace_manager


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trigger a pipeline test run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
test_mode behaviour:
  true  (default) – test run, no concurrent-run check, safe during development
  false           – production run, 409 if another run is already active

Examples:
  # Safe test run (development)
  python cli/test_pipeline.py my-pipeline

  # Send local widdle.json (don't use saved draft)
  python cli/test_pipeline.py my-pipeline --local-wdl

  # Production-mode run (same as UI "Run Now")
  python cli/test_pipeline.py my-pipeline --production

  # Just mark the last run as passed (no new run)
  python cli/test_pipeline.py my-pipeline --mark-passed

  # Check status / recent runs
  python cli/test_pipeline.py my-pipeline --status
        """,
    )
    parser.add_argument("pipeline_id", help="Pipeline workspace ID")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Run in production mode (test_mode=false). Returns 409 if a run is already active.",
    )
    parser.add_argument(
        "--local-wdl",
        action="store_true",
        help="Send the local widdle.json instead of using the saved remote draft",
    )
    parser.add_argument(
        "--mark-passed",
        action="store_true",
        help="Mark the pipeline's test run status as passed (no new run)",
    )
    parser.add_argument(
        "--mark-failed",
        action="store_true",
        help="Mark the pipeline's test run status as failed (no new run)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show pipeline status and recent runs (no new run)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    env_name = ensure_env()
    manager = get_workspace_manager()

    meta = manager.get_pipeline_workspace(args.pipeline_id, env_name)
    if not meta:
        print(f"❌ Pipeline workspace not found: {args.pipeline_id}")
        return 1

    remote_pipeline_id = meta.get("remote_pipeline_id")
    if not remote_pipeline_id and not args.mark_passed and not args.mark_failed and not args.status:
        print(
            f"❌ No remote pipeline linked. Run save_pipeline_draft.py first:\n"
            f"   python cli/save_pipeline_draft.py {args.pipeline_id}"
        )
        return 1

    client = get_pipeline_client()

    # --status
    if args.status:
        print(f"\n📋 Pipeline: {args.pipeline_id}")
        print(f"   Name          : {meta['name']}")
        print(f"   Remote ID     : {remote_pipeline_id or '(none)'}")
        print(f"   State         : {meta.get('state', 'local')}")
        if remote_pipeline_id:
            try:
                remote = client.get_pipeline(remote_pipeline_id)
                print(f"   Remote state  : {remote.get('state', '?')}")
                print(f"   Last test     : {remote.get('last_test_run_status', '?')}")
                runs = client.list_runs(remote_pipeline_id, page_size=5)
                run_list = runs if isinstance(runs, list) else runs.get("items", [])
                if run_list:
                    print(f"\n   Recent runs ({len(run_list)}):")
                    for run in run_list[:5]:
                        print(
                            f"     • {run.get('id', '?')[:8]}…  "
                            f"status={run.get('status', '?')}  "
                            f"started={run.get('started_at', '?')[:19]}"
                        )
            except Exception as exc:
                print(f"   (Could not fetch remote details: {exc})")
        print()
        return 0

    # --mark-passed / --mark-failed
    if args.mark_passed or args.mark_failed:
        if not remote_pipeline_id:
            print("❌ No remote pipeline linked.")
            return 1
        status = "passed" if args.mark_passed else "failed"
        try:
            if args.mark_passed:
                client.mark_test_passed(remote_pipeline_id)
            else:
                client.mark_test_failed(remote_pipeline_id)
            print(f"✅ Test run marked as {status} for pipeline: {remote_pipeline_id}")
            return 0
        except Exception as exc:
            print(f"❌ Failed to mark test status: {exc}")
            return 1

    # Determine WDL to send
    wdl: list | None = None
    if args.local_wdl:
        wdl_path = Path(meta["path"]) / "widdle.json"
        if not wdl_path.exists():
            print(f"❌ widdle.json not found: {wdl_path}")
            return 1
        try:
            wdl = json.loads(wdl_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"❌ widdle.json is not valid JSON: {exc}")
            return 1
        if not wdl:
            print("❌ widdle.json is empty")
            return 1

    test_mode = not args.production
    mode_label = "test" if test_mode else "production"
    wdl_label = f"local widdle.json ({len(wdl)} steps)" if wdl else "remote draft"

    print(f"\n🚀 Triggering {mode_label} run for pipeline: {args.pipeline_id}")
    print(f"   Remote ID   : {remote_pipeline_id}")
    print(
        f"   Mode        : {'test_mode=true (safe)' if test_mode else 'test_mode=false (production)'}"
    )
    print(f"   WDL         : {wdl_label}")

    # Inject a fresh bearer token so {workflow_arguments.auth_token} is resolved.
    # The /test-run endpoint does NOT perform server-side workflow_arguments
    # substitution, so we must pre-substitute here.
    workflow_params: dict = {"auth_token": client.bearer_token}

    try:
        result = client.test_run(
            remote_pipeline_id,
            wdl=wdl,
            test_mode=test_mode,
            workflow_params=workflow_params,
            # For production runs, allow_concurrent_runs lets a stale
            # pipeline_run record from a previous run be safely bypassed.
            allow_concurrent_runs=not test_mode,
            max_concurrent_runs=2,
        )
        run_id = result.get("run_id") or result.get("id") or "?"
        print("\n✅ Run triggered!")
        print(f"   Run ID      : {run_id}")
        if args.verbose:
            print(f"   Full response: {json.dumps(result, indent=2)}")
        print(f"\n💡 Results stream via Pusher channel: conversation_{remote_pipeline_id}")
        print(
            f"   After reviewing results, mark as passed:\n"
            f"   python cli/test_pipeline.py {args.pipeline_id} --mark-passed"
        )
        print()
        return 0
    except Exception as exc:
        msg = str(exc)
        if "409" in msg:
            print(
                "\n⚠️  Conflict (409): Another run is already active for this pipeline.\n"
                "   Wait for it to complete, or use --production only when intentional."
            )
        else:
            print(f"\n❌ Test run failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
