#!/usr/bin/env python3
"""
Discovery CLI - Search for actions and APIs.

Usage:
    # Semantic search (for requirements-based discovery)
    python cli/discover.py --actions "inventory management system"
    python cli/discover.py --apis "user authentication endpoint"

    # Fuzzy search (for finding specific names)
    python cli/discover.py --actions "get-orderpoints" --mode fuzzy

    # Hybrid (both semantic + fuzzy)
    python cli/discover.py --apis "orderpoints" --mode hybrid

    # Tools only
    python cli/discover.py --actions "inventory" --tools-only

    # From requirements file
    python cli/discover.py --requirements requirements.md

    # Specify environment for cache
    python cli/discover.py --actions "query" --env staging
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.discovery import Discovery, get_discovery


def main():
    parser = argparse.ArgumentParser(
        description="Search for actions and APIs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Semantic search for actions matching a description
  python cli/discover.py --actions "inventory management"

  # Fuzzy search for a specific action name
  python cli/discover.py --actions "get-orderpoints" --mode fuzzy

  # Search APIs
  python cli/discover.py --apis "authentication endpoint"

  # Discover for requirements file
  python cli/discover.py --requirements requirements.md

  # Tools only, hybrid mode
  python cli/discover.py --actions "fetch data" --tools-only --mode hybrid
        """,
    )

    # Search targets
    parser.add_argument(
        "--actions",
        type=str,
        metavar="QUERY",
        help="Search for actions matching query",
    )
    parser.add_argument(
        "--apis",
        type=str,
        metavar="QUERY",
        help="Search for APIs matching query",
    )
    parser.add_argument(
        "--requirements",
        "-r",
        type=str,
        metavar="FILE",
        help="Discover actions/APIs for requirements file",
    )

    # Search options
    parser.add_argument(
        "--mode",
        "-m",
        choices=["semantic", "fuzzy", "hybrid"],
        default="hybrid",
        help="Search mode: semantic (FAISS), fuzzy (text), hybrid (both). Default: hybrid",
    )
    parser.add_argument(
        "--tools-only",
        action="store_true",
        help="Only return tool-type actions",
    )
    parser.add_argument(
        "--top",
        "-k",
        type=int,
        default=10,
        help="Maximum results to return (default: 10)",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=0.4,
        help="Minimum fuzzy match threshold 0-1 (default: 0.4)",
    )

    # Environment
    parser.add_argument(
        "--env",
        "-e",
        type=str,
        help="Environment name for cache location",
    )

    # Output options
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Fetch full details for each result",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh cache and re-embed all items",
    )

    args = parser.parse_args()

    # Validate arguments
    if not any([args.actions, args.apis, args.requirements]):
        parser.print_help()
        print("\n❌ Error: Specify --actions, --apis, or --requirements")
        sys.exit(1)

    # Get discovery instance
    discovery = get_discovery(args.env)

    results = {"actions": [], "apis": []}

    # Handle requirements file
    if args.requirements:
        req_path = Path(args.requirements)
        if not req_path.exists():
            print(f"❌ Requirements file not found: {req_path}")
            sys.exit(1)

        requirements_text = req_path.read_text()
        print(f"📄 Discovering for requirements: {req_path.name}")

        results = discovery.discover_for_requirements(
            requirements_text,
            include_actions=True,
            include_apis=True,
            tools_only=args.tools_only,
            top_k=args.top,
        )

    else:
        # Search actions
        if args.actions:
            if args.refresh:
                discovery.fetch_actions(tools_only=args.tools_only, force_refresh=True)

            success, actions, msg = discovery.search_actions(
                args.actions,
                mode=args.mode,
                tools_only=args.tools_only,
                top_k=args.top,
                fuzzy_threshold=args.threshold,
            )

            if not success:
                print(f"❌ {msg}")
                sys.exit(1)

            results["actions"] = actions

        # Search APIs
        if args.apis:
            if args.refresh:
                discovery.fetch_apis(force_refresh=True)

            success, apis, msg = discovery.search_apis(
                args.apis,
                mode=args.mode,
                top_k=args.top,
                fuzzy_threshold=args.threshold,
            )

            if not success:
                print(f"❌ {msg}")
                sys.exit(1)

            results["apis"] = apis

    # Fetch details if requested
    if args.details:
        for action in results.get("actions", []):
            action_id = action.get("id")
            if action_id:
                success, details, _ = discovery.get_action_details(action_id)
                if success and details:
                    action.update(details)

        for api in results.get("apis", []):
            api_id = api.get("id")
            if api_id:
                success, details, _ = discovery.get_api_details(api_id)
                if success and details:
                    api.update(details)

    # Output
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        # Pretty print
        if results.get("actions"):
            print("\n" + "=" * 70)
            print(f"🔧 ACTIONS ({len(results['actions'])} results)")
            print("=" * 70)
            for i, action in enumerate(results["actions"], 1):
                score = action.get("_search_score", "N/A")
                title = action.get("title", "Untitled")
                action_id = action.get("id", "")[:20]
                exec_type = action.get("execution_type", "")
                desc = (action.get("description") or "")[:60]
                print(f"\n{i}. {title} [{score}%]")
                print(f"   ID: {action_id}... | Type: {exec_type}")
                if desc:
                    print(f"   {desc}...")

        if results.get("apis"):
            print("\n" + "=" * 70)
            print(f"🌐 APIs ({len(results['apis'])} results)")
            print("=" * 70)
            for i, api in enumerate(results["apis"], 1):
                score = api.get("_search_score", "N/A")
                name = api.get("name", "Untitled")
                method = api.get("method", "")
                path = api.get("path", "")[:40]
                desc = (api.get("description") or "")[:60]
                print(f"\n{i}. {name} [{score}%]")
                print(f"   {method} {path}")
                if desc:
                    print(f"   {desc}...")

        if not results.get("actions") and not results.get("apis"):
            print("\n⚠️  No results found")

        print()


if __name__ == "__main__":
    main()


