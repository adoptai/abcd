"""Tests for app.auth_detector — authentication pattern detection."""

import pytest
from app.auth_detector import detect_auth_patterns


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _entry_with_headers(headers: dict, query_params: list | None = None):
    """Build a HAR entry with specific request headers."""
    header_list = [{"name": k, "value": v} for k, v in headers.items()]
    entry = {
        "request": {
            "method": "GET",
            "url": "https://api.example.com/v1/data",
            "headers": header_list,
            "queryString": query_params or [],
        },
        "response": {
            "status": 200,
            "content": {"mimeType": "application/json"},
        },
    }
    return entry


# ── Tests ────────────────────────────────────────────────────────────────────

class TestDetectAuthPatterns:
    def test_empty_entries(self):
        result = detect_auth_patterns([])
        assert result["primary_auth"] == "none"
        assert result["security_params"] == {}
        assert result["details"]["entries_analyzed"] == 0

    def test_bearer_token(self):
        entries = [
            _entry_with_headers({"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test"}),
            _entry_with_headers({"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "bearer"
        assert result["details"]["bearer_token_seen"] is True
        assert result["security_params"]["type"] == "bearer"
        assert result["security_params"]["token_env_var"] == "API_ACCESS_TOKEN"

    def test_basic_auth(self):
        entries = [
            _entry_with_headers({"Authorization": "Basic dXNlcjpwYXNz"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "basic"
        assert result["details"]["basic_auth_seen"] is True
        assert result["security_params"]["type"] == "basic"
        assert "username_env_var" in result["security_params"]

    def test_api_key_header(self):
        entries = [
            _entry_with_headers({"X-API-Key": "sk-abc123def456"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "api_key"
        assert result["security_params"]["type"] == "api_key"
        assert result["security_params"]["location"] == "header"

    def test_api_key_query_string(self):
        entries = [
            _entry_with_headers(
                {},
                query_params=[{"name": "api_key", "value": "sk-abc123"}],
            ),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "api_key"
        assert result["security_params"]["location"] == "query"

    def test_cookie_auth(self):
        entries = [
            _entry_with_headers({"Cookie": "session_id=abc123; other=val"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "cookie"
        assert result["details"]["cookie_auth_seen"] is True
        assert result["security_params"]["type"] == "cookie"

    def test_bearer_wins_over_cookie(self):
        """Bearer should take priority even if cookies are also present."""
        entries = [
            _entry_with_headers({
                "Authorization": "Bearer token123",
                "Cookie": "session_id=abc",
            }),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "bearer"

    def test_no_auth(self):
        entries = [
            _entry_with_headers({"Content-Type": "application/json", "Accept": "*/*"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "none"
        assert result["security_params"] == {}

    def test_entries_with_auth_count(self):
        entries = [
            _entry_with_headers({"Authorization": "Bearer tok1"}),
            _entry_with_headers({}),
            _entry_with_headers({"Authorization": "Bearer tok2"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["details"]["entries_analyzed"] == 3
        assert result["details"]["entries_with_auth"] == 2

    def test_x_auth_token_header(self):
        entries = [
            _entry_with_headers({"x-auth-token": "mytoken123"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "api_key"

    def test_cookie_without_session_keyword_ignored(self):
        """Cookies without session-like keywords shouldn't trigger cookie auth."""
        entries = [
            _entry_with_headers({"Cookie": "theme=dark; language=en"}),
        ]
        result = detect_auth_patterns(entries)
        assert result["primary_auth"] == "none"
