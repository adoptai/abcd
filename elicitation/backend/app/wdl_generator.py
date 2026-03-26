"""WDL (Workflow Definition Language) draft generation from captured data.

Transforms filtered HAR entries + click events + narrations into a
multi-step WDL definition compatible with ABCD's widdle.json format.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs


# Minimum length for a value to be considered as a dependency candidate
_MIN_DEP_VALUE_LEN = 4

# Values to never treat as dependencies (too generic)
_IGNORE_VALUES = frozenset({
    "true", "false", "null", "ok", "error", "success", "failed",
    "application/json", "text/plain", "utf-8",
    "GET", "POST", "PUT", "PATCH", "DELETE",
})


# ── Public API ───────────────────────────────────────────────────────────────

def generate_wdl(
    har_entries: list[dict],
    click_events: list[dict] | None = None,
    narrations: list[dict] | None = None,
    timeline_events: list[dict] | None = None,
    base_url: str = "",
) -> dict:
    """Generate a WDL draft from captured data.

    Args:
        har_entries: Filtered (and optionally annotated) HAR entries.
        click_events: Click event dicts for parameterization.
        narrations: Narration dicts for step descriptions.
        timeline_events: Timeline event dicts for ordering context.
        base_url: Base URL to replace with ``{{base_url}}``.

    Returns:
        {
            "steps": [<WDL step dicts>],
            "detected_params": {"param_name": {"example": "...", "source": "..."}},
            "detected_dependencies": [{"from_step": 0, "to_step": 2, "via": "customer_id"}],
            "source_stats": {"har_entries": N, "click_events": N, "narrations": N},
        }
    """
    click_events = click_events or []
    narrations = narrations or []
    timeline_events = timeline_events or []

    if not har_entries:
        return {
            "steps": [],
            "detected_params": {},
            "detected_dependencies": [],
            "source_stats": {"har_entries": 0, "click_events": 0, "narrations": 0},
        }

    # Phase 1: Detect the effective base_url if not provided
    if not base_url:
        base_url = _detect_base_url(har_entries)

    # Phase 2: Detect data dependencies between HAR entries
    dependencies = detect_dependencies(har_entries)

    # Phase 3: Collect form input values for parameterization
    form_values = _collect_form_values(click_events)

    # Phase 4: Build WDL steps from HAR entries
    steps = []

    # Pre-index: which entries source dependencies, and which consume them
    dep_sources: dict[int, list[dict]] = {}   # entry_idx -> deps it produces
    dep_consumers: dict[int, list[dict]] = {}  # entry_idx -> deps it consumes
    for dep in dependencies:
        dep_sources.setdefault(dep["from_entry"], []).append(dep)
        dep_consumers.setdefault(dep["to_entry"], []).append(dep)

    detected_params: dict[str, dict] = {}

    for step_idx, entry in enumerate(har_entries):
        # Collect dependency substitutions for this entry
        # (replace concrete values with {{variable}} placeholders)
        dep_substitutions: dict[str, str] = {}
        for dep in dep_consumers.get(step_idx, []):
            dep_substitutions[dep["value"]] = "{{" + dep["variable"] + "}}"

        # Build REST step
        rest_step = _build_rest_step(
            entry, base_url, form_values, detected_params, step_idx,
            dep_substitutions=dep_substitutions,
        )

        # Add description from narration if available
        narration_desc = _find_matching_narration(entry, narrations)
        if narration_desc:
            rest_step["description"] = narration_desc

        steps.append(rest_step)

        # Insert JQ_FILTER steps for any dependencies sourced from this entry
        for dep in dep_sources.get(step_idx, []):
            jq_step = {
                "operation": "JQ_FILTER",
                "config": {"expression": dep["jq_expression"]},
                "output": dep["variable"],
                "description": f"Extract {dep['variable']} from response",
            }
            steps.append(jq_step)

    # Phase 5: Add OUTPUT_TEXT step if we have narrations describing the result
    final_narration = _find_final_narration(narrations)
    if final_narration:
        steps.append({
            "operation": "OUTPUT_TEXT",
            "config": {"template": final_narration},
        })

    # Always add base_url to detected_params
    if base_url:
        detected_params["base_url"] = {"example": base_url, "source": "har_analysis"}

    # Format dependencies for output (map entry indices to step indices)
    formatted_deps = _format_dependencies(dependencies, har_entries, steps)

    return {
        "steps": steps,
        "detected_params": detected_params,
        "detected_dependencies": formatted_deps,
        "source_stats": {
            "har_entries": len(har_entries),
            "click_events": len(click_events),
            "narrations": len(narrations),
        },
    }


# ── Base URL detection ────────────────────────────────────────────────────────

def _detect_base_url(har_entries: list[dict]) -> str:
    """Detect the most common base URL from HAR entries."""
    from collections import Counter
    bases: Counter = Counter()
    for entry in har_entries:
        url = entry.get("request", {}).get("url", "")
        if not url:
            continue
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        bases[base] += 1
    if bases:
        return bases.most_common(1)[0][0]
    return ""


# ── Dependency detection ─────────────────────────────────────────────────────

def detect_dependencies(har_entries: list[dict]) -> list[dict]:
    """Find data dependencies between sequential HAR entries.

    For each response, extract scalar values and check if they appear in
    any subsequent request (URL, headers, body).

    Returns:
        [{"from_entry": 0, "to_entry": 2, "field": "id",
          "jq_expression": ".id", "variable": "result_id", "value": "123"}]
    """
    if len(har_entries) < 2:
        return []

    deps = []

    for i, entry in enumerate(har_entries):
        resp_body = _parse_response_body(entry)
        if not resp_body or not isinstance(resp_body, dict):
            continue

        # Extract candidate scalar values from response
        candidates = _extract_scalar_values(resp_body, prefix="")
        if not candidates:
            continue

        # Check each subsequent entry for references to these values
        for j in range(i + 1, len(har_entries)):
            subsequent = har_entries[j]
            subsequent_text = _request_as_searchable_text(subsequent)
            if not subsequent_text:
                continue

            for field_path, value in candidates:
                str_val = str(value)
                if len(str_val) < _MIN_DEP_VALUE_LEN:
                    continue
                if str_val.lower() in _IGNORE_VALUES:
                    continue

                if str_val in subsequent_text:
                    var_name = _path_to_variable_name(field_path)
                    jq_expr = _path_to_jq(field_path)

                    # Avoid duplicates
                    if not any(
                        d["from_entry"] == i and d["variable"] == var_name
                        for d in deps
                    ):
                        deps.append({
                            "from_entry": i,
                            "to_entry": j,
                            "field": field_path,
                            "jq_expression": jq_expr,
                            "variable": var_name,
                            "value": str_val,
                        })

    return deps


# ── REST step builder ────────────────────────────────────────────────────────

def _build_rest_step(
    entry: dict,
    base_url: str,
    form_values: dict[str, str],
    detected_params: dict[str, dict],
    step_idx: int,
    dep_substitutions: dict[str, str] | None = None,
) -> dict:
    """Build a WDL REST step from a HAR entry."""
    request = entry.get("request", {})
    method = request.get("method", "GET").upper()
    url = request.get("url", "")
    status = entry.get("response", {}).get("status", 200)

    # Parameterize URL
    parameterized_url = url
    if base_url and url.startswith(base_url):
        parameterized_url = "{{base_url}}" + url[len(base_url):]
    elif base_url:
        # Try matching just the scheme+host portion
        parsed = urlparse(base_url)
        url_parsed = urlparse(url)
        if parsed.netloc == url_parsed.netloc:
            parameterized_url = "{{base_url}}" + url_parsed.path
            if url_parsed.query:
                parameterized_url += "?" + url_parsed.query

    # Parameterize form values in URL
    parameterized_url = _parameterize_string(
        parameterized_url, form_values, detected_params
    )

    # Parameterize dependency values in URL (e.g. replace "123" with "{{customer_id}}")
    dep_substitutions = dep_substitutions or {}
    for concrete_val, placeholder in dep_substitutions.items():
        parameterized_url = parameterized_url.replace(concrete_val, placeholder)

    # Build headers (only interesting ones)
    headers = _extract_api_headers(request.get("headers", []))
    # Parameterize auth headers
    for key, val in list(headers.items()):
        if key.lower() == "authorization" and val.startswith("Bearer "):
            headers[key] = "Bearer {{access_token}}"
            detected_params["access_token"] = {
                "example": val[7:20] + "...",
                "source": "auth_header",
            }
        elif key.lower() in ("x-api-key", "apikey", "api-key"):
            headers[key] = "{{api_key}}"
            detected_params["api_key"] = {
                "example": val[:8] + "...",
                "source": "auth_header",
            }

    step: dict = {
        "operation": "REST",
        "config": {
            "method": method,
            "url": parameterized_url,
        },
        "description": f"{method} {urlparse(url).path}",
    }

    if headers:
        step["config"]["headers"] = headers

    # Request body
    post_data = request.get("postData", {})
    if post_data and post_data.get("text"):
        body_text = post_data["text"]
        # Apply dependency substitutions to raw body text first
        for concrete_val, placeholder in dep_substitutions.items():
            body_text = body_text.replace(concrete_val, placeholder)
        try:
            body_json = json.loads(body_text)
            # Parameterize known form values in the body
            body_json = _parameterize_dict(body_json, form_values, detected_params)
            step["config"]["body"] = body_json
        except (json.JSONDecodeError, TypeError):
            step["config"]["body"] = _parameterize_string(
                body_text, form_values, detected_params
            )

    if status:
        step["config"]["expected_status"] = status

    return step


# ── Parameterization helpers ─────────────────────────────────────────────────

def _collect_form_values(click_events: list[dict]) -> dict[str, str]:
    """Collect field_name -> value from form input events."""
    values: dict[str, str] = {}
    for click in click_events:
        if click.get("event_type") not in ("input", "change"):
            continue
        field_name = click.get("field_name") or click.get("element_id") or ""
        value = click.get("value", "")
        if field_name and value:
            values[field_name] = value
    return values


def _parameterize_string(
    text: str,
    form_values: dict[str, str],
    detected_params: dict[str, dict],
) -> str:
    """Replace known form values in a string with {{variable}} placeholders."""
    for field_name, value in form_values.items():
        if not value or len(value) < 2:
            continue
        if value in text:
            var_name = _to_var_name(field_name)
            text = text.replace(value, "{{" + var_name + "}}")
            if var_name not in detected_params:
                detected_params[var_name] = {
                    "example": value,
                    "source": "form_input",
                }
    return text


def _parameterize_dict(
    obj: dict | list,
    form_values: dict[str, str],
    detected_params: dict[str, dict],
) -> dict | list:
    """Recursively parameterize values in a JSON body."""
    if isinstance(obj, dict):
        return {
            k: _parameterize_dict(v, form_values, detected_params)
            if isinstance(v, (dict, list))
            else _parameterize_string(str(v), form_values, detected_params)
            if isinstance(v, str)
            else v
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [
            _parameterize_dict(item, form_values, detected_params)
            if isinstance(item, (dict, list))
            else _parameterize_string(str(item), form_values, detected_params)
            if isinstance(item, str)
            else item
            for item in obj
        ]
    return obj


def _to_var_name(field_name: str) -> str:
    """Convert field name to variable name."""
    name = re.sub(r"[-\s.]+", "_", field_name.strip())
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return name.lower().strip("_") or "param"


# ── Narration matching ───────────────────────────────────────────────────────

def _find_matching_narration(entry: dict, narrations: list[dict]) -> str:
    """Find a narration that's temporally close to this HAR entry."""
    entry_time = entry.get("startedDateTime", "")
    if not entry_time or not narrations:
        return ""

    try:
        entry_dt = _parse_to_naive_utc(entry_time)
    except (ValueError, AttributeError, TypeError):
        return ""

    if entry_dt is None:
        return ""

    best_narration = ""
    best_distance = float("inf")

    for narration in narrations:
        nar_ts = narration.get("timestamp")
        if not nar_ts:
            continue
        try:
            nar_dt = _parse_to_naive_utc(nar_ts)
        except (ValueError, AttributeError, TypeError):
            continue
        if nar_dt is None:
            continue

        dist = abs((entry_dt - nar_dt).total_seconds())
        # Narration should be within 10 seconds of the HAR entry
        if dist < 10 and dist < best_distance:
            best_distance = dist
            best_narration = narration.get("content", "")

    return best_narration


def _find_final_narration(narrations: list[dict]) -> str:
    """Get the last narration as a candidate for OUTPUT_TEXT."""
    if not narrations:
        return ""
    # The last narration often describes the expected result
    last = narrations[-1]
    content = last.get("content", "")
    # Only use if it looks like a result description (short-ish)
    if content and len(content) < 300:
        return content
    return ""


# ── Timestamp normalization ───────────────────────────────────────────────────

def _parse_to_naive_utc(ts) -> datetime | None:
    """Parse a timestamp (string or datetime) to a naive UTC datetime.

    This avoids offset-naive vs offset-aware comparison errors.
    """
    if isinstance(ts, str):
        ts_clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
    elif isinstance(ts, datetime):
        dt = ts
    else:
        return None

    # Convert to UTC and strip tzinfo
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

    return dt


# ── Response/request parsing ─────────────────────────────────────────────────

def _parse_response_body(entry: dict) -> dict | list | None:
    """Parse the response body from a HAR entry as JSON."""
    content = entry.get("response", {}).get("content", {})
    text = content.get("text", "")
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_scalar_values(
    obj: dict | list, prefix: str, max_depth: int = 3,
) -> list[tuple[str, str | int | float]]:
    """Recursively extract (json_path, value) pairs for scalar values."""
    if max_depth <= 0:
        return []

    results = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (str, int, float)) and value != "":
                results.append((path, value))
            elif isinstance(value, (dict, list)):
                results.extend(_extract_scalar_values(value, path, max_depth - 1))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            path = f"{prefix}[{i}]"
            if isinstance(item, (str, int, float)) and item != "":
                results.append((path, item))
            elif isinstance(item, (dict, list)):
                results.extend(_extract_scalar_values(item, path, max_depth - 1))
    return results


def _request_as_searchable_text(entry: dict) -> str:
    """Convert a request to a single string for dependency searching."""
    request = entry.get("request", {})
    parts = [
        request.get("url", ""),
        request.get("postData", {}).get("text", ""),
    ]
    # Include header values
    for h in request.get("headers", []):
        parts.append(h.get("value", ""))
    return " ".join(parts)


def _path_to_jq(path: str) -> str:
    """Convert a dotted path to a jq expression. e.g. 'data.id' -> '.data.id'"""
    if not path.startswith("."):
        return "." + path
    return path


def _path_to_variable_name(path: str) -> str:
    """Convert a JSON path to a variable name. e.g. 'data.customer_id' -> 'customer_id'"""
    # Take the last segment
    parts = re.split(r"[.\[\]]+", path)
    parts = [p for p in parts if p and not p.isdigit()]
    if parts:
        return _to_var_name(parts[-1])
    return "result"


# ── Header extraction ────────────────────────────────────────────────────────

_SKIP_HEADERS = frozenset({
    "host", "connection", "user-agent", "accept-encoding",
    "accept-language", "cache-control", "pragma", "origin",
    "referer", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "dnt", "upgrade-insecure-requests", "if-none-match",
    "if-modified-since", "cookie", "content-length",
})


def _extract_api_headers(headers: list[dict]) -> dict[str, str]:
    """Extract only API-relevant headers from a HAR headers list."""
    result = {}
    for h in headers:
        name = h.get("name", "")
        if name.lower() in _SKIP_HEADERS:
            continue
        if name.lower().startswith("sec-"):
            continue
        result[name] = h.get("value", "")
    return result


# ── Dependency formatting ────────────────────────────────────────────────────

def _format_dependencies(
    dependencies: list[dict],
    har_entries: list[dict],
    steps: list[dict],
) -> list[dict]:
    """Map entry-level dependencies to step-level for output."""
    # For now, entry index roughly equals step index for REST steps
    # (JQ_FILTER steps are inserted after, shifting indices)
    # We return the simplified from_step/to_step/via format
    result = []
    for dep in dependencies:
        result.append({
            "from_step": dep["from_entry"],
            "to_step": dep["to_entry"],
            "via": dep["variable"],
        })
    return result
