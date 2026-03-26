"""Tests for app.test_case_generator — ABCD test case generation."""

import json
import pytest
from app.test_case_generator import (
    generate_test_cases,
    _derive_prompt,
    _derive_expected_output,
    _derive_validation,
    _derive_workflow_params,
    _slugify,
    _to_var,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _har_entry(url, method="GET", status=200, resp_body=None):
    """Build a minimal HAR entry."""
    return {
        "request": {"method": method, "url": url, "headers": []},
        "response": {
            "status": status,
            "content": {
                "mimeType": "application/json",
                "text": json.dumps(resp_body) if resp_body else "",
            },
        },
    }


# ── generate_test_cases ──────────────────────────────────────────────────────

class TestGenerateTestCases:
    def test_happy_path_generated(self):
        cases = generate_test_cases("Create Customer")
        assert len(cases) >= 1
        assert cases[0]["test_case_name"] == "happy_path_create_customer"

    def test_prompt_from_narration(self):
        narrations = [{"content": "I am creating a new customer record"}]
        cases = generate_test_cases("Create Customer", narrations=narrations)
        assert cases[0]["prompt"] == "I am creating a new customer record"

    def test_prompt_fallback(self):
        cases = generate_test_cases("Add Quote")
        assert "Add Quote" in cases[0]["prompt"]

    def test_workflow_params_from_clicks(self):
        clicks = [
            {"event_type": "input", "field_name": "name", "value": "Acme Corp"},
            {"event_type": "input", "field_name": "email", "value": "acme@test.com"},
            {"event_type": "click"},  # should be ignored
        ]
        cases = generate_test_cases("Test", click_events=clicks)
        params = cases[0]["workflow_params"]
        assert params["name"] == "Acme Corp"
        assert params["email"] == "acme@test.com"

    def test_expected_output_from_har(self):
        har = [
            _har_entry("https://api.example.com/v1/data", resp_body={"message": "Customer created"}),
        ]
        cases = generate_test_cases("Test", har_entries=har)
        assert cases[0]["expected_output"] == "Customer created"

    def test_expected_output_fallback(self):
        cases = generate_test_cases("Test")
        assert cases[0]["expected_output"] == "Workflow completed successfully"

    def test_validation_structure(self):
        cases = generate_test_cases("Test")
        val = cases[0]["validation"]
        assert "type" in val
        assert "value" in val

    def test_missing_params_stub_generated(self):
        wdl_params = {
            "name": {"example": "Acme", "source": "form_input"},
            "email": {"example": "a@b.com", "source": "form_input"},
        }
        cases = generate_test_cases("Create Customer", wdl_params=wdl_params)
        assert len(cases) == 2
        assert cases[1]["test_case_name"] == "missing_params_create_customer"
        assert cases[1]["workflow_params"] == {}
        assert cases[1]["expected_output"] == "error"

    def test_no_missing_params_stub_without_form_inputs(self):
        wdl_params = {
            "base_url": {"example": "https://api.com", "source": "har_analysis"},
        }
        cases = generate_test_cases("Test", wdl_params=wdl_params)
        assert len(cases) == 1  # Only happy path


# ── _derive_prompt ───────────────────────────────────────────────────────────

class TestDerivePrompt:
    def test_from_narration(self):
        narrations = [{"content": "Long enough narration text here"}]
        assert _derive_prompt("Test", narrations) == "Long enough narration text here"

    def test_short_narration_skipped(self):
        narrations = [{"content": "Too short"}]
        prompt = _derive_prompt("Test", narrations)
        assert "Test" in prompt  # Falls back to process name

    def test_empty_narrations(self):
        prompt = _derive_prompt("My Process", [])
        assert "My Process" in prompt


# ── _derive_expected_output ──────────────────────────────────────────────────

class TestDeriveExpectedOutput:
    def test_message_field(self):
        har = [_har_entry("https://api.com/v1/x", resp_body={"message": "Success"})]
        assert _derive_expected_output(har) == "Success"

    def test_status_field(self):
        har = [_har_entry("https://api.com/v1/x", resp_body={"status": "completed"})]
        assert _derive_expected_output(har) == "completed"

    def test_data_field(self):
        har = [_har_entry("https://api.com/v1/x", resp_body={"data": "some result"})]
        assert _derive_expected_output(har) == "some result"

    def test_list_response(self):
        har = [_har_entry("https://api.com/v1/x", resp_body=[1, 2, 3])]
        assert _derive_expected_output(har) == "Returned 3 items"

    def test_no_response_body(self):
        har = [_har_entry("https://api.com/v1/x")]
        result = _derive_expected_output(har)
        assert "200" in result

    def test_empty_entries(self):
        assert _derive_expected_output([]) == "Workflow completed successfully"

    def test_fallback_to_keys_summary(self):
        har = [_har_entry("https://api.com/v1/x", resp_body={"foo": "bar", "baz": 42})]
        result = _derive_expected_output(har)
        assert "foo" in result


# ── _derive_validation ───────────────────────────────────────────────────────

class TestDeriveValidation:
    def test_short_output(self):
        val = _derive_validation("Customer created")
        assert val["type"] == "contains"
        assert val["value"] == "Customer created"

    def test_long_output(self):
        long_text = "This is a very long expected output. It goes on and on. " * 5
        val = _derive_validation(long_text)
        assert val["type"] == "contains"
        assert len(val["value"]) < len(long_text)

    def test_empty_output(self):
        val = _derive_validation("")
        assert val["type"] == "contains"


# ── _slugify ─────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("Create Customer") == "create_customer"

    def test_special_chars(self):
        assert _slugify("Add Quote (v2)") == "add_quote_v2"

    def test_empty(self):
        assert _slugify("") == "unnamed"


# ── _to_var ──────────────────────────────────────────────────────────────────

class TestToVar:
    def test_basic(self):
        assert _to_var("customer-name") == "customer_name"

    def test_camel_case(self):
        assert _to_var("firstName") == "first_name"

    def test_empty(self):
        assert _to_var("") == "param"
