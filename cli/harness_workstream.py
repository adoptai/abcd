#!/usr/bin/env python3
"""
Ensure a real (non-general) workstream + linked docstore exist for harness
skill testing, and seed it with local test files.

A skill's ws_read/ws_grep tools need real docstore data scoped to a real
workstream -- 'general' cannot be linked to a docstore (see
adoptai-workflows/docs/agent-harness/agent_harness_architecture.md section
10, "Workstream integration"). This creates workstream -> docstore store ->
link, and caches the IDs in workspaces/{env}/harness/workstreams.json so
repeat runs reuse them instead of creating a new workstream every time.

Usage:
    abcd workstream ensure <name> [--env ENV]
    abcd workstream seed <name> <file> [<file> ...] [--env ENV]
    (the old `python cli/harness_workstream.py ensure|seed ...` form still works)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from cli.core.output import Result
from cli.core.registry import Registry, legacy_main
from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env
from cli.harness_common.workspace import load_workstreams_cache, save_workstreams_cache

if TYPE_CHECKING:
    from cli.core.context import Context


def ensure(name: str, env: str | None) -> dict | None:
    cache = load_workstreams_cache(env)
    if name in cache:
        print(f"✅ Reusing cached workstream '{name}': {cache[name]}")
        return cache[name]

    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return None

    print(f"🆕 Creating workstream '{name}'...")
    try:
        ws = client.create_workstream(
            name=name, description="Created by abcd cli/harness_workstream.py"
        )
    except HarnessAPIError as e:
        print(f"❌ Failed to create workstream: {e}")
        if e.status_code == 403:
            print(
                "   Workstream creation is admin-only -- the account behind your PAT must be "
                "an org admin. Ask an admin to run this once and share the workstream_id, or "
                "add it directly to workspaces/{env}/harness/workstreams.json."
            )
        return None
    workstream_id = ws.get("id") or ws.get("workstream_id")
    if not workstream_id:
        print(f"❌ No workstream id in response: {ws}")
        return None
    print(f"   ✅ workstream_id={workstream_id}")

    # The BFF creates a default "Workstream Files" docstore (linked) with every workstream;
    # reuse it instead of adding a second, redundant store.
    store_id: str | None = None
    try:
        existing = client.list_stores(workstream_id)
    except HarnessAPIError:
        existing = {}
    for found in existing.get("stores", []) if isinstance(existing, dict) else []:
        if isinstance(found, dict) and found.get("id"):
            store_id = str(found["id"])
            print(f"   ♻️  reusing the workstream's default docstore {store_id}")
            break

    if store_id is None:
        print(f"🆕 Creating docstore store for '{name}'...")
        try:
            store = client.create_store(name=name, workstream_id=None)
        except HarnessAPIError as e:
            print(f"❌ Failed to create docstore store: {e}")
            return None
        store_id = store.get("id") or store.get("store_id")
        if not store_id:
            print(f"❌ No store id in response: {store}")
            return None
        print(f"   ✅ store_id={store_id}")

        print("🔗 Linking store to workstream...")
        try:
            client.link_store_to_workstreams(store_id, [workstream_id])
        except HarnessAPIError as e:
            print(f"❌ Failed to link store to workstream: {e}")
            return None
        print("   ✅ Linked")

    entry = {"workstream_id": workstream_id, "store_id": store_id}
    cache[name] = entry
    save_workstreams_cache(cache, env)
    return entry


def seed(name: str, files: list[Path], env: str | None) -> int:
    entry = ensure(name, env)
    if entry is None:
        return 1
    store_id = entry["store_id"]

    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"❌ File(s) not found: {', '.join(str(f) for f in missing)}")
        return 1

    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"\n⬆️  Uploading {len(files)} file(s) to store {store_id}...")
    file_metas = [
        {
            "filename": f.name,
            "file_type": f.suffix.lstrip(".") or "bin",
            "size_bytes": f.stat().st_size,
        }
        for f in files
    ]
    try:
        presign = client.get_upload_urls(store_id, file_metas, override=True)
    except HarnessAPIError as e:
        print(f"❌ Failed to get upload URLs: {e}")
        return 1

    for item, f in zip(presign.get("upload_urls", []), files, strict=False):
        print(f"   ⬆️  {f.name} -> {item['s3_key']}")
        put_response = requests.put(item["presigned_url"], data=f.read_bytes(), timeout=60)
        if put_response.status_code >= 400:
            print(f"   ❌ Upload failed for {f.name}: {put_response.status_code}")
            return 1

    try:
        confirm = client.confirm_upload(store_id, presign["batch_id"])
    except HarnessAPIError as e:
        print(f"❌ Failed to confirm upload: {e}")
        return 1

    print(f"   ✅ Confirmed {confirm.get('confirmed', 0)} document(s)")
    return 0


def _cmd_ensure(ctx: Context) -> int | Result:
    if ctx.dry_run:
        print(f"DRY RUN: would create-or-reuse workstream '{ctx.args.name}' + docstore")
        return 0
    entry = ensure(ctx.args.name, ctx.env_arg)
    if not entry:
        return 1
    # `--json` used to carry `data: null`; the ids are what a caller needs (text "": already printed)
    return Result.done({"name": ctx.args.name, **entry}, "")


def _cmd_seed(ctx: Context) -> int:
    if ctx.dry_run:
        print(f"DRY RUN: would upload {len(ctx.args.files)} file(s) to '{ctx.args.name}'")
        return 0
    return seed(ctx.args.name, ctx.args.files, ctx.env_arg)


def register(registry: Registry) -> None:
    """Register ``abcd workstream ensure|seed`` (and the flat ``harness_workstream.py`` CLI)."""
    group = registry.group("workstream", "Workstreams + docstores for harness skill testing")
    p = group.command(
        "ensure",
        _cmd_ensure,
        help="Create-or-reuse a workstream + linked docstore",
        writes=True,
        legacy_name="ensure",
    )
    p.add_argument("name")
    p = group.command(
        "seed",
        _cmd_seed,
        help="Upload local test file(s) into the workstream's docstore",
        writes=True,
        legacy_name="seed",
    )
    p.add_argument("name")
    p.add_argument("files", nargs="+", type=Path)


def main(argv: list[str] | None = None) -> int:
    return legacy_main("harness_workstream.py", register, __doc__, argv)


if __name__ == "__main__":
    sys.exit(main())
