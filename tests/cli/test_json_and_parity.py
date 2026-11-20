"""``--json`` output through the real entry point, and old script vs ``abcd`` parity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli.conftest import run_cli


def _doc(proc) -> dict:  # type: ignore[no-untyped-def,type-arg]
    # stdout must be exactly one JSON document, nothing else.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, proc.stdout
    return json.loads(lines[0])


@pytest.fixture
def acme(ws_dir: Path, make_env) -> Path:  # type: ignore[no-untyped-def]
    return make_env("acme")


@pytest.mark.parametrize(
    "argv",
    [
        ["ws", "list", "--json"],
        ["ws", "show", "acme", "--json"],
        ["noui", "scripts", "--json"],
        ["noui", "env", "--env", "acme", "--json"],
        ["noui", "paths", "--json", "--env", "acme"],
        ["noui", "list", "--env", "acme", "--json"],
        ["wdl", "--list"],
        ["trace", "review", "nope", "--env", "acme", "--json"],
    ],
)
def test_json_stdout_is_one_clean_document(ws_dir: Path, acme: Path, argv: list[str]) -> None:
    proc = run_cli(ws_dir, *argv)
    doc = _doc(proc) if "--json" in argv else None
    if doc is not None:
        assert doc["exit_code"] == proc.returncode
        assert doc["ok"] is (proc.returncode == 0)
        assert (
            doc["command"]
            == " ".join(a for a in argv if not a.startswith("-"))[: len(doc["command"])]
        )
    else:
        assert proc.returncode == 0 and "save_wdl_draft" in proc.stdout


def test_json_data_for_structured_commands(ws_dir: Path, acme: Path) -> None:
    doc = _doc(run_cli(ws_dir, "ws", "list", "--json"))
    assert {e["env_id"] for e in doc["data"]["environments"]} >= {"acme"}
    doc = _doc(run_cli(ws_dir, "noui", "scripts", "--json"))
    assert "compile_workflow" in doc["data"]["scripts"]
    doc = _doc(run_cli(ws_dir, "noui", "env", "--env", "acme", "--json"))
    assert doc["data"]["env"] == "acme"


def test_json_failure_reports_exit_code(ws_dir: Path) -> None:
    proc = run_cli(ws_dir, "noui", "env", "--env", "ghost", "--json")
    doc = _doc(proc)
    assert proc.returncode == 2 and doc["exit_code"] == 2 and "ghost" in doc["error"]
    assert not (ws_dir / "ghost").exists()


def test_ws_delete_never_prompts(ws_dir: Path, acme: Path) -> None:
    refused = run_cli(ws_dir, "ws", "delete", "acme")
    assert refused.returncode == 1 and acme.exists()
    ok = run_cli(ws_dir, "ws", "delete", "acme", "--yes")
    assert ok.returncode == 0 and not acme.exists()


def test_ws_create_use_show_roundtrip(ws_dir: Path) -> None:
    assert (
        run_cli(ws_dir, "ws", "create", "--id", "zenith", "--name", "Zenith", "--use").returncode
        == 0
    )
    assert (ws_dir / ".active_env").read_text().strip() == "zenith"
    doc = _doc(run_cli(ws_dir, "ws", "show", "zenith", "--json"))
    assert doc["data"]["env_id"] == "zenith"
    dry = run_cli(ws_dir, "ws", "create", "--id", "nope", "--name", "N", "--dry-run")
    assert dry.returncode == 0 and not (ws_dir / "nope").exists()


def test_events_are_recorded_by_the_real_entry_point(ws_dir: Path, acme: Path) -> None:
    run_cli(ws_dir, "noui", "scripts", "--env", "acme")
    lines = (acme / ".builder" / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["command"] == "noui scripts"


# -- parity: the old entry point and abcd print the same thing ---------------------------


def test_parity_ws_list(ws_dir: Path, acme: Path) -> None:
    old = run_cli(ws_dir, "env", "list", script="workspace.py")
    new = run_cli(ws_dir, "ws", "list")
    assert (old.returncode, old.stdout) == (new.returncode, new.stdout)
    assert "acme" in new.stdout


@pytest.mark.parametrize("command", ["env", "paths", "list", "scripts"])
def test_parity_noui_bridge(ws_dir: Path, acme: Path, command: str) -> None:
    old = run_cli(ws_dir, "--env", "acme", command, script="noui_workspace.py")
    new = run_cli(ws_dir, "noui", command, "--env", "acme")
    assert old.returncode == new.returncode == 0
    assert old.stdout == new.stdout


def test_parity_trace_review_missing_run(ws_dir: Path, acme: Path) -> None:
    old = run_cli(ws_dir, "review", "run-x", "--env", "acme", script="harness_trace.py")
    new = run_cli(ws_dir, "trace", "review", "run-x", "--env", "acme")
    assert old.returncode == new.returncode == 1
    assert old.stdout == new.stdout and "No turn-*.ndjson" in new.stdout


def test_parity_unknown_old_command_is_a_usage_error(ws_dir: Path) -> None:
    assert run_cli(ws_dir, "bogus", script="harness_skill.py").returncode == 2
    assert run_cli(ws_dir, "skill", "bogus").returncode == 2
