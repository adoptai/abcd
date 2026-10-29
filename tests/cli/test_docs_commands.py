"""Every ``abcd ...`` and ``python cli/x.py`` command written in the docs resolves."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.abcd import build_registry
from cli.core import passthrough

ROOT = Path(__file__).resolve().parents[2]
DOCS = [
    ROOT / "README.md",
    ROOT / "agents.md",
    *sorted((ROOT / "abcd-builder" / "skills").glob("*/SKILL.md")),
]
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_INLINE = re.compile(r"`([^`\n]+)`")
_WORD = re.compile(r"^[a-z][a-z0-9_-]*$")


def code_snippets(text: str) -> list[str]:
    """Lines of fenced blocks plus inline code spans."""
    out: list[str] = []
    for block in _FENCE.findall(text):
        out.extend(block.splitlines())
    out.extend(_INLINE.findall(_FENCE.sub("", text)))
    return out


def abcd_commands(snippet: str) -> list[list[str]]:
    """Command words of each ``abcd ...`` invocation in a snippet (stops at the first non-word)."""
    found = []
    for m in re.finditer(r"^(?:\$ )?(?:poetry run )?abcd( [^\n]*)?$", snippet.strip()):
        words: list[str] = []
        for tok in (m.group(1) or "").split():
            if not _WORD.match(tok):
                break
            words.append(tok)
        found.append(words)
    return found


def _resolves(words: list[str], registry, scripts: list[str]) -> bool:  # type: ignore[no-untyped-def]
    if not words:
        return True  # bare `abcd`
    if words[0] == "wdl":
        return len(words) == 1 or words[1] in scripts
    if len(words) == 1 and (words[0] in registry.groups or words[0] in registry.top):
        return True  # `abcd skill ...`: a group named on its own
    return registry.resolve(words) is not None


def test_docs_were_found() -> None:
    assert all(p.exists() for p in DOCS) and len(DOCS) >= 6


@pytest.mark.parametrize(
    "doc", DOCS, ids=lambda p: p.name if p.name != "SKILL.md" else p.parent.name
)
def test_abcd_commands_in_docs_resolve(doc: Path) -> None:
    registry = build_registry()
    scripts = passthrough.list_scripts()
    bad = []
    count = 0
    for snippet in code_snippets(doc.read_text()):
        for words in abcd_commands(snippet):
            count += 1
            if not _resolves(words, registry, scripts):
                bad.append(f"abcd {' '.join(words)}   (in: {snippet.strip()[:80]})")
    assert not bad, "\n".join(bad)
    if doc.name == "agents.md" or doc.parent.name in ("skill-builder", "discover-and-plan"):
        assert count > 0


@pytest.mark.parametrize(
    "doc", DOCS, ids=lambda p: p.name if p.name != "SKILL.md" else p.parent.name
)
def test_python_cli_scripts_in_docs_exist(doc: Path) -> None:
    text = doc.read_text()
    missing = {
        name
        for name in re.findall(r"python cli/([A-Za-z0-9_<>.-]+?)\.py", text)
        if not name.startswith("<") and not (ROOT / "cli" / f"{name}.py").exists()
    }
    assert not missing, sorted(missing)


def test_moved_skills_use_abcd_not_old_entry_points() -> None:
    sk = ROOT / "abcd-builder" / "skills"
    for name in ("skill-builder", "discover-and-plan"):
        text = (sk / name / "SKILL.md").read_text()
        assert not re.search(r"python cli/(harness_|noui_workspace|tabby_bootstrap)", text), name
    for name in ("action-builder", "pipeline-builder"):
        text = (sk / name / "SKILL.md").read_text()
        assert "python cli/" not in text.replace("`python cli/<script>.py` still works", ""), name
        assert "abcd wdl" in text


def test_resolver_rejects_a_bogus_command() -> None:
    registry = build_registry()
    assert registry.resolve(["skill", "nonsense"]) is None
    assert registry.resolve(["skill", "push"]) is not None
    assert abcd_commands("abcd skill bogus --env x") == [["skill", "bogus"]]
