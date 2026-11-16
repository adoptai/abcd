"""``abcd hitl policy``: rule and binding construction, local validation, the 403 refusal."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cli.abcd import build_registry
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_policy import (
    _call,
    build_bindings,
    build_rules,
    parse_assignee,
)
from cli.harness_common.api_client import HarnessAPIError


def test_commands_are_registered_under_hitl_policy() -> None:
    reg = build_registry()
    for sub in ("list", "get", "create", "enable", "disable"):
        assert reg.resolve(["hitl", "policy", sub]) is not None
    assert reg.resolve(["hitl", "policy", "delete"]) is None  # disable, never delete
    assert reg.resolve(["hitl", "decide"]) is not None  # the existing commands still resolve


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("admin", {"kind": "role", "value": "admin"}),
        ("role:reviewer", {"kind": "role", "value": "reviewer"}),
        ("member:om-1", {"kind": "member", "value": "om-1"}),
    ],
)
def test_parse_assignee(text: str, want: dict[str, str]) -> None:
    assert parse_assignee(text) == want


@pytest.mark.parametrize("bad", ["boss", "role:", "team:x", "member:", ""])
def test_parse_assignee_rejects(bad: str) -> None:
    with pytest.raises(UsageError):
        parse_assignee(bad)


def test_build_rules_single_tool_is_eq_and_escalates_to_admin() -> None:
    rules = build_rules(["ws_list"], "escalate", None)
    rule = rules["rules"][0]
    assert rule["when"] == [{"field": "tool_name", "op": "eq", "value": "ws_list"}]
    assert rule["then"] == {
        "action": "escalate",
        "params": {"assignee": {"kind": "role", "value": "admin"}},
    }
    assert rules["default_action"] == "allow"


def test_build_rules_many_tools_in_and_glob() -> None:
    assert build_rules(["a", "b"], "deny", None)["rules"][0]["when"][0]["op"] == "in"
    cond = build_rules(["ws_*"], "notify", None)["rules"][0]
    assert cond["when"][0]["op"] == "matches_glob" and cond["then"]["params"] == {}
    with pytest.raises(UsageError, match="single --tool"):
        build_rules(["a*", "b"], "deny", None)


def test_build_rules_needs_a_tool() -> None:
    with pytest.raises(UsageError, match="--tool"):
        build_rules([], "escalate", None)


def test_bindings_need_a_scope_and_carry_the_hook() -> None:
    with pytest.raises(UsageError, match="scope"):
        build_bindings([], [], False, ["pre_tool_use"])
    got = build_bindings(["ws1"], ["tpl"], False, ["pre_tool_use"])
    assert got == [
        {"scope_kind": "workstream", "scope_id": "ws1", "hooks": ["pre_tool_use"]},
        {"scope_kind": "process", "scope_id": "tpl", "hooks": ["pre_tool_use"]},
    ]
    assert build_bindings([], [], True, ["pre_tool_use"])[0]["scope_kind"] == "org"


def _err(code: int, body: str = "x") -> HarnessAPIError:
    return HarnessAPIError("PUT", "https://bff/v1/org/policies/p", code, body)


def _raiser(code: int) -> Any:
    def fn() -> None:
        raise _err(code)

    return fn


def test_call_maps_statuses() -> None:
    with pytest.raises(RefusedError, match="Admin"):
        _call(_raiser(403))
    with pytest.raises(UsageError, match="not found"):
        _call(_raiser(404))
    with pytest.raises(UsageError, match="rejected"):
        _call(_raiser(422))
    with pytest.raises(PlatformError):
        _call(_raiser(500))
    assert _call(lambda: {"ok": 1}) == {"ok": 1}


def test_create_refuses_org_wide_without_yes_and_bad_rules_file(tmp_path: Path) -> None:
    from cli.builder_policy import _cmd_create

    base: dict[str, Any] = {
        "name": "p", "description": None, "workstream": None, "process": None, "org_wide": True,
        "tool": ["t"], "action": "escalate", "assignee": None, "hook": None,
        "rules_file": None, "enable": False,
    }  # fmt: skip
    ctx = SimpleNamespace(args=SimpleNamespace(**base), yes=False, dry_run=True, env=None)
    with pytest.raises(RefusedError, match="--yes"):
        _cmd_create(ctx)  # type: ignore[arg-type]
    bad = tmp_path / "r.json"
    bad.write_text("[1]")
    ctx.args.org_wide, ctx.args.rules_file, ctx.args.tool = False, str(bad), None
    with pytest.raises(UsageError, match="object"):
        _cmd_create(ctx)  # type: ignore[arg-type]
    bad.write_text(json.dumps({"rules": []}))
    ctx.args.tool = ["t"]
    with pytest.raises(UsageError, match="exclusive"):
        _cmd_create(ctx)  # type: ignore[arg-type]
