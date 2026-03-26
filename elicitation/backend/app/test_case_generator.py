"""ABCD test case generation from captured data.

Each capture session represents a "happy path" test case.  Form input
values become workflow_params, narrations seed the prompt, and API
responses inform expected_output.
"""

from __future__ import annotations

import json
import re


def generate_test_cases(
    process_name: str,
    click_events: list[dict] | None = None,
    narrations: list[dict] | None = None,
    har_entries: list[dict] | None = None,
    wdl_params: dict | None = None,
) -> list[dict]:
    """Generate ABCD test cases from captured data.

    Args:
        process_name: Name of the process (used for test_case_name).
        click_events: Click event dicts (for workflow_params values).
        narrations: Narration dicts (for prompt text).
        har_entries: Filtered HAR entries (for expected_output).
        wdl_params: ``detected_params`` from wdl_generator (for param names).

    Returns:
        List of ABCD test case dicts.
    """
    click_events = click_events or []
    narrations = narrations or []
    har_entries = har_entries or []
    wdl_params = wdl_params or {}

    test_cases: list[dict] = []

    # ── Happy-path test case ─────────────────────────────────────────────
    # This represents the workflow as demonstrated by the user.

    # Test case name
    slug = _slugify(process_name)
    tc_name = f"happy_path_{slug}"

    # Prompt: first narration or a generated description
    prompt = _derive_prompt(process_name, narrations)

    # Workflow params: from form inputs
    workflow_params = _derive_workflow_params(click_events, wdl_params)

    # Expected output: from last API response
    expected_output = _derive_expected_output(har_entries)

    # Validation
    validation = _derive_validation(expected_output)

    test_cases.append({
        "test_case_name": tc_name,
        "prompt": prompt,
        "workflow_params": workflow_params,
        "expected_output": expected_output,
        "validation": validation,
    })

    # ── Edge-case stubs ──────────────────────────────────────────────────
    # If there are required params, generate a "missing params" test stub
    required_params = [
        k for k, v in wdl_params.items()
        if isinstance(v, dict) and v.get("source") == "form_input"
    ]
    if required_params:
        test_cases.append({
            "test_case_name": f"missing_params_{slug}",
            "prompt": prompt,
            "workflow_params": {},  # Deliberately empty
            "expected_output": "error",
            "validation": {"type": "contains", "value": "error"},
        })

    return test_cases


# ── Helpers ──────────────────────────────────────────────────────────────────

def _derive_prompt(process_name: str, narrations: list[dict]) -> str:
    """Derive a test prompt from narrations or process name."""
    if narrations:
        # Use the first narration as the prompt
        first = narrations[0].get("content", "")
        if first and len(first) > 10:
            return first
    # Fallback: generate from process name
    return f"Execute the {process_name} workflow"


def _derive_workflow_params(
    click_events: list[dict],
    wdl_params: dict,
) -> dict:
    """Extract concrete param values from click events."""
    params: dict[str, str] = {}
    seen_fields: set[str] = set()

    for click in click_events:
        if click.get("event_type") not in ("input", "change"):
            continue
        field_name = click.get("field_name") or click.get("element_id") or ""
        value = click.get("value", "")
        if not field_name or not value:
            continue

        var_name = _to_var(field_name)
        if var_name in seen_fields:
            continue
        seen_fields.add(var_name)
        params[var_name] = value

    return params


def _derive_expected_output(har_entries: list[dict]) -> str:
    """Derive expected output from the last API response."""
    if not har_entries:
        return "Workflow completed successfully"

    last_entry = har_entries[-1]
    resp_content = last_entry.get("response", {}).get("content", {})
    resp_text = resp_content.get("text", "")

    if resp_text:
        try:
            resp_json = json.loads(resp_text)
            # Try to summarize the response
            if isinstance(resp_json, dict):
                # Look for message/status fields
                for key in ("message", "status", "result", "data"):
                    if key in resp_json:
                        val = resp_json[key]
                        if isinstance(val, str) and len(val) < 200:
                            return val
                # Fallback: first few keys
                summary_parts = []
                for k, v in list(resp_json.items())[:3]:
                    summary_parts.append(f"{k}: {v}")
                return ", ".join(summary_parts)
            elif isinstance(resp_json, list):
                return f"Returned {len(resp_json)} items"
        except (json.JSONDecodeError, TypeError):
            if len(resp_text) < 200:
                return resp_text

    status = last_entry.get("response", {}).get("status", 200)
    return f"HTTP {status} response received"


def _derive_validation(expected_output: str) -> dict:
    """Choose a validation strategy based on the expected output."""
    if not expected_output or len(expected_output) < 5:
        return {"type": "contains", "value": ""}

    # If it's a short, clean phrase — use contains
    if len(expected_output) < 100:
        return {"type": "contains", "value": expected_output}

    # For longer outputs, extract a key phrase
    # Take the first sentence or first 60 chars
    first_sentence = expected_output.split(".")[0].strip()
    if len(first_sentence) > 10:
        return {"type": "contains", "value": first_sentence}

    return {"type": "contains", "value": expected_output[:60]}


def _slugify(name: str) -> str:
    """Convert a name to a slug for test case naming."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower())
    return slug.strip("_") or "unnamed"


def _to_var(field_name: str) -> str:
    """Convert field name to variable name."""
    name = re.sub(r"[-\s.]+", "_", field_name.strip())
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return name.lower().strip("_") or "param"
