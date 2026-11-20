"""Client and lead status: scorecard, per-agent status, daily FDE report (PLAN.md §15).

Pure rendering from input dataclasses: callers fetch the rows (tables, runs,
spend) elsewhere. ``publish_plan`` lists the Doc Store files ``abcd status
publish`` writes in ``builder-status-<client>``. Pointer files are rewritten on
purpose each night; dated copies are frozen and never rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape

from cli.builder_common.explain import CriterionRow, OpenItem, e, html_page, owner_label


@dataclass(frozen=True)
class ReleaseRow:
    agent: str
    version: str
    score: str = ""
    date: str = ""


@dataclass(frozen=True)
class AgentStatus:
    agent: str
    display_name: str = ""
    live_version: str = ""
    staged_version: str = ""
    criteria: tuple[CriterionRow, ...] = ()
    open_items: tuple[OpenItem, ...] = ()

    @property
    def title(self) -> str:
        return self.display_name or self.agent


@dataclass(frozen=True)
class ScorecardInput:
    client: str
    as_of: str  # ISO date
    agents: tuple[AgentStatus, ...] = ()
    releases: tuple[ReleaseRow, ...] = ()


@dataclass(frozen=True)
class DailyReportInput:
    fde: str
    day: str  # ISO date
    agents_touched: tuple[str, ...] = ()
    step_minutes: dict[str, float] = field(default_factory=dict)  # loop step -> minutes
    releases: tuple[ReleaseRow, ...] = ()
    spend_fde_usd: float = 0.0  # FDE turns
    spend_cloud_usd: float = 0.0  # cloud runs
    stuck: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishEntry:
    path: str  # path inside the builder-status Doc Store
    content: str
    frozen: bool  # True: dated copy, written once; False: pointer, rewritten each run


def _md(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "-"


def _progress(a: AgentStatus) -> str:
    if not a.criteria:
        return "no criteria yet"
    met = sum(1 for c in a.criteria if c.met)
    return f"{met} of {len(a.criteria)} met"


def _item_line(i: OpenItem) -> str:
    due = f", due {i.due}" if i.due else ""
    return f"{i.title} ({owner_label(i.owner)}{due})"


def render_agent_status_md(a: AgentStatus) -> str:
    lines = [
        f"# {a.title}: status",
        "",
        f"- Live version: {a.live_version or 'not released yet'}",
        f"- Staged version: {a.staged_version or 'none'}",
        f"- Criteria: {_progress(a)}",
        "",
        "## Criteria",
        "",
    ]
    if a.criteria:
        lines += ["| Criterion | Target | Current | Met |", "|---|---|---|---|"]
        lines += [
            f"| {_md(c.name)} | {_md(c.target)} | {_md(c.current)} | {'yes' if c.met else 'no'} |"
            for c in a.criteria
        ]
    else:
        lines.append("No criteria agreed yet.")
    lines += ["", "## Open items", ""]
    lines += [f"- {_item_line(i)}" for i in a.open_items] or ["Nothing pending."]
    return "\n".join(lines) + "\n"


def render_scorecard_md(card: ScorecardInput) -> str:
    lines = [
        f"# {card.client}: scorecard",
        "",
        f"As of {card.as_of}.",
        "",
        "| Agent | Live | Staged | Criteria | Open items |",
        "|---|---|---|---|---|",
    ]
    for a in card.agents:
        lines.append(
            f"| {_md(a.title)} | {_md(a.live_version)} | {_md(a.staged_version)} "
            f"| {_progress(a)} | {len(a.open_items)} |"
        )
    if not card.agents:
        lines.append("| - | - | - | - | - |")
    lines += ["", "## Releases", ""]
    if card.releases:
        lines += ["| Agent | Version | Score | Date |", "|---|---|---|---|"]
        lines += [
            f"| {_md(r.agent)} | {_md(r.version)} | {_md(r.score)} | {_md(r.date)} |"
            for r in card.releases
        ]
    else:
        lines.append("No releases yet.")
    return "\n".join(lines) + "\n"


def render_scorecard_html(card: ScorecardInput) -> str:
    rows = "".join(
        f"<tr><td>{e(a.title)}</td><td>{e(a.live_version)}</td><td>{e(a.staged_version)}</td>"
        f"<td>{escape(_progress(a))}</td>"
        f"<td>{'<br>'.join(escape(_item_line(i)) for i in a.open_items) or 'none'}</td></tr>"
        for a in card.agents
    )
    table = (
        '<div class="tbl"><table><thead><tr><th>Agent</th><th>Live</th><th>Staged</th>'
        f"<th>Criteria</th><th>Open items</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if rows
        else '<p class="mut">No agents yet.</p>'
    )
    rel = "".join(
        f"<tr><td>{e(r.agent)}</td><td>{e(r.version)}</td><td>{e(r.score)}</td><td>{e(r.date)}</td></tr>"
        for r in card.releases
    )
    releases = (
        '<div class="tbl"><table><thead><tr><th>Agent</th><th>Version</th><th>Score</th>'
        f"<th>Date</th></tr></thead><tbody>{rel}</tbody></table></div>"
        if rel
        else '<p class="mut">No releases yet.</p>'
    )
    body = (
        f"<h1>{escape(card.client)}: scorecard</h1>"
        f'<p class="mut">Frozen {escape(card.as_of)}.</p>'
        f"<h2>Agents</h2>{table}<h2>Releases</h2>{releases}"
    )
    return html_page(f"{card.client} scorecard {card.as_of}", body)


def render_daily_report_md(rep: DailyReportInput) -> str:
    total_min = sum(rep.step_minutes.values())
    lines = [
        f"# Daily report: {rep.fde}, {rep.day}",
        "",
        "## Agents touched",
        "",
    ]
    lines += [f"- {a}" for a in rep.agents_touched] or ["None."]
    lines += ["", "## Time per loop step", ""]
    if rep.step_minutes:
        lines += ["| Step | Minutes |", "|---|---|"]
        lines += [f"| {_md(k)} | {v:.0f} |" for k, v in rep.step_minutes.items()]
        lines.append(f"| Total | {total_min:.0f} |")
    else:
        lines.append("No time recorded.")
    lines += ["", "## Releases", ""]
    lines += [f"- {r.agent} {r.version}: score {r.score or 'n/a'}" for r in rep.releases] or [
        "None."
    ]
    lines += [
        "",
        "## Platform spend",
        "",
        f"- FDE turns: ${rep.spend_fde_usd:.2f}",
        f"- Cloud runs: ${rep.spend_cloud_usd:.2f}",
        f"- Total: ${rep.spend_fde_usd + rep.spend_cloud_usd:.2f}",
        "",
        "## Where stuck",
        "",
    ]
    lines += [f"- {s}" for s in rep.stuck] or ["Nothing."]
    return "\n".join(lines) + "\n"


def publish_plan(
    card: ScorecardInput, *, freeze: bool = False, extra_frozen: dict[str, str] | None = None
) -> list[PublishEntry]:
    """Doc Store files for ``builder-status-<client>``.

    Always: ``scorecard.md`` and ``agents/<agent>/status.md`` (pointers, rewritten).
    With ``freeze`` (the Friday run): ``scorecards/<as_of>.html``, written once.
    ``extra_frozen`` adds dated copies such as release eval reports.
    """
    plan = [PublishEntry("scorecard.md", render_scorecard_md(card), False)]
    plan += [
        PublishEntry(f"agents/{a.agent}/status.md", render_agent_status_md(a), False)
        for a in card.agents
    ]
    if freeze:
        plan.append(
            PublishEntry(f"scorecards/{card.as_of}.html", render_scorecard_html(card), True)
        )
    for path, content in sorted((extra_frozen or {}).items()):
        plan.append(PublishEntry(path, content, True))
    return plan


def frozen_paths(plan: Sequence[PublishEntry]) -> list[str]:
    return [p.path for p in plan if p.frozen]
