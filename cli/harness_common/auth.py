#!/usr/bin/env python3
"""
Harness auth -- exchange a Frontegg Personal Access Token (PAT) for a bearer JWT.

Mirrors cli/auth.py's ADOPT_CLIENT_ID/ADOPT_CLIENT_SECRET pattern, but talks
to adoptwebui's end-user API (POST /v1/users/api-token) instead of the WDL
actions auth endpoint -- agent-harness routes are end-user (Frontegg JWT)
routes, not WDL's ADOPT_API_ENDPOINT ones.

One-time setup (per org, done once by a human in a browser -- this cannot be
automated, it requires an interactive Frontegg login):
    1. Log into the Adopt webui as the account you want this tool to act as.
    2. Create a Personal Access Token (POST /v1/personal-tokens while
       authenticated, or the webui's Personal Access Tokens settings page).
    3. Put the resulting clientId/secret into workspaces/{env}/.env as
       ADOPT_HARNESS_PAT_CLIENT_ID / ADOPT_HARNESS_PAT_SECRET.

After that, every command in this harness/ tree exchanges the PAT for a
fresh bearer JWT automatically via POST /v1/users/api-token, caching it on
disk (per environment) until it's close to expiry -- same UX as abcd's
existing ADOPT_CLIENT_ID/SECRET flow, just a different endpoint.
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Refresh this many seconds before actual expiry, so a long-running command
# doesn't race a request against a token that expires mid-flight.
_EXPIRY_SAFETY_MARGIN_S = 60


def _token_cache_path(env_path: Path) -> Path:
    return env_path / "harness" / ".token_cache.json"


def _read_cached_token(env_path: Path) -> str | None:
    cache_file = _token_cache_path(env_path)
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("expires_at", 0) - _EXPIRY_SAFETY_MARGIN_S > time.time():
        access_token = data.get("access_token")
        if isinstance(access_token, str):
            return access_token
    return None


def _write_cached_token(env_path: Path, access_token: str, expires_in: int) -> None:
    cache_file = _token_cache_path(env_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"access_token": access_token, "expires_at": time.time() + expires_in})
    )


def get_harness_bearer_token(
    client_id: str | None = None,
    secret: str | None = None,
    webui_endpoint: str | None = None,
    env_path: Path | None = None,
) -> str:
    """
    Get a bearer JWT for adoptwebui's end-user agent-harness API.

    Short-circuits on ADOPT_HARNESS_BEARER_TOKEN (paste a token grabbed from
    browser devtools -- useful before PAT setup is done). Otherwise exchanges
    the PAT (client_id/secret) for a fresh JWT via POST /v1/users/api-token,
    cached on disk per environment until close to expiry.

    Raises:
        ValueError: If no credentials are configured, or the exchange fails.
    """
    pre_issued = os.getenv("ADOPT_HARNESS_BEARER_TOKEN")
    if pre_issued:
        return pre_issued

    if env_path is not None:
        cached = _read_cached_token(env_path)
        if cached:
            return cached

    client_id = client_id or os.getenv("ADOPT_HARNESS_PAT_CLIENT_ID")
    secret = secret or os.getenv("ADOPT_HARNESS_PAT_SECRET")
    webui_endpoint = webui_endpoint or os.getenv("ADOPT_WEBUI_ENDPOINT")

    if not client_id:
        raise ValueError(
            "ADOPT_HARNESS_PAT_CLIENT_ID is required. Mint a Personal Access Token in the "
            "Adopt webui once (see cli/harness_common/auth.py docstring), then set it in "
            "workspaces/{env}/.env."
        )
    if not secret:
        raise ValueError(
            "ADOPT_HARNESS_PAT_SECRET is required. Mint a Personal Access Token in the "
            "Adopt webui once, then set it in workspaces/{env}/.env."
        )
    if not webui_endpoint:
        raise ValueError(
            "ADOPT_WEBUI_ENDPOINT is required (the base URL of the Adopt webui backend). "
            "Set it in workspaces/{env}/.env."
        )

    url = f"{webui_endpoint.rstrip('/')}/v1/users/api-token"
    response = requests.post(url, json={"client_id": client_id, "secret": secret}, timeout=30)
    if response.status_code != 200:
        raise ValueError(
            f"Harness PAT exchange failed with status {response.status_code}: {response.text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise ValueError(f"No access_token in PAT exchange response: {data}")

    if env_path is not None:
        _write_cached_token(env_path, access_token, int(data.get("expires_in", 3600)))

    return access_token


def get_harness_bearer_token_for_env(env_name: str | None = None) -> str:
    """
    Get a harness bearer token for the specified (or active) environment.

    Loads workspaces/{env}/.env first, mirroring
    cli.auth.get_bearer_token_for_env's behavior for the WDL client.
    """
    from cli.wdl_common.workspace_manager import (
        DEFAULT_ENV,
        WORKSPACES_DIR,
        get_workspace_manager,
    )

    manager = get_workspace_manager()
    env = env_name or manager.active_env or DEFAULT_ENV
    env_path = WORKSPACES_DIR / env

    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        load_dotenv(env_dotenv, override=True)

    return get_harness_bearer_token(env_path=env_path)
