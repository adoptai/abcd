"""Agent explainer: ``harness/agents/<agent>/docs/explainer.html`` (PLAN.md §16).

One self-contained, interactive HTML page per agent: steps, skills, versions,
dataset, criteria, checks, last eval, open items and scenarios. No external
assets; light and dark follow ``prefers-color-scheme``. Failure to generate is
reported in the result, never raised, so a release is not blocked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

import yaml

_DASH = "&mdash;"


@dataclass(frozen=True)
class Step:
    name: str
    description: str = ""
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class VersionRow:
    version: str
    date: str = ""
    note: str = ""
    live: bool = False
    staged: bool = False


@dataclass(frozen=True)
class DatasetInfo:
    name: str = ""
    case_count: int = 0
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionRow:
    name: str
    target: str = ""
    current: str = ""
    met: bool = False


@dataclass(frozen=True)
class EvalInfo:
    date: str = ""
    score: str = ""
    passed: int = 0
    failed: int = 0
    summary: str = ""


@dataclass(frozen=True)
class OpenItem:
    title: str
    owner: str = "adopt"  # "client" or "adopt"
    due: str = ""


@dataclass(frozen=True)
class Scenario:
    title: str
    situation: str = ""
    behaviour: str = ""


@dataclass(frozen=True)
class AgentInfo:
    agent: str
    display_name: str = ""
    description: str = ""
    steps: tuple[Step, ...] = ()
    skills: tuple[str, ...] = ()
    versions: tuple[VersionRow, ...] = ()
    dataset: DatasetInfo | None = None
    criteria: tuple[CriterionRow, ...] = ()
    checks: tuple[str, ...] = ()
    last_eval: EvalInfo | None = None
    open_items: tuple[OpenItem, ...] = ()
    scenarios: tuple[Scenario, ...] = ()

    @property
    def title(self) -> str:
        return self.display_name or self.agent


@dataclass
class GenerationResult:
    """Outcome of a generate call. ``ok`` False means flag it on the release."""

    ok: bool
    paths: list[Path] = field(default_factory=list)
    error: str = ""


def owner_label(owner: str) -> str:
    return "waiting on the client" if owner == "client" else "with Adopt"


def info_from_bundle(bundle_dir: Path, **extra: Any) -> AgentInfo:
    """Build ``AgentInfo`` from an agent bundle (``process.yaml``, ``flow.json``).

    Reads leniently: whatever the bundle lacks stays empty. Version, dataset,
    criteria, eval and open-item data come in as keyword arguments (rows fetched
    elsewhere), e.g. ``info_from_bundle(d, versions=(...), last_eval=...)``.
    """
    proc: dict[str, Any] = {}
    pfile = bundle_dir / "process.yaml"
    if pfile.is_file():
        loaded = yaml.safe_load(pfile.read_text())
        if isinstance(loaded, dict):
            proc = loaded
    skills: list[str] = []
    for comp in proc.get("components") or []:
        if isinstance(comp, dict) and comp.get("type") == "skill" and comp.get("ref"):
            skills.append(str(comp["ref"]))
    steps: list[Step] = []
    ffile = bundle_dir / "flow.json"
    raw_steps: list[Any] = []
    if ffile.is_file():
        try:
            flow = json.loads(ffile.read_text())
        except json.JSONDecodeError:
            flow = {}
        if isinstance(flow, dict):
            raw_steps = flow.get("steps") or flow.get("nodes") or []
    for raw in raw_steps:
        if isinstance(raw, dict):
            name = raw.get("title") or raw.get("name") or raw.get("id")
            if name:
                steps.append(
                    Step(
                        str(name),
                        str(raw.get("description") or ""),
                        tuple(str(s) for s in raw.get("skills") or ()),
                    )
                )
    return AgentInfo(
        agent=bundle_dir.name,
        display_name=str(proc.get("display_name") or ""),
        description=str(proc.get("description") or ""),
        steps=tuple(steps),
        skills=tuple(skills),
        **extra,
    )


_CSS = """
:root{--bg:#fff;--fg:#1c2024;--mut:#5f6872;--card:#f4f6f8;--line:#d9dee3;--acc:#1f6feb;--ok:#1a7f37;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171a;--fg:#e6e9ec;--mut:#9aa4ae;--card:#1e2328;--line:#343b42;--acc:#58a6ff;--ok:#3fb950;--warn:#d29922}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:60rem;margin:0 auto;padding:1.5rem 16px 3rem}
h1{margin:0 0 .25rem;font-size:1.6rem}h2{font-size:1.15rem;margin:1.5rem 0 .5rem}
.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}
nav{display:flex;flex-wrap:wrap;gap:.4rem;margin:1rem 0}
nav button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.4rem .8rem;font:inherit;cursor:pointer}
nav button[aria-selected="true"]{border-color:var(--acc);color:var(--acc)}
section.tab{display:none}section.tab.on{display:block}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
details{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.5rem .8rem;margin:.4rem 0}
summary{cursor:pointer;font-weight:600}
.tbl{overflow-x:auto}
""".strip()

_JS = """
(function(){
  var btns=document.querySelectorAll('nav button');
  function show(id){
    document.querySelectorAll('section.tab').forEach(function(s){s.classList.toggle('on',s.id===id)});
    btns.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===id?'true':'false')});
  }
  btns.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab)})});
  if(btns.length){show(btns[0].dataset.tab)}
})();
""".strip()


def html_page(title: str, body: str, script: str = "") -> str:
    """A self-contained page shell (inline CSS, optional inline JS, no external URLs)."""
    js = f"<script>{script}</script>" if script else ""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main>{js}</body></html>\n"
    )


def e(value: object) -> str:
    """Escape a value for HTML; empty values show a dash."""
    text = "" if value is None else str(value)
    return escape(text) if text.strip() else _DASH


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="mut">Nothing recorded yet.</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _tabs(info: AgentInfo) -> list[tuple[str, str, str]]:
    steps = (
        "".join(
            f"<details><summary>{i}. {e(s.name)}</summary><p>{e(s.description)}</p>"
            + (
                '<p class="mut">Skills: ' + ", ".join(escape(k) for k in s.skills) + "</p>"
                if s.skills
                else ""
            )
            + "</details>"
            for i, s in enumerate(info.steps, 1)
        )
        or '<p class="mut">No steps recorded yet.</p>'
    )
    skills = (
        "<ul>" + "".join(f"<li>{escape(s)}</li>" for s in info.skills) + "</ul>"
        if info.skills
        else '<p class="mut">No skills attached.</p>'
    )
    versions = _table(
        ["Version", "Date", "Status", "Note"],
        [
            [
                e(v.version),
                e(v.date),
                "live" if v.live else ("staged" if v.staged else "earlier"),
                e(v.note),
            ]
            for v in info.versions
        ],
    )
    ds = info.dataset
    dataset = (
        f"<p><strong>{e(ds.name)}</strong>: {ds.case_count} case(s)"
        + (f" ({', '.join(escape(k) for k in ds.kinds)})" if ds.kinds else "")
        + "</p>"
        if ds
        else '<p class="mut">No dataset yet.</p>'
    )
    criteria = _table(
        ["Criterion", "Target", "Current", "Met"],
        [
            [
                e(c.name),
                e(c.target),
                e(c.current),
                '<span class="ok">met</span>' if c.met else '<span class="warn">open</span>',
            ]
            for c in info.criteria
        ],
    )
    checks = (
        "<ul>" + "".join(f"<li>{escape(c)}</li>" for c in info.checks) + "</ul>"
        if info.checks
        else '<p class="mut">No checks defined.</p>'
    )
    ev = info.last_eval
    last_eval = (
        f"<p>Run on {e(ev.date)}: score <strong>{e(ev.score)}</strong>, "
        f"{ev.passed} passed, {ev.failed} failed.</p><p>{e(ev.summary)}</p>"
        if ev
        else '<p class="mut">No evaluation has run yet.</p>'
    )
    items = _table(
        ["Item", "Who", "Due"],
        [[e(i.title), owner_label(i.owner), e(i.due)] for i in info.open_items],
    )
    scenarios = (
        "".join(
            f"<details><summary>{e(s.title)}</summary><p>{e(s.situation)}</p>"
            f'<p class="mut">{e(s.behaviour)}</p></details>'
            for s in info.scenarios
        )
        or '<p class="mut">No scenarios yet.</p>'
    )
    return [
        ("steps", "Steps", steps),
        ("skills", "Skills", skills),
        ("versions", "Versions", versions),
        ("dataset", "Dataset", dataset),
        ("criteria", "Criteria", criteria),
        ("checks", "Checks", checks),
        ("eval", "Last eval", last_eval),
        ("items", "Open items", items),
        ("scenarios", "Scenarios", scenarios),
    ]


def render_explainer(info: AgentInfo) -> str:
    tabs = _tabs(info)
    nav = (
        "<nav>"
        + "".join(
            f'<button type="button" data-tab="{tid}" aria-selected="false">{label}</button>'
            for tid, label, _ in tabs
        )
        + "</nav>"
    )
    sections = "".join(
        f'<section class="tab" id="{tid}"><h2>{label}</h2>{content}</section>'
        for tid, label, content in tabs
    )
    head = f"<h1>{escape(info.title)}</h1>"
    if info.description:
        head += f'<p class="mut">{escape(info.description)}</p>'
    return html_page(f"{info.title} explainer", head + nav + sections, _JS)


def explainer_path(agent_dir: Path) -> Path:
    return agent_dir / "docs" / "explainer.html"


def generate_explainer(info: AgentInfo, agent_dir: Path) -> GenerationResult:
    """Write ``<agent_dir>/docs/explainer.html``. Never raises: failure is reported."""
    try:
        out = explainer_path(agent_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_explainer(info), encoding="utf-8")
        return GenerationResult(True, [out])
    except Exception as exc:  # reported on the release, which is not blocked (PLAN §16)
        return GenerationResult(False, [], f"explainer failed: {type(exc).__name__}: {exc}")
