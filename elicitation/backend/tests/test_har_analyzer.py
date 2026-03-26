"""Tests for app.har_analyzer — HAR filtering, annotation, multi-API detection."""

import pytest
from app.har_analyzer import (
    filter_har_entries,
    annotate_har_entries,
    detect_api_groups,
    _is_api_call,
    _detect_api_prefix,
    _auto_name_api,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _har_entry(url, method="GET", status=200, resp_content_type="application/json", req_body=None):
    """Build a minimal HAR entry dict."""
    entry = {
        "startedDateTime": "2026-02-23T10:00:00.000Z",
        "request": {
            "method": method,
            "url": url,
            "headers": [
                {"name": "Content-Type", "value": "application/json"},
            ],
            "queryString": [],
        },
        "response": {
            "status": status,
            "headers": [],
            "content": {
                "mimeType": resp_content_type,
                "text": "",
            },
        },
    }
    if req_body:
        entry["request"]["postData"] = {"mimeType": "application/json", "text": req_body}
    return entry


def _wrap_har(entries):
    """Wrap entries in a HAR log structure."""
    return {"log": {"entries": entries}}


# ── filter_har_entries ───────────────────────────────────────────────────────

class TestFilterHarEntries:
    def test_empty_har(self):
        assert filter_har_entries({"log": {"entries": []}}) == []
        assert filter_har_entries({}) == []

    def test_keeps_json_api_calls(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 1
        assert result[0]["_original_index"] == 0

    def test_drops_asset_files(self):
        entries = [
            _har_entry("https://example.com/bundle.js", resp_content_type="application/javascript"),
            _har_entry("https://example.com/style.css", resp_content_type="text/css"),
            _har_entry("https://example.com/logo.png", resp_content_type="image/png"),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 0

    def test_drops_noise_urls(self):
        entries = [
            _har_entry("https://www.google-analytics.com/collect"),
            _har_entry("https://api.segment.io/v1/track"),
            _har_entry("https://o123.sentry.io/api/events"),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 0

    def test_drops_options_preflight(self):
        entries = [
            _har_entry("https://api.example.com/v1/users", method="OPTIONS"),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 0

    def test_keeps_post_json(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers", method="POST",
                        status=201, req_body='{"name": "Acme"}'),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 1

    def test_include_patterns_override(self):
        entries = [
            _har_entry("https://example.com/style.css", resp_content_type="text/css"),
            _har_entry("https://example.com/special-endpoint"),
        ]
        result = filter_har_entries(_wrap_har(entries), include_patterns=["special-endpoint"])
        assert len(result) == 1
        assert "special-endpoint" in result[0]["request"]["url"]

    def test_exclude_patterns_override(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
            _har_entry("https://api.example.com/v1/internal"),
        ]
        result = filter_har_entries(_wrap_har(entries), exclude_patterns=["internal"])
        assert len(result) == 1
        assert "customers" in result[0]["request"]["url"]

    def test_original_index_injected(self):
        entries = [
            _har_entry("https://example.com/logo.png", resp_content_type="image/png"),
            _har_entry("https://api.example.com/v1/data"),
            _har_entry("https://example.com/style.css", resp_content_type="text/css"),
            _har_entry("https://api.example.com/v2/items"),
        ]
        result = filter_har_entries(_wrap_har(entries))
        assert len(result) == 2
        assert result[0]["_original_index"] == 1
        assert result[1]["_original_index"] == 3


# ── _is_api_call ─────────────────────────────────────────────────────────────

class TestIsApiCall:
    def test_json_response_is_api(self):
        assert _is_api_call(_har_entry("https://example.com/data", resp_content_type="application/json"))

    def test_html_response_not_api(self):
        assert not _is_api_call(_har_entry("https://example.com/page", resp_content_type="text/html"))

    def test_api_path_is_api(self):
        assert _is_api_call(_har_entry("https://example.com/api/v1/users", resp_content_type=""))

    def test_graphql_is_api(self):
        assert _is_api_call(_har_entry("https://example.com/graphql", method="POST",
                                        resp_content_type="application/json"))

    def test_rest_path_is_api(self):
        assert _is_api_call(_har_entry("https://example.com/rest/data", resp_content_type=""))

    def test_woff_file_not_api(self):
        assert not _is_api_call(_har_entry("https://example.com/font.woff2"))


# ── _detect_api_prefix ───────────────────────────────────────────────────────

class TestDetectApiPrefix:
    def test_api_v1(self):
        assert _detect_api_prefix("/api/v1/users") == "/api/v1"

    def test_api_no_version(self):
        assert _detect_api_prefix("/api/users") == "/api"

    def test_v2(self):
        assert _detect_api_prefix("/v2/customers") == "/v2"

    def test_rest(self):
        assert _detect_api_prefix("/rest/data") == "/rest"

    def test_no_prefix(self):
        assert _detect_api_prefix("/users/123") == ""


# ── _auto_name_api ───────────────────────────────────────────────────────────

class TestAutoNameApi:
    def test_api_subdomain(self):
        assert _auto_name_api("api.crm.example.com") == "crm"

    def test_www_subdomain(self):
        assert _auto_name_api("www.shop.example.com") == "shop"

    def test_localhost(self):
        assert _auto_name_api("localhost:8000") == "localhost"

    def test_simple_domain(self):
        assert _auto_name_api("example.com") == "example"


# ── annotate_har_entries ─────────────────────────────────────────────────────

class TestAnnotateHarEntries:
    def test_adds_step_numbers(self):
        entries = [
            _har_entry("https://api.example.com/v1/a"),
            _har_entry("https://api.example.com/v1/b"),
        ]
        result = annotate_har_entries(entries)
        assert result[0]["_annotations"]["step_number"] == 0
        assert result[1]["_annotations"]["step_number"] == 1

    def test_default_is_primary(self):
        entries = [_har_entry("https://api.example.com/v1/a")]
        result = annotate_har_entries(entries)
        assert result[0]["_annotations"]["is_primary"] is True

    def test_correlates_click_events(self):
        entries = [
            {
                "startedDateTime": "2026-02-23T10:00:02.000Z",
                "request": {"url": "https://api.example.com/v1/submit", "method": "POST"},
                "response": {"status": 200, "content": {"mimeType": "application/json"}},
            },
        ]
        click_events = [
            {
                "id": "click-1",
                "timestamp": "2026-02-23T10:00:01.000Z",
                "event_type": "click",
            },
            {
                "id": "click-2",
                "timestamp": "2026-02-23T10:00:00.500Z",
                "event_type": "input",
                "field_name": "email",
                "value": "test@example.com",
            },
        ]
        result = annotate_har_entries(entries, click_events=click_events)
        ann = result[0]["_annotations"]
        assert "click-1" in ann["related_click_event_ids"]
        assert "click-2" in ann["related_click_event_ids"]
        assert any(p["field_name"] == "email" for p in ann["detected_params"])

    def test_no_clicks_no_crash(self):
        entries = [_har_entry("https://api.example.com/v1/a")]
        result = annotate_har_entries(entries, click_events=None)
        assert result[0]["_annotations"]["related_click_event_ids"] == []

    def test_empty_entries(self):
        assert annotate_har_entries([]) == []


# ── detect_api_groups ────────────────────────────────────────────────────────

class TestDetectApiGroups:
    def test_single_api(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
            _har_entry("https://api.example.com/v1/orders"),
        ]
        groups = detect_api_groups(entries)
        assert len(groups) == 1
        assert groups[0]["entry_count"] == 2
        assert len(groups[0]["endpoints"]) == 2

    def test_multiple_apis(self):
        entries = [
            _har_entry("https://api.crm.com/v1/customers"),
            _har_entry("https://api.billing.com/v2/invoices"),
        ]
        groups = detect_api_groups(entries)
        assert len(groups) == 2

    def test_deduplicates_endpoints(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
            _har_entry("https://api.example.com/v1/customers"),
        ]
        groups = detect_api_groups(entries)
        assert len(groups) == 1
        assert groups[0]["entry_count"] == 2
        assert len(groups[0]["endpoints"]) == 1

    def test_sorted_by_entry_count(self):
        entries = [
            _har_entry("https://api.b.com/data"),
            _har_entry("https://api.a.com/v1/x"),
            _har_entry("https://api.a.com/v1/y"),
            _har_entry("https://api.a.com/v1/z"),
        ]
        groups = detect_api_groups(entries)
        assert groups[0]["entry_count"] >= groups[1]["entry_count"]

    def test_empty_entries(self):
        assert detect_api_groups([]) == []

    def test_tracks_status_codes(self):
        entries = [
            _har_entry("https://api.example.com/v1/items", status=200),
            _har_entry("https://api.example.com/v1/items", method="POST", status=201),
        ]
        groups = detect_api_groups(entries)
        # The GET and POST are different endpoints
        assert len(groups[0]["endpoints"]) == 2
