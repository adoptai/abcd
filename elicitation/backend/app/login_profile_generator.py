"""
Login profile generator: converts a login-recording CaptureSession into
Tabby Application + ServiceProfile drafts plus a review report.

Inputs (loaded by the router before calling generate()):
    - session: CaptureSession dict
    - click_events: list of ClickEvent dicts (chronological)
    - url_events: list of TimelineEvent dicts with event_type="url_change"
    - har: HAR dict (log.entries[]) or None

Outputs (returned as a dict):
    {
        "recording": {"session_id": "...", "source": "elicitation-login-recorder"},
        "application_draft": { ... },
        "service_profile_draft": { ... },
        "review_items": [ {"type": ..., "severity": ..., "message": ...} ],
        "validation": {"generator_valid": bool, "issues": []}
    }
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from typing import Any


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------

def _selector_confidence(ev: dict) -> str:
    """Return 'high', 'medium', or 'low'."""
    data_attrs: dict = {}
    if ev.get("data_attrs_json"):
        try:
            data_attrs = json.loads(ev["data_attrs_json"])
        except Exception:
            pass

    # high: data-testid / data-test
    if data_attrs.get("data-testid") or data_attrs.get("data-test"):
        return "high"
    # high: stable id (no 8+ hex chars that look like random UUIDs)
    elem_id = ev.get("element_id") or ""
    if elem_id and not re.search(r"[0-9a-f]{8,}", elem_id, re.I):
        return "high"
    # medium: name, autocomplete, aria-label, placeholder
    if ev.get("field_name") or ev.get("autocomplete") or ev.get("aria_label") or ev.get("placeholder"):
        return "medium"
    return "low"


def _build_selector(ev: dict) -> str:
    """Build the best CSS selector from click event metadata."""
    data_attrs: dict = {}
    if ev.get("data_attrs_json"):
        try:
            data_attrs = json.loads(ev["data_attrs_json"])
        except Exception:
            pass

    # Priority 1: data-testid / data-test
    if data_attrs.get("data-testid"):
        return f'[data-testid="{data_attrs["data-testid"]}"]'
    if data_attrs.get("data-test"):
        return f'[data-test="{data_attrs["data-test"]}"]'

    # Priority 2: stable id
    elem_id = ev.get("element_id") or ""
    if elem_id and not re.search(r"[0-9a-f]{8,}", elem_id, re.I):
        return f"#{elem_id}"

    # Priority 3: name attribute
    field_name = ev.get("field_name") or ""
    tag = (ev.get("tag_name") or "input").lower()
    if field_name:
        return f'{tag}[name="{field_name}"]'

    # Priority 4: autocomplete
    autocomplete = ev.get("autocomplete") or ""
    if autocomplete:
        return f'{tag}[autocomplete="{autocomplete}"]'

    # Priority 5: semantic combination
    input_type = ev.get("input_type") or ""
    aria_label = ev.get("aria_label") or ""
    placeholder = ev.get("placeholder") or ""
    parts = [tag]
    if input_type and tag == "input":
        parts.append(f'[type="{input_type}"]')
    if aria_label:
        parts.append(f'[aria-label="{aria_label}"]')
    elif placeholder:
        parts.append(f'[placeholder="{placeholder}"]')
    if len(parts) > 1:
        return "".join(parts)

    # Priority 6: fallback to existing selector
    return ev.get("selector") or tag


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _url_origin(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url


def _url_domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def _is_redirect_hop(prev_url: str, next_url: str) -> bool:
    """True if next_url looks like a transient redirect (same domain, /callback, /sso, /auth)."""
    try:
        prev_parsed = urlparse(prev_url)
        next_parsed = urlparse(next_url)
        if prev_parsed.netloc != next_parsed.netloc:
            return False
        path = next_parsed.path.lower()
        return any(s in path for s in ["/callback", "/sso", "/auth", "/oauth", "/saml", "/redirect"])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HAR analysis helpers
# ---------------------------------------------------------------------------

def _analyze_har(har: dict | None) -> dict[str, Any]:
    """Extract auth signals from HAR entries."""
    result: dict[str, Any] = {
        "has_cookies": False,
        "has_auth_headers": False,
        "has_csrf": False,
        "auth_domains": [],
        "set_cookie_headers": [],
        "auth_header_names": [],
    }
    if not har:
        return result

    entries = har.get("log", {}).get("entries", [])
    domains_seen: set[str] = set()
    cookie_names: list[str] = []
    auth_header_names: list[str] = []

    for entry in entries:
        response = entry.get("response", {})
        req = entry.get("request", {})
        url = req.get("url", "")
        domain = _url_domain(url)
        if domain:
            domains_seen.add(domain)

        # Check response Set-Cookie headers
        for header in response.get("headers", []):
            name = (header.get("name") or "").lower()
            if name == "set-cookie":
                result["has_cookies"] = True
                val = header.get("value", "")
                # Extract cookie name
                cookie_name = val.split("=")[0].strip()
                if cookie_name:
                    cookie_names.append(cookie_name)

        # Check request Authorization headers
        for header in req.get("headers", []):
            hname = (header.get("name") or "").lower()
            if hname in ("authorization", "x-auth-token", "x-api-key"):
                result["has_auth_headers"] = True
                auth_header_names.append(header.get("name", ""))

        # Check for CSRF tokens
        for header in req.get("headers", []):
            hname = (header.get("name") or "").lower()
            if "csrf" in hname or "xsrf" in hname:
                result["has_csrf"] = True
                auth_header_names.append(header.get("name", ""))

    result["auth_domains"] = list(domains_seen)
    result["set_cookie_headers"] = list(set(cookie_names))[:20]
    result["auth_header_names"] = list(set(auth_header_names))[:10]
    return result


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate(
    session: dict,
    click_events: list[dict],
    url_events: list[dict],
    har: dict | None = None,
) -> dict[str, Any]:
    """
    Generate an Application draft, ServiceProfile draft, and review items
    from a login recording session.

    Parameters
    ----------
    session:
        CaptureSession as a dict (id, app_name, login_url, ...)
    click_events:
        List of ClickEvent dicts, chronological order
    url_events:
        List of TimelineEvent dicts with event_type="url_change", chronological
    har:
        HAR object (or None)

    Returns
    -------
    Full bundle dict.
    """
    session_id = session.get("id", "")
    app_name = session.get("app_name") or "recorded-app"
    login_url = session.get("login_url") or ""
    review_items: list[dict] = []
    issues: list[str] = []

    # Normalize app_name to a slug for profile_id
    profile_id = re.sub(r"[^a-z0-9]+", "-", app_name.lower()).strip("-") or "recorded-app"

    # ---- Parse URL transitions ----
    # url_events have metadata_json with {"url": "...", "from_url": "..."}
    url_transitions: list[tuple[str, str]] = []  # (from_url, to_url)
    for ev in url_events:
        try:
            meta = json.loads(ev.get("metadata_json") or "{}")
            to_url = meta.get("url") or ev.get("summary", "").replace("Navigated to ", "")
            from_url = meta.get("from_url") or ""
            if to_url:
                url_transitions.append((from_url, to_url))
        except Exception:
            pass

    # First URL: either the session's login_url or the first observed URL
    first_url = login_url
    if not first_url and url_transitions:
        first_url = url_transitions[0][1]
    if not first_url:
        first_url = "https://example.com/login"
        issues.append("No login URL recorded — placeholder used")

    origin = _url_origin(first_url)
    domain = _url_domain(first_url)

    # ---- Map click events to DSL steps ----
    steps: list[dict[str, Any]] = []
    otp_selector: str | None = None
    has_otp = False
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    post_login_url: str | None = None

    # goto first URL
    steps.append({"action": "goto", "url": first_url})

    # Deduplicate consecutive input events for the same field — keep only the
    # last value per (selector, event_type) run so we don't emit partial
    # keystrokes recorded by the standard tracker.
    deduped: list[dict] = []
    for ev in click_events:
        if ev.get("event_type") in ("input", "change") and deduped:
            prev = deduped[-1]
            if (
                prev.get("event_type") in ("input", "change")
                and prev.get("element_id") == ev.get("element_id")
                and prev.get("field_name") == ev.get("field_name")
            ):
                deduped[-1] = ev  # replace with the later (more complete) value
                continue
        deduped.append(ev)
    click_events = deduped

    for ev in click_events:
        # Infer field_role from standard tracker signals when login recorder
        # was not used (field_role will be null from regular capture sessions).
        # Only infer on input/change events — click events on the same elements
        # should stay as clicks, not be converted to fill steps.
        field_role = ev.get("field_role") or ""
        event_type = ev.get("event_type") or "click"
        if not field_role and event_type in ("input", "change"):
            inp_type = (ev.get("input_type") or "").lower()
            elem_id = (ev.get("element_id") or "").lower()
            fname = (ev.get("field_name") or "").lower()
            ac = (ev.get("autocomplete") or "").lower()
            tag = (ev.get("tag_name") or "").lower()
            # Only classify INPUT elements, not buttons or other tags
            if tag in ("input", "textarea", ""):
                _username_signals = {"username", "email", "user", "mail"}
                _password_signals = {"password", "pass", "passwd", "pwd"}
                if inp_type == "password" or ac in ("current-password", "new-password") or any(s in fname or s in elem_id for s in _password_signals):
                    field_role = "password"
                elif inp_type in ("text", "email", "tel") or ac in ("username", "email") or any(s in fname or s in elem_id for s in _username_signals):
                    field_role = "username"
        selector = _build_selector(ev)
        confidence = _selector_confidence(ev)

        if confidence == "low":
            review_items.append({
                "type": "selector_confidence",
                "severity": "warning",
                "message": f"Low-confidence selector for {field_role or event_type} event: {selector!r}",
                "selector": selector,
                "confidence": "low",
            })

        if field_role == "username":
            username_selector = selector
            steps.append({
                "action": "fill",
                "selector": selector,
                "value": "${USERNAME}",
            })
        elif field_role == "password":
            password_selector = selector
            steps.append({
                "action": "fill",
                "selector": selector,
                "value": "${PASSWORD}",
                "sensitive": True,
            })
        elif field_role == "otp":
            has_otp = True
            otp_selector = selector
            # Do NOT generate fill ${OTP} — use wait_for with sensitive: true
            steps.append({
                "action": "wait_for",
                "selector": selector,
                "timeout_ms": 120000,
                "sensitive": True,
            })
        elif field_role == "unknown_sensitive":
            steps.append({
                "action": "wait_for",
                "selector": selector,
                "timeout_ms": 60000,
                "sensitive": True,
            })
            review_items.append({
                "type": "unresolved_sensitive_field",
                "severity": "warning",
                "message": f"Unknown sensitive field detected — review and classify: {selector!r}",
                "selector": selector,
            })
        elif event_type == "click":
            tag = (ev.get("tag_name") or "").lower()
            inp_type = (ev.get("input_type") or "").lower()
            text = (ev.get("text_content") or "").lower()
            # Skip clicks on plain input fields (user focusing before typing) —
            # the fill step covers those interactions.
            if tag == "input" and inp_type in ("text", "email", "password", "tel", ""):
                continue
            # Likely a submit or nav click
            step = {"action": "click", "selector": selector}
            if tag in ("button", "input") and (inp_type == "submit" or text in ("login", "sign in", "log in", "submit", "continue")):
                submit_selector = selector
            steps.append(step)
        elif event_type in ("input", "change"):
            # Non-role-labeled input (e.g. remember-me checkbox, tenant select)
            if ev.get("is_redacted"):
                # Skip — we don't know what this is
                review_items.append({
                    "type": "redacted_field",
                    "severity": "info",
                    "message": f"Redacted field with no role detected — review: {selector!r}",
                    "selector": selector,
                })
            else:
                val = ev.get("value") or ""
                tag_lower = (ev.get("tag_name") or "").lower()
                if tag_lower == "select":
                    steps.append({"action": "select", "selector": selector, "value": val})
                elif val:
                    steps.append({"action": "fill", "selector": selector, "value": val})
        elif event_type == "submit":
            # Skip form submit events when a submit button click was already
            # recorded — the button click navigates away and the form is gone
            # by the time a second click step would run.
            if not submit_selector:
                steps.append({"action": "click", "selector": selector})

    # After OTP wait, if there's a submit needed
    if has_otp:
        # Add click on submit after OTP if we have a submit selector
        if submit_selector:
            steps.append({"action": "click", "selector": submit_selector})
        elif password_selector:
            # Guess: try [type='submit']
            steps.append({"action": "click", "selector": "[type='submit']"})
            review_items.append({
                "type": "otp_submit_inferred",
                "severity": "warning",
                "message": "OTP submit step inferred — verify the correct submit selector",
            })

    # Determine post-login success condition
    # Find the last stable URL after the login sequence
    stable_urls = [
        to_url for from_url, to_url in url_transitions
        if not _is_redirect_hop(from_url, to_url) and to_url != first_url
    ]
    if stable_urls:
        post_login_url = stable_urls[-1]
        # Add wait_for_url step — Tabby requires a "pattern" key.
        # Ensure the URL has a scheme before parsing (bare host:port strings
        # confuse urlparse, making the host land in scheme).
        _raw = post_login_url
        if not _raw.startswith("http://") and not _raw.startswith("https://"):
            _raw = "http://" + _raw
        parsed = urlparse(_raw)
        url_pattern = f"{parsed.scheme}://{parsed.netloc}/**"
        steps.append({
            "action": "wait_for_url",
            "pattern": url_pattern,
            "timeout_ms": 30000,
        })
    else:
        review_items.append({
            "type": "no_post_login_url",
            "severity": "warning",
            "message": "Could not determine post-login URL — add a wait_for or wait_for_url step manually",
        })

    # ---- Build credential_ref ----
    secret_name = f"tabby-{profile_id}"
    credential_ref = f"k8s:secret/{secret_name}"

    # ---- Build login_config ----
    login_config: dict[str, Any] = {
        "login_url": first_url,
        "credential_ref": credential_ref,
        "steps": steps,
    }
    if has_otp and otp_selector:
        login_config["otp_prompt"] = {
            "method": "chat",
            "field_selector": otp_selector,
            "timeout_ms": 120000,
        }

    # ---- Analyze HAR ----
    har_analysis = _analyze_har(har)

    # ---- Infer keepalive ----
    keepalive_actions: list[dict] = []
    keepalive_health_checks: list[dict] = []
    keepalive_confidence = "low"

    # Use post-login URL for url_check if known; always ensure scheme present.
    def _ensure_scheme(url: str, fallback_scheme: str = "http") -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{fallback_scheme}://{url}"

    if post_login_url:
        keepalive_health_checks.append({
            "type": "url_check",
            "url": _ensure_scheme(post_login_url),
            "expect_status": 200,
        })
        keepalive_confidence = "medium"
    else:
        # Fall back to a path we know returns 200 — avoid bare origin which
        # typically redirects (302) and fails an exact status check.
        keepalive_health_checks.append({
            "type": "url_check",
            "url": _ensure_scheme(origin) + "/",
            "expect_status": 200,
        })
        review_items.append({
            "type": "keepalive_weak",
            "severity": "warning",
            "message": "Keepalive health check uses origin URL — consider adding a dom_check for an authenticated-only element",
            "confidence": "low",
        })

    keepalive_config: dict[str, Any] = {
        "interval_seconds": 300,
        "actions": keepalive_actions,
        "health_checks": keepalive_health_checks,
        "policy": "all",
    }

    # ---- Infer export policy ----
    artifact_types: list[str] = ["cookies", "headers"]
    if har_analysis["has_csrf"]:
        artifact_types.append("csrf_token")

    export_policy: dict[str, Any] = {
        "artifact_types": artifact_types,
        "encryption": {"algo": "AES-256-GCM", "key_version": "v1"},
        "ttl_seconds": 3600,
        "target_urls": [origin],
    }

    # ---- Infer credential_types ----
    credential_types: dict[str, list] = {"cookies": [], "headers": []}
    if har_analysis["set_cookie_headers"]:
        credential_types["cookies"] = har_analysis["set_cookie_headers"]
    if har_analysis["auth_header_names"]:
        credential_types["headers"] = har_analysis["auth_header_names"]

    # ---- Build Application draft ----
    application_draft: dict[str, Any] = {
        "name": app_name,
        "target_urls": [origin],
        "login_config": login_config,
        "keepalive_config": keepalive_config,
        "export_policy": export_policy,
        "notification_config": {"channels": ["slack:#local-dev"]},
        "desired_session_count": 0,
        "browser_policy": {"streaming_mode": "cdp"},
    }

    # ---- Build ServiceProfile draft ----
    import time as _time
    t = _time.localtime()
    version = f"{t.tm_year % 100}.{t.tm_mon}.{t.tm_mday}"

    service_profile_draft: dict[str, Any] = {
        "profile_id": profile_id,
        "version": version,
        "login_config": login_config,  # identical to app at creation time
        "credential_types": credential_types,
        "target_domains": [domain] if domain else [],
        "extra_config": {},
    }

    # ---- Validation ----
    generator_valid = True
    has_username_step = any(
        s.get("value") == "${USERNAME}" for s in steps
    )
    has_password_step = any(
        s.get("value") == "${PASSWORD}" for s in steps
    )
    if not has_username_step and not has_otp:
        issues.append("No username field detected in recording")
    if not has_password_step and not has_otp:
        issues.append("No password field detected in recording")
        generator_valid = False

    if issues:
        for issue in issues:
            review_items.append({
                "type": "generator_issue",
                "severity": "error",
                "message": issue,
            })

    return {
        "recording": {
            "session_id": session_id,
            "source": "elicitation-login-recorder",
        },
        "application_draft": application_draft,
        "service_profile_draft": service_profile_draft,
        "review_items": review_items,
        "validation": {
            "generator_valid": generator_valid,
            "issues": issues,
        },
    }
