"""``abcd pipeline``: process pipelines through the BFF ``/v1/pipelines`` (BFF_ROUTES.md section 9).

A process pipeline is a server-built ``RUN_PROCESS`` step that fires the process across its assigned
workstreams, like the Run button. The plan's rule is to create cron pipelines once, from a browser:
a pipeline created with a PAT records the PAT token id as its owner, and the scheduler later uses
that owner as the principal of the turns it fires (per-user Tabby/NoUI/web-API credentials resolve
against it). These commands exist for testing and admin use.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_session import resolve_process
from cli.core.output import Result
from cli.harness_common.api_client import HarnessAPIClient

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

OWNER_WARNING = (
    "WARNING: a pipeline created with a PAT records the PAT token id as its owner, and the "
    "scheduler fires its turns as that principal (PLAN section 4). The plan's rule is to create "
    "cron pipelines once from a browser; use this command for testing and admin only."
)
_HEX_ID = re.compile(r"[0-9a-f]{16}")


def _client(ctx: Context) -> HarnessAPIClient:
    from cli.builder_session import get_client

    return get_client(ctx)


def _validate_cron(expr: str) -> str:
    fields = expr.split()
    if len(fields) not in (5, 6):
        raise UsageError(f"--cron must have 5 fields (minute hour day month weekday), got {expr!r}")
    return " ".join(fields)


def resolve_pipeline(client: HarnessAPIClient, ref: str) -> str:
    """A pipeline id (16 hex) or an exact name -> the id."""
    if _HEX_ID.fullmatch(ref):
        return ref
    found = client._request("GET", "/v1/pipelines", params={"search": ref, "page_size": 100})
    for item in found.get("items", []) if isinstance(found, dict) else []:
        if isinstance(item, dict) and item.get("name") == ref:
            return str(item["id"])
    raise UsageError(f"pipeline not found: {ref!r} (see `abcd pipeline list`)")


def _row(p: dict[str, Any]) -> str:
    return (
        f"{p.get('id')}  {p.get('state')}  {p.get('schedule_type')}"
        f"{' ' + str(p['cron_expr']) if p.get('cron_expr') else ''}  "
        f"{p.get('name')}  process={p.get('process_display_name') or p.get('process_id')}"
    )


def _cmd_create(ctx: Context) -> Result:
    a = ctx.args
    client = _client(ctx)
    proc = resolve_process(client, a.process)
    if not proc.get("is_enabled"):
        raise RefusedError(
            f"process {proc.get('name')!r} is not enabled: the BFF refuses a pipeline on it "
            "(enable it first, `abcd agent update`)"
        )
    payload: dict[str, Any] = {
        "name": a.name or f"{proc.get('name')}-{a.schedule}",
        "process_id": proc["id"],
        "schedule_type": a.schedule,
        "state": a.state,
    }
    if a.schedule == "cron":
        if not a.cron:
            raise UsageError("--schedule cron needs --cron EXPR")
        payload["cron_expr"] = _validate_cron(a.cron)
    elif a.cron:
        raise UsageError("--cron only goes with --schedule cron")
    ctx.log(OWNER_WARNING)
    if ctx.dry_run:
        return Result.done(
            {"payload": payload, "warnings": [OWNER_WARNING]},
            f"DRY RUN: would POST /v1/pipelines {payload}\n{OWNER_WARNING}",
        )
    created = client._request("POST", "/v1/pipelines", json=payload)
    text = f"created pipeline {_row(created)}\n{OWNER_WARNING}"
    if a.state == "draft":
        text += "\nIt is a draft and does not fire until `abcd pipeline activate`."
    return Result.done({"pipeline": created, "warnings": [OWNER_WARNING]}, text)


def _cmd_list(ctx: Context) -> Result:
    a = ctx.args
    params: dict[str, Any] = {"page_size": a.limit}
    for key in ("state", "search"):
        if getattr(a, key):
            params[key] = getattr(a, key)
    if a.schedule:
        params["schedule_type"] = a.schedule
    found = _client(ctx)._request("GET", "/v1/pipelines", params=params)
    items = (
        [i for i in found.get("items", []) if isinstance(i, dict)]
        if isinstance(found, dict)
        else []
    )
    if a.process:
        proc_id = resolve_process(_client(ctx), a.process)["id"]
        items = [i for i in items if i.get("process_id") == proc_id]
    text = "\n".join([f"{len(items)} pipeline(s)"] + [f"  {_row(i)}" for i in items])
    return Result.done(
        {"items": items, "total": found.get("total") if isinstance(found, dict) else None}, text
    )


def _cmd_get(ctx: Context) -> Result:
    client = _client(ctx)
    pid = resolve_pipeline(client, ctx.args.pipeline)
    p = client._request("GET", f"/v1/pipelines/{pid}")
    text = (
        _row(p)
        + f"\nowner (created_by): {p.get('created_by')}  next_run_at: {p.get('next_run_at')}"
    )
    return Result.done(p, text)


def _set_state(ctx: Context, state: str) -> Result:
    client = _client(ctx)
    pid = resolve_pipeline(client, ctx.args.pipeline)
    if ctx.dry_run:
        return Result.done(
            {"pipeline_id": pid, "state": state}, f"DRY RUN: would set {pid} to {state}"
        )
    p = client._request("POST", f"/v1/pipelines/{pid}/update", json={"state": state})
    return Result.done({"pipeline": p}, f"pipeline {pid} is now {p.get('state', state)}")


def _cmd_activate(ctx: Context) -> Result:
    return _set_state(ctx, "running")


def _cmd_pause(ctx: Context) -> Result:
    return _set_state(ctx, "paused")


def _cmd_delete(ctx: Context) -> Result:
    client = _client(ctx)
    pid = resolve_pipeline(client, ctx.args.pipeline)
    if not ctx.yes:
        raise RefusedError(f"deleting pipeline {pid} is not undoable from here: re-run with --yes")
    if ctx.dry_run:
        return Result.done({"pipeline_id": pid}, f"DRY RUN: would delete {pid}")
    client._request("DELETE", f"/v1/pipelines/{pid}")
    return Result.done({"pipeline_id": pid, "deleted": True}, f"deleted pipeline {pid}")


def _conversation_ids(run: dict[str, Any]) -> list[str]:
    """The conversations a process pipeline run started (``metadata.process_runs``)."""
    meta = run.get("metadata")
    rows = meta.get("process_runs") if isinstance(meta, dict) else None
    return [
        str(r["conversation_id"])
        for r in rows or []
        if isinstance(r, dict) and r.get("conversation_id")
    ]


def _conversations(run: dict[str, Any]) -> str:
    ids = _conversation_ids(run)
    return f"  conversation={','.join(ids)}" if ids else ""


def _cmd_runs(ctx: Context) -> Result:
    a = ctx.args
    client = _client(ctx)
    pid = resolve_pipeline(client, a.pipeline)
    params: dict[str, Any] = {"page_size": a.limit}
    if a.status:
        params["status"] = a.status
    found = client._request("GET", f"/v1/pipelines/{quote(pid, safe='')}/runs", params=params)
    items = (
        [i for i in found.get("items", []) if isinstance(i, dict)]
        if isinstance(found, dict)
        else []
    )
    lines = [f"{len(items)} run(s)"] + [
        f"  {r.get('id')}  {r.get('status')}  {r.get('started_at')}  "
        f"{r.get('duration_ms')}ms  {r.get('workstream_name') or ''} "
        f"{r.get('error_message') or ''}{_conversations(r)}".rstrip()
        for r in items
    ]
    if any(_conversation_ids(r) for r in items):
        lines.append("Follow a conversation with `abcd runs detail --run-id <conversation id>`.")
    return Result.done({"pipeline_id": pid, "items": items}, "\n".join(lines))


def _cmd_run_now(ctx: Context) -> Result:
    """``POST /v1/pipelines/workflows/test-run`` with the stored RUN_PROCESS WDL.

    The BFF falls back to the pipeline's stored wdl when none is sent and forwards it to the
    workflows ``/wdl/execute``, whose workflow runs a single ``RUN_PROCESS`` step through the same
    path as a scheduled fire. ``test_mode=false`` makes it a real run, so it needs ``--yes``.
    """
    client = _client(ctx)
    pid = resolve_pipeline(client, ctx.args.pipeline)
    if not ctx.yes:
        raise RefusedError(f"run-now starts a real run of pipeline {pid} (agent turns): pass --yes")
    body: dict[str, Any] = {"pipeline_id": pid, "test_mode": False}
    if ctx.dry_run:
        return Result.done(
            {"payload": body}, f"DRY RUN: would POST /v1/pipelines/workflows/test-run {body}"
        )
    resp = client._request("POST", "/v1/pipelines/workflows/test-run", json=body)
    text = f"run started for pipeline {pid}: {resp}\nFollow it with `abcd pipeline runs {pid}`."
    return Result.done({"pipeline_id": pid, "response": resp}, text)


def register(registry: Registry) -> None:
    g = registry.group("pipeline", "Process pipelines (scheduled or manual runs of a process)")
    p = g.command(
        "create",
        _cmd_create,
        help="Create a process pipeline (testing/admin; see warning)",
        writes=True,
    )
    p.add_argument("--process", required=True, help="Process (agent) name or id; must be enabled")
    p.add_argument("--schedule", required=True, choices=["cron", "manual"])
    p.add_argument("--cron", help="Cron expression (with --schedule cron)")
    p.add_argument("--state", default="draft", choices=["draft", "running"])
    p.add_argument("--name", help="Pipeline name (default: <process>-<schedule>)")

    p = g.command("list", _cmd_list, help="List pipelines")
    p.add_argument("--state")
    p.add_argument("--schedule", choices=["cron", "manual"])
    p.add_argument("--search")
    p.add_argument("--process", help="Only pipelines of this process")
    p.add_argument("--limit", type=int, default=100)

    p = g.command("get", _cmd_get, help="Show one pipeline")
    p.add_argument("pipeline", help="Pipeline id or exact name")

    p = g.command("activate", _cmd_activate, help="Set the pipeline to running", writes=True)
    p.add_argument("pipeline")

    p = g.command("pause", _cmd_pause, help="Set the pipeline to paused", writes=True)
    p.add_argument("pipeline")

    p = g.command("delete", _cmd_delete, help="Delete a pipeline (needs --yes)", writes=True)
    p.add_argument("pipeline")

    p = g.command("runs", _cmd_runs, help="Run history of a pipeline")
    p.add_argument("pipeline")
    p.add_argument("--status")
    p.add_argument("--limit", type=int, default=25)

    p = g.command(
        "run-now", _cmd_run_now, help="Fire a pipeline once now (needs --yes)", writes=True
    )
    p.add_argument("pipeline")
