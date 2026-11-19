"""Cloud mode: a draft agent is tested in the Builder workstream, then published disabled."""

from __future__ import annotations

import pytest

from cli import harness_process
from cli.builder_common.cloudmode import write_marker

from .release_fixtures import AGENT, SKILL_MD, Env, build_env

CONV = "conv-guided-1"


def _cloud(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", CONV)
    write_marker(env.ws, CONV)
    monkeypatch.setattr(harness_process, "get_harness_client_for_env", lambda e=None: env.client)


def _env(tmp_path, monkeypatch) -> Env:  # type: ignore[no-untyped-def]
    env = build_env(tmp_path, monkeypatch)
    env.client.org_skills["tax-calc"] = {"body": SKILL_MD, "aux": {}, "plugin": None}
    return env


def _pid(env: Env) -> str:
    return str(env.client.find_process_by_name(AGENT)["id"])


def test_cloud_push_defaults_to_a_disabled_draft_in_the_builder_workstream_only(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    assert "is_enabled: true" in (env.ws / f"harness/agents/{AGENT}/process.yaml").read_text()
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc
    pid = _pid(env)
    assert env.client.processes[pid]["is_enabled"] is False
    assert env.client.assigned[pid] == ["ws-builder"]  # not the bundle's ws-client
    assert env.client.grants[pid]["workstream_ids"] == []
    d = doc["data"]
    assert d["mode"] == "test-in-builder" and d["is_enabled"] is False
    assert f"agent update {pid} --enable" in d["next"]


def test_explicit_test_in_builder_is_the_same_and_flags_are_cloud_only(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    code, doc = env.run("agent", "push", AGENT, "--yes", "--test-in-builder", capsys=capsys)
    assert code == 2 and "cloud" in str(doc.get("error"))
    _cloud(monkeypatch, env)
    code, doc = env.run(
        "agent", "push", AGENT, "--yes", "--test-in-builder", "--publish", capsys=capsys
    )
    assert code == 2
    code, doc = env.run("agent", "push", AGENT, "--yes", "--test-in-builder", capsys=capsys)
    assert code == 0 and env.client.assigned[_pid(env)] == ["ws-builder"]


def test_draft_enable_in_builder_then_publish_assigns_the_targets_disabled(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    assert env.run("agent", "push", AGENT, "--yes", capsys=capsys)[0] == 0
    pid = _pid(env)
    # enabling the Builder-only draft is allowed...
    code, doc = env.run("agent", "update", pid, "--enable", capsys=capsys)
    assert code == 0, doc
    assert env.client.processes[pid]["is_enabled"] is True
    # ...a re-push of the test draft keeps it enabled and in the Builder workstream
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0 and doc["data"]["is_enabled"] is True
    assert env.client.assigned[pid] == ["ws-builder"]
    # publish: the bundle's own workstreams, disabled (the platform would force it too)
    code, doc = env.run("agent", "push", AGENT, "--yes", "--publish", capsys=capsys)
    assert code == 0, doc
    assert env.client.assigned[pid] == ["ws-client"]
    assert env.client.processes[pid]["is_enabled"] is False
    assert doc["data"]["mode"] == "publish" and "Studio" in doc["data"]["enable_hint"]


def test_enable_is_refused_unless_the_agent_is_in_the_builder_workstream_alone(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    assert env.run("agent", "push", AGENT, "--yes", "--publish", capsys=capsys)[0] == 0
    pid = _pid(env)
    assert env.client.assigned[pid] == ["ws-client"]
    code, doc = env.run("agent", "update", pid, "--enable", capsys=capsys)
    assert code == 1 and "Builder workstream" in str(doc.get("error"))
    env.client.assigned[pid] = ["ws-builder", "ws-client"]  # Builder plus another: still refused
    assert env.run("agent", "update", pid, "--enable", capsys=capsys)[0] == 1
    assert env.client.processes[pid]["is_enabled"] is False
    code, doc = env.run("agent", "create", "x-agent", "X", "--enable", capsys=capsys)
    assert code == 1 and "Builder workstream" in str(doc.get("error"))


def test_publish_needs_a_target_and_test_mode_needs_a_builder_workstream(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    env.client.builder_ws_id = None
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "Builder workstream" in str(doc.get("error"))
    assert env.client.writes() == []
    env.client.builder_ws_id = "ws-builder"
    p = env.ws / f"harness/agents/{AGENT}/process.yaml"
    p.write_text(p.read_text().replace("workstreams: [acme-client]", "workstreams: []", 1))
    code, doc = env.run("agent", "push", AGENT, "--yes", "--publish", capsys=capsys)
    assert code == 2 and "no workstream to publish to" in str(doc.get("error"))


def test_cloud_push_dry_run_writes_nothing_and_still_needs_yes(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "push", AGENT, "--yes", "--dry-run", capsys=capsys)
    assert code == 0 and doc["data"]["is_enabled"] is False
    assert doc["data"]["mode"] == "test-in-builder" and env.client.writes() == []
    code, doc = env.run("agent", "push", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in str(doc.get("error"))


def test_cloud_push_refuses_to_touch_an_enabled_live_agent(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    assert (
        env.run("agent", "push", AGENT, "--yes", capsys=capsys)[0] == 0
    )  # local: enabled, ws-client
    pid = _pid(env)
    assert env.client.processes[pid]["is_enabled"] is True
    _cloud(monkeypatch, env)
    env.write(f"harness/agents/{AGENT}/instructions.md", "Changed in the cloud.\n")
    for flags in ((), ("--publish",)):
        before = len(env.client.calls)
        code, doc = env.run("agent", "push", AGENT, "--yes", *flags, capsys=capsys)
        assert code == 1 and "`abcd release`" in str(doc.get("error"))
        assert [c for c, _ in env.client.calls[before:] if c == "update_process"] == []
    assert env.client.processes[pid]["is_enabled"] is True
    assert env.client.assigned[pid] == ["ws-client"]


def test_local_push_still_honours_the_bundle(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc
    assert env.client.processes[_pid(env)]["is_enabled"] is True
    assert env.client.assigned[_pid(env)] == ["ws-client"]
    assert "enable_hint" not in doc["data"] and "mode" not in doc["data"]
