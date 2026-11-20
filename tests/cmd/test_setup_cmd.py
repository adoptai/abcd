"""``abcd builder setup`` against the fake BFF: idempotent, dry-run makes no call."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.builder_common import tables as tbl
from cli.builder_common import workspace as bws
from tests.cmd.ws_fixtures import WsBFF, make_workspace, run, use_client, workspaces_env


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


@pytest.fixture
def acme(ws_dir: Path) -> Path:
    ws = make_workspace(ws_dir, client="Acme Corp")
    bws.init_layout(ws)
    return ws


def test_setup_creates_everything_and_records_ids(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, doc = run(capsys, "builder", "setup", "--env", "acme")
    assert code == 0, doc
    names = {w["name"] for w in bff.ws_rows.values()}
    assert names == {
        "builder-context",
        "builder-ops",
        "builder-sme-acme-corp",
        "builder-status-acme-corp",
        "acme-corp-staging",
    }
    assert len(bff.tables) == len(tbl.TABLES) == 25

    # every workstream has exactly one store (the auto-created default is reused), plus noui-bundles
    assert len(bff.stores) == 6
    bundles = bff.store_named("noui-bundles")
    assert bundles is not None and bundles["workstream_ids"] == [bff.ws_id("builder-ops")]
    for name in names:
        assert any(bff.ws_id(name) in s["workstream_ids"] for s in bff.stores.values())

    # tables are linked as tables.py says: write, to ops/context/sme
    ops, ctxw, sme = (
        bff.ws_id(n) for n in ("builder-ops", "builder-context", "builder-sme-acme-corp")
    )
    by_name = {t["table_name"]: t["id"] for t in bff.tables.values()}
    assert bff.workstreams[by_name["bld_versions"]] == {ops: "write"}
    assert bff.workstreams[by_name["bld_decisions"]] == {ctxw: "write"}
    assert set(bff.workstreams[by_name["bld_context_gaps"]]) == {ops, sme}
    assert set(bff.workstreams[by_name["bld_open_items"]]) == {ops, ctxw}

    cfg = json.loads((acme / "builder.json").read_text())
    assert cfg["workstreams"]["builder-ops"] == ops
    assert cfg["stores"]["noui-bundles"] == bundles["id"]
    assert cfg["tables"] == doc["data"]["tables"] and len(cfg["tables"]) == 25
    assert {g["workstream"] for g in doc["data"]["grants"]} >= {
        "builder-ops",
        "builder-sme-acme-corp",
    }


def test_setup_run_twice_is_idempotent(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, first = run(capsys, "builder", "setup", "--env", "acme")
    assert code == 0
    assert len(first["data"]["created"]["tables"]) == 25
    snapshot = (
        len(bff.ws_rows),
        len(bff.stores),
        len(bff.tables),
        {t: dict(w) for t, w in bff.workstreams.items()},
    )
    posts = {
        p: bff.count("POST", p)
        for p in ("/v1/org/workstreams", "/v1/docstore/stores", "/v1/org-db/tables")
    }
    cfg_before = (acme / "builder.json").read_text()

    code, second = run(capsys, "builder", "setup", "--env", "acme")
    assert code == 0, second
    assert second["data"]["created"] == {"workstreams": [], "stores": [], "tables": []}
    assert (len(bff.ws_rows), len(bff.stores), len(bff.tables)) == snapshot[:3]
    assert {t: dict(w) for t, w in bff.workstreams.items()} == snapshot[3]
    assert {p: bff.count("POST", p) for p in posts} == posts  # no create call the second time
    assert (acme / "builder.json").read_text() == cfg_before
    assert second["data"]["workstreams"] == first["data"]["workstreams"]


def test_setup_dry_run_makes_no_calls(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    built = use_client(monkeypatch)
    before = (acme / "builder.json").read_text()
    code, doc = run(capsys, "builder", "setup", "--env", "acme", "--dry-run")
    assert code == 0
    assert bff.calls == [] and built == []
    assert (acme / "builder.json").read_text() == before
    plan = doc["data"]["plan"]
    assert len(plan["workstreams"]) == 5 and len(plan["tables"]) == 25
    assert [s["name"] for s in plan["doc_stores"]][-1] == "noui-bundles"
    assert plan["doc_stores"][-1]["only_linked_to"] == "builder-ops"
    assert len(plan["grants"]) == 5
    assert doc["data"]["dry_run"] is True


def test_setup_reuses_existing_workstream_and_links_live_agents(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    existing = bff.add_workstream("builder-ops")
    live = bff.add_workstream("claims-agent-ws")
    code, doc = run(
        capsys, "builder", "setup", "--env", "acme", "--agent-workstream", "claims-agent-ws"
    )
    assert code == 0, doc
    assert doc["data"]["workstreams"]["builder-ops"] == existing
    assert "builder-ops" not in doc["data"]["created"]["workstreams"]
    by_name = {t["table_name"]: t["id"] for t in bff.tables.values()}
    assert live in bff.workstreams[by_name["bld_check_results"]]

    code, doc = run(capsys, "builder", "setup", "--env", "acme", "--agent-workstream", "nope")
    assert code == 2


def test_setup_warns_when_bundles_store_is_linked_elsewhere(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    ops = bff.add_workstream("builder-ops")
    other = bff.add_workstream("somewhere-else")
    bff.add_store("noui-bundles", [ops, other])
    code, doc = run(capsys, "builder", "setup", "--env", "acme")
    assert code == 0
    assert doc["data"]["warnings"][0]["workstream_ids"] == [other]


def test_setup_needs_an_initialised_workspace(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_workspace(ws_dir)
    WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, doc = run(capsys, "builder", "setup", "--env", "acme")
    assert code == 2 and "ws init" in doc["error"]
    make_workspace(ws_dir, "default")
    code, _ = run(capsys, "builder", "setup", "--env", "default")
    assert code == 1


def test_setup_records_the_live_agent_workstream_ids_in_builder_json(
    acme: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    live = bff.add_workstream("claims-agent-ws")
    code, doc = run(
        capsys, "builder", "setup", "--env", "acme", "--agent-workstream", "claims-agent-ws"
    )
    assert code == 0, doc
    assert doc["data"]["agent_workstreams"] == {"claims-agent-ws": live}
    cfg = bws.load_builder_config(acme)
    assert cfg["workstreams"]["claims-agent-ws"] == live
    assert cfg["workstreams"]["builder-ops"] == doc["data"]["workstreams"]["builder-ops"]
