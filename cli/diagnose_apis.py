#!/usr/bin/env python3
"""
Diagnose APIs - Scan APIs for issues by matching to network logs.

This script scans all APIs and matches them to HTTP network logs to identify
potential issues like trailing slash mismatches, missing parameters, etc.

Usage:
    # Full diagnostic scan
    python cli/diagnose_apis.py --scan --output diagnostics/report.json
    
    # Diagnose specific API
    python cli/diagnose_apis.py --api-id <id> --verbose
    
    # Filter by issue type
    python cli/diagnose_apis.py --scan --filter trailing_slash
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
from cli.auth import get_bearer_token

from wdl_common.api_client import AdoptAPIClient
from wdl_common.data_cache import DataCache
from wdl_common.issue_detector import detect_trailing_slash_issue
from wdl_common.log_matcher import match_api_to_network_logs
from wdl_common.models import DiagnosticIssue, IssueType, MatchResult, Severity


def fetch_all_apis(bearer_token: str, api_endpoint: Optional[str] = None) -> List[Dict]:
    """Fetch all APIs from the AdoptAI API."""
    api_endpoint = api_endpoint or os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    
    url = f"{api_endpoint}/v1/tools/apis"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    all_apis = []
    page = 1
    page_size = 50
    
    print("⏳ Fetching all APIs...")
    
    while True:
        params = {"page": page, "page_size": page_size}
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            raise ValueError(f"Failed to list APIs. Status: {response.status_code}")
        
        json_response = response.json()
        
        page_apis = []
        if isinstance(json_response, list):
            page_apis = json_response
        elif isinstance(json_response, dict):
            page_apis = json_response.get("apis") or json_response.get("data") or json_response.get("items") or []
        
        if not page_apis:
            break
        
        all_apis.extend(page_apis)
        
        has_more = len(page_apis) >= page_size
        if not has_more:
            break
        
        page += 1
    
    print(f"✅ Fetched {len(all_apis)} API(s)")
    return all_apis


def scan_apis_for_issues(
    apis: List[Dict],
    network_logs: List,
    issue_filter: Optional[str] = None,
    min_match_score: float = 0.7,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Scan all APIs for issues.
    
    Args:
        apis: List of API dictionaries
        network_logs: List of NetworkLogEntry objects
        issue_filter: Optional issue type to filter (e.g., 'trailing_slash')
        min_match_score: Minimum match score for log matching
        verbose: Print verbose output
        
    Returns:
        Diagnostic report dictionary
    """
    report = {
        'scan_date': datetime.now().isoformat(),
        'total_apis': len(apis),
        'matched': 0,
        'issues_found': 0,
        'issues_by_type': {},
        'issues_by_severity': {},
        'issues': [],
        'api_matches': [],
    }
    
    issue_type_map = {
        'trailing_slash': IssueType.TRAILING_SLASH_MISMATCH,
        'workflow_arguments': IssueType.MISSING_WORKFLOW_ARGUMENTS,
        'query_params': IssueType.MISSING_QUERY_PARAMETERS,
    }
    
    filter_type = issue_type_map.get(issue_filter) if issue_filter else None
    
    print(f"\n⏳ Scanning {len(apis)} APIs against {len(network_logs)} network logs...")
    
    for i, api in enumerate(apis):
        api_id = api.get('id', '')
        api_title = api.get('title') or api.get('name') or 'Untitled'
        
        if verbose:
            print(f"   [{i+1}/{len(apis)}] {api_title[:40]}...", end=" ")
        
        # Match API to network logs
        match_result = match_api_to_network_logs(
            api=api,
            network_logs=network_logs,
            min_match_score=min_match_score,
        )
        
        if match_result.matched_logs:
            report['matched'] += 1
            
            # Record match info
            report['api_matches'].append({
                'api_id': api_id,
                'api_title': api_title,
                'canonical_path': match_result.canonical_path,
                'best_match_score': match_result.best_match_score,
                'matched_log_count': len(match_result.matched_logs),
                'needs_fix': match_result.needs_fix,
            })
            
            # Check for trailing slash issue
            issue = detect_trailing_slash_issue(match_result, api)
            
            if issue:
                # Apply filter if specified
                if filter_type and issue.issue_type != filter_type:
                    if verbose:
                        print("filtered")
                    continue
                
                report['issues_found'] += 1
                report['issues'].append(issue.to_dict())
                
                # Count by type
                issue_type = issue.issue_type
                report['issues_by_type'][issue_type] = report['issues_by_type'].get(issue_type, 0) + 1
                
                # Count by severity
                severity = issue.severity
                report['issues_by_severity'][severity] = report['issues_by_severity'].get(severity, 0) + 1
                
                if verbose:
                    print(f"⚠️ {issue.issue_type}")
            elif verbose:
                print("✓")
        elif verbose:
            print("no match")
    
    return report


def display_scan_results(report: Dict[str, Any]) -> None:
    """Display scan results in a formatted way."""
    print("\n" + "=" * 70)
    print("📊 DIAGNOSTIC SCAN RESULTS")
    print("=" * 70)
    
    print(f"\nScan Date: {report['scan_date'][:19]}")
    print(f"Total APIs: {report['total_apis']}")
    print(f"APIs Matched to Logs: {report['matched']}")
    print(f"Issues Found: {report['issues_found']}")
    
    if report['issues_by_severity']:
        print("\n📋 Issues by Severity:")
        for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
            count = report['issues_by_severity'].get(severity, 0)
            if count > 0:
                icon = {'CRITICAL': '🔴', 'HIGH': '🟠', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(severity, '⚪')
                print(f"   {icon} {severity}: {count}")
    
    if report['issues_by_type']:
        print("\n📋 Issues by Type:")
        for issue_type, count in sorted(report['issues_by_type'].items(), key=lambda x: -x[1]):
            print(f"   • {issue_type}: {count}")
    
    if report['issues']:
        print("\n📋 Top Issues (first 10):")
        for i, issue in enumerate(report['issues'][:10], 1):
            api_title = issue.get('api_title', 'Unknown')[:40]
            issue_type = issue.get('issue_type', 'unknown')
            severity = issue.get('severity', 'MEDIUM')
            print(f"   {i}. [{severity}] {api_title}: {issue_type}")
    
    print("\n" + "=" * 70)


def save_report(report: Dict[str, Any], output_file: str) -> None:
    """Save report to JSON file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n💾 Report saved to: {output_path}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Scan APIs for issues by matching to network logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full diagnostic scan
  python cli/diagnose_apis.py --scan --output diagnostics/report.json
  
  # Diagnose specific API
  python cli/diagnose_apis.py --api-id abc123 --verbose
  
  # Filter by issue type
  python cli/diagnose_apis.py --scan --filter trailing_slash
""",
    )
    
    # Action modes
    parser.add_argument("--scan", action="store_true", help="Scan all APIs for issues")
    parser.add_argument("--api-id", help="Diagnose specific API by ID")
    
    # Options
    parser.add_argument("--filter", choices=['trailing_slash', 'workflow_arguments', 'query_params'],
                       help="Filter by issue type")
    parser.add_argument("--min-score", type=float, default=0.7, help="Minimum match score (default: 0.7)")
    parser.add_argument("--output", "-o", help="Output file for report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--force-refresh", "-f", action="store_true", help="Force refresh caches")
    
    args = parser.parse_args()
    
    if not args.scan and not args.api_id:
        parser.print_help()
        return 1
    
    print("\n" + "=" * 70)
    print("🔍 API DIAGNOSTIC SCANNER")
    print("=" * 70)
    
    try:
        cache = DataCache()
        bearer_token = get_bearer_token()
        
        # Load or fetch network logs
        logs = cache.load_logs_cache()
        if not logs or args.force_refresh:
            print("\n⚠️  Network logs not cached. Fetching from API...")
            from cli.fetch_http_logs import fetch_network_logs_from_api
            logs = fetch_network_logs_from_api(bearer_token, max_logs=10000)
            if logs:
                cache.save_logs_cache(logs)
        
        if not logs:
            print("❌ No network logs available. Run: python cli/fetch_http_logs.py --fetch")
            return 1
        
        print(f"📊 Using {len(logs)} cached network logs")
        
        # Load or fetch APIs
        apis = cache.load_apis_cache()
        if not apis or args.force_refresh:
            apis = fetch_all_apis(bearer_token)
            if apis:
                cache.save_apis_cache(apis)
        
        if not apis:
            print("❌ No APIs available.")
            return 1
        
        if args.api_id:
            # Diagnose single API
            api = next((a for a in apis if a.get('id') == args.api_id), None)
            if not api:
                print(f"❌ API not found: {args.api_id}")
                return 1
            
            apis = [api]
            print(f"\n🔍 Diagnosing API: {api.get('title') or api.get('name')}")
        
        # Run scan
        report = scan_apis_for_issues(
            apis=apis,
            network_logs=logs,
            issue_filter=args.filter,
            min_match_score=args.min_score,
            verbose=args.verbose,
        )
        
        # Display results
        display_scan_results(report)
        
        # Save report if output specified
        if args.output:
            save_report(report, args.output)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









