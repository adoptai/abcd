"""Registry, context, output, events, discover and passthrough."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest
import requests

from cli.abcd import build_registry
from cli.builder_common.errors import (
    BuilderError,
    PlatformError,
    RefusedError,
    UsageError,
)
from cli.core import events, passthrough
from cli.core.context import Context
from cli.core.output import Result, build_document, exit_code_for, extract_json, to_result
from cli.core.registry import Registry, discover
from cli.harness_common.api_client import HarnessAPIClient, HarnessAPIError


def make_registry(handler, *, writes: bool = False) -> Registry:  # type: ignore[no-untyped-def]
    reg = Registry(prog="abcd")
    reg.group("demo", "Demo").command("go", handler, help="go", writes=writes)
    return reg


# -- every command takes the global flags -------------------------------------------------


def test_every_command_accepts_global_flags() -> None:
    registry = build_registry()
    specs = list(registry.specs())
    assert len(specs) > 30
    for spec in specs:
        assert spec.parser is not None
        flags = {o for a in spec.parser._actions for o in a.option_strings}
        for flag in ("--env", "--json", "--yes", "--verbose"):
            assert flag in flags, f"{spec.name} lacks {flag}"
        if spec.writes:
            assert "--dry-run" in flags, f"{spec.name} writes but has no --dry-run"


def test_expected_commands_are_registered() -> None:
    names = {s.name for s in build_registry().specs()}
    expected = {
        "ws list", "ws use", "ws show", "ws create", "ws delete",
        "skill audit", "skill push", "skill verify", "skill delete", "skill deploy-default",
        "plugin push",
        "agent list", "agent get", "agent create", "agent update", "agent delete",
        "agent workstreams", "agent set-workstreams", "agent run",
        "run start", "run continue", "runs list", "runs detail", "runs trace",
        "trace fetch", "trace review", "conversations list",
        "workstream ensure", "workstream seed",
        "noui env", "noui paths", "noui list", "noui doctor", "noui scripts", "noui run",
        "noui capture_record", "noui compile_workflow",
        "tabby status", "tabby init", "tabby up", "tabby provision", "tabby down", "tabby full",
        "wdl",
    }  # fmt: skip
    assert expected <= names


def test_group_is_idempotent() -> None:
    reg = Registry()
    assert reg.group("x", "X") is reg.group("x")
    with pytest.raises(ValueError):
        reg.group("x").command("a", lambda c: 0)
        reg.group("x").command("a", lambda c: 0)


# -- exit codes ---------------------------------------------------------------------------


def test_exit_code_mapping() -> None:
    assert exit_code_for(BuilderError()) == 1
    assert exit_code_for(RefusedError()) == 1
    assert exit_code_for(UsageError()) == 2
    assert exit_code_for(PlatformError()) == 3
    assert exit_code_for(HarnessAPIError("GET", "u", 500, "boom")) == 3
    assert exit_code_for(requests.ConnectionError()) == 3
    assert exit_code_for(TimeoutError()) == 3
    assert exit_code_for(ValueError("x")) == 1


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (UsageError("bad"), 2),
        (PlatformError("down"), 3),
        (RefusedError("no"), 1),
        (HarnessAPIError("GET", "u", 502, "x"), 3),
    ],
)
def test_uncaught_errors_become_exit_codes(raised: Exception, code: int, capsys) -> None:  # type: ignore[no-untyped-def]
    def handler(ctx: Context) -> int:
        raise raised

    assert make_registry(handler).run(["demo", "go", "--json"]) == code
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit_code"] == code and doc["ok"] is False and doc["error"]


def test_handler_return_values(capsys) -> None:  # type: ignore[no-untyped-def]
    assert make_registry(lambda c: None).run(["demo", "go"]) == 0
    assert make_registry(lambda c: 1).run(["demo", "go"]) == 1
    assert make_registry(lambda c: Result.refused("nope")).run(["demo", "go"]) == 1
    assert "nope" in capsys.readouterr().err

    def exiting(ctx: Context) -> int:
        raise SystemExit(3)

    assert make_registry(exiting).run(["demo", "go"]) == 3


def test_moved_commands_that_catch_api_errors_keep_returning_one(ws_dir: Path) -> None:
    from cli import harness_conversations as hc

    class Boom:
        def list_conversations(self, **kw: object) -> None:
            raise HarnessAPIError("GET", "u", 500, "x")

    import cli.harness_conversations as mod

    orig = mod.get_harness_client_for_env
    mod.get_harness_client_for_env = lambda env: Boom()  # type: ignore[assignment]
    try:
        assert hc.main(["list"]) == 1
        assert build_registry().run(["conversations", "list"]) == 1
    finally:
        mod.get_harness_client_for_env = orig  # type: ignore[assignment]


# -- --json -------------------------------------------------------------------------------


def test_json_document_wraps_output(capsys) -> None:  # type: ignore[no-untyped-def]
    def handler(ctx: Context) -> int:
        print("progress line")
        print(json.dumps({"a": 1}, indent=2))
        return 0

    assert make_registry(handler).run(["demo", "go", "--json"]) == 0
    cap = capsys.readouterr()
    doc = json.loads(cap.out)  # stdout is exactly one document
    assert doc["data"] == {"a": 1}
    assert doc["command"] == "demo go" and "progress line" in doc["output"]
    assert "progress line" in cap.err  # mirrored to stderr, not stdout


def test_result_data_wins_over_captured_json() -> None:
    doc = build_document("x", Result.done({"k": 1}), '{"other": 2}\n')
    assert doc["data"] == {"k": 1}
    assert extract_json("noise\n[1, 2]\nmore") == [1, 2]
    assert extract_json("no json here") is None
    assert to_result(None).ok and to_result(2).code == 2


def test_json_flag_is_not_wrapped_in_standalone_mode(capsys) -> None:  # type: ignore[no-untyped-def]
    reg = Registry(prog="old.py", flat=True)
    reg.group("g").command("go", lambda c: print("plain") or 0, legacy_name="go")
    assert reg.run(["go", "--json"]) == 0
    assert capsys.readouterr().out == "plain\n"


# -- context ------------------------------------------------------------------------------


def _ctx(ws: Path, argv: list[str]) -> Context:
    reg = make_registry(lambda c: 0)
    args = reg.build().parse_args(argv)
    return Context(args=args, spec=args._spec, registry=reg)


def test_env_resolution_order(ws_dir: Path) -> None:
    assert _ctx(ws_dir, ["demo", "go"]).env == "default"
    (ws_dir / ".active_env").write_text("beta\n")
    assert _ctx(ws_dir, ["demo", "go"]).env == "beta"
    assert _ctx(ws_dir, ["demo", "go", "--env", "gamma"]).env == "gamma"


def test_load_env_overrides_process_env(ws_dir: Path, make_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    d = make_env("acme")
    (d / ".env").write_text("ABCD_TEST_VAR=from-file\n")
    monkeypatch.setenv("ABCD_TEST_VAR", "from-process")
    ctx = _ctx(ws_dir, ["demo", "go", "--env", "acme"])
    assert ctx.load_env() == d / ".env"
    import os

    assert os.environ["ABCD_TEST_VAR"] == "from-file"
    monkeypatch.delenv("ABCD_TEST_VAR")


def test_workspaces_override_patches_imported_names(ws_dir: Path) -> None:
    from cli.harness_common import workspace as hw

    assert ws_dir.resolve() == hw.WORKSPACES_DIR
    assert hw.env_path("acme") == ws_dir.resolve() / "acme"


def test_dry_run_notice(capsys) -> None:  # type: ignore[no-untyped-def]
    seen: list[bool] = []

    def handler(ctx: Context) -> int:
        seen.append(ctx.dry_run_notice("would write"))
        return 0

    make_registry(handler, writes=True).run(["demo", "go", "--dry-run"])
    assert seen == [True] and "DRY RUN: would write" in capsys.readouterr().out


# -- events -------------------------------------------------------------------------------


def test_mask_by_key_and_shape() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"
    out = events.mask(
        {
            "pat_secret": "hunter2",
            "name": "ok",
            "note": f"use {jwt} now",
            "nested": {"api_key": "k", "list": ["ghp_" + "a" * 30, "fine"]},
            "empty_token": "",
        }
    )
    assert out["pat_secret"] == "***" and out["name"] == "ok"
    assert jwt not in out["note"] and "***" in out["note"]
    assert out["nested"]["api_key"] == "***"
    assert out["nested"]["list"] == ["***", "fine"]
    assert out["empty_token"] == ""


def test_events_written_only_when_env_exists(ws_dir: Path, make_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    reg = make_registry(lambda c: 0)
    reg.run(["demo", "go", "--env", "ghost"])
    assert not (ws_dir / "ghost").exists()

    d = make_env("acme")
    reg.run(["demo", "go", "--env", "acme"])
    lines = (d / ".builder" / "events.jsonl").read_text().splitlines()
    event = json.loads(lines[-1])
    assert event["command"] == "demo go" and event["mode"] == "local" and event["ok"] is True

    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c-1")
    reg.run(["demo", "go", "--env", "acme"])
    last = json.loads((d / ".builder" / "events.jsonl").read_text().splitlines()[-1])
    assert last["mode"] == "cloud" and last["conversation_id"] == "c-1"


def test_events_mask_args_and_sinks_never_fail_the_command(ws_dir: Path, make_env) -> None:  # type: ignore[no-untyped-def]
    d = make_env("acme")
    got: list[dict] = []  # type: ignore[type-arg]

    def bad_sink(e: dict) -> None:  # type: ignore[type-arg]
        raise RuntimeError("sink down")

    events.clear_sinks()
    events.register_sink(bad_sink)
    events.register_sink(got.append)
    try:
        reg = Registry()
        p = reg.group("demo").command("go", lambda c: 1)
        p.add_argument("--api-key")
        assert reg.run(["demo", "go", "--env", "acme", "--api-key", "s3cret"]) == 1
    finally:
        events.clear_sinks()
    assert got and got[0]["args"]["api_key"] == "***" and got[0]["exit_code"] == 1
    assert "s3cret" not in (d / ".builder" / "events.jsonl").read_text()


def test_standalone_mode_records_no_events(ws_dir: Path, make_env) -> None:  # type: ignore[no-untyped-def]
    d = make_env("acme")
    reg = Registry(prog="old.py", flat=True)
    reg.group("g").command("go", lambda c: 0, legacy_name="go")
    reg.run(["go", "--env", "acme"])
    assert not (d / ".builder").exists()


# -- discover -----------------------------------------------------------------------------


def test_discover_registers_builder_modules(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "builder_fake.py").write_text("")
    (tmp_path / "builder_noreg.py").write_text("")
    (tmp_path / "other.py").write_text("")
    fake = types.ModuleType("cli.builder_fake")
    fake.register = lambda r: r.group("fake").command("hi", lambda c: 0, help="hi")  # type: ignore[attr-defined]
    noreg = types.ModuleType("cli.builder_noreg")
    monkeypatch.setitem(sys.modules, "cli.builder_fake", fake)
    monkeypatch.setitem(sys.modules, "cli.builder_noreg", noreg)
    reg = Registry()
    assert discover(reg, tmp_path) == ["builder_fake"]
    assert {s.name for s in reg.specs()} == {"fake hi"}


def test_discover_default_dir_is_cli_package() -> None:
    assert isinstance(discover(Registry()), list)


# -- passthrough --------------------------------------------------------------------------


def test_list_scripts_has_wdl_and_hides_moved() -> None:
    scripts = passthrough.list_scripts()
    assert "save_wdl_draft" in scripts and "test_runner" in scripts
    assert not any(s.startswith(("harness_", "builder_")) for s in scripts)
    assert "noui_workspace" not in scripts and "abcd" not in scripts


def test_passthrough_forwards_argv_verbatim(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    seen: list[list[str]] = []
    fake = types.ModuleType("cli.fakewdl")
    fake.main = lambda: seen.append(list(sys.argv)) or 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cli.fakewdl", fake)
    monkeypatch.setattr(passthrough, "list_scripts", lambda: ["fakewdl"])
    reg = Registry()
    passthrough.register(reg)
    assert reg.run(["wdl", "fakewdl", "--env", "x", "--json", "-y", "a b"]) == 0
    assert seen == [["fakewdl.py", "--env", "x", "--json", "-y", "a b"]]


def test_passthrough_exit_codes_and_list(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    fake = types.ModuleType("cli.fakewdl")

    def main() -> None:
        raise SystemExit(7)

    fake.main = main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cli.fakewdl", fake)
    monkeypatch.setattr(passthrough, "list_scripts", lambda: ["fakewdl"])
    reg = Registry()
    passthrough.register(reg)
    assert reg.run(["wdl", "fakewdl"]) == 7
    assert reg.run(["wdl", "nope"]) == 2
    assert reg.run(["wdl", "--list"]) == 0
    assert capsys.readouterr().out.strip() == "fakewdl"
    assert reg.run(["wdl", "--env", "x", "fakewdl"]) == 2


def test_wdl_script_help_runs_real_main(capsys) -> None:  # type: ignore[no-untyped-def]
    assert build_registry().run(["wdl", "validate", "--help"]) == 0
    assert "usage" in capsys.readouterr().out.lower()


# -- stop_turn fix ------------------------------------------------------------------------


def test_stop_turn_sends_conversation_id() -> None:
    client = HarnessAPIClient(bearer_token="t", webui_endpoint="http://example.invalid")
    calls: list[tuple] = []  # type: ignore[type-arg]
    client._request = lambda method, path, **kw: calls.append((method, path, kw)) or {}  # type: ignore[method-assign]
    client.stop_turn("t1", "c1")
    assert calls == [
        ("POST", "/v1/end-user/agent-harness/turns/t1/stop", {"json": {"conversation_id": "c1"}})
    ]


def test_argparse_namespace_has_no_leaked_internal_args() -> None:
    reg = make_registry(lambda c: 0)
    ns = reg.build().parse_args(["demo", "go"])
    assert isinstance(ns, argparse.Namespace) and ns._spec.name == "demo go"


def test_mask_argv_secret_flag_values() -> None:
    out = events.mask(["run", "--pat-secret", "zzz", "--token=abc", "--name", "acme", "-k", "x"])
    assert out == ["run", "--pat-secret", "***", "--token=***", "--name", "acme", "-k", "x"]


# -- which workspace a command acts on (live: a stray event in a client workspace) ----------


def _events(d: Path) -> list[dict]:  # type: ignore[type-arg]
    f = d / ".builder" / "events.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_ws_create_logs_into_the_new_workspace_not_the_active_one(
    ws_dir: Path, make_env, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    active = make_env("client-prod")
    (ws_dir / ".active_env").write_text("client-prod")
    reg = build_registry()
    assert reg.run(["ws", "create", "--id", "fresh", "--name", "Fresh"]) == 0
    assert _events(active) == []  # nothing leaks into the active workspace
    assert [e["env"] for e in _events(ws_dir / "fresh")] == ["fresh"]
    assert reg.run(["ws", "show", "fresh"]) == 0
    assert _events(active) == []
    assert reg.run(["ws", "use", "fresh"]) == 0
    assert _events(active) == []


def test_ws_clone_without_env_logs_nowhere(ws_dir: Path, make_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    active = make_env("client-prod")
    (ws_dir / ".active_env").write_text("client-prod")
    ctx = Context(
        args=argparse.Namespace(env=None, id=None),
        spec=types.SimpleNamespace(name="ws clone"),  # type: ignore[arg-type]
        registry=Registry(),
    )
    assert ctx.event_env is None
    events.write_event(ctx, {"command": "ws clone"})
    assert _events(active) == []


def test_active_env_fallback_is_announced_and_can_be_forbidden(
    ws_dir: Path, make_env, monkeypatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    make_env("client-prod")
    (ws_dir / ".active_env").write_text("client-prod")
    monkeypatch.delenv("ABCD_REQUIRE_ENV", raising=False)
    spec = types.SimpleNamespace(name="plan show")

    def ctx_for(env: str | None) -> Context:
        return Context(
            args=argparse.Namespace(env=env),
            spec=spec,
            registry=Registry(),  # type: ignore[arg-type]
        )

    implicit = ctx_for(None)
    assert implicit.env == "client-prod" and ".active_env" in implicit.env_source
    monkeypatch.setattr(
        "cli.harness_common.api_client.get_harness_client_for_env", lambda env: object()
    )
    implicit.harness_client()
    err = capsys.readouterr().err
    assert "client-prod" in err and ".active_env" in err

    explicit = ctx_for("client-prod")
    explicit.harness_client()
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("ABCD_REQUIRE_ENV", "1")
    with pytest.raises(UsageError, match="--env is required"):
        _ = ctx_for(None).env
    assert ctx_for("client-prod").env == "client-prod"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    import cli.abcd as abcd_main

    with pytest.raises(SystemExit) as exc:
        abcd_main.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("abcd ")
