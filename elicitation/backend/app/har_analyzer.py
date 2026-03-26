"""HAR filtering, annotation, and multi-API detection.

Pure functions — no DB dependencies. Operates on HAR log dicts
(standard HAR 1.2 format) and returns enriched data structures.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# ── Asset / noise patterns ──────────────────────────────────────────────────

_ASSET_EXTENSIONS = frozenset({
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg",
})

_NOISE_URL_FRAGMENTS = frozenset({
    "analytics", "tracking", "telemetry", "beacon", "pixel",
    "hotjar", "segment", "mixpanel", "google-analytics",
    "doubleclick", "facebook.com/tr", "googletagmanager",
    "sentry", "bugsnag", "newrelic", "datadog",
})

_API_CONTENT_TYPES = frozenset({
    "application/json", "application/xml", "text/xml",
    "application/hal+json", "application/vnd.api+json",
    "application/problem+json", "application/graphql+json",
})

_SKIP_CONTENT_TYPES = frozenset({
    "text/html", "text/css", "application/javascript",
    "text/javascript", "application/x-javascript",
})


# ── Filtering ────────────────────────────────────────────────────────────────

def filter_har_entries(
    har_log: dict,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[dict]:
    """Filter HAR entries to likely API calls only.

    Args:
        har_log: Full HAR object with har_log["log"]["entries"].
        include_patterns: Regex patterns — if any match the URL, entry is included.
        exclude_patterns: Regex patterns — if any match the URL, entry is excluded.

    Returns:
        List of HAR entry dicts with ``_original_index`` injected.
    """
    entries = har_log.get("log", {}).get("entries", [])
    if not entries:
        return []

    include_re = [re.compile(p, re.IGNORECASE) for p in (include_patterns or [])]
    exclude_re = [re.compile(p, re.IGNORECASE) for p in (exclude_patterns or [])]

    result = []
    for idx, entry in enumerate(entries):
        url = entry.get("request", {}).get("url", "")
        method = entry.get("request", {}).get("method", "GET").upper()

        # Skip CORS preflight
        if method == "OPTIONS":
            continue

        # User-supplied exclusions take priority
        if exclude_re and any(r.search(url) for r in exclude_re):
            continue

        # User-supplied inclusions override all other logic
        if include_re and any(r.search(url) for r in include_re):
            entry_copy = dict(entry)
            entry_copy["_original_index"] = idx
            result.append(entry_copy)
            continue

        # Heuristic classification
        if _is_api_call(entry):
            entry_copy = dict(entry)
            entry_copy["_original_index"] = idx
            result.append(entry_copy)

    return result


def _is_api_call(entry: dict) -> bool:
    """Heuristic: is this HAR entry likely an API call?"""
    request = entry.get("request", {})
    response = entry.get("response", {})
    url = request.get("url", "")
    method = request.get("method", "GET").upper()

    parsed = urlparse(url)
    path = parsed.path.lower()

    # Exclude asset files
    for ext in _ASSET_EXTENSIONS:
        if path.endswith(ext):
            return False

    # Exclude known noise domains / URL fragments
    url_lower = url.lower()
    for frag in _NOISE_URL_FRAGMENTS:
        if frag in url_lower:
            return False

    # Check response content type
    resp_content_type = _get_content_type(response)
    if resp_content_type:
        for api_ct in _API_CONTENT_TYPES:
            if api_ct in resp_content_type:
                return True
        for skip_ct in _SKIP_CONTENT_TYPES:
            if skip_ct in resp_content_type:
                return False

    # Non-GET methods are likely API calls (form submissions, mutations)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        # Check request content type too
        req_content_type = _get_content_type(request, key="headers")
        if req_content_type and "application/json" in req_content_type:
            return True
        # POST with form data to a path that looks API-ish
        if any(seg in path for seg in ("/api/", "/v1/", "/v2/", "/v3/", "/graphql")):
            return True

    # URL path looks like an API
    if any(seg in path for seg in ("/api/", "/v1/", "/v2/", "/v3/", "/graphql", "/rest/")):
        return True

    # If we got a JSON response for a GET, it's likely API
    if method == "GET" and resp_content_type and "json" in resp_content_type:
        return True

    return False


def _get_content_type(obj: dict, key: str = "content") -> str:
    """Extract content-type from HAR response.content or request headers."""
    if key == "content":
        return (obj.get("content", {}).get("mimeType", "") or "").lower()
    # From headers list
    for h in obj.get("headers", []):
        if h.get("name", "").lower() == "content-type":
            return h.get("value", "").lower()
    return ""


# ── Annotation ───────────────────────────────────────────────────────────────

def annotate_har_entries(
    entries: list[dict],
    timeline_events: list[dict] | None = None,
    click_events: list[dict] | None = None,
) -> list[dict]:
    """Add ``_annotations`` to filtered HAR entries.

    Annotations include step_number, is_primary, related_click_event_ids,
    related_narration_summary, and detected_params.

    Args:
        entries: Filtered HAR entries (from ``filter_har_entries``).
        timeline_events: Timeline event dicts (with ``timestamp``, ``event_type``,
            ``summary``, ``metadata_json``).  Can be None.
        click_events: Click event dicts (with ``timestamp``, ``event_type``,
            ``field_name``, ``value``).  Can be None.

    Returns:
        Same entries list with ``_annotations`` added to each.
    """
    timeline_events = timeline_events or []
    click_events = click_events or []

    for step_num, entry in enumerate(entries):
        entry_time = entry.get("startedDateTime", "")
        request = entry.get("request", {})
        url = request.get("url", "")

        annotations: dict = {
            "step_number": step_num,
            "is_primary": True,  # refined below if we can detect side-effects
            "related_click_event_ids": [],
            "detected_params": [],
        }

        # Find click events within 2 seconds before this HAR entry
        if entry_time and click_events:
            related = _find_nearby_clicks(entry_time, click_events, window_ms=2000)
            annotations["related_click_event_ids"] = [c.get("id", "") for c in related]

            # Extract detected params from related input events
            for click in related:
                if click.get("event_type") in ("input", "change") and click.get("field_name"):
                    annotations["detected_params"].append({
                        "field_name": click["field_name"],
                        "value": click.get("value", ""),
                        "source": "form_input",
                    })

        entry["_annotations"] = annotations

    return entries


def _find_nearby_clicks(
    har_timestamp: str,
    click_events: list[dict],
    window_ms: int = 2000,
) -> list[dict]:
    """Find click events that happened within ``window_ms`` before the HAR entry."""
    from datetime import datetime, timedelta, timezone

    try:
        har_dt = _to_naive_utc(har_timestamp)
    except (ValueError, AttributeError, TypeError):
        return []

    if har_dt is None:
        return []

    window = timedelta(milliseconds=window_ms)
    results = []
    for click in click_events:
        click_ts = click.get("timestamp")
        if not click_ts:
            continue
        try:
            click_dt = _to_naive_utc(click_ts)
        except (ValueError, AttributeError, TypeError):
            continue
        if click_dt is None:
            continue

        diff = har_dt - click_dt
        if timedelta(0) <= diff <= window:
            results.append(click)

    return results


def _to_naive_utc(ts) -> "datetime | None":
    """Parse a timestamp to naive UTC datetime to avoid tz comparison errors."""
    from datetime import datetime, timezone

    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif isinstance(ts, datetime):
        dt = ts
    else:
        return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── Multi-API detection ──────────────────────────────────────────────────────

def detect_api_groups(entries: list[dict]) -> list[dict]:
    """Group filtered HAR entries by base URL to identify distinct APIs.

    Returns:
        List of API group dicts:
        ``[{"name": "auto", "base_url": "...", "entry_count": N,
            "endpoints": [{"method": "POST", "path": "/customers"}]}]``
    """
    if not entries:
        return []

    groups: dict[str, dict] = {}  # base_url -> group info

    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        method = entry.get("request", {}).get("method", "GET").upper()
        status = entry.get("response", {}).get("status", 0)
        parsed = urlparse(url)

        base = f"{parsed.scheme}://{parsed.netloc}"
        # Try to detect API prefix (e.g. /api/v1)
        path = parsed.path
        api_base = _detect_api_prefix(path)
        if api_base:
            base = base + api_base

        rel_path = path
        if api_base and path.startswith(api_base):
            rel_path = path[len(api_base):]
        if not rel_path:
            rel_path = "/"

        if base not in groups:
            groups[base] = {
                "name": _auto_name_api(parsed.netloc),
                "base_url": base,
                "entry_count": 0,
                "endpoints": [],
                "status_codes": set(),
            }

        g = groups[base]
        g["entry_count"] += 1
        g["status_codes"].add(status)

        endpoint_key = (method, rel_path)
        if not any(
            (ep["method"], ep["path"]) == endpoint_key for ep in g["endpoints"]
        ):
            g["endpoints"].append({
                "method": method,
                "path": rel_path,
                "status_codes": [status],
            })
        else:
            for ep in g["endpoints"]:
                if (ep["method"], ep["path"]) == endpoint_key:
                    if status not in ep["status_codes"]:
                        ep["status_codes"].append(status)

    # Convert sets for JSON serialization and sort by entry count
    result = []
    for g in groups.values():
        g.pop("status_codes", None)
        result.append(g)
    result.sort(key=lambda g: g["entry_count"], reverse=True)

    return result


def _detect_api_prefix(path: str) -> str:
    """Try to detect a common API prefix from a URL path."""
    # Match patterns like /api, /api/v1, /v1, /v2, /rest
    m = re.match(r"(/(?:api(?:/v\d+)?|v\d+|rest))", path, re.IGNORECASE)
    return m.group(1) if m else ""


def _auto_name_api(netloc: str) -> str:
    """Generate a human-friendly name from a domain."""
    # "api.crm.example.com" -> "crm"
    # "localhost:8000" -> "localhost"
    host = netloc.split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] in ("api", "www"):
        return parts[1]
    return parts[0]
