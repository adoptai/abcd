#!/usr/bin/env python3
"""
Content-based secret scan for anything about to leave the machine as a skill.

harness_skill.py already refuses to stage files by NAME (.env*, *.pem, dev_only/,
tests/). That stops the incident it was written for -- a dev_only/.env.graph
holding a live private key -- but not a key pasted into SKILL.md, a token baked
into a script, or a credential inside a zipped aux bundle. This scans the
CONTENT of every file that would be uploaded, including the members of any
.zip aux file, before the PAT is even exchanged.

Findings report path, line and kind -- never the matched value.
"""

import io
import re
import zipfile
from dataclasses import dataclass

# (kind, pattern). Specific token shapes first; the generic assignment rule last.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY"),
    ),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b")),
    (
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "hard-coded credential",
        re.compile(
            r"""(?ix)
            \b(?:password|passwd|secret|client_secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b
            ["']?\s*[:=]\s*["']([^"'\s]{12,})["']
            """
        ),
    ),
)

# Values that are obviously not secrets: placeholders and templating.
_PLACEHOLDER = re.compile(
    r"""(?ix)
    ^(?:\$\{[^}]*\}|\{\{[^}]*\}\}|<[^>]*>|your[-_].*|.*[-_]here|x{6,}|\*{6,}|changeme.*|
    example.*|dummy.*|fake.*|test.*|redacted.*|placeholder.*|replace[-_]?me.*)$
    """
)

# Identifier-shaped values (snake/kebab/dotted words, ENV_VAR names) are names,
# not secrets: e.g. {"api-key": "static_secret_header"} in an enum mapping.
_IDENTIFIER = re.compile(r"^(?:[a-z][a-z_.-]*|[A-Z][A-Z0-9_]*)$")

_MAX_SCAN_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            value = m.group(1) if m.groups() else m.group(0)
            if kind == "hard-coded credential" and (
                _PLACEHOLDER.match(value) or _IDENTIFIER.match(value)
            ):
                continue
            findings.append(Finding(path, lineno, kind))
            break  # one finding per line is enough to stop an upload
    return findings


def _decode(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None  # binary
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def scan_bytes(path: str, data: bytes) -> list[Finding]:
    """Scan one file's bytes; descends into .zip archives."""
    if path.lower().endswith(".zip"):
        findings: list[Finding] = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > _MAX_SCAN_BYTES:
                        continue
                    findings.extend(scan_bytes(f"{path}!{info.filename}", zf.read(info)))
        except zipfile.BadZipFile:
            pass
        return findings
    if len(data) > _MAX_SCAN_BYTES:
        return []
    text = _decode(data)
    return scan_text(path, text) if text is not None else []
