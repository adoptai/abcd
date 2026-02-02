#!/usr/bin/env python3
"""
Patch Tool WDL - Patch tool WDLs with user confirmation and diff display.

This script patches tool WDLs based on fix files or API changes,
showing colored diffs and requiring user confirmation.

Usage:
    # Patch tool from fix file
    python cli/patch_tool_wdl.py <tool-id> --apply-fix fix.json
    
    # Patch tool based on API change
    python cli/patch_tool_wdl.py <tool-id> --api-change --old-path "/x" --new-path "/x/"
    
    # Dry run
    python cli/patch_tool_wdl.py <tool-id> --apply-fix fix.json --dry-run
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from cli.auth import get_bearer_token_for_env

from wdl_common.api_client import AdoptAPIClient
from wdl_common.diff_utils import display_diff, generate_wdl_diff, summarize_changes
from wdl_common.interactive import (
    print_error,
    print_success,
    print_warning,
    prompt_tool_confirmation,
)
from wdl_common.rollback import RollbackManager


def fetch_tool_wdl(
    bearer_token: str,
    tool_id: str,
) -> Tuple[Optional[List[Dict]], Optional[Dict]]:
    """
    Fetch a tool's WDL.
    
    Args:
        bearer_token: Authentication token
        tool_id: Tool ID
        
    Returns:
        Tuple of (wdl, full_tool_data)
    """
    client = AdoptAPIClient(bearer_token)
    success, data, msg = client.get_action(tool_id)
    
    if not success or not data:
        print_error(f"Failed to fetch tool: {msg}")
        return None, None
    
    wdl = data.get('wdl') or data.get('widdle') or []
    return wdl, data


def update_wdl_canonical_endpoints(
    wdl: List[Dict],
    old_path: str,
    new_path: str,
) -> List[Dict]:
    """
    Update canonical_api_endpoint and url fields in WDL.
    
    Args:
        wdl: Original WDL
        old_path: Old canonical path
        new_path: New canonical path
        
    Returns:
        Updated WDL
    """
    import copy
    new_wdl = copy.deepcopy(wdl)
    
    for op in new_wdl:
        if op.get('operation') != 'REST':
            continue
        
        # Update canonical_api_endpoint
        canonical = op.get('canonical_api_endpoint', '')
        if canonical == old_path:
            op['canonical_api_endpoint'] = new_path
        
        # Update url field - convert to workflow_arguments format
        url = op.get('url', '')
        if url:
            # Convert old path to new path in URL
            # The URL uses {workflow_arguments.param} format
            old_url_pattern = old_path.replace('{', '{workflow_arguments.')
            new_url_pattern = new_path.replace('{', '{workflow_arguments.')
            
            # Handle case where URL might already have workflow_arguments
            if old_url_pattern in url:
                op['url'] = url.replace(old_url_pattern, new_url_pattern)
            elif old_path in url:
                # Try direct replacement
                op['url'] = url.replace(old_path, new_path)
    
    return new_wdl


def patch_tool_with_wdl(
    bearer_token: str,
    tool_id: str,
    new_wdl: List[Dict],
    description: Optional[str] = None,
) -> Tuple[bool, str, Optional[int]]:
    """
    Patch a tool with new WDL (publish, save draft, approve).
    
    Args:
        bearer_token: Authentication token
        tool_id: Tool ID
        new_wdl: New WDL to publish
        description: Optional change description
        
    Returns:
        Tuple of (success, message, version_number)
    """
    client = AdoptAPIClient(bearer_token)
    
    # Get current action to find draft_id
    success, data, msg = client.get_action(tool_id)
    if not success or not data:
        return False, f"Failed to get tool: {msg}", None
    
    draft_id = data.get('draft_id') or data.get('id')
    
    # Publish WDL
    success, msg = client.publish_wdl(tool_id, new_wdl, draft_id)
    if not success:
        return False, f"Failed to publish WDL: {msg}", None
    
    # Save draft
    success, version, msg = client.save_draft(tool_id, draft_id, description)
    if not success:
        return False, f"Failed to save draft: {msg}", None
    
    # Approve version
    if version:
        success, msg = client.approve_version(tool_id, str(version), description)
        if not success:
            return False, f"Failed to approve: {msg}", version
    
    return True, "Tool patched successfully", version


def apply_fix_file(
    bearer_token: str,
    tool_id: str,
    fix_file: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    rollback_manager: Optional[RollbackManager] = None,
) -> Tuple[bool, str]:
    """
    Apply a fix file to a tool.
    
    Args:
        bearer_token: Authentication token
        tool_id: Tool ID
        fix_file: Path to fix JSON file
        dry_run: If True, don't make changes
        auto_approve: If True, skip confirmation
        rollback_manager: Optional rollback manager
        
    Returns:
        Tuple of (success, message)
    """
    # Load fix file
    with open(fix_file, 'r') as f:
        fix_data = json.load(f)
    
    new_wdl = fix_data.get('new_wdl')
    if not new_wdl:
        return False, "Fix file has no 'new_wdl' field"
    
    tool_title = fix_data.get('tool_name', fix_data.get('tool_title', 'Unknown'))
    changes_summary = fix_data.get('changes_summary', 'WDL update')
    
    # Fetch current WDL
    current_wdl, tool_data = fetch_tool_wdl(bearer_token, tool_id)
    if current_wdl is None:
        return False, "Could not fetch current WDL"
    
    if tool_data:
        tool_title = tool_data.get('title') or tool_data.get('name') or tool_title
    
    # Show diff
    display_diff(current_wdl, new_wdl, title=f"Changes to {tool_title}")
    
    # Summarize changes
    summary = summarize_changes(current_wdl, new_wdl)
    print(f"\n📋 Summary: {summary['operations_changed']} operations changed, "
          f"{summary['operations_added']} added, {summary['operations_removed']} removed")
    
    if dry_run:
        print("\n[DRY RUN] Would apply these changes")
        return True, "Dry run - no changes made"
    
    # Confirm
    if not auto_approve:
        action = prompt_tool_confirmation(
            tool_id=tool_id,
            tool_title=tool_title,
            changes_summary=changes_summary,
        )
        
        if action in ('skip', 'quit'):
            return False, "Skipped by user"
    
    # Record for rollback
    if rollback_manager:
        rollback_manager.add_tool_change(tool_id, tool_title, current_wdl, new_wdl)
    
    # Apply patch
    success, message, version = patch_tool_with_wdl(
        bearer_token=bearer_token,
        tool_id=tool_id,
        new_wdl=new_wdl,
        description=changes_summary,
    )
    
    if success:
        print_success(f"Patched {tool_title} (version {version})")
    else:
        print_error(f"Failed to patch: {message}")
    
    return success, message


def apply_api_change(
    bearer_token: str,
    tool_id: str,
    old_path: str,
    new_path: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    rollback_manager: Optional[RollbackManager] = None,
) -> Tuple[bool, str]:
    """
    Apply an API path change to a tool's WDL.
    
    Args:
        bearer_token: Authentication token
        tool_id: Tool ID
        old_path: Old canonical path
        new_path: New canonical path
        dry_run: If True, don't make changes
        auto_approve: If True, skip confirmation
        rollback_manager: Optional rollback manager
        
    Returns:
        Tuple of (success, message)
    """
    # Fetch current WDL
    current_wdl, tool_data = fetch_tool_wdl(bearer_token, tool_id)
    if current_wdl is None:
        return False, "Could not fetch current WDL"
    
    tool_title = tool_data.get('title') or tool_data.get('name') or 'Unknown'
    
    # Update WDL
    new_wdl = update_wdl_canonical_endpoints(current_wdl, old_path, new_path)
    
    # Check if any changes were made
    if current_wdl == new_wdl:
        print_warning(f"No changes needed for {tool_title}")
        return True, "No changes needed"
    
    # Show diff
    display_diff(current_wdl, new_wdl, title=f"Changes to {tool_title}")
    
    if dry_run:
        print("\n[DRY RUN] Would apply these changes")
        return True, "Dry run - no changes made"
    
    # Confirm
    if not auto_approve:
        action = prompt_tool_confirmation(
            tool_id=tool_id,
            tool_title=tool_title,
            changes_summary=f"Update path: {old_path} → {new_path}",
        )
        
        if action in ('skip', 'quit'):
            return False, "Skipped by user"
    
    # Record for rollback
    if rollback_manager:
        rollback_manager.add_tool_change(tool_id, tool_title, current_wdl, new_wdl)
    
    # Apply patch
    success, message, version = patch_tool_with_wdl(
        bearer_token=bearer_token,
        tool_id=tool_id,
        new_wdl=new_wdl,
        description=f"Update path: {old_path} → {new_path}",
    )
    
    if success:
        print_success(f"Patched {tool_title} (version {version})")
    else:
        print_error(f"Failed to patch: {message}")
    
    return success, message


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Patch tool WDLs with confirmation and diff display",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Apply fix file
  python cli/patch_tool_wdl.py abc123 --apply-fix fix.json
  
  # Apply API path change
  python cli/patch_tool_wdl.py abc123 --api-change --old-path "/org/{id}/apps" --new-path "/org/{id}/apps/"
  
  # Dry run
  python cli/patch_tool_wdl.py abc123 --apply-fix fix.json --dry-run
""",
    )
    
    parser.add_argument("tool_id", help="Tool ID to patch")
    parser.add_argument("--apply-fix", help="Path to fix JSON file")
    parser.add_argument("--api-change", action="store_true", help="Apply API path change")
    parser.add_argument("--old-path", help="Old canonical path (with --api-change)")
    parser.add_argument("--new-path", help="New canonical path (with --api-change)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--auto-approve", action="store_true", help="Skip confirmations")
    
    args = parser.parse_args()
    
    if not args.apply_fix and not args.api_change:
        parser.print_help()
        return 1
    
    if args.api_change and (not args.old_path or not args.new_path):
        print("❌ --api-change requires --old-path and --new-path")
        return 1
    
    print("\n" + "=" * 70)
    print("🔨 PATCH TOOL WDL")
    print("=" * 70)
    
    try:
        bearer_token = get_bearer_token_for_env()  # Uses active environment
        
        rollback_manager = RollbackManager()
        rollback_manager.start_session()
        
        if args.apply_fix:
            success, message = apply_fix_file(
                bearer_token=bearer_token,
                tool_id=args.tool_id,
                fix_file=args.apply_fix,
                dry_run=args.dry_run,
                auto_approve=args.auto_approve,
                rollback_manager=rollback_manager,
            )
        else:
            success, message = apply_api_change(
                bearer_token=bearer_token,
                tool_id=args.tool_id,
                old_path=args.old_path,
                new_path=args.new_path,
                dry_run=args.dry_run,
                auto_approve=args.auto_approve,
                rollback_manager=rollback_manager,
            )
        
        if rollback_manager.get_entry_count() > 0:
            print(f"\n💾 Rollback file: {rollback_manager.get_session_file()}")
        
        return 0 if success else 1
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









