"""The Tabby API listens on 8000 by default (tabby packages/shared PORTS.API).

noui_core.auth used to default to 8080 while noui_core.config defaulted to
8000, so an unset TABBY_API_URL pointed the two halves of the toolkit at
different ports. Pin them to one value.
"""

from __future__ import annotations

import importlib


def test_auth_and_config_agree_on_default_tabby_port(monkeypatch):
    monkeypatch.delenv("TABBY_API_URL", raising=False)
    import noui_core.auth as auth
    import noui_core.config as config

    auth = importlib.reload(auth)
    defaults = config.Settings()
    assert auth.TABBY_API_HOST == "http://localhost:8000"
    assert defaults.tabby_api_host == "http://localhost:8000"


def test_ignore_dotenv_skips_bundle_and_cwd_env_files(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    (tmp_path / ".env").write_text("ADOPT_CLIENT_ID=from-cwd-dotenv\n")
    noui_root = Path(__file__).resolve().parents[2] / "cli" / "noui"
    code = "import os, noui_core.config; print(os.environ.get('ADOPT_CLIENT_ID', '-'))"
    base = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(noui_root)}

    def run(extra):
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={**base, **extra},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert run({}) == "from-cwd-dotenv"
    assert run({"NOUI_IGNORE_DOTENV": "1"}) == "-"
