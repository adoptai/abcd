#!/usr/bin/env python3
"""
Fetch HTTP Network Logs - Fetch and cache network logs from the API.

This script fetches HTTP network logs from the AdoptAI API and caches them
for use by diagnostic tools.

IMPORTANT: This script integrates with the hierarchical workspace manager.
Logs are cached per-environment in workspaces/{env}/.cache/.

Auto-detection: If no mode is specified (--fetch/--list/--inspect), the script
will automatically fetch logs if no cache exists, or use cached logs if available.

Usage:
    # Auto-detect mode: searches cache if available, fetches if not
    python cli/fetch_http_logs.py --search "campaign_list"
    
    # Explicitly fetch logs and cache them
    python cli/fetch_http_logs.py --fetch
    
    # Fetch logs with search filter
    python cli/fetch_http_logs.py --fetch --search "api.example.com"
    
    # Fetch logs with max limit
    python cli/fetch_http_logs.py --fetch --max-logs 5000
    
    # Show cached logs stats
    python cli/fetch_http_logs.py --list
    
    # Show logs for specific path pattern
    python cli/fetch_http_logs.py --inspect --path "/org/"
"""

import argparse
import os
import sys
from pathlib import Path
from time import sleep
from typing import Any, Dict, List, Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from wdl_common.context import ensure_env, get_client
from wdl_common.data_cache import DataCache
from wdl_common.log_parser import get_logs_stats, load_network_logs, save_logs_to_file
from wdl_common.models import NetworkLogEntry


def fetch_network_logs_from_api(
    bearer_token: str,
    api_endpoint: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_logs: int = 10000,
    request_delay: float = 1.0,
    max_retries: int = 5,
    skip_failed_pages: bool = False
) -> List[NetworkLogEntry]:
    """
    Fetch network logs from the external API with pagination.
    
    Args:
        bearer_token: The authentication bearer token
        api_endpoint: The external API endpoint
        search: Optional search filter for API URLs
        start_date: Optional start date filter (ISO format)
        end_date: Optional end date filter (ISO format)
        max_logs: Maximum number of logs to fetch
        request_delay: Delay between requests in seconds
        max_retries: Maximum number of retries on rate limit or server errors
        skip_failed_pages: If True, skip pages that fail after max retries
        
    Returns:
        List of NetworkLogEntry objects
    """
    from wdl_common.log_parser import parse_log_entry
    
    api_endpoint = (
        api_endpoint or 
        os.getenv('ADOPT_API_ENDPOINT') or
        'https://connect.adopt.ai'
    )
    
    url = f"{api_endpoint}/v1/http-network-logs"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    all_logs: List[NetworkLogEntry] = []
    page = 1
    page_size = 5
    
    RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
    
    def make_request_with_retry(params: Dict[str, Any], retry_count: int = 0) -> Optional[requests.Response]:
        """Make a request with retry logic."""
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            
            if response.status_code in RETRY_STATUS_CODES:
                if retry_count >= max_retries:
                    if skip_failed_pages:
                        print(f"⏭️  Skipping page {params.get('page')} after {max_retries} failed attempts")
                        return None
                    raise ValueError(f"Max retries exceeded. Status: {response.status_code}")
                
                wait_time = (2 ** retry_count) * request_delay
                print(f"⏳ Rate limited. Waiting {wait_time:.1f}s (retry {retry_count + 1}/{max_retries})...")
                sleep(wait_time)
                return make_request_with_retry(params, retry_count + 1)
            
            return response
            
        except requests.exceptions.Timeout:
            if retry_count >= max_retries:
                if skip_failed_pages:
                    return None
                raise
            wait_time = (2 ** retry_count) * request_delay
            print(f"⏳ Timeout. Waiting {wait_time:.1f}s...")
            sleep(wait_time)
            return make_request_with_retry(params, retry_count + 1)
        except requests.exceptions.ConnectionError:
            if retry_count >= max_retries:
                if skip_failed_pages:
                    return None
                raise
            wait_time = (2 ** retry_count) * request_delay
            print(f"⏳ Connection error. Waiting {wait_time:.1f}s...")
            sleep(wait_time)
            return make_request_with_retry(params, retry_count + 1)
    
    skipped_pages: List[int] = []
    
    print(f"⏳ Fetching network logs from API: {api_endpoint}")
    print(f"   (request delay: {request_delay}s, max retries: {max_retries})")
    
    while len(all_logs) < max_logs:
        params: Dict[str, Any] = {
            "page": page,
            "page_size": page_size
        }
        
        if search:
            params["search"] = search
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        
        print(f"   📥 Fetching page {page}...", end=" ")
        
        response = make_request_with_retry(params)
        
        if response is None:
            skipped_pages.append(page)
            page += 1
            sleep(request_delay)
            continue
        
        if response.status_code != 200:
            if skip_failed_pages:
                skipped_pages.append(page)
                page += 1
                sleep(request_delay)
                continue
            raise ValueError(f"Failed to fetch logs. Status: {response.status_code}")
        
        json_response = response.json()
        
        page_logs = []
        has_more = False
        
        if isinstance(json_response, list):
            page_logs = json_response
            has_more = len(page_logs) >= page_size
        elif isinstance(json_response, dict):
            if "data" in json_response:
                page_logs = json_response["data"]
            elif "items" in json_response:
                page_logs = json_response["items"]
            elif "logs" in json_response:
                page_logs = json_response["logs"]
            else:
                page_logs = [json_response]
            
            has_more = (
                json_response.get("has_more", False) or
                json_response.get("hasMore", False) or
                len(page_logs) >= page_size
            )
        
        for entry in page_logs:
            log_entry = parse_log_entry(entry)
            if log_entry:
                all_logs.append(log_entry)
        
        print(f"got {len(page_logs)} logs (total: {len(all_logs)})")
        
        if not page_logs or not has_more or len(all_logs) >= max_logs:
            break
        
        page += 1
        sleep(request_delay)
    
    if skipped_pages:
        print(f"⚠️  Fetched {len(all_logs)} logs with {len(skipped_pages)} skipped pages")
    else:
        print(f"✅ Successfully fetched {len(all_logs)} network log(s)")
    
    return all_logs


def display_logs_stats(logs: List[NetworkLogEntry]) -> None:
    """Display statistics about network logs."""
    stats = get_logs_stats(logs)
    
    print("\n" + "=" * 60)
    print("📊 NETWORK LOGS STATISTICS")
    print("=" * 60)
    print(f"  Total logs: {stats['total']}")
    print(f"  Unique paths: {stats['unique_paths']}")
    print(f"  Unique hosts: {len(stats['hosts'])}")
    
    if stats['methods']:
        print("\n  Methods:")
        for method, count in sorted(stats['methods'].items(), key=lambda x: -x[1]):
            print(f"    {method}: {count}")
    
    if stats['hosts']:
        print("\n  Hosts (top 10):")
        for host in stats['hosts'][:10]:
            print(f"    {host}")
    
    print("=" * 60)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch and cache HTTP network logs from the API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect: searches cache if available, fetches if not
  python cli/fetch_http_logs.py --search "campaign_list"
  
  # Fetch logs and cache them
  python cli/fetch_http_logs.py --fetch
  
  # Fetch with search filter
  python cli/fetch_http_logs.py --fetch --search "api.example.com" --max-logs 5000
  
  # Show cached logs stats
  python cli/fetch_http_logs.py --list
  
  # Inspect logs matching a path pattern
  python cli/fetch_http_logs.py --inspect --path "/org/"
""",
    )
    
    # Action modes (optional - will auto-detect if not specified)
    action_group = parser.add_mutually_exclusive_group(required=False)
    action_group.add_argument("--fetch", action="store_true", help="Fetch logs from API")
    action_group.add_argument("--list", action="store_true", help="Show cached logs stats")
    action_group.add_argument("--inspect", action="store_true", help="Inspect cached logs")
    
    # Fetch options
    parser.add_argument("--search", help="Search filter for API URLs")
    parser.add_argument("--start-date", help="Start date filter (ISO format)")
    parser.add_argument("--end-date", help="End date filter (ISO format)")
    parser.add_argument("--max-logs", type=int, default=10000, help="Maximum logs to fetch")
    parser.add_argument("--delay", type=float, default=1.0, help="Request delay in seconds")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retries on failure")
    parser.add_argument("--skip-failed-pages", action="store_true", help="Skip failed pages")
    parser.add_argument("--output", "-o", help="Output file (default: cache/network_logs.json)")
    parser.add_argument("--force", "-f", action="store_true", help="Force refresh cache")
    
    # Inspect options
    parser.add_argument("--path", help="Path pattern to filter (for --inspect)")
    parser.add_argument("--method", help="HTTP method to filter (for --inspect)")
    parser.add_argument("--host", help="Host to filter (for --inspect)")
    
    args = parser.parse_args()
    
    # Ensure environment is loaded and show which one we're using
    env_name = ensure_env()
    print(f"📁 Environment: {env_name}")
    
    cache = DataCache()  # Uses active environment's cache automatically
    print(f"📂 Cache: {cache.cache_dir}")
    
    # Auto-detect mode if not specified
    if not args.fetch and not args.list and not args.inspect:
        # Check if we have cached logs
        cached_logs = cache.load_logs_cache()
        if cached_logs:
            # Use inspect mode with cached logs
            args.inspect = True
            print("ℹ️  Using cached logs (use --fetch to refresh)")
        else:
            # No cache - fetch first
            args.fetch = True
            print("ℹ️  No cached logs found, fetching from API...")
    
    if args.list:
        # Show cache status
        cache.display_cache_status()
        
        logs = cache.load_logs_cache()
        if logs:
            display_logs_stats(logs)
        else:
            print("\n⚠️  No cached logs found. Run with --fetch to fetch logs.")
        
        return 0
    
    if args.inspect:
        # Inspect cached logs
        logs = cache.load_logs_cache()
        if not logs:
            print("❌ No cached logs found. Run with --fetch first.")
            return 1
        
        # Apply filters
        from wdl_common.log_parser import (
            filter_logs_by_host,
            filter_logs_by_method,
            filter_logs_by_path_pattern,
        )
        
        filtered_logs = logs
        
        if args.search:
            # Search filter works on full URL
            filtered_logs = [log for log in filtered_logs if args.search.lower() in log.api_endpoint_url.lower()]
            print(f"Filtered by search '{args.search}': {len(filtered_logs)} logs")
        
        if args.path:
            filtered_logs = filter_logs_by_path_pattern(filtered_logs, args.path)
            print(f"Filtered by path '{args.path}': {len(filtered_logs)} logs")
        
        if args.method:
            filtered_logs = filter_logs_by_method(filtered_logs, [args.method])
            print(f"Filtered by method '{args.method}': {len(filtered_logs)} logs")
        
        if args.host:
            filtered_logs = filter_logs_by_host(filtered_logs, args.host)
            print(f"Filtered by host '{args.host}': {len(filtered_logs)} logs")
        
        display_logs_stats(filtered_logs)
        
        # Show sample entries
        if filtered_logs:
            print("\n📝 Sample entries (first 10):")
            for i, log in enumerate(filtered_logs[:10], 1):
                print(f"  {i}. [{log.method}] {log.api_endpoint_url[:80]}")
        
        return 0
    
    if args.fetch:
        # Check if cache exists and not forcing refresh
        if not args.force and not cache.is_cache_stale('logs', max_age_hours=24):
            print("✅ Using cached logs (use --force to refresh)")
            display_logs_stats(cache.load_logs_cache() or [])
            return 0
        
        # Fetch logs
        print("\n" + "=" * 60)
        print("📡 FETCH HTTP NETWORK LOGS")
        print("=" * 60)
        
        try:
            client = get_client()
            bearer_token = client.bearer_token
            
            logs = fetch_network_logs_from_api(
                bearer_token=bearer_token,
                search=args.search,
                start_date=args.start_date,
                end_date=args.end_date,
                max_logs=args.max_logs,
                request_delay=args.delay,
                max_retries=args.max_retries,
                skip_failed_pages=args.skip_failed_pages,
            )
            
            if logs:
                # Save to cache
                cache.save_logs_cache(logs)
                
                # Also save to output file if specified
                if args.output:
                    save_logs_to_file(logs, args.output)
                
                display_logs_stats(logs)
            else:
                print("⚠️  No logs fetched")
                return 1
            
            return 0
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
            return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())



