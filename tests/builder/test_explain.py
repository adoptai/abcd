"""cli/builder_common/explain.py."""

from __future__ import annotations

import json
import re
from pathlib import Path

from cli.builder_common import explain as ex
from cli.builder_common.explain import (
    AgentInfo,
    CriterionRow,
    DatasetInfo,
    EvalInfo,
    OpenItem,
    Scenario,
    Step,
    VersionRow,
)

FULL = AgentInfo(
    agent="return-prep",
    display_name="Return prep",
    description="Prepares returns.",
    steps=(Step("Extract", "Reads the forms", ("pdf-reader",)), Step("Review")),
    skills=("pdf-reader",),
    versions=(VersionRow("v3", "2026-10-20", "faster", live=True), VersionRow("v4", staged=True)),
    dataset=DatasetInfo("core", 12, ("extract", "e2e")),
    criteria=(CriterionRow("Accuracy", "95%", "96%", True),),
    checks=("totals tie out",),
    last_eval=EvalInfo("2026-10-21", "0.96", 11, 1, "One miss on scans."),
    open_items=(OpenItem("Confirm method", "client", "2026-11-03"),),
    scenarios=(Scenario("Scanned return", "A scanned PDF arrives", "Runs OCR first"),),
)


def _assert_clean(html: str) -> None:
    assert "undefined" not in html and "None" not in html
    assert not re.search(r"https?://|\ssrc=|\shref=|@import|url\(", html)
    assert "prefers-color-scheme" in html
    assert "<script>" in html  # inline, interactive


def test_full_explainer() -> None:
    html = ex.render_explainer(FULL)
    _assert_clean(html)
    for text in (
        "Return prep",
        "Extract",
        "pdf-reader",
        "v3",
        "core",
        "Accuracy",
        "totals tie out",
        "0.96",
        "waiting on the client",
        "Scanned return",
    ):
        assert text in html


def test_sparse_explainer_has_no_undefined() -> None:
    html = ex.render_explainer(AgentInfo(agent="bare"))
    _assert_clean(html)
    assert "Nothing recorded yet." in html or "No steps recorded yet." in html


def test_escaping() -> None:
    html = ex.render_explainer(AgentInfo(agent="x", description="<img onerror=1>"))
    assert "<img" not in html


def test_generate_writes_file(tmp_path: Path) -> None:
    res = ex.generate_explainer(FULL, tmp_path / "agent")
    assert res.ok and res.paths == [tmp_path / "agent" / "docs" / "explainer.html"]
    assert res.paths[0].read_text().startswith("<!doctype html>")


def test_generate_failure_reported_not_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "agent"
    blocker.write_text("a file, not a directory")
    res = ex.generate_explainer(FULL, blocker)
    assert not res.ok and "explainer failed" in res.error


def test_info_from_bundle(tmp_path: Path) -> None:
    d = tmp_path / "return-prep"
    d.mkdir()
    (d / "process.yaml").write_text(
        "display_name: Return prep\ndescription: Does returns\ncomponents:\n"
        "  - {type: skill, source: org, ref: pdf-reader}\n  - {type: tool, ref: other}\n"
    )
    (d / "flow.json").write_text(json.dumps({"steps": [{"title": "Extract", "description": "d"}]}))
    info = ex.info_from_bundle(d, checks=("c",))
    assert info.agent == "return-prep" and info.skills == ("pdf-reader",)
    assert info.steps[0].name == "Extract" and info.checks == ("c",)
    _assert_clean(ex.render_explainer(info))
    assert ex.info_from_bundle(tmp_path).steps == ()
