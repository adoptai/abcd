#!/usr/bin/env python3
"""
Push local pipeline WDL to the platform as a draft.

Auto-creates the remote pipeline on the first push if no remote_pipeline_id
is stored in pipeline.json yet.

Usage:
    python cli/save_pipeline_draft.py <pipeline-id>
    python cli/save_pipeline_draft.py <pipeline-id> --description "Fixed fan-out step"
    python cli/save_pipeline_draft.py pipeline-a pipeline-b --parallel 2

Workflow:
    1. Load widdle.json from workspace
    2. If no remote_pipeline_id → POST /v1/pipelines (create remote)
    3. POST /v1/pipelines/workflows/draft (create draft, triggers async LLM)
    4. Poll until LLM draft is ready
    5. PUT /v1/pipelines/workflows/draft (push our WDL, no LLM)
    6. Poll until WDL is confirmed
    7. Save version snapshot locally (versions/v{N}_widdle.json)
    8. Update pipeline.json with remote_pipeline_id and version_id
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import ensure_env
from cli.wdl_common.pipeline_client import get_pipeline_client
from cli.wdl_common.workspace_manager import get_workspace_manager

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


@dataclass
class DraftResult:
    pipeline_id: str
    success: bool
    message: str
    remote_pipeline_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    error: str | None = None


def _next_version_number(pipeline_path: Path) -> int:
    """Determine the next version number based on existing version snapshots."""
    versions_dir = pipeline_path / "versions"
    if not versions_dir.exists():
        return 1
    existing = list(versions_dir.glob("v*_widdle.json"))
    if not existing:
        return 1
    nums = []
    for f in existing:
        try:
            nums.append(int(f.stem.split("_")[0].lstrip("v")))
        except ValueError:
            pass
    return max(nums, default=0) + 1


def save_single_draft(
    pipeline_id: str,
    description: str | None = None,
    dry_run: bool = False,
) -> DraftResult:
    """Save draft for a single pipeline workspace."""
    env_name = ensure_env()
    manager = get_workspace_manager()

    meta = manager.get_pipeline_workspace(pipeline_id, env_name)
    if not meta:
        return DraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message=f"Pipeline workspace not found: {pipeline_id}",
            error="not_found",
        )

    pipeline_path = Path(meta["path"])
    wdl_path = pipeline_path / "widdle.json"

    if not wdl_path.exists():
        return DraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="widdle.json not found in workspace",
            error="missing_wdl",
        )

    try:
        wdl = json.loads(wdl_path.read_text())
    except json.JSONDecodeError as exc:
        return DraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message=f"widdle.json is not valid JSON: {exc}",
            error="invalid_json",
        )

    if not wdl:
        return DraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message="widdle.json is empty — add WDL steps before saving",
            error="empty_wdl",
        )

    if dry_run:
        print(f"   DRY RUN: Would push {len(wdl)} WDL steps for pipeline: {pipeline_id}")
        return DraftResult(
            pipeline_id=pipeline_id,
            success=True,
            message="Dry run — no changes made",
        )

    client = get_pipeline_client()
    remote_pipeline_id = meta.get("remote_pipeline_id")

    try:
        # Step 1 — create remote pipeline if not yet linked
        if not remote_pipeline_id:
            print(f"   → Creating remote pipeline for '{meta['name']}'...")
            pipeline_resp = client.create_pipeline(
                name=meta["name"],
                description=meta.get("description", ""),
                prompt=meta.get("prompt") or meta["name"],
                source=meta.get("source"),
                destinations=meta.get("destinations"),
                schedule_type=meta.get("schedule_type", "manual"),
            )
            remote_pipeline_id = pipeline_resp["id"]
            manager.update_pipeline_workspace_meta(
                pipeline_id,
                {"remote_pipeline_id": remote_pipeline_id, "state": "draft"},
                env_name,
            )
            print(f"   ✅ Remote pipeline created: {remote_pipeline_id}")
        else:
            print(f"   ℹ️  Using existing remote pipeline: {remote_pipeline_id}")

        # Step 2 — create draft (triggers async LLM)
        print("   → Creating draft...")
        prompt = meta.get("prompt") or meta["name"]
        draft = client.create_draft(remote_pipeline_id, prompt)
        version_id = draft.get("version_id") or draft.get("id")
        print(f"   ✅ Draft created: version_id={version_id}")

        # Step 3 — wait for LLM draft
        print("   → Waiting for LLM draft to complete...")
        client.poll_until_wdl_ready(remote_pipeline_id, version_id)
        print("   ✅ LLM draft ready")

        # Step 4 — push our WDL
        print(f"   → Pushing WDL ({len(wdl)} steps)...")
        push_result = client.push_wdl(remote_pipeline_id, version_id, wdl)
        publish_version_id = push_result.get("version_id") or version_id
        print(f"   ✅ WDL pushed (publish version: {publish_version_id})")

        # Step 5 — poll until WDL is confirmed on platform
        print("   → Waiting for WDL confirmation...")
        confirmed = client.poll_until_wdl_confirmed(
            remote_pipeline_id, publish_version_id, wdl[0]["id"]
        )
        if not confirmed:
            return DraftResult(
                pipeline_id=pipeline_id,
                success=False,
                message="WDL push did not appear after polling",
                error="poll_timeout",
            )
        print(f"   ✅ WDL confirmed on platform")

        # Step 6 — save local version snapshot
        version_number = _next_version_number(pipeline_path)
        snapshot = manager.save_pipeline_version(
            pipeline_id, wdl, version_number, env_name
        )
        if snapshot:
            print(f"   💾 Version snapshot: {snapshot}")

        # Step 7 — update local metadata
        updates: dict = {
            "remote_pipeline_id": remote_pipeline_id,
            "version_id": publish_version_id,
            "state": "draft",
        }
        if description:
            updates["last_description"] = description
        manager.update_pipeline_workspace_meta(pipeline_id, updates, env_name)

        return DraftResult(
            pipeline_id=pipeline_id,
            success=True,
            message=f"Draft saved (version {version_number})",
            remote_pipeline_id=remote_pipeline_id,
            version_id=publish_version_id,
            version_number=version_number,
        )

    except Exception as exc:
        return DraftResult(
            pipeline_id=pipeline_id,
            success=False,
            message=str(exc),
            error="api_error",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push pipeline WDL to platform as draft",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli/save_pipeline_draft.py my-pipeline
  python cli/save_pipeline_draft.py my-pipeline --description "Added fan-out step"
  python cli/save_pipeline_draft.py pipeline-a pipeline-b --parallel 2
  python cli/save_pipeline_draft.py my-pipeline --dry-run
        """,
    )
    parser.add_argument(
        "pipeline_ids",
        nargs="+",
        metavar="PIPELINE_ID",
        help="Pipeline workspace ID(s) to save",
    )
    parser.add_argument("--description", "-d", help="Description for this draft version")
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="Number of pipelines to process in parallel (default: 1)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose

    print(f"\n{'='*65}")
    print(f"  Save Pipeline Draft(s)")
    print(f"{'='*65}\n")

    results: list[DraftResult] = []

    if len(args.pipeline_ids) == 1 or args.parallel <= 1:
        for pid in args.pipeline_ids:
            print(f"📦 {pid}")
            result = save_single_draft(pid, args.description, args.dry_run)
            results.append(result)
            if result.success:
                print(f"   ✅ {result.message}\n")
            else:
                print(f"   ❌ {result.message}\n")
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            future_to_pid = {
                executor.submit(save_single_draft, pid, args.description, args.dry_run): pid
                for pid in args.pipeline_ids
            }
            for future in as_completed(future_to_pid):
                result = future.result()
                results.append(result)
                status = "✅" if result.success else "❌"
                print(f"   {status} {result.pipeline_id}: {result.message}")

    # Summary
    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    print(f"\n{'='*65}")
    print(f"  {len(successes)} ✅  {len(failures)} ❌")
    print(f"{'='*65}")

    if successes:
        print("\nSaved drafts:")
        for r in successes:
            remote = r.remote_pipeline_id or "?"
            version = r.version_id or "?"
            print(f"  • {r.pipeline_id}  →  remote={remote}  version={version}")

    if failures:
        print("\nFailed:")
        for r in failures:
            print(f"  • {r.pipeline_id}: {r.message}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
