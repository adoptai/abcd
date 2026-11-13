"""Training kit: ``harness/agents/<agent>/training/*.md`` (PLAN.md §16).

quickstart, prompts, scenarios, faq and what's new, from the agent info plus
version and eval data. Only prompts that worked on a dataset case ship
(``validate_training_prompts``). Failure to generate is reported, never raised.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cli.builder_common.explain import AgentInfo, GenerationResult

TRAINING_FILES = ("quickstart.md", "prompts.md", "scenarios.md", "faq.md", "whats-new.md")


@dataclass
class PromptValidation:
    shipped: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FaqEntry:
    question: str
    answer: str


def validate_training_prompts(
    prompts: Sequence[str], run_fn: Callable[[str], bool]
) -> PromptValidation:
    """Keep only prompts for which ``run_fn`` (a dataset-case run) returns True.

    A prompt whose run raises counts as not working. Duplicates and blanks are dropped.
    """
    result = PromptValidation()
    seen: set[str] = set()
    for raw in prompts:
        prompt = raw.strip()
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        try:
            worked = bool(run_fn(prompt))
        except Exception:
            worked = False
        (result.shipped if worked else result.dropped).append(prompt)
    return result


def _quickstart(info: AgentInfo) -> str:
    lines = [f"# {info.title}: quickstart", ""]
    if info.description:
        lines += [info.description, ""]
    lines += ["## What it does", ""]
    if info.steps:
        lines += [
            f"{i}. **{s.name}**" + (f": {s.description}" if s.description else "")
            for i, s in enumerate(info.steps, 1)
        ]
    else:
        lines.append("Steps will be listed here once the agent's flow is recorded.")
    live = next((v for v in info.versions if v.live), None)
    lines += [
        "",
        "## Version",
        "",
        f"Live version: {live.version if live else 'not released yet'}.",
    ]
    if info.last_eval:
        ev = info.last_eval
        lines += ["", f"Last evaluation ({ev.date or 'undated'}): score {ev.score or 'n/a'}."]
    return "\n".join(lines) + "\n"


def _prompts(info: AgentInfo, prompts: Sequence[str]) -> str:
    lines = [f"# {info.title}: prompts to try", ""]
    if prompts:
        lines += ["Each prompt below was run on a real test case and worked.", ""]
        lines += [f"- {p}" for p in prompts]
    else:
        lines.append("No prompts have been verified yet.")
    return "\n".join(lines) + "\n"


def _scenarios(info: AgentInfo) -> str:
    lines = [f"# {info.title}: scenarios", ""]
    if not info.scenarios:
        lines.append("No scenarios recorded yet.")
    for s in info.scenarios:
        lines += [f"## {s.title}", "", s.situation or "No description.", ""]
        if s.behaviour:
            lines += [f"What the agent does: {s.behaviour}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _faq(info: AgentInfo, faq: Sequence[FaqEntry]) -> str:
    lines = [f"# {info.title}: frequently asked questions", ""]
    if not faq:
        lines.append("No questions have been collected yet.")
    for entry in faq:
        lines += [f"## {entry.question}", "", entry.answer, ""]
    return "\n".join(lines).rstrip() + "\n"


def _whats_new(info: AgentInfo, notes: Sequence[str]) -> str:
    lines = [f"# {info.title}: what's new", ""]
    if notes:
        lines += [f"- {n}" for n in notes]
    else:
        for v in info.versions:
            lines.append(
                f"- {v.version}"
                + (f" ({v.date})" if v.date else "")
                + (f": {v.note}" if v.note else "")
            )
        if not info.versions:
            lines.append("Nothing released yet.")
    return "\n".join(lines) + "\n"


def render_training_kit(
    info: AgentInfo,
    prompts: Sequence[str] = (),
    faq: Sequence[FaqEntry] = (),
    whats_new: Sequence[str] = (),
) -> dict[str, str]:
    """File name to markdown. ``prompts`` must already be validated."""
    return {
        "quickstart.md": _quickstart(info),
        "prompts.md": _prompts(info, prompts),
        "scenarios.md": _scenarios(info),
        "faq.md": _faq(info, faq),
        "whats-new.md": _whats_new(info, whats_new),
    }


def generate_training_kit(
    info: AgentInfo,
    agent_dir: Path,
    prompts: Sequence[str] = (),
    faq: Sequence[FaqEntry] = (),
    whats_new: Sequence[str] = (),
) -> GenerationResult:
    """Write ``<agent_dir>/training/``. Never raises: failure is reported."""
    try:
        out_dir = agent_dir / "training"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for name, text in render_training_kit(info, prompts, faq, whats_new).items():
            path = out_dir / name
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        return GenerationResult(True, paths)
    except Exception as exc:  # reported on the release, which is not blocked (PLAN §16)
        return GenerationResult(False, [], f"training kit failed: {type(exc).__name__}: {exc}")
