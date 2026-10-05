"""Generalize: prune/rename/reword a compiled skill through a confirmed plan.

The replay half runs against a REAL capture (tests/noui/fixtures/
toyapp_notes_bundle.json, recorded with Playwright against the bundled toy app);
the browser half against the recorded browser bundle. Neither needs Tabby or an
LLM key.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "cli" / "noui"))

from noui_core.compile import generalize as g  # noqa: E402
from noui_core.compile.browser_skill import generate_browser_skill  # noqa: E402
from noui_core.compile.provenance import steps_digest  # noqa: E402
from noui_core.compile.workflow import compile_workflow_bundle  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = _ROOT / "cli" / "noui" / "scripts" / "generalize.py"


@pytest.fixture
def replay_skill(tmp_path: Path) -> Path:
    bundle = json.loads((FIXTURES / "toyapp_notes_bundle.json").read_text())
    compile_workflow_bundle(
        session_id="toyapp0001",
        bundle=bundle,
        name="toyapp-notes",
        target="skill",
        profile_slug="toyapp",
        execution_mode="harness",
        output_root=str(tmp_path),
        auth_type="session",
        allow_unbound_profile=True,
    )
    skill = tmp_path / "skills" / "toyapp-notes"
    assert (skill / "operations.json").exists()
    return skill


@pytest.fixture
def browser_skill(tmp_path: Path) -> Path:
    bundle = json.loads((FIXTURES / "examplebank_statement_bundle.json").read_text())
    out = tmp_path / "skills" / "examplebank-statement"
    generate_browser_skill(
        app_slug="examplebank-statement",
        app_name="ExampleBank Statement",
        workflow_name="statement",
        profile_slug="examplebank",
        url_events=bundle.get("url_events") or [],
        click_events=bundle.get("click_events") or [],
        login_url="https://retail.examplebank.test/login-page",
        output_dir=str(out),
        bundle=bundle,
    )
    return out


def _ops(skill: Path) -> list[dict]:
    return g.load_operations(skill)


def _plan(skill: Path) -> dict:
    return json.loads((skill / g.PLAN_FILE).read_text())


def _save(skill: Path, plan: dict) -> None:
    (skill / g.PLAN_FILE).write_text(json.dumps(plan))


# --- draft -----------------------------------------------------------------


def test_draft_flags_keepalive_and_suggests_natural_names(replay_skill: Path) -> None:
    plan = g.draft_plan(replay_skill)
    assert plan["kind"] == "replay" and plan["status"] == "draft"
    by_url = {e["url_template"].split("toyapp.example.test")[1]: e for e in plan["operations"]}
    health = by_url["/health"]
    assert health["decision"]["action"] == "drop"
    assert any("keepalive" in r for r in health["noise_reasons"])
    assert by_url["/api/notes"]["decision"]["rename"] == "list_notes"
    get_one = [e for e in plan["operations"] if e["method"] == "GET" and "{" in e["url_template"]]
    assert get_one[0]["decision"]["rename"] == "get_note"
    delete = [e for e in plan["operations"] if e["method"] == "DELETE"][0]
    assert delete["decision"]["rename"] == "delete_note"


def test_third_party_host_is_flagged_for_review_not_dropped() -> None:
    seen: set[tuple[str, str]] = set()
    reasons = g.classify(
        {"method": "GET", "url_template": "https://auth.other-idp.example/userinfo"},
        primary_domain="example.test",
        seen=seen,
    )
    assert reasons and reasons[0].startswith("REVIEW:")


def test_telemetry_and_duplicates_are_noise() -> None:
    seen: set[tuple[str, str]] = set()
    op = {"method": "POST", "url_template": "https://app.example.test/api/analytics/collect"}
    assert g.classify(op, primary_domain="example.test", seen=seen)
    dup = g.classify(op, primary_domain="example.test", seen=seen)
    assert any("duplicate" in r for r in dup)
    host = g.classify(
        {"method": "GET", "url_template": "https://www.google-analytics.com/g/c"},
        primary_domain="example.test",
        seen=seen,
    )
    assert any("telemetry host" in r for r in host)


# --- confirm / apply gate --------------------------------------------------


def test_apply_refuses_an_unconfirmed_plan(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    before = (replay_skill / "operations.json").read_text()
    with pytest.raises(g.GeneralizeError, match="confirm"):
        g.apply_plan(replay_skill)
    assert (replay_skill / "operations.json").read_text() == before


def test_confirmed_plan_prunes_renames_and_rerenders_docs(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    plan = _plan(replay_skill)
    for e in plan["operations"]:
        if e["decision"]["rename"] == "get_note":
            e["decision"]["params"] = {"note_id": {"rename": "id", "description": "The note id."}}
    _save(replay_skill, plan)
    g.confirm_plan(replay_skill, confirmed_by="member")
    summary = g.apply_plan(replay_skill)

    names = [op["name"] for op in _ops(replay_skill)]
    assert names == ["list_notes", "get_note", "delete_note"]
    assert summary["dropped"] and "health" in summary["dropped"][0]
    get_note = _ops(replay_skill)[1]
    assert get_note["url_template"].endswith("/api/notes/{id}")
    assert get_note["path_params"][0] == {
        "name": "id",
        "type": "string",
        "required": True,
        "description": "The note id.",
    }

    manifest = json.loads((replay_skill / "manifest.json").read_text())
    assert [o["name"] for o in manifest["operations"]] == names
    assert manifest["operations"][1]["path"] == "/api/notes/{id}"
    assert "generalized_at" in manifest["generation"]

    skill_md = (replay_skill / "SKILL.md").read_text()
    assert "### `list_notes`" in skill_md and "get_health" not in skill_md
    assert skill_md.startswith("---\nname: toyapp-notes\n")
    assert _plan(replay_skill)["status"] == "applied"


def test_rerender_keeps_a_renamed_skill_slug(replay_skill: Path) -> None:
    """Regression: the catalog slug in SKILL.md (e.g. an org prefix added after
    compiling) must survive the re-render; it once reverted to manifest.skill_id."""
    md = replay_skill / "SKILL.md"
    md.write_text(md.read_text().replace("name: toyapp-notes\n", "name: acme-toyapp-notes\n", 1))
    g.write_draft(replay_skill)
    g.confirm_plan(replay_skill, confirmed_by="member")
    g.apply_plan(replay_skill)
    assert md.read_text().startswith("---\nname: acme-toyapp-notes\n")


def test_query_and_body_params_cannot_be_renamed(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    plan = _plan(replay_skill)
    ops = _ops(replay_skill)
    ops[0]["query_params"] = [{"name": "q", "type": "string", "required": False}]
    (replay_skill / "operations.json").write_text(json.dumps({"operations": ops}))
    plan["operations"][0]["decision"]["params"] = {"q": {"rename": "query"}}
    problems = g.validate_plan(plan, ops, "replay")
    assert any("only path parameters can be renamed" in p for p in problems)


def test_plan_must_match_operations_and_not_collide(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    plan = _plan(replay_skill)
    for e in plan["operations"]:
        if e["decision"]["action"] == "keep":
            e["decision"]["rename"] = "same_name"
    problems = g.validate_plan(plan, _ops(replay_skill), "replay")
    assert any("collide" in p for p in problems)
    plan["operations"].pop()
    problems = g.validate_plan(plan, _ops(replay_skill), "replay")
    assert any("do not match" in p for p in problems)


def test_confirm_refuses_an_invalid_plan(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    plan = _plan(replay_skill)
    for e in plan["operations"]:
        e["decision"]["action"] = "drop"
    _save(replay_skill, plan)
    with pytest.raises(g.GeneralizeError, match="drops every operation"):
        g.confirm_plan(replay_skill, confirmed_by="member")


def test_redraft_over_a_confirmed_plan_needs_force(replay_skill: Path) -> None:
    g.write_draft(replay_skill)
    g.confirm_plan(replay_skill, confirmed_by="member")
    with pytest.raises(g.GeneralizeError, match="--force"):
        g.write_draft(replay_skill)
    g.write_draft(replay_skill, force=True)
    assert _plan(replay_skill)["status"] == "draft"


# --- browser skills ---------------------------------------------------------


def test_browser_skill_allows_rename_only_and_keeps_steps_digest(browser_skill: Path) -> None:
    before = _ops(browser_skill)
    manifest = json.loads((browser_skill / "manifest.json").read_text())
    sealed = (manifest.get("provenance") or {}).get("steps_sha256")

    g.write_draft(browser_skill)
    plan = _plan(browser_skill)
    assert plan["kind"] == "browser" and plan["allowed"] == ["rename", "description"]
    plan["operations"][0]["decision"]["action"] = "drop"
    _save(browser_skill, plan)
    with pytest.raises(g.GeneralizeError, match="cannot drop"):
        g.confirm_plan(browser_skill, confirmed_by="member")

    plan["operations"][0]["decision"] = {
        "action": "keep",
        "rename": "download_statement",
        "description": "Download the statement for a period.",
    }
    _save(browser_skill, plan)
    g.confirm_plan(browser_skill, confirmed_by="member")
    summary = g.apply_plan(browser_skill)

    after = _ops(browser_skill)
    assert after[0]["name"] == "download_statement"
    assert steps_digest(after) == steps_digest(before)
    if sealed:
        assert steps_digest(after) == sealed
    assert summary["renamed"] == {before[0]["name"]: "download_statement"}


def test_browser_skill_rejects_parameter_changes(browser_skill: Path) -> None:
    g.write_draft(browser_skill)
    plan = _plan(browser_skill)
    plan["operations"][0]["decision"]["params"] = {"x": {"description": "y"}}
    problems = g.validate_plan(plan, _ops(browser_skill), "browser")
    assert any("cannot change parameters" in p for p in problems)


def test_tabby_mode_skills_are_refused(tmp_path: Path) -> None:
    bundle = json.loads((FIXTURES / "toyapp_notes_bundle.json").read_text())
    compile_workflow_bundle(
        session_id="toyapp0002",
        bundle=bundle,
        name="toyapp-tabby",
        target="skill",
        profile_slug="toyapp",
        execution_mode="tabby",
        output_root=str(tmp_path),
        auth_type="session",
        allow_unbound_profile=True,
    )
    skill = tmp_path / "skills" / "toyapp-tabby"
    with pytest.raises(g.GeneralizeError, match="recompile"):
        g.draft_plan(skill)


# --- CLI ---------------------------------------------------------------------


def test_cli_end_to_end(replay_skill: Path, tmp_path: Path) -> None:
    skill = tmp_path / "cli-skill"
    shutil.copytree(replay_skill, skill)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
        )

    assert run("draft", str(skill)).returncode == 0
    refused = run("apply", str(skill))
    assert refused.returncode == 1 and "Refused" in refused.stderr
    assert run("confirm", str(skill), "--by", "member").returncode == 0
    applied = run("apply", str(skill))
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["kept"] == ["list_notes", "get_note", "delete_note"]
    assert run("show", str(tmp_path / "missing")).returncode == 2
