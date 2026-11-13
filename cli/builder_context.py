"""Context and knowledge graph commands, plus the shared ops helpers (PLAN.md sections 9, 12).

Commands:

* ``abcd context pull <agent>``: download the agent's KG page, its neighbours one or two edges
  away, and its decisions, open items, checks, metrics and criteria into
  ``workspaces/<env>/context/`` (ignored by the workspace repo).
* ``abcd context add <calls|emails|process-docs> FILE --client C``: upload to ``builder-context``.
* ``abcd kg page <type> <name> --file F [--propose]``, ``abcd kg link <from> <rel> <to>``,
  ``abcd kg impact <name>``.

The module also holds what the other ops commands share: :class:`DocStore` (Doc Store upload
and download through the BFF routes), the actor name, and small row helpers.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common.docstore import ConflictError, DocStore, NotFoundError, safe_relative
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.identity import current_user
from cli.builder_common.identity import slug as _ident_slug
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.tables import get_spec
from cli.builder_common.workspace import context_dir, load_builder_config

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.output import Result
    from cli.core.registry import Registry

CONTEXT_STORE = "builder-context"
OPS_STORE = "builder-ops"
STATUS_STORE_PREFIX = "builder-status-"

CONTEXT_KINDS = ("calls", "emails", "process-docs")
NODE_TYPES = get_spec("bld_kg_nodes").column("type").enum_values
RELATIONS = get_spec("bld_kg_edges").column("relation").enum_values
_KG_DIRS = {
    "client": "kg/clients",
    "system": "kg/systems",
    "api": "kg/apis",
    "skill": "kg/skills",
    "agent": "kg/agents",
    "dataset": "kg/datasets",
    "incident": "kg/incidents",
    "person": "kg/people",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IMPACT_DEPENDENCY_RELATIONS = ("uses", "calls")


# ---------------------------------------------------------------------------------------------
# Small helpers shared by the ops modules
# ---------------------------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def actor(ws: Path | None = None) -> str:
    """Who is running abcd (see ``builder_common.identity``)."""
    return current_user(ws)


def check_name(value: str, what: str) -> str:
    """A name that is safe in a Doc Store path and a file name."""
    if not _NAME_RE.match(value or ""):
        raise UsageError(f"{what} {value!r} must match {_NAME_RE.pattern}")
    return value


def open_db(ctx: Context) -> OrgDBClient:
    return OrgDBClient.from_client(ctx.harness_client())


def as_float(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def open_docs(ctx: Context, ws: Path | None = None) -> DocStore:
    cfg = load_builder_config(ws or ctx.env_dir)
    return DocStore(ctx.harness_client(), cfg["stores"])


# ---------------------------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------------------------


def node_slug(type_: str, name: str) -> str:
    s = _ident_slug(name.replace("/", " "), strict=True)
    if not s:
        raise UsageError(f"{name!r} has no usable letters or digits")
    return s


def node_key(type_: str, name: str) -> str:
    return f"{type_}:{node_slug(type_, name)}"


def page_path_for(type_: str, name: str) -> str:
    """Where a page lives in ``builder-context`` (section 9 layout)."""
    if type_ not in _KG_DIRS:
        raise UsageError(f"node type {type_!r} must be one of {', '.join(NODE_TYPES)}")
    if type_ == "api":
        system, _, api = name.partition("/")
        if not _ident_slug(system, strict=True) or not _ident_slug(api, strict=True):
            raise UsageError("an api name must be <system>/<api>")
        return f"kg/apis/{_ident_slug(system, strict=True)}/{_ident_slug(api, strict=True)}.md"
    return f"{_KG_DIRS[type_]}/{node_slug(type_, name)}.md"


def _edge_key(from_node: str, relation: str, to_node: str) -> str:
    return f"{from_node}|{relation}|{to_node}"


def _parse_node_ref(ref: str) -> str:
    """``type:slug`` as given, or ``type:Name`` normalised to a node key."""
    type_, sep, rest = ref.partition(":")
    if not sep or type_ not in NODE_TYPES or not rest.strip():
        raise UsageError(f"node {ref!r} must look like <type>:<name>, type one of {NODE_TYPES}")
    return f"{type_}:{node_slug(type_, rest)}"


def neighbours(
    edges: Sequence[Mapping[str, Any]], start: str, depth: int
) -> tuple[set[str], list[dict[str, Any]]]:
    """Nodes within ``depth`` edges of ``start`` (either direction) and the edges walked."""
    seen = {start}
    walked: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, dist = queue.popleft()
        if dist >= depth:
            continue
        for e in edges:
            a, b = str(e.get("from_node")), str(e.get("to_node"))
            if node not in (a, b):
                continue
            walked[str(e.get("edge_key") or _edge_key(a, str(e.get("relation")), b))] = dict(e)
            other = b if node == a else a
            if other not in seen:
                seen.add(other)
                queue.append((other, dist + 1))
    return seen, list(walked.values())


def compute_impact(
    start: str, edges: Sequence[Mapping[str, Any]], nodes: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """What a change to ``start`` touches: dependents, their tests, owners and history."""
    dependents: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, dist = queue.popleft()
        for e in edges:
            if e.get("to_node") == node and e.get("relation") in IMPACT_DEPENDENCY_RELATIONS:
                src = str(e["from_node"])
                if src != start and src not in dependents:
                    dependents[src] = {"node": src, "depth": dist + 1, "via": e.get("relation")}
                    queue.append((src, dist + 1))
    affected = [start, *dependents]
    tests = sorted(
        {
            str(e["to_node"])
            for e in edges
            if e.get("relation") == "tested_by" and e.get("from_node") in affected
        }
    )
    owners = sorted(
        {
            str(e["from_node"])
            for e in edges
            if e.get("relation") == "owns" and e.get("to_node") in affected
        }
    )
    history = [
        {"relation": e["relation"], "from": e["from_node"], "to": e["to_node"]}
        for e in edges
        if e.get("relation") in ("failed_on", "replaced_by")
        and start in (e["from_node"], e["to_node"])
    ]
    return {
        "node": start,
        "page_path": (nodes.get(start) or {}).get("page_path") or "",
        "dependents": sorted(dependents.values(), key=lambda d: (d["depth"], d["node"])),
        "tests_to_rerun": tests,
        "owners": owners,
        "history": history,
    }


def resolve_node(rows: Sequence[Mapping[str, Any]], ref: str) -> dict[str, Any]:
    """Find one node by key or by name (case-insensitive); ambiguity is a usage error."""
    exact = [r for r in rows if r.get("node_key") == ref]
    if exact:
        return dict(exact[0])
    by_name = [r for r in rows if str(r.get("name", "")).lower() == ref.lower()]
    if len(by_name) == 1:
        return dict(by_name[0])
    if len(by_name) > 1:
        keys = ", ".join(sorted(str(r["node_key"]) for r in by_name))
        raise UsageError(f"{ref!r} is ambiguous; use one of: {keys}")
    raise RefusedError(f"no knowledge graph node {ref!r}; create it with `abcd kg page`")


# ---------------------------------------------------------------------------------------------
# context pull
# ---------------------------------------------------------------------------------------------

AGENT_TABLES = {
    "decisions": "bld_decisions",
    "open_items": "bld_open_items",
    "checks": "bld_checks",
    "metrics": "bld_metrics",
    "criteria": "bld_release_criteria",
}


def pull_context(db: Any, docs: Any, ws: Path, agent: str, depth: int = 2) -> dict[str, Any]:
    """Write everything for ``agent`` under ``ws/context``; returns a summary. Nothing else is touched."""
    check_name(agent, "agent")
    root = context_dir(ws)
    written: list[str] = []
    missing: list[str] = []

    def write(rel: Path, data: bytes) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(rel.as_posix())

    nodes = {str(r["node_key"]): dict(r) for r in db.list_all("bld_kg_nodes")}
    edges = [dict(r) for r in db.list_all("bld_kg_edges")]
    start = node_key("agent", agent)
    if start not in nodes:
        named = [k for k, r in nodes.items() if r.get("type") == "agent" and r.get("name") == agent]
        start = named[0] if named else start
    has_node = start in nodes
    keep, walked = neighbours(edges, start, depth) if has_node else (set(), [])
    pages = [
        nodes[k]
        for k in sorted(keep)
        if nodes[k].get("page_path") and nodes[k].get("status") != "retired"
    ]
    for node in pages:
        rel = safe_relative(str(node["page_path"]))
        try:
            write(rel, docs.get(f"{CONTEXT_STORE}/{rel.as_posix()}"))
        except NotFoundError:
            missing.append(str(node["page_path"]))
    agent_dir = Path("agents") / agent
    graph = {
        "agent": agent,
        "root": start if has_node else None,
        "nodes": [nodes[k] for k in sorted(keep)],
        "edges": walked,
    }
    write(agent_dir / "graph.json", (json.dumps(graph, indent=2, default=str) + "\n").encode())
    counts: dict[str, int] = {}
    for name, table in AGENT_TABLES.items():
        rows = db.find_rows(table, "agent_key", agent)
        counts[name] = len(rows)
        write(agent_dir / f"{name}.json", (json.dumps(rows, indent=2, default=str) + "\n").encode())
    summary = {
        "agent": agent,
        "context_dir": str(root),
        "has_kg_node": has_node,
        "pages": len(pages) - len(missing),
        "missing_pages": missing,
        "rows": counts,
        "files": sorted(written),
    }
    write(
        agent_dir / "manifest.json",
        (json.dumps({**summary, "pulled_at": now_iso()}, indent=2) + "\n").encode(),
    )
    summary["files"] = sorted(written)
    return summary


def _cmd_context_pull(ctx: Context) -> Result:
    from cli.core.output import Result

    agent = check_name(ctx.args.agent, "agent")
    ws = ctx.env_dir
    if ctx.dry_run_notice(f"would download context for {agent} into {context_dir(ws)}"):
        return Result.done({"dry_run": True, "agent": agent})
    summary = pull_context(open_db(ctx), open_docs(ctx, ws), ws, agent, depth=ctx.args.depth)
    lines = [
        f"Context for {agent} written to {summary['context_dir']}",
        f"  KG pages: {summary['pages']}"
        + ("" if summary["has_kg_node"] else " (the agent has no KG node yet)"),
        "  rows: " + ", ".join(f"{k} {v}" for k, v in summary["rows"].items()),
    ]
    if summary["missing_pages"]:
        lines.append("  not in the Doc Store: " + ", ".join(summary["missing_pages"]))
    return Result.done(summary, "\n".join(lines))


# ---------------------------------------------------------------------------------------------
# context add
# ---------------------------------------------------------------------------------------------


def context_add_path(
    kind: str, client: str, file: Path, *, date: str, slug_: str, process: str
) -> str:
    suffix = file.suffix if file.suffix else ".md"
    if kind == "process-docs":
        if not process:
            raise UsageError("process-docs needs --process <name>")
        return f"context/process-docs/{client}/{check_name(process, 'process')}/sop{suffix}"
    if not _DATE_RE.match(date):
        raise UsageError(f"--date {date!r} must be YYYY-MM-DD")
    return f"context/{kind}/{client}/{date}-{slug_}{suffix}"


def _cmd_context_add(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    file = Path(a.file)
    if not file.is_file():
        raise UsageError(f"{file} is not a file")
    client = check_name(a.client, "client")
    name = _ident_slug(a.slug or file.stem, strict=True)
    if not name and a.kind != "process-docs":
        raise UsageError("--slug has no usable letters or digits")
    rel = context_add_path(
        a.kind, client, file, date=a.date or today(), slug_=name, process=a.process or ""
    )
    path = f"{CONTEXT_STORE}/{rel}"
    data = file.read_bytes()
    if ctx.dry_run_notice(f"would upload {file} ({len(data)} bytes) to {path}"):
        return Result.done({"dry_run": True, "path": path, "bytes": len(data)})
    try:
        digest = open_docs(ctx).put(path, data)
    except ConflictError as exc:
        raise RefusedError(
            f"{path} already exists; context files are never overwritten (use another --slug)"
        ) from exc
    return Result.done({"path": path, "sha256": digest, "bytes": len(data)}, f"Uploaded {path}")


# ---------------------------------------------------------------------------------------------
# kg page / link / impact
# ---------------------------------------------------------------------------------------------


def _append_log(docs: DocStore, line: str) -> str | None:
    """Append to ``kg/log.md`` (rewritten on purpose). Returns a warning, never raises."""
    path = f"{CONTEXT_STORE}/kg/log.md"
    try:
        try:
            current = docs.get(path).decode("utf-8")
        except NotFoundError:
            current = "# Knowledge graph log\n\n"
        docs.put_text(path, current.rstrip("\n") + "\n" + line + "\n", replace=True)
    except Exception as exc:
        return f"kg/log.md not updated: {exc}"
    return None


def write_kg_page(
    ctx: Context,
    type_: str,
    name: str,
    text: str,
    *,
    propose: bool,
    replace: bool,
    docs: DocStore | None = None,
    db: OrgDBClient | None = None,
) -> dict[str, Any]:
    """Write one KG page to the Doc Store and upsert its node. Shared by ``kg page`` and ``wire``."""
    key = node_key(type_, name)
    kg_path = page_path_for(type_, name)
    page = f"proposed/{kg_path}" if propose else kg_path
    status = "proposed" if propose else "vetted"
    path = f"{CONTEXT_STORE}/{page}"
    docs = docs or open_docs(ctx)
    try:
        digest = docs.put_text(path, text, replace=replace)
    except ConflictError as exc:
        raise RefusedError(f"{path} already exists; pass --replace to rewrite the page") from exc
    db = db or open_db(ctx)
    row = {
        "node_key": key,
        "type": type_,
        "name": name,
        "page_path": page,
        "status": status,
        "owner_user_id": actor(ctx.env_dir),
    }
    result = db.insert_rows("bld_kg_nodes", [row])
    result.raise_for_failures()
    if result.duplicates:
        db.update_row(
            "bld_kg_nodes", "node_key", key, {k: v for k, v in row.items() if k != "node_key"}
        )
    warning = _append_log(
        docs,
        f"- {now_iso()} {actor(ctx.env_dir)} {'proposed' if propose else 'wrote'} {key} ({page})",
    )
    data: dict[str, Any] = {
        "node_key": key,
        "page_path": page,
        "status": status,
        "sha256": digest,
        "updated_node": bool(result.duplicates),
    }
    if warning:
        data["warning"] = warning
    return data


def _cmd_kg_page(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    file = Path(a.file)
    if not file.is_file():
        raise UsageError(f"{file} is not a file")
    text = file.read_text(encoding="utf-8")
    key = node_key(a.type, a.name)
    page = (
        f"proposed/{page_path_for(a.type, a.name)}" if a.propose else page_path_for(a.type, a.name)
    )
    status = "proposed" if a.propose else "vetted"
    if ctx.dry_run_notice(f"would write {CONTEXT_STORE}/{page} and node {key} ({status})"):
        return Result.done({"dry_run": True, "node_key": key, "page_path": page, "status": status})
    data = write_kg_page(ctx, a.type, a.name, text, propose=a.propose, replace=a.replace)
    return Result.done(data, f"Wrote {CONTEXT_STORE}/{data['page_path']} and node {key} ({status})")


def add_kg_edge(
    ctx: Context,
    from_node: str,
    relation: str,
    to_node: str,
    *,
    propose: bool,
    source: str | None = None,
    db: OrgDBClient | None = None,
) -> dict[str, Any]:
    """Add one typed edge between two existing nodes. Shared by ``kg link`` and ``wire``."""
    if relation not in RELATIONS:
        raise UsageError(f"relation {relation!r} must be one of {', '.join(RELATIONS)}")
    src, dst = _parse_node_ref(from_node), _parse_node_ref(to_node)
    if src == dst:
        raise UsageError("an edge needs two different nodes")
    row: dict[str, Any] = {
        "edge_key": _edge_key(src, relation, dst),
        "from_node": src,
        "to_node": dst,
        "relation": relation,
        "status": "proposed" if propose else "vetted",
    }
    if source:
        row["source_ref"] = source
    db = db or open_db(ctx)
    known = {str(r["node_key"]) for r in db.list_all("bld_kg_nodes")}
    absent = [n for n in (src, dst) if n not in known]
    if absent:
        raise RefusedError(
            f"no knowledge graph node {', '.join(absent)}; create with `abcd kg page`"
        )
    res = db.insert_rows("bld_kg_edges", [row])
    res.raise_for_failures()
    return {**row, "already_existed": bool(res.duplicates)}


def _cmd_kg_link(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    if a.relation not in RELATIONS:
        raise UsageError(f"relation {a.relation!r} must be one of {', '.join(RELATIONS)}")
    src, dst = _parse_node_ref(a.from_node), _parse_node_ref(a.to_node)
    status = "proposed" if a.propose else "vetted"
    if ctx.dry_run_notice(f"would add edge {src} --{a.relation}--> {dst} ({status})"):
        return Result.done(
            {
                "dry_run": True,
                "edge_key": _edge_key(src, a.relation, dst),
                "from_node": src,
                "to_node": dst,
                "relation": a.relation,
                "status": status,
                **({"source_ref": a.source} if a.source else {}),
            }
        )
    data = add_kg_edge(ctx, a.from_node, a.relation, a.to_node, propose=a.propose, source=a.source)
    text = f"Edge {src} --{a.relation}--> {dst} " + (
        "already exists" if data["already_existed"] else f"added ({status})"
    )
    return Result.done(data, text)


def _cmd_kg_impact(ctx: Context) -> Result:
    from cli.core.output import Result

    db = open_db(ctx)
    rows = db.list_all("bld_kg_nodes")
    node = resolve_node(rows, ctx.args.name)
    nodes = {str(r["node_key"]): r for r in rows}
    impact = compute_impact(str(node["node_key"]), db.list_all("bld_kg_edges"), nodes)
    lines = [f"Changing {impact['node']} affects:"]
    lines += [
        f"  depends on it (depth {d['depth']}, {d['via']}): {d['node']}"
        for d in impact["dependents"]
    ] or ["  nothing depends on it"]
    if impact["tests_to_rerun"]:
        lines.append("  re-run tests: " + ", ".join(impact["tests_to_rerun"]))
    if impact["owners"]:
        lines.append("  tell owners: " + ", ".join(impact["owners"]))
    lines += [f"  history: {h['from']} {h['relation']} {h['to']}" for h in impact["history"]]
    return Result.done(impact, "\n".join(lines))


def register(registry: Registry) -> None:
    g = registry.group("context", "Pull and add client context")
    p = g.command(
        "pull", _cmd_context_pull, help="Download an agent's context into workspaces/<env>/context/"
    )
    p.add_argument("agent")
    p.add_argument("--depth", type=int, default=2, help="Edges away from the agent page to include")
    p = g.command(
        "add",
        _cmd_context_add,
        help="Upload calls, emails or process docs to builder-context",
        writes=True,
    )
    p.add_argument("kind", choices=CONTEXT_KINDS)
    p.add_argument("file")
    p.add_argument("--client", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default today); calls and emails")
    p.add_argument("--slug", help="Name part of the path (default: the file name)")
    p.add_argument("--process", help="Process name (process-docs)")

    k = registry.group("kg", "Knowledge graph pages and links")
    p = k.command("page", _cmd_kg_page, help="Write a KG page and its node", writes=True)
    p.add_argument("type", choices=NODE_TYPES)
    p.add_argument("name", help="Node name (api: <system>/<api>)")
    p.add_argument("--file", required=True)
    p.add_argument("--propose", action="store_true", help="Write to proposed/ for review")
    p.add_argument("--replace", action="store_true", help="Rewrite an existing page")
    p = k.command("link", _cmd_kg_link, help="Add a typed edge between two nodes", writes=True)
    p.add_argument("from_node", metavar="from", help="<type>:<name>")
    p.add_argument("relation", help=" | ".join(RELATIONS))
    p.add_argument("to_node", metavar="to", help="<type>:<name>")
    p.add_argument("--source", help="Call, doc, run or release that showed it")
    p.add_argument("--propose", action="store_true")
    p = k.command("impact", _cmd_kg_impact, help="What a change to a node affects, via edges")
    p.add_argument("name", help="Node key or name")
