"""Read-only inventory commands: ``skill list``, ``plugin list``, ``workstream list``, ``session models``.

They answer "what is already on the platform?" before anything is pushed, without hand-written
curl calls. Shapes come from BFF_ROUTES.md sections 2, 4 and 6 and were checked live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cli.core.output import Result

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry


def _items(found: Any) -> list[dict[str, Any]]:
    rows = found.get("items", []) if isinstance(found, dict) else found
    return [r for r in rows or [] if isinstance(r, dict)]


def _cmd_skill_list(ctx: Context) -> Result:
    a = ctx.args
    rows = _items(ctx.harness_client().list_skills())
    if a.source:
        rows = [r for r in rows if r.get("source") == a.source]
    if a.search:
        rows = [r for r in rows if a.search.lower() in str(r.get("name", "")).lower()]
    lines = [f"{len(rows)} skill(s)  (name  source  has_process  plugin)"]
    for r in rows:
        lines.append(
            f"  {r.get('name')}  {r.get('source')}  "
            f"{'process' if r.get('has_process') else '-'}  {r.get('plugin') or '-'}"
        )
    lines.append("A name shown once as 'org' can hide a default skill of the same name (org wins).")
    return Result.done({"items": rows}, "\n".join(lines))


def _cmd_plugin_list(ctx: Context) -> Result:
    rows = _items(ctx.harness_client().list_plugins())
    lines = [f"{len(rows)} plugin(s)  (name  version  source  skills)"]
    lines += [
        f"  {r.get('name')}  {r.get('version')}  {r.get('source')}  {r.get('skill_count')}"
        for r in rows
    ]
    return Result.done({"items": rows}, "\n".join(lines))


def _cmd_workstream_list(ctx: Context) -> Result:
    rows = _items(ctx.harness_client().list_workstreams(search=ctx.args.search))
    lines = [f"{len(rows)} workstream(s)  (id  name  members)"]
    lines += [f"  {r.get('id')}  {r.get('name')}  {r.get('member_count', '?')}" for r in rows]
    return Result.done({"items": rows}, "\n".join(lines))


def _cmd_session_models(ctx: Context) -> Result:
    found = ctx.harness_client()._request("GET", "/v1/end-user/agent-harness/models")
    rows = _items(found)
    lines = [f"{len(rows)} model(s)  (model_id  provider  route  context  thinking)"]
    for r in rows:
        mark = "  [org default]" if r.get("is_default") else ""
        lines.append(
            f"  {r.get('model_id')}  {r.get('provider')}  {r.get('route')}  "
            f"{r.get('context_window')}  {'thinking' if r.get('thinking') else '-'}{mark}"
        )
    lines.append(
        "Pass one to `abcd session start --model <model_id>`; omit it for the org default."
    )
    return Result.done({"items": rows}, "\n".join(lines))


def register(registry: Registry) -> None:
    p = registry.group("skill").command(
        "list", _cmd_skill_list, help="List the org's skill catalog (default + org tiers)"
    )
    p.add_argument("--source", choices=["org", "default"], help="Only this tier")
    p.add_argument("--search", help="Substring of the skill name")
    registry.group("plugin").command("list", _cmd_plugin_list, help="List the org's plugins")
    p = registry.group("workstream").command(
        "list", _cmd_workstream_list, help="List the workstreams the PAT can see"
    )
    p.add_argument("--search", help="Substring of the workstream name")
    registry.group("session").command(
        "models", _cmd_session_models, help="List the models a session may use"
    )
