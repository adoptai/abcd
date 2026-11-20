"""``abcd plan``: the build plan (``bld_build_plan``), PLAN.md sections 8-9.

``propose`` adds an item, ``approve`` records who approved it (a lead's decision, so it needs
``--yes``), ``show`` lists the items and ``done`` closes one. Rows go through ``OrgDBClient``;
``item_key`` is the table's unique key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.release_approval import refuse_self_asserted_in_cloud
from cli.builder_common.tables import validate_row
from cli.core.output import Result
from cli.core.registry import Registry

if TYPE_CHECKING:
    from cli.core.context import Context

TABLE = "bld_build_plan"
COLUMNS = ("item_key", "kind", "name", "action", "status", "agent_key", "reason_ref", "approved_by")


def _store(ctx: Context) -> OrgDBClient:
    return OrgDBClient.from_client(ctx.harness_client())


def _row(store: OrgDBClient, item_key: str) -> dict[str, Any]:
    rows = store.find_rows(TABLE, "item_key", item_key)
    if not rows:
        raise RefusedError(f"no build-plan item {item_key!r}; `abcd plan show` lists them")
    return rows[0]


def _render(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "The build plan is empty."
    lines = [f"{'ITEM':<28} {'KIND':<7} {'ACTION':<7} {'STATUS':<9} {'BY':<12} TITLE"]
    for r in rows:
        lines.append(
            f"{r.get('item_key', ''):<28} {r.get('kind', ''):<7} {r.get('action') or '':<7} "
            f"{r.get('status', ''):<9} {r.get('approved_by') or '-':<12} {r.get('name') or ''}"
        )
    return "\n".join(lines)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    keys = (*COLUMNS, "approved_at")
    return {k: row.get(k) for k in keys}


def _propose(ctx: Context) -> Result:
    a = ctx.args
    row: dict[str, Any] = {
        "item_key": a.item_key,
        "kind": a.kind,
        "name": a.title,
        "action": a.action,
        "status": "proposed",
    }
    if a.agent:
        row["agent_key"] = a.agent
    if a.reason_ref:
        row["reason_ref"] = a.reason_ref
    validate_row(TABLE, row)
    if ctx.dry_run:
        return Result.done({"dry_run": True, "row": row}, f"DRY RUN: would propose {a.item_key}")
    result = _store(ctx).insert_rows(TABLE, [row])
    if result.failed:
        raise PlatformError(f"could not write {a.item_key}: {result.failed[0]['error']}")
    if result.duplicates:
        raise RefusedError(f"{a.item_key} is already in the plan; pick another key")
    return Result.done({"item": row}, f"Proposed {a.item_key} ({a.kind}): {a.title}")


def _approve(ctx: Context) -> Result:
    a = ctx.args
    refuse_self_asserted_in_cloud(ctx.env_dir, by=a.by)
    if not a.by.strip():
        raise UsageError("--by needs a name")
    if not ctx.dry_run and not ctx.yes:
        raise RefusedError(f"Approving {a.item_key} is a recorded decision: pass --yes")
    if ctx.dry_run:
        return Result.done(
            {"dry_run": True, "item_key": a.item_key, "approved_by": a.by},
            f"DRY RUN: would approve {a.item_key} as {a.by}",
        )
    store = _store(ctx)
    row = _row(store, a.item_key)
    if row.get("status") != "proposed":
        raise RefusedError(f"{a.item_key} is {row.get('status')}; only a proposed item is approved")
    patch = {
        "status": "approved",
        "approved_by": a.by,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    store.update_row(TABLE, "item_key", a.item_key, patch)
    return Result.done({"item": _public({**row, **patch})}, f"Approved {a.item_key} by {a.by}")


def _done(ctx: Context) -> Result:
    a = ctx.args
    if ctx.dry_run:
        return Result.done(
            {"dry_run": True, "item_key": a.item_key}, f"DRY RUN: would close {a.item_key}"
        )
    store = _store(ctx)
    row = _row(store, a.item_key)
    if row.get("status") not in ("approved", "building"):
        raise RefusedError(
            f"{a.item_key} is {row.get('status')}; only an approved item can be marked done"
        )
    store.update_row(TABLE, "item_key", a.item_key, {"status": "done"})
    return Result.done({"item": _public({**row, "status": "done"})}, f"Marked {a.item_key} done")


def _show(ctx: Context) -> Result:
    store = _store(ctx)
    rows = store.list_all(TABLE)
    if ctx.args.item_key:
        rows = [r for r in rows if r.get("item_key") == ctx.args.item_key]
        if not rows:
            raise RefusedError(f"no build-plan item {ctx.args.item_key!r}")
    if ctx.args.status:
        rows = [r for r in rows if r.get("status") == ctx.args.status]
    items = [_public(r) for r in rows]
    return Result.done({"items": items, "count": len(items)}, _render(items))


def register(registry: Registry) -> None:
    group = registry.group("plan", "The build plan: what to build or change, and its approval")
    p = group.command(
        "propose", _propose, help="Propose a skill, plugin, agent or check to build", writes=True
    )
    p.add_argument("item_key")
    p.add_argument("--kind", required=True, choices=["skill", "plugin", "agent", "check"])
    p.add_argument("--title", required=True)
    p.add_argument("--action", choices=["new", "change", "retire"], default="new")
    p.add_argument("--agent", help="The agent this item belongs to (agent_key)")
    p.add_argument("--reason-ref", help="Decision, run or ticket id")
    p = group.command(
        "approve", _approve, help="Approve a proposed item (needs --yes)", writes=True
    )
    p.add_argument("item_key")
    p.add_argument("--by", required=True, help="Who approved it")
    p = group.command("show", _show, help="Show the build plan")
    p.add_argument("item_key", nargs="?")
    p.add_argument("--status", choices=["proposed", "approved", "building", "done", "dropped"])
    p = group.command("done", _done, help="Mark an approved item done", writes=True)
    p.add_argument("item_key")
