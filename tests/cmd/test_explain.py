"""abcd explain and abcd train."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common.evals.protocols import TURN_COMPLETED, TURN_FAILED, TurnHandle, TurnStatus
from cli.builder_common.workspace import (
    agent_dir,
    new_agent_skeleton,
    set_agent_process_ids,
    update_builder_config,
)
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        new_agent_skeleton(e.ws, "prep", display_name="Return prep", description="Prepares returns")
        yield e


def _rows(ops: OpsEnv) -> None:
    ops.put_rows(
        "bld_versions",
        {
            "version_id": "prep@v1",
            "object_kind": "agent",
            "object_name": "prep",
            "state": "live",
            "reason_ref": "first cut",
        },
    )
    ops.put_rows(
        "bld_releases",
        {
            "release_id": "r1",
            "agent_key": "prep",
            "version_id": "prep@v1",
            "gate_result": "pass",
            "released_at": "2026-10-20T00:00:00Z",
        },
    )
    ops.put_rows(
        "bld_checks",
        {
            "check_key": "c1",
            "agent_key": "prep",
            "title": "Totals tie out",
            "kind": "deterministic",
        },
    )
    ops.put_rows(
        "bld_open_items",
        {
            "item_key": "o1",
            "agent_key": "prep",
            "title": "Confirm depreciation",
            "waiting_on": "client",
            "status": "open",
        },
    )


def test_explain_writes_explainer_under_the_agent_folder(ops: OpsEnv) -> None:
    _rows(ops)
    before = ops.tree()
    code, doc = run("explain", "prep")
    assert code == 0, doc
    assert doc["data"]["generated"] is True and doc["data"]["flagged"] is False
    page = agent_dir(ops.ws, "prep") / "docs" / "explainer.html"
    html = page.read_text()
    assert "Return prep" in html and "Totals tie out" in html and "Confirm depreciation" in html
    new = {p for p in ops.tree() - before if not p.startswith("acme/.builder/")}
    assert new == {"acme/harness/agents/prep/docs/explainer.html"}


def test_explain_flags_generation_failure_without_raising(ops: OpsEnv) -> None:
    (agent_dir(ops.ws, "prep") / "docs").write_text("a file where the folder should be")
    code, doc = run("explain", "prep", "--offline")
    assert code == 0
    assert doc["data"]["generated"] is False and doc["data"]["flagged"] is True
    assert "explainer failed" in doc["data"]["error"]


def test_explain_falls_back_to_the_bundle_when_rows_are_unreadable(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Any) -> Any:
        raise RuntimeError("no token")

    monkeypatch.setattr("cli.core.context.Context.harness_client", boom)
    code, doc = run("explain", "prep")
    assert code == 0 and doc["data"]["generated"] is True
    assert "built from the bundle only" in doc["data"]["warnings"][0]


def test_explain_unknown_agent(ops: OpsEnv) -> None:
    code, doc = run("explain", "ghost", "--offline")
    assert code == 2 and "no agent bundle" in doc["error"]


class FakeDriver:
    def __init__(self, ok: set[str]) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str, str]] = []

    def start_turn(
        self, *, workstream: str, process_id: str, message: str, conversation_id: str | None = None
    ) -> TurnHandle:
        self.calls.append((workstream, process_id, message))
        return TurnHandle(message, "conv", workstream)

    def turn_status(self, handle: TurnHandle) -> TurnStatus:
        if handle.turn_id in self.ok:
            return TurnStatus(state=TURN_COMPLETED)
        return TurnStatus(state=TURN_FAILED, error="turn ended FAILED")

    def stop_turn(self, handle: TurnHandle) -> None:  # pragma: no cover - not reached
        raise AssertionError("no turn should be stopped")


def test_train_validates_prompts_and_writes_kit(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rows(ops)
    update_builder_config(ops.ws, workstreams={"ds:prep-set": "ws-ds"})
    set_agent_process_ids(ops.ws, "prep", staged_process_id="proc-staged")
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("# candidates\nPrepare the return\nDo something silly\n")
    faq = tmp_path / "faq.yaml"
    faq.write_text("- question: Why was it flagged?\n  answer: A check failed.\n")
    driver = FakeDriver({"Prepare the return"})
    monkeypatch.setattr("cli.builder_explain.make_driver", lambda ctx: driver)
    before = ops.tree()
    code, doc = run(
        "train",
        "prep",
        "--validate-on",
        "prep-set",
        "--prompts",
        str(prompts),
        "--faq",
        str(faq),
        "--note",
        "Faster extraction",
    )
    assert code == 0, doc
    assert doc["data"]["shipped_prompts"] == ["Prepare the return"]
    assert doc["data"]["dropped_prompts"] == ["Do something silly"]
    assert driver.calls[0] == ("ws-ds", "proc-staged", "Prepare the return")
    root = agent_dir(ops.ws, "prep") / "training"
    assert "Prepare the return" in (root / "prompts.md").read_text()
    assert "silly" not in (root / "prompts.md").read_text()
    assert "Why was it flagged?" in (root / "faq.md").read_text()
    assert "Faster extraction" in (root / "whats-new.md").read_text()
    new = {p for p in ops.tree() - before if not p.startswith("acme/.builder/")}
    assert new and all(p.startswith("acme/harness/agents/prep/training/") for p in new)


def test_train_without_validation_ships_no_prompts(ops: OpsEnv, tmp_path: Path) -> None:
    prompts = tmp_path / "p.txt"
    prompts.write_text("Try this\n")
    code, doc = run("train", "prep", "--prompts", str(prompts), "--offline")
    assert code == 0
    assert doc["data"]["shipped_prompts"] == []
    assert "not shipped" in doc["data"]["warnings"][0]
    assert (
        "No prompts have been verified"
        in (agent_dir(ops.ws, "prep") / "training" / "prompts.md").read_text()
    )


def test_train_validation_needs_staged_process_and_dataset(ops: OpsEnv) -> None:
    code, doc = run("train", "prep", "--validate-on", "prep-set", "--offline")
    assert code == 2 and "ds:prep-set" in doc["error"]


def test_train_flags_failure(ops: OpsEnv) -> None:
    (agent_dir(ops.ws, "prep") / "training").write_text("blocked")
    code, doc = run("train", "prep", "--offline")
    assert code == 0 and doc["data"]["flagged"] is True
    assert "training kit failed" in doc["data"]["error"]
