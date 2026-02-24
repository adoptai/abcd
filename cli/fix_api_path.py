#!/usr/bin/env python3
# mypy: ignore-errors
"""
Fix API Path - Fix API paths (trailing slashes, etc.) with user confirmation.

This script fixes API paths based on diagnostic results, showing diffs and
requiring user confirmation before making changes.

IMPORTANT: This script integrates with the hierarchical workspace manager.
All operations use the active environment's credentials and cache.

Usage:
    # Fix single API
    python cli/fix_api_path.py <api-id> --trailing-slash add

    # Interactive mode for all issues
    python cli/fix_api_path.py --scan --fix --interactive

    # Dry run
    python cli/fix_api_path.py --scan --fix --dry-run
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from wdl_common.context import ensure_env, get_client
from wdl_common.data_cache import DataCache
from wdl_common.diff_utils import format_path_change
from wdl_common.interactive import (
    print_error,
    print_success,
    prompt_api_confirmation,
)
from wdl_common.rollback import RollbackManager


def update_api_path(
    bearer_token: str,
    api_id: str,
    new_path: str,
    api_endpoint: str | None = None,
) -> tuple[bool, str]:
    """
    Update an API's canonical path via the backend documented-apis endpoint.

    Args:
        bearer_token: The authentication bearer token
        api_id: The API ID to update
        new_path: The new canonical path
        api_endpoint: The API endpoint

    Returns:
        Tuple of (success, message)
    """
    api_endpoint = api_endpoint or os.getenv("ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai")

    url = f"{api_endpoint}/v1/documented-apis/{api_id}"
    headers = {"Authorization": f"Bearer {bearer_token}", "Content-Type": "application/json"}

    payload = {"path": new_path}

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=30)

        if response.status_code not in (200, 201, 204):
            return False, f"Failed: {response.status_code} - {response.text}"

        return True, "API path updated successfully"

    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def fetch_api_from_remote(bearer_token: str, api_id: str) -> dict | None:
    """
    Fetch API details directly from the remote API.

    Args:
        bearer_token: Authentication token
        api_id: API ID to fetch

    Returns:
        API dict or None if not found
    """
    api_endpoint = os.getenv("ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai")
    url = f"{api_endpoint}/v1/documented-apis/{api_id}"
    headers = {"Authorization": f"Bearer {bearer_token}", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.exceptions.RequestException:
        return None


def fix_single_api(
    bearer_token: str,
    api_id: str,
    trailing_slash_action: str,
    dry_run: bool = False,
    rollback_manager: RollbackManager | None = None,
    new_path_override: str | None = None,
) -> tuple[bool, str]:
    """
    Fix a single API's path.

    Args:
        bearer_token: Authentication token
        api_id: API ID to fix
        trailing_slash_action: 'add' or 'remove'
        dry_run: If True, don't make changes
        rollback_manager: Optional rollback manager
        new_path_override: Directly specify the new path (bypasses cache lookup)

    Returns:
        Tuple of (success, message)
    """
    # If new_path_override is provided, update directly without cache lookup
    if new_path_override:
        print(f"\n📝 API: {api_id}")
        print(f"   New path: {new_path_override}")

        if dry_run:
            print("   [DRY RUN] Would update path")
            return True, "Dry run - no changes made"

        success, message = update_api_path(bearer_token, api_id, new_path_override)

        if success:
            print_success(f"Updated API path to: {new_path_override}")
        else:
            print_error(f"Failed: {message}")

        return success, message

    # Try cache first, then fall back to remote fetch
    cache = DataCache()
    api = cache.get_api_by_id(api_id)

    if not api:
        print("   API not in cache, fetching from remote...")
        api = fetch_api_from_remote(bearer_token, api_id)

    if not api:
        return (
            False,
            f"API not found: {api_id}. Run 'python cli/diagnose_and_fix.py --scan' to populate cache, or use --path to specify the new path directly.",
        )

    current_path = api.get("path") or api.get("canonical_api_endpoint", "")
    api_title = api.get("title") or api.get("name") or "Untitled"

    if not current_path:
        return False, "API has no path. Use --path to specify the new path directly."

    # Calculate new path
    if trailing_slash_action == "add":
        if current_path.endswith("/"):
            return False, "API already has trailing slash"
        new_path = current_path + "/"
    elif trailing_slash_action == "remove":
        if not current_path.endswith("/") or current_path == "/":
            return False, "API has no trailing slash to remove"
        new_path = current_path.rstrip("/")
    else:
        return False, f"Invalid action: {trailing_slash_action}"

    print(f"\n📝 API: {api_title}")
    print(f"   {format_path_change(current_path, new_path)}")

    if dry_run:
        print("   [DRY RUN] Would update path")
        return True, "Dry run - no changes made"

    # Record for rollback
    if rollback_manager:
        rollback_manager.add_api_change(api_id, api_title, current_path, new_path)

    # Update the path
    success, message = update_api_path(bearer_token, api_id, new_path)

    if success:
        print_success(f"Updated: {api_title}")
    else:
        print_error(f"Failed: {message}")

    return success, message


def fix_apis_from_diagnostics(
    bearer_token: str,
    diagnostics_file: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    interactive: bool = True,
) -> dict[str, Any]:
    """
    Fix APIs based on a diagnostics report.

    Args:
        bearer_token: Authentication token
        diagnostics_file: Path to diagnostics report JSON
        dry_run: If True, don't make changes
        auto_approve: If True, skip confirmations
        interactive: If True, prompt for each change

    Returns:
        Results summary
    """
    with open(diagnostics_file) as f:
        report = json.load(f)

    issues = report.get("issues", [])

    if not issues:
        print("No issues to fix")
        return {"fixed": 0, "skipped": 0, "failed": 0}

    rollback_manager = RollbackManager()
    rollback_manager.start_session()

    results = {
        "fixed": 0,
        "skipped": 0,
        "failed": 0,
        "rollback_file": str(rollback_manager.get_session_file()),
    }

    apply_all = auto_approve

    for _i, issue in enumerate(issues):
        if issue.get("issue_type") != "trailing_slash_mismatch":
            continue

        api_id = issue.get("api_id")
        api_title = issue.get("api_title", "Unknown")
        details = issue.get("details", {})
        current_path = details.get("current_canonical_path", "")
        expected_path = details.get("expected_path", "")
        match_score = details.get("match_score", 0)
        matched_url = details.get("matched_log_url", "")

        if not api_id or not expected_path:
            results["skipped"] += 1
            continue

        # Prompt for confirmation
        if interactive and not apply_all:
            action = prompt_api_confirmation(
                api_id=api_id,
                api_title=api_title,
                old_path=current_path,
                new_path=expected_path,
                match_score=match_score,
                matched_url=matched_url,
            )

            if action == "quit":
                print("\n⚠️  Quitting (progress saved)")
                break
            elif action == "skip":
                results["skipped"] += 1
                continue
            elif action == "apply_all":
                apply_all = True

        if dry_run:
            print(f"   [DRY RUN] Would update {api_title}")
            results["fixed"] += 1
            continue

        # Record for rollback
        rollback_manager.add_api_change(api_id, api_title, current_path, expected_path)

        # Apply fix
        success, message = update_api_path(bearer_token, api_id, expected_path)

        if success:
            print_success(f"Fixed: {api_title}")
            results["fixed"] += 1
        else:
            print_error(f"Failed: {api_title} - {message}")
            results["failed"] += 1

    return results


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fix API paths with user confirmation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fix single API - add trailing slash
  python cli/fix_api_path.py abc123 --trailing-slash add

  # Fix single API - set path directly (no cache needed)
  python cli/fix_api_path.py abc123 --path "/api/v1/resource/{id}/"

  # Fix from diagnostics report
  python cli/fix_api_path.py --from-diagnostics diagnostics/report.json

  # Dry run
  python cli/fix_api_path.py --from-diagnostics diagnostics/report.json --dry-run

  # Auto-approve all
  python cli/fix_api_path.py --from-diagnostics diagnostics/report.json --auto-approve
""",
    )

    parser.add_argument("api_id", nargs="?", help="API ID to fix")
    parser.add_argument(
        "--trailing-slash", choices=["add", "remove"], help="Add or remove trailing slash"
    )
    parser.add_argument("--path", help="Set the API path directly (no cache lookup needed)")
    parser.add_argument("--from-diagnostics", help="Fix from diagnostics report JSON")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--auto-approve", action="store_true", help="Skip confirmations")
    parser.add_argument(
        "--interactive", "-i", action="store_true", default=True, help="Interactive mode (default)"
    )

    args = parser.parse_args()

    if not args.api_id and not args.from_diagnostics:
        parser.print_help()
        return 1

    print("\n" + "=" * 70)
    print("🔧 FIX API PATHS")
    print("=" * 70)

    try:
        # Ensure environment is loaded and show which one we're using
        env_name = ensure_env()
        print(f"📁 Environment: {env_name}")

        # Get client (uses active environment credentials)
        client = get_client()
        bearer_token = client.bearer_token

        if args.api_id and args.path:
            # Fix single API with direct path (no cache needed)
            success, message = fix_single_api(
                bearer_token=bearer_token,
                api_id=args.api_id,
                trailing_slash_action="add",  # Dummy, not used when path is provided
                dry_run=args.dry_run,
                new_path_override=args.path,
            )
            return 0 if success else 1

        elif args.api_id and args.trailing_slash:
            # Fix single API with trailing slash modification
            success, message = fix_single_api(
                bearer_token=bearer_token,
                api_id=args.api_id,
                trailing_slash_action=args.trailing_slash,
                dry_run=args.dry_run,
            )
            return 0 if success else 1

        elif args.from_diagnostics:
            # Fix from diagnostics
            results = fix_apis_from_diagnostics(
                bearer_token=bearer_token,
                diagnostics_file=args.from_diagnostics,
                dry_run=args.dry_run,
                auto_approve=args.auto_approve,
                interactive=args.interactive,
            )

            print("\n" + "=" * 70)
            print("📊 RESULTS")
            print("=" * 70)
            print(f"Fixed: {results['fixed']}")
            print(f"Skipped: {results['skipped']}")
            print(f"Failed: {results['failed']}")
            if results.get("rollback_file"):
                print(f"\nRollback file: {results['rollback_file']}")
            print("=" * 70)

            return 0 if results["failed"] == 0 else 1

        else:
            print("⚠️  Please specify either API ID with --trailing-slash or --from-diagnostics")
            return 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
