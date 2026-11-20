"""``abcd hitl policy``: list, read, create, enable and disable org escalation policies.

A policy holds (escalates), denies or notifies on tool calls of the agent harness. The rules
live in ``rules_json`` and each policy is bound to a scope (workstream, process or the whole
org). Routes: adoptwebui ``/v1/org/policies``. Reads are open to every member; writes need an
Admin PAT, so a 403 is reported as a refusal, not a crash.

A policy escalation then reaches the member as a ``policy-...`` card (``abcd hitl list``) and is
answered with ``abcd hitl decide``.

There is deliberately no delete command: disable a policy instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_session import get_client, resolve_workstream
from cli.core.output import Result
from cli.harness_common.api_client import HarnessAPIError

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

_ACTIONS = ("escalate", "deny", "notify")
_HOOKS = (
    "pre_tool_use",
    "post_tool_use",
    "turn_start",
    "turn_end",
    "escalation_route",
    "notification_route",
)
_ROLES = ("admin", "reviewer")


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a client call; a 403 becomes a friendly refusal."""
    try:
        return fn(*args, **kwargs)
    except HarnessAPIError as e:
        if e.status_code == 403:
            raise RefusedError(
                "the platform refused this: creating or changing org policies needs an Admin "
                "user (a PAT of a non-admin member can only list and read them)"
            ) from e
        if e.status_code == 404:
            raise UsageError(f"policy not found ({e.url})") from e
        if e.status_code == 422:
            raise UsageError(f"the platform rejected the policy: {e.body}") from e
        raise PlatformError(str(e)) from e


def _binding_summary(policy: dict[str, Any]) -> str:
    parts = []
    for b in policy.get("bindings") or []:
        scope = b.get("scope_kind", "?")
        parts.append(f"{scope}:{b['scope_id']}" if b.get("scope_id") else str(scope))
    return ", ".join(parts) or f"{policy.get('binding_count', 0)} binding(s)"


def _row(p: dict[str, Any]) -> str:
    return f"{p.get('id')}  {p.get('status', '?'):<8}  {p.get('name')}  [{_binding_summary(p)}]"


def _items(resp: Any) -> list[dict[str, Any]]:
    rows = resp.get("items", []) if isinstance(resp, dict) else resp
    return [r for r in rows or [] if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Rule and binding construction (pure: unit tested)
# ---------------------------------------------------------------------------


def parse_assignee(text: str) -> dict[str, str]:
    """``admin`` / ``role:reviewer`` / ``member:<org-member-id>`` -> ``{kind, value}``."""
    kind, sep, value = text.partition(":")
    if not sep:
        kind, value = "role", text
    kind, value = kind.strip(), value.strip()
    if kind not in ("role", "member") or not value:
        raise UsageError(f"--assignee {text!r}: use admin, reviewer, role:<role> or member:<id>")
    if kind == "role" and value not in _ROLES:
        raise UsageError(
            f"--assignee role {value!r} reaches nobody: use one of {', '.join(_ROLES)}"
        )
    return {"kind": kind, "value": value}


def build_rules(
    tools: list[str], action: str, assignee: dict[str, str] | None, rule_id: str = "r1"
) -> dict[str, Any]:
    """The rules document: WHEN tool_name matches one of ``tools`` THEN ``action``."""
    if not tools:
        raise UsageError("give at least one --tool (the tool name to hold; * is a glob)")
    if any(not t.strip() for t in tools):
        raise UsageError("--tool must not be empty")
    if len(tools) > 1:
        cond: dict[str, Any] = {"field": "tool_name", "op": "in", "value": list(tools)}
        if any("*" in t for t in tools):
            raise UsageError("a glob (*) works with a single --tool only")
    elif "*" in tools[0]:
        cond = {"field": "tool_name", "op": "matches_glob", "value": tools[0]}
    else:
        cond = {"field": "tool_name", "op": "eq", "value": tools[0]}
    params: dict[str, Any] = {}
    if action == "escalate":
        params["assignee"] = assignee or {"kind": "role", "value": "admin"}
    return {
        "version": 1,
        "default_action": "allow",
        "rules": [{"id": rule_id, "when": [cond], "then": {"action": action, "params": params}}],
    }


def build_bindings(
    workstream_ids: list[str], processes: list[str], org_wide: bool, hooks: list[str]
) -> list[dict[str, Any]]:
    if not (workstream_ids or processes or org_wide):
        raise UsageError(
            "a policy needs a scope: --workstream <name|id> (repeatable), --process <template>, "
            "or --org-wide (affects every workstream; needs --yes)"
        )
    bindings: list[dict[str, Any]] = []
    if org_wide:
        bindings.append({"scope_kind": "org", "scope_id": None, "hooks": hooks})
    bindings += [
        {"scope_kind": "workstream", "scope_id": w, "hooks": hooks} for w in workstream_ids
    ]
    bindings += [{"scope_kind": "process", "scope_id": p, "hooks": hooks} for p in processes]
    return bindings


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_list(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    if a.workstream:
        ws = resolve_workstream(client, a.workstream, ctx.env)
        resp = _call(client.list_policies, "workstream", ws)
    else:
        resp = _call(client.list_policies)
    rows = _items(resp)
    if a.status:
        rows = [r for r in rows if r.get("status") == a.status]
    text = "\n".join(_row(r) for r in rows) or "no policies"
    return Result.done({"policies": rows, "total": len(rows)}, text)


def _cmd_get(ctx: Context) -> Result:
    p = _call(get_client(ctx).get_policy, ctx.args.policy)
    lines = [_row(p), f"rules: {json.dumps(p.get('rules_json'), sort_keys=True)}"]
    for b in p.get("bindings") or []:
        lines.append(f"binding {b.get('scope_kind')}:{b.get('scope_id')} hooks={b.get('hooks')}")
    return Result.done(p, "\n".join(lines))


def _cmd_create(ctx: Context) -> Result:
    a = ctx.args
    if a.org_wide and not ctx.yes:
        raise RefusedError(
            "an org-wide policy applies to every workstream of the org: pass --yes to confirm"
        )
    if a.rules_file:
        try:
            rules = json.loads(Path(a.rules_file).read_text())
        except (OSError, ValueError) as e:
            raise UsageError(f"--rules-file {a.rules_file}: {e}") from e
        if not isinstance(rules, dict):
            raise UsageError("--rules-file must hold a JSON object (the rules document)")
        if a.tool:
            raise UsageError("--tool and --rules-file are exclusive")
    else:
        assignee = parse_assignee(a.assignee) if a.assignee else None
        if assignee and a.action != "escalate":
            raise UsageError("--assignee only applies to --action escalate")
        rules = build_rules(a.tool or [], a.action, assignee)
    if not a.name.strip():
        raise UsageError("--name must not be empty")
    hooks = a.hook or ["pre_tool_use"]
    client = get_client(ctx)
    ws_ids = [resolve_workstream(client, w, ctx.env) for w in a.workstream or []]
    for ref, wid in zip(a.workstream or [], ws_ids, strict=True):
        if not client.workstream_exists(wid):  # a typo must not become a binding that never matches
            raise UsageError(f"workstream not found: {ref!r} (try `abcd workstream list`)")
    bindings = build_bindings(ws_ids, a.process or [], a.org_wide, hooks)
    payload = {
        "name": a.name,
        "description": a.description,
        "rules_json": rules,
        "bindings": bindings,
    }
    if ctx.dry_run:
        return Result.done(
            {"payload": payload, "then_activate": a.enable},
            "DRY RUN: would POST /v1/org/policies (starts as a draft"
            + ("; then activate it" if a.enable else "; `abcd hitl policy enable` activates it")
            + f")\n{json.dumps(payload, indent=2, sort_keys=True)}",
        )
    created = _call(
        client.create_policy, a.name, rules, bindings=bindings, description=a.description
    )
    if a.enable:
        try:
            created = _call(client.update_policy, created["id"], status="active")
        except (RefusedError, UsageError, PlatformError) as e:
            raise PlatformError(
                f"policy {created['id']} was created as a draft but could not be activated: {e}"
            ) from e
    return Result.done(created, f"policy created: {_row(created)}")


def _set_status(ctx: Context, status: str) -> Result:
    client = get_client(ctx)
    if ctx.dry_run:
        return Result.done(
            {"policy": ctx.args.policy, "status": status},
            f"DRY RUN: would set policy {ctx.args.policy} to {status}",
        )
    updated = _call(client.update_policy, ctx.args.policy, status=status)
    return Result.done(updated, f"policy {status}: {_row(updated)}")


def _cmd_enable(ctx: Context) -> Result:
    return _set_status(ctx, "active")


def _cmd_disable(ctx: Context) -> Result:
    return _set_status(ctx, "disabled")


def register(registry: Registry) -> None:
    h = registry.group("hitl", "Resolve human-in-the-loop items of harness sessions")

    p = h.command("policy list", _cmd_list, help="List the org's policies (id, status, scope)")
    p.add_argument("--workstream", help="Only policies bound to this workstream (name or id)")
    p.add_argument("--status", choices=("draft", "active", "disabled"))

    p = h.command("policy get", _cmd_get, help="Show one policy: rules and bindings")
    p.add_argument("policy", help="Policy id")

    p = h.command(
        "policy create",
        _cmd_create,
        help="Create a policy that holds tool calls for a decision (draft; --enable activates)",
        writes=True,
    )
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument(
        "--workstream",
        action="append",
        metavar="NAME|ID",
        help="Scope: this workstream (repeatable); never affects the others",
    )
    p.add_argument(
        "--process", action="append", metavar="TEMPLATE", help="Scope: a process template"
    )
    p.add_argument(
        "--org-wide", action="store_true", help="Scope: every workstream of the org (needs --yes)"
    )
    p.add_argument(
        "--tool",
        action="append",
        metavar="TOOL",
        help="Tool name the rule matches (repeatable; one value may use * as a glob)",
    )
    p.add_argument("--action", choices=_ACTIONS, default="escalate", help="Default: escalate")
    p.add_argument(
        "--assignee",
        metavar="ROLE|member:ID",
        help="Who decides an escalation: admin (default), reviewer or member:<org-member-id>",
    )
    p.add_argument(
        "--hook",
        action="append",
        choices=_HOOKS,
        help="Hook the policy runs at (repeatable; default pre_tool_use)",
    )
    p.add_argument("--rules-file", help="JSON rules document, instead of --tool/--action")
    p.add_argument("--enable", action="store_true", help="Activate it right after creating it")

    for name, handler, what in (
        ("enable", _cmd_enable, "Activate a policy (it is validated again; it then applies)"),
        ("disable", _cmd_disable, "Disable a policy (it stops applying; nothing is deleted)"),
    ):
        p = h.command(f"policy {name}", handler, help=what, writes=True)
        p.add_argument("policy", help="Policy id")
