"""Tests for app.wdl_generator — WDL draft generation."""

import json
import pytest
from app.wdl_generator import (
    generate_wdl,
    detect_dependencies,
    _detect_base_url,
    _collect_form_values,
    _to_var_name,
    _path_to_jq,
    _path_to_variable_name,
    _extract_scalar_values,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _har_entry(url, method="GET", status=200, resp_body=None, req_body=None, timestamp=None):
    """Build a HAR entry for WDL tests."""
    entry = {
        "startedDateTime": timestamp or "2026-02-23T10:00:00.000Z",
        "request": {
            "method": method,
            "url": url,
            "headers": [
                {"name": "Content-Type", "value": "application/json"},
                {"name": "Accept", "value": "application/json"},
            ],
            "queryString": [],
        },
        "response": {
            "status": status,
            "content": {
                "mimeType": "application/json",
                "text": json.dumps(resp_body) if resp_body else "",
            },
        },
    }
    if req_body:
        entry["request"]["postData"] = {
            "mimeType": "application/json",
            "text": json.dumps(req_body) if isinstance(req_body, dict) else req_body,
        }
    return entry


# ── generate_wdl ─────────────────────────────────────────────────────────────

class TestGenerateWdl:
    def test_empty_entries(self):
        result = generate_wdl([])
        assert result["steps"] == []
        assert result["detected_params"] == {}
        assert result["detected_dependencies"] == []

    def test_single_get_step(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
        ]
        result = generate_wdl(entries)
        assert len(result["steps"]) >= 1
        step = result["steps"][0]
        assert step["operation"] == "REST"
        assert step["config"]["method"] == "GET"
        assert "{{base_url}}" in step["config"]["url"]

    def test_base_url_parameterized(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
            _har_entry("https://api.example.com/v1/orders"),
        ]
        result = generate_wdl(entries)
        assert "base_url" in result["detected_params"]
        for step in result["steps"]:
            if step["operation"] == "REST":
                assert step["config"]["url"].startswith("{{base_url}}")

    def test_explicit_base_url(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
        ]
        result = generate_wdl(entries, base_url="https://api.example.com")
        step = result["steps"][0]
        assert step["config"]["url"].startswith("{{base_url}}")

    def test_post_with_body(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/customers",
                method="POST",
                status=201,
                req_body={"name": "Acme Corp", "email": "acme@test.com"},
            ),
        ]
        result = generate_wdl(entries)
        step = result["steps"][0]
        assert step["config"]["method"] == "POST"
        assert "body" in step["config"]

    def test_form_value_parameterization(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/customers",
                method="POST",
                req_body={"name": "Acme Corp"},
            ),
        ]
        click_events = [
            {"event_type": "input", "field_name": "name", "value": "Acme Corp"},
        ]
        result = generate_wdl(entries, click_events=click_events)
        step = result["steps"][0]
        body = step["config"].get("body", {})
        assert "{{name}}" in str(body)
        assert "name" in result["detected_params"]

    def test_dependency_detection(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/customers",
                method="POST",
                status=201,
                resp_body={"id": "cust-12345", "name": "Acme"},
            ),
            _har_entry(
                "https://api.example.com/v1/customers/cust-12345/orders",
                method="GET",
                status=200,
            ),
        ]
        result = generate_wdl(entries)
        assert len(result["detected_dependencies"]) > 0
        dep = result["detected_dependencies"][0]
        assert dep["from_step"] == 0
        assert dep["to_step"] == 1
        assert dep["via"] == "id"

    def test_dependency_substitution_in_url(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/customers",
                method="POST",
                status=201,
                resp_body={"id": "cust-12345"},
            ),
            _har_entry(
                "https://api.example.com/v1/customers/cust-12345",
                method="GET",
            ),
        ]
        result = generate_wdl(entries)
        # The second REST step should have {{id}} instead of "cust-12345"
        rest_steps = [s for s in result["steps"] if s["operation"] == "REST"]
        assert len(rest_steps) == 2
        assert "{{id}}" in rest_steps[1]["config"]["url"]

    def test_jq_filter_steps_inserted(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/customers",
                method="POST",
                resp_body={"id": "cust-12345"},
            ),
            _har_entry(
                "https://api.example.com/v1/customers/cust-12345",
            ),
        ]
        result = generate_wdl(entries)
        jq_steps = [s for s in result["steps"] if s["operation"] == "JQ_FILTER"]
        assert len(jq_steps) >= 1
        assert jq_steps[0]["output"] == "id"

    def test_auth_header_parameterized(self):
        entry = _har_entry("https://api.example.com/v1/data")
        entry["request"]["headers"].append(
            {"name": "Authorization", "value": "Bearer eyJtoken123"}
        )
        result = generate_wdl([entry])
        step = result["steps"][0]
        headers = step["config"].get("headers", {})
        assert headers.get("Authorization") == "Bearer {{access_token}}"
        assert "access_token" in result["detected_params"]

    def test_source_stats(self):
        entries = [_har_entry("https://api.example.com/v1/data")]
        narrations = [{"content": "Test narration", "timestamp": "2026-02-23T10:00:00Z"}]
        clicks = [{"event_type": "click", "timestamp": "2026-02-23T10:00:00Z"}]
        result = generate_wdl(entries, click_events=clicks, narrations=narrations)
        assert result["source_stats"]["har_entries"] == 1
        assert result["source_stats"]["click_events"] == 1
        assert result["source_stats"]["narrations"] == 1

    def test_output_text_from_final_narration(self):
        entries = [_har_entry("https://api.example.com/v1/data")]
        narrations = [
            {"content": "Starting the workflow", "timestamp": "2026-02-23T10:00:00Z"},
            {"content": "The customer was created successfully", "timestamp": "2026-02-23T10:01:00Z"},
        ]
        result = generate_wdl(entries, narrations=narrations)
        output_steps = [s for s in result["steps"] if s["operation"] == "OUTPUT_TEXT"]
        assert len(output_steps) == 1
        assert "customer was created" in output_steps[0]["config"]["template"]


# ── detect_dependencies ──────────────────────────────────────────────────────

class TestDetectDependencies:
    def test_no_deps_single_entry(self):
        entries = [_har_entry("https://api.example.com/v1/data")]
        assert detect_dependencies(entries) == []

    def test_detects_id_dependency(self):
        entries = [
            _har_entry("https://api.example.com/v1/users", resp_body={"id": "user-99999"}),
            _har_entry("https://api.example.com/v1/users/user-99999/profile"),
        ]
        deps = detect_dependencies(entries)
        assert len(deps) == 1
        assert deps[0]["from_entry"] == 0
        assert deps[0]["to_entry"] == 1
        assert deps[0]["value"] == "user-99999"

    def test_ignores_short_values(self):
        entries = [
            _har_entry("https://api.example.com/v1/data", resp_body={"id": "ab"}),
            _har_entry("https://api.example.com/v1/ab/detail"),
        ]
        deps = detect_dependencies(entries)
        assert len(deps) == 0  # "ab" is too short (< 4 chars)

    def test_ignores_common_values(self):
        entries = [
            _har_entry("https://api.example.com/v1/data", resp_body={"status": "success"}),
            _har_entry("https://api.example.com/v1/other?status=success"),
        ]
        deps = detect_dependencies(entries)
        assert len(deps) == 0  # "success" is in _IGNORE_VALUES

    def test_nested_value_detection(self):
        entries = [
            _har_entry("https://api.example.com/v1/create",
                        resp_body={"data": {"customer_id": "cust-55555"}}),
            _har_entry("https://api.example.com/v1/customers/cust-55555"),
        ]
        deps = detect_dependencies(entries)
        assert len(deps) >= 1
        assert deps[0]["value"] == "cust-55555"

    def test_dependency_in_request_body(self):
        entries = [
            _har_entry("https://api.example.com/v1/auth",
                        resp_body={"token": "tok-abcdef12345"}),
            _har_entry("https://api.example.com/v1/data",
                        req_body={"auth_token": "tok-abcdef12345"}),
        ]
        deps = detect_dependencies(entries)
        assert len(deps) >= 1


# ── Helper functions ─────────────────────────────────────────────────────────

class TestDetectBaseUrl:
    def test_most_common_wins(self):
        entries = [
            _har_entry("https://api.a.com/v1/x"),
            _har_entry("https://api.b.com/v1/x"),
            _har_entry("https://api.a.com/v1/y"),
        ]
        assert _detect_base_url(entries) == "https://api.a.com"

    def test_empty(self):
        assert _detect_base_url([]) == ""


class TestCollectFormValues:
    def test_collects_input_events(self):
        clicks = [
            {"event_type": "input", "field_name": "email", "value": "a@b.com"},
            {"event_type": "click"},
            {"event_type": "change", "field_name": "role", "value": "admin"},
        ]
        result = _collect_form_values(clicks)
        assert result == {"email": "a@b.com", "role": "admin"}

    def test_skips_empty_values(self):
        clicks = [{"event_type": "input", "field_name": "name", "value": ""}]
        result = _collect_form_values(clicks)
        assert result == {}


class TestToVarName:
    def test_snake_case(self):
        assert _to_var_name("Customer Name") == "customer_name"
        assert _to_var_name("email-address") == "email_address"
        assert _to_var_name("firstName") == "first_name"

    def test_empty(self):
        assert _to_var_name("") == "param"


class TestPathToJq:
    def test_simple(self):
        assert _path_to_jq("id") == ".id"
        assert _path_to_jq("data.id") == ".data.id"

    def test_already_dotted(self):
        assert _path_to_jq(".id") == ".id"


class TestPathToVariableName:
    def test_last_segment(self):
        assert _path_to_variable_name("data.customer_id") == "customer_id"
        assert _path_to_variable_name("id") == "id"

    def test_array_path(self):
        assert _path_to_variable_name("data[0].id") == "id"


class TestExtractScalarValues:
    def test_flat_dict(self):
        vals = _extract_scalar_values({"id": "123", "name": "Acme"}, "")
        assert ("id", "123") in vals
        assert ("name", "Acme") in vals

    def test_nested_dict(self):
        vals = _extract_scalar_values({"data": {"id": "abc"}}, "")
        assert ("data.id", "abc") in vals

    def test_max_depth(self):
        deep = {"a": {"b": {"c": {"d": "too-deep"}}}}
        vals = _extract_scalar_values(deep, "", max_depth=2)
        # Should not reach d at depth 3+
        paths = [v[0] for v in vals]
        assert "a.b.c.d" not in paths

    def test_list(self):
        vals = _extract_scalar_values({"items": ["x", "y"]}, "")
        assert ("items[0]", "x") in vals
        assert ("items[1]", "y") in vals
