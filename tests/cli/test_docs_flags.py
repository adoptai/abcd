"""Every ``--flag`` written after an ``abcd ...`` command in the docs exists on that command."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.abcd import build_registry
from tests.cli.test_docs_commands import DOCS, code_snippets

_INVOCATION = re.compile(r"^(?:\$ )?(?:poetry run )?abcd( [^\n]*)?$")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def documented_flags(snippet: str) -> tuple[list[str], list[str]] | None:
    """``(command words, flags)`` of one ``abcd ...`` invocation, or None."""
    m = _INVOCATION.match(snippet.strip())
    if not m:
        return None
    rest = (m.group(1) or "").split("#", 1)[0]
    words: list[str] = []
    for tok in rest.split():
        if not re.match(r"^[a-z][a-z0-9_-]*$", tok):
            break
        words.append(tok)
    return words, _FLAG.findall(rest)


def _known_options(spec) -> set[str]:  # type: ignore[no-untyped-def]
    parser = spec.parser
    return {opt for action in parser._actions for opt in action.option_strings} if parser else set()


@pytest.mark.parametrize(
    "doc", DOCS, ids=lambda p: p.name if p.name != "SKILL.md" else p.parent.name
)
def test_documented_flags_exist(doc: Path) -> None:
    registry = build_registry()
    bad = []
    for snippet in code_snippets(doc.read_text()):
        parsed = documented_flags(snippet)
        if not parsed or not parsed[0]:
            continue
        words, flags = parsed
        spec = registry.resolve(words)
        if spec is None or spec.passthrough:
            continue  # unknown commands are reported by test_docs_commands; passthrough args are free
        known = _known_options(spec)
        for flag in flags:
            if flag not in known:
                bad.append(
                    f"abcd {' '.join(spec.path)}: unknown {flag}   (in: {snippet.strip()[:90]})"
                )
        if flags:  # a full invocation (not a bare mention in prose) must carry required options
            for opt in _required_options(spec):
                if not any(o in flags for o in opt):
                    bad.append(
                        f"abcd {' '.join(spec.path)}: missing required {opt[-1]}   "
                        f"(in: {snippet.strip()[:90]})"
                    )
    assert not bad, "\n".join(bad)


def _required_options(spec) -> list[list[str]]:  # type: ignore[no-untyped-def]
    parser = spec.parser
    if not parser:
        return []
    return [list(a.option_strings) for a in parser._actions if a.option_strings and a.required]


def test_flag_parser_extracts_flags() -> None:
    assert documented_flags("abcd skill push ./s --replace --env acme  # note --ignored") == (
        ["skill", "push"],
        ["--replace", "--env"],
    )
    assert documented_flags("echo abcd") is None
