"""Cloud mode keeps agents disabled (C4 / D3): ``agent push``, ``agent create`` and ``agent update``."""

from __future__ import annotations

import pytest

from cli.builder_common.cloudmode import write_marker

from .release_fixtures import AGENT, SKILL_MD, Env, build_env

CONV = "conv-guided-1"


def _cloud(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", CONV)
    write_marker(env.ws, CONV)


def _env(tmp_path, monkeypatch) -> Env:  # type: ignore[no-untyped-def]
    env = build_env(tmp_path, monkeypatch)
    env.client.org_skills["tax-calc"] = {"body": SKILL_MD, "aux": {}, "plugin": None}
    return env


def test_cloud_push_creates_the_agent_disabled_even_if_the_bundle_says_enabled(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    assert "is_enabled: true" in (env.ws / f"harness/agents/{AGENT}/process.yaml").read_text()
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc
    pid = env.client.find_process_by_name(AGENT)["id"]
    assert env.client.processes[pid]["is_enabled"] is False
    assert doc["data"]["is_enabled"] is False
    assert "Studio" in doc["data"]["enable_hint"] and "Enabled" in doc["data"]["enable_hint"]


def test_cloud_push_dry_run_says_disabled_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "push", AGENT, "--yes", "--dry-run", capsys=capsys)
    assert code == 0 and doc["data"]["is_enabled"] is False
    assert env.client.writes() == []


def test_cloud_push_still_needs_yes(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "push", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in str(doc.get("error"))
    assert env.client.writes() == []


def test_cloud_push_keeps_a_disabled_agent_disabled_on_update(
    tmp_path, monkeypatch, capsys
) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    assert env.run("agent", "push", AGENT, "--yes", capsys=capsys)[0] == 0
    env.write(f"harness/agents/{AGENT}/instructions.md", "Use tax-calc, then list the gaps.\n")
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc
    pid = env.client.find_process_by_name(AGENT)["id"]
    assert env.client.processes[pid]["is_enabled"] is False
    assert "list the gaps" in env.client.processes[pid]["agent_instructions"]


def test_cloud_push_refuses_to_touch_an_enabled_live_agent(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    assert env.run("agent", "push", AGENT, "--yes", capsys=capsys)[0] == 0  # local: enabled
    pid = env.client.find_process_by_name(AGENT)["id"]
    assert env.client.processes[pid]["is_enabled"] is True
    _cloud(monkeypatch, env)
    env.write(f"harness/agents/{AGENT}/instructions.md", "Changed in the cloud.\n")
    before = list(env.client.calls)
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "`abcd release`" in str(doc.get("error"))
    assert [c for c, _ in env.client.calls[len(before) :] if c in {"update_process"}] == []
    assert env.client.processes[pid]["is_enabled"] is True


def test_local_push_still_honours_the_bundle(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc
    assert env.client.processes[env.client.find_process_by_name(AGENT)["id"]]["is_enabled"] is True
    assert "enable_hint" not in doc["data"]


def test_cloud_refuses_agent_create_and_update_enable(tmp_path, monkeypatch, capsys) -> None:
    env = _env(tmp_path, monkeypatch)
    _cloud(monkeypatch, env)
    code, doc = env.run("agent", "create", "x-agent", "X", "--enable", capsys=capsys)
    assert code == 1 and "DISABLED" in str(doc.get("error")) and "Studio" in str(doc.get("error"))
    code, doc = env.run("agent", "update", "proc-1", "--enable", capsys=capsys)
    assert code == 1 and "DISABLED" in str(doc.get("error"))
    assert env.client.writes() == []
