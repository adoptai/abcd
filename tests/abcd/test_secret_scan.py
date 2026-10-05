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


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f"token = '{GH}'", "GitHub token"),
        (f"aws_key: {AWS}", "AWS access key id"),
        (f"Authorization: Bearer {JWT}", "JSON Web Token"),
        (PEM, "private key"),
        ('client_secret = "s3cr3tValue-9f8e7d6c"', "hard-coded credential"),
        ('"password": "Hunter2Hunter2!"', "hard-coded credential"),
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
