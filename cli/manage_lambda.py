#!/usr/bin/env python3
"""
Manage lambda workspaces (create, list, show).

Features:
- Create: manage_lambda.py --create my-lambda
- List:   manage_lambda.py --list
- Show:   manage_lambda.py --show my-lambda
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new lambda workspace."""
    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            return 1

    name = args.create
    language = args.language or "python"
    agent = args.agent

    print(f"Creating lambda workspace: {name}")
    if agent:
        print(f"  Agent: {agent}")
    print(f"  Language: {language}")

    try:
        ctx = manager.create_lambda(name=name, agent_name=agent, language=language)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"  Path: {ctx.path}")
    print(f"Created lambda workspace '{name}'")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all lambdas in current env."""
    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            return 1

    lambdas = manager.list_lambdas(agent_name=args.agent)

    if not lambdas:
        print("No lambda workspaces found.")
        return 0

    print(f"Lambda workspaces ({len(lambdas)}):")
    for ctx in lambdas:
        lambda_id = ctx.metadata.get("lambda_id")
        agent_info = f" [agent: {ctx.agent_name}]" if ctx.agent_name else ""
        id_info = f" (id: {lambda_id})" if lambda_id else " (not linked)"
        language = ctx.lambda_json.get("language", "python")
        print(f"  {ctx.name}{agent_info} [{language}]{id_info}")

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show lambda details."""
    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            return 1

    ctx = manager.find_lambda(args.show)
    if not ctx:
        print(f"ERROR: Lambda '{args.show}' not found", file=sys.stderr)
        return 1

    lambda_id = ctx.metadata.get("lambda_id")

    print(f"Lambda: {ctx.name}")
    print(f"  Path     : {ctx.path}")
    print(f"  Env      : {ctx.env_name}")
    print(f"  Agent    : {ctx.agent_name or '(env-level)'}")
    print(f"  Language : {ctx.lambda_json.get('language', 'python')}")
    print(f"  Lambda ID: {lambda_id or '(not linked)'}")

    if ctx.source_files:
        print(f"  Files    : {', '.join(ctx.source_files)}")

    if lambda_id and not args.local_only:
        client = get_api_client_for_env()
        success, data, msg = client.get_lambda(lambda_id)
        if success and data:
            print("\nRemote details:")
            print(f"  Status   : {data.get('status', 'unknown')}")
            description = data.get("description", "")
            if description:
                print(f"  Desc     : {description}")
        else:
            print(f"\n  (Remote fetch failed: {msg})")

    return 0


def _lookup_lambda_by_name(client: AdoptAPIClient, name: str) -> tuple[str | None, str]:
    """Return (lambda_id, error_message). lambda_id is None on failure."""
    success, lambdas, msg = client.list_lambdas(search=name, page_size=100)
    if not success or lambdas is None:
        return None, f"Failed to list lambdas: {msg}"
    matches = [lam for lam in lambdas if lam.get("name") == name]
    if not matches:
        return None, f"Lambda '{name}' not found in remote"
    return matches[0]["id"], ""


def cmd_update(args: argparse.Namespace) -> int:
    """Update a lambda's metadata."""
    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            return 1

    name = args.update
    ctx = manager.find_lambda(name)
    lambda_id = ctx.metadata.get("lambda_id") if ctx else None

    if not lambda_id:
        # Fall back to remote lookup by name
        client = get_api_client_for_env()
        lambda_id, err = _lookup_lambda_by_name(client, name)
        if not lambda_id:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
    else:
        client = get_api_client_for_env()

    fields: dict = {}
    if args.image is not None:
        fields["image"] = args.image
    if args.cpu is not None:
        fields["cpu"] = args.cpu
    if args.memory is not None:
        fields["memory"] = args.memory
    if args.timeout is not None:
        fields["timeout"] = args.timeout
    if args.entry_point is not None:
        fields["entry_point"] = args.entry_point
    if args.description is not None:
        fields["description"] = args.description

    if not fields:
        print(
            "ERROR: No fields to update. Provide at least one of: --image, --cpu, --memory, --timeout, --entry-point, --description",
            file=sys.stderr,
        )
        return 1

    print(f"Updating lambda '{name}' (id: {lambda_id})")
    for k, v in fields.items():
        print(f"  {k}: {v}")

    success, data, msg = client.update_lambda(lambda_id, **fields)
    if not success:
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    print(f"Lambda '{name}' updated successfully")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a lambda."""
    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            return 1

    name = args.delete
    ctx = manager.find_lambda(name)
    lambda_id = ctx.metadata.get("lambda_id") if ctx else None

    if not lambda_id:
        client = get_api_client_for_env()
        lambda_id, err = _lookup_lambda_by_name(client, name)
        if not lambda_id:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
    else:
        client = get_api_client_for_env()

    print(f"Lambda to delete: {name} (id: {lambda_id})")
    answer = input("Are you sure? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return 0

    success, msg = client.delete_lambda(lambda_id)
    if not success:
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    print(f"Lambda '{name}' deleted successfully")
    return 0


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage lambda workspaces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a lambda workspace
  python manage_lambda.py --create my-lambda

  # Create scoped to an agent
  python manage_lambda.py --create my-lambda --agent my-agent

  # Create a JavaScript lambda
  python manage_lambda.py --create my-lambda --language javascript

  # List all lambdas
  python manage_lambda.py --list

  # Show details for a lambda
  python manage_lambda.py --show my-lambda

  # Update a lambda's CPU and memory
  python manage_lambda.py --update my-lambda --cpu 512 --memory 1024

  # Delete a lambda (with confirmation)
  python manage_lambda.py --delete my-lambda
        """,
    )

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--create", metavar="NAME", help="Create a new lambda workspace")
    action_group.add_argument("--list", action="store_true", help="List all lambda workspaces")
    action_group.add_argument("--show", metavar="NAME", help="Show lambda details")
    action_group.add_argument("--update", metavar="NAME", help="Update a lambda's metadata")
    action_group.add_argument(
        "--delete", metavar="NAME", help="Delete a lambda (with confirmation)"
    )

    parser.add_argument("--agent", "-a", help="Agent name (scope lambda to agent)")
    parser.add_argument(
        "--language",
        "-l",
        choices=["python", "javascript"],
        default="python",
        help="Language for new lambda (default: python)",
    )
    parser.add_argument("--env", "-e", help="Environment to use")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip remote API call when showing details",
    )

    # --update optional field flags
    parser.add_argument("--image", help="Container image (for --update)")
    parser.add_argument("--cpu", type=int, help="CPU units (for --update)")
    parser.add_argument("--memory", type=int, help="Memory in MB (for --update)")
    parser.add_argument("--timeout", type=int, help="Timeout in seconds (for --update)")
    parser.add_argument("--entry-point", dest="entry_point", help="Entry point (for --update)")
    parser.add_argument("--description", help="Description (for --update)")

    args = parser.parse_args()

    if args.create:
        sys.exit(cmd_create(args))
    elif args.list:
        sys.exit(cmd_list(args))
    elif args.show:
        sys.exit(cmd_show(args))
    elif args.update:
        sys.exit(cmd_update(args))
    elif args.delete:
        sys.exit(cmd_delete(args))


if __name__ == "__main__":
    main()
