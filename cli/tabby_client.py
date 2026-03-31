"""
Tabby credential client for abcd test runner enrichment.

Provides live authentication credentials (cookies, headers, CSRF tokens) from a
running Tabby instance so that `abcd test` does not require developers to
manually copy session state out of a browser tab.

Thread safety: `TabbyClient` uses a `threading.Lock` around all token-cache
reads and refreshes so it can safely be shared across `ThreadPoolExecutor`
workers (see `test_runner.py:run_parallel_tests`).
"""

import os
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class _CachedToken:
    token: str
    refresh_at: float  # epoch seconds — refresh when time.time() >= this


class TabbyClient:
    """
    Thin HTTP client for the Tabby credential service.

    Responsibilities:
    - Authenticate as an agent via POST /auth/agent-token → bearer JWT
    - Cache the JWT with auto-refresh (5 min before expiry by default)
    - Fetch a CredentialResponseEnvelope via POST /credentials/request
    - Overlay envelope fields onto an existing security_params dict
    """

    def __init__(self, api_url: str, client_id: str, client_secret: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_lock = threading.Lock()
        self._cached_token: _CachedToken | None = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid bearer JWT, refreshing if necessary."""
        with self._token_lock:
            if self._cached_token and time.time() < self._cached_token.refresh_at:
                return self._cached_token.token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        """Fetch a new JWT from Tabby. Caller must hold _token_lock."""
        resp = requests.post(
            f"{self._api_url}/auth/agent-token",
            json={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        # refresh_before: seconds before expiry to proactively refresh; plan specifies 5 min
        refresh_before = data.get("refresh_before", 300)
        self._cached_token = _CachedToken(
            token=token,
            refresh_at=time.time() + expires_in - refresh_before,
        )
        return token

    # ------------------------------------------------------------------
    # Credential fetch
    # ------------------------------------------------------------------

    def fetch_credentials(
        self, profile_id: str, force_refresh: bool = False
    ) -> dict[str, Any]:
        """
        POST /credentials/request and return the raw CredentialResponseEnvelope
        as a dict.
        """
        token = self._get_token()
        payload: dict[str, Any] = {"profile_id": profile_id}
        if force_refresh:
            payload["force_refresh"] = True
        resp = requests.post(
            f"{self._api_url}/credentials/request",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        # Credentials are nested under the "credentials" key in the envelope
        raw = resp.json()
        return raw.get("credentials", raw)

    # ------------------------------------------------------------------
    # Overlay
    # ------------------------------------------------------------------

    @staticmethod
    def overlay_security_params(
        envelope: dict[str, Any],
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Merge Tabby auth fields from a CredentialResponseEnvelope into
        existing security_params.

        Mapping (from plan):
          cookies[]        → "Cookie": "name=val; name2=val2"
          headers[]        → each header.name: header.value
          csrf.header_name → csrf.header_name: csrf.token

        Non-auth keys already in existing (e.g. user_org_id, user_email) are
        preserved — this is an overlay, not a replacement.
        """
        result = dict(existing)

        cookies = envelope.get("cookies") or []
        if cookies:
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}"
                for c in cookies
                if c.get("name") is not None and c.get("value") is not None
            )
            if cookie_str:
                result["Cookie"] = cookie_str

        for header in envelope.get("headers") or []:
            name = header.get("name")
            value = header.get("value")
            if name and value is not None:
                result[name] = value

        csrf = envelope.get("csrf") or {}
        header_name = csrf.get("header_name")
        token_value = csrf.get("token")
        if header_name and token_value:
            result[header_name] = token_value

        return result

    # ------------------------------------------------------------------
    # Profile enrichment
    # ------------------------------------------------------------------

    def enrich_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """
        Shallow-copy profile and overlay Tabby credentials wherever a
        tabby_profile_id is present (top level and per-profiles_map entry).
        The original dict is never mutated.
        """
        profile = dict(profile)

        tabby_profile_id = profile.get("tabby_profile_id")
        if tabby_profile_id:
            envelope = self.fetch_credentials(tabby_profile_id)
            profile["security_params"] = self.overlay_security_params(
                envelope, profile.get("security_params") or {}
            )

        profiles_map = profile.get("profiles_map")
        if profiles_map:
            enriched_map: dict[str, Any] = {}
            for key, entry in profiles_map.items():
                if isinstance(entry, dict) and entry.get("tabby_profile_id"):
                    entry = dict(entry)
                    envelope = self.fetch_credentials(entry["tabby_profile_id"])
                    entry["security_params"] = self.overlay_security_params(
                        envelope, entry.get("security_params") or {}
                    )
                enriched_map[key] = entry
            profile["profiles_map"] = enriched_map

        return profile


# ---------------------------------------------------------------------------
# Module-level singleton and public helper
# ---------------------------------------------------------------------------

_singleton_lock = threading.Lock()
_singleton: TabbyClient | None = None


def _has_real_fallback(security_params: dict[str, Any] | None) -> bool:
    """
    Return True when security_params contains at least one non-empty string
    value — i.e. a real credential rather than an empty dict or a placeholder
    like {"type": "cookie", "note": "..."}.
    """
    if not security_params:
        return False
    return any(isinstance(v, str) and v for v in security_params.values())


def get_tabby_client() -> TabbyClient | None:
    """
    Return the module-level TabbyClient singleton if all three Tabby env vars
    are set, otherwise None.  Thread-safe.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        api_url = os.environ.get("TABBY_API_URL", "").strip()
        client_id = os.environ.get("TABBY_CLIENT_ID", "").strip()
        client_secret = os.environ.get("TABBY_CLIENT_SECRET", "").strip()
        if not (api_url and client_id and client_secret):
            return None
        _singleton = TabbyClient(api_url, client_id, client_secret)
        return _singleton


def enrich_profile_with_tabby(profile: dict[str, Any]) -> dict[str, Any]:
    """
    Enrich a resolved adopt_profile dict with live credentials from Tabby.

    Behaviour:
    - No tabby_profile_id anywhere in the profile → returned unchanged.
    - Tabby env vars missing + real static fallback present → warning, unchanged.
    - Tabby env vars missing + no real static fallback → RuntimeError (fail hard).
    - Tabby reachable → credentials fetched and overlaid.
    - Tabby unreachable + real static fallback present → warning, unchanged.
    - Tabby unreachable + no real static fallback → RuntimeError (fail hard).
    """
    needs_tabby = bool(profile.get("tabby_profile_id")) or any(
        isinstance(e, dict) and e.get("tabby_profile_id")
        for e in (profile.get("profiles_map") or {}).values()
    )
    if not needs_tabby:
        return profile

    client = get_tabby_client()

    if client is None:
        msg = (
            "tabby_profile_id is set but Tabby is not configured "
            "(TABBY_API_URL, TABBY_CLIENT_ID, and TABBY_CLIENT_SECRET must all be set)."
        )
        if _has_real_fallback(profile.get("security_params")):
            warnings.warn(msg + " Falling back to static security_params.", stacklevel=2)
            return profile
        raise RuntimeError(
            msg + " Add a static security_params fallback or set the Tabby env vars."
        )

    try:
        return client.enrich_profile(profile)
    except Exception as exc:
        msg = f"Tabby credential fetch failed: {exc}."
        if _has_real_fallback(profile.get("security_params")):
            warnings.warn(msg + " Falling back to static security_params.", stacklevel=2)
            return profile
        raise RuntimeError(
            msg + " No usable static security_params fallback is available."
        ) from exc
