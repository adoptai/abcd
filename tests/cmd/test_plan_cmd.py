"""``abcd plan`` lifecycle through ``bld_build_plan`` on the fake BFF."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.builder_common import workspace as bws
from tests.cmd.ws_fixtures import WsBFF, make_workspace, run, use_client, workspaces_env


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


@pytest.fixture
def bff(ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> WsBFF:
    ws = make_workspace(ws_dir)
    bws.init_layout(ws)
    fake = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    assert run(capsys, "builder", "setup", "--env", "acme")[0] == 0
    fake.calls.clear()
    return fake


def rows(bff: WsBFF) -> list[dict[str, object]]:
    tid = next(t["id"] for t in bff.tables.values() if t["table_name"] == "bld_build_plan")
    return bff.rows[tid]


def test_plan_lifecycle(bff: WsBFF, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc = run(capsys, "plan", "propose", "intake-skill", "--kind", "skill",
                    "--title", "Intake classifier", "--agent", "claims", "--env", "acme")  # fmt: skip
    assert code == 0, doc
    assert doc["data"]["item"]["status"] == "proposed"
    assert rows(bff)[0]["name"] == "Intake classifier" and rows(bff)[0]["agent_key"] == "claims"

    code, doc = run(capsys, "plan", "show", "--env", "acme")
    assert code == 0 and doc["data"]["count"] == 1
    assert doc["data"]["items"][0]["item_key"] == "intake-skill"

    # approval is a recorded decision: refused without --yes, row untouched
    code, doc = run(capsys, "plan", "approve", "intake-skill", "--by", "lead", "--env", "acme")
    assert code == 1 and "--yes" in doc["error"]
    assert rows(bff)[0]["status"] == "proposed"

    # not approved yet: done is refused
    code, _ = run(capsys, "plan", "done", "intake-skill", "--env", "acme")
    assert code == 1

    code, doc = run(
        capsys, "plan", "approve", "intake-skill", "--by", "lead", "--yes", "--env", "acme"
    )
    assert code == 0, doc
    row = rows(bff)[0]
    assert (row["status"], row["approved_by"]) == ("approved", "lead") and row["approved_at"]

    # approving twice is refused
    code, _ = run(
        capsys, "plan", "approve", "intake-skill", "--by", "lead", "--yes", "--env", "acme"
    )
    assert code == 1

    code, doc = run(capsys, "plan", "done", "intake-skill", "--env", "acme")
    assert code == 0 and doc["data"]["item"]["status"] == "done"
    assert rows(bff)[0]["status"] == "done"

    code, doc = run(capsys, "plan", "show", "--status", "done", "--env", "acme")
    assert doc["data"]["count"] == 1
    code, doc = run(capsys, "plan", "show", "--status", "proposed", "--env", "acme")
    assert doc["data"]["count"] == 0


def test_propose_duplicate_and_bad_input(bff: WsBFF, capsys: pytest.CaptureFixture[str]) -> None:
    args = ("plan", "propose", "k1", "--kind", "agent", "--title", "T", "--env", "acme")
    assert run(capsys, *args)[0] == 0
    code, doc = run(capsys, *args)
    assert code == 1 and "already" in doc["error"]
    assert len(rows(bff)) == 1
    with pytest.raises(SystemExit) as exc:
        run(capsys, "plan", "propose", "k2", "--kind", "widget", "--title", "T", "--env", "acme")
    assert exc.value.code == 2
    code, _ = run(capsys, "plan", "approve", "nope", "--by", "lead", "--yes", "--env", "acme")
    assert code == 1
    code, _ = run(capsys, "plan", "show", "nope", "--env", "acme")
    assert code == 1


def test_plan_dry_run_makes_no_calls(bff: WsBFF, capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ("plan", "propose", "k1", "--kind", "plugin", "--title", "T", "--dry-run"),
        ("plan", "approve", "k1", "--by", "lead", "--dry-run"),
        ("plan", "done", "k1", "--dry-run"),
    ):
        code, doc = run(capsys, *argv, "--env", "acme")
        assert code == 0 and doc["data"]["dry_run"] is True
    assert bff.calls == [] and rows(bff) == []


def test_show_without_tables_is_a_platform_error(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(ws_dir)
    bws.init_layout(ws)
    WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, doc = run(capsys, "plan", "show", "--env", "acme")
    assert code == 3 and "builder setup" in doc["error"]


def test_plan_text_output(bff: WsBFF, capsys: pytest.CaptureFixture[str]) -> None:
    from cli.abcd import main

    assert (
        main(["plan", "propose", "k1", "--kind", "skill", "--title", "Hello", "--env", "acme"]) == 0
    )
    capsys.readouterr()
    assert main(["plan", "show", "--env", "acme"]) == 0
    out = capsys.readouterr().out
    assert "k1" in out and "proposed" in out and "Hello" in out
