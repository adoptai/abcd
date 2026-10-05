"""cli/harness_common/secret_scan.py -- content scan before any skill upload."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli.harness_common.secret_scan import scan_bytes, scan_text  # noqa: E402

# Built at runtime so this file itself never contains a matchable token.
GH = "ghp_" + "A1b2C3d4" * 5
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + ".c2lnbmF0dXJlLXNpZ25hdHVyZQ"
)
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
HEX = "9f8e7d6c" + "5b4a39281706"


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f"token = '{GH}'", "GitHub token"),
        (f"aws_key: {AWS}", "AWS access key id"),
        (f"Authorization: Bearer {JWT}", "JSON Web Token"),
        (PEM, "private key"),
        ('client_secret = "s3cr3tValue-9f8e7d6c"', "hard-coded credential"),
        ('"password": "Hunter2Hunter2!"', "hard-coded credential"),
        (f'TABBY_CLIENT_SECRET="{HEX}"', "hard-coded credential"),
        (f"ADOPT_CLIENT_SECRET={HEX}", "hard-coded credential"),
        (f"client_secret: {HEX}", "hard-coded credential"),
        (f"export API_KEY={HEX}", "hard-coded credential"),
        (f'curl -H "Authorization: Bearer {HEX}abcd"', "bearer token"),
    ],
)
def test_detects_credentials(line: str, kind: str) -> None:
    findings = scan_text("x.py", f"ok\n{line}\n")
    assert [(f.line, f.kind) for f in findings] == [(2, kind)]


@pytest.mark.parametrize(
    "line",
    [
        '"api-key": "static_secret_header",',
        "password = '${SECRET:app_password}'",
        'client_secret = "your-client-secret-here"',
        'api_key = "<your api key>"',
        'secret = "TABBY_CLIENT_SECRET_NAME"',
        "token = os.environ['TOKEN']",
        "'{{password}}'",
        'headers["Authorization"] = "${SECRET:SANDBOX_example_bank_api_key}"',
        'auth = f"Bearer {self._bearer_token}"',
        "password = get_password_from_vault()",
        "api_key_header=args.api_key_header",
        "TABBY_CLIENT_SECRET=${TABBY_CLIENT_SECRET}",
    ],
)
def test_ignores_placeholders_and_identifiers(line: str) -> None:
    assert scan_text("x.py", line) == []


def test_finding_never_contains_the_value() -> None:
    (finding,) = scan_text("cfg.json", f'"token": "{GH}"')
    assert GH not in str(finding)
    assert str(finding) == "cfg.json:1: GitHub token"


def test_scans_inside_zip_members() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/clean.py", "x = 1\n")
        zf.writestr("pkg/leak.py", f"\n\nKEY = '{AWS}'\n")
        zf.writestr("img.bin", b"\x00\x01" + AWS.encode())
    findings = scan_bytes("bundle.zip", buf.getvalue())
    assert [str(f) for f in findings] == ["bundle.zip!pkg/leak.py:3: AWS access key id"]


def test_binary_files_are_skipped() -> None:
    assert scan_bytes("a.png", b"\x89PNG\x00\x00" + GH.encode()) == []


def test_oversized_files_are_reported_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    from cli.harness_common import secret_scan

    monkeypatch.setattr(secret_scan, "_MAX_SCAN_BYTES", 10)
    (f,) = scan_bytes("big.txt", b"x" * 11)
    assert f.kind == "unscanned (too large)"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("big.bin", b"y" * 11)
    (f,) = scan_bytes("b.zip", buf.getvalue())
    assert f.path == "b.zip!big.bin" and f.kind == "unscanned (too large)"
    assert scan_bytes("bad.zip", b"not a zip")[0].kind == "unscanned (not a readable zip)"
