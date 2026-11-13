"""abcd context pull|add and abcd kg page|link|impact."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.cmd.ops_fixtures import OpsEnv, ops_env, run


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


def _node(
    key: str, type_: str, name: str, page: str = "", status: str = "vetted"
) -> dict[str, str]:
    row = {"node_key": key, "type": type_, "name": name, "status": status}
    if page:
        row["page_path"] = page
    return row


def _edge(a: str, rel: str, b: str) -> dict[str, str]:
    return {
        "edge_key": f"{a}|{rel}|{b}",
        "from_node": a,
        "to_node": b,
        "relation": rel,
        "status": "vetted",
    }


def _seed_graph(ops: OpsEnv) -> None:
    ops.put_rows(
        "bld_kg_nodes",
        _node("agent:prep", "agent", "prep", "kg/agents/prep.md"),
        _node("skill:extract", "skill", "extract", "kg/skills/extract.md"),
        _node("system:erp", "system", "erp", "kg/systems/erp.md"),
        _node("api:erp-invoices", "api", "erp/invoices", "kg/apis/erp/invoices.md"),
        _node("client:acme", "client", "acme", "kg/clients/acme.md"),
    )
    ops.put_rows(
        "bld_kg_edges",
        _edge("agent:prep", "uses", "skill:extract"),
        _edge("skill:extract", "calls", "system:erp"),
        _edge("system:erp", "calls", "api:erp-invoices"),
        _edge("api:erp-invoices", "owns", "client:acme"),
    )
    for rel in (
        "agents/prep",
        "skills/extract",
        "systems/erp",
        "apis/erp/invoices",
        "clients/acme",
    ):
        ops.docs.seed("store-0", f"kg/{rel}.md", f"# {rel}\n")


def test_context_pull_writes_only_under_context(ops: OpsEnv) -> None:
    _seed_graph(ops)
    ops.put_rows(
        "bld_decisions",
        {
            "decision_key": "d1",
            "agent_key": "prep",
            "question": "q",
            "answer": "a",
            "status": "confirmed",
        },
        {
            "decision_key": "d2",
            "agent_key": "other",
            "question": "q",
            "answer": "a",
            "status": "confirmed",
        },
    )
    ops.put_rows(
        "bld_open_items",
        {"item_key": "o1", "agent_key": "prep", "title": "Need W2", "status": "open"},
    )
    ops.put_rows(
        "bld_checks",
        {"check_key": "c1", "agent_key": "prep", "title": "Totals tie", "kind": "deterministic"},
    )
    before = ops.tree()
    code, doc = run("context", "pull", "prep")
    assert code == 0, doc
    new = {
        p for p in ops.tree() - before if not p.startswith("acme/.builder/")
    }  # the core event log
    assert new and all(p.startswith("acme/context/") for p in new)
    ctx = ops.ws / "context"
    # depth 2: skill (1) and system (2) are in; api (3) and client (4) are not
    assert (ctx / "kg/agents/prep.md").is_file()
    assert (ctx / "kg/skills/extract.md").is_file()
    assert (ctx / "kg/systems/erp.md").is_file()
    assert not (ctx / "kg/apis/erp/invoices.md").exists()
    decisions = json.loads((ctx / "agents/prep/decisions.json").read_text())
    assert [d["decision_key"] for d in decisions] == ["d1"]
    assert json.loads((ctx / "agents/prep/open_items.json").read_text())[0]["title"] == "Need W2"
    assert doc["data"]["rows"]["checks"] == 1
    graph = json.loads((ctx / "agents/prep/graph.json").read_text())
    assert {n["node_key"] for n in graph["nodes"]} == {"agent:prep", "skill:extract", "system:erp"}


def test_context_pull_reports_missing_page_and_unknown_agent(ops: OpsEnv) -> None:
    ops.put_rows("bld_kg_nodes", _node("agent:prep", "agent", "prep", "kg/agents/prep.md"))
    code, doc = run("context", "pull", "prep")
    assert code == 0
    assert doc["data"]["missing_pages"] == ["kg/agents/prep.md"]
    code, doc = run("context", "pull", "ghost")
    assert code == 0 and doc["data"]["has_kg_node"] is False


def test_context_pull_rejects_unsafe_page_path(ops: OpsEnv) -> None:
    ops.put_rows("bld_kg_nodes", _node("agent:prep", "agent", "prep", "../../escape.md"))
    before = ops.tree()
    code, doc = run("context", "pull", "prep")
    assert code == 2 and "unsafe path" in doc["error"]
    assert not any("escape" in p for p in ops.tree() - before)


def test_context_add_layout_and_no_overwrite(ops: OpsEnv, tmp_path: Path) -> None:
    f = tmp_path / "kickoff.md"
    f.write_text("transcript")
    code, doc = run(
        "context",
        "add",
        "calls",
        str(f),
        "--client",
        "acme",
        "--date",
        "2026-10-20",
        "--slug",
        "Kickoff Call",
    )
    assert code == 0, doc
    assert doc["data"]["path"] == "builder-context/context/calls/acme/2026-10-20-kickoff-call.md"
    assert ops.docs.read("store-0", "context/calls/acme/2026-10-20-kickoff-call.md") == "transcript"
    code, doc = run(
        "context",
        "add",
        "calls",
        str(f),
        "--client",
        "acme",
        "--date",
        "2026-10-20",
        "--slug",
        "Kickoff Call",
    )
    assert code == 1 and "already exists" in doc["error"]
    code, doc = run("context", "add", "process-docs", str(f), "--client", "acme")
    assert code == 2 and "--process" in doc["error"]
    code, doc = run(
        "context", "add", "process-docs", str(f), "--client", "acme", "--process", "year-end"
    )
    assert doc["data"]["path"].endswith("context/process-docs/acme/year-end/sop.md")


def test_context_add_dry_run_uploads_nothing(ops: OpsEnv, tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("x")
    code, doc = run("context", "add", "emails", str(f), "--client", "acme", "--dry-run")
    assert code == 0 and doc["data"]["dry_run"] is True
    assert ops.docs.paths("store-0") == []


def test_kg_page_proposed_and_vetted(ops: OpsEnv, tmp_path: Path) -> None:
    f = tmp_path / "page.md"
    f.write_text("# Prep agent\n")
    code, doc = run("kg", "page", "agent", "prep", "--file", str(f), "--propose")
    assert code == 0, doc
    assert doc["data"]["page_path"] == "proposed/kg/agents/prep.md"
    assert ops.docs.read("store-0", "proposed/kg/agents/prep.md") == "# Prep agent\n"
    row = ops.db.find_rows("bld_kg_nodes", "node_key", "agent:prep")[0]
    assert row["status"] == "proposed" and row["page_path"] == "proposed/kg/agents/prep.md"
    assert "agent:prep" in ops.docs.read("store-0", "kg/log.md")
    # vetting writes the kg path and moves the row
    code, doc = run("kg", "page", "agent", "prep", "--file", str(f))
    assert code == 0 and doc["data"]["updated_node"] is True
    assert ops.db.find_rows("bld_kg_nodes", "node_key", "agent:prep")[0]["status"] == "vetted"
    code, doc = run("kg", "page", "agent", "prep", "--file", str(f))
    assert code == 1 and "--replace" in doc["error"]
    code, doc = run("kg", "page", "agent", "prep", "--file", str(f), "--replace")
    assert code == 0


def test_kg_page_api_layout(ops: OpsEnv, tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("x")
    code, doc = run("kg", "page", "api", "ERP/" + "Invoices", "--file", str(f))
    assert code == 0 and doc["data"]["page_path"] == "kg/apis/erp/invoices.md"
    code, doc = run("kg", "page", "api", "noslash", "--file", str(f))
    assert code == 2


def test_kg_link_validates(ops: OpsEnv) -> None:
    _seed_graph(ops)
    code, doc = run("kg", "link", "agent:prep", "likes", "skill:extract")
    assert code == 2 and "must be one of" in doc["error"]
    code, doc = run("kg", "link", "widget:prep", "uses", "skill:extract")
    assert code == 2
    code, doc = run("kg", "link", "agent:prep", "uses", "agent:prep")
    assert code == 2
    code, doc = run("kg", "link", "agent:prep", "uses", "skill:ghost")
    assert code == 1 and "skill:ghost" in doc["error"]
    code, doc = run(
        "kg", "link", "agent:prep", "tested_by", "skill:extract", "--source", "run-9", "--propose"
    )
    assert code == 0, doc
    row = ops.db.find_rows("bld_kg_edges", "edge_key", "agent:prep|tested_by|skill:extract")[0]
    assert row["status"] == "proposed" and row["source_ref"] == "run-9"
    code, doc = run("kg", "link", "agent:prep", "tested_by", "skill:extract")
    assert code == 0 and doc["data"]["already_existed"] is True


def test_kg_impact(ops: OpsEnv) -> None:
    _seed_graph(ops)
    ops.put_rows(
        "bld_kg_nodes",
        _node("dataset:prep-set", "dataset", "prep-set"),
        _node("person:sam", "person", "sam"),
    )
    ops.put_rows(
        "bld_kg_edges",
        _edge("agent:prep", "tested_by", "dataset:prep-set"),
        _edge("person:sam", "owns", "agent:prep"),
        _edge("skill:extract", "failed_on", "system:erp"),
    )
    code, doc = run("kg", "impact", "erp")
    assert code == 0, doc
    data = doc["data"]
    assert data["node"] == "system:erp"
    assert [(d["node"], d["depth"]) for d in data["dependents"]] == [
        ("skill:extract", 1),
        ("agent:prep", 2),
    ]
    assert data["tests_to_rerun"] == ["dataset:prep-set"]
    assert data["owners"] == ["person:sam"]
    assert data["history"] == [
        {"relation": "failed_on", "from": "skill:extract", "to": "system:erp"}
    ]
    code, doc = run("kg", "impact", "nothing-here")
    assert code == 1
