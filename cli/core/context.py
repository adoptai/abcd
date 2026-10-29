"""Per-command context: the resolved workspace, its ``.env`` and lazily built API clients."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cli.core.registry import CommandSpec, Registry
    from cli.harness_common.api_client import HarnessAPIClient

WORKSPACES_ENV_VAR = "ABCD_WORKSPACES_DIR"
REQUIRE_ENV_VAR = "ABCD_REQUIRE_ENV"
_ID_TARGET_COMMANDS = frozenset({"ws create", "ws show", "ws use", "ws delete"})


def _is_source_checkout(project_root: Path) -> bool:
    return (project_root / "pyproject.toml").exists()


def apply_workspaces_override() -> Path:
    """Point every module at the workspaces directory and return it.

    ``ABCD_WORKSPACES_DIR`` wins (tests use it). Without it a source checkout keeps
    ``<repo>/workspaces`` (and any value already patched in is kept); an installed package (no ``pyproject.toml`` beside ``cli/``) uses
    ``./workspaces`` so nothing is ever written into site-packages.
    ``workspace_manager.WORKSPACES_DIR`` is imported by name in many modules, so every loaded
    ``cli.*`` module holding the old value is patched, and the manager singleton is dropped so it
    re-reads the new location. Safe to call repeatedly.
    """
    from cli.wdl_common import workspace_manager as wm

    override = os.environ.get(WORKSPACES_ENV_VAR)
    if override:
        target = Path(override).expanduser().resolve()
    elif wm.WORKSPACES_DIR == wm.PROJECT_ROOT / "workspaces" and not _is_source_checkout(
        wm.PROJECT_ROOT
    ):
        target = Path.cwd() / "workspaces"
    else:
        return wm.WORKSPACES_DIR  # a checkout default, or a value someone already patched
    set_workspaces_dir(target)
    return target


def set_workspaces_dir(target: Path) -> None:
    """Make ``target`` the workspaces directory in every loaded ``cli.*`` module."""
    from cli.wdl_common import workspace_manager as wm

    current = wm.WORKSPACES_DIR
    if current != target:
        for name, module in list(sys.modules.items()):
            if name.startswith("cli.") and getattr(module, "WORKSPACES_DIR", None) == current:
                module.__dict__["WORKSPACES_DIR"] = target
        wm.WORKSPACES_DIR = target
        wm._manager = None


@dataclass
class Context:
    """What a handler receives: parsed args plus helpers."""

    args: argparse.Namespace
    spec: CommandSpec
    registry: Registry

    def __post_init__(self) -> None:
        self._env: str | None = None
        self._env_source = "--env"
        self._client: HarnessAPIClient | None = None

    # -- flags --------------------------------------------------------------------------
    @property
    def env_arg(self) -> str | None:
        """``--env`` exactly as typed (``None`` = not given); legacy helpers resolve it."""
        return getattr(self.args, "env", None)

    @property
    def as_json(self) -> bool:
        return bool(getattr(self.args, "json", False))

    @property
    def yes(self) -> bool:
        return bool(getattr(self.args, "yes", False))

    @property
    def verbose(self) -> bool:
        return bool(getattr(self.args, "verbose", False))

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False))

    # -- workspace ----------------------------------------------------------------------
    def apply_workspaces_override(self) -> Path:
        return apply_workspaces_override()

    @property
    def workspaces_dir(self) -> Path:
        return apply_workspaces_override()

    @property
    def env(self) -> str:
        """``--env``, else ``workspaces/.active_env``, else ``default`` (resolved once, lazily).

        With ``ABCD_REQUIRE_ENV=1`` (recommended for agents) a command without an explicit
        ``--env`` refuses instead of falling back to the active workspace.
        """
        if self._env is None:
            from cli.builder_common.errors import UsageError
            from cli.wdl_common.workspace_manager import DEFAULT_ENV

            name = self.env_arg
            source = "--env"
            if not name:
                if os.environ.get(REQUIRE_ENV_VAR, "").strip().lower() in ("1", "true", "yes"):
                    raise UsageError(
                        f"--env is required ({REQUIRE_ENV_VAR}=1): name the workspace explicitly "
                        "instead of using the active one"
                    )
                marker = self.workspaces_dir / ".active_env"
                try:
                    name = marker.read_text().strip() or None
                except OSError:
                    name = None
                source = f"{marker}" if name else "the built-in default"
            self._env = name or DEFAULT_ENV
            self._env_source = source
        return self._env

    @property
    def env_source(self) -> str:
        """Where :attr:`env` came from: ``--env``, the ``.active_env`` file or the default."""
        _ = self.env
        return self._env_source

    @property
    def event_env(self) -> str | None:
        """The workspace this command acts on, for its local event; ``None`` = write none.

        ``ws create/show/use/delete`` act on their ``id`` argument, ``ws clone`` on ``--env``
        only; neither may log into whatever workspace happens to be active.
        """
        name = self.spec.name
        if name in _ID_TARGET_COMMANDS:
            return str(getattr(self.args, "id", "") or "") or None
        if name == "ws clone":
            return self.env_arg or None
        return self.env

    @property
    def env_dir(self) -> Path:
        return self.workspaces_dir / self.env

    def load_env(self) -> Path | None:
        """Load ``workspaces/<env>/.env`` into ``os.environ`` (same call as harness_common/auth)."""
        from dotenv import load_dotenv

        dotenv = self.env_dir / ".env"
        if dotenv.exists():
            load_dotenv(dotenv, override=True)
            return dotenv
        return None

    def harness_client(self) -> HarnessAPIClient:
        """The harness API client for this env (built once, on first use)."""
        if self._client is None:
            from cli.harness_common.api_client import get_harness_client_for_env

            self.load_env()
            if self.env_source != "--env":
                print(
                    f"abcd: using workspace '{self.env}' from {self.env_source} "
                    "(pass --env to choose; ABCD_REQUIRE_ENV=1 makes that mandatory)",
                    file=sys.stderr,
                )
            self._client = get_harness_client_for_env(self.env)
        return self._client

    def dry_run_notice(self, what: str) -> bool:
        """With ``--dry-run``: say what would happen and return True (the handler then returns 0)."""
        if self.dry_run:
            print(f"DRY RUN: {what}")
            return True
        return False

    # -- output -------------------------------------------------------------------------
    def log(self, *parts: Any) -> None:
        """Progress and logs always go to stderr, so stdout stays clean for ``--json``."""
        print(*parts, file=sys.stderr)

    def debug(self, *parts: Any) -> None:
        if self.verbose:
            print("[debug]", *parts, file=sys.stderr)
