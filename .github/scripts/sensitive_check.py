#!/usr/bin/env python3
"""Sensitive-content gate for this open-source repo.

Fails when tracked files contain:

1. a forbidden file type -- HAR captures, real env files (anything named .env or
   *.env except *.example), private keys/certs;
2. a client or customer identifier from the denylist.

The denylist would itself leak the names if it were stored in plain text, so
`.github/sensitive-denylist.sha256` holds only SHA-256 digests of lower-cased
terms. Each file is tokenized into alphanumeric words plus every adjacent pair
of words joined with "" and "-" ("acme corp" -> "acmecorp", "acme-corp"), and
each candidate is hashed and looked up.

    python .github/scripts/sensitive_check.py                 # scan all tracked files
    python .github/scripts/sensitive_check.py FILE [FILE ...]  # scan these (pre-commit)
    python .github/scripts/sensitive_check.py --add "Acme Corp"  # append a term's digests

Exit 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DENYLIST = ROOT / ".github" / "sensitive-denylist.sha256"
# Never scanned: the denylist itself, the vendored Tabby submodule, lock files.
SKIP_PREFIXES = (".github/sensitive-denylist.sha256", "tabby/", "poetry.lock")
FORBIDDEN = (
    (re.compile(r"\.har$", re.I), "HAR capture"),
    (re.compile(r"(^|/)\.env$|(^|/)[^/]*\.env$|(^|/)\.env\.(?!example$)[^/]+$"), "env file"),
    (re.compile(r"\.(pem|key|p12|pfx)$", re.I), "private key / certificate"),
)
_WORD = re.compile(r"[a-z0-9]+")
_MAX_BYTES = 2 * 1024 * 1024


def digest(term: str) -> str:
    return hashlib.sha256(term.strip().lower().encode()).hexdigest()


def load_denylist() -> set[str]:
    if not DENYLIST.exists():
        return set()
    return {
        line.split()[0]
        for line in DENYLIST.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def candidates(line: str) -> set[str]:
    words = _WORD.findall(line.lower())
    out = set(words)
    for a, b in zip(words, words[1:]):
        out.add(a + b)
        out.add(f"{a}-{b}")
    return out


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    return [f for f in out.split("\0") if f]


def scan(paths: list[str], deny: set[str]) -> list[str]:
    findings: list[str] = []
    for rel in paths:
        if rel.startswith(SKIP_PREFIXES):
            continue
        for pattern, kind in FORBIDDEN:
            if pattern.search(rel):
                findings.append(f"{rel}: forbidden file type ({kind})")
        path = ROOT / rel
        if not deny or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            continue
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(digest(c) in deny for c in candidates(line)):
                findings.append(f"{rel}:{lineno}: client/customer identifier (denylisted)")
    return findings


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("files", nargs="*")
    p.add_argument("--add", metavar="TERM", action="append", help="append a term's digest(s)")
    args = p.parse_args()

    if args.add:
        existing = load_denylist()
        new = []
        for term in args.add:
            words = _WORD.findall(term.lower())
            for variant in {"".join(words), "-".join(words)} if len(words) > 1 else {words[0]}:
                h = digest(variant)
                if h not in existing:
                    new.append(h)
                    existing.add(h)
        with DENYLIST.open("a") as fh:
            fh.writelines(f"{h}\n" for h in new)
        print(f"added {len(new)} digest(s)")
        return 0

    paths = (
        [str(Path(f).resolve().relative_to(ROOT)) for f in args.files]
        if args.files
        else tracked_files()
    )
    findings = scan(paths, load_denylist())
    for f in findings:
        print(f)
    if findings:
        print(
            f"\n{len(findings)} finding(s). This repo is open source: no client names, client "
            "URLs, captures, env files or keys. Replace with neutral examples (acme, example.test).",
            file=sys.stderr,
        )
        return 1
    print(f"sensitive-content check: {len(paths)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
