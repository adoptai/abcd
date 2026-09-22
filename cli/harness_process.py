#!/usr/bin/env python3
"""
CRUD a "Process" -- what the Adopt webui's Agents tab actually edits under
the hood (table db_org_process, router adoptwebui/backend/app/routes/
process.py, prefix /v1/org/processes). Lets you create/update/delete an
agent from a terminal loop instead of only through the Agent builder UI.

Every write route (create/patch/delete/set-workstreams/run) is admin-gated
server-side (require_admin: 'Admin' in the caller's Frontegg JWT roles[]) --
the PAT behind --env's workspaces/{env}/.env must belong to an admin user or
every write here 403s. list/get work for any PAT.

A skill/plugin is attached by name, not by id: components is a list of
{"type": "skill"|"plugin", "source": "org", "ref": <skill name or plugin
slug>}, resolved against the org's skill/plugin catalog at read time (see
adoptwebui/docs/agent-harness/processes.md). Push the skill first via
cli/harness_skill.py push, then reference its frontmatter name here.

delete is soft-delete only -- there is no hard delete on this resource.

Usage:
    python cli/harness_process.py list [--env ENV]
    python cli/harness_process.py get <process_id> [--env ENV]
    python cli/harness_process.py create <name> <display_name> [--description TEXT]
        [--component TYPE:REF ...] [--instructions TEXT] [--enable] [--env ENV]
    python cli/harness_process.py update <process_id> [--display-name TEXT]
        [--description TEXT] [--component TYPE:REF ...] [--instructions TEXT]
        [--enable] [--disable] [--env ENV]
    python cli/harness_process.py delete <process_id> [--env ENV]
    python cli/harness_process.py get-workstreams <process_id> [--env ENV]
    python cli/harness_process.py set-workstreams <process_id> <workstream_id> [<workstream_id> ...] [--env ENV]
    python cli/harness_process.py run <process_id> [--env ENV]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env


def _parse_components(raw: list[str] | None) -> list[dict[str, str]]:
    """--component skill:my-skill-name or --component plugin:my-plugin-slug"""
    components = []
    for item in raw or []:
        if ":" not in item:
            raise ValueError(f"--component must be TYPE:REF (e.g. skill:my-skill), got {item!r}")
        type_, ref = item.split(":", 1)
        if type_ not in ("skill", "plugin"):
            raise ValueError(f"--component type must be 'skill' or 'plugin', got {type_!r}")
        components.append({"type": type_, "source": "org", "ref": ref})
    return components


def _print_forbidden_hint(e: HarnessAPIError) -> None:
    if e.status_code == 403:
        print(
            "   Process writes are admin-only -- the account behind your PAT must have the "
            "Admin role in Frontegg. Ask an org admin to mint the PAT, or grant that role."
        )


def cmd_list(env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
        result = client.list_processes()
    except (ValueError, HarnessAPIError) as e:
        print(f"❌ {e}")
        return 1
    processes = result.get("processes", result)
    print(json.dumps(processes, indent=2))
    return 0


def cmd_get(process_id: str, env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
        result = client.get_process(process_id)
    except (ValueError, HarnessAPIError) as e:
        print(f"❌ {e}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_create(
    name: str,
    display_name: str,
    description: str | None,
    components: list[str] | None,
    instructions: str | None,
    enable: bool,
    env: str | None,
) -> int:
    try:
        parsed_components = _parse_components(components)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"🆕 Creating process '{name}' ({display_name})...")
    try:
        result = client.create_process(
            name=name,
            display_name=display_name,
            description=description,
            components=parsed_components,
            agent_instructions=instructions,
            is_enabled=enable,
        )
    except HarnessAPIError as e:
        print(f"❌ Create failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_update(
    process_id: str,
    display_name: str | None,
    description: str | None,
    components: list[str] | None,
    instructions: str | None,
    enable: bool,
    disable: bool,
    env: str | None,
) -> int:
    if enable and disable:
        print("❌ --enable and --disable are mutually exclusive")
        return 1

    try:
        parsed_components = _parse_components(components) if components is not None else None
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    fields: dict = {}
    if display_name is not None:
        fields["display_name"] = display_name
    if description is not None:
        fields["description"] = description
    if parsed_components is not None:
        fields["components"] = parsed_components
    if instructions is not None:
        fields["agent_instructions"] = instructions
    if enable:
        fields["is_enabled"] = True
    if disable:
        fields["is_enabled"] = False

    if not fields:
        print("❌ Nothing to update -- pass at least one of --display-name/--description/"
              "--component/--instructions/--enable/--disable")
        return 1

    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"✏️  Updating process {process_id}: {list(fields.keys())}")
    try:
        result = client.update_process(process_id, **fields)
    except HarnessAPIError as e:
        print(f"❌ Update failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_delete(process_id: str, env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"🗑️  Soft-deleting process {process_id}...")
    try:
        client.delete_process(process_id)
    except HarnessAPIError as e:
        print(f"❌ Delete failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print("   ✅ Deleted (soft -- is_deleted=True; no hard delete exists for this resource)")
    return 0


def cmd_get_workstreams(process_id: str, env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
        result = client.get_process_workstreams(process_id)
    except (ValueError, HarnessAPIError) as e:
        print(f"❌ {e}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_set_workstreams(process_id: str, workstream_ids: list[str], env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"🔗 Setting workstreams for process {process_id}: {workstream_ids}")
    try:
        result = client.set_process_workstreams(process_id, workstream_ids)
    except HarnessAPIError as e:
        print(f"❌ Set-workstreams failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_run(process_id: str, env: str | None) -> int:
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"▶️  Running process {process_id} (builder test-run -- fans out to all assigned "
          "workstreams)...")
    try:
        result = client.run_process(process_id)
    except HarnessAPIError as e:
        print(f"❌ Run failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List processes (agents) in the org")
    list_p.add_argument("--env", help="Environment to use (defaults to active env)")

    get_p = sub.add_parser("get", help="Fetch one process by id")
    get_p.add_argument("process_id")
    get_p.add_argument("--env", help="Environment to use (defaults to active env)")

    create_p = sub.add_parser("create", help="Create a new process (agent)")
    create_p.add_argument("name", help="Stable slug, unique per org, immutable after creation")
    create_p.add_argument("display_name", help="Customer-facing name")
    create_p.add_argument("--description")
    create_p.add_argument(
        "--component", dest="components", action="append",
        help="TYPE:REF, e.g. skill:email-sorting-plugin -- repeatable",
    )
    create_p.add_argument("--instructions", help="Orchestration prompt (agent_instructions)")
    create_p.add_argument("--enable", action="store_true", help="Create as Live, not Draft")
    create_p.add_argument("--env", help="Environment to use (defaults to active env)")

    update_p = sub.add_parser("update", help="Patch an existing process")
    update_p.add_argument("process_id")
    update_p.add_argument("--display-name")
    update_p.add_argument("--description")
    update_p.add_argument(
        "--component", dest="components", action="append",
        help="TYPE:REF -- repeatable; passing any replaces the FULL components list",
    )
    update_p.add_argument("--instructions")
    update_p.add_argument("--enable", action="store_true")
    update_p.add_argument("--disable", action="store_true")
    update_p.add_argument("--env", help="Environment to use (defaults to active env)")

    delete_p = sub.add_parser("delete", help="Soft-delete a process")
    delete_p.add_argument("process_id")
    delete_p.add_argument("--env", help="Environment to use (defaults to active env)")

    gws_p = sub.add_parser("get-workstreams", help="List workstreams assigned to a process")
    gws_p.add_argument("process_id")
    gws_p.add_argument("--env", help="Environment to use (defaults to active env)")

    sws_p = sub.add_parser(
        "set-workstreams", help="Full replace of a process's assigned workstreams"
    )
    sws_p.add_argument("process_id")
    sws_p.add_argument("workstream_ids", nargs="+")
    sws_p.add_argument("--env", help="Environment to use (defaults to active env)")

    run_p = sub.add_parser("run", help="Builder test-run across all assigned workstreams")
    run_p.add_argument("process_id")
    run_p.add_argument("--env", help="Environment to use (defaults to active env)")

    args = parser.parse_args()

    if args.command == "list":
        sys.exit(cmd_list(args.env))
    elif args.command == "get":
        sys.exit(cmd_get(args.process_id, args.env))
    elif args.command == "create":
        sys.exit(
            cmd_create(
                args.name, args.display_name, args.description, args.components,
                args.instructions, args.enable, args.env,
            )
        )
    elif args.command == "update":
        sys.exit(
            cmd_update(
                args.process_id, args.display_name, args.description, args.components,
                args.instructions, args.enable, args.disable, args.env,
            )
        )
    elif args.command == "delete":
        sys.exit(cmd_delete(args.process_id, args.env))
    elif args.command == "get-workstreams":
        sys.exit(cmd_get_workstreams(args.process_id, args.env))
    elif args.command == "set-workstreams":
        sys.exit(cmd_set_workstreams(args.process_id, args.workstream_ids, args.env))
    elif args.command == "run":
        sys.exit(cmd_run(args.process_id, args.env))


if __name__ == "__main__":
    main()
