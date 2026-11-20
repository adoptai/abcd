"""cli/builder_common/status.py rendering and publish plan."""

from __future__ import annotations

import re

from cli.builder_common.explain import CriterionRow, OpenItem
from cli.builder_common.status import (
    AgentStatus,
    DailyReportInput,
    ReleaseRow,
    ScorecardInput,
    frozen_paths,
    publish_plan,
    render_agent_status_md,
    render_daily_report_md,
    render_scorecard_html,
    render_scorecard_md,
)

AGENT = AgentStatus(
    agent="return-prep",
    display_name="Return prep",
    live_version="v3",
    staged_version="v4",
    criteria=(
        CriterionRow("Accuracy", "95%", "96%", True),
        CriterionRow("Coverage", "90%", "81%", False),
    ),
    open_items=(
        OpenItem("Confirm depreciation method", "client", "2026-11-03"),
        OpenItem("Fix extraction of scanned forms", "adopt", "2026-11-05"),
    ),
)
CARD = ScorecardInput(
    "Acme",
    "2026-10-30",
    (AGENT, AgentStatus(agent="bare")),
    (ReleaseRow("return-prep", "v3", "0.96", "2026-10-20"),),
)


def test_agent_status_md() -> None:
    md = render_agent_status_md(AGENT)
    assert "Live version: v3" in md and "Staged version: v4" in md
    assert "1 of 2 met" in md
    assert "Confirm depreciation method (waiting on the client, due 2026-11-03)" in md
    assert "Fix extraction of scanned forms (with Adopt, due 2026-11-05)" in md


def test_scorecard_md_handles_empty_agent() -> None:
    md = render_scorecard_md(CARD)
    assert "| Return prep | v3 | v4 | 1 of 2 met | 2 |" in md
    assert "| bare | - | - | no criteria yet | 0 |" in md
    assert "None" not in md and "undefined" not in md


def test_scorecard_html_self_contained() -> None:
    html = render_scorecard_html(CARD)
    assert "undefined" not in html and "None" not in html
    assert not re.search(r"(?:src|href)=|https?://|@import|url\(", html)
    assert "prefers-color-scheme" in html


def test_html_escapes() -> None:
    card = ScorecardInput("A<b>", "2026-10-30", (AgentStatus(agent="x", display_name="<script>"),))
    html = render_scorecard_html(card)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_daily_report() -> None:
    rep = DailyReportInput(
        "fde-1",
        "2026-10-30",
        ("return-prep",),
        {"build": 90, "eval": 30},
        (ReleaseRow("return-prep", "v3", "0.96"),),
        12.5,
        3.25,
        ("Scanned PDFs time out",),
    )
    md = render_daily_report_md(rep)
    assert "| Total | 120 |" in md
    assert "FDE turns: $12.50" in md and "Cloud runs: $3.25" in md and "Total: $15.75" in md
    assert "Scanned PDFs time out" in md
    empty = render_daily_report_md(DailyReportInput("f", "d"))
    assert "None" not in empty.replace("None.", "")


def test_publish_plan_pointers_and_frozen() -> None:
    weekday = publish_plan(CARD)
    assert [p.path for p in weekday] == [
        "scorecard.md",
        "agents/return-prep/status.md",
        "agents/bare/status.md",
    ]
    assert not any(p.frozen for p in weekday)
    friday = publish_plan(CARD, freeze=True, extra_frozen={"releases/return-prep/v3/eval.md": "x"})
    assert frozen_paths(friday) == ["scorecards/2026-10-30.html", "releases/return-prep/v3/eval.md"]
