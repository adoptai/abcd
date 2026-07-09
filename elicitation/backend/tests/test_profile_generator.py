"""Tests for app.profile_generator — adopt_profile.json generation."""

import pytest
from app.profile_generator import (
    generate_adopt_profile,
    _extract_workflow_params,
    _to_variable_name,
    _input_type_to_param_type,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _har_entry(url, method="GET", status=200, headers=None):
    """Build a minimal HAR entry."""
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": headers or [{"name": "Content-Type", "value": "application/json"}],
            "queryString": [],
        },
        "response": {
            "status": status,
            "content": {"mimeType": "application/json"},
        },
    }


# ── generate_adopt_profile ───────────────────────────────────────────────────

class TestGenerateAdoptProfile:
    def test_basic_profile(self):
        entries = [
            _har_entry("https://api.example.com/v1/customers"),
        ]
        result = generate_adopt_profile("https://api.example.com", entries)
        assert "base_url" in result
        assert "security_params" in result
        assert "workflow_params" in result
        assert "profiles_map" in result

    def test_base_url_from_har(self):
        """Most common HAR base URL should be used."""
        entries = [
            _har_entry("https://api.example.com/v1/a"),
            _har_entry("https://api.example.com/v1/b"),
        ]
        result = generate_adopt_profile("https://fallback.com", entries)
        assert "api.example.com" in result["base_url"]

    def test_workflow_params_from_clicks(self):
        entries = [_har_entry("https://api.example.com/v1/data")]
        clicks = [
            {"event_type": "input", "field_name": "Customer Name", "value": "Acme", "input_type": "text"},
            {"event_type": "change", "field_name": "quantity", "value": "10", "input_type": "number"},
        ]
        result = generate_adopt_profile("https://api.example.com", entries, click_events=clicks)
        params = result["workflow_params"]
        assert "customer_name" in params
        assert params["customer_name"]["example"] == "Acme"
        assert "quantity" in params
        assert params["quantity"]["type"] == "number"

    def test_auth_detection_integrated(self):
        entries = [
            _har_entry(
                "https://api.example.com/v1/data",
                headers=[
                    {"name": "Authorization", "value": "Bearer eyJtoken"},
                    {"name": "Content-Type", "value": "application/json"},
                ],
            ),
        ]
        result = generate_adopt_profile("https://api.example.com", entries)
        assert result["security_params"]["type"] == "bearer"

    def test_multi_api_profiles_map(self):
        entries = [
            _har_entry("https://api.crm.com/v1/customers"),
            _har_entry("https://api.billing.com/v2/invoices"),
        ]
        result = generate_adopt_profile("https://api.crm.com", entries)
        assert len(result["profiles_map"]) == 2

    def test_single_api_no_profiles_map(self):
        entries = [
            _har_entry("https://api.example.com/v1/a"),
            _har_entry("https://api.example.com/v1/b"),
        ]
        result = generate_adopt_profile("https://api.example.com", entries)
        assert result["profiles_map"] == {}

    def test_empty_clicks(self):
        entries = [_har_entry("https://api.example.com/v1/data")]
        result = generate_adopt_profile("https://api.example.com", entries, click_events=[])
        assert result["workflow_params"] == {}


# ── _extract_workflow_params ─────────────────────────────────────────────────

class TestExtractWorkflowParams:
    def test_basic_extraction(self):
        clicks = [
            {"event_type": "input", "field_name": "email", "value": "a@b.com", "input_type": "email"},
        ]
        params = _extract_workflow_params(clicks)
        assert "email" in params
        assert params["email"]["type"] == "string"
        assert params["email"]["example"] == "a@b.com"
        assert params["email"]["required"] is True

    def test_deduplicates(self):
        clicks = [
            {"event_type": "input", "field_name": "name", "value": "Alice"},
            {"event_type": "input", "field_name": "name", "value": "Bob"},
        ]
        params = _extract_workflow_params(clicks)
        assert len(params) == 1

    def test_ignores_clicks(self):
        clicks = [
            {"event_type": "click", "field_name": "submit"},
        ]
        params = _extract_workflow_params(clicks)
        assert len(params) == 0

    def test_fallback_to_element_id(self):
        clicks = [
            {"event_type": "input", "field_name": "", "element_id": "txtSearch", "value": "test"},
        ]
        params = _extract_workflow_params(clicks)
        assert "txt_search" in params


# ── _to_variable_name ────────────────────────────────────────────────────────

class TestToVariableName:
    def test_dash_to_underscore(self):
        assert _to_variable_name("customer-name") == "customer_name"

    def test_space_to_underscore(self):
        assert _to_variable_name("Customer Name") == "customer_name"

    def test_camel_case(self):
        assert _to_variable_name("firstName") == "first_name"

    def test_already_snake(self):
        assert _to_variable_name("email_address") == "email_address"

    def test_empty(self):
        assert _to_variable_name("") == "param"


# ── _input_type_to_param_type ────────────────────────────────────────────────

class TestInputTypeToParamType:
    def test_number(self):
        assert _input_type_to_param_type("number") == "number"

    def test_checkbox(self):
        assert _input_type_to_param_type("checkbox") == "boolean"

    def test_unknown(self):
        assert _input_type_to_param_type("unknown") == "string"

    def test_select_multiple(self):
        assert _input_type_to_param_type("select-multiple") == "array"
