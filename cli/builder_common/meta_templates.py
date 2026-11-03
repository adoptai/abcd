"""The meta agents as shippable templates (PLAN.md section 13).

Templates live in ``cli/builder_common/templates/meta/``:

* ``<agent>/process.yaml`` and ``<agent>/instructions.md``: one agent bundle each;
* ``skills/<skill>/``: skills an agent needs (``abcd-release-gate``) and the ``maker-checker``
  skill, which is rendered once per live agent from that agent's checks.

Placeholders are ``${client}``, ``${env}`` and (maker-checker only) ``${agent}``,
``${checks_table}`` and ``${criteria_note}``. Templates hold no client name, no URL and no secret.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

from cli.builder_common.errors import UsageError
from cli.builder_common.template_loader import TEMPLATES_DIR

META_DIR = TEMPLATES_DIR / "meta"
SKILLS_DIR = META_DIR / "skills"
MAKER_CHECKER = "maker-checker"
CLIENT = "client"
HOME = "home"

_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_]+\}")
_ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class MetaAgent:
    name: str
    where: str  # "client" org or Builder "home" org
    summary: str
    skills: tuple[str, ...] = ()  # template skills installed with it


AGENTS: dict[str, MetaAgent] = {
    a.name: a
    for a in (
        MetaAgent("status-agent", CLIENT, "Read-only Q&A over builder-status-<client>"),
        MetaAgent("context-agent", CLIENT, "Walks the SME through context gaps (builder-sme)"),
        MetaAgent("context-curator", CLIENT, "Nightly: proposes pages, decisions, open items"),
        MetaAgent(
            "abcd-runner",
            HOME,
            "Runs abcd for the FDE in chat; release is a gated step",
            skills=("abcd-release-gate",),
        ),
        MetaAgent("abcd-nightly", HOME, "Nightly cloud run: abcd cloud nightly"),
        MetaAgent("abcd-weekly", HOME, "Friday run: status publish --freeze and the scorecard"),
    )
}
HOME_AGENTS = tuple(n for n, a in AGENTS.items() if a.where == HOME)


def agent_names(where: str | None = None) -> list[str]:
    return [n for n, a in AGENTS.items() if where in (None, a.where)]


def check_env(env: str) -> str:
    if not _ENV_RE.match(env):
        raise UsageError(f"{env!r} is not a valid workspace name")
    return env


def render(text: str, values: Mapping[str, str]) -> str:
    """Substitute ``${name}``; a placeholder left over is a template bug, so it raises."""
    out = Template(text).safe_substitute(**values)
    left = _PLACEHOLDER.findall(out)
    if left:
        raise UsageError(f"unresolved placeholder(s) {sorted(set(left))} in a meta template")
    return out


def _tree(root: Path, values: Mapping[str, str]) -> dict[str, str]:
    if not root.is_dir():
        raise UsageError(f"no such meta template: {root.name}")
    return {
        p.relative_to(root).as_posix(): render(p.read_text(encoding="utf-8"), values)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def agent_files(name: str, values: Mapping[str, str]) -> dict[str, str]:
    """``{path relative to harness/agents/<name>/: text}`` for one meta agent."""
    if name not in AGENTS:
        raise UsageError(f"unknown meta agent {name!r}; known: {', '.join(AGENTS)}")
    return _tree(META_DIR / name, values)


def skill_files(skill: str, values: Mapping[str, str]) -> dict[str, str]:
    """``{path relative to harness/skills/<skill>/: text}`` for a template skill."""
    return _tree(SKILLS_DIR / skill, values)


def raw_template_files() -> list[Path]:
    """Every template file, unrendered (for the no-client-names test)."""
    return sorted(p for p in META_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
