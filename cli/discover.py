#!/usr/bin/env python3
"""
Discovery CLI - List and search for actions and APIs.

Uses the ACTIVE ENVIRONMENT for credentials and caching.
Switch environment with: python cli/workspace.py env use <env-id>

Usage:
    # List all (by type)
    python cli/discover.py --list-tools            # Tools only (execution_type=TOOL)
    python cli/discover.py --list-all              # All actions (no filter)
    python cli/discover.py --list-workflows        # Workflows only (execution_type=WORKFLOW)
    python cli/discover.py --list-apis             # All APIs

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

    # Verbose debugging
    python cli/discover.py --list-tools --verbose
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.discovery import get_discovery

# Global verbose flag
_verbose = False


def _verbose_print(func_name: str, stage: str, extra: str = "") -> None:
    """Print verbose message if verbose mode is enabled."""
    if _verbose:
        msg = f"[VERBOSE] {func_name}: {stage}"
        if extra:
            msg += f" - {extra}"
        print(msg, file=sys.stderr)


def main() -> int:
    global _verbose

    parser = argparse.ArgumentParser(
        description="List and search for actions and APIs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all tools
  python cli/discover.py --list-tools

  # List all actions (includes non-tool actions)
  python cli/discover.py --list-all

  # List workflows only
  python cli/discover.py --list-workflows

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

  # Verbose mode for debugging
  python cli/discover.py --list-tools --verbose
        """,
    )

    # List commands (mutually exclusive with search)
    list_group = parser.add_argument_group("List Commands")
    list_group.add_argument(
        "--list-tools",
        action="store_true",
        help="List all tools (execution_type=TOOL)",
    )
    list_group.add_argument(
        "--list-all",
        action="store_true",
        help="List all actions (no type filter, includes hidden)",
    )
    list_group.add_argument(
        "--list-workflows",
        action="store_true",
        help="List all workflows (execution_type=WORKFLOW)",
    )
    list_group.add_argument(
        "--list-apis",
        action="store_true",
        help="List all available APIs",
    )
    list_group.add_argument(
        "--list-uber-agents",
        action="store_true",
        help="List Uber Agents (actions with PROMPT_AND_TOOLS_AGENT)",
    )

    # Search targets
    search_group = parser.add_argument_group("Search Commands")
    search_group.add_argument(
        "--actions",
        type=str,
        metavar="QUERY",
        help="Search for actions matching query",
    )
    search_group.add_argument(
        "--apis",
        type=str,
        metavar="QUERY",
        help="Search for APIs matching query",
    )
    search_group.add_argument(
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

    # NOTE: --env removed - always uses active environment
    # To switch environment: python cli/workspace.py env use <env-id>

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
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debugging output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op for read-only script (accepted for consistency)",
    )

    args = parser.parse_args()

    # Set verbose mode
    if args.verbose:
        _verbose = True
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    # Validate arguments
    has_list_cmd = any(
        [args.list_tools, args.list_all, args.list_workflows, args.list_apis, args.list_uber_agents]
    )
    has_search_cmd = any([args.actions, args.apis, args.requirements])

    if not has_list_cmd and not has_search_cmd:
        parser.print_help()
        print(
            "\n❌ Error: Specify a list command (--list-tools, --list-all, --list-workflows, --list-apis, --list-uber-agents)"
        )
        print("   or a search command (--actions, --apis, --requirements)")
        sys.exit(1)

    _verbose_print("main", "ENTER")

    # Get discovery instance (uses active environment)
    _verbose_print("main", "getting discovery instance")
    discovery = get_discovery(verbose=_verbose)

    results: dict[str, list[Any]] = {"actions": [], "apis": []}

    # Handle list commands
    if has_list_cmd:
        if args.list_tools:
            _verbose_print("main", "listing tools (execution_type=TOOL)")
            print("⏳ Fetching tools...", file=sys.stderr)
            success, items, msg = discovery.fetch_actions(
                execution_type="TOOL", force_refresh=args.refresh
            )
            if not success:
                print(f"❌ {msg}")
                sys.exit(1)
            results["actions"] = items
            _verbose_print("main", f"fetched {len(items)} tools")

        elif args.list_all:
            _verbose_print("main", "listing all actions (including hidden sub-actions)")
            print("⏳ Fetching all actions (including hidden)...", file=sys.stderr)
            success, items, msg = discovery.fetch_actions(
                execution_type=None,
                force_refresh=args.refresh,
                include_hidden=True,  # Fetch hidden sub-actions from Uber Agents
            )
            if not success:
                print(f"❌ {msg}")
                sys.exit(1)
            results["actions"] = items
            _verbose_print("main", f"fetched {len(items)} actions (including hidden)")

        elif args.list_workflows:
            _verbose_print("main", "listing workflows (execution_type=WORKFLOW)")
            print("⏳ Fetching workflows...", file=sys.stderr)
            success, items, msg = discovery.fetch_actions(
                execution_type="WORKFLOW", force_refresh=args.refresh
            )
            if not success:
                print(f"❌ {msg}")
                sys.exit(1)
            results["actions"] = items
            _verbose_print("main", f"fetched {len(items)} workflows")

        elif args.list_apis:
            _verbose_print("main", "listing APIs")
            print("⏳ Fetching APIs...", file=sys.stderr)
            success, items, msg = discovery.fetch_apis(force_refresh=args.refresh)
            if not success:
                print(f"❌ {msg}")
                sys.exit(1)
            results["apis"] = items
            _verbose_print("main", f"fetched {len(items)} APIs")

        elif args.list_uber_agents:
            _verbose_print("main", "listing Uber Agents (PROMPT_AND_TOOLS_AGENT)")
            print("⏳ Fetching Uber Agents (this may take a moment)...", file=sys.stderr)
            success, items, msg = discovery.fetch_uber_agents(force_refresh=args.refresh)
            if not success:
                print(f"❌ {msg}")
                sys.exit(1)
            results["actions"] = items
            _verbose_print("main", f"fetched {len(items)} uber agents")

    # Handle search commands
    elif args.requirements:
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

                # Build status indicators
                status_flags = []
                if action.get("is_uber_agent"):
                    status_flags.append(
                        f"🤖 UBER ({action.get('sub_action_count', 0)} sub-actions)"
                    )
                if action.get("is_subaction"):
                    parent = action.get("parent_agent_title", "unknown")
                    status_flags.append(f"🔗 Sub-action of {parent}")
                if action.get("is_hidden"):
                    status_flags.append("👁️ Hidden")

                status_str = " | ".join(status_flags) if status_flags else ""

                print(f"\n{i}. {title} [{score}%]")
                print(f"   ID: {action_id}... | Type: {exec_type}")
                if status_str:
                    print(f"   {status_str}")
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

    return 0


if __name__ == "__main__":
    main()
