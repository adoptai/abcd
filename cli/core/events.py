"""Command events: one JSONL line per ``abcd`` command, written once around every command.

``workspaces/<env>/.builder/events.jsonl`` is the local sink; it is written only when the env
directory already exists, and recording never fails (or slows down) the command. Other sinks
(for example the ``bld_dev_events`` table writer) attach with :func:`register_sink`.
Secrets are masked by argument name and by token shape before anything is stored.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.output import Result

MASK = "***"
_SECRET_KEY = re.compile(
    r"token|secret|password|passwd|api[-_]?key|authorization|credential|bearer|cookie|"
    r"(^|[-_])(pat|key)($|[-_])",
    re.I,
)
_SECRET_SHAPE = re.compile(
    r"eyJ[A-Za-z0-9_-]{8,4096}+\.[A-Za-z0-9_-]{8,4096}+\.[A-Za-z0-9_-]{0,4096}+"  # JWT
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub
    r"|sk-[A-Za-z0-9_-]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|Bearer[ \t]{1,16}[A-Za-z0-9._~+/=-]{12,}"
)
# ``Authorization: Basic|Bearer|Token|Digest <value>``: the whole value, scheme included.
_AUTH_HEADER = re.compile(
    r"(?P<pre>authorization[ \t]{0,8}[:=][ \t]{0,8})"
    r"(?:basic|bearer|token|digest)[ \t]{1,16}[^\s\"'\\]{1,8192}",
    re.I,
)
# ``scheme://user:pass@host``: the ``user:pass`` part (a user alone is not a secret).
_URL_USERINFO = re.compile(
    r"(?P<pre>[A-Za-z][A-Za-z0-9+.-]{0,15}://)[^\s/@:\"'\\]{0,128}:[^\s/@\"'\\]{1,256}@"
)
# ``KEY=value``, ``"key": "value"`` and ``key: value`` (quoted, or JSON-escaped inside a JSONL
# transcript line): only the value is masked, and only when the key is secret-named
# (``_SECRET_KEY``). The scan is linear on any line length: a key starts at a word boundary and
# is at most 64 characters, quoted values are possessive, a bare value is one negated class
# (no nested quantifiers) and there is no cap on a value's length. A bare value ends at
# whitespace, a delimiter or a literal ``\n`` escape; after ``key: `` in YAML/env/header text
# (not JSON) it runs to the end of the line (``password: multi word value``). Values may be
# ``$(...)``, ``${...}`` or backticks or quoted with escaped inner quotes.
_PAIR = re.compile(
    r"""(?<![A-Za-z0-9_.-])(?P<key>\\?["']?[A-Za-z0-9_.-]{1,64}\\?["']?)"""
    r"""(?P<sep>[ \t]{0,32}[:=][ \t]{0,32})"""
    r"""(?P<val>\\"(?:(?!\\").)*+\\"|"(?:\\.|[^"\\\n])*+"|'(?:\\.|[^'\\\n])*+'"""
    r"""|\$\((?:[^()\n]|\([^()\n]{0,500}\)){0,2000}+\)|\$\{[^}\n]{0,500}\}|`[^`\n]{0,2000}`"""
    r"""|(?:(?!\\[nrt])[^\s,;&}\]"'\\])++)"""
)
_TO_EOL = re.compile(r"""(?:(?!\\[nrt])[^\n"\\])*+""")
_MAX_STR = 500
_INTERNAL_ARGS = {"_spec", "_command", "_subcommand"}

Sink = Callable[[dict[str, Any]], None]
_sinks: list[Sink] = []


def register_sink(fn: Sink) -> Sink:
    """Receive every event dict (after masking). A failing sink is ignored."""
    if fn not in _sinks:
        _sinks.append(fn)
    return fn


def clear_sinks() -> None:
    _sinks.clear()


def mask(value: Any, key: str = "") -> Any:
    """Mask secrets in ``value`` by the name of its key and by the shape of strings."""
    if key and _SECRET_KEY.search(key) and value not in (None, False, True, ""):
        return MASK
    if isinstance(value, dict):
        return {str(k): mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return _mask_argv([mask(v, key) for v in value])
    if isinstance(value, str):
        value = _SECRET_SHAPE.sub(MASK, value)
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "..."
    if isinstance(value, int | float | bool) or value is None:
        return value
    return mask(str(value), key)


def _mask_pairs(s: str, json_like: bool | None = None) -> str:
    """Mask the value of every secret-named ``key<sep>value`` pair in ``s`` (one line)."""
    if json_like is None:
        json_like = s.lstrip()[:1] in ("{", "[")
    out: list[str] = []
    pos = 0
    while True:
        m = _PAIR.search(s, pos)
        if m is None:
            break
        out.append(s[pos : m.start()])
        key, sep, val = m.group("key"), m.group("sep"), m.group("val")
        quote = next((q for q in ('\\"', '"', "'") if val.startswith(q) and len(val) > 1), "")
        bare = val[len(quote) : len(val) - len(quote)] if quote else val
        pos = m.end()
        if not bare or bare == MASK:
            out.append(m.group(0))
        elif not _SECRET_KEY.search(key.strip("\\\"'")):
            # a harmless key whose value holds more pairs (a whole dump inside a JSON string)
            out.append(f"{key}{sep}{_mask_pairs(val, json_like)}")
        elif (
            not quote
            and ":" in sep
            and not json_like
            and not key.startswith(('"', "'", "\\"))
            and not val.startswith(("$", "`"))
        ):
            # ``password: multi word value`` (YAML/env/header, not a JSON value): to the end of
            # the line (or of the quoted string)
            tail = _TO_EOL.match(s, m.start("val"))
            pos = tail.end() if tail else pos
            out.append(f"{key}{sep}{MASK}")
        elif not quote and key.startswith(('"', "\\")) and not val.startswith(("$", "`")):
            out.append(f'{key}{sep}"{MASK}"')  # a bare JSON number/true/false/null stays valid JSON
        else:
            out.append(f"{key}{sep}{quote}{MASK}{quote}")
    out.append(s[pos:])
    return "".join(out)


def _mask_line(line: str) -> str:
    line = _SECRET_SHAPE.sub(MASK, line)
    line = _AUTH_HEADER.sub(lambda m: f"{m.group('pre')}{MASK}", line)
    line = _URL_USERINFO.sub(lambda m: f"{m.group('pre')}{MASK}@", line)
    return _mask_pairs(line)


def mask_text(text: str) -> str:
    """Mask secrets in free text (a transcript, a ``.env`` dump, a JSON config), line by line in
    linear time: token shapes, ``Authorization: <scheme> <value>``, ``scheme://user:pass@host``,
    and the value of every ``KEY=value`` / ``"key": "value"`` / ``key: value`` whose key is
    secret-named (``SECRET=$(...)``, quoted values with escaped quotes and ``password: a b c``
    included). Every line is scanned whatever its length. Idempotent."""
    return "\n".join(_mask_line(line) for line in text.split("\n"))


def _mask_argv(items: list[Any]) -> list[Any]:
    """Mask argv-style secrets: ``--secret VALUE`` and ``--secret=VALUE``."""
    out: list[Any] = []
    hide_next = False
    for item in items:
        if hide_next:
            out.append(MASK)
            hide_next = False
            continue
        if isinstance(item, str) and item.startswith("-"):
            flag, eq, _ = item.partition("=")
            if _SECRET_KEY.search(flag.lstrip("-")):
                if eq:
                    out.append(f"{flag}={MASK}")
                    continue
                hide_next = True
        out.append(item)
    return out


def _mode() -> str:
    return "cloud" if os.environ.get("ADOPT_CONVERSATION_ID") else "local"


@dataclass
class Tracker:
    """Filled in by the executor; read when the tracked block exits."""

    result: Result | None = None


def build_event(ctx: Context, tracker: Tracker, duration_ms: int) -> dict[str, Any]:
    result = tracker.result
    code = result.code if result is not None else 1
    args = {k: v for k, v in vars(ctx.args).items() if k not in _INTERNAL_ARGS}
    event: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": ctx.spec.name,
        "env": ctx.event_env or "",
        "mode": _mode(),
        "ok": code == 0,
        "exit_code": code,
        "duration_ms": duration_ms,
        "args": mask(args),
    }
    conversation = os.environ.get("ADOPT_CONVERSATION_ID")
    if conversation:
        event["conversation_id"] = conversation
    if result is not None and result.message:
        event["message"] = mask(result.message)
    return event


def write_event(ctx: Context, event: dict[str, Any]) -> None:
    target_env = ctx.event_env
    if not target_env:
        return
    env_dir = ctx.workspaces_dir / target_env
    if not env_dir.is_dir():
        return
    target = env_dir / ".builder"
    target.mkdir(parents=True, exist_ok=True)
    with open(target / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


@contextmanager
def track(ctx: Context) -> Iterator[Tracker]:
    """Wrap one command; on exit record its event. Never raises."""
    tracker = Tracker()
    started = time.monotonic()
    try:
        yield tracker
    finally:
        try:
            event = build_event(ctx, tracker, int((time.monotonic() - started) * 1000))
            try:
                write_event(ctx, event)
            except Exception:
                pass
            for sink in list(_sinks):
                try:
                    sink(event)
                except Exception:
                    pass
        except Exception:
            pass
