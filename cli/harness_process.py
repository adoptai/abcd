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
abcd skill push, then reference its frontmatter name here.

delete is soft-delete only -- there is no hard delete on this resource.

Usage:
    abcd agent list [--env ENV]
    abcd agent get <process_id> [--env ENV]
    abcd agent create <name> <display_name> [--description TEXT]
        [--component TYPE:REF ...] [--instructions TEXT] [--enable] [--env ENV]
    abcd agent update <process_id> [--display-name TEXT]
        [--description TEXT] [--component TYPE:REF ...] [--instructions TEXT]
        [--enable] [--disable] [--env ENV]
    abcd agent delete <process_id> [--env ENV]
    abcd agent workstreams <process_id> [--env ENV]
    abcd agent set-workstreams <process_id> <workstream_id> [<workstream_id> ...] [--env ENV]
    abcd agent run <process_id> [--env ENV]
    (the old `python cli/harness_process.py <command> ...` form still works; get-workstreams
    is `abcd agent workstreams`)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.core.registry import Registry, legacy_main
from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

if TYPE_CHECKING:
    from cli.core.context import Context


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
        print(
            "❌ Nothing to update -- pass at least one of --display-name/--description/"
            "--component/--instructions/--enable/--disable"
        )
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

    print(
        f"▶️  Running process {process_id} (builder test-run -- fans out to all assigned "
        "workstreams)..."
    )
    try:
        result = client.run_process(process_id)
    except HarnessAPIError as e:
        print(f"❌ Run failed: {e}")
        _print_forbidden_hint(e)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _cmd_list(ctx: Context) -> int:
    return cmd_list(ctx.env_arg)


def _cmd_get(ctx: Context) -> int:
    return cmd_get(ctx.args.process_id, ctx.env_arg)


def _cmd_create(ctx: Context) -> int:
    a = ctx.args
    if ctx.dry_run_notice(f"would create process '{a.name}' ({a.display_name})"):
        return 0
    return cmd_create(
        a.name, a.display_name, a.description, a.components, a.instructions, a.enable, a.env
    )


def _cmd_update(ctx: Context) -> int:
    a = ctx.args
    if ctx.dry_run_notice(f"would update process {a.process_id}"):
        return 0
    return cmd_update(
        a.process_id,
        a.display_name,
        a.description,
        a.components,
        a.instructions,
        a.enable,
        a.disable,
        a.env,
    )


def _cmd_delete(ctx: Context) -> int:
    if ctx.dry_run_notice(f"would soft-delete process {ctx.args.process_id}"):
        return 0
    return cmd_delete(ctx.args.process_id, ctx.env_arg)


def _cmd_get_workstreams(ctx: Context) -> int:
    return cmd_get_workstreams(ctx.args.process_id, ctx.env_arg)


def _cmd_set_workstreams(ctx: Context) -> int:
    a = ctx.args
    if ctx.dry_run_notice(f"would set workstreams of {a.process_id} to {a.workstream_ids}"):
        return 0
    return cmd_set_workstreams(a.process_id, a.workstream_ids, a.env)


def _cmd_run(ctx: Context) -> int:
    if ctx.dry_run_notice(f"would test-run process {ctx.args.process_id}"):
        return 0
    return cmd_run(ctx.args.process_id, ctx.env_arg)


def register(registry: Registry) -> None:
    """Register ``abcd agent ...`` (and the flat ``harness_process.py`` CLI)."""
    group = registry.group("agent", "Agents (processes) in the org")

    group.command("list", _cmd_list, help="List processes (agents) in the org", legacy_name="list")

    p = group.command("get", _cmd_get, help="Fetch one process by id", legacy_name="get")
    p.add_argument("process_id")

    p = group.command(
        "create",
        _cmd_create,
        help="Create a new process (agent)",
        writes=True,
        legacy_name="create",
    )
    p.add_argument("name", help="Stable slug, unique per org, immutable after creation")
    p.add_argument("display_name", help="Customer-facing name")
    p.add_argument("--description")
    p.add_argument(
        "--component",
        dest="components",
        action="append",
        help="TYPE:REF, e.g. skill:email-sorting-plugin -- repeatable",
    )
    p.add_argument("--instructions", help="Orchestration prompt (agent_instructions)")
    p.add_argument("--enable", action="store_true", help="Create as Live, not Draft")

    p = group.command(
        "update", _cmd_update, help="Patch an existing process", writes=True, legacy_name="update"
    )
    p.add_argument("process_id")
    p.add_argument("--display-name")
    p.add_argument("--description")
    p.add_argument(
        "--component",
        dest="components",
        action="append",
        help="TYPE:REF -- repeatable; passing any replaces the FULL components list",
    )
    p.add_argument("--instructions")
    p.add_argument("--enable", action="store_true")
    p.add_argument("--disable", action="store_true")

    p = group.command(
        "delete", _cmd_delete, help="Soft-delete a process", writes=True, legacy_name="delete"
    )
    p.add_argument("process_id")

    p = group.command(
        "workstreams",
        _cmd_get_workstreams,
        help="List workstreams assigned to a process",
        legacy_name="get-workstreams",
    )
    p.add_argument("process_id")

    p = group.command(
        "set-workstreams",
        _cmd_set_workstreams,
        help="Full replace of a process's assigned workstreams",
        writes=True,
        legacy_name="set-workstreams",
    )
    p.add_argument("process_id")
    p.add_argument("workstream_ids", nargs="+")

    p = group.command(
        "run",
        _cmd_run,
        help="Builder test-run across all assigned workstreams",
        writes=True,
        legacy_name="run",
    )
    p.add_argument("process_id")


def main(argv: list[str] | None = None) -> int:
    return legacy_main("harness_process.py", register, __doc__, argv)


if __name__ == "__main__":
    sys.exit(main())
