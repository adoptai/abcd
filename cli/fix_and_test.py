#!/usr/bin/env python3
"""
Fix and Test - Apply fixes to tools and run tests to verify.

This script applies fixes to tool WDLs and runs tests to verify the fixes work.
It's designed for the fix-test-iterate workflow used by AI agents.

Usage:
    # Apply a single fix and test
    python cli/fix_and_test.py <tool-id> --fix-file fix.json --test
    
    # Apply fix and run quick test
    python cli/fix_and_test.py <tool-id> --fix-file fix.json --quick-test
    
    # Apply fix with auto-publish on success
    python cli/fix_and_test.py <tool-id> --fix-file fix.json --test --publish
    
    # Generate fix instruction for AI agent on failure
    python cli/fix_and_test.py <tool-id> --fix-file fix.json --test --generate-instruction
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from cli.auth import get_bearer_token

from wdl_common.api_client import AdoptAPIClient
from wdl_common.data_cache import DataCache
from wdl_common.diff_utils import display_diff, generate_wdl_diff
from wdl_common.interactive import print_error, print_info, print_success, print_warning
from wdl_common.models import DiagnosticIssue
from wdl_common.rollback import RollbackManager


def load_fix_file(fix_file_path: str) -> Dict[str, Any]:
    """Load and validate a fix file."""
    path = Path(fix_file_path)
    if not path.exists():
        raise FileNotFoundError(f"Fix file not found: {fix_file_path}")
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    # Validate structure
    if 'new_wdl' not in data and 'wdl' not in data and 'fixes' not in data:
        raise ValueError("Fix file must contain 'new_wdl', 'wdl', or 'fixes'")
    
    return data


def apply_fix(
    bearer_token: str,
    tool_id: str,
    new_wdl: List[Dict],
    change_reason: str = "Apply fix",
    auto_approve: bool = False,
    publish: bool = False,
) -> Tuple[bool, str, Optional[int]]:
    """
    Apply a WDL fix to a tool.
    
    Returns:
        Tuple of (success, message, version_number)
    """
    client = AdoptAPIClient(bearer_token)
    
    # Save as draft
    success, data, msg = client.save_draft(tool_id, new_wdl, change_reason)
    if not success:
        return False, f"Failed to save draft: {msg}", None
    
    version = data.get('version') if data else None
    
    if auto_approve and version:
        # Approve the version
        success, data, msg = client.approve_version(tool_id, version)
        if not success:
            return False, f"Failed to approve version: {msg}", version
    
    if publish:
        # Publish WDL
        success, data, msg = client.publish_wdl(tool_id)
        if not success:
            return False, f"Failed to publish: {msg}", version
    
    return True, "Fix applied successfully", version


def run_quick_test(
    bearer_token: str,
    tool_id: str,
    test_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run a quick test of a tool.
    
    Args:
        bearer_token: Auth token
        tool_id: Tool ID to test
        test_prompt: Optional test prompt
        
    Returns:
        Test result with success, output, errors
    """
    client = AdoptAPIClient(bearer_token)
    
    # Get tool details
    success, tool, msg = client.get_action(tool_id)
    if not success:
        return {'success': False, 'error': f"Failed to get tool: {msg}"}
    
    tool_title = tool.get('title') or tool.get('name') or tool_id
    
    # Get default test prompt if not provided
    if not test_prompt:
        test_prompt = tool.get('test_prompts', [{}])[0].get('prompt', f"Test {tool_title}")
    
    # Execute
    try:
        result = client.run_action(
            tool_id,
            test_prompt,
            conversation_history=[],
            timeout=60
        )
        
        return {
            'success': True,
            'prompt': test_prompt,
            'output': result,
            'tool_title': tool_title,
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'prompt': test_prompt,
            'tool_title': tool_title,
        }


def run_full_test(
    bearer_token: str,
    tool_id: str,
    test_prompts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run full test suite for a tool.
    
    Returns:
        Test results with passed/failed counts and details
    """
    client = AdoptAPIClient(bearer_token)
    
    # Get tool details
    success, tool, msg = client.get_action(tool_id)
    if not success:
        return {'success': False, 'error': f"Failed to get tool: {msg}"}
    
    tool_title = tool.get('title') or tool.get('name') or tool_id
    
    # Get test prompts from tool if not provided
    if not test_prompts:
        prompts_data = tool.get('test_prompts', [])
        test_prompts = [p.get('prompt') for p in prompts_data if p.get('prompt')]
    
    if not test_prompts:
        # Generate basic test prompt
        test_prompts = [f"Test the {tool_title} functionality"]
    
    results = {
        'success': True,
        'tool_id': tool_id,
        'tool_title': tool_title,
        'total_tests': len(test_prompts),
        'passed': 0,
        'failed': 0,
        'errors': [],
        'test_results': [],
    }
    
    for i, prompt in enumerate(test_prompts):
        print(f"   🧪 Running test {i+1}/{len(test_prompts)}...")
        
        try:
            output = client.run_action(
                tool_id,
                prompt,
                conversation_history=[],
                timeout=60
            )
            
            # Check if output indicates success
            is_success = True
            if isinstance(output, dict):
                if output.get('error') or output.get('status') == 'error':
                    is_success = False
                    results['errors'].append(output.get('error', 'Unknown error'))
            
            results['test_results'].append({
                'prompt': prompt,
                'success': is_success,
                'output': output,
            })
            
            if is_success:
                results['passed'] += 1
            else:
                results['failed'] += 1
                results['success'] = False
                
        except Exception as e:
            results['test_results'].append({
                'prompt': prompt,
                'success': False,
                'error': str(e),
            })
            results['failed'] += 1
            results['errors'].append(str(e))
            results['success'] = False
    
    return results


def generate_fix_instruction(
    tool_id: str,
    tool_title: str,
    wdl: List[Dict],
    test_results: Dict[str, Any],
    errors: List[str],
) -> str:
    """
    Generate an AI-agent instruction for fixing based on test failures.
    
    Args:
        tool_id: Tool ID
        tool_title: Tool title
        wdl: Current WDL
        test_results: Test results
        errors: List of errors encountered
        
    Returns:
        Markdown instruction for AI agent
    """
    instruction = f"""# Fix Instruction for {tool_title}

## Tool Details
- **Tool ID**: {tool_id}
- **Tool Title**: {tool_title}

## Test Failures

{len(errors)} tests failed with the following errors:

"""
    
    for i, err in enumerate(errors[:5]):  # Limit to 5 errors
        instruction += f"### Error {i+1}\n```\n{err}\n```\n\n"
    
    instruction += f"""## Current WDL

```json
{json.dumps(wdl, indent=2)}
```

## Instructions for AI Agent

1. Analyze the errors above and identify the root cause
2. Common issues to check:
   - Missing `workflow_arguments.` prefix in URL parameters
   - Missing entries in `required_inputs`
   - Incorrect API paths (trailing slash mismatches)
   - Missing query parameters
   - Wrong HTTP method

3. Generate a fix file with the following structure:

```json
{{
    "tool_id": "{tool_id}",
    "changes_summary": "Description of what you fixed",
    "new_wdl": [
        // Your corrected WDL blocks here
    ]
}}
```

4. Save the fix file and apply it:
   ```bash
   python cli/fix_and_test.py {tool_id} --fix-file your_fix.json --test
   ```
"""
    
    return instruction


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Apply fixes to tools and run tests to verify",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Apply fix and run test
  python cli/fix_and_test.py <tool-id> --fix-file fix.json --test
  
  # Quick test only
  python cli/fix_and_test.py <tool-id> --quick-test --prompt "List users"
  
  # Apply fix with auto-publish
  python cli/fix_and_test.py <tool-id> --fix-file fix.json --test --publish --auto-approve
  
  # Generate instruction on failure
  python cli/fix_and_test.py <tool-id> --fix-file fix.json --test --generate-instruction
""",
    )
    
    parser.add_argument("tool_id", help="Tool ID to fix and test")
    parser.add_argument("--fix-file", help="Path to JSON file with WDL fix")
    parser.add_argument("--test", action="store_true", help="Run full test after fix")
    parser.add_argument("--quick-test", action="store_true", help="Run quick single test")
    parser.add_argument("--prompt", help="Test prompt for quick test")
    parser.add_argument("--publish", action="store_true", help="Publish on successful test")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve version")
    parser.add_argument("--generate-instruction", action="store_true", 
                       help="Generate AI instruction on failure")
    parser.add_argument("--output", help="Output file for results/instructions")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    
    args = parser.parse_args()
    
    if not args.fix_file and not args.test and not args.quick_test:
        parser.print_help()
        return 1
    
    print("\n" + "=" * 70)
    print("🔧 FIX AND TEST")
    print("=" * 70)
    
    try:
        bearer_token = get_bearer_token()
        client = AdoptAPIClient(bearer_token)
        rollback_manager = RollbackManager()
        
        # Get current tool details
        print(f"\n⏳ Fetching tool {args.tool_id}...")
        success, tool, msg = client.get_action(args.tool_id)
        if not success:
            print_error(f"Failed to get tool: {msg}")
            return 1
        
        tool_title = tool.get('title') or tool.get('name') or args.tool_id
        current_wdl = tool.get('wdl') or tool.get('widdle') or []
        
        print(f"   Tool: {tool_title}")
        print(f"   WDL Blocks: {len(current_wdl)}")
        
        new_wdl = None
        
        # Apply fix if provided
        if args.fix_file:
            print(f"\n📂 Loading fix from {args.fix_file}...")
            fix_data = load_fix_file(args.fix_file)
            
            new_wdl = fix_data.get('new_wdl') or fix_data.get('wdl')
            change_reason = fix_data.get('changes_summary', 'Apply fix from file')
            
            if not new_wdl:
                print_error("Fix file does not contain 'new_wdl'")
                return 1
            
            # Show diff
            print("\n📋 Proposed Changes:")
            diff = generate_wdl_diff(current_wdl, new_wdl)
            display_diff(diff)
            
            if args.dry_run:
                print("\n[DRY RUN] Would apply these changes")
                return 0
            
            # Save rollback state
            rollback_manager.start_session()
            rollback_manager.add_tool_change(args.tool_id, tool_title, current_wdl, new_wdl)
            
            # Apply fix
            print("\n⚡ Applying fix...")
            success, message, version = apply_fix(
                bearer_token=bearer_token,
                tool_id=args.tool_id,
                new_wdl=new_wdl,
                change_reason=change_reason,
                auto_approve=args.auto_approve,
                publish=False,  # Will publish after test if --publish is set
            )
            
            if not success:
                print_error(message)
                return 1
            
            print_success(f"Fix applied (version {version})")
        
        # Run tests
        test_results = None
        
        if args.quick_test:
            print("\n🧪 Running quick test...")
            test_results = run_quick_test(
                bearer_token=bearer_token,
                tool_id=args.tool_id,
                test_prompt=args.prompt,
            )
            
            if test_results['success']:
                print_success("Quick test passed!")
            else:
                print_error(f"Quick test failed: {test_results.get('error', 'Unknown error')}")
        
        if args.test:
            print("\n🧪 Running full test suite...")
            test_results = run_full_test(
                bearer_token=bearer_token,
                tool_id=args.tool_id,
            )
            
            print(f"\n   Results: {test_results['passed']}/{test_results['total_tests']} passed")
            
            if test_results['success']:
                print_success("All tests passed!")
            else:
                print_error(f"{test_results['failed']} tests failed")
        
        # Publish on success
        if args.publish and test_results and test_results['success']:
            print("\n🚀 Publishing...")
            success, data, msg = client.publish_wdl(args.tool_id)
            if success:
                print_success("Published successfully!")
            else:
                print_error(f"Publish failed: {msg}")
        
        # Generate instruction on failure
        if args.generate_instruction and test_results and not test_results['success']:
            print("\n📝 Generating fix instruction...")
            instruction = generate_fix_instruction(
                tool_id=args.tool_id,
                tool_title=tool_title,
                wdl=new_wdl or current_wdl,
                test_results=test_results,
                errors=test_results.get('errors', []),
            )
            
            output_file = args.output or f"diagnostics/fix_instruction_{args.tool_id}.md"
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(instruction)
            
            print(f"   Instruction saved to: {output_file}")
        
        # Summary
        print("\n" + "=" * 70)
        if test_results:
            if test_results['success']:
                print("✅ SUCCESS - All tests passed")
                return 0
            else:
                print("❌ FAILURE - Tests failed")
                print(f"   Rollback file: {rollback_manager.get_session_file()}")
                return 1
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









