#!/usr/bin/env python3
"""
Publish a pipeline draft (makes it live).

Marks the test run as passed (required by the platform) then publishes the
draft version. Optionally activates the pipeline immediately after publishing.

Usage:
    python cli/publish_pipeline.py <pipeline-id>
    python cli/publish_pipeline.py <pipeline-id> --activate
    python cli/publish_pipeline.py <pipeline-id> --yes          # skip confirmation
    python cli/publish_pipeline.py pipeline-a pipeline-b --parallel 2 --yes

⚠️  Publishing makes the pipeline live. Always run a test first.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
class PublishResult:
    pipeline_id: str
    success: bool
    message: str
    remote_pipeline_id: str | None = None
    error: str | None = None


def publish_single(
    pipeline_id: str,
    activate: bool = False,
    skip_test_mark: bool = False,
    dry_run: bool = False,
) -> PublishResult:
    """Publish a single pipeline."""
    env_name = ensure_env()
    manager = get_workspace_manager()

    meta = manager.get_pipeline_workspace(pipeline_id, env_name)
    if not meta:
        return PublishResult(
            pipeline_id=pipeline_id,
            success=False,
            message=f"Pipeline workspace not found: {pipeline_id}",
            error="not_found",
        )

    remote_pipeline_id = meta.get("remote_pipeline_id")
    version_id = meta.get("version_id")

    if not remote_pipeline_id:
        return PublishResult(
            pipeline_id=pipeline_id,
            success=False,
            message="No remote pipeline linked. Run save_pipeline_draft.py first.",
            error="not_pushed",
        )

    if not version_id:
        return PublishResult(
            pipeline_id=pipeline_id,
            success=False,
            message="No draft version found. Run save_pipeline_draft.py first.",
            error="no_version",
        )

    if dry_run:
        action = "activate" if activate else "publish"
        print(f"   DRY RUN: Would {action} pipeline {remote_pipeline_id} (version {version_id})")
        return PublishResult(
            pipeline_id=pipeline_id,
            success=True,
            message="Dry run — no changes made",
            remote_pipeline_id=remote_pipeline_id,
        )

    client = get_pipeline_client()

    try:
        if not skip_test_mark:
            print("   → Marking test as passed...")
            client.mark_test_passed(remote_pipeline_id)
            print("   ✅ Test marked as passed")

        print("   → Publishing draft...")
        client.publish_draft(remote_pipeline_id, version_id)
        print("   ✅ Draft published!")

        manager.update_pipeline_workspace_meta(
            pipeline_id, {"state": "published"}, env_name
        )

        if activate:
            print("   → Activating pipeline (state=running)...")
            client.activate_pipeline(remote_pipeline_id)
            print("   ✅ Pipeline activated!")
            manager.update_pipeline_workspace_meta(
                pipeline_id, {"state": "running"}, env_name
            )

        state = "running" if activate else "published"
        return PublishResult(
            pipeline_id=pipeline_id,
            success=True,
            message=f"Published ({state})",
            remote_pipeline_id=remote_pipeline_id,
        )

    except Exception as exc:
        return PublishResult(
            pipeline_id=pipeline_id,
            success=False,
            message=str(exc),
            error="api_error",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a pipeline draft",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli/publish_pipeline.py my-pipeline
  python cli/publish_pipeline.py my-pipeline --activate
  python cli/publish_pipeline.py my-pipeline --yes
  python cli/publish_pipeline.py pipeline-a pipeline-b --parallel 2 --yes
        """,
    )
    parser.add_argument(
        "pipeline_ids",
        nargs="+",
        metavar="PIPELINE_ID",
        help="Pipeline workspace ID(s) to publish",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate the pipeline (state=running) immediately after publishing",
    )
    parser.add_argument(
        "--skip-test-mark",
        action="store_true",
        help="Skip marking test as passed (only use if already marked)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="Number of pipelines to publish in parallel",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose

    print(f"\n{'='*65}")
    print(f"  Publish Pipeline(s)")
    print(f"{'='*65}\n")

    if not args.yes and not args.dry_run:
        pipeline_list = ", ".join(args.pipeline_ids)
        action = "publish + activate" if args.activate else "publish"
        confirm = input(
            f"⚠️  This will {action} pipeline(s): {pipeline_list}\n"
            f"   Are you sure? [y/N]: "
        ).strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0

    results: list[PublishResult] = []

    if len(args.pipeline_ids) == 1 or args.parallel <= 1:
        for pid in args.pipeline_ids:
            print(f"📦 {pid}")
            result = publish_single(pid, args.activate, args.skip_test_mark, args.dry_run)
            results.append(result)
            if result.success:
                print(f"   ✅ {result.message}\n")
            else:
                print(f"   ❌ {result.message}\n")
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            future_to_pid = {
                executor.submit(
                    publish_single, pid, args.activate, args.skip_test_mark, args.dry_run
                ): pid
                for pid in args.pipeline_ids
            }
            for future in as_completed(future_to_pid):
                result = future.result()
                results.append(result)
                status = "✅" if result.success else "❌"
                print(f"   {status} {result.pipeline_id}: {result.message}")

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]

    print(f"\n{'='*65}")
    print(f"  {len(successes)} ✅  {len(failures)} ❌")
    print(f"{'='*65}")

    if successes:
        print("\nPublished:")
        for r in successes:
            print(f"  • {r.pipeline_id}  →  remote={r.remote_pipeline_id or '?'}")

    if failures:
        print("\nFailed:")
        for r in failures:
            print(f"  • {r.pipeline_id}: {r.message}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
