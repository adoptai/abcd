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
