"""cli/trigger_hitl.py + cli/trigger_single_child.py -- generic, workspace-driven."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli import trigger_hitl  # noqa: E402


def test_parse_params() -> None:
    assert trigger_hitl.parse_params(["a=1", "b=x=y", "c="]) == {"a": "1", "b": "x=y", "c": ""}
    with pytest.raises(ValueError, match="KEY=VALUE"):
        trigger_hitl.parse_params(["novalue"])


def test_hitl_dry_run_uses_the_active_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pipe = tmp_path / "acme-dev" / "pipelines" / "review"
    pipe.mkdir(parents=True)
    (pipe / "widdle.json").write_text(json.dumps([{"operation": "ESCALATE"}]))
    (pipe / "pipeline.json").write_text(json.dumps({"remote_pipeline_id": "p-123"}))
    monkeypatch.setattr(trigger_hitl, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(trigger_hitl, "ensure_env", lambda: "acme-dev")
    monkeypatch.setattr(trigger_hitl.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        ["trigger_hitl.py", "--pipeline", "review", "--param", "item_id=7", "--dry-run"],
    )
    assert trigger_hitl.main() == 0
    payload = json.loads((tmp_path / "hitl_payload.json").read_text())
    assert payload["pipeline_id"] == "p-123" and payload["workflow_params"] == {"item_id": "7"}
    assert payload["test_mode"] is False


def test_hitl_requires_a_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trigger_hitl, "ensure_env", lambda: "acme-dev")
    monkeypatch.setattr(sys, "argv", ["trigger_hitl.py", "--chat"])
    with pytest.raises(SystemExit):
        trigger_hitl.main()


def test_single_child_rejects_conflicting_workstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    from cli import trigger_single_child as tsc

    monkeypatch.setattr(tsc, "ensure_env", lambda: "acme-dev")
    for argv in (
        ["--pipeline", "c", "--workstream-id", "a", "--create-workstream", "b"],
        ["--pipeline", "c", "--workstream-id", "a", "--param", "workstream_id=z"],
        ["--pipeline", "c", "--workstream-property", "k=v"],
    ):
        monkeypatch.setattr(sys, "argv", ["trigger_single_child.py", *argv])
        with pytest.raises(SystemExit):
            tsc.main()
