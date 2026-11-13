"""The command registry: one root parser built from groups and commands.

Every module that owns commands exposes ``register(registry)``. A leaf command always carries the
global flags ``--env --json --yes --verbose`` (plus ``--dry-run`` when ``writes=True``), so a
module only declares its own arguments on the parser ``command()`` returns.

Two modes:

* ``Registry(prog="abcd")``: the ``abcd <group> <command>`` tree. ``--json`` wraps the result in
  one document and every command records an event.
* ``Registry(prog=..., flat=True)``: the legacy standalone CLI of a moved script. Commands are
  exposed under their ``legacy_name`` and run exactly like the old script (no wrapping, no events).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.core.output import EXIT_USAGE, Result, emit, exit_code_for, to_result

if TYPE_CHECKING:
    from cli.core.context import Context

Handler = Callable[["Context"], "int | Result | None"]


@dataclass
class CommandSpec:
    """A registered leaf command."""

    path: tuple[str, ...]
    handler: Handler
    help: str
    writes: bool = False
    legacy_name: str | None = None
    aliases: tuple[str, ...] = ()
    parser: argparse.ArgumentParser | None = field(default=None, repr=False)
    passthrough: bool = False

    @property
    def name(self) -> str:
        return " ".join(self.path)


class _Tee(io.TextIOBase):
    """Capture writes and mirror them to stderr (stdout is reserved for the JSON document)."""

    def __init__(self, buffer: io.StringIO, mirror: Any) -> None:
        self._buffer = buffer
        self._mirror = mirror

    def write(self, s: str) -> int:
        self._buffer.write(s)
        self._mirror.write(s)
        return len(s)

    def flush(self) -> None:
        self._mirror.flush()


def _installed_version() -> str:
    """The installed package version (``unknown`` when run from a bare source tree)."""
    from importlib import metadata

    try:
        return metadata.version("abcd")
    except metadata.PackageNotFoundError:
        return "unknown"


def add_global_flags(parser: argparse.ArgumentParser, *, writes: bool) -> None:
    """Add ``--env --json --yes --verbose`` (and ``--dry-run`` for writing commands)."""
    existing = {opt for action in parser._actions for opt in action.option_strings}

    def add(*names: str, **kw: Any) -> None:
        if not existing.intersection(names):
            parser.add_argument(*names, **kw)

    add("--env", help="Workspace to use (defaults to the active workspace)")
    add("--json", action="store_true", help="Print one JSON document on stdout")
    add("--yes", "-y", action="store_true", help="Never prompt: approve what the command asks for")
    add("--verbose", action="store_true", help="Print debug detail to stderr")
    if writes:
        add("--dry-run", action="store_true", help="Show what would change; change nothing")


class Group:
    """A command group (``abcd skill ...``)."""

    def __init__(self, registry: Registry, name: str, help: str) -> None:
        self.registry = registry
        self.name = name
        self.help = help
        self.specs: dict[str, CommandSpec] = {}
        self._parser: argparse.ArgumentParser | None = None
        self._sub: Any = None

    def command(
        self,
        name: str,
        handler: Handler,
        *,
        help: str = "",
        aliases: Sequence[str] = (),
        writes: bool = False,
        legacy_name: str | None = None,
        passthrough: bool = False,
    ) -> argparse.ArgumentParser:
        """Register ``abcd <group> <name>`` and return its leaf parser for extra arguments.

        ``passthrough=True``: leading global flags are read, then every remaining token goes
        verbatim to the handler as ``ctx.args.rest`` (used to forward to another CLI).
        """
        spec = CommandSpec(
            (self.name, name), handler, help, writes, legacy_name, tuple(aliases), None, passthrough
        )
        if name in self.specs:
            raise ValueError(f"command already registered: {spec.name}")
        self.specs[name] = spec
        spec.parser = self.registry._leaf_parser(self, spec)
        return spec.parser


class Registry:
    def __init__(self, prog: str = "abcd", flat: bool = False, description: str = "") -> None:
        self.prog = prog
        self.flat = flat
        self.description = description
        self.groups: dict[str, Group] = {}
        self.top: dict[str, CommandSpec] = {}
        self._root: argparse.ArgumentParser | None = None
        self._root_sub: Any = None

    # -- registration -------------------------------------------------------------------
    def _ensure_root(self) -> Any:
        if self._root is None:
            self._root = argparse.ArgumentParser(
                prog=self.prog,
                description=self.description,
                formatter_class=argparse.RawDescriptionHelpFormatter,
            )
            self._root.add_argument(
                "--version", action="version", version=f"{self.prog} {_installed_version()}"
            )
            self._root_sub = self._root.add_subparsers(dest="_command", required=True)
        return self._root_sub

    def group(self, name: str, help: str = "") -> Group:
        """Return the group ``name``, creating it on first use (idempotent)."""
        group = self.groups.get(name)
        if group is None:
            group = Group(self, name, help)
            self.groups[name] = group
        elif help and not group.help:
            group.help = help
        return group

    def command(
        self,
        name: str,
        handler: Handler,
        *,
        help: str = "",
        aliases: Sequence[str] = (),
        writes: bool = False,
        legacy_name: str | None = None,
        passthrough: bool = False,
    ) -> argparse.ArgumentParser:
        """Register a top-level command (``abcd <name>``)."""
        spec = CommandSpec(
            (name,), handler, help, writes, legacy_name, tuple(aliases), None, passthrough
        )
        if name in self.top:
            raise ValueError(f"command already registered: {name}")
        self.top[name] = spec
        spec.parser = self._leaf_parser(None, spec)
        return spec.parser

    def _leaf_parser(self, group: Group | None, spec: CommandSpec) -> argparse.ArgumentParser:
        """Create the leaf parser in the right place of the tree."""
        if self.flat:
            # Standalone script: commands sit at the root under their legacy name.
            leaf_name = spec.legacy_name or spec.path[-1]
            sub = self._ensure_root()
            parser = sub.add_parser(
                leaf_name,
                help=spec.help,
                description=spec.help,
                aliases=list(spec.aliases),
            )
        elif group is None:
            sub = self._ensure_root()
            parser = sub.add_parser(
                spec.path[0], help=spec.help, description=spec.help, aliases=list(spec.aliases)
            )
        else:
            if group._parser is None:
                root_sub = self._ensure_root()
                group._parser = root_sub.add_parser(group.name, help=group.help)
                group._sub = group._parser.add_subparsers(dest="_subcommand", required=True)
            parser = group._sub.add_parser(
                spec.path[-1], help=spec.help, description=spec.help, aliases=list(spec.aliases)
            )
        add_global_flags(parser, writes=spec.writes)
        if spec.passthrough:
            parser.add_argument("rest", nargs=argparse.REMAINDER, help="Arguments, passed through")
        parser.set_defaults(_spec=spec)
        return parser

    # -- introspection ------------------------------------------------------------------
    def specs(self) -> Iterator[CommandSpec]:
        """Every registered leaf command."""
        yield from self.top.values()
        for group in self.groups.values():
            yield from group.specs.values()

    def build(self) -> argparse.ArgumentParser:
        self._ensure_root()
        assert self._root is not None
        return self._root

    def resolve(self, words: Sequence[str]) -> CommandSpec | None:
        """Find the command named by leading words (``["skill", "push", ...]``)."""
        for spec in self.specs():
            names = [spec.path] + [spec.path[:-1] + (a,) for a in spec.aliases]
            if self.flat:
                names = [(spec.legacy_name or spec.path[-1],)]
            for n in names:
                if tuple(words[: len(n)]) == n:
                    return spec
        return None

    def _match_passthrough(self, words: list[str]) -> tuple[CommandSpec, list[str]] | None:
        """If ``words`` start with a passthrough command, return it and the tokens after it."""
        for spec in self.specs():
            if not spec.passthrough:
                continue
            for alias in (None, *spec.aliases):
                name = spec.path if alias is None else (*spec.path[:-1], alias)
                if tuple(words[: len(name)]) == name:
                    return spec, words[len(name) :]
        return None

    def _run_passthrough(self, spec: CommandSpec, tokens: list[str]) -> int:
        from cli.core.context import Context

        flags: dict[str, Any] = {"env": None, "json": False, "yes": False, "verbose": False}
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == "--env":
                if i + 1 >= len(tokens):
                    print(f"{self.prog}: error: --env needs a value", file=sys.stderr)
                    return EXIT_USAGE
                flags["env"] = tokens[i + 1]
                i += 2
            elif t.startswith("--env="):
                flags["env"] = t.split("=", 1)[1]
                i += 1
            elif t in ("--json", "--verbose"):
                flags[t[2:]] = True
                i += 1
            elif t in ("--yes", "-y"):
                flags["yes"] = True
                i += 1
            else:
                break
        if i < len(tokens) and tokens[i] == "--":
            i += 1
        args = argparse.Namespace(**flags, dry_run=False, rest=tokens[i:], _spec=spec)
        return execute(Context(args=args, spec=spec, registry=self))

    # -- execution ----------------------------------------------------------------------
    def run(self, argv: Sequence[str] | None = None) -> int:
        """Parse ``argv`` and run the command; returns the process exit code."""
        from cli.core.context import Context

        parser = self.build()
        words = list(sys.argv[1:] if argv is None else argv)
        if not self.flat:
            matched = self._match_passthrough(words)
            if matched is not None:
                spec, rest = matched
                return self._run_passthrough(spec, rest)
        args = parser.parse_args(words)
        chosen: CommandSpec | None = getattr(args, "_spec", None)
        if chosen is None:
            parser.print_help(sys.stderr)
            return EXIT_USAGE
        ctx = Context(args=args, spec=chosen, registry=self)
        return execute(ctx)


def execute(ctx: Context) -> int:
    """Run one command: standalone (legacy behaviour) or wrapped (abcd)."""
    from cli.core.events import track

    spec = ctx.spec
    ctx.apply_workspaces_override()
    if ctx.registry.flat:
        # Old script behaviour: no wrapping, no events; exceptions surface as they always did,
        # except Builder errors (e.g. a refused PAT exchange), which print and keep their code.
        from cli.builder_common.errors import BuilderError

        try:
            result = to_result(spec.handler(ctx))
        except BuilderError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return exc.code
        emit(spec.name, result, as_json=False)
        return result.code

    as_json = ctx.as_json
    buffer = io.StringIO()
    with track(ctx) as tracker:
        out_cm: Any = (
            contextlib.redirect_stdout(_Tee(buffer, sys.stderr))
            if as_json
            else contextlib.nullcontext()
        )
        with out_cm:
            try:
                result = to_result(spec.handler(ctx))
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    code = 0
                elif not isinstance(code, int):
                    print(code, file=sys.stderr)
                    code = 1
                result = Result(code, text="")
            except KeyboardInterrupt:
                result = Result(130, message="interrupted")
            except Exception as exc:
                if ctx.verbose:
                    import traceback

                    traceback.print_exc()
                result = Result(exit_code_for(exc), message=str(exc) or type(exc).__name__)
        tracker.result = result
    emit(spec.name, result, as_json=as_json, captured=buffer.getvalue())
    return result.code


def discover(registry: Registry, package_dir: Path | None = None) -> list[str]:
    """Import every ``cli/builder_*.py`` that exposes ``register(registry)`` and call it.

    Builder modules only register commands (no standalone script). Returns the module names that
    registered. An import error is raised, not hidden: a broken module must be visible.
    """
    import importlib

    directory = package_dir or Path(__file__).resolve().parent.parent
    registered: list[str] = []
    for path in sorted(directory.glob("builder_*.py")):
        module = importlib.import_module(f"cli.{path.stem}")
        fn = getattr(module, "register", None)
        if callable(fn):
            fn(registry)
            registered.append(path.stem)
    return registered


def legacy_main(
    prog: str,
    register: Callable[[Registry], None],
    description: str | None,
    argv: Sequence[str] | None = None,
) -> int:
    """The flat entry point of a moved script: its commands under their old names."""
    registry = Registry(prog=prog, flat=True, description=description or "")
    register(registry)
    return registry.run(argv)
