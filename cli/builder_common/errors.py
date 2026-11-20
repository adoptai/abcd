"""Errors raised by Builder code. ``code`` is the CLI exit code (PLAN.md §17)."""

from __future__ import annotations


class BuilderError(Exception):
    """Base error. Exit code 1 unless a subclass says otherwise."""

    code: int = 1


class RefusedError(BuilderError):
    """A gate, lock, check or rule refused the operation."""

    code = 1


class UsageError(BuilderError):
    """Bad arguments or a workspace in the wrong state."""

    code = 2


class PlatformError(BuilderError):
    """The platform or network failed."""

    code = 3
