#!/usr/bin/env python3
"""
Inspect Tool WDL - Inspect a tool's WDL for common issues.

This script inspects tool WDLs for common issues like missing workflow_arguments
prefix, trailing slash mismatches, missing query parameters, etc.

Usage:
    # Inspect single tool
    python cli/inspect_tool_wdl.py <tool-id>
    
    # Inspect all tools matching a pattern
    python cli/inspect_tool_wdl.py --search "organization"
    
    # Check tools using specific API
    python cli/inspect_tool_wdl.py --api-id <api-id>
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from cli.auth import get_bearer_token_for_env

from wdl_common.api_client import AdoptAPIClient
from wdl_common.data_cache import DataCache
from wdl_common.issue_detector import (
    detect_all_issues,
    detect_missing_required_inputs,
    detect_missing_workflow_arguments,
    detect_wdl_structure_issues,
)
from wdl_common.models import DiagnosticIssue, Severity


def fetch_tool_details(
    bearer_token: str,
    tool_id: str,
    actions_endpoint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch detailed tool information including WDL."""
    actions_endpoint = actions_endpoint or os.getenv('ADOPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{tool_id}/current/"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"⚠️  Failed to fetch tool details. Status: {response.status_code}")
            return None
        
        return response.json()
        
    except requests.exceptions.RequestException as e:
        print(f"⚠️  Network error: {e}")
        return None


def fetch_all_tools(bearer_token: str, api_endpoint: Optional[str] = None) -> List[Dict]:
    """Fetch all tools from the API."""
    api_endpoint = api_endpoint or os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    
    url = f"{api_endpoint}/v1/actions/list"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    params = {"execution_type": "TOOL"}
    
    print("⏳ Fetching all tools...")
    response = requests.get(url, headers=headers, params=params, timeout=30)
    
    if response.status_code != 200:
        raise ValueError(f"Failed to list tools. Status: {response.status_code}")
    
    json_response = response.json()
    
    if "capabilities" in json_response:
        tools = json_response["capabilities"]
    elif isinstance(json_response, list):
        tools = json_response
    else:
        tools = []
    
    print(f"✅ Fetched {len(tools)} tool(s)")
    return tools


def inspect_tool(
    tool_id: str,
    bearer_token: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Inspect a single tool for issues.
    
    Args:
        tool_id: The tool ID to inspect
        bearer_token: Authentication token
        verbose: Print verbose output
        
    Returns:
        Inspection results dictionary
    """
    result = {
        'tool_id': tool_id,
        'success': False,
        'issues': [],
        'summary': {},
    }
    
    # Fetch tool details
    tool_details = fetch_tool_details(bearer_token, tool_id)
    
    if not tool_details:
        result['error'] = f"Could not fetch tool: {tool_id}"
        return result
    
    tool_title = tool_details.get('title') or tool_details.get('name') or 'Untitled'
    wdl = tool_details.get('wdl') or tool_details.get('widdle') or []
    
    result['tool_title'] = tool_title
    result['wdl_operation_count'] = len(wdl) if isinstance(wdl, list) else 0
    
    if verbose:
        print(f"\n📋 Tool: {tool_title}")
        print(f"   ID: {tool_id}")
        print(f"   WDL Operations: {result['wdl_operation_count']}")
    
    if not wdl:
        result['error'] = "Tool has no WDL"
        if verbose:
            print("   ⚠️  No WDL found")
        return result
    
    # Detect issues
    issues = detect_all_issues(wdl)
    
    # Update issues with tool info
    for issue in issues:
        issue.tool_id = tool_id
        issue.tool_title = tool_title
    
    result['issues'] = [issue.to_dict() for issue in issues]
    result['issue_count'] = len(issues)
    result['success'] = True
    
    # Summarize by type
    result['summary'] = {}
    for issue in issues:
        issue_type = issue.issue_type
        result['summary'][issue_type] = result['summary'].get(issue_type, 0) + 1
    
    if verbose:
        if issues:
            print(f"\n   ⚠️  Found {len(issues)} issue(s):")
            for issue in issues:
                severity_icon = {
                    'CRITICAL': '🔴',
                    'HIGH': '🟠',
                    'MEDIUM': '🟡',
                    'LOW': '🟢'
                }.get(issue.severity, '⚪')
                print(f"      {severity_icon} {issue.issue_type}")
                if issue.details:
                    for key, value in list(issue.details.items())[:3]:
                        print(f"         {key}: {str(value)[:60]}")
        else:
            print("   ✅ No issues found")
    
    return result


def display_inspection_summary(results: List[Dict[str, Any]]) -> None:
    """Display summary of inspection results."""
    total_tools = len(results)
    tools_with_issues = sum(1 for r in results if r.get('issue_count', 0) > 0)
    total_issues = sum(r.get('issue_count', 0) for r in results)
    
    print("\n" + "=" * 70)
    print("📊 INSPECTION SUMMARY")
    print("=" * 70)
    
    print(f"\nTools Inspected: {total_tools}")
    print(f"Tools with Issues: {tools_with_issues}")
    print(f"Total Issues: {total_issues}")
    
    # Aggregate issues by type
    issue_types: Dict[str, int] = {}
    for r in results:
        for issue_type, count in r.get('summary', {}).items():
            issue_types[issue_type] = issue_types.get(issue_type, 0) + count
    
    if issue_types:
        print("\n📋 Issues by Type:")
        for issue_type, count in sorted(issue_types.items(), key=lambda x: -x[1]):
            print(f"   • {issue_type}: {count}")
    
    # List tools with most issues
    tools_sorted = sorted(
        [r for r in results if r.get('issue_count', 0) > 0],
        key=lambda x: x.get('issue_count', 0),
        reverse=True
    )
    
    if tools_sorted:
        print("\n📋 Tools with Most Issues (top 10):")
        for r in tools_sorted[:10]:
            tool_title = r.get('tool_title', 'Unknown')[:40]
            issue_count = r.get('issue_count', 0)
            print(f"   • {tool_title}: {issue_count} issue(s)")
    
    print("\n" + "=" * 70)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Inspect tool WDLs for common issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Inspect single tool
  python cli/inspect_tool_wdl.py abc123
  
  # Inspect all tools matching a pattern  
  python cli/inspect_tool_wdl.py --search "organization"
  
  # Check tools using specific API
  python cli/inspect_tool_wdl.py --api-id xyz789
  
  # Inspect all tools
  python cli/inspect_tool_wdl.py --all
""",
    )
    
    parser.add_argument("tool_id", nargs="?", help="Tool ID to inspect")
    parser.add_argument("--search", help="Search pattern to filter tools")
    parser.add_argument("--api-id", help="Inspect tools using this API ID")
    parser.add_argument("--all", action="store_true", help="Inspect all tools")
    parser.add_argument("--output", "-o", help="Output file for results (JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if not args.tool_id and not args.search and not args.api_id and not args.all:
        parser.print_help()
        return 1
    
    print("\n" + "=" * 70)
    print("🔍 TOOL WDL INSPECTOR")
    print("=" * 70)
    
    try:
        cache = DataCache()
        bearer_token = get_bearer_token_for_env()  # Uses active environment
        
        tools_to_inspect = []
        
        if args.tool_id:
            # Single tool inspection
            tools_to_inspect = [{'id': args.tool_id}]
        else:
            # Fetch all tools
            all_tools = cache.load_tools_cache()
            if not all_tools:
                all_tools = fetch_all_tools(bearer_token)
                if all_tools:
                    cache.save_tools_cache(all_tools)
            
            if args.search:
                # Filter by search pattern
                search_lower = args.search.lower()
                tools_to_inspect = [
                    t for t in all_tools
                    if search_lower in (t.get('title') or t.get('name') or '').lower()
                    or search_lower in (t.get('description') or '').lower()
                ]
                print(f"\n🔍 Found {len(tools_to_inspect)} tool(s) matching '{args.search}'")
            elif args.api_id:
                # Filter by API ID
                tools_to_inspect = [
                    t for t in all_tools
                    if args.api_id in (t.get('api_ids') or [])
                ]
                print(f"\n🔍 Found {len(tools_to_inspect)} tool(s) using API '{args.api_id}'")
            elif args.all:
                tools_to_inspect = all_tools
                print(f"\n🔍 Inspecting all {len(tools_to_inspect)} tool(s)")
        
        if not tools_to_inspect:
            print("❌ No tools to inspect")
            return 1
        
        # Inspect each tool
        results = []
        for i, tool in enumerate(tools_to_inspect):
            tool_id = tool.get('id')
            if not tool_id:
                continue
            
            if not args.verbose and len(tools_to_inspect) > 1:
                print(f"   [{i+1}/{len(tools_to_inspect)}] Inspecting {tool.get('title', tool_id)[:40]}...", end=" ")
            
            result = inspect_tool(tool_id, bearer_token, verbose=args.verbose)
            results.append(result)
            
            if not args.verbose and len(tools_to_inspect) > 1:
                if result.get('issue_count', 0) > 0:
                    print(f"⚠️ {result['issue_count']} issue(s)")
                else:
                    print("✓")
        
        # Display summary
        display_inspection_summary(results)
        
        # Save results if output specified
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            report = {
                'inspection_date': datetime.now().isoformat(),
                'tools_inspected': len(results),
                'results': results,
            }
            
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)
            
            print(f"\n💾 Results saved to: {output_path}")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









