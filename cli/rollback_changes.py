#!/usr/bin/env python3
"""
Rollback Changes - Rollback API/WDL changes using saved rollback files.

This script restores APIs and tools to their original state before
fixes were applied.

IMPORTANT: This script integrates with the hierarchical workspace manager.
All operations use the active environment's credentials.

Usage:
    # Rollback from file
    python cli/rollback_changes.py --file rollback_20260119.json

    # Show rollback contents
    python cli/rollback_changes.py --file rollback.json --show

    # List available rollback files
    python cli/rollback_changes.py --list
"""

import argparse
import os
import sys
from pathlib import Path

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from wdl_common.api_client import AdoptAPIClient
from wdl_common.context import ensure_env, get_client
from wdl_common.interactive import prompt_confirmation
from wdl_common.rollback import display_rollback_contents, list_rollback_files, load_rollback_state


def update_api_path(
    bearer_token: str,
    api_id: str,
    new_path: str,
) -> tuple[bool, str]:
    """Update an API's path."""
    api_endpoint = os.getenv("ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai")

    url = f"{api_endpoint}/v1/documented-apis/{api_id}"
    headers = {"Authorization": f"Bearer {bearer_token}", "Content-Type": "application/json"}

    try:
        response = requests.patch(url, headers=headers, json={"path": new_path}, timeout=30)

        if response.status_code not in (200, 201, 204):
            return False, f"Failed: {response.status_code}"

        return True, "Path updated"

    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def restore_tool_wdl(
    bearer_token: str,
    tool_id: str,
    original_wdl: list[dict],
) -> tuple[bool, str]:
    """Restore a tool's WDL to original state."""
    client = AdoptAPIClient(bearer_token)

    # Get current action
    success, data, msg = client.get_action(tool_id)
    if not success or not data:
        return False, f"Failed to get tool: {msg}"

    draft_id = data.get("draft_id") or data.get("id")

    # Publish original WDL
    success, msg = client.publish_wdl(tool_id, original_wdl, draft_id)
    if not success:
        return False, f"Failed to publish WDL: {msg}"

    # Save draft
    success, version, msg = client.save_draft(tool_id, draft_id, "Rollback to original WDL")
    if not success:
        return False, f"Failed to save draft: {msg}"

    # Approve version
    if version:
        success, msg = client.approve_version(tool_id, str(version), "Rollback")
        if not success:
            return False, f"Failed to approve: {msg}"

    return True, f"Restored (version {version})"


def rollback_from_file(
    bearer_token: str,
    rollback_file: str,
    auto_approve: bool = False,
    only_apis: bool = False,
    only_tools: bool = False,
) -> dict[str, int]:
    """
    Rollback changes from a rollback file.

    Args:
        bearer_token: Authentication token
        rollback_file: Path to rollback file
        auto_approve: If True, skip confirmations
        only_apis: Only rollback APIs
        only_tools: Only rollback tools

    Returns:
        Results dictionary with counts
    """
    data = load_rollback_state(rollback_file)

    print(f"\n📋 Rollback file: {rollback_file}")
    print(f"   Created: {data.get('created_at', 'unknown')}")

    results = {
        "apis_rolled_back": 0,
        "apis_failed": 0,
        "tools_rolled_back": 0,
        "tools_failed": 0,
    }

    # Handle both old format (apis/tools) and new format (entries)
    apis = data.get("apis", [])
    tools = data.get("tools", [])
    entries = data.get("entries", [])

    # Convert entries to old format if needed
    if entries and not apis and not tools:
        for entry in entries:
            if entry.get("entry_type") == "api":
                apis.append(
                    {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "original_path": entry.get("original_value"),
                        "new_path": entry.get("new_value"),
                    }
                )
            elif entry.get("entry_type") == "tool":
                tools.append(
                    {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "original_wdl": entry.get("original_value"),
                    }
                )

    print(f"   APIs to rollback: {len(apis)}")
    print(f"   Tools to rollback: {len(tools)}")

    if not auto_approve:
        if not prompt_confirmation("\n❓ Proceed with rollback?"):
            print("⚠️  Rollback cancelled")
            return results

    # Rollback APIs
    if apis and not only_tools:
        print("\n🔄 Rolling back APIs...")
        for api in apis:
            api_id = api.get("id")
            title = api.get("title", api_id)
            original_path = api.get("original_path")

            print(f"   • {title}...", end=" ")

            success, message = update_api_path(bearer_token, api_id, original_path)

            if success:
                print("✅")
                results["apis_rolled_back"] += 1
            else:
                print(f"❌ {message}")
                results["apis_failed"] += 1

    # Rollback tools
    if tools and not only_apis:
        print("\n🔄 Rolling back tools...")
        for tool in tools:
            tool_id = tool.get("id")
            title = tool.get("title", tool_id)
            original_wdl = tool.get("original_wdl")

            if not original_wdl:
                print(f"   • {title}: ⚠️  No original WDL stored")
                results["tools_failed"] += 1
                continue

            print(f"   • {title}...", end=" ")

            success, message = restore_tool_wdl(bearer_token, tool_id, original_wdl)

            if success:
                print("✅")
                results["tools_rolled_back"] += 1
            else:
                print(f"❌ {message}")
                results["tools_failed"] += 1

    return results


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Rollback API/WDL changes from rollback files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available rollback files
  python cli/rollback_changes.py --list

  # Show contents of a rollback file
  python cli/rollback_changes.py --file diagnostics/rollback_20260119.json --show

  # Rollback all changes
  python cli/rollback_changes.py --file diagnostics/rollback_20260119.json

  # Rollback only APIs
  python cli/rollback_changes.py --file rollback.json --only-apis
""",
    )

    parser.add_argument("--file", "-f", help="Rollback file to use")
    parser.add_argument("--list", "-l", action="store_true", help="List available rollback files")
    parser.add_argument("--show", "-s", action="store_true", help="Show rollback file contents")
    parser.add_argument("--only-apis", action="store_true", help="Only rollback APIs")
    parser.add_argument("--only-tools", action="store_true", help="Only rollback tools")
    parser.add_argument("--auto-approve", "-y", action="store_true", help="Skip confirmations")

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("↩️  ROLLBACK CHANGES")
    print("=" * 70)

    if args.list:
        files = list_rollback_files()

        if not files:
            print("\n⚠️  No rollback files found in diagnostics/")
            return 0

        print(f"\n📋 Available rollback files ({len(files)}):")
        for f in files:
            # Get file stats
            stat = f.stat()
            size = stat.st_size
            mtime = stat.st_mtime
            from datetime import datetime

            mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"   • {f.name} ({size} bytes, {mtime_str})")

        return 0

    if not args.file:
        parser.print_help()
        return 1

    file_path = Path(args.file)
    if not file_path.exists():
        # Try in diagnostics directory
        repo_root = Path(__file__).parent.parent
        alt_path = repo_root / "diagnostics" / args.file
        if alt_path.exists():
            file_path = alt_path
        else:
            print(f"❌ File not found: {args.file}")
            return 1

    if args.show:
        display_rollback_contents(str(file_path))
        return 0

    try:
        # Ensure environment is loaded and show which one we're using
        env_name = ensure_env()
        print(f"📁 Environment: {env_name}")

        # Get client (uses active environment credentials)
        client = get_client()
        bearer_token = client.bearer_token

        results = rollback_from_file(
            bearer_token=bearer_token,
            rollback_file=str(file_path),
            auto_approve=args.auto_approve,
            only_apis=args.only_apis,
            only_tools=args.only_tools,
        )

        print("\n" + "=" * 70)
        print("📊 ROLLBACK RESULTS")
        print("=" * 70)
        print(f"APIs rolled back: {results['apis_rolled_back']}")
        print(f"APIs failed: {results['apis_failed']}")
        print(f"Tools rolled back: {results['tools_rolled_back']}")
        print(f"Tools failed: {results['tools_failed']}")
        print("=" * 70)

        total_failed = results["apis_failed"] + results["tools_failed"]
        return 0 if total_failed == 0 else 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
