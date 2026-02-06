#!/usr/bin/env python3
"""
Deployment Rules CLI - Manage action deployment rules and tool mode.

Commands:
    deployment_rules.py <action> --show           Show current deployment rules
    deployment_rules.py <action> --enable-tool-mode   Enable tool mode
    deployment_rules.py <action> --disable-tool-mode  Disable tool mode
    deployment_rules.py <action> --visible        Make action visible in list
    deployment_rules.py <action> --hidden         Hide action from list
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def get_action_id(workflow_id: str) -> str | None:
    """
    Get remote action ID from workspace metadata.

    Args:
        workflow_id: Local workflow/action identifier

    Returns:
        Remote action ID or None
    """
    manager = get_workspace_manager()
    action_info = manager.find_action(workflow_id)

    if not action_info:
        # Try legacy workspace
        from cli.wdl_common.workspace_manager import STANDALONE_ACTIONS_DIR
        legacy_path = STANDALONE_ACTIONS_DIR / workflow_id
        if legacy_path.exists():
            meta_path = legacy_path / "metadata.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    return meta.get("action_id")
                except Exception:
                    pass
        return None

    metadata = action_info.get("metadata", {})
    return metadata.get("action_id")


def cmd_show(args: argparse.Namespace) -> int:
    """Show deployment rules."""
    action_id = get_action_id(args.action)

    if not action_id:
        print(f"❌ No remote action ID found for: {args.action}")
        print("   The action may not be published yet.")
        return 1

    client = get_api_client_for_env()  # Uses active environment
    success, rules, message = client.get_deployment_rules(action_id)

    if not success:
        print(f"❌ {message}")
        return 1

    print("\n" + "=" * 60)
    print(f"📋 DEPLOYMENT RULES: {args.action}")
    print("=" * 60)
    print(f"  Action ID:        {rules.get('action_id')}")
    print(f"  Tool Mode:        {'ENABLED ✓' if rules.get('is_tool_mode') else 'Disabled'}")
    print(f"  Visible in List:  {'Yes' if rules.get('is_visible_in_list') else 'No (Hidden)'}")
    print(f"  Org ID:           {rules.get('org_id')}")
    print(f"  Updated:          {rules.get('updated_at', '-')[:19]}")

    targeting_rules = rules.get("rules", [])
    if targeting_rules:
        print(f"\n  Targeting Rules ({len(targeting_rules)}):")
        for rule in targeting_rules:
            print(f"    - {rule.get('field')} {rule.get('operator')} {rule.get('values')}")
    else:
        print(f"\n  Targeting Rules: None (available to all users)")

    print("=" * 60)
    return 0


def cmd_enable_tool_mode(args: argparse.Namespace) -> int:
    """Enable tool mode."""
    action_id = get_action_id(args.action)

    if not action_id:
        print(f"❌ No remote action ID found for: {args.action}")
        return 1

    client = get_api_client_for_env()  # Uses active environment

    # Get current rules first
    success, current_rules, _ = client.get_deployment_rules(action_id)
    is_visible = current_rules.get("is_visible_in_list", True) if success else True
    rules = current_rules.get("rules", []) if success else []

    # Update with tool mode enabled
    success, result, message = client.set_deployment_rules(
        action_id=action_id,
        is_tool_mode=True,
        is_visible_in_list=is_visible,
        rules=rules,
    )

    if success:
        print(f"✅ Tool Mode ENABLED for: {args.action}")
        print("   The action will now bypass orchestrator for deterministic execution.")

        # Update local metadata
        update_local_metadata(args.action, is_tool_mode=True)
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_disable_tool_mode(args: argparse.Namespace) -> int:
    """Disable tool mode."""
    action_id = get_action_id(args.action)

    if not action_id:
        print(f"❌ No remote action ID found for: {args.action}")
        return 1

    client = get_api_client_for_env()  # Uses active environment

    # Get current rules first
    success, current_rules, _ = client.get_deployment_rules(action_id)
    is_visible = current_rules.get("is_visible_in_list", True) if success else True
    rules = current_rules.get("rules", []) if success else []

    # Update with tool mode disabled
    success, result, message = client.set_deployment_rules(
        action_id=action_id,
        is_tool_mode=False,
        is_visible_in_list=is_visible,
        rules=rules,
    )

    if success:
        print(f"✅ Tool Mode DISABLED for: {args.action}")
        print("   The action will use normal orchestrator flow.")

        # Update local metadata
        update_local_metadata(args.action, is_tool_mode=False)
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_set_visible(args: argparse.Namespace) -> int:
    """Set action visibility."""
    action_id = get_action_id(args.action)

    if not action_id:
        print(f"❌ No remote action ID found for: {args.action}")
        return 1

    client = get_api_client_for_env()  # Uses active environment

    # Get current rules first
    success, current_rules, _ = client.get_deployment_rules(action_id)
    is_tool_mode = current_rules.get("is_tool_mode", False) if success else False
    rules = current_rules.get("rules", []) if success else []

    # Update visibility
    success, result, message = client.set_deployment_rules(
        action_id=action_id,
        is_tool_mode=is_tool_mode,
        is_visible_in_list=args.visible,
        rules=rules,
    )

    if success:
        status = "VISIBLE" if args.visible else "HIDDEN"
        print(f"✅ Action is now {status}: {args.action}")

        # Update local metadata
        update_local_metadata(args.action, is_visible_in_list=args.visible)
        return 0
    else:
        print(f"❌ {message}")
        return 1


def update_local_metadata(
    workflow_id: str,
    is_tool_mode: bool | None = None,
    is_visible_in_list: bool | None = None,
) -> None:
    """Update local metadata.json with deployment rules."""
    manager = get_workspace_manager()
    action_info = manager.find_action(workflow_id)

    if not action_info:
        return

    meta_path = action_info["path"] / "metadata.json"
    if not meta_path.exists():
        return

    try:
        metadata = json.loads(meta_path.read_text())

        if is_tool_mode is not None:
            metadata["is_tool_mode"] = is_tool_mode
        if is_visible_in_list is not None:
            metadata["is_visible_in_list"] = is_visible_in_list

        from datetime import datetime
        metadata["deployment_rules_synced_at"] = datetime.now().isoformat()

        meta_path.write_text(json.dumps(metadata, indent=2))
    except Exception:
        pass


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage action deployment rules and tool mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s my-action --show                Show deployment rules
  %(prog)s my-action --enable-tool-mode    Enable tool mode
  %(prog)s my-action --disable-tool-mode   Disable tool mode
  %(prog)s my-action --visible             Make visible in list
  %(prog)s my-action --hidden              Hide from list
        """,
    )

    parser.add_argument("action", help="Action/workflow ID")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="Show deployment rules")
    group.add_argument("--enable-tool-mode", action="store_true", help="Enable tool mode")
    group.add_argument("--disable-tool-mode", action="store_true", help="Disable tool mode")
    group.add_argument("--visible", action="store_true", help="Make visible in list")
    group.add_argument("--hidden", action="store_true", help="Hide from list")

    args = parser.parse_args()

    if args.show:
        return cmd_show(args)
    elif args.enable_tool_mode:
        return cmd_enable_tool_mode(args)
    elif args.disable_tool_mode:
        return cmd_disable_tool_mode(args)
    elif args.visible:
        args.visible = True
        return cmd_set_visible(args)
    elif args.hidden:
        args.visible = False
        return cmd_set_visible(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())


