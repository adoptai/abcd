#!/usr/bin/env python3
"""
List pipeline workspaces in the active environment.

Usage:
    python cli/list_pipelines.py              # local workspaces only
    python cli/list_pipelines.py --remote     # fetch from platform
    python cli/list_pipelines.py --json       # JSON output
    python cli/list_pipelines.py --state draft|running|paused
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import ensure_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def _state_icon(state: str | None) -> str:
    icons = {
        "local": "🏠",
        "draft": "📝",
        "published": "✅",
        "running": "🟢",
        "paused": "⏸",
        "error": "🔴",
    }
    return icons.get(state or "local", "❓")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List pipeline workspaces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli/list_pipelines.py
  python cli/list_pipelines.py --remote
  python cli/list_pipelines.py --state running
  python cli/list_pipelines.py --json
        """,
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Also fetch pipelines from the platform (requires linked remote)",
    )
    parser.add_argument(
        "--state",
        choices=["draft", "running", "paused", "error", "local"],
        help="Filter by state",
    )
    parser.add_argument("--search", help="Filter by name (substring match)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    env_name = ensure_env()
    manager = get_workspace_manager()

    pipelines = manager.list_pipeline_workspaces(env_name)

    # Apply local filters
    if args.state:
        pipelines = [p for p in pipelines if p.get("state") == args.state]
    if args.search:
        search_lower = args.search.lower()
        pipelines = [
            p
            for p in pipelines
            if search_lower in p.get("name", "").lower()
            or search_lower in p.get("pipeline_id", "").lower()
        ]

    if args.remote:
        try:
            from cli.wdl_common.pipeline_client import get_pipeline_client

            client = get_pipeline_client()
            remote_pipelines = client.list_pipelines()
            remote_list = remote_pipelines if isinstance(remote_pipelines, list) else remote_pipelines.get("items", [])

            # Build index of local pipelines by remote_pipeline_id
            local_by_remote_id: dict[str, dict] = {
                p["remote_pipeline_id"]: p
                for p in pipelines
                if p.get("remote_pipeline_id")
            }

            # Merge remote info into local pipelines or add remote-only entries
            remote_only = []
            for rp in remote_list:
                rid = rp.get("id")
                if rid in local_by_remote_id:
                    local_by_remote_id[rid]["remote_state"] = rp.get("state")
                    local_by_remote_id[rid]["remote_last_test"] = rp.get("last_test_run_status")
                else:
                    remote_only.append(
                        {
                            "pipeline_id": rp.get("name", rid),
                            "name": rp.get("name", rid),
                            "remote_pipeline_id": rid,
                            "state": rp.get("state"),
                            "remote_state": rp.get("state"),
                            "path": None,
                            "_remote_only": True,
                        }
                    )
        except Exception as exc:
            print(f"⚠️  Could not fetch remote pipelines: {exc}", file=sys.stderr)
            remote_only = []
    else:
        remote_only = []

    all_pipelines = pipelines + remote_only

    if args.json:
        out = []
        for p in all_pipelines:
            entry = {k: v for k, v in p.items() if k != "path"}
            if p.get("path"):
                entry["path"] = str(p["path"])
            out.append(entry)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if not all_pipelines:
        msg = f"No pipeline workspaces found in environment '{env_name}'"
        if args.state:
            msg += f" with state='{args.state}'"
        print(msg)
        print(f"\nCreate one with: python cli/manage_pipeline.py --create -t \"My Pipeline\"")
        return 0

    print(f"\n📦 Pipelines in environment: {env_name}  ({len(all_pipelines)} found)\n")
    print(f"  {'ID':<30}  {'STATE':<12}  {'REMOTE ID':<38}  NAME")
    print(f"  {'-'*30}  {'-'*12}  {'-'*38}  {'-'*30}")

    for p in all_pipelines:
        pid = p.get("pipeline_id", "?")[:30]
        state = p.get("state") or "local"
        remote_id = (p.get("remote_pipeline_id") or "—")[:38]
        name = p.get("name", pid)[:40]
        remote_note = " [remote-only]" if p.get("_remote_only") else ""
        icon = _state_icon(state)
        remote_state = p.get("remote_state")
        state_display = f"{icon} {state}"
        if remote_state and remote_state != state:
            state_display += f" ({remote_state})"
        print(f"  {pid:<30}  {state_display:<20}  {remote_id:<38}  {name}{remote_note}")

    print()
    print("Commands:")
    print("  python cli/save_pipeline_draft.py <id>    – push WDL to platform")
    print("  python cli/test_pipeline.py <id>          – run test")
    print("  python cli/publish_pipeline.py <id>       – publish")
    print("  python cli/manage_pipeline.py --show <id> – show details")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
