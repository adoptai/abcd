"""abcd status, status publish, report daily and report scorecard."""

from __future__ import annotations

import contextlib
import io
from collections.abc import Iterator
from pathlib import Path

import pytest

import cli.abcd
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


def _seed(ops: OpsEnv) -> None:
    ops.put_rows(
        "bld_agents",
        {
            "agent_key": "prep",
            "name": "Return prep",
            "live_version": "v2",
            "staged_version": "v3",
            "phase": "staged",
        },
    )
    ops.put_rows(
        "bld_metrics",
        {
            "metric_key": "prep:accuracy@1",
            "agent_key": "prep",
            "name": "Accuracy",
            "category": "quality",
            "unit": "%",
        },
    )
    ops.put_rows(
        "bld_release_criteria",
        {
            "criteria_key": "c1",
            "agent_key": "prep",
            "metric_key": "prep:accuracy@1",
            "comparator": ">=",
            "target": 95,
        },
    )
    ops.put_rows(
        "bld_metric_values",
        {
            "value_key": "v1",
            "metric_key": "prep:accuracy@1",
            "agent_key": "prep",
            "scope": "week",
            "value": 90,
            "computed_at": "2026-10-20T00:00:00Z",
        },
        {
            "value_key": "v2",
            "metric_key": "prep:accuracy@1",
            "agent_key": "prep",
            "scope": "week",
            "value": 96,
            "computed_at": "2026-10-26T00:00:00Z",
        },
    )
    ops.put_rows(
        "bld_open_items",
        {
            "item_key": "o1",
            "agent_key": "prep",
            "title": "Sign-off on targets",
            "waiting_on": "client",
            "due": "2026-10-30",
            "status": "open",
        },
        {
            "item_key": "o2",
            "agent_key": "prep",
            "title": "Done thing",
            "waiting_on": "fde",
            "status": "resolved",
        },
    )
    ops.put_rows(
        "bld_versions",
        {"version_id": "prep@v2", "object_kind": "agent", "object_name": "prep", "state": "live"},
        {"version_id": "prep@v3", "object_kind": "agent", "object_name": "prep", "state": "staged"},
    )
    ops.put_rows(
        "bld_releases",
        {
            "release_id": "r1",
            "agent_key": "prep",
            "version_id": "prep@v2",
            "gate_result": "pass",
            "approved_by": "dana",
            "released_at": "2026-10-26T10:00:00Z",
        },
    )
    ops.put_rows(
        "bld_eval_runs",
        {
            "eval_run_id": "e1",
            "agent_key": "prep",
            "version_id": "prep@v3",
            "tier": "staged",
            "status": "done",
            "started_at": "2026-10-26T09:00:00Z",
        },
    )


def test_status_agent_summary_is_read_only(ops: OpsEnv) -> None:
    _seed(ops)
    before = {k: list(v) for k, v in ops.bff.rows.items()}
    code, doc = run("status", "prep", "--no-drift")
    assert code == 0, doc
    data = doc["data"]
    assert data["live_version"] == "v2" and data["staged_version"] == "v3"
    assert [v["version_id"] for v in data["versions"]] == ["prep@v2", "prep@v3"]
    assert data["criteria"][0]["met"] is True and data["criteria"][0]["current"] == "96%"
    assert data["last_eval"]["eval_run_id"] == "e1"
    assert [i["title"] for i in data["open_items"]] == ["Sign-off on targets"]
    assert {k: list(v) for k, v in ops.bff.rows.items()} == before
    assert ops.docs.stores == {}


def test_status_reports_drift_check_failure_without_raising(ops: OpsEnv) -> None:
    _seed(ops)
    code, doc = run("status", "prep")  # the fake platform has no skills catalog route for this
    assert code == 0
    assert "drift" in doc["data"]


def test_status_publish_pointer_files_and_frozen_copies(ops: OpsEnv) -> None:
    _seed(ops)
    code, doc = run("status", "publish", "--as-of", "2026-10-27", "--freeze")
    assert code == 0, doc
    files = {f["path"]: f for f in doc["data"]["files"]}
    store = "builder-status-acme"
    assert files[f"{store}/scorecard.md"]["frozen"] is False
    assert files[f"{store}/agents/prep/status.md"]["frozen"] is False
    assert files[f"{store}/scorecards/2026-10-27.html"]["frozen"] is True
    assert files[f"{store}/agents/prep/history/2026-10-27.md"]["frozen"] is True
    stored = ops.docs.paths("store-2")
    assert stored == sorted(
        [
            "scorecard.md",
            "agents/prep/status.md",
            "scorecards/2026-10-27.html",
            "agents/prep/history/2026-10-27.md",
        ]
    )
    assert "Sign-off on targets" in ops.docs.read("store-2", "agents/prep/status.md")
    assert "1 of 1 met" in ops.docs.read("store-2", "scorecard.md")


def test_status_publish_rewrites_pointers_but_never_frozen(ops: OpsEnv) -> None:
    _seed(ops)
    run("status", "publish", "--as-of", "2026-10-27", "--freeze")
    ops.docs.stores["store-2"]["scorecards/2026-10-27.html"] = b"FROZEN"
    ops.put_rows(
        "bld_open_items",
        {"item_key": "o3", "agent_key": "prep", "title": "New question", "status": "open"},
    )
    code, doc = run("status", "publish", "--as-of", "2026-10-27", "--freeze")
    assert code == 0, doc
    states = {f["path"].split("/", 1)[1]: f["state"] for f in doc["data"]["files"]}
    assert states["scorecard.md"] == "written"  # pointer: rewritten on purpose
    assert states["scorecards/2026-10-27.html"] == "exists"  # frozen: written once
    assert ops.docs.read("store-2", "scorecards/2026-10-27.html") == "FROZEN"
    assert "New question" in ops.docs.read("store-2", "agents/prep/status.md")


def test_status_publish_without_freeze_and_dry_run(ops: OpsEnv) -> None:
    _seed(ops)
    code, doc = run("status", "publish", "--dry-run")
    assert code == 0 and doc["data"]["dry_run"] is True
    assert ops.docs.stores == {}
    code, doc = run("status", "publish")
    assert not any(f["frozen"] for f in doc["data"]["files"])


def test_status_publish_needs_store(ops: OpsEnv) -> None:
    code, doc = run("status", "publish", "--client", "zenith")
    assert code == 2 and "builder-status-zenith" in doc["error"]


def _events(ops: OpsEnv) -> None:
    ops.put_rows(
        "bld_dev_events",
        {
            "event_id": "a",
            "workspace": "acme",
            "ran_in": "local",
            "actor": "dana",
            "ts": "2026-10-27T09:00:00+00:00",
            "step": 2,
            "kind": "eval",
            "result": "ok",
            "cost_usd": 1.5,
            "payload": {"command": "eval run", "duration_ms": 600000, "agent": "prep"},
        },
        {
            "event_id": "b",
            "workspace": "acme",
            "ran_in": "local",
            "actor": "dana",
            "ts": "2026-10-27T10:00:00+00:00",
            "step": 4,
            "kind": "run",
            "result": "fail",
            "cost_usd": 0.5,
            "payload": {"command": "run start", "exit_code": 3, "duration_ms": 120000},
        },
        {
            "event_id": "c",
            "workspace": "acme",
            "ran_in": "cloud",
            "actor": "dana",
            "ts": "2026-10-27T11:00:00+00:00",
            "step": 6,
            "kind": "eval",
            "result": "ok",
            "cost_usd": 2.0,
            "payload": {"command": "eval run", "duration_ms": 60000},
        },
        {
            "event_id": "d",
            "workspace": "acme",
            "ran_in": "local",
            "actor": "lee",
            "ts": "2026-10-27T09:30:00+00:00",
            "step": 3,
            "kind": "review",
            "result": "ok",
            "payload": {"command": "review", "duration_ms": 30000},
        },
        {
            "event_id": "e",
            "workspace": "acme",
            "ran_in": "cloud",
            "actor": "cloud:abcd-nightly",
            "ts": "2026-10-27T02:00:00+00:00",
            "step": 0,
            "kind": "status",
            "result": "ok",
            "payload": {"command": "status publish"},
        },
        {
            "event_id": "f",
            "workspace": "acme",
            "ran_in": "local",
            "actor": "dana",
            "ts": "2026-10-26T09:00:00+00:00",
            "step": 2,
            "kind": "eval",
            "result": "ok",
            "payload": {"command": "eval run", "duration_ms": 999999},
        },
    )


def test_report_daily_renders_and_stores(ops: OpsEnv) -> None:
    _seed(ops)
    _events(ops)
    ops.put_rows(
        "bld_releases",
        {
            "release_id": "r2",
            "agent_key": "prep",
            "version_id": "prep@v3",
            "gate_result": "pass",
            "approved_by": "dana",
            "released_at": "2026-10-27T12:00:00Z",
        },
    )
    code, doc = run("report", "daily", "--fde", "dana", "--date", "2026-10-27")
    assert code == 0, doc
    (rep,) = doc["data"]["reports"]
    assert rep["path"] == "builder-ops/reports/daily/2026-10-27/dana.md"
    body = ops.docs.read("store-1", "reports/daily/2026-10-27/dana.md")
    assert body == rep["markdown"]
    assert "# Daily report: dana, 2026-10-27" in body
    assert "- prep" in body  # agent touched
    assert "| Local evals | 10 |" in body and "| Run on the harness | 2 |" in body
    assert "FDE turns: $2.00" in body and "Cloud runs: $2.00" in body
    assert "run start failed (exit 3)" in body
    assert "- prep v3: score pass" in body


def test_report_daily_without_fde_covers_everyone_but_cloud_actors(ops: OpsEnv) -> None:
    _events(ops)
    code, doc = run("report", "daily", "--date", "2026-10-27", "--no-upload")
    assert code == 0
    assert [r["fde"] for r in doc["data"]["reports"]] == ["dana", "lee"]
    assert not any(r["uploaded"] for r in doc["data"]["reports"])
    assert ops.docs.stores == {}
    code, doc = run("report", "daily", "--date", "2026-01-01")
    assert code == 0 and doc["data"]["reports"] == []


def test_report_send_only_prints(ops: OpsEnv) -> None:
    _events(ops)
    _seed(ops)
    code, doc = run(
        "report",
        "daily",
        "--fde",
        "lee",
        "--date",
        "2026-10-27",
        "--send",
        "slack",
        "--to",
        "#fde-reports",
        "--no-upload",
    )
    assert code == 0
    call = doc["data"]["reports"][0]["would_send"]
    assert call["integration"] == "slack" and call["args"]["channel"] == "#fde-reports"
    assert "Daily report: lee" in call["args"]["message"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.abcd.main(
            [
                "report",
                "daily",
                "--fde",
                "lee",
                "--date",
                "2026-10-27",
                "--send",
                "slack",
                "--no-upload",
                "--env",
                "acme",
            ]
        )
    assert "Not sent. --send slack would call slack.slack_send_message" in out.getvalue()
    code, doc = run(
        "report",
        "scorecard",
        "--as-of",
        "2026-10-27",
        "--send",
        "gmail",
        "--to",
        "lead@example.com",
    )
    call = doc["data"]["would_send"]
    assert call["integration"] == "gmail" and call["args"]["to"] == "lead@example.com"
    assert call["args"]["subject"] == "acme: scorecard 2026-10-27"
    assert not [c for c in ops.bff.calls if "slack" in c[1] or "gmail" in c[1]]


def test_report_scorecard_renders_from_rows(ops: OpsEnv) -> None:
    _seed(ops)
    code, doc = run("report", "scorecard", "--as-of", "2026-10-27")
    assert code == 0
    md = doc["data"]["markdown"]
    assert "# acme: scorecard" in md and "| Return prep | v2 | v3 | 1 of 1 met | 1 |" in md
    assert "| prep | v2 | pass | 2026-10-26 |" in md
    assert ops.docs.stores == {}


def test_status_client_defaults_to_the_workspace_client_not_the_env_name(tmp_path: Path) -> None:
    """Live: env ``acme-staging`` has client ``acme``; setup made ``builder-status-acme``."""
    import json
    from types import SimpleNamespace

    from cli.builder_status import _client_slug

    (tmp_path / "env.json").write_text(json.dumps({"env_id": "acme-staging", "client": "acme"}))
    ctx = SimpleNamespace(env_dir=tmp_path, args=SimpleNamespace(client=None))
    assert _client_slug(ctx) == "acme"  # type: ignore[arg-type]
    ctx.args.client = "Other Co"
    assert _client_slug(ctx) == "other-co"  # type: ignore[arg-type]
