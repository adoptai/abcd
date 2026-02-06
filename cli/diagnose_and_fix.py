#!/usr/bin/env python3
"""
Diagnose and Fix - Complete AI-agent-driven diagnostic and fix workflow.

This is the main workflow script that ties all diagnostic tools together,
providing a comprehensive workflow for scanning, diagnosing, and fixing
API and tool WDL issues.

IMPORTANT: This script integrates with the hierarchical workspace manager.
All operations use the active environment's credentials and cache.

Usage Modes:
    # MODE 1: Scan & Generate Summary (for AI agent investigation)
    python cli/diagnose_and_fix.py --scan --output diagnostics/issues_summary.md
    
    # MODE 2: Scan with LLM-optimized output format
    python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/agent_report.json
    
    # MODE 3: Apply fixes from agent-generated file
    python cli/diagnose_and_fix.py --apply-fixes diagnostics/fixes.json
    
    # MODE 4: Full interactive workflow
    python cli/diagnose_and_fix.py --interactive
    
    # MODE 5: Single tool deep-dive
    python cli/diagnose_and_fix.py --tool-id <id> --deep-scan
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

from wdl_common.api_client import AdoptAPIClient
from wdl_common.context import ensure_env, get_client
from wdl_common.data_cache import DataCache
from wdl_common.diff_utils import display_diff
from wdl_common.interactive import (
    print_error,
    print_info,
    print_success,
    print_warning,
    prompt_confirmation,
    prompt_fix_action,
)
from wdl_common.issue_detector import detect_all_issues
from wdl_common.log_matcher import match_api_to_network_logs
from wdl_common.models import DiagnosticIssue, IssueType, Severity
from wdl_common.rollback import RollbackManager, save_rollback_state


def fetch_all_apis(bearer_token: str) -> List[Dict]:
    """Fetch all APIs from the API."""
    import requests
    
    api_endpoint = os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    url = f"{api_endpoint}/v1/tools/apis"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    all_apis = []
    page = 1
    
    while True:
        response = requests.get(url, headers=headers, params={"page": page, "page_size": 50}, timeout=30)
        if response.status_code != 200:
            break
        
        data = response.json()
        apis = data.get("apis") or data.get("data") or data.get("items") or []
        if isinstance(data, list):
            apis = data
        
        if not apis:
            break
        
        all_apis.extend(apis)
        if len(apis) < 50:
            break
        page += 1
    
    return all_apis


def fetch_all_tools(bearer_token: str) -> List[Dict]:
    """Fetch all tools from the API."""
    import requests
    
    api_endpoint = os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    url = f"{api_endpoint}/v1/actions/list"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers, params={"execution_type": "TOOL"}, timeout=30)
    if response.status_code != 200:
        return []
    
    data = response.json()
    return data.get("capabilities") or data.get("data") or []


def fetch_tool_details(bearer_token: str, tool_id: str) -> Optional[Dict]:
    """Fetch detailed tool information including WDL."""
    client = AdoptAPIClient(bearer_token)
    success, data, msg = client.get_action(tool_id)
    return data if success else None


def run_comprehensive_scan(
    bearer_token: str,
    cache: DataCache,
    output_format: str = "llm",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Run a comprehensive diagnostic scan of all tools and APIs.
    
    Args:
        bearer_token: Authentication token
        cache: Data cache instance
        output_format: 'llm' for JSON, 'markdown' for human-readable
        force_refresh: Force refresh caches
        
    Returns:
        Comprehensive diagnostic report
    """
    print("\n⏳ Starting comprehensive diagnostic scan...")
    
    # Load or fetch network logs
    logs = cache.load_logs_cache()
    if not logs or force_refresh:
        print("   📡 Fetching network logs from API...")
        from cli.fetch_http_logs import fetch_network_logs_from_api
        logs = fetch_network_logs_from_api(bearer_token, max_logs=10000)
        if logs:
            cache.save_logs_cache(logs)
    
    if not logs:
        return {"error": "No network logs available"}
    
    print(f"   📊 Using {len(logs)} network logs")
    
    # Load or fetch APIs
    apis = cache.load_apis_cache()
    if not apis or force_refresh:
        print("   📡 Fetching APIs...")
        apis = fetch_all_apis(bearer_token)
        if apis:
            cache.save_apis_cache(apis)
    
    print(f"   📊 Found {len(apis)} APIs")
    
    # Load or fetch tools
    tools = cache.load_tools_cache()
    if not tools or force_refresh:
        print("   📡 Fetching tools...")
        tools = fetch_all_tools(bearer_token)
        if tools:
            cache.save_tools_cache(tools, include_wdl=False)
    
    print(f"   📊 Found {len(tools)} tools")
    
    # Initialize report
    report = {
        "report_metadata": {
            "generated_at": datetime.now().isoformat(),
            "scan_duration_seconds": 0,
            "tool_builder_version": "1.0.0",
        },
        "summary": {
            "total_tools": len(tools),
            "total_apis": len(apis),
            "tools_with_issues": 0,
            "apis_with_issues": 0,
            "total_issues": 0,
            "issues_by_type": {},
            "severity_breakdown": {},
        },
        "issue_categories": _get_issue_category_descriptions(),
        "issues": [],
        "tools_requiring_attention": [],
        "next_steps_for_agent": [
            "1. Review each issue in the 'issues' array",
            "2. For each issue, examine the 'details' and 'suggested_fix'",
            "3. Generate a fixes.json file with your corrections",
            "4. Run: python cli/diagnose_and_fix.py --apply-fixes fixes.json",
            "5. Test each fixed tool: python cli/test_runner.py <tool-id> --all",
        ],
    }
    
    import time
    start_time = time.time()
    
    # Scan APIs for issues
    print("\n   🔍 Scanning APIs for issues...")
    api_issues_count = 0
    for i, api in enumerate(apis):
        match_result = match_api_to_network_logs(api, logs, min_match_score=0.7)
        
        if match_result.needs_fix:
            api_issues_count += 1
            issue = _create_api_issue(api, match_result)
            report["issues"].append(issue.to_dict())
            _update_summary_counts(report["summary"], issue)
    
    report["summary"]["apis_with_issues"] = api_issues_count
    
    # Scan tools for WDL issues
    print("   🔍 Scanning tools for WDL issues...")
    tools_with_issues = set()
    
    for i, tool in enumerate(tools[:100]):  # Limit to first 100 for performance
        tool_id = tool.get('id')
        if not tool_id:
            continue
        
        # Fetch full tool details with WDL
        tool_details = fetch_tool_details(bearer_token, tool_id)
        if not tool_details:
            continue
        
        wdl = tool_details.get('wdl') or tool_details.get('widdle') or []
        if not wdl:
            continue
        
        # Detect issues in WDL
        issues = detect_all_issues(wdl)
        
        for issue in issues:
            issue.tool_id = tool_id
            issue.tool_title = tool.get('title') or tool.get('name')
            report["issues"].append(issue.to_dict())
            _update_summary_counts(report["summary"], issue)
            tools_with_issues.add(tool_id)
    
    report["summary"]["tools_with_issues"] = len(tools_with_issues)
    report["summary"]["total_issues"] = len(report["issues"])
    
    # Add tools requiring attention
    for tool_id in tools_with_issues:
        tool = next((t for t in tools if t.get('id') == tool_id), None)
        if tool:
            tool_issues = [i for i in report["issues"] if i.get('tool_id') == tool_id]
            report["tools_requiring_attention"].append({
                "tool_id": tool_id,
                "tool_title": tool.get('title') or tool.get('name'),
                "issue_count": len(tool_issues),
                "issue_ids": [i.get('id') for i in tool_issues],
            })
    
    end_time = time.time()
    report["report_metadata"]["scan_duration_seconds"] = round(end_time - start_time, 2)
    
    return report


def _get_issue_category_descriptions() -> Dict[str, Dict[str, str]]:
    """Get descriptions for each issue category."""
    return {
        IssueType.TRAILING_SLASH_MISMATCH: {
            "description": "API path trailing slash differs from original HTTP log URL",
            "detection_method": "Compare canonical_api_endpoint to matched network logs",
            "fix_approach": "Add or remove trailing slash to match original URL",
        },
        IssueType.MISSING_WORKFLOW_ARGUMENTS: {
            "description": "URL field uses {param} instead of {workflow_arguments.param}",
            "detection_method": "Regex scan for {xxx} without workflow_arguments. prefix in url field",
            "fix_approach": "Replace {param} with {workflow_arguments.param}",
        },
        IssueType.MISSING_QUERY_PARAMETERS: {
            "description": "HTTP logs show query params not present in WDL query_parameters",
            "detection_method": "Parse query string from matched logs, compare to WDL",
            "fix_approach": "Add missing params to query_parameters and required_inputs",
        },
        IssueType.MISSING_REQUIRED_INPUTS: {
            "description": "Parameters referenced in WDL but not declared in required_inputs",
            "detection_method": "Extract all {workflow_arguments.X} refs, verify in required_inputs",
            "fix_approach": "Add missing declarations to required_inputs",
        },
        IssueType.INVALID_WDL_STRUCTURE: {
            "description": "WDL has structural issues (missing id, operation, etc.)",
            "detection_method": "Validate WDL structure against schema",
            "fix_approach": "Add missing required fields",
        },
    }


def _create_api_issue(api: Dict, match_result) -> DiagnosticIssue:
    """Create a DiagnosticIssue from a match result."""
    return DiagnosticIssue(
        id=f"issue-{api.get('id', 'unknown')[:8]}",
        tool_id=None,
        tool_title=None,
        api_id=api.get('id'),
        api_title=api.get('title') or api.get('name'),
        issue_type=IssueType.TRAILING_SLASH_MISMATCH,
        severity=Severity.HIGH,
        confidence=match_result.best_match_score,
        details={
            'current_canonical_path': match_result.canonical_path,
            'expected_path': match_result.corrected_path,
            'matched_log_url': match_result.best_match.api_endpoint_url if match_result.best_match else None,
            'match_score': match_result.best_match_score,
        },
        suggested_fix={
            'type': 'path_update',
            'changes': [
                {
                    'field': 'path',
                    'old_value': match_result.canonical_path,
                    'new_value': match_result.corrected_path,
                },
            ],
        },
    )


def _update_summary_counts(summary: Dict, issue: DiagnosticIssue) -> None:
    """Update summary counts for an issue."""
    issue_type = issue.issue_type
    severity = issue.severity
    
    summary["issues_by_type"][issue_type] = summary["issues_by_type"].get(issue_type, 0) + 1
    summary["severity_breakdown"][severity] = summary["severity_breakdown"].get(severity, 0) + 1


def generate_markdown_report(report: Dict[str, Any]) -> str:
    """Generate a markdown summary from the report."""
    lines = [
        "# Tool & API Diagnostic Report",
        "",
        f"Generated: {report['report_metadata']['generated_at'][:19]}",
        "",
        "## Executive Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total Tools Scanned | {report['summary']['total_tools']} |",
        f"| Total APIs Scanned | {report['summary']['total_apis']} |",
        f"| Tools with Issues | {report['summary']['tools_with_issues']} |",
        f"| APIs with Issues | {report['summary']['apis_with_issues']} |",
        f"| Total Issues Found | {report['summary']['total_issues']} |",
        "",
        "## Issues by Severity",
        "",
    ]
    
    severity_icons = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🟢",
    }
    
    for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
        count = report['summary']['severity_breakdown'].get(severity, 0)
        if count > 0:
            lines.append(f"- {severity_icons.get(severity, '⚪')} **{severity}**: {count} issues")
    
    lines.extend([
        "",
        "## Issues by Type",
        "",
        "| Issue Type | Count |",
        "|------------|-------|",
    ])
    
    for issue_type, count in sorted(report['summary']['issues_by_type'].items(), key=lambda x: -x[1]):
        lines.append(f"| {issue_type} | {count} |")
    
    lines.extend([
        "",
        "## How to Fix",
        "",
        "### Option 1: Apply fixes from a fixes.json file",
        "",
        "```bash",
        "python cli/diagnose_and_fix.py --apply-fixes fixes.json",
        "```",
        "",
        "### Option 2: Fix individual APIs",
        "",
        "```bash",
        "python cli/fix_api_path.py <api-id> --trailing-slash add",
        "```",
        "",
        "### Option 3: Fix individual tools",
        "",
        "```bash",
        "python cli/patch_tool_wdl.py <tool-id> --apply-fix fix.json",
        "```",
        "",
    ])
    
    return "\n".join(lines)


def apply_fixes_from_file(
    bearer_token: str,
    fixes_file: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    test_after_fix: bool = False,
) -> Dict[str, Any]:
    """
    Apply fixes from an agent-generated fixes file.
    
    Args:
        bearer_token: Authentication token
        fixes_file: Path to fixes JSON file
        dry_run: If True, don't make changes
        auto_approve: If True, skip confirmations
        test_after_fix: If True, run tests after each fix
        
    Returns:
        Results summary
    """
    with open(fixes_file, 'r') as f:
        fixes_data = json.load(f)
    
    fixes = fixes_data.get('fixes', [])
    
    print(f"\n📋 Found {len(fixes)} fixes to apply")
    
    results = {
        'applied': 0,
        'skipped': 0,
        'failed': 0,
        'tested': 0,
        'test_passed': 0,
        'details': [],
    }
    
    rollback_manager = RollbackManager()
    rollback_manager.start_session()
    
    apply_all = auto_approve
    
    from cli.fix_api_path import update_api_path
    from cli.patch_tool_wdl import patch_tool_with_wdl
    
    for i, fix in enumerate(fixes):
        fix_id = fix.get('issue_id', f'fix-{i}')
        tool_id = fix.get('tool_id')
        api_id = fix.get('api_id')
        action = fix.get('action', 'update_wdl')
        changes_summary = fix.get('changes_summary', 'Apply fix')
        
        title = fix.get('tool_title') or fix.get('api_title') or 'Unknown'
        
        if not apply_all and not dry_run:
            user_action = prompt_fix_action(i + 1, len(fixes), f"{title}: {changes_summary}")
            
            if user_action == 'quit':
                print("\n⚠️  Quitting (progress saved)")
                break
            elif user_action == 'skip':
                results['skipped'] += 1
                results['details'].append({'fix_id': fix_id, 'status': 'skipped'})
                continue
            elif user_action == 'apply_all':
                apply_all = True
        
        if dry_run:
            print(f"   [DRY RUN] Would apply: {changes_summary}")
            results['applied'] += 1
            continue
        
        success = False
        message = ""
        
        if action == 'update_api' and api_id:
            # Update API path
            api_changes = fix.get('api_changes', [])
            for change in api_changes:
                new_path = change.get('new_value')
                if new_path:
                    success, message = update_api_path(bearer_token, api_id, new_path)
                    if success:
                        rollback_manager.add_api_change(
                            api_id, title,
                            change.get('old_value', ''),
                            new_path
                        )
        
        elif action in ('update_wdl', 'update_both') and tool_id:
            # Update tool WDL
            new_wdl = fix.get('new_wdl')
            if new_wdl:
                # Get current WDL for rollback
                tool_details = fetch_tool_details(bearer_token, tool_id)
                current_wdl = tool_details.get('wdl', []) if tool_details else []
                
                success, message, version = patch_tool_with_wdl(
                    bearer_token, tool_id, new_wdl, changes_summary
                )
                
                if success:
                    rollback_manager.add_tool_change(tool_id, title, current_wdl, new_wdl)
        
        if success:
            print_success(f"Applied: {title}")
            results['applied'] += 1
            results['details'].append({'fix_id': fix_id, 'status': 'applied'})
            
            # Test after fix if requested
            if test_after_fix and tool_id:
                print(f"   🧪 Running tests...")
                try:
                    from cli.test_runner import run_test
                    test_result = run_test(tool_id, run_all=True)
                    results['tested'] += 1
                    if test_result.get('success'):
                        results['test_passed'] += 1
                        print_success("Tests passed")
                    else:
                        print_warning("Tests failed")
                except Exception as e:
                    print_warning(f"Test error: {e}")
        else:
            print_error(f"Failed: {title} - {message}")
            results['failed'] += 1
            results['details'].append({'fix_id': fix_id, 'status': 'failed', 'error': message})
    
    results['rollback_file'] = str(rollback_manager.get_session_file())
    
    return results


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Complete AI-agent-driven diagnostic and fix workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan and generate LLM-optimized report
  python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/agent_report.json
  
  # Generate human-readable markdown
  python cli/diagnose_and_fix.py --scan --format markdown --output diagnostics/issues_summary.md
  
  # Apply fixes from file
  python cli/diagnose_and_fix.py --apply-fixes diagnostics/fixes.json
  
  # Apply fixes with testing
  python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix
  
  # Dry run
  python cli/diagnose_and_fix.py --apply-fixes fixes.json --dry-run
""",
    )
    
    # Scan mode
    parser.add_argument("--scan", action="store_true", help="Run comprehensive diagnostic scan")
    parser.add_argument("--format", choices=['llm', 'markdown'], default='llm',
                       help="Output format (default: llm)")
    
    # Apply fixes mode
    parser.add_argument("--apply-fixes", help="Apply fixes from JSON file")
    
    # Single tool mode
    parser.add_argument("--tool-id", help="Diagnose/fix single tool")
    parser.add_argument("--deep-scan", action="store_true", help="Deep scan for single tool")
    
    # Options
    parser.add_argument("--output", "-o", help="Output file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--auto-approve", action="store_true", help="Skip confirmations")
    parser.add_argument("--test-after-fix", action="store_true", help="Run tests after each fix")
    parser.add_argument("--force-refresh", "-f", action="store_true", help="Force refresh caches")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    
    args = parser.parse_args()
    
    if not args.scan and not args.apply_fixes and not args.tool_id and not args.interactive:
        parser.print_help()
        return 1
    
    print("\n" + "=" * 70)
    print("🔍 DIAGNOSE AND FIX - Comprehensive Diagnostic Workflow")
    print("=" * 70)
    
    try:
        # Ensure environment is loaded and show which one we're using
        env_name = ensure_env()
        print(f"📁 Environment: {env_name}")
        
        # Get client and cache (both use active environment)
        client = get_client()
        bearer_token = client.bearer_token
        cache = DataCache()  # Uses active environment's cache automatically
        print(f"📂 Cache: {cache.cache_dir}")
        
        if args.scan or args.interactive:
            # Run comprehensive scan
            report = run_comprehensive_scan(
                bearer_token=bearer_token,
                cache=cache,
                output_format=args.format,
                force_refresh=args.force_refresh,
            )
            
            # Display summary
            print("\n" + "=" * 70)
            print("📊 SCAN RESULTS")
            print("=" * 70)
            print(f"Total APIs: {report['summary']['total_apis']}")
            print(f"APIs with Issues: {report['summary']['apis_with_issues']}")
            print(f"Total Tools: {report['summary']['total_tools']}")
            print(f"Tools with Issues: {report['summary']['tools_with_issues']}")
            print(f"Total Issues: {report['summary']['total_issues']}")
            print("=" * 70)
            
            # Save output
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                if args.format == 'markdown':
                    content = generate_markdown_report(report)
                    output_path.write_text(content)
                else:
                    with open(output_path, 'w') as f:
                        json.dump(report, f, indent=2)
                
                print(f"\n💾 Report saved to: {output_path}")
            
            return 0
        
        if args.apply_fixes:
            # Apply fixes from file
            results = apply_fixes_from_file(
                bearer_token=bearer_token,
                fixes_file=args.apply_fixes,
                dry_run=args.dry_run,
                auto_approve=args.auto_approve,
                test_after_fix=args.test_after_fix,
            )
            
            print("\n" + "=" * 70)
            print("📊 RESULTS")
            print("=" * 70)
            print(f"Applied: {results['applied']}")
            print(f"Skipped: {results['skipped']}")
            print(f"Failed: {results['failed']}")
            if results.get('tested'):
                print(f"Tested: {results['tested']} (passed: {results['test_passed']})")
            if results.get('rollback_file'):
                print(f"\nRollback file: {results['rollback_file']}")
            print("=" * 70)
            
            return 0 if results['failed'] == 0 else 1
        
        if args.tool_id:
            # Single tool diagnosis
            from cli.inspect_tool_wdl import inspect_tool
            result = inspect_tool(args.tool_id, bearer_token, verbose=True)
            return 0 if result.get('success') else 1
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









