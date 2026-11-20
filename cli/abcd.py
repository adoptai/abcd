#!/usr/bin/env python3
"""abcd: one CLI for the Adopt Builder, the agent harness, NoUI and Tabby.

    abcd <group> <command> [args]       e.g. abcd skill push ./my-skill --env acme
    abcd wdl <script> [args]            the legacy WDL scripts, unchanged (abcd wdl --list)

Every command takes --env, --json, --yes and --verbose (and --dry-run when it writes).
Exit codes: 0 done, 1 refused or a check failed, 2 usage error, 3 platform or network error.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):  # run as `python cli/abcd.py` from a checkout without an install
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.core.registry import Registry, discover

# Moved scripts: each exposes register(registry) and keeps its own flat entry point.
MOVED_MODULES = (
    "cli.workspace",
    "cli.harness_skill",
    "cli.harness_process",
    "cli.harness_run",
    "cli.harness_runs",
    "cli.harness_trace",
    "cli.harness_conversations",
    "cli.harness_workstream",
    "cli.noui_workspace",
    "cli.tabby_bootstrap",
)

DESCRIPTION = __doc__ or ""


def build_registry() -> Registry:
    """The full ``abcd`` command tree: moved scripts, Builder modules, ``abcd wdl``."""
    import importlib

    from cli.core import passthrough

    registry = Registry(prog="abcd", description=DESCRIPTION)
    for name in MOVED_MODULES:
        importlib.import_module(name).register(registry)
    discover(registry)
    passthrough.register(registry)
    return registry


def main(argv: Sequence[str] | None = None) -> int:
    return build_registry().run(argv)


if __name__ == "__main__":
    sys.exit(main())
