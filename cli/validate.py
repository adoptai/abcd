#!/usr/bin/env python3
"""
Validate Command - Comprehensive WDL validation.

Validates:
- JSON syntax
- WDL structure
- required_inputs format
- Tool titles for orchestrator use
- Operation references
- OUTPUT_TEXT references

Usage:
    python cli/validate.py my-workflow
    python cli/validate.py my-workflow --auto-fix
    python cli/validate.py my-workflow --orchestrator
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.workspace_manager import WorkspaceManager
from cli.wdl_common.validator import validate_wdl_file, WDLValidator


def find_workspace(workflow_id: str) -> Path:
    """Find workspace by workflow_id, auto-detecting agent vs standalone."""
    manager = WorkspaceManager(use_agents=True)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"]
    
    manager = WorkspaceManager(use_agents=False)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"]
    
    raise ValueError(f"Workspace not found: {workflow_id}")


def validate_workflow(
    workflow_id: str,
    auto_fix: bool = False,
    orchestrator_context: bool = False,
) -> bool:
    """
    Validate WDL workflow.
    
    Args:
        workflow_id: Workflow ID
        auto_fix: Auto-fix issues where possible
        orchestrator_context: Validate for orchestrator use
        
    Returns:
        True if valid (no errors)
    """
    print("\n" + "=" * 80)
    print("🔍 VALIDATE WDL")
    print("=" * 80)
    print(f"Workflow: {workflow_id}")
    if orchestrator_context:
        print("Context: Orchestrator (stricter title validation)")
    
    # Find workspace
    try:
        workspace = find_workspace(workflow_id)
    except ValueError as e:
        print(f"\n❌ {e}")
        return False
    
    print(f"📁 Workspace: {workspace}")
    
    # Check for WDL file
    wdl_path = workspace / "widdle.json"
    if not wdl_path.exists():
        print("\n❌ widdle.json not found")
        return False
    
    # Step 1: JSON syntax validation
    print("\n📋 Step 1: Validating JSON syntax...")
    try:
        wdl = json.loads(wdl_path.read_text())
        print("   ✅ JSON syntax is valid")
    except json.JSONDecodeError as e:
        print(f"\n❌ Invalid JSON syntax at line {e.lineno}, column {e.colno}")
        print(f"   Error: {e.msg}")
        print("\n💡 Common JSON errors:")
        print("   - Missing comma between elements")
        print("   - Trailing comma (not allowed in JSON)")
        print("   - Unescaped quotes in strings")
        print("   - Mismatched brackets [ ] or braces { }")
        return False
    
    # Step 2: WDL structure validation
    print("\n📋 Step 2: Validating WDL structure...")
    
    context = "orchestrator" if orchestrator_context else "action"
    result = validate_wdl_file(wdl_path, context=context, auto_fix=auto_fix)
    
    # Show results
    print(f"\n{result}")
    
    # Additional info
    if result.is_valid:
        # Count operations
        op_count = len([op for op in wdl if isinstance(op, dict) and op.get("operation")])
        print(f"\n📊 Summary:")
        print(f"   Total operations: {op_count}")
        
        # Show operation types
        op_types = {}
        for op in wdl:
            if isinstance(op, dict) and op.get("operation"):
                op_type = op["operation"]
                op_types[op_type] = op_types.get(op_type, 0) + 1
        
        if op_types:
            print("   Operation types:")
            for op_type, count in sorted(op_types.items()):
                print(f"      - {op_type}: {count}")
        
        print("\n" + "=" * 80)
        print("✅ VALIDATION PASSED")
        print("=" * 80)
    else:
        if not auto_fix:
            print("\n💡 Tip: Run with --auto-fix to automatically fix some issues")
        
        print("\n" + "=" * 80)
        print("❌ VALIDATION FAILED")
        print("=" * 80)
    
    return result.is_valid


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate WDL workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli/validate.py my-workflow
  python cli/validate.py my-workflow --auto-fix
  python cli/validate.py my-workflow --orchestrator

Validations performed:
  1. JSON syntax
  2. Required operation fields (id, operation)
  3. Operation-specific fields (url for REST, etc.)
  4. required_inputs format (must be list, not dict)
  5. Tool titles for orchestrator (only letters, numbers, - and _)
  6. Duplicate operation IDs
  7. Operation references
        """,
    )
    
    parser.add_argument("workflow_id", help="Workflow ID")
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Auto-fix issues where possible",
    )
    parser.add_argument(
        "--orchestrator", "-o",
        action="store_true",
        help="Validate for orchestrator use (stricter title validation)",
    )
    
    args = parser.parse_args()
    
    success = validate_workflow(
        workflow_id=args.workflow_id,
        auto_fix=args.auto_fix,
        orchestrator_context=args.orchestrator,
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()



