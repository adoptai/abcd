#!/usr/bin/env python3
"""
Error Pattern Recognition and Enhanced Error Messages.

Translates cryptic API errors into actionable guidance for agents.
"""

import re
from typing import Dict, List, Optional, Tuple


# Error pattern catalog with solutions
ERROR_PATTERNS: Dict[str, Dict[str, str]] = {
    # Issue 2.2: required_inputs format mismatch
    r"required_inputs.*Input should be a valid list": {
        "issue": "required_inputs format mismatch",
        "explanation": (
            "The platform expects 'required_inputs' as a list of JSON strings, not a dict."
        ),
        "fix": "Run: python cli/validate.py workflow-id --auto-fix",
        "auto_fixable": "true",
    },
    r"pydantic.*ValidationError.*required_inputs.*list_type": {
        "issue": "required_inputs format mismatch",
        "explanation": (
            "The platform expects 'required_inputs' as a list of JSON strings.\n"
            "   Current format: {\"field\": {...}}\n"
            "   Required format: [\"{\\\"field\\\": {...}}\"]"
        ),
        "fix": "Run: python cli/validate.py workflow-id --auto-fix",
        "auto_fixable": "true",
    },

    # Issue 2.3: Tool title validation
    r"tool names? have invalid characters": {
        "issue": "Tool title contains invalid characters",
        "explanation": (
            "Tool titles used in PROMPT_AND_TOOLS_AGENT must match pattern:\n"
            "   ^[a-zA-Z0-9_-]{1,128}$\n"
            "   ✅ Good: my-tool, tool_name, Tool123\n"
            "   ❌ Bad: My Tool, tool(name), tool name"
        ),
        "fix": "Rename the tool title or run: python cli/validate.py workflow-id --auto-fix",
        "auto_fixable": "true",
    },
    r"Invalid tool name": {
        "issue": "Tool title contains invalid characters",
        "explanation": (
            "Tool names must only contain letters, numbers, hyphens, and underscores."
        ),
        "fix": "Use --auto-fix flag with validate.py to automatically fix titles",
        "auto_fixable": "true",
    },

    # Issue 2.4: Empty WDL field
    r"empty wdl field": {
        "issue": "WDL not saved to remote",
        "explanation": (
            "The draft was saved but the WDL field is empty on remote.\n"
            "   This can happen when save fails partially."
        ),
        "fix": "Re-save the draft: python cli/save.py workflow-id",
        "auto_fixable": "false",
    },

    # Issue 3.3: Session expired
    r"Odoo Session Expired": {
        "issue": "Session cookie expired",
        "explanation": "The Odoo session has expired and needs to be refreshed.",
        "fix": (
            "Update security_params in adopt_profile.json with fresh cookies.\n"
            "   Get new cookies from browser DevTools (Network tab)."
        ),
        "auto_fixable": "false",
    },
    r"session_id.*invalid|Invalid session": {
        "issue": "Session cookie expired or invalid",
        "explanation": "The session cookie is no longer valid.",
        "fix": "Update security_params.cookie in adopt_profile.json with a fresh cookie",
        "auto_fixable": "false",
    },

    # Action not found
    r"Action not found|404.*action": {
        "issue": "Action not found on remote",
        "explanation": (
            "The action_id in metadata doesn't match any action on the platform.\n"
            "   The action may have been deleted or the ID is incorrect."
        ),
        "fix": (
            "Either:\n"
            "   1. Reconnect to existing action: python cli/reconnect.py workflow-id ACTION_ID\n"
            "   2. Create new action: remove action_id from metadata.json and save again"
        ),
        "auto_fixable": "false",
    },

    # Version not found
    r"Version.*not found|version does not exist": {
        "issue": "Version not found",
        "explanation": "The specified version doesn't exist on the remote.",
        "fix": (
            "List available versions: python cli/versions.py workflow-id\n"
            "   Then checkout a valid version."
        ),
        "auto_fixable": "false",
    },

    # WDL validation errors
    r"Invalid WDL|WDL validation failed": {
        "issue": "WDL validation failed on platform",
        "explanation": "The WDL contains errors that the platform couldn't process.",
        "fix": (
            "Run local validation first: python cli/validate.py workflow-id\n"
            "   Fix any errors before uploading."
        ),
        "auto_fixable": "false",
    },

    # JSON syntax errors
    r"JSONDecodeError|Expecting.*line.*column": {
        "issue": "Invalid JSON syntax",
        "explanation": "The widdle.json file has a JSON syntax error.",
        "fix": (
            "Check the JSON for:\n"
            "   - Missing commas between elements\n"
            "   - Trailing commas (not allowed in JSON)\n"
            "   - Unescaped quotes in strings\n"
            "   - Mismatched brackets"
        ),
        "auto_fixable": "false",
    },

    # Network errors
    r"Network error|ConnectionError|Timeout": {
        "issue": "Network connection failed",
        "explanation": "Could not connect to the AdoptAI platform.",
        "fix": (
            "Check:\n"
            "   1. Internet connection\n"
            "   2. Platform status\n"
            "   3. Firewall/proxy settings"
        ),
        "auto_fixable": "false",
    },

    # Authentication errors
    r"401.*Unauthorized|Invalid token|Token expired": {
        "issue": "Authentication failed",
        "explanation": "The authentication token is invalid or expired.",
        "fix": (
            "Re-authenticate:\n"
            "   1. Delete cached token\n"
            "   2. Run command again to trigger new login"
        ),
        "auto_fixable": "false",
    },

    # Rate limiting
    r"429.*Too Many Requests|Rate limit": {
        "issue": "Rate limit exceeded",
        "explanation": "Too many API requests in a short time.",
        "fix": "Wait a few minutes and try again.",
        "auto_fixable": "false",
    },

    # Duplicate action
    r"already exists|duplicate.*action": {
        "issue": "Action already exists",
        "explanation": "An action with this title already exists on the platform.",
        "fix": (
            "Either:\n"
            "   1. Use a different title\n"
            "   2. Reconnect to existing: python cli/reconnect.py workflow-id ACTION_ID"
        ),
        "auto_fixable": "false",
    },
}


def enhance_error_message(raw_error: str) -> str:
    """
    Parse error and provide actionable guidance.

    Args:
        raw_error: Raw error message from API or system

    Returns:
        Enhanced error message with explanation and fix
    """
    for pattern, info in ERROR_PATTERNS.items():
        if re.search(pattern, raw_error, re.IGNORECASE):
            lines = [
                f"❌ ERROR: {info['issue']}",
                "",
                "📋 Explanation:",
                f"   {info['explanation']}",
                "",
                "💡 Fix:",
                f"   {info['fix']}",
            ]

            if info.get("auto_fixable") == "true":
                lines.extend([
                    "",
                    "🔧 This issue can be auto-fixed with the --auto-fix flag",
                ])

            return "\n".join(lines)

    # No pattern matched - return formatted raw error
    return f"❌ Error: {raw_error}"


def detect_error_type(raw_error: str) -> Optional[str]:
    """
    Detect the type of error from a raw error message.

    Args:
        raw_error: Raw error message

    Returns:
        Error type identifier or None
    """
    for pattern, info in ERROR_PATTERNS.items():
        if re.search(pattern, raw_error, re.IGNORECASE):
            return info["issue"]
    return None


def is_auto_fixable(raw_error: str) -> bool:
    """
    Check if an error can be auto-fixed.

    Args:
        raw_error: Raw error message

    Returns:
        True if error can be auto-fixed
    """
    for pattern, info in ERROR_PATTERNS.items():
        if re.search(pattern, raw_error, re.IGNORECASE):
            return info.get("auto_fixable") == "true"
    return False


def get_fix_suggestion(raw_error: str) -> Optional[str]:
    """
    Get fix suggestion for an error.

    Args:
        raw_error: Raw error message

    Returns:
        Fix suggestion or None
    """
    for pattern, info in ERROR_PATTERNS.items():
        if re.search(pattern, raw_error, re.IGNORECASE):
            return info["fix"]
    return None


def format_api_error(
    status_code: int,
    response_text: str,
    operation: str = "API call",
) -> str:
    """
    Format an API error response with enhanced messaging.

    Args:
        status_code: HTTP status code
        response_text: Response body text
        operation: Description of what operation was attempted

    Returns:
        Formatted error message
    """
    # Try to enhance with pattern matching
    enhanced = enhance_error_message(response_text)

    # If no pattern matched, provide generic guidance based on status code
    if enhanced.startswith("❌ Error:"):
        if status_code == 400:
            enhanced = (
                f"❌ Bad Request during {operation}\n\n"
                f"The request was malformed or invalid.\n"
                f"Details: {response_text}\n\n"
                f"💡 Check the request parameters and try again."
            )
        elif status_code == 401:
            enhanced = (
                f"❌ Authentication failed during {operation}\n\n"
                f"Your session may have expired.\n\n"
                f"💡 Re-authenticate and try again."
            )
        elif status_code == 403:
            enhanced = (
                f"❌ Permission denied during {operation}\n\n"
                f"You don't have access to this resource.\n"
                f"Details: {response_text}"
            )
        elif status_code == 404:
            enhanced = (
                f"❌ Resource not found during {operation}\n\n"
                f"The requested resource doesn't exist.\n"
                f"Details: {response_text}\n\n"
                f"💡 Check that the ID/path is correct."
            )
        elif status_code == 500:
            enhanced = (
                f"❌ Server error during {operation}\n\n"
                f"The platform encountered an internal error.\n"
                f"Details: {response_text}\n\n"
                f"💡 Try again later. If the issue persists, contact support."
            )
        else:
            enhanced = (
                f"❌ Error {status_code} during {operation}\n\n"
                f"Details: {response_text}"
            )

    return enhanced





