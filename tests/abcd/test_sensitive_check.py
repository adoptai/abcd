""".github/scripts/sensitive_check.py -- hashed client denylist + forbidden files."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sensitive_check", ROOT / ".github" / "scripts" / "sensitive_check.py"
)
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def test_multi_word_terms_match_as_runs() -> None:
    deny = {sc.digest("acmebigcorp"), sc.digest("acme-big-corp")}
    assert any(sc.digest(c) in deny for c in sc.candidates("We work with Acme Big Corp daily"))
    assert any(sc.digest(c) in deny for c in sc.candidates("host acme-big-corp.example"))
    assert not any(sc.digest(c) in deny for c in sc.candidates("acme corp big"))


def test_forbidden_file_types(tmp_path: Path) -> None:
    findings = sc.scan(
        [
            "capture.har",
            "dev.env",
            "a/.env",
            "x/.env.local",
            "k.pem",
            "dev.env.example",
            ".env.example",
        ],
        set(),
    )
    flagged = {f.split(":")[0] for f in findings}
    assert flagged == {"capture.har", "dev.env", "a/.env", "x/.env.local", "k.pem"}


def test_repository_is_clean() -> None:
    assert sc.scan(sc.tracked_files(), sc.load_denylist()) == []
