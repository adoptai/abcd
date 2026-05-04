#!/usr/bin/env python3
"""
Browse lambda execution logs.

Features:
- List recent executions: lambda_logs.py my-lambda
- Get full log for a specific execution: lambda_logs.py my-lambda --execution-id <id>
- Limit results: lambda_logs.py my-lambda --limit 5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Browse lambda execution logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List recent executions (default)
  python lambda_logs.py my-lambda

  # Limit to 5 most recent
  python lambda_logs.py my-lambda --limit 5

  # Get full log for a specific execution
  python lambda_logs.py my-lambda --execution-id abc-123-def
        """,
    )

    parser.add_argument("name", help="Lambda name")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--list",
        action="store_true",
        default=True,
        help="List recent executions (default)",
    )
    mode_group.add_argument(
        "--execution-id",
        metavar="ID",
        help="Get full log for a specific execution",
    )

    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=20,
        help="Number of executions to show (default: 20)",
    )
    parser.add_argument("--env", "-e", help="Environment to use")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show verbose output")

    args = parser.parse_args()

    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            sys.exit(1)

    ctx = manager.find_lambda(args.name)
    if not ctx:
        print(f"ERROR: Lambda '{args.name}' not found", file=sys.stderr)
        sys.exit(1)

    lambda_id = ctx.metadata.get("lambda_id")
    if not lambda_id:
        print(
            "ERROR: Lambda is not linked to remote (no lambda_id). Run save_lambda.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = get_api_client_for_env()

    if args.execution_id:
        print(f"Lambda       : {ctx.name}")
        print(f"Execution ID : {args.execution_id}")
        print("\nFetching execution log...")

        success, log_data, msg = client.get_lambda_execution_log(
            lambda_id=lambda_id,
            execution_id=args.execution_id,
        )
        if not success:
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)

        if log_data:
            print(json.dumps(log_data, indent=2))
        else:
            print("(No log data returned)")

    else:
        # Default: list executions
        print(f"Lambda    : {ctx.name}")
        print(f"Lambda ID : {lambda_id}")
        print(f"\nRecent executions (limit={args.limit}):")

        success, executions, msg = client.list_lambda_executions(
            lambda_id=lambda_id,
            page_size=args.limit,
        )
        if not success:
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)

        if not executions:
            print("  (no executions found)")
        else:
            for ex in executions[: args.limit]:
                exec_id = ex.get("id") or ex.get("execution_id", "?")
                status = ex.get("status", "unknown")
                exit_code = ex.get("exit_code")
                started = ex.get("started_at") or ex.get("created_at", "")
                duration = ex.get("duration_ms")
                date_str = f" ({started[:19]})" if started else ""
                dur_str = f" {duration}ms" if duration is not None else ""
                # Surface non-zero exits — "completed" can mean exited with code 1.
                # Falls back to status label if exit_code isn't returned by the summary endpoint.
                if exit_code is not None and exit_code != 0:
                    status_str = f"[failed:{exit_code}]"
                elif exit_code == 0 and status == "completed":
                    status_str = "[completed]"
                else:
                    status_str = f"[{status}]"
                print(f"  {exec_id}  {status_str}{date_str}{dur_str}")

            print(f"\n  Total shown: {min(len(executions), args.limit)}")
            print("  Use --execution-id <id> to get the full log for an execution.")

    sys.exit(0)


if __name__ == "__main__":
    main()
