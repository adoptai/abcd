"""Authentication pattern detection from HAR data.

Analyzes HAR request headers to infer authentication schemes and produce
ABCD-compatible ``security_params`` configuration.
"""

from __future__ import annotations

import re
from collections import Counter


# ── Common API-key header names ──────────────────────────────────────────────

_API_KEY_HEADERS = frozenset({
    "x-api-key", "apikey", "api-key", "x-auth-token",
    "x-access-token", "x-token",
})

_API_KEY_QUERY_NAMES = frozenset({
    "api_key", "apikey", "key", "access_token", "token", "auth",
})

# Headers to ignore when looking for auth (standard non-auth headers)
_IGNORE_HEADERS = frozenset({
    "content-type", "accept", "user-agent", "host", "origin",
    "referer", "accept-encoding", "accept-language", "connection",
    "cache-control", "pragma", "sec-fetch-dest", "sec-fetch-mode",
    "sec-fetch-site", "sec-ch-ua", "sec-ch-ua-mobile",
    "sec-ch-ua-platform", "dnt", "upgrade-insecure-requests",
    "if-none-match", "if-modified-since", "content-length",
})


def detect_auth_patterns(har_entries: list[dict]) -> dict:
    """Analyze filtered HAR entries to detect authentication patterns.

    Args:
        har_entries: List of HAR entry dicts (filtered to API calls).

    Returns:
        {
            "primary_auth": "bearer"|"api_key"|"basic"|"cookie"|"none",
            "security_params": {<ABCD-compatible>},
            "details": {
                "bearer_token_seen": bool,
                "basic_auth_seen": bool,
                "api_key_header": str|None,
                "api_key_query": str|None,
                "cookie_auth_seen": bool,
                "oauth2_detected": bool,
                "entries_analyzed": int,
                "entries_with_auth": int,
            },
        }
    """
    if not har_entries:
        return {
            "primary_auth": "none",
            "security_params": {},
            "details": _empty_details(),
        }

    details = _empty_details()
    details["entries_analyzed"] = len(har_entries)

    auth_type_counts: Counter = Counter()

    for entry in har_entries:
        request = entry.get("request", {})
        headers = {
            h["name"].lower(): h["value"]
            for h in request.get("headers", [])
            if isinstance(h, dict) and "name" in h and "value" in h
        }

        # Authorization header
        auth_header = headers.get("authorization", "")
        if auth_header:
            details["entries_with_auth"] += 1

            if auth_header.lower().startswith("bearer "):
                details["bearer_token_seen"] = True
                auth_type_counts["bearer"] += 1
            elif auth_header.lower().startswith("basic "):
                details["basic_auth_seen"] = True
                auth_type_counts["basic"] += 1

        # Custom API-key headers
        for hdr_name in _API_KEY_HEADERS:
            if hdr_name in headers:
                details["api_key_header"] = _find_original_case(
                    request.get("headers", []), hdr_name
                )
                details["entries_with_auth"] += 1
                auth_type_counts["api_key"] += 1
                break

        # Query string API keys
        for param in request.get("queryString", []):
            param_name = param.get("name", "").lower()
            if param_name in _API_KEY_QUERY_NAMES:
                details["api_key_query"] = param.get("name", "")
                details["entries_with_auth"] += 1
                auth_type_counts["api_key_query"] += 1
                break

        # Cookie-based auth
        if "cookie" in headers and not auth_header:
            cookie_val = headers["cookie"]
            # Session-like cookies suggest cookie auth
            if any(
                tok in cookie_val.lower()
                for tok in ("session", "sid", "auth", "token", "jwt")
            ):
                details["cookie_auth_seen"] = True
                auth_type_counts["cookie"] += 1

    # Determine primary auth type
    primary = _determine_primary(auth_type_counts, details)
    security_params = _build_security_params(primary, details)

    return {
        "primary_auth": primary,
        "security_params": security_params,
        "details": details,
    }


def _empty_details() -> dict:
    return {
        "bearer_token_seen": False,
        "basic_auth_seen": False,
        "api_key_header": None,
        "api_key_query": None,
        "cookie_auth_seen": False,
        "oauth2_detected": False,
        "entries_analyzed": 0,
        "entries_with_auth": 0,
    }


def _find_original_case(headers: list[dict], lower_name: str) -> str:
    """Find the original-case header name from a HAR headers list."""
    for h in headers:
        if h.get("name", "").lower() == lower_name:
            return h["name"]
    return lower_name


def _determine_primary(counts: Counter, details: dict) -> str:
    """Pick the most likely primary auth mechanism."""
    if not counts:
        return "none"

    # Bearer wins if present (most common modern API auth)
    if counts.get("bearer", 0) > 0:
        return "bearer"
    if counts.get("api_key", 0) > 0:
        return "api_key"
    if counts.get("api_key_query", 0) > 0:
        return "api_key"
    if counts.get("basic", 0) > 0:
        return "basic"
    if counts.get("cookie", 0) > 0:
        return "cookie"
    return "none"


def _build_security_params(primary: str, details: dict) -> dict:
    """Build ABCD-compatible security_params from detection results."""
    if primary == "bearer":
        return {
            "type": "bearer",
            "token_env_var": "API_ACCESS_TOKEN",
        }

    if primary == "api_key":
        if details["api_key_header"]:
            return {
                "type": "api_key",
                "location": "header",
                "name": details["api_key_header"],
                "key_env_var": "API_KEY",
            }
        if details["api_key_query"]:
            return {
                "type": "api_key",
                "location": "query",
                "name": details["api_key_query"],
                "key_env_var": "API_KEY",
            }

    if primary == "basic":
        return {
            "type": "basic",
            "username_env_var": "API_USERNAME",
            "password_env_var": "API_PASSWORD",
        }

    if primary == "cookie":
        return {
            "type": "cookie",
            "note": "Session-based auth detected — manual credential setup required",
        }

    return {}
