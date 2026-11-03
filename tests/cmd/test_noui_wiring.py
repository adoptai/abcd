"""``abcd wire``: a NoUI-compiled skill joins an agent, the KG and the checks."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cli.builder_common.evals.scorers import evaluate_check, goal_reached, load_checks
from cli.builder_common.tables import validate_row
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run
from tests.noui.test_browser_skill import LOGIN, ExampleBank_CLICKS, ExampleBank_EVENTS

_NOUI = Path(__file__).resolve().parents[2] / "cli" / "noui"
sys.path.insert(0, str(_NOUI))

from noui_core.compile.browser_skill import generate_browser_skill  # noqa: E402
from noui_core.compile.harness_md_generator import render_operations_json  # noqa: E402
from noui_core.verify import gate, replay  # noqa: E402

BROWSER = "examplebank-credit-card"
API = "toyapp-notes"
AGENT = "claims"
CONTENT = "builder-context"

# What the recording carried and no page may repeat.
COOKIE = "SESSIONCOOKIE-9f8e7d6c5b"
QUERY_VALUE = "acct-98765432"
SECRET_BEARER = "bearer-token-abcdef123456"
LEAKS = (COOKIE, QUERY_VALUE, SECRET_BEARER, "tab=statements", "?")

BUNDLE: dict[str, Any] = {
    "session_id": "sess-1",
    "har": {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": f"https://retail.examplebank.test/api/x?acct={QUERY_VALUE}",
                        "cookies": [{"name": "sid", "value": COOKIE}],
                        "headers": [{"name": "Authorization", "value": SECRET_BEARER}],
                        "queryString": [{"name": "acct", "value": QUERY_VALUE}],
                    },
                    "response": {"cookies": []},
                }
            ]
        }
    },
    "click_events": ExampleBank_CLICKS,
    "url_events": ExampleBank_EVENTS,
}


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


def skills_dir(e: OpsEnv) -> Path:
    return e.ws / "harness" / "skills"


def make_browser_skill(e: OpsEnv, *, approve: bool, name: str = BROWSER) -> Path:
    out = skills_dir(e) / name
    out.mkdir(parents=True)
    generate_browser_skill(
        app_slug=name,
        app_name="Examplebank Credit Card",
        workflow_name="credit card statement",
        profile_slug=name,
        url_events=ExampleBank_EVENTS,
        click_events=ExampleBank_CLICKS,
        login_url=LOGIN,
        output_dir=str(out),
        session_id="sess-1",
        start_url=f"{LOGIN}?token={SECRET_BEARER}",
        bundle=BUNDLE,
        bundle_file=f"bundles/{name}-sess-1.json",
    )
    (out / "recording_bundle.json").write_text(json.dumps(BUNDLE))
    if approve:
        ops = json.loads((out / "operations.json").read_text())["operations"]
        results = [[{"command": "get_page_summary", "status": "ok"}] for _ in ops]
        report = replay.approve(replay.build_report(ops, results))
        gate.write_approval(out, report)
    return out


def make_api_skill(e: OpsEnv, name: str = API) -> Path:
    out = skills_dir(e) / name
    out.mkdir(parents=True)
    tool = {
        "name": "list_notes",
        "description": f"List notes, e.g. https://toyapp.example.test/api/notes?owner={QUERY_VALUE}",
        "method": "GET",
        "path": "/api/notes",
        "base_url": "https://toyapp.example.test",
        "params": [{"name": "owner", "source": "query", "required": False}],
        "request_headers": [{"name": "X-Key", "value": SECRET_BEARER}],
    }
    (out / "operations.json").write_text(
        render_operations_json([tool], profile_slug="toyapp", auth_plan=None)
    )
    (out / "SKILL.md").write_text(f"---\nname: {name}\ndescription: notes\n---\nbody\n")
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "app": {"name": "Toy App", "slug": "toyapp"},
                "workflow": {"name": "notes", "start_url": "https://toyapp.example.test/home?s=1"},
                "runtime": {"operation_style": "call_web_api"},
                "operations": [{"name": "list_notes", "method": "GET", "path": "/api/notes"}],
            }
        )
    )
    return out


def new_agent(e: OpsEnv) -> None:
    code, doc = run("agent", "new", AGENT)
    assert code == 0, doc


def components(e: OpsEnv) -> list[dict[str, str]]:
    doc = yaml.safe_load((e.ws / f"harness/agents/{AGENT}/process.yaml").read_text())
    return list(doc["components"])


def node(key: str, type_: str, name: str) -> dict[str, str]:
    return {"node_key": key, "type": type_, "name": name, "status": "vetted"}


def page_texts(e: OpsEnv) -> dict[str, str]:
    return {p: e.docs.read(e.store_ids[CONTENT], p) for p in e.docs.paths(e.store_ids[CONTENT])}


def test_component_is_added_once(ops: OpsEnv) -> None:
    new_agent(ops)
    make_browser_skill(ops, approve=True)
    code, doc = run("wire", "skill", BROWSER, "--agent", AGENT)
    assert code == 0, doc
    assert doc["data"]["added"] is True and doc["data"]["browser_skill"] is True
    assert components(ops) == [{"type": "skill", "source": "org", "ref": BROWSER}]
    code, doc = run("wire", "skill", BROWSER, "--agent", AGENT)
    assert code == 0 and doc["data"]["added"] is False
    assert components(ops) == [{"type": "skill", "source": "org", "ref": BROWSER}]


def test_skill_directory_path_works_and_dry_run_writes_nothing(ops: OpsEnv) -> None:
    new_agent(ops)
    skill = make_api_skill(ops)
    code, _ = run("wire", "skill", str(skill), "--agent", AGENT, "--dry-run")
    assert code == 0 and components(ops) == []
    assert run("wire", "skill", str(skill), "--agent", AGENT)[0] == 0
    assert components(ops)[0]["ref"] == API


def test_unapproved_browser_skill_is_refused(ops: OpsEnv) -> None:
    new_agent(ops)
    make_browser_skill(ops, approve=False)
    code, doc = run("wire", "skill", BROWSER, "--agent", AGENT)
    assert code == 1
    assert "verify_replay" in doc["error"] and "verify_approve" in doc["error"]
    assert components(ops) == []
    # `wire all` writes nothing for it either: no pages, no check, no plan row
    code, _ = run("wire", "all", BROWSER, "--agent", AGENT)
    assert code == 1
    assert ops.docs.paths(ops.store_ids[CONTENT]) == []
    assert not (ops.ws / "harness/checks").exists() or not list(
        (ops.ws / "harness/checks").iterdir()
    )
    assert ops.db.list_all("bld_build_plan") == []


def test_stale_approval_is_refused(ops: OpsEnv) -> None:
    new_agent(ops)
    skill = make_browser_skill(ops, approve=True)
    data = json.loads((skill / "operations.json").read_text())
    data["operations"][0]["steps"].append({"command": "navigate", "url": "https://x.test/"})
    (skill / "operations.json").write_text(json.dumps(data))
    code, doc = run("wire", "skill", BROWSER, "--agent", AGENT)
    assert code == 1 and "changed since" in doc["error"]


def test_kg_pages_hold_no_recording_values_and_edges_are_written(ops: OpsEnv) -> None:
    ops.put_rows("bld_kg_nodes", node(f"agent:{AGENT}", "agent", AGENT))
    make_browser_skill(ops, approve=True)
    make_api_skill(ops)

    code, doc = run("wire", "kg", BROWSER, "--agent", AGENT)
    assert code == 0, doc
    code, doc = run("wire", "kg", API, "--agent", AGENT)
    assert code == 0, doc

    pages = page_texts(ops)
    assert f"proposed/kg/skills/{BROWSER}.md" in pages
    assert f"proposed/kg/skills/{API}.md" in pages
    assert "proposed/kg/apis/toyapp/list-notes.md" in pages
    assert "proposed/kg/systems/toyapp.md" in pages
    for path, text in pages.items():
        if path.endswith("log.md"):
            continue
        for leak in LEAKS:
            assert leak not in text, f"{leak!r} in {path}"
    skill_page = pages[f"proposed/kg/skills/{BROWSER}.md"]
    for want in ("read_overview", "## Goal", "bundle_sha256", "steps_sha256", "replay_fingerprint"):
        assert want in skill_page
    api = pages["proposed/kg/apis/toyapp/list-notes.md"]
    assert "GET /api/notes" in api and "`owner`" in api and "X-Key" not in api

    nodes = {r["node_key"]: r for r in ops.db.list_all("bld_kg_nodes")}
    assert nodes[f"skill:{BROWSER}"]["status"] == "proposed"
    assert nodes["api:toyapp-list-notes"]["page_path"] == "proposed/kg/apis/toyapp/list-notes.md"
    edges = {(r["from_node"], r["relation"], r["to_node"]) for r in ops.db.list_all("bld_kg_edges")}
    assert (f"agent:{AGENT}", "uses", f"skill:{BROWSER}") in edges
    assert (f"skill:{BROWSER}", "uses", "system:toyapp") not in edges
    assert (f"skill:{API}", "uses", "system:toyapp") in edges
    assert (f"skill:{API}", "calls", "api:toyapp-list-notes") in edges

    # idempotent: a second run rewrites the pages and adds no edge
    before = len(ops.db.list_all("bld_kg_edges"))
    assert run("wire", "kg", API, "--agent", AGENT)[0] == 0
    assert len(ops.db.list_all("bld_kg_edges")) == before


def test_vetted_flag_writes_outside_proposed(ops: OpsEnv) -> None:
    make_api_skill(ops)
    code, doc = run("wire", "kg", API, "--vetted")
    assert code == 0, doc
    assert f"kg/skills/{API}.md" in page_texts(ops)
    assert {r["status"] for r in ops.db.list_all("bld_kg_edges")} == {"vetted"}


def test_missing_agent_node_is_noted_not_fatal(ops: OpsEnv) -> None:
    make_api_skill(ops)
    code, doc = run("wire", "kg", API, "--agent", AGENT)
    assert code == 0 and any("agent node" in n for n in doc["data"]["notes"])


def test_a_recording_value_in_a_description_blocks_the_write(ops: OpsEnv) -> None:
    skill = make_api_skill(ops)
    manifest = json.loads((skill / "manifest.json").read_text())
    manifest["workflow"]["name"] = f"notes for {COOKIE}"
    (skill / "manifest.json").write_text(json.dumps(manifest))
    (skill / "recording_bundle.json").write_text(json.dumps(BUNDLE))
    code, doc = run("wire", "kg", API)
    assert code == 1 and "recording" in doc["error"]
    assert ops.docs.paths(ops.store_ids[CONTENT]) == []


def test_goal_check_is_drafted_and_proposed(ops: OpsEnv) -> None:
    new_agent(ops)
    make_browser_skill(ops, approve=True)
    code, doc = run("wire", "goal-check", BROWSER, "--agent", AGENT)
    assert code == 0, doc
    path = ops.ws / f"harness/checks/{BROWSER}-goal-reached.yaml"
    check = yaml.safe_load(path.read_text())
    assert check["kind"] == "goal_reached" and check["status"] == "draft"
    assert check["agent"] == AGENT and check["goal"]["operation"]
    assert check["goal"]["kind"] in ("read", "submit", "download")

    plan = ops.db.list_all("bld_build_plan")
    assert len(plan) == 1
    assert (plan[0]["kind"], plan[0]["status"], plan[0]["agent_key"]) == (
        "check",
        "proposed",
        AGENT,
    )
    validate_row("bld_build_plan", {k: v for k, v in plan[0].items() if k != "id"})

    # a draft is not scored until approved
    assert load_checks(ops.ws) == []
    assert len(load_checks(ops.ws, include_drafts=True)) == 1
    code, doc = run("check", "list")
    assert code == 0 and doc["data"]["checks"][0]["status"] == "draft"

    # again: nothing new
    code, doc = run("wire", "goal-check", BROWSER, "--agent", AGENT)
    assert code == 0 and doc["data"]["proposed"] is False and doc["data"]["drafted"] is False
    assert len(ops.db.list_all("bld_build_plan")) == 1


def test_goal_check_activates_only_after_the_plan_item_is_approved(ops: OpsEnv) -> None:
    new_agent(ops)
    make_browser_skill(ops, approve=True)
    assert run("wire", "goal-check", BROWSER, "--agent", AGENT)[0] == 0
    code, doc = run("wire", "goal-check", BROWSER, "--agent", AGENT, "--activate")
    assert code == 1 and "plan approve" in doc["error"]
    item = ops.db.list_all("bld_build_plan")[0]["item_key"]
    assert run("plan", "approve", item, "--by", "lead", "--yes")[0] == 0
    code, doc = run("wire", "goal-check", BROWSER, "--agent", AGENT, "--activate")
    assert code == 0 and doc["data"]["activated"] is True
    assert [c["check_id"] for c in load_checks(ops.ws)] == [f"{BROWSER}-goal-reached"]
    rows = ops.db.list_all("bld_checks")
    assert rows[0]["kind"] == "goal_reached" and rows[0]["rule"]["operation"]


def test_wire_all_runs_the_three_in_order(ops: OpsEnv) -> None:
    new_agent(ops)
    ops.put_rows("bld_kg_nodes", node(f"agent:{AGENT}", "agent", AGENT))
    make_browser_skill(ops, approve=True)
    code, doc = run("wire", "all", BROWSER, "--agent", AGENT)
    assert code == 0, doc
    assert set(doc["data"]) == {"skill", "kg", "goal_check"}
    assert components(ops)[0]["ref"] == BROWSER
    assert f"proposed/kg/skills/{BROWSER}.md" in page_texts(ops)
    assert len(ops.db.list_all("bld_build_plan")) == 1


# ---------------------------------------------------------------- the goal_reached scorer

DOWNLOAD_GOAL = {"operation": "download_statement", "kind": "download"}


def transcript(*, reached: bool, listed: bool = True) -> list[dict[str, Any]]:
    result = {"operations": [{"name": "download_statement", "goal_reached": reached}]}
    events: list[dict[str, Any]] = [
        {"kind": "assistant_message", "text": "running"},
        {
            "kind": "tool_result",
            "output": json.dumps({"name": "download_statement", "goal_reached": reached}),
        },
        {"kind": "tool_result", "output": result},
    ]
    if listed:
        events.append({"kind": "tool_result", "output": json.dumps({"downloads": [{"id": "d1"}]})})
    return events


def test_goal_reached_scorer_passes_and_fails() -> None:
    ok = goal_reached({}, {"transcript": transcript(reached=True)}, {}, DOWNLOAD_GOAL)
    assert ok.passed is True and ok.score == 1.0 and ok.scorer == "goal_reached"
    # the verdict is false: fail
    assert (
        goal_reached({}, {"transcript": transcript(reached=False)}, {}, DOWNLOAD_GOAL).passed
        is False
    )
    # claimed reached but no file was listed for a download goal: fail
    no_file = transcript(reached=True, listed=False)
    assert goal_reached({}, {"transcript": no_file}, {}, DOWNLOAD_GOAL).passed is False
    # a read goal needs only the verdict
    assert goal_reached({}, {"transcript": no_file}, {}, {**DOWNLOAD_GOAL, "kind": "read"}).passed
    # NDJSON text works too; no transcript or no operation never passes
    nd = "\n".join(json.dumps(e) for e in transcript(reached=True))
    assert goal_reached({}, {"transcript": nd}, {}, DOWNLOAD_GOAL).passed is True
    assert goal_reached({}, {}, {}, DOWNLOAD_GOAL).passed is False
    assert goal_reached({}, {"transcript": nd}, {}, {}).passed is False
    # details stay value-free
    assert set(ok.details) <= {
        "reached",
        "operation",
        "kind",
        "verdicts",
        "downloads_listed",
        "transcript_events",
    }


def test_goal_reached_check_rule_and_scorer_name_are_known() -> None:
    from cli.builder_common.datasets import normalize_scorer

    assert normalize_scorer({"name": "goal_reached", **DOWNLOAD_GOAL})[0] == "goal_reached"
    check = {"check_id": "c", "kind": "goal_reached", "goal": DOWNLOAD_GOAL, "severity": "error"}
    assert evaluate_check(check, {"transcript": transcript(reached=True)}).passed is True
    assert evaluate_check(check, {"transcript": transcript(reached=False)}).passed is False
