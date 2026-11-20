"""``push --staged`` leaves the staged process id in bld_agents, not builder.json (live finding)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.builder_common import workspace as bws
from cli.builder_common.errors import UsageError
from cli.builder_dataset import _process_ids
from cli.builder_eval import process_id_for


class Rows:
    def query(self, table: str, where: dict[str, str] | None = None) -> list[dict[str, str]]:
        assert table == "bld_agents" and where is not None
        row = {"staged_process_id": "p-staged", "process_id": "p-live"}
        return [row] if where["agent_key"] == "a" else []


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "builder.json").write_text('{"agents": {}}')
    return tmp_path


def test_eval_and_dataset_fall_back_to_the_bld_agents_row(ws: Path) -> None:
    assert process_id_for(ws, "a", staged=True, rows=Rows()) == "p-staged"
    assert process_id_for(ws, "a", staged=False, rows=Rows()) == "p-live"
    assert _process_ids(ws, ["a"], Rows()) == ["p-staged"]


def test_builder_json_wins_and_a_missing_agent_says_what_to_run(ws: Path) -> None:
    bws.set_agent_process_ids(ws, "a", staged_process_id="from-json")
    assert process_id_for(ws, "a", staged=True, rows=Rows()) == "from-json"
    with pytest.raises(UsageError, match="push b --staged"):
        _process_ids(ws, ["b"], Rows())
    with pytest.raises(UsageError, match="push b --staged"):
        process_id_for(ws, "b", staged=True, rows=Rows())
