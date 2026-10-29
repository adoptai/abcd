"""``abcd builder setup``: the Builder's workstreams, Doc Stores and tables in the client org.

Creates or reuses, idempotently (PLAN.md section 9): the workstreams ``builder-context``,
``builder-ops``, ``builder-sme-<client>``, ``builder-status-<client>`` and ``<client>-staging``;
a Doc Store linked to each; the ``noui-bundles`` store (linked only to ``builder-ops``); and the
25 ``bld_*`` tables with their workstream links. The ids go into the workspace's ``builder.json``.
Access grants need user ids, so they are printed as instructions, never applied.

The Doc Store helpers here (``ensure_store``, ...) are shared with
``cli/builder_ws.py`` (``push-bundles`` / ``pull-bundles``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common import tables as tbl
from cli.builder_common import workspace as bws
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.orgdb import OrgDBClient
from cli.core.output import Result
from cli.core.registry import Registry
from cli.harness_common.api_client import HarnessAPIClient, HarnessAPIError

if TYPE_CHECKING:
    from cli.core.context import Context

WS_CONTEXT_NAME = "builder-context"
WS_OPS_NAME = "builder-ops"
BUNDLES_STORE = "noui-bundles"
PAGE = 100
# The platform makes every new workstream a store with this name (live 2026-10-29). Only such an
# untouched default may be adopted; a store with any other name is somebody's (e.g. noui-bundles).
DEFAULT_STORE_NAMES = ("workstream files",)


# ---------------------------------------------------------------------------
# Names and plan (pure, no calls)
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise UsageError(f"cannot derive a client name from {value!r}; pass --client")
    return slug


def client_name(ws: Path, override: str | None = None) -> str:
    """``--client``, else ``env.json`` ``client``, else the workspace folder name."""
    if override:
        return slugify(override)
    meta = bws._read_env_json(ws) or {}
    return slugify(str(meta.get("client") or ws.name))


def workstream_names(client: str) -> dict[str, str]:
    """Role -> workstream name."""
    return {
        tbl.WS_CONTEXT: WS_CONTEXT_NAME,
        tbl.WS_OPS: WS_OPS_NAME,
        tbl.WS_SME: f"builder-sme-{client}",
        "status": f"builder-status-{client}",
        "staging": f"{client}-staging",
    }


GRANT_INSTRUCTIONS = (
    ("builder-context", "write", "every FDE"),
    ("builder-ops", "write", "every FDE"),
    ("{staging}", "write", "every FDE (FDEs only)"),
    ("{sme}", "write", "the customer SME; FDEs; the Context agent's process"),
    (
        "{status}",
        "read",
        "client stakeholders; `abcd status publish` writes with the workspace PAT",
    ),
)


def grant_instructions(names: dict[str, str]) -> list[dict[str, str]]:
    """Who needs access to what. Applied by an admin with user ids (``PUT /v1/org/console-access/{user_id}``)."""
    out = []
    for ws_name, permission, who in GRANT_INSTRUCTIONS:
        resolved = ws_name.format(
            sme=names[tbl.WS_SME], status=names["status"], staging=names["staging"]
        )
        out.append(
            {
                "workstream": resolved,
                "permission": permission,
                "who": who,
                "how": "Settings > Console access, or PUT /v1/org/console-access/{user_id} "
                "with {workstreams: [{workstream_id, permission}]} (replace semantics: send the "
                "user's full list)",
            }
        )
    return out


def build_plan(
    names: dict[str, str], live_agent_workstreams: list[str] | None = None
) -> dict[str, Any]:
    """Everything setup would create or reuse. Pure: makes no call."""
    tables = []
    for spec in tbl.TABLES:
        roles = list(spec.workstream_roles)
        tables.append({"name": spec.name, "group": spec.group, "link_roles": roles})
    return {
        "workstreams": [{"role": role, "name": name} for role, name in names.items()],
        "doc_stores": [
            *({"name": name, "workstream": name} for name in names.values()),
            {"name": BUNDLES_STORE, "workstream": WS_OPS_NAME, "only_linked_to": WS_OPS_NAME},
        ],
        "tables": tables,
        "live_agent_workstreams": list(live_agent_workstreams or []),
        "grants": grant_instructions(names),
    }


# ---------------------------------------------------------------------------
# Platform helpers (workstreams, Doc Stores)
# ---------------------------------------------------------------------------


def rows_of(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in (*keys, "items", "data"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []


def _same_name(a: Any, b: str) -> bool:
    return str(a or "").strip().lower() == b.strip().lower()


def find_workstream(client: HarnessAPIClient, name: str) -> dict[str, Any] | None:
    page = 1
    while True:
        data = client._request(
            "GET", "/v1/org/workstreams", params={"search": name, "page": page, "page_size": PAGE}
        )
        rows = rows_of(data, "workstreams")
        for row in rows:
            if _same_name(row.get("name"), name):
                return row
        if len(rows) < PAGE:
            return None
        page += 1


def ensure_workstream(client: HarnessAPIClient, name: str, description: str) -> tuple[str, bool]:
    """``(workstream_id, created)``; reuses an existing one (names are unique per org)."""
    found = find_workstream(client, name)
    if found is not None:
        return str(found["id"]), False
    try:
        made = client.create_workstream(name, description)
    except HarnessAPIError as exc:
        if exc.status_code != 409:
            raise
        found = find_workstream(client, name)
        if found is None:
            raise
        return str(found["id"]), False
    wid = made.get("id") or made.get("workstream_id")
    if not wid:
        raise PlatformError(f"workstream {name!r} created but the response has no id")
    return str(wid), True


def iter_stores(
    client: HarnessAPIClient, workstream_id: str | None = None
) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        params: dict[str, Any] = {"page": page, "page_size": PAGE}
        if workstream_id:
            params["workstream_id"] = workstream_id
        rows = rows_of(client._request("GET", "/v1/docstore/stores", params=params), "stores")
        yield from rows
        if len(rows) < PAGE:
            return
        page += 1


def store_workstream_ids(store: dict[str, Any]) -> list[str]:
    return [str(w) for w in store.get("workstream_ids") or []]


def ensure_store(
    client: HarnessAPIClient, name: str, workstream_id: str, *, accept_default: bool = True
) -> dict[str, Any]:
    """Find or create the Doc Store ``name`` and make sure it is linked to ``workstream_id``.

    Returns ``{id, name, created, linked, workstream_ids}``. With ``accept_default`` a workstream's
    auto-created default store ("Workstream Files") is reused when none is named ``name``; a
    differently named store (e.g. ``noui-bundles``) is never taken for it.
    """
    stores = list(iter_stores(client, workstream_id))
    chosen = next((s for s in stores if _same_name(s.get("name"), name)), None)
    if chosen is None and accept_default:
        chosen = next(
            (s for s in stores if str(s.get("name") or "").strip().lower() in DEFAULT_STORE_NAMES),
            None,
        )
    if chosen is None:
        chosen = next((s for s in iter_stores(client) if _same_name(s.get("name"), name)), None)
    created = False
    if chosen is None:
        chosen = client.create_store(name, workstream_id)
        created = True
    linked_ids = store_workstream_ids(chosen)
    linked_now = False
    if workstream_id not in linked_ids:
        client.link_store_to_workstreams(str(chosen["id"]), [workstream_id])
        linked_ids.append(workstream_id)
        linked_now = True
    return {
        "id": str(chosen["id"]),
        "name": str(chosen.get("name") or name),
        "created": created,
        "linked": linked_now or created,
        "workstream_ids": linked_ids,
    }


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def _resolve_agent_workstreams(client: HarnessAPIClient, refs: list[str]) -> dict[str, str]:
    """Name -> id of each live (client) workstream; recorded in builder.json so release and the
    eval runner can resolve the names an agent bundle uses."""
    ids: dict[str, str] = {}
    for ref in refs:
        found = find_workstream(client, ref)
        if found is not None:
            ids[str(found.get("name") or ref)] = str(found["id"])
        elif re.fullmatch(r"[0-9a-fA-F-]{16,}", ref):
            ids[ref] = ref  # looks like an id already
        else:
            raise UsageError(f"--agent-workstream {ref!r}: no such workstream")
    return ids


def _render_plan(plan: dict[str, Any]) -> str:
    lines = ["Would create or reuse:", "  Workstreams:"]
    lines += [f"    {w['name']}  ({w['role']})" for w in plan["workstreams"]]
    lines.append("  Doc Stores (each linked to its workstream):")
    for s in plan["doc_stores"]:
        extra = f"  (linked only to {s['only_linked_to']})" if s.get("only_linked_to") else ""
        lines.append(f"    {s['name']} -> {s['workstream']}{extra}")
    lines.append(f"  Tables ({len(plan['tables'])}, workstream_scoped=false, write-linked):")
    for t in plan["tables"]:
        lines.append(f"    {t['name']}  -> {', '.join(t['link_roles'])}")
    if plan["live_agent_workstreams"]:
        lines.append("  live_agents role linked to: " + ", ".join(plan["live_agent_workstreams"]))
    lines.append("  builder.json: workstreams, stores and tables ids recorded")
    lines.append("Access grants (apply with user ids; setup never grants):")
    for g in plan["grants"]:
        lines.append(f"    {g['workstream']}: {g['permission']} for {g['who']}")
    return "\n".join(lines)


def _setup(ctx: Context) -> Result:
    ws = ctx.env_dir
    bws.assert_not_default(ctx.env)
    if not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    existing = bws.load_builder_config(ws)  # needs `abcd ws init` first
    if existing.get("role") == "home":
        raise RefusedError(
            f"{ws.name} is a Builder home org workspace (builder.json role: home); `builder setup` "
            "creates the client org's workstreams. Run it with the client org's workspace"
        )
    names = workstream_names(client_name(ws, ctx.args.client))
    agent_refs: list[str] = list(ctx.args.agent_workstream or [])
    plan = build_plan(names, agent_refs)

    if ctx.dry_run:
        return Result.done(
            {"dry_run": True, "plan": plan},
            "DRY RUN: nothing was called or written.\n" + _render_plan(plan),
        )

    client = ctx.harness_client()
    ws_ids: dict[str, str] = {}
    created_ws: list[str] = []
    for name in names.values():
        wid, created = ensure_workstream(client, name, "Created by abcd builder setup")
        ws_ids[name] = wid
        if created:
            created_ws.append(name)

    store_ids: dict[str, str] = {}
    store_notes: list[dict[str, Any]] = []
    created_stores: list[str] = []
    for name in names.values():
        info = ensure_store(client, name, ws_ids[name])
        store_ids[name] = info["id"]
        if info["created"]:
            created_stores.append(name)
    ops_id = ws_ids[WS_OPS_NAME]
    bundles = ensure_store(client, BUNDLES_STORE, ops_id, accept_default=False)
    store_ids[BUNDLES_STORE] = bundles["id"]
    if bundles["created"]:
        created_stores.append(BUNDLES_STORE)
    extra = [w for w in bundles["workstream_ids"] if w != ops_id]
    if extra:
        store_notes.append(
            {
                "store": BUNDLES_STORE,
                "warning": "linked to workstreams other than builder-ops; unlink them "
                "(DELETE /v1/docstore/stores/{id}/workstreams/{ws}): capture bundles hold cookies",
                "workstream_ids": extra,
            }
        )

    live_by_name = _resolve_agent_workstreams(client, agent_refs)
    live_ids = list(live_by_name.values())
    role_ids: dict[str, str | list[str]] = {
        tbl.WS_OPS: ops_id,
        tbl.WS_CONTEXT: ws_ids[WS_CONTEXT_NAME],
        tbl.WS_SME: [ws_ids[names[tbl.WS_SME]]],
    }
    if live_ids:
        role_ids[tbl.WS_LIVE_AGENTS] = live_ids
    orgdb = OrgDBClient.from_client(client)
    results = tbl.ensure_all_tables(orgdb, role_ids)

    bws.update_builder_config(
        ws,
        workstreams={**ws_ids, **live_by_name},
        stores=store_ids,
        tables={name: r["table_id"] for name, r in results.items()},
        role="client",
    )
    created_tables = [n for n, r in results.items() if r["created"]]
    data = {
        "dry_run": False,
        "workstreams": ws_ids,
        "agent_workstreams": live_by_name,
        "stores": store_ids,
        "tables": {n: r["table_id"] for n, r in results.items()},
        "created": {
            "workstreams": created_ws,
            "stores": created_stores,
            "tables": created_tables,
        },
        "warnings": store_notes,
        "grants": plan["grants"],
    }
    lines = [
        f"Builder setup for {ws.name}: "
        f"{len(created_ws)} workstream(s), {len(created_stores)} store(s), "
        f"{len(created_tables)} table(s) created; the rest reused.",
        "Ids recorded in builder.json (commit it).",
    ]
    if live_by_name:
        lines.append(
            "Live agent workstreams recorded in builder.json: " + ", ".join(sorted(live_by_name))
        )
    lines += [f"WARNING: {n['store']}: {n['warning']}" for n in store_notes]
    lines.append("Grants to apply (needs user ids; not done here):")
    lines += [f"  {g['workstream']}: {g['permission']} for {g['who']}" for g in plan["grants"]]
    if not live_ids:
        lines.append(
            "The platform-written tables are not linked to live agents' workstreams yet: rerun with "
            "--agent-workstream <name> once agents exist."
        )
    return Result.done(data, "\n".join(lines))


def register(registry: Registry) -> None:
    group = registry.group("builder", "Adopt Builder setup")
    p = group.command(
        "setup",
        _setup,
        help="Create or reuse the Builder workstreams, Doc Stores and tables in the client org",
        writes=True,
    )
    p.add_argument("--client", help="Client slug (default: env.json client, else the env name)")
    p.add_argument(
        "--agent-workstream",
        action="append",
        metavar="NAME",
        help="A live agent's workstream to link the platform-written tables to (repeatable)",
    )
