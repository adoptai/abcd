"""The plugin layout: root marketplace -> nested plugins -> Agent Skills."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def skill_files() -> list[Path]:
    skip = {"tabby", "workspaces", ".git", "node_modules"}
    return [p for p in ROOT.rglob("SKILL.md") if not skip & set(p.relative_to(ROOT).parts)]


def meta(path: Path) -> dict:
    m = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    assert m, f"{path}: no frontmatter"
    data = yaml.safe_load(m.group(1))
    assert isinstance(data, dict), path
    return data


def test_marketplace_lists_existing_plugins() -> None:
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    names = []
    for entry in market["plugins"]:
        plugin_dir = ROOT / entry["source"]
        manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == entry["name"]
        assert isinstance(manifest.get("author", {}), dict), "Claude Code wants author as an object"
        assert list((plugin_dir / "skills").glob("*/SKILL.md")), plugin_dir
        names.append(entry["name"])
    assert {"abcd-builder", "adopt-skill-review"} <= set(names)


def test_builder_has_the_four_skills() -> None:
    skills = sorted(p.parent.name for p in (ROOT / "abcd-builder" / "skills").glob("*/SKILL.md"))
    assert skills == ["action-builder", "discover-and-plan", "pipeline-builder", "skill-builder"]


def test_plugin_skills_are_valid_agent_skills() -> None:
    for path in (ROOT / "abcd-builder" / "skills").glob("*/SKILL.md"):
        data = meta(path)
        assert data["name"] == path.parent.name
        assert 0 < len(data["description"]) <= 1024
        for link in re.findall(r"\]\(([^)#\s]+)", path.read_text(encoding="utf-8")):
            if not link.startswith(("http://", "https://")):
                assert (path.parent / link).exists(), f"{path}: broken link {link}"


def test_exactly_one_skill_is_named_noui() -> None:
    """noui shipped two skills both named `noui`; after the merge only the
    harness edition keeps the name (the toolkit doc became cli/noui/README.md)."""
    named = [p.relative_to(ROOT).as_posix() for p in skill_files() if meta(p).get("name") == "noui"]
    assert named == ["harness-skills/noui/SKILL.md"]


def test_skill_names_are_unique() -> None:
    names = [meta(p)["name"] for p in skill_files()]
    assert len(names) == len(set(names)), names
