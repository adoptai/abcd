"""``abcd wdl <script> [args]``: run a legacy WDL script's ``main()`` unchanged."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from cli.core.output import EXIT_USAGE, Result

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

CLI_DIR = Path(__file__).resolve().parent.parent
# Scripts that are NOT passthrough targets: the framework itself and the moved/native commands.
_EXCLUDED = {"abcd"}


def is_excluded(stem: str) -> bool:
    return (
        stem.startswith("_")
        or stem in _EXCLUDED
        or stem.startswith("harness_")
        or stem.startswith("builder_")
        or stem in {"noui_workspace", "tabby_bootstrap"}
    )


def list_scripts() -> list[str]:
    """Names of the legacy scripts ``abcd wdl`` can run (``cli/<name>.py`` with a ``main``)."""
    names = []
    for path in sorted(CLI_DIR.glob("*.py")):
        if is_excluded(path.stem):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "def main(" in text:
            names.append(path.stem)
    return names


def run_script(script: str, argv: Sequence[str]) -> int:
    """Import ``cli.<script>`` and call ``main()`` with ``argv`` as ``sys.argv[1:]``."""
    scripts = list_scripts()
    if script not in scripts:
        print(
            f"Unknown WDL script '{script}'. Run 'abcd wdl --list' to see the scripts.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    module = importlib.import_module(f"cli.{script}")
    saved = sys.argv
    sys.argv = [f"{script}.py", *argv]
    try:
        rc = module.main()
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        sys.argv = saved
    return rc if isinstance(rc, int) else 0


def _handle(ctx: Context) -> int | Result:
    rest = list(ctx.args.rest)
    if ctx.env_arg:
        return Result.usage("give --env after the script name: abcd wdl <script> --env ENV")
    if not rest or rest[0] in ("--list", "-h", "--help"):
        scripts = list_scripts()
        text = "\n".join(scripts)
        if not rest or rest[0] != "--list":
            text = "usage: abcd wdl <script> [args]   (abcd wdl --list)\n" + text
        return Result.done({"scripts": scripts}, text)
    return run_script(rest[0], rest[1:])


def register(registry: Registry) -> None:
    """Register ``abcd wdl``. Every token after ``wdl`` is handed to the script untouched."""
    registry.command(
        "wdl",
        _handle,
        help="Run a legacy WDL script: abcd wdl <script> [args] (abcd wdl --list)",
        passthrough=True,
    )
