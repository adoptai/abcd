#!/usr/bin/env python3
"""
Playground Profiles & Token Manager CLI.

Manage remote playground profiles and token configs from the command line.

Profile Commands:
    list                          List all playground profiles
    show <id>                     Show a single profile (or --default)
    create                        Create a new playground profile
    edit <id>                     Edit an existing profile
    delete <id>                   Delete a profile
    push [--profile PATH]         Push local adopt_profile.json to remote
    pull [--profile PATH]         Pull remote profiles into local adopt_profile.json
    set-token <id> <key> <token>  Set a security header to a token config name
    show-tokens <id>              Show which headers reference token configs

Token Commands:
    token list                    List all token configs
    token show <id>               Show a single token config
    token create                  Create a new token config
    token edit <id>               Edit an existing token config
    token delete <id>             Delete a token config
    token publish <ids...>        Publish token configs
    token unpublish <ids...>      Unpublish token configs

Examples:
    python cli/playground_profiles.py list
    python cli/playground_profiles.py list --json
    python cli/playground_profiles.py show --default
    python cli/playground_profiles.py create --name "My Profile" --application SalesforceCPQ
    python cli/playground_profiles.py push --dry-run
    python cli/playground_profiles.py pull --dry-run
    python cli/playground_profiles.py token list
    python cli/playground_profiles.py token create --name sid --domain-suffix .force.com --storage-type cookie --cookie-key sid
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import get_client, get_manager
from cli.wdl_common.workspace_manager import WORKSPACES_DIR

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


def _truncate(s: str, max_len: int = 40) -> str:
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def _redact_value(key: str, value: str) -> str:
    """Redact sensitive values for display."""
    sensitive_keywords = (
        "token",
        "cookie",
        "sid",
        "auth",
        "csrf",
        "secret",
        "password",
        "authorization",
    )
    if any(kw in key.lower() for kw in sensitive_keywords):
        if len(value) > 12:
            return value[:6] + "..." + value[-4:]
        return "***"
    return _truncate(value, 50)


def _format_headers(headers: dict[str, Any] | None, indent: int = 6) -> str:
    if not headers:
        return f"{' ' * indent}(none)"
    lines = []
    for k, v in headers.items():
        display_val = _redact_value(k, str(v))
        lines.append(f"{' ' * indent}{k}: {display_val}")
    return "\n".join(lines)


def _parse_key_value(raw: str) -> tuple[str, str]:
    """Parse KEY=VALUE string, raising on invalid format."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"Expected KEY=VALUE format, got: {raw}")
    k, v = raw.split("=", 1)
    return k.strip(), v.strip()


def _display_profile(p: dict[str, Any]) -> None:
    """Display a single profile in detail."""
    default_tag = " ⭐ DEFAULT" if p.get("is_default") else ""
    avail_tag = "" if p.get("is_available", True) else " (disabled)"
    app_tag = f" [{p['application']}]" if p.get("application") else ""

    print(f"  {p['profile_name']}{app_tag}{default_tag}{avail_tag}")
    print(f"     ID:           {p['id']}")
    print(f"     API base:     {p.get('api_base_url', '-')}")
    print(f"     App base:     {p.get('app_base_url', '-')}")

    if p.get("application"):
        print(f"     Application:  {p['application']}")
    if p.get("documented_api_id"):
        print(f"     API ID:       {p['documented_api_id']}")
    if p.get("integration_id"):
        print(f"     Integration:  {p['integration_id']}")

    headers = p.get("security_headers") or {}
    if headers:
        print(f"     Headers ({len(headers)}):")
        print(_format_headers(headers, indent=8))
    else:
        print("     Headers:      (none)")

    props = p.get("user_properties") or {}
    if props:
        print(f"     Properties ({len(props)}):")
        for k, v in props.items():
            print(f"        {k}: {_truncate(str(v), 50)}")

    print()


# =========================================================================
# Profile Commands
# =========================================================================


def cmd_list(args: argparse.Namespace) -> int:
    """List all playground profiles."""
    client = get_client(verbose=_verbose)

    integration_id = getattr(args, "integration_id", None)
    success, profiles, msg = client.list_playground_profiles(integration_id=integration_id)

    if not success:
        print(f"❌ Failed to list profiles: {msg}")
        return 1

    if not profiles:
        if getattr(args, "json_output", False):
            print("[]")
        else:
            print("No playground profiles found.")
        return 0

    available = [p for p in profiles if not p.get("is_deleted", False)]

    if getattr(args, "json_output", False):
        print(json.dumps(available, indent=2))
        return 0

    print(f"\n📋 Found {len(available)} playground profile(s)\n")
    for i, p in enumerate(available, 1):
        default_tag = " ⭐ DEFAULT" if p.get("is_default") else ""
        avail_tag = "" if p.get("is_available", True) else " (disabled)"
        app_tag = f" [{p['application']}]" if p.get("application") else ""

        print(f"  {i}. {p['profile_name']}{app_tag}{default_tag}{avail_tag}")
        print(f"     ID:           {p['id']}")
        print(f"     API base:     {p.get('api_base_url', '-')}")
        print(f"     App base:     {p.get('app_base_url', '-')}")

        if p.get("application"):
            print(f"     Application:  {p['application']}")
        if p.get("documented_api_id"):
            print(f"     API ID:       {p['documented_api_id']}")

        headers = p.get("security_headers") or {}
        if headers:
            print(f"     Headers ({len(headers)}):")
            print(_format_headers(headers, indent=8))
        else:
            print("     Headers:      (none)")

        props = p.get("user_properties") or {}
        if props:
            print(f"     Properties ({len(props)}):")
            for k, v in props.items():
                print(f"        {k}: {_truncate(str(v), 50)}")

        print()

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show a single playground profile."""
    client = get_client(verbose=_verbose)

    if args.default:
        success, profile, msg = client.get_default_playground_profile()
        label = "default profile"
    else:
        if not args.profile_id:
            print("❌ Provide a profile ID or use --default")
            return 1
        success, profile, msg = client.get_playground_profile(args.profile_id)
        label = f"profile {args.profile_id}"

    if not success:
        print(f"❌ Failed to fetch {label}: {msg}")
        return 1

    if not profile:
        print(f"No {label} found.")
        return 0

    if getattr(args, "json_output", False):
        print(json.dumps(profile, indent=2))
        return 0

    print()
    _display_profile(profile)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new playground profile."""
    data: dict[str, Any] = {
        "profile_name": args.name,
        "application": args.application or args.name,
        "api_base_url": args.api_base_url or "",
        "app_base_url": args.app_base_url or args.api_base_url or "",
        "is_default": args.default or False,
    }

    if args.integration_id:
        data["integration_id"] = args.integration_id

    if args.security_header:
        headers = {}
        for raw in args.security_header:
            k, v = _parse_key_value(raw)
            headers[k] = v
        data["security_headers"] = headers

    if args.user_property:
        props = {}
        for raw in args.user_property:
            k, v = _parse_key_value(raw)
            props[k] = v
        data["user_properties"] = props

    client = get_client(verbose=_verbose)
    _vprint(f"Creating profile: {json.dumps(data, indent=2)}")

    success, result, msg = client.create_playground_profile(data)
    if not success:
        print(f"❌ Failed to create profile: {msg}")
        return 1

    new_id = result.get("id", "?") if result else "?"
    print(f"✅ Created profile '{args.name}' (id={new_id})")

    if getattr(args, "json_output", False) and result:
        print(json.dumps(result, indent=2))

    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Edit an existing playground profile."""
    client = get_client(verbose=_verbose)

    success, current, msg = client.get_playground_profile(args.profile_id)
    if not success:
        print(f"❌ Failed to fetch profile: {msg}")
        return 1

    updates: dict[str, Any] = {}

    if args.name:
        updates["profile_name"] = args.name
    if args.api_base_url:
        updates["api_base_url"] = args.api_base_url
    if args.app_base_url:
        updates["app_base_url"] = args.app_base_url
    if args.default is not None:
        updates["is_default"] = args.default

    current_headers = dict(current.get("security_headers") or {}) if current else {}
    headers_changed = False
    if args.set_header:
        for raw in args.set_header:
            k, v = _parse_key_value(raw)
            current_headers[k] = v
            headers_changed = True
    if args.remove_header:
        for k in args.remove_header:
            if k in current_headers:
                del current_headers[k]
                headers_changed = True
    if headers_changed:
        updates["security_headers"] = current_headers

    current_props = dict(current.get("user_properties") or {}) if current else {}
    props_changed = False
    if args.set_property:
        for raw in args.set_property:
            k, v = _parse_key_value(raw)
            current_props[k] = v
            props_changed = True
    if args.remove_property:
        for k in args.remove_property:
            if k in current_props:
                del current_props[k]
                props_changed = True
    if props_changed:
        updates["user_properties"] = current_props

    if not updates:
        print("Nothing to update (no flags provided).")
        return 0

    _vprint(f"Updating profile {args.profile_id}: {list(updates.keys())}")
    success, result, msg = client.update_playground_profile(args.profile_id, updates)
    if not success:
        print(f"❌ Failed to update profile: {msg}")
        return 1

    print(f"✅ Updated profile {args.profile_id}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a playground profile."""
    if not args.force:
        confirm = input(f"Delete profile {args.profile_id}? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return 0

    client = get_client(verbose=_verbose)
    success, msg = client.delete_playground_profile(args.profile_id)
    if not success:
        print(f"❌ Failed to delete profile: {msg}")
        return 1

    print(f"✅ Deleted profile {args.profile_id}")
    return 0


# =========================================================================
# Sync Commands (push / pull)
# =========================================================================


def _find_adopt_profile(args: argparse.Namespace) -> Path | None:
    """Resolve the adopt_profile.json path."""
    profile_path = getattr(args, "profile", None)
    if profile_path:
        path = Path(profile_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.exists():
            return path
        print(f"❌ Profile not found: {path}")
        return None

    manager = get_manager()
    if not manager.active_env:
        print("❌ No active environment and no --profile specified.")
        return None

    env_path = WORKSPACES_DIR / manager.active_env / "adopt_profile.json"
    if env_path.exists():
        return env_path

    print(f"❌ adopt_profile.json not found at: {env_path}")
    return None


def _build_sync_plan(
    local_profile: dict[str, Any],
    remote_profiles: list[dict[str, Any]],
    only_keys: list[str] | None = None,
    exclude_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Build a sync plan by matching local profiles_map entries to remote profiles
    via the 'application' field.
    """
    plan: list[dict[str, Any]] = []

    active_remotes = [p for p in remote_profiles if not p.get("is_deleted", False)]
    remote_by_app: dict[str, dict[str, Any]] = {}
    for rp in active_remotes:
        app = rp.get("application")
        if app:
            remote_by_app[app] = rp

    profiles_map = local_profile.get("profiles_map", {})

    for local_key, local_entry in profiles_map.items():
        if not isinstance(local_entry, dict):
            continue
        if only_keys and local_key not in only_keys:
            continue
        if exclude_keys and local_key in exclude_keys:
            continue

        remote = remote_by_app.get(local_key)
        if not remote:
            _vprint(f"No remote profile with application='{local_key}', will create")
            local_security = local_entry.get("security_params", {})
            plan.append(
                {
                    "action": "create",
                    "local_key": local_key,
                    "data": {
                        "profile_name": local_key,
                        "api_base_url": local_entry.get("base_url", ""),
                        "app_base_url": local_entry.get("base_url", ""),
                        "application": local_key,
                        "security_headers": local_security if local_security else None,
                        "is_default": False,
                    },
                }
            )
            continue

        updates: dict[str, Any] = {}

        local_base = local_entry.get("base_url")
        if local_base and local_base != remote.get("api_base_url"):
            updates["api_base_url"] = local_base

        local_security = local_entry.get("security_params", {})
        remote_headers = remote.get("security_headers") or {}

        if local_security:
            merged = {**remote_headers, **local_security}
            if merged != remote_headers:
                updates["security_headers"] = merged

        if updates:
            plan.append(
                {
                    "action": "update",
                    "remote": remote,
                    "local_key": local_key,
                    "updates": updates,
                }
            )
        else:
            plan.append(
                {
                    "action": "skip",
                    "remote": remote,
                    "local_key": local_key,
                    "reason": "already in sync",
                }
            )

    root_security = local_profile.get("security_params", {})
    root_base = local_profile.get("base_url", "")

    if root_security and root_base:
        already_matched_ids = {item["remote"]["id"] for item in plan if "remote" in item}
        root_remote = next(
            (
                p
                for p in active_remotes
                if p.get("api_base_url") == root_base and p["id"] not in already_matched_ids
            ),
            None,
        )

        if root_remote:
            remote_headers = root_remote.get("security_headers") or {}
            merged = {**remote_headers, **root_security}
            updates_root: dict[str, Any] = {}
            if merged != remote_headers:
                updates_root["security_headers"] = merged

            root_app_base = local_profile.get("application_base_url")
            if root_app_base and root_app_base != root_remote.get("app_base_url"):
                updates_root["app_base_url"] = root_app_base

            root_wf_params = local_profile.get("workflow_params")
            if root_wf_params:
                remote_props = root_remote.get("user_properties") or {}
                merged_props = {**remote_props, **root_wf_params}
                if merged_props != remote_props:
                    updates_root["user_properties"] = merged_props

            label = root_remote.get("profile_name", "(root)")
            if updates_root:
                plan.append(
                    {
                        "action": "update",
                        "remote": root_remote,
                        "local_key": f"(root → {label})",
                        "updates": updates_root,
                    }
                )
            else:
                plan.append(
                    {
                        "action": "skip",
                        "remote": root_remote,
                        "local_key": f"(root → {label})",
                        "reason": "already in sync",
                    }
                )
        else:
            _vprint(f"No remote profile with api_base_url='{root_base}' for root sync")

    return plan


def _display_plan(plan: list[dict[str, Any]]) -> None:
    """Display the sync plan."""
    for item in plan:
        action = item["action"]
        local_key = item["local_key"]

        if action == "skip":
            remote = item["remote"]
            print(f"  ⏭  {local_key} → {remote['profile_name']} (id={remote['id'][:8]}...)")
            print("     Already in sync")

        elif action in ("update", "update_default"):
            remote = item["remote"]
            updates = item["updates"]
            tag = " [DEFAULT]" if action == "update_default" else ""
            print(f"  🔄 {local_key} → {remote['profile_name']}{tag} (id={remote['id'][:8]}...)")
            for field, value in updates.items():
                if field == "security_headers" and isinstance(value, dict):
                    print(f"     {field}: {len(value)} key(s)")
                    for k in value:
                        print(f"       + {k}")
                elif field == "user_properties" and isinstance(value, dict):
                    print(f"     {field}: {len(value)} key(s)")
                else:
                    print(f"     {field}: {_truncate(str(value), 60)}")

        elif action == "create":
            data = item["data"]
            print(f"  ➕ {local_key} → NEW profile")
            print(f"     api_base_url: {data.get('api_base_url', '-')}")
            headers = data.get("security_headers") or {}
            if headers:
                print(f"     security_headers: {len(headers)} key(s)")
                for k in headers:
                    print(f"       + {k}")

        print()


def cmd_push(args: argparse.Namespace) -> int:
    """Push local adopt_profile.json values to matching remote playground profiles."""
    profile_path = _find_adopt_profile(args)
    if not profile_path:
        return 1

    _vprint(f"Loading local profile: {profile_path}")
    try:
        local_profile = json.loads(profile_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ Failed to read profile: {e}")
        return 1

    client = get_client(verbose=_verbose)

    print("📡 Fetching remote playground profiles...")
    success, remote_profiles, msg = client.list_playground_profiles()
    if not success:
        print(f"❌ Failed to list remote profiles: {msg}")
        return 1

    remote_profiles = remote_profiles or []
    active = [p for p in remote_profiles if not p.get("is_deleted", False)]
    print(f"   Found {len(active)} active remote profile(s)")

    only_keys = args.only if hasattr(args, "only") and args.only else None
    exclude_keys = args.exclude if hasattr(args, "exclude") and args.exclude else None
    plan = _build_sync_plan(
        local_profile, remote_profiles, only_keys=only_keys, exclude_keys=exclude_keys
    )

    if not plan:
        print("\n✅ Nothing to sync (no profiles_map entries in local profile)")
        return 0

    actionable = [p for p in plan if p["action"] != "skip"]
    skipped = [p for p in plan if p["action"] == "skip"]

    print(f"\n📋 Sync plan: {len(actionable)} change(s), {len(skipped)} already in sync\n")
    _display_plan(plan)

    if not actionable:
        print("✅ All profiles already in sync")
        return 0

    if args.dry_run:
        print("🔍 Dry run — no changes made")
        return 0

    print("🚀 Applying changes...\n")
    errors = 0

    for item in actionable:
        action = item["action"]
        local_key = item["local_key"]

        if action in ("update", "update_default"):
            remote = item["remote"]
            profile_id = remote["id"]
            updates = item["updates"]

            _vprint(f"Updating profile {profile_id}: {list(updates.keys())}")
            ok, result, result_msg = client.update_playground_profile(profile_id, updates)
            if ok:
                print(f"  ✅ Updated {remote['profile_name']} ({local_key})")
            else:
                print(f"  ❌ Failed to update {remote['profile_name']}: {result_msg}")
                errors += 1

        elif action == "create":
            data = item["data"]
            _vprint(f"Creating profile: {data.get('profile_name')}")
            ok, result, result_msg = client.create_playground_profile(data)
            if ok:
                new_id = result.get("id", "?") if result else "?"
                print(f"  ✅ Created {data['profile_name']} (id={new_id})")
            else:
                print(f"  ❌ Failed to create {data['profile_name']}: {result_msg}")
                errors += 1

    print()
    if errors:
        print(f"⚠️  Completed with {errors} error(s)")
        return 1

    print("✅ All changes applied successfully")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Pull remote playground profiles into local adopt_profile.json."""
    client = get_client(verbose=_verbose)

    print("📡 Fetching remote playground profiles...")
    success, remote_profiles, msg = client.list_playground_profiles()
    if not success:
        print(f"❌ Failed to list remote profiles: {msg}")
        return 1

    remote_profiles = remote_profiles or []
    active = [p for p in remote_profiles if not p.get("is_deleted", False)]
    if not active:
        print("No active remote profiles found.")
        return 0

    print(f"   Found {len(active)} active remote profile(s)")

    resolved_path = _find_adopt_profile(args)
    if resolved_path and resolved_path.exists():
        profile_path = resolved_path
        try:
            local_profile = json.loads(profile_path.read_text())
        except (json.JSONDecodeError, OSError):
            local_profile = {}
    else:
        manager = get_manager()
        if not manager.active_env:
            print("❌ No active environment and no --profile specified.")
            return 1
        profile_path = WORKSPACES_DIR / manager.active_env / "adopt_profile.json"
        local_profile = {}

    merge = getattr(args, "merge", False)
    if merge:
        new_profile = dict(local_profile)
    else:
        new_profile = {}

    default_remote = next((p for p in active if p.get("is_default")), active[0] if active else None)

    if default_remote:
        new_profile["base_url"] = default_remote.get("api_base_url", "")
        new_profile["application_base_url"] = default_remote.get("app_base_url", "")

        remote_headers = default_remote.get("security_headers") or {}
        if remote_headers:
            new_profile["security_params"] = remote_headers

        remote_props = default_remote.get("user_properties") or {}
        if remote_props:
            new_profile["workflow_params"] = remote_props

    profiles_map = new_profile.get("profiles_map", {}) if merge else {}

    for rp in active:
        app = rp.get("application")
        if not app:
            continue
        if default_remote and rp["id"] == default_remote["id"]:
            continue

        entry: dict[str, Any] = {"base_url": rp.get("api_base_url", "")}
        remote_headers = rp.get("security_headers") or {}
        if remote_headers:
            entry["security_params"] = remote_headers
        profiles_map[app] = entry

    if profiles_map:
        new_profile["profiles_map"] = profiles_map

    print("\n📋 Pull plan:")
    print(f"   Root base_url:         {new_profile.get('base_url', '-')}")
    print(f"   Root security_params:  {len(new_profile.get('security_params', {}))} key(s)")
    print(f"   Root workflow_params:  {len(new_profile.get('workflow_params', {}))} key(s)")
    print(f"   profiles_map entries:  {len(profiles_map)}")
    for app_name in profiles_map:
        print(f"     - {app_name}")
    print()

    if args.dry_run:
        print("🔍 Dry run — no changes made")
        print(json.dumps(new_profile, indent=2))
        return 0

    profile_path.write_text(json.dumps(new_profile, indent=2) + "\n")
    print(f"✅ Written to {profile_path}")
    return 0


# =========================================================================
# Profile-Token Integration Commands
# =========================================================================


def cmd_set_token(args: argparse.Namespace) -> int:
    """Set a security header to reference a token config name."""
    client = get_client(verbose=_verbose)

    success, profile, msg = client.get_playground_profile(args.profile_id)
    if not success:
        print(f"❌ Failed to fetch profile: {msg}")
        return 1

    headers = dict(profile.get("security_headers") or {}) if profile else {}
    headers[args.header_key] = args.token_name

    success, _, msg = client.update_playground_profile(
        args.profile_id, {"security_headers": headers}
    )
    if not success:
        print(f"❌ Failed to update profile: {msg}")
        return 1

    print(f"✅ Set header '{args.header_key}' = '{args.token_name}' on profile {args.profile_id}")
    return 0


def cmd_show_tokens(args: argparse.Namespace) -> int:
    """Show which security headers reference token configs."""
    client = get_client(verbose=_verbose)

    success, profile, msg = client.get_playground_profile(args.profile_id)
    if not success:
        print(f"❌ Failed to fetch profile: {msg}")
        return 1

    success, token_data, msg = client.list_token_configs(page_size=100)
    if not success:
        print(f"❌ Failed to list token configs: {msg}")
        return 1

    token_names: set[str] = set()
    if token_data:
        for tc in token_data.get("items", []):
            token_names.add(tc["name"])

    headers = (profile.get("security_headers") or {}) if profile else {}
    if not headers:
        print("No security headers configured on this profile.")
        return 0

    profile_name = profile.get("profile_name", args.profile_id) if profile else args.profile_id
    print(f"\n🔍 Headers for '{profile_name}':\n")

    for key, value in headers.items():
        val_str = str(value)
        if val_str in token_names:
            print(f"  🔗 {key}: {val_str}  (-> token config)")
        else:
            print(f"  📝 {key}: {_redact_value(key, val_str)}  (static)")

    print()
    return 0


# =========================================================================
# Token Commands
# =========================================================================


def cmd_token_list(args: argparse.Namespace) -> int:
    """List all token configs."""
    client = get_client(verbose=_verbose)

    kwargs: dict[str, Any] = {}
    if hasattr(args, "search") and args.search:
        kwargs["search"] = args.search
    if hasattr(args, "published") and args.published is not None:
        kwargs["is_published"] = args.published
    if hasattr(args, "integration_id") and args.integration_id:
        kwargs["integration_id"] = args.integration_id
    if hasattr(args, "page") and args.page:
        kwargs["page"] = args.page
    if hasattr(args, "page_size") and args.page_size:
        kwargs["page_size"] = args.page_size

    success, data, msg = client.list_token_configs(**kwargs)
    if not success:
        print(f"❌ Failed to list token configs: {msg}")
        return 1

    if not data:
        if getattr(args, "json_output", False):
            print("[]")
        else:
            print("No token configs found.")
        return 0

    items = data.get("items", [])
    total = data.get("total", len(items))

    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2))
        return 0

    print(f"\n🔑 Token configs ({len(items)} of {total})\n")

    for i, tc in enumerate(items, 1):
        pub_tag = "" if tc.get("is_published") else " (unpublished)"
        print(f"  {i}. {tc['name']}{pub_tag}")
        print(f"     ID:            {tc['id']}")
        print(f"     Storage type:  {tc.get('storage_type', '-')}")
        print(f"     Domain suffix: {tc.get('domain_suffix', '-')}")

        st = tc.get("storage_type", "")
        if st in ("localStorage", "sessionStorage"):
            print(f"     Storage key:   {tc.get('storage_key', '-')}")
        elif st == "cookie":
            ck = tc.get("cookie_key", "-")
            if tc.get("use_all_cookies"):
                ck = "ALL_COOKIES"
            print(f"     Cookie key:    {ck}")
            domains = tc.get("cookie_domains") or []
            if domains:
                print(f"     Cookie domains: {', '.join(domains)}")
        elif st == "domElement":
            print(f"     DOM selector:  {tc.get('dom_selector', '-')}")
            print(f"     DOM attribute: {tc.get('dom_attribute', '-')}")
        elif st == "customScript":
            script = tc.get("custom_script", "")
            if script:
                print(f"     Custom script: {_truncate(script, 60)}")

        if tc.get("parser_logic"):
            print(f"     Parser logic:  {_truncate(tc['parser_logic'], 60)}")
        if tc.get("integration_id"):
            print(f"     Integration:   {tc['integration_id']}")

        print()

    page = data.get("page", 1)
    total_pages = data.get("total_pages", 1)
    if total_pages > 1:
        print(f"  Page {page}/{total_pages} (use --page N to navigate)")

    return 0


def cmd_token_show(args: argparse.Namespace) -> int:
    """Show a single token config."""
    client = get_client(verbose=_verbose)

    success, tc, msg = client.get_token_config(args.token_id)
    if not success:
        print(f"❌ Failed to fetch token config: {msg}")
        return 1

    if not tc:
        print("Token config not found.")
        return 0

    if getattr(args, "json_output", False):
        print(json.dumps(tc, indent=2))
        return 0

    pub_tag = "" if tc.get("is_published") else " (unpublished)"
    print(f"\n🔑 {tc['name']}{pub_tag}\n")
    print(f"  ID:             {tc['id']}")
    print(f"  Domain suffix:  {tc.get('domain_suffix', '-')}")
    print(f"  Storage type:   {tc.get('storage_type', '-')}")

    st = tc.get("storage_type", "")
    if st in ("localStorage", "sessionStorage"):
        print(f"  Storage key:    {tc.get('storage_key', '-')}")
    elif st == "cookie":
        ck = tc.get("cookie_key", "-")
        if tc.get("use_all_cookies"):
            ck = "ALL_COOKIES"
        print(f"  Cookie key:     {ck}")
        domains = tc.get("cookie_domains") or []
        if domains:
            print(f"  Cookie domains: {', '.join(domains)}")
    elif st == "domElement":
        print(f"  DOM selector:   {tc.get('dom_selector', '-')}")
        print(f"  DOM attribute:  {tc.get('dom_attribute', '-')}")
        print(f"  Use content:    {tc.get('use_content', False)}")
    elif st == "customScript":
        script = tc.get("custom_script", "")
        if script:
            print("  Custom script:")
            for line in script.splitlines():
                print(f"    {line}")

    if tc.get("parser_logic"):
        print("  Parser logic:")
        for line in tc["parser_logic"].splitlines():
            print(f"    {line}")

    if tc.get("integration_id"):
        print(f"  Integration:    {tc['integration_id']}")

    print(f"  Published:      {tc.get('is_published', True)}")
    print(f"  Created:        {tc.get('created_at', '-')}")
    print(f"  Updated:        {tc.get('updated_at', '-')}")
    print()

    return 0


def _build_token_data(args: argparse.Namespace) -> dict[str, Any]:
    """Build token config data dict from parsed args."""
    data: dict[str, Any] = {}

    if hasattr(args, "name") and args.name:
        data["name"] = args.name
    if hasattr(args, "domain_suffix") and args.domain_suffix:
        data["domain_suffix"] = args.domain_suffix
    if hasattr(args, "storage_type") and args.storage_type:
        data["storage_type"] = args.storage_type
    if hasattr(args, "storage_key") and args.storage_key:
        data["storage_key"] = args.storage_key
    if hasattr(args, "cookie_key") and args.cookie_key:
        data["cookie_key"] = args.cookie_key
    if hasattr(args, "cookie_domain") and args.cookie_domain:
        data["cookie_domains"] = args.cookie_domain
    if hasattr(args, "all_cookies") and args.all_cookies:
        data["use_all_cookies"] = True
        data["cookie_key"] = "ALL_COOKIES"
    if hasattr(args, "dom_selector") and args.dom_selector:
        data["dom_selector"] = args.dom_selector
    if hasattr(args, "dom_attribute") and args.dom_attribute:
        data["dom_attribute"] = args.dom_attribute
    if hasattr(args, "use_content") and args.use_content:
        data["use_content"] = True
    if hasattr(args, "integration_id") and args.integration_id:
        data["integration_id"] = args.integration_id

    if hasattr(args, "published") and args.published is not None:
        data["is_published"] = args.published

    # Handle custom_script from flag or file
    if hasattr(args, "custom_script") and args.custom_script:
        data["custom_script"] = args.custom_script
    elif hasattr(args, "custom_script_file") and args.custom_script_file:
        data["custom_script"] = Path(args.custom_script_file).read_text()

    # Handle parser_logic from flag or file
    if hasattr(args, "parser_logic") and args.parser_logic:
        data["parser_logic"] = args.parser_logic
    elif hasattr(args, "parser_logic_file") and args.parser_logic_file:
        data["parser_logic"] = Path(args.parser_logic_file).read_text()

    return data


def cmd_token_create(args: argparse.Namespace) -> int:
    """Create a new token config."""
    data = _build_token_data(args)

    if "name" not in data:
        print("❌ --name is required")
        return 1
    if "domain_suffix" not in data:
        print("❌ --domain-suffix is required")
        return 1
    if "storage_type" not in data:
        print("❌ --storage-type is required")
        return 1

    client = get_client(verbose=_verbose)
    _vprint(f"Creating token config: {json.dumps(data, indent=2)}")

    success, result, msg = client.create_token_config(data)
    if not success:
        print(f"❌ Failed to create token config: {msg}")
        return 1

    new_id = result.get("id", "?") if result else "?"
    print(f"✅ Created token config '{data['name']}' (id={new_id})")
    return 0


def cmd_token_edit(args: argparse.Namespace) -> int:
    """Edit an existing token config."""
    data = _build_token_data(args)

    if not data:
        print("Nothing to update (no flags provided).")
        return 0

    client = get_client(verbose=_verbose)
    _vprint(f"Updating token config {args.token_id}: {list(data.keys())}")

    success, result, msg = client.update_token_config(args.token_id, data)
    if not success:
        print(f"❌ Failed to update token config: {msg}")
        return 1

    print(f"✅ Updated token config {args.token_id}")
    return 0


def cmd_token_delete(args: argparse.Namespace) -> int:
    """Delete a token config."""
    if not args.force:
        confirm = input(f"Delete token config {args.token_id}? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return 0

    client = get_client(verbose=_verbose)
    success, msg = client.delete_token_config(args.token_id)
    if not success:
        print(f"❌ Failed to delete token config: {msg}")
        return 1

    print(f"✅ Deleted token config {args.token_id}")
    return 0


def cmd_token_publish(args: argparse.Namespace) -> int:
    """Publish token configs."""
    client = get_client(verbose=_verbose)
    success, result, msg = client.bulk_update_token_config_status(args.token_ids, is_published=True)
    if not success:
        print(f"❌ Failed to publish: {msg}")
        return 1

    count = result.get("updated_count", 0) if result else 0
    print(f"✅ Published {count} token config(s)")
    return 0


def cmd_token_unpublish(args: argparse.Namespace) -> int:
    """Unpublish token configs."""
    client = get_client(verbose=_verbose)
    success, result, msg = client.bulk_update_token_config_status(
        args.token_ids, is_published=False
    )
    if not success:
        print(f"❌ Failed to unpublish: {msg}")
        return 1

    count = result.get("updated_count", 0) if result else 0
    print(f"✅ Unpublished {count} token config(s)")
    return 0


# =========================================================================
# Argument parser helpers for token subcommands
# =========================================================================


def _add_token_flags(parser: argparse.ArgumentParser) -> None:
    """Add common token config flags to a parser (used by create and edit)."""
    parser.add_argument("--name", help="Token name (no spaces)")
    parser.add_argument(
        "--domain-suffix", dest="domain_suffix", help="Domain suffix (e.g. .force.com)"
    )
    parser.add_argument(
        "--storage-type",
        dest="storage_type",
        choices=["localStorage", "sessionStorage", "cookie", "domElement", "customScript"],
        help="How the token is extracted",
    )
    parser.add_argument(
        "--storage-key", dest="storage_key", help="Key for localStorage/sessionStorage"
    )
    parser.add_argument("--cookie-key", dest="cookie_key", help="Cookie name (or ALL_COOKIES)")
    parser.add_argument(
        "--cookie-domain", dest="cookie_domain", action="append", help="Cookie domain (repeatable)"
    )
    parser.add_argument(
        "--all-cookies", dest="all_cookies", action="store_true", help="Extract all cookies"
    )
    parser.add_argument(
        "--dom-selector", dest="dom_selector", help="XPath selector for DOM extraction"
    )
    parser.add_argument("--dom-attribute", dest="dom_attribute", help="DOM attribute to read")
    parser.add_argument(
        "--use-content", dest="use_content", action="store_true", help="Use element text content"
    )
    parser.add_argument("--custom-script", dest="custom_script", help="Custom JS extraction script")
    parser.add_argument(
        "--custom-script-file", dest="custom_script_file", help="Path to custom JS file"
    )
    parser.add_argument("--parser-logic", dest="parser_logic", help="JS post-processing logic")
    parser.add_argument(
        "--parser-logic-file", dest="parser_logic_file", help="Path to parser logic JS file"
    )
    parser.add_argument("--integration-id", dest="integration_id", help="Integration ID")

    pub_group = parser.add_mutually_exclusive_group()
    pub_group.add_argument(
        "--published", dest="published", action="store_true", default=None, help="Mark as published"
    )
    pub_group.add_argument(
        "--no-publish", dest="published", action="store_false", help="Mark as unpublished"
    )


# =========================================================================
# Main
# =========================================================================


def main() -> int:
    global _verbose

    parser = argparse.ArgumentParser(
        description="Manage playground profiles and token configs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-e", "--env", help="Environment to use (overrides active)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Profile commands ---

    # list
    list_parser = subparsers.add_parser("list", help="List all playground profiles")
    list_parser.add_argument(
        "--integration-id", dest="integration_id", help="Filter by integration ID"
    )
    list_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output as JSON"
    )

    # show
    show_parser = subparsers.add_parser("show", help="Show a single profile")
    show_parser.add_argument("profile_id", nargs="?", help="Profile ID")
    show_parser.add_argument("--default", action="store_true", help="Show the default profile")
    show_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output as JSON"
    )

    # create
    create_parser = subparsers.add_parser("create", help="Create a new playground profile")
    create_parser.add_argument("--name", required=True, help="Profile name")
    create_parser.add_argument("--application", help="Application identifier (defaults to name)")
    create_parser.add_argument("--api-base-url", dest="api_base_url", help="API base URL")
    create_parser.add_argument("--app-base-url", dest="app_base_url", help="App base URL")
    create_parser.add_argument("--default", action="store_true", help="Set as default profile")
    create_parser.add_argument("--integration-id", dest="integration_id", help="Integration ID")
    create_parser.add_argument(
        "--security-header", dest="security_header", action="append", help="KEY=VALUE (repeatable)"
    )
    create_parser.add_argument(
        "--user-property", dest="user_property", action="append", help="KEY=VALUE (repeatable)"
    )
    create_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output as JSON"
    )

    # edit
    edit_parser = subparsers.add_parser("edit", help="Edit an existing profile")
    edit_parser.add_argument("profile_id", help="Profile ID to edit")
    edit_parser.add_argument("--name", help="New profile name")
    edit_parser.add_argument("--api-base-url", dest="api_base_url", help="New API base URL")
    edit_parser.add_argument("--app-base-url", dest="app_base_url", help="New app base URL")
    edit_parser.add_argument(
        "--default", dest="default", action="store_true", default=None, help="Set as default"
    )
    edit_parser.add_argument(
        "--set-header", dest="set_header", action="append", help="KEY=VALUE (repeatable)"
    )
    edit_parser.add_argument(
        "--remove-header",
        dest="remove_header",
        action="append",
        help="Header key to remove (repeatable)",
    )
    edit_parser.add_argument(
        "--set-property", dest="set_property", action="append", help="KEY=VALUE (repeatable)"
    )
    edit_parser.add_argument(
        "--remove-property",
        dest="remove_property",
        action="append",
        help="Property key to remove (repeatable)",
    )

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete a playground profile")
    delete_parser.add_argument("profile_id", help="Profile ID to delete")
    delete_parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # push
    push_parser = subparsers.add_parser("push", help="Push local profile to remote")
    push_parser.add_argument("--profile", "-p", help="Path to adopt_profile.json")
    push_parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Preview changes without applying"
    )
    push_parser.add_argument(
        "--only", action="append", help="Sync only these profiles_map keys (repeatable)"
    )
    push_parser.add_argument(
        "--exclude", action="append", help="Exclude these profiles_map keys (repeatable)"
    )

    # pull
    pull_parser = subparsers.add_parser(
        "pull", help="Pull remote profiles into local adopt_profile.json"
    )
    pull_parser.add_argument("--profile", "-p", help="Path to adopt_profile.json")
    pull_parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Preview changes without applying"
    )
    pull_parser.add_argument(
        "--merge", action="store_true", help="Merge into existing profile (vs overwrite)"
    )

    # set-token
    set_token_parser = subparsers.add_parser(
        "set-token", help="Set a security header to a token config name"
    )
    set_token_parser.add_argument("profile_id", help="Profile ID")
    set_token_parser.add_argument("header_key", help="Security header key")
    set_token_parser.add_argument("token_name", help="Token config name")

    # show-tokens
    show_tokens_parser = subparsers.add_parser(
        "show-tokens", help="Show which headers reference token configs"
    )
    show_tokens_parser.add_argument("profile_id", help="Profile ID")

    # --- Token commands ---

    token_parser = subparsers.add_parser("token", help="Manage token configs")
    token_subs = token_parser.add_subparsers(dest="token_command", required=True)

    # token list
    tl_parser = token_subs.add_parser("list", help="List token configs")
    tl_parser.add_argument("--search", help="Search by name")
    tl_parser.add_argument(
        "--published", dest="published", action="store_true", default=None, help="Published only"
    )
    tl_parser.add_argument(
        "--unpublished", dest="published", action="store_false", help="Unpublished only"
    )
    tl_parser.add_argument("--integration-id", dest="integration_id", help="Filter by integration")
    tl_parser.add_argument("--page", type=int, help="Page number")
    tl_parser.add_argument("--page-size", dest="page_size", type=int, help="Page size")
    tl_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # token show
    ts_parser = token_subs.add_parser("show", help="Show a token config")
    ts_parser.add_argument("token_id", help="Token config ID")
    ts_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # token create
    tc_parser = token_subs.add_parser("create", help="Create a token config")
    _add_token_flags(tc_parser)

    # token edit
    te_parser = token_subs.add_parser("edit", help="Edit a token config")
    te_parser.add_argument("token_id", help="Token config ID")
    _add_token_flags(te_parser)

    # token delete
    td_parser = token_subs.add_parser("delete", help="Delete a token config")
    td_parser.add_argument("token_id", help="Token config ID")
    td_parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # token publish
    tp_parser = token_subs.add_parser("publish", help="Publish token configs")
    tp_parser.add_argument("token_ids", nargs="+", help="Token config IDs")

    # token unpublish
    tu_parser = token_subs.add_parser("unpublish", help="Unpublish token configs")
    tu_parser.add_argument("token_ids", nargs="+", help="Token config IDs")

    args = parser.parse_args()
    _verbose = args.verbose

    manager = get_manager()
    if args.env:
        manager.active_env = args.env

    if not manager.active_env:
        print("❌ No active environment. Set one with: python cli/workspace.py env use <env-id>")
        return 1

    _vprint(f"📁 Environment: {manager.active_env}")

    # Dispatch
    cmd_map = {
        "list": cmd_list,
        "show": cmd_show,
        "create": cmd_create,
        "edit": cmd_edit,
        "delete": cmd_delete,
        "push": cmd_push,
        "pull": cmd_pull,
        "set-token": cmd_set_token,
        "show-tokens": cmd_show_tokens,
    }

    if args.command in cmd_map:
        return cmd_map[args.command](args)

    if args.command == "token":
        token_cmd_map = {
            "list": cmd_token_list,
            "show": cmd_token_show,
            "create": cmd_token_create,
            "edit": cmd_token_edit,
            "delete": cmd_token_delete,
            "publish": cmd_token_publish,
            "unpublish": cmd_token_unpublish,
        }
        return token_cmd_map[args.token_command](args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
