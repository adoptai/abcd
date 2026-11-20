"""Loader for the Builder file templates in ``cli/builder_common/templates/``."""

from __future__ import annotations

from pathlib import Path
from string import Template

from cli.builder_common.errors import UsageError

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

GITIGNORE = "workspace.gitignore"
ADOPT_MD = "ADOPT.md.tmpl"
PROCESS_YAML = "process.yaml.tmpl"


def load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if not path.is_file():
        raise UsageError(f"Unknown Builder template: {name}")
    return path.read_text(encoding="utf-8")


def render_template(template: str, /, **values: str) -> str:
    """Substitute ``$var`` placeholders; unknown placeholders are left in place."""
    return Template(load_template(template)).safe_substitute(**values)


def gitignore_patterns() -> list[str]:
    """The ignore patterns in the workspace .gitignore (no comments or blanks)."""
    return [
        line.strip()
        for line in load_template(GITIGNORE).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
