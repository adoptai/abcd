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


def test_generated_runtime_stops_at_an_abcd_workspace_root(tmp_path):
    """A compiled MCP/skill runtime under workspaces/<env>/harness/... must not
    load the workspace .env (WDL creds under NoUI's PAT names)."""
    import importlib.util
    import os

    from noui_core.activate.auth_adapter import generate_auth_adapter
    from noui_core.activate.execute_adapter import generate_execute_adapter

    ws = tmp_path / "workspaces" / "acme-dev"
    runtime = ws / "harness" / "mcp_servers" / "app" / "srv" / "noui_runtime"
    runtime.mkdir(parents=True)
    (ws / "env.json").write_text("{}")
    (ws / ".env").write_text("ADOPT_CLIENT_ID=wdl\n")
    (runtime / "auth.py").write_text(generate_auth_adapter("http://localhost:8000"))
    (runtime / "execute.py").write_text(generate_execute_adapter())

    old = os.environ.pop("NOUI_ENV_FILE", None)
    try:
        for name in ("auth", "execute"):
            spec = importlib.util.spec_from_file_location(f"_rt_{name}", runtime / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            found = mod._find_env_file()
            assert found is None or "acme-dev/.env" not in str(found), (name, found)
        # A .env inside the harness tree (below the workspace root) is still found.
        (ws / "harness" / ".env").write_text("TABBY_API_URL=http://t\n")
        spec = importlib.util.spec_from_file_location("_rt_auth2", runtime / "auth.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert str(mod._find_env_file()).endswith("harness/.env")
    finally:
        if old is not None:
            os.environ["NOUI_ENV_FILE"] = old
