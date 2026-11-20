"""Command results and exit codes.

Exit codes are the same for every ``abcd`` command:

* 0 done
* 1 refused, or a check or gate failed
* 2 usage error
* 3 platform or network error

A handler returns an ``int`` (legacy: it already printed its output), ``None`` (done) or a
:class:`Result`. Without ``--json`` the result is printed for people; with ``--json`` exactly one
JSON document goes to stdout and everything else (logs, progress) goes to stderr.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_PLATFORM = 3


@dataclass
class Result:
    """What a command produced.

    ``data``: structured payload (the ``data`` field of the ``--json`` document).
    ``text``: human rendering. ``None`` means "print ``data`` as indented JSON"; ``""`` means
    "the handler already printed everything" (moved legacy commands).
    ``message``: one-line reason, used for refusals and errors.
    """

    code: int = EXIT_OK
    data: Any = None
    text: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.code == EXIT_OK

    @classmethod
    def done(cls, data: Any = None, text: str | None = None) -> Result:
        return cls(EXIT_OK, data, text)

    @classmethod
    def refused(cls, message: str, data: Any = None) -> Result:
        return cls(EXIT_REFUSED, data, None, message)

    @classmethod
    def usage(cls, message: str) -> Result:
        return cls(EXIT_USAGE, None, None, message)

    @classmethod
    def platform_error(cls, message: str) -> Result:
        return cls(EXIT_PLATFORM, None, None, message)


def to_result(value: int | Result | None) -> Result:
    """Normalise a handler's return value (an int means "already printed")."""
    if value is None:
        return Result(text="")
    if isinstance(value, Result):
        return value
    return Result(int(value), text="")


def exit_code_for(exc: BaseException) -> int:
    """Map an exception to the shared exit codes."""
    from cli.builder_common.errors import BuilderError

    if isinstance(exc, BuilderError):
        return exc.code
    try:
        from cli.harness_common.api_client import HarnessAPIError

        if isinstance(exc, HarnessAPIError):
            return EXIT_PLATFORM
    except ImportError:
        pass
    try:
        import requests

        if isinstance(exc, requests.RequestException):
            return EXIT_PLATFORM
    except ImportError:
        pass
    try:
        import httpx

        if isinstance(exc, httpx.HTTPError):
            return EXIT_PLATFORM
    except ImportError:
        pass
    if isinstance(exc, ConnectionError | TimeoutError):
        return EXIT_PLATFORM
    return EXIT_REFUSED


def extract_json(text: str) -> Any:
    """Return the first JSON object/array that starts a line in ``text``, else ``None``."""
    decoder = json.JSONDecoder()
    pos = 0
    for line in text.splitlines(keepends=True):
        if line[:1] in ("{", "["):
            try:
                value, _ = decoder.raw_decode(text[pos:])
            except ValueError:
                value = None
            if isinstance(value, dict | list):
                return value
        pos += len(line)
    return None


def build_document(command: str, result: Result, captured: str = "") -> dict[str, Any]:
    """The single ``--json`` document for one command."""
    data = result.data
    if data is None and captured:
        data = extract_json(captured)
    doc: dict[str, Any] = {
        "ok": result.ok,
        "exit_code": result.code,
        "command": command,
        "data": data,
    }
    if captured:
        doc["output"] = captured
    if result.message:
        doc["message" if result.ok else "error"] = result.message
    return doc


def emit(command: str, result: Result, *, as_json: bool, captured: str = "") -> None:
    """Print ``result`` for people, or as one JSON document on stdout."""
    if as_json:
        sys.stdout.write(json.dumps(build_document(command, result, captured), default=str) + "\n")
        sys.stdout.flush()
        return
    if result.text is None:
        if result.data is not None:
            print(json.dumps(result.data, indent=2, default=str))
    elif result.text:
        print(result.text)
    if result.message and not result.ok:
        print(f"Error: {result.message}", file=sys.stderr)
