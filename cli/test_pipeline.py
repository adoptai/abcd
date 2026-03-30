#!/usr/bin/env python3
"""
Pipeline Test Runner.

Test pipeline WDL directly via the test-run endpoint (test_mode=true).
No draft or version needed -- WDL is sent directly for execution.

Features:
- Single pipeline test: test_pipeline.py my-pipeline
- Pipeline ID resolution: uses local workspace metadata to find remote pipeline_id
- Dispatches test run and reports workflow_id

Known gap: Test-run results are delivered via Pusher (WebSocket), not HTTP.
The status endpoint lives on the internal workflows service and is not proxied
through the web backend. Until Pusher subscription or a proxy is added, ABCD
cannot automatically poll for test completion.

Usage:
  python cli/test_pipeline.py my-pipeline
  python cli/test_pipeline.py --pipeline-id <remote-id> --wdl-file widdle.json
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager


@dataclass
class PipelineTestResult:
    """Result of a pipeline test run."""

    pipeline_id: str
    success: bool
    message: str
    run_data: dict | None = None
    error: str | None = None


def run_pipeline_test(
    local_id: str | None = None,
    remote_pipeline_id: str | None = None,
    wdl_file: Path | None = None,
    client: AdoptAPIClient | None = None,
    verbose: bool = True,
) -> PipelineTestResult:
    """
    Start a pipeline test run.

    Dispatches the WDL to the test-run endpoint and returns immediately.
    Result polling is not yet implemented (requires Pusher or a status proxy).

    Args:
        local_id: Local pipeline workspace ID
        remote_pipeline_id: Remote pipeline ID (if known)
        wdl_file: Path to WDL file (resolved from workspace if not provided)
        client: API client (created if not provided)
        verbose: Print progress

    Returns:
        PipelineTestResult
    """

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    manager = get_workspace_manager()

    if client is None:
        client = get_api_client_for_env()

    # Resolve pipeline_id and WDL
    pipeline_id = remote_pipeline_id
    wdl = None
    pipeline_path = None

    if local_id:
        pipeline = manager.find_pipeline(local_id)
        if not pipeline:
            return PipelineTestResult(
                pipeline_id=local_id,
                success=False,
                message="Pipeline not found",
                error=f"Pipeline '{local_id}' not found in active environment",
            )

        pipeline_id = pipeline.get("pipeline_id")
        pipeline_path_str = pipeline.get("path")
        if pipeline_path_str:
            pipeline_path = Path(pipeline_path_str)

        if not pipeline_id:
            return PipelineTestResult(
                pipeline_id=local_id,
                success=False,
                message="No remote pipeline ID",
                error="Pipeline not linked to remote. Create it first with 'pipeline create'.",
            )

        log(f"📁 Pipeline: {pipeline_path}")
        log(f"🔑 Remote ID: {pipeline_id}")

    if not pipeline_id:
        return PipelineTestResult(
            pipeline_id=local_id or "?",
            success=False,
            message="No pipeline ID",
            error="Must provide either local_id or remote_pipeline_id",
        )

    # Load WDL
    if wdl_file:
        wdl_path = wdl_file
    elif pipeline_path:
        wdl_path = pipeline_path / "widdle.json"
    else:
        return PipelineTestResult(
            pipeline_id=pipeline_id,
            success=False,
            message="No WDL",
            error="No WDL file specified and no local workspace found",
        )

    if not wdl_path.exists():
        return PipelineTestResult(
            pipeline_id=pipeline_id,
            success=False,
            message="WDL file not found",
            error=f"widdle.json not found at {wdl_path}",
        )

    try:
        wdl = json.loads(wdl_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return PipelineTestResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Invalid WDL",
            error=str(e),
        )

    if not wdl or not isinstance(wdl, list):
        return PipelineTestResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Empty WDL",
            error="WDL is empty or not a list",
        )

    log(f"📝 WDL: {len(wdl)} steps")

    # Execute test run
    log("\n🚀 Starting test run (test_mode=true)...")

    success, data, msg = client.test_pipeline_wdl(pipeline_id, wdl)
    if not success:
        return PipelineTestResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Test run failed to start",
            error=msg,
        )

    workflow_id = data.get("workflow_id") if data else None
    log("   ✅ Test run started")
    if workflow_id:
        log(f"   Workflow ID: {workflow_id}")

    # GAP: Result polling not yet implemented.
    # Test-run results are delivered via Pusher (channel: conversation_<pipeline_id>).
    # The workflow status endpoint (GET /api/v1/wdl/status/{workflow_id}) lives on
    # the internal workflows service and is not proxied through the web backend.
    log("\n📋 Test run dispatched successfully.")
    log(f"   Results arrive via Pusher channel: conversation_{pipeline_id}")
    log("   Check the platform UI for results, then persist status with:")
    log(f'   POST /v1/pipelines/{pipeline_id}/test-run-status  {{"status": "passed"}}')

    return PipelineTestResult(
        pipeline_id=pipeline_id,
        success=True,
        message="Test run started (result polling not yet implemented — check platform UI)",
        run_data={"workflow_id": workflow_id, "status": "started"},
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Pipeline Test Runner - test pipeline WDL via test-run endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test by local pipeline ID
  python cli/test_pipeline.py my-pipeline

  # Test with explicit remote ID and WDL file
  python cli/test_pipeline.py --pipeline-id abc123 --wdl-file widdle.json
        """,
    )

    parser.add_argument("pipeline", nargs="?", help="Local pipeline ID")
    parser.add_argument("--pipeline-id", help="Remote pipeline ID (overrides local lookup)")
    parser.add_argument("--wdl-file", help="Path to WDL file (overrides workspace)")
    parser.add_argument("--env", "-e", help="Environment to use")

    args = parser.parse_args()

    if not args.pipeline and not args.pipeline_id:
        parser.print_help()
        print("\n❌ Must provide pipeline ID (positional) or --pipeline-id")
        return 1

    manager = get_workspace_manager()

    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"❌ Environment not found: {args.env}")
            return 1

    wdl_file = Path(args.wdl_file) if args.wdl_file else None

    print("\n" + "=" * 80)
    print("🔄 PIPELINE TEST RUN")
    print("=" * 80)

    result = run_pipeline_test(
        local_id=args.pipeline,
        remote_pipeline_id=args.pipeline_id,
        wdl_file=wdl_file,
        verbose=True,
    )

    print("\n" + "=" * 80)
    if result.success:
        print("✅ PIPELINE TEST DISPATCHED")
    else:
        print("❌ PIPELINE TEST FAILED")
        if result.error:
            print(f"   Error: {result.error}")
    print("=" * 80)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
