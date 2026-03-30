#!/usr/bin/env python3
"""
Save Pipeline WDL Draft.

Saves local pipeline WDL to the remote platform through the draft system:
1. Create draft with auto-generated prompt (required by API)
2. Poll until server-side WDL generation completes
3. Overwrite with local WDL via update (no LLM, wdl-only)
4. Publish the draft
5. Optionally activate the pipeline (state -> running)

Usage:
  python cli/save_pipeline_draft.py my-pipeline
  python cli/save_pipeline_draft.py my-pipeline --activate
  python cli/save_pipeline_draft.py my-pipeline --dry-run
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
class PipelineDraftResult:
    """Result of a pipeline draft save operation."""

    pipeline_id: str
    success: bool
    message: str
    version_id: str | None = None
    error: str | None = None


def _generate_prompt_from_wdl(wdl: list[dict]) -> str:
    """Auto-generate a short prompt from WDL step descriptions."""
    descriptions = []
    for step in wdl:
        if not isinstance(step, dict):
            continue
        op = step.get("operation", "")
        step_id = step.get("id", "")
        desc = step.get("description") or step.get("notes") or ""
        if op:
            label = op
            if step_id:
                label = f"{step_id} ({op})"
            if desc:
                label += f": {desc}"
            descriptions.append(label)

    if descriptions:
        return "Pipeline with steps: " + "; ".join(descriptions[:5])
    return "Pipeline workflow managed by ABCD"


def save_pipeline_draft(
    local_id: str,
    client: AdoptAPIClient | None = None,
    activate: bool = False,
    verbose: bool = True,
    dry_run: bool = False,
) -> PipelineDraftResult:
    """
    Save local pipeline WDL to remote via the draft system.

    Flow:
    1. Create draft (with auto-prompt, triggers server LLM generation)
    2. Poll until draft WDL is ready
    3. Update draft with local WDL (no LLM since no prompt change)
    4. Publish draft
    5. Optionally activate (requires test to have passed)
    """

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    manager = get_workspace_manager()
    if client is None:
        client = get_api_client_for_env()

    # Find pipeline workspace
    pipeline = manager.find_pipeline(local_id)
    if not pipeline:
        return PipelineDraftResult(
            pipeline_id=local_id,
            success=False,
            message="Pipeline not found",
            error=f"Pipeline '{local_id}' not found in active environment",
        )

    pipeline_path_str = pipeline.get("path")
    if not pipeline_path_str:
        return PipelineDraftResult(
            pipeline_id=local_id,
            success=False,
            message="Invalid pipeline metadata",
            error="Pipeline metadata is missing 'path' field",
        )
    pipeline_path = Path(pipeline_path_str)
    pipeline_id = pipeline.get("pipeline_id")

    if not pipeline_id:
        return PipelineDraftResult(
            pipeline_id=local_id,
            success=False,
            message="Not linked to remote",
            error="Pipeline has no remote pipeline_id. Create it first.",
        )

    log(f"📁 Pipeline: {pipeline_path}")
    log(f"🔑 Remote ID: {pipeline_id}")

    # Load local WDL
    wdl_path = pipeline_path / "widdle.json"
    if not wdl_path.exists():
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="No WDL file",
            error="widdle.json not found",
        )

    try:
        wdl = json.loads(wdl_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Invalid WDL",
            error=str(e),
        )

    if not wdl or not isinstance(wdl, list):
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Empty WDL",
            error="WDL is empty or not a list",
        )

    log(f"📝 WDL: {len(wdl)} steps")

    if dry_run:
        prompt = _generate_prompt_from_wdl(wdl)
        log("\n🔍 [DRY-RUN] Would save pipeline draft:")
        log(f"   Pipeline ID:  {pipeline_id}")
        log(f"   WDL steps:    {len(wdl)}")
        log(f"   Auto-prompt:  {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
        log(f"   Activate:     {activate}")
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=True,
            message="[DRY-RUN] Would save pipeline draft",
        )

    # Step 1: Create draft with auto-generated prompt
    prompt = _generate_prompt_from_wdl(wdl)
    log("\n📋 Step 1: Creating draft (with auto-prompt)...")
    log(f"   Auto-prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

    success, draft_data, msg = client.create_pipeline_workflow_draft(
        pipeline_id=pipeline_id,
        prompt=prompt,
        sources=[],
        destinations=[],
    )

    if not success:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Failed to create draft",
            error=msg,
        )

    version_id = draft_data.get("version_id") if draft_data else None
    if not version_id:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="No version_id returned",
            error="Server did not return version_id from draft creation",
        )

    log(f"   ✅ Draft created: {version_id}")

    # Step 2: Poll until server-side WDL generation completes
    log("\n⏳ Step 2: Waiting for server WDL generation...")
    success, poll_data, msg = client.poll_pipeline_wdl(
        pipeline_id=pipeline_id,
        version_id=version_id,
        interval=2,
        max_attempts=30,
    )

    if not success:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Timeout waiting for draft",
            version_id=version_id,
            error=msg,
        )

    log("   ✅ Server WDL ready (will be overwritten)")

    # Step 3: Overwrite with local WDL (no prompt = no LLM)
    log("\n📝 Step 3: Updating draft with local WDL...")

    success, update_data, msg = client.update_pipeline_workflow_draft(
        pipeline_id=pipeline_id,
        version_id=version_id,
        wdl=wdl,
    )

    if not success:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Failed to update draft with WDL",
            version_id=version_id,
            error=msg,
        )

    log("   ✅ Draft updated with local WDL")

    # Step 4: Publish draft
    log("\n📦 Step 4: Publishing draft...")
    success, pub_data, msg = client.publish_pipeline_workflow_draft(
        pipeline_id=pipeline_id,
        version_id=version_id,
    )

    if not success:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Failed to publish draft",
            version_id=version_id,
            error=msg,
        )

    log("   ✅ Draft published")

    # Save WDL to versions directory
    try:
        versions_dir = pipeline_path / "versions"
        versions_dir.mkdir(exist_ok=True)
        version_file = versions_dir / f"{version_id}_widdle.json"
        version_file.write_text(json.dumps(wdl, indent=2))
        log(f"   💾 WDL saved locally: {version_file.name}")
    except OSError as e:
        log(f"   ⚠️  Failed to save WDL locally: {e}")

    # Update local metadata
    manager.update_pipeline_metadata(
        local_id,
        version_id=version_id,
    )

    # Step 5: Activate if requested
    activation_failed = False
    if activate:
        log("\n🚀 Step 5: Activating pipeline...")

        success_get, pipeline_data, msg_get = client.get_pipeline(pipeline_id)
        if not success_get:
            log(f"   ⚠️  Cannot check test status: {msg_get}")
            activation_failed = True
        else:
            test_status = pipeline_data.get("last_test_run_status") if pipeline_data else None

            if test_status != "passed":
                log(
                    f"   ⚠️  Cannot activate: last_test_run_status is "
                    f"'{test_status}' (must be 'passed')"
                )
                log(f"   Run tests first: python cli/test_pipeline.py {local_id}")
                activation_failed = True
            else:
                success_act, act_data, msg_act = client.update_pipeline(
                    pipeline_id=pipeline_id,
                    state="running",
                )

                if success_act:
                    log("   ✅ Pipeline activated (state=running)")
                    manager.update_pipeline_metadata(local_id, state="running")
                else:
                    log(f"   ❌ Activation failed: {msg_act}")
                    activation_failed = True

    if activate and activation_failed:
        return PipelineDraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="Draft saved and published, but activation failed",
            version_id=version_id,
        )

    return PipelineDraftResult(
        pipeline_id=pipeline_id,
        success=True,
        message="Pipeline draft saved and published",
        version_id=version_id,
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Save pipeline WDL draft to remote platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Save pipeline draft
  python cli/save_pipeline_draft.py my-pipeline

  # Save and activate
  python cli/save_pipeline_draft.py my-pipeline --activate

  # Dry run
  python cli/save_pipeline_draft.py my-pipeline --dry-run
        """,
    )

    parser.add_argument("pipeline", help="Local pipeline ID")
    parser.add_argument(
        "--activate",
        "-a",
        action="store_true",
        help="Activate pipeline after publishing (requires passed test)",
    )
    parser.add_argument("--env", "-e", help="Environment to use")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without making changes",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    manager = get_workspace_manager()

    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"❌ Environment not found: {args.env}")
            return 1

    print("\n" + "=" * 80)
    print("💾 SAVE PIPELINE DRAFT")
    print("=" * 80)

    result = save_pipeline_draft(
        local_id=args.pipeline,
        activate=args.activate,
        verbose=True,
        dry_run=args.dry_run,
    )

    print("\n" + "=" * 80)
    if result.success:
        print("✅ PIPELINE DRAFT SAVED SUCCESSFULLY")
        if result.version_id:
            print(f"   Version: {result.version_id}")
        print(f"   Pipeline: {result.pipeline_id}")
    else:
        print("❌ PIPELINE DRAFT SAVE FAILED")
        if result.error:
            print(f"   Error: {result.error}")
    print("=" * 80)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
