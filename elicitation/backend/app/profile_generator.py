"""Adopt profile generation for ABCD.

Generates ``adopt_profile.json`` content from captured data:
base_url, security_params, workflow_params, and profiles_map.
"""

from __future__ import annotations

import re

from app.auth_detector import detect_auth_patterns
from app.har_analyzer import detect_api_groups


def generate_adopt_profile(
    base_url: str,
    har_entries: list[dict],
    click_events: list[dict] | None = None,
) -> dict:
    """Generate an ABCD-compatible adopt_profile.json.

    Args:
        base_url: Process base_url (fallback if HAR analysis yields nothing).
        har_entries: Filtered HAR entries.
        click_events: Click event dicts for workflow_params extraction.

    Returns:
        Dict matching adopt_profile.json schema:
        ``{"base_url", "security_params", "workflow_params", "profiles_map"}``
    """
    click_events = click_events or []

    # Detect API groups from HAR
    api_groups = detect_api_groups(har_entries)

    # Determine effective base_url
    effective_base_url = base_url
    if api_groups:
        # Use the most-seen API's base_url
        effective_base_url = api_groups[0]["base_url"]

    # Detect auth (on full set first, then per-group if multi-API)
    auth_result = detect_auth_patterns(har_entries)
    security_params = auth_result["security_params"]

    # Extract workflow_params from form inputs
    workflow_params = _extract_workflow_params(click_events)

    # Build profiles_map if multiple APIs detected
    profiles_map = {}
    if len(api_groups) > 1:
        profiles_map = _build_profiles_map(api_groups, har_entries)

    return {
        "base_url": effective_base_url,
        "security_params": security_params,
        "workflow_params": workflow_params,
        "profiles_map": profiles_map,
    }


def _extract_workflow_params(click_events: list[dict]) -> dict:
    """Extract workflow_params from form input click events.

    Each input event with a field_name becomes a parameterized variable.
    """
    params: dict[str, dict] = {}
    seen_fields: set[str] = set()

    for click in click_events:
        if click.get("event_type") not in ("input", "change"):
            continue

        field_name = click.get("field_name") or ""
        if not field_name:
            # Try to derive from element_id or selector
            field_name = click.get("element_id") or ""

        if not field_name:
            continue

        # Normalize field_name to a variable-friendly key
        var_name = _to_variable_name(field_name)
        if var_name in seen_fields:
            continue
        seen_fields.add(var_name)

        value = click.get("value", "")
        input_type = click.get("input_type", "text")

        params[var_name] = {
            "type": _input_type_to_param_type(input_type),
            "description": f"Value for {field_name} field",
            "required": True,
            "example": value,
        }

    return params


def _to_variable_name(field_name: str) -> str:
    """Convert a form field name to a clean variable name.

    "customer-name" -> "customer_name"
    "Customer Name" -> "customer_name"
    "email_address" -> "email_address"
    """
    # Replace common separators with underscores
    name = re.sub(r"[-\s.]+", "_", field_name.strip())
    # Remove non-alphanumeric (except underscores)
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    # Convert camelCase to snake_case
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return name.lower().strip("_") or "param"


def _input_type_to_param_type(input_type: str) -> str:
    """Map HTML input type to a parameter type string."""
    mapping = {
        "number": "number",
        "email": "string",
        "tel": "string",
        "url": "string",
        "date": "string",
        "datetime-local": "string",
        "checkbox": "boolean",
        "radio": "string",
        "select-one": "string",
        "select-multiple": "array",
    }
    return mapping.get(input_type, "string")


def _build_profiles_map(
    api_groups: list[dict],
    har_entries: list[dict],
) -> dict:
    """Build profiles_map for multi-API scenarios.

    Each detected API group gets its own entry with base_url and
    security_params detected from its subset of HAR entries.
    """
    profiles: dict[str, dict] = {}

    for group in api_groups:
        group_base = group["base_url"]
        group_name = group["name"]

        # Filter HAR entries for this group
        group_entries = [
            e for e in har_entries
            if e.get("request", {}).get("url", "").startswith(group_base)
        ]

        # Detect auth specific to this API group
        group_auth = detect_auth_patterns(group_entries)

        profiles[group_name] = {
            "base_url": group_base,
            "security_params": group_auth["security_params"],
        }

    return profiles
