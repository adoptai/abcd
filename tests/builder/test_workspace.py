"""Tests for cli.builder_common.workspace and templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.builder_common import template_loader as templates
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError
from cli.wdl_common import workspace_manager as wm


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "acme"
    d.mkdir()
    (d / "env.json").write_text(
        json.dumps({"env_id": "acme", "name": "Acme", "client": "Acme Co", "target": "staging"})
    )
    (d / "agents").mkdir()
    (d / "agents" / "wdl-agent.txt").write_text("legacy")
    (d / "harness" / "skills" / "s1").mkdir(parents=True)
    (d / "harness" / "skills" / "s1" / "SKILL.md").write_text("skill")
    return d


def test_assert_not_default(tmp_path: Path) -> None:
    with pytest.raises(RefusedError):
        bws.assert_not_default("default")
    with pytest.raises(RefusedError):
        bws.assert_not_default(tmp_path / "default")
    bws.assert_not_default("acme")


def test_init_layout_refuses_default(tmp_path: Path) -> None:
    d = tmp_path / "default"
    d.mkdir()
    with pytest.raises(RefusedError):
        bws.init_layout(d)


def test_init_layout_creates_and_is_idempotent(ws: Path) -> None:
    changed = bws.init_layout(ws, default_branch="main", org_id="org-1")
    assert "builder.json" in changed and ".gitignore" in changed and "ADOPT.md" in changed
    for rel in (*bws.TRACKED_DIRS, *bws.LOCAL_DIRS):
        assert (ws / rel).is_dir()
    assert bws.bundles_lock_path(ws).is_file()
    cfg = bws.load_builder_config(ws)
    assert cfg["org_id"] == "org-1" and cfg["tenant"] == "Acme Co"
    # existing files untouched
    assert (ws / "agents" / "wdl-agent.txt").read_text() == "legacy"
    assert (ws / "harness/skills/s1/SKILL.md").read_text() == "skill"
    assert not (ws / "harness/skills/.gitkeep").exists()
    assert (ws / "harness/plugins/.gitkeep").exists()
    snapshot = {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}
    assert bws.init_layout(ws) == []
    assert snapshot == {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}


def test_init_layout_appends_missing_gitignore_patterns(ws: Path) -> None:
    (ws / ".gitignore").write_text("node_modules/\n.env")
    assert ".gitignore" in bws.init_layout(ws)
    text = (ws / ".gitignore").read_text()
    assert "node_modules/" in text
    for pat in templates.gitignore_patterns():
        assert pat in text.splitlines()


def test_gitignore_template_matches_plan() -> None:
    pats = templates.gitignore_patterns()
    for expected in (
        ".env", ".env.*", "harness/.token_cache.json", "*.pem", "*.key",
        "harness/traces/", "harness/sessions/", "harness/workstreams.json",
        "harness/bundles/", "harness/skills/*/recording_bundle.json", "*.har",
        ".cache/", "agents/*/traces/", "actions/*/traces/", "context/", ".builder/",
    ):  # fmt: skip
        assert expected in pats


def test_builder_json_round_trip_and_update(ws: Path) -> None:
    bws.init_layout(ws)
    bws.update_builder_config(
        ws,
        org_id="o1",
        webui_endpoint="https://webui.example.test",
        workstreams={"builder-ops": "ws-1"},
        stores={"noui-bundles": "st-1"},
        tables={"bld_versions": "tb-1"},
    )
    bws.set_agent_process_ids(ws, "acme-prep", live_process_id="p-live")
    bws.set_agent_process_ids(ws, "acme-prep", staged_process_id="p-staged")
    cfg = bws.load_builder_config(ws)
    assert cfg["workstreams"] == {"builder-ops": "ws-1"}
    assert cfg["agents"]["acme-prep"] == {
        "live_process_id": "p-live",
        "staged_process_id": "p-staged",
    }
    bws.save_builder_config(ws, cfg)
    assert bws.load_builder_config(ws) == cfg


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"org_id": 3},
        {"default_branch": ""},
        {"workstreams": {"a": 1}},
        {"workstreams": []},
        {"agents": {"a": {"bogus": "x"}}},
        {"agents": {"a": {"live_process_id": 5}}},
        {"agents": {"a": "x"}},
    ],
)
def test_builder_json_validation(bad: object) -> None:
    with pytest.raises(UsageError):
        bws.validate_builder_config(bad)


def test_update_rejects_unknown_field(ws: Path) -> None:
    bws.init_layout(ws)
    with pytest.raises(UsageError):
        bws.update_builder_config(ws, nonsense="x")


def test_load_missing_and_invalid(ws: Path) -> None:
    with pytest.raises(UsageError):
        bws.load_builder_config(ws)
    (ws / "builder.json").write_text("{nope")
    with pytest.raises(UsageError):
        bws.load_builder_config(ws)


def test_paths_and_name_safety(ws: Path) -> None:
    assert bws.agent_dir(ws, "a1") == ws / "harness/agents/a1"
    assert bws.dataset_manifest_path(ws, "d") == ws / "harness/datasets/d/manifest.json"
    assert bws.dataset_cases_dir(ws, "d") == ws / "harness/datasets/d/cases"
    assert bws.scorers_dir(ws) == ws / "harness/evals/scorers"
    assert bws.reports_dir(ws) == ws / "harness/evals/reports"
    with pytest.raises(UsageError):
        bws.agent_dir(ws, "../x")
    with pytest.raises(UsageError):
        bws.dataset_dir(ws, "")


def test_bundles_lock(ws: Path) -> None:
    bws.init_layout(ws)
    bundle = ws / "harness/skills/s1/recording_bundle.json"
    bundle.write_text('{"har": {}}')
    entry = bws.record_bundle(ws, "harness/skills/s1/recording_bundle.json", "bundles/s1.json")
    assert entry["size"] == len(b'{"har": {}}') and len(entry["sha256"]) == 64
    lock = bws.load_bundles_lock(ws)
    assert lock["entries"]["harness/skills/s1/recording_bundle.json"] == entry
    with pytest.raises(UsageError):
        bws.record_bundle(ws, "missing.json", "x")
    bws.bundles_lock_path(ws).write_text('{"entries": {"a": {}}}')
    with pytest.raises(UsageError):
        bws.load_bundles_lock(ws)


def test_env_json_matches(ws: Path, tmp_path: Path) -> None:
    assert bws.env_json_matches(ws)
    (ws / "env.json").write_text(json.dumps({"env_id": "other"}))
    assert not bws.env_json_matches(ws)
    with pytest.raises(UsageError):
        bws.require_env_json_match(ws)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not bws.env_json_matches(empty)


def test_env_template_equals_create_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wm, "WORKSPACES_DIR", tmp_path / "workspaces")
    mgr = wm.HierarchicalWorkspaceManager()
    ok, path, _ = mgr.create_env("acme", "Acme Env", target="production")
    assert ok
    expected = (path / ".env").read_text()
    assert (path / ".env").stat().st_mode & 0o077 == 0  # credentials: owner only
    assert bws.env_template("Acme Env", "production") == expected
    (path / ".env").unlink()
    assert bws.write_env_template(path)
    assert (path / ".env").read_text() == expected
    assert (path / ".env").stat().st_mode & 0o077 == 0
    # never clobbers an existing .env
    (path / ".env").write_text("ADOPT_CLIENT_ID=real")
    assert not bws.write_env_template(path)
    assert (path / ".env").read_text() == "ADOPT_CLIENT_ID=real"


def test_adopt_md_rendering(ws: Path) -> None:
    bws.init_layout(ws, default_branch="trunk", org_id="org-9")
    text = (ws / "ADOPT.md").read_text()
    assert "Acme Co" in text and "org-9" in text and "`trunk`" in text
    assert "_No agents yet._" in text
    assert "abcd ws status --env acme" in text
    assert "WDL legacy" in text and "default tier" in text
    assert "$client" not in text and "$env" not in text

    bws.new_agent_skeleton(ws, "acme-prep", display_name="Prep")
    bws.set_agent_process_ids(ws, "acme-prep", live_process_id="p-1")
    text = bws.render_adopt_md(ws)
    assert "| acme-prep | p-1 | - | `harness/agents/acme-prep/` |" in text
    bws.refresh_adopt_md(ws)
    assert (ws / "ADOPT.md").read_text() == text


def test_process_skeleton(ws: Path) -> None:
    import yaml

    p = bws.new_agent_skeleton(ws, "acme-prep", display_name="Prep: v1", description="Does it")
    data = yaml.safe_load(p.read_text())
    assert data["name"] == "acme-prep" and data["display_name"] == "Prep: v1"
    assert data["is_enabled"] is True and data["components"] == []
    assert data["workstreams"] == [] and data["write_workstreams"] == []
    with pytest.raises(RefusedError):
        bws.new_agent_skeleton(ws, "acme-prep")


def test_unknown_template() -> None:
    with pytest.raises(UsageError):
        templates.load_template("nope")
