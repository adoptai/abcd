"""``abcd wire``: put a NoUI-compiled skill to work in an agent (PLAN.md R15, sections 10 and 12).

NoUI compiles a site into ``workspaces/<env>/harness/skills/<skill_id>/`` and stops there. These
commands add the wiring the Builder owns:

* ``abcd wire skill <skill> --agent A``: the skill becomes a component of the agent bundle
  (``process.yaml``). A browser skill with no approved replay is refused.
* ``abcd wire kg <skill> [--agent A]``: knowledge graph pages (``kg/skills/<skill>.md``, one
  ``kg/apis/<system>/<api>.md`` per API operation) and the ``uses``/``calls`` edges, written to
  ``proposed/`` unless ``--vetted``. Pages hold names, kinds and digests, never values from the
  recording.
* ``abcd wire goal-check <skill> --agent A``: the goal NoUI recorded becomes a draft
  ``goal_reached`` check, proposed through ``bld_build_plan`` for the FDE to approve.
  ``--activate`` turns the draft on once that plan item is approved.
* ``abcd wire all <skill> --agent A``: the three, in order.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cli import builder_agent as bag
from cli import builder_context as bc
from cli.builder_common import agent_bundle as ab
from cli.builder_common import browser_gate as bg
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.identity import slug
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.tables import validate_row
from cli.core.output import Result
from cli.core.registry import Registry
from cli.harness_skill import _SLUG_RE

if TYPE_CHECKING:
    from cli.core.context import Context

PLAN_TABLE = "bld_build_plan"
APPROVED_PLAN_STATES = ("approved", "building", "done")
_URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")
_MIN_SECRET_LEN = 6


# ------------------------------------------------------------------------------ the skill


def resolve_skill(ctx: Context, ref: str) -> Path:
    """``ref`` is a skill directory or a name under ``harness/skills/``."""
    path = Path(ref)
    skill_dir = path if path.is_dir() else bws.harness_dir(ctx.env_dir) / "skills" / ref
    if not (skill_dir / "SKILL.md").is_file():
        raise UsageError(f"{ref!r} is not a compiled skill: no SKILL.md in {skill_dir}")
    if not _SLUG_RE.match(skill_dir.name):
        raise UsageError(f"skill name {skill_dir.name!r} must match ^[a-z0-9][a-z0-9-]*$")
    return skill_dir


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refuse_unapproved_browser_skill(skill_dir: Path) -> bool:
    """Refuse a browser skill whose replay is not approved. Returns whether it is a browser skill."""
    if not bg.is_browser_skill(skill_dir):
        return False
    gate, _ = bg._noui()
    try:
        gate.check_installable(skill_dir)
    except gate.NotApprovedError as exc:
        name = skill_dir.name
        raise RefusedError(
            f"{name} is a browser skill with no approved replay: {exc}\n"
            f"  1. replay it:  abcd noui verify_replay ws:skills/{name} --profile-slug <slug>\n"
            f"  2. show the member the result, then record their approval:\n"
            f"     abcd noui verify_approve ws:skills/{name}\n"
            f"then run `abcd wire` again."
        ) from exc
    return True


# ------------------------------------------------------------------------------ wire skill


def wire_skill(ctx: Context, skill_dir: Path, agent: str) -> dict[str, Any]:
    browser = refuse_unapproved_browser_skill(skill_dir)
    ws = ctx.env_dir
    bundle = bag.load_agent_bundle(ws, agent)
    comp = ("skill", "org", skill_dir.name)
    present = comp in bundle.components
    data: dict[str, Any] = {
        "skill": skill_dir.name,
        "agent": agent,
        "component": {"type": comp[0], "source": comp[1], "ref": comp[2]},
        "browser_skill": browser,
        "added": not present,
    }
    if present or ctx.dry_run:
        data["dry_run"] = ctx.dry_run and not present
        return data
    ab.write_bundle(
        replace(bundle, components=(*bundle.components, comp)), bws.agent_dir(ws, agent)
    )
    return data


def _skill_text(d: dict[str, Any]) -> str:
    if d.get("dry_run"):
        return f"DRY RUN: would add skill {d['skill']} (org) to agent {d['agent']}"
    if d["added"]:
        return (
            f"Added skill {d['skill']} (org) to harness/agents/{d['agent']}/process.yaml. "
            f"Push it with `abcd agent push {d['agent']}`."
        )
    return f"Skill {d['skill']} is already a component of agent {d['agent']}; nothing changed."


# ------------------------------------------------------------------------------ the goal


def recorded_goal(skill_dir: Path) -> dict[str, str]:
    """The goal NoUI recorded: the replay approval's goal, else the one ``goals.py`` reads off the
    operations (the last download, else the last submit, else the last operation reached)."""
    approval = _json(skill_dir / bg.APPROVAL_FILE)
    for g in approval.get("goals") or []:
        if isinstance(g, dict) and g.get("name"):
            return _goal_dict(g)
    bg._noui()
    from noui_core.compile import goals

    ops = bg._operations(skill_dir) or [
        o for o in _json(skill_dir / "manifest.json").get("operations") or [] if isinstance(o, dict)
    ]
    goal = goals.infer_primary_goal(ops)
    if not goal or not goal.get("name"):
        raise RefusedError(f"{skill_dir.name} records no operations, so it has no goal to check")
    return _goal_dict(goal)


def _goal_dict(g: dict[str, Any]) -> dict[str, str]:
    return {
        "operation": str(g["name"]),
        "kind": str(g.get("kind") or "read"),
        "description": _clean(str(g.get("description") or "")),
    }


# ------------------------------------------------------------------------------ KG pages


def _clean(text: str) -> str:
    """Every URL reduced to scheme, host and path: no query, fragment or credentials."""

    def one(m: re.Match[str]) -> str:
        try:
            u = urlsplit(m.group(0))
            host = u.hostname or ""
            port = f":{u.port}" if u.port else ""
        except ValueError:
            return "<url>"
        return f"{u.scheme}://{host}{port}{u.path}"

    return _URL_RE.sub(one, text)


def _origin(url: str) -> str:
    try:
        u = urlsplit(url)
        return f"{u.scheme}://{u.hostname}{f':{u.port}' if u.port else ''}" if u.hostname else ""
    except ValueError:
        return ""


def recording_values(skill_dir: Path) -> set[str]:
    """Cookie, credential and query values in the capture, used only to prove none reach a page."""
    manifest = _json(skill_dir / "manifest.json")
    candidates = [skill_dir / bg.RECORDING_BUNDLE_FILE]
    bundle_file = (manifest.get("provenance") or {}).get("bundle_file")
    if isinstance(bundle_file, str) and bundle_file:
        candidates.append(skill_dir.parent.parent / bundle_file)
    values: set[str] = set()
    for path in candidates:
        bundle = _json(path)
        entries = ((bundle.get("har") or {}).get("log") or {}).get("entries") or []
        for e in entries:
            req = (e.get("request") or {}) if isinstance(e, dict) else {}
            for c in req.get("cookies") or []:
                values.add(str(c.get("value", "")))
            for h in req.get("headers") or []:
                if str(h.get("name", "")).lower() in ("cookie", "authorization", "x-api-key"):
                    values.update(re.split(r"[;=\s]+", str(h.get("value", ""))))
            for q in req.get("queryString") or []:
                values.add(str(q.get("value", "")))
            for c in (e.get("response") or {}).get("cookies") or []:
                values.add(str(c.get("value", "")))
    return {v for v in values if len(v) >= _MIN_SECRET_LEN}


def _guard(skill_dir: Path, pages: dict[str, str]) -> None:
    leaked = [v for v in recording_values(skill_dir) if any(v in t for t in pages.values())]
    if leaked:
        raise RefusedError(
            f"{len(leaked)} value(s) from the recording would reach the knowledge graph pages; "
            "nothing was written. Remove them from the skill's descriptions first."
        )


def _ops(skill_dir: Path) -> list[dict[str, Any]]:
    manifest = _json(skill_dir / "manifest.json")
    ops = bg._operations(skill_dir)
    by_name = {
        str(o.get("name")): o for o in manifest.get("operations") or [] if isinstance(o, dict)
    }
    # operations.json carries the recipe; the manifest entry fills in kind and description.
    merged = [{**by_name.get(str(o.get("name")), {}), **o} for o in ops]
    return merged or list(by_name.values())


def _is_api_op(op: dict[str, Any]) -> bool:
    return bool(op.get("method")) and (op.get("tool") or "call_web_api") != "call_web_browser"


def system_of(skill_dir: Path) -> str:
    manifest = _json(skill_dir / "manifest.json")
    app = manifest.get("app") or {}
    return (
        slug(str(app.get("slug") or app.get("name") or skill_dir.name), strict=True)
        or skill_dir.name
    )


def _names(items: Any) -> str:
    names = [str(p.get("name")) for p in items or [] if isinstance(p, dict) and p.get("name")]
    return ", ".join(f"`{n}`" for n in names) or "none"


def skill_page(skill_dir: Path, goal: dict[str, str], system: str) -> str:
    manifest = _json(skill_dir / "manifest.json")
    approval = _json(skill_dir / bg.APPROVAL_FILE)
    prov = manifest.get("provenance") or {}
    workflow = manifest.get("workflow") or {}
    ops = _ops(skill_dir)
    origin = _origin(str(workflow.get("start_url") or ""))
    lines = [
        f"# Skill: {skill_dir.name}",
        "",
        f"Compiled by NoUI from a recording of **{_clean(str((manifest.get('app') or {}).get('name') or system))}**.",
        "",
        "## What it does",
        "",
        _clean(str(workflow.get("name") or skill_dir.name)),
        "",
        "## Source system",
        "",
        f"- system: `{system}` (see `kg/systems/{system}.md`)",
        *([f"- origin: {origin}"] if origin else []),
        f"- kind: {'browser' if bg.is_browser_skill(skill_dir) else 'API (call_web_api)'}",
        "",
        "## Goal",
        "",
        f"- operation: `{goal['operation']}` ({goal['kind']})",
        *([f"- {goal['description']}"] if goal["description"] else []),
        "",
        "## Operations",
        "",
    ]
    for op in ops:
        desc = _clean(str(op.get("description") or ""))
        kind = op.get("kind") or "read"
        lines.append(f"- `{op.get('name')}` ({kind}){f': {desc}' if desc else ''}")
        if op.get("parameters"):
            lines.append(f"  - parameters: {_names(op['parameters'])}")
    lines += ["", "## Provenance", ""]
    digests = {
        "provenance_version": prov.get("provenance_version"),
        "bundle_sha256": prov.get("bundle_sha256"),
        "steps_sha256": prov.get("steps_sha256"),
        "recorded_events": prov.get("recorded_events"),
        "recorded_urls": prov.get("recorded_urls"),
        "replay_fingerprint": approval.get("fingerprint"),
        "replay_approved": bool(approval.get("installable")),
    }
    lines += [f"- {k}: `{v}`" for k, v in digests.items() if v not in (None, "")]
    lines += [
        "",
        "Values from the recording (cookies, query strings, typed text) are never kept here.",
        "",
    ]
    return "\n".join(lines)


def api_page(skill: str, system: str, op: dict[str, Any]) -> str:
    template = _clean(str(op.get("url_template") or op.get("path") or ""))
    path = urlsplit(template).path if "://" in template else template.split("?")[0]
    origin = _origin(template)
    auth = op.get("auth") or {}
    lines = [
        f"# API: {system}/{slug(str(op.get('name')), strict=True)}",
        "",
        _clean(str(op.get("description") or op.get("name") or "")),
        "",
        f"- request: `{str(op.get('method')).upper()} {path}`",
        *([f"- origin: {origin}"] if origin else []),
        f"- called by skill: `{skill}` (operation `{op.get('name')}`)",
        *([f"- auth: {auth.get('type')}"] if isinstance(auth, dict) and auth.get("type") else []),
        f"- path parameters: {_names(op.get('path_params'))}",
        f"- query parameters: {_names(op.get('query_params'))}",
        f"- body parameters: {_names(op.get('body_params'))}",
        "",
    ]
    return "\n".join(lines)


def system_page(system: str, origin: str) -> str:
    return "\n".join([f"# System: {system}", "", *([f"- origin: {origin}"] if origin else []), ""])


def wire_kg(ctx: Context, skill_dir: Path, agent: str | None, *, vetted: bool) -> dict[str, Any]:
    skill, system = skill_dir.name, system_of(skill_dir)
    goal = recorded_goal(skill_dir)
    api_ops = [o for o in _ops(skill_dir) if _is_api_op(o)]
    pages: dict[str, str] = {f"skill:{skill}": skill_page(skill_dir, goal, system)}
    for op in api_ops:
        pages[f"api:{system}/{slug(str(op.get('name')), strict=True)}"] = api_page(
            skill, system, op
        )
    _guard(skill_dir, pages)
    propose = not vetted
    status = "vetted" if vetted else "proposed"
    plan = [
        {
            "node": key,
            "page": ("proposed/" if propose else "") + bc.page_path_for(*key.split(":", 1)),
        }
        for key in pages
    ]
    data: dict[str, Any] = {
        "skill": skill,
        "status": status,
        "pages": plan,
        "edges": [],
        "notes": [],
    }
    if ctx.dry_run:
        data["dry_run"] = True
        return data

    db = bc.open_db(ctx)
    docs = bc.open_docs(ctx)
    known = {str(r["node_key"]) for r in db.list_all("bld_kg_nodes")}
    if bc.node_key("system", system) not in known:
        origin = _origin(
            str(_json(skill_dir / "manifest.json").get("workflow", {}).get("start_url"))
        )
        bc.write_kg_page(
            ctx, "system", system, system_page(system, origin), propose=propose, replace=True,
            docs=docs, db=db,
        )  # fmt: skip
        data["notes"].append(f"created system node {bc.node_key('system', system)}")
    for key, text in pages.items():
        type_, name = key.split(":", 1)
        bc.write_kg_page(ctx, type_, name, text, propose=propose, replace=True, docs=docs, db=db)
    source = f"noui:{skill}"
    edges: list[tuple[str, str, str, str, str]] = [("skill", skill, "uses", "system", system)]
    for k in pages:
        if k.startswith("api:"):
            edges.append(("skill", skill, "calls", "api", k.split(":", 1)[1]))
    if agent:
        if bc.node_key("agent", agent) in {str(r["node_key"]) for r in db.list_all("bld_kg_nodes")}:
            edges.append(("agent", agent, "uses", "skill", skill))
        else:
            data["notes"].append(
                f"no agent node {bc.node_key('agent', agent)}: the agent uses skill edge was not "
                "written (add the agent page with `abcd kg page agent ...`, then run this again)"
            )
    for ft, fn, rel, tt, tn in edges:
        edge = bc.add_kg_edge(
            ctx,
            bc.node_key(ft, fn),
            rel,
            bc.node_key(tt, tn),
            propose=propose,
            source=source,
            db=db,
        )
        data["edges"].append(
            {k: edge[k] for k in ("from_node", "relation", "to_node", "status", "already_existed")}
        )
    return data


def _kg_text(d: dict[str, Any]) -> str:
    head = "DRY RUN: would write" if d.get("dry_run") else "Wrote"
    lines = [f"{head} {len(d['pages'])} KG page(s) for {d['skill']} ({d['status']}):"]
    lines += [f"  {p['page']}" for p in d["pages"]]
    lines += [f"  edge {e['from_node']} --{e['relation']}--> {e['to_node']}" for e in d["edges"]]
    lines += [f"  note: {n}" for n in d["notes"]]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ goal check


def check_id_for(skill: str) -> str:
    return f"{skill}-goal-reached"


def plan_key_for(skill: str, agent: str) -> str:
    return f"check-{agent}-{check_id_for(skill)}"


def _plan_row(db: OrgDBClient, item_key: str) -> dict[str, Any] | None:
    rows = db.find_rows(PLAN_TABLE, "item_key", item_key)
    return rows[0] if rows else None


def wire_goal_check(
    ctx: Context, skill_dir: Path, agent: str, *, activate: bool = False
) -> dict[str, Any]:
    ws = ctx.env_dir
    skill = skill_dir.name
    bag.load_agent_bundle(ws, agent)
    goal = recorded_goal(skill_dir)
    cid = check_id_for(skill)
    path = bws.checks_dir(ws) / f"{cid}.yaml"
    import yaml

    existing = yaml.safe_load(path.read_text()) if path.exists() else None
    if existing and existing.get("agent") != agent:
        raise RefusedError(f"check {cid!r} belongs to agent {existing.get('agent')!r}")
    check = existing or {
        "check_id": cid,
        "agent": agent,
        "title": f"{skill}: goal reached ({goal['operation']})",
        "kind": "goal_reached",
        "severity": "error",
        "status": "draft",
        "source": f"noui:{skill}",
        "goal": {"operation": goal["operation"], "kind": goal["kind"]},
    }
    item_key = plan_key_for(skill, agent)
    row = {
        "item_key": item_key,
        "kind": "check",
        "name": f"Goal check for {skill} on {agent}",
        "action": "new",
        "status": "proposed",
        "agent_key": agent,
        "reason_ref": f"noui:{skill}",
    }
    validate_row(PLAN_TABLE, row)
    data: dict[str, Any] = {
        "check_id": cid,
        "skill": skill,
        "path": str(path.relative_to(ws)),
        "agent": agent,
        "goal": check["goal"],
        "check_status": check.get("status"),
        "plan_item": item_key,
        "drafted": existing is None,
        "activated": False,
    }
    if ctx.dry_run:
        data["dry_run"] = True
        return data
    db = bc.open_db(ctx)
    if existing is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(check, sort_keys=False, allow_unicode=True))
    res = db.insert_rows(PLAN_TABLE, [row])
    res.raise_for_failures()
    data["proposed"] = not res.duplicates
    if activate:
        plan = _plan_row(db, item_key)
        if not plan or plan.get("status") not in APPROVED_PLAN_STATES:
            raise RefusedError(
                f"the build-plan item {item_key} is {plan.get('status') if plan else 'missing'}: "
                f"an FDE approves it first (`abcd plan approve {item_key} --by <name> --yes`)"
            )
        if check.get("status") != "active":
            check["status"] = "active"
            path.write_text(yaml.safe_dump(check, sort_keys=False, allow_unicode=True))
        mirror = {
            "check_key": cid,
            "agent_key": agent,
            "title": check["title"],
            "kind": "goal_reached",
            "rule": dict(check["goal"]),
            "severity": "blocking",
            "source_decision": item_key,
        }
        out = db.insert_rows("bld_checks", [{**mirror, "active_from": bc.now_iso()}])
        out.raise_for_failures()
        if out.duplicates:
            db.update_row(
                "bld_checks",
                "check_key",
                cid,
                {k: v for k, v in mirror.items() if k != "check_key"},
            )
        data.update(activated=True, check_status="active")
    return data


def _goal_text(d: dict[str, Any]) -> str:
    if d.get("dry_run"):
        return f"DRY RUN: would draft {d['path']} and propose {d['plan_item']}"
    if d["activated"]:
        return f"Check {d['check_id']} is active for agent {d['agent']}."
    return (
        f"{'Drafted' if d['drafted'] else 'Kept'} {d['path']} ({d['check_status']}) and "
        f"{'proposed' if d.get('proposed') else 'found'} build-plan item {d['plan_item']}. "
        f"Once an FDE approves it, `abcd wire goal-check {d['skill']} --agent "
        f"{d['agent']} --activate` turns it on."
    )


# ------------------------------------------------------------------------------ handlers


def _agent(ctx: Context, required: bool = True) -> str | None:
    agent = getattr(ctx.args, "agent", None)
    if required and not agent:
        raise UsageError("--agent is required")
    return str(agent) if agent else None


def _cmd_skill(ctx: Context) -> Result:
    d = wire_skill(ctx, resolve_skill(ctx, ctx.args.skill), str(_agent(ctx)))
    return Result.done(d, _skill_text(d))


def _cmd_kg(ctx: Context) -> Result:
    d = wire_kg(ctx, resolve_skill(ctx, ctx.args.skill), _agent(ctx, False), vetted=ctx.args.vetted)
    return Result.done(d, _kg_text(d))


def _cmd_goal_check(ctx: Context) -> Result:
    d = wire_goal_check(
        ctx, resolve_skill(ctx, ctx.args.skill), str(_agent(ctx)), activate=ctx.args.activate
    )
    return Result.done(d, _goal_text(d))


def _cmd_all(ctx: Context) -> Result:
    skill_dir, agent = resolve_skill(ctx, ctx.args.skill), str(_agent(ctx))
    refuse_unapproved_browser_skill(skill_dir)  # nothing is written for a skill that cannot ship
    s = wire_skill(ctx, skill_dir, agent)
    k = wire_kg(ctx, skill_dir, agent, vetted=ctx.args.vetted)
    g = wire_goal_check(ctx, skill_dir, agent)
    text = "\n\n".join((_skill_text(s), _kg_text(k), _goal_text(g)))
    return Result.done({"skill": s, "kg": k, "goal_check": g}, text)


def register(registry: Registry) -> None:
    g = registry.group("wire", "Wire a NoUI-compiled skill into an agent")
    p = g.command("skill", _cmd_skill, help="Add the skill to an agent's process.yaml", writes=True)
    p.add_argument("skill", help="Skill directory or name under harness/skills/")
    p.add_argument("--agent", required=True)
    p = g.command("kg", _cmd_kg, help="Write the skill's KG pages and edges", writes=True)
    p.add_argument("skill")
    p.add_argument("--agent", help="Also write the agent --uses--> skill edge")
    p.add_argument("--vetted", action="store_true", help="Write vetted pages instead of proposed/")
    p = g.command(
        "goal-check",
        _cmd_goal_check,
        help="Draft the skill's goal_reached check and propose it in the build plan",
        writes=True,
    )
    p.add_argument("skill")
    p.add_argument("--agent", required=True)
    p.add_argument("--activate", action="store_true", help="Turn the draft on (plan item approved)")
    p = g.command("all", _cmd_all, help="wire skill, then kg, then goal-check", writes=True)
    p.add_argument("skill")
    p.add_argument("--agent", required=True)
    p.add_argument("--vetted", action="store_true")
