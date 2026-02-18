#!/usr/bin/env python3
"""
Trace Analyzer for WDL test execution.

Analyzes execution traces to identify errors and suggest fixes.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceIssue:
    """Represents an issue found in a trace."""

    operation_id: str
    operation_type: str
    issue_type: str  # "error", "warning", "suggestion"
    message: str
    details: dict[str, Any] | None = field(default=None)
    suggested_fix: str | None = field(default=None)


class TraceAnalyzer:
    """Analyzes WDL execution traces to identify issues."""

    def __init__(self) -> None:
        """Initialize trace analyzer."""
        self.common_error_patterns = self._build_error_patterns()

    def _build_error_patterns(self) -> dict[str, dict[str, str]]:
        """Build mapping of error patterns to fixes."""
        return {
            "401": {
                "issue_type": "error",
                "message": "Authentication failed - check security_params",
                "suggested_fix": ("Verify API credentials in adopt_profile.json security_params"),
            },
            "403": {
                "issue_type": "error",
                "message": "Authorization failed - insufficient permissions",
                "suggested_fix": "Check API permissions or add required scopes",
            },
            "404": {
                "issue_type": "error",
                "message": "Endpoint not found - check URL",
                "suggested_fix": "Verify the URL path matches the API specification",
            },
            "500": {
                "issue_type": "error",
                "message": "Server error from API",
                "suggested_fix": "Check payload format and required fields",
            },
            "timeout": {
                "issue_type": "error",
                "message": "Request timed out",
                "suggested_fix": "Increase timeout or check API availability",
            },
            "jq_error": {
                "issue_type": "error",
                "message": "JQ filter syntax error",
                "suggested_fix": "Review JQ filter syntax in the JQ_FILTER operation",
            },
            "missing_input": {
                "issue_type": "error",
                "message": "Required workflow argument not provided",
                "suggested_fix": (
                    "Add the missing parameter to required_inputs or workflow_params"
                ),
            },
        }

    def analyze_trace(self, trace: dict[str, Any]) -> list[TraceIssue]:
        """
        Analyze an execution trace for issues.

        Args:
            trace: Execution trace dictionary

        Returns:
            List of identified issues
        """
        issues: list[TraceIssue] = []

        # Check overall status
        if trace.get("status") == "error" or not trace.get("status"):
            issues.append(
                TraceIssue(
                    operation_id="overall",
                    operation_type="workflow",
                    issue_type="error",
                    message=trace.get("error_message", "Workflow failed"),
                    details=trace,
                )
            )

        # Analyze individual operations
        operations = trace.get("operations", [])
        for op in operations:
            op_issues = self._analyze_operation(op)
            issues.extend(op_issues)

        # Check for REST response issues
        rest_responses = trace.get("rest_responses", [])
        for resp in rest_responses:
            resp_issues = self._analyze_rest_response(resp)
            issues.extend(resp_issues)

        return issues

    def _analyze_operation(self, op: dict[str, Any]) -> list[TraceIssue]:
        """Analyze a single operation from the trace."""
        issues: list[TraceIssue] = []

        op_id = op.get("id", "unknown")
        op_type = op.get("operation", "unknown")
        status = op.get("status", "unknown")
        error = op.get("error")

        if status == "error" or error:
            issue = TraceIssue(
                operation_id=op_id,
                operation_type=op_type,
                issue_type="error",
                message=error or f"Operation {op_id} failed",
                details=op,
            )

            # Try to match error pattern
            for pattern, fix_info in self.common_error_patterns.items():
                if error and pattern.lower() in str(error).lower():
                    issue.suggested_fix = fix_info["suggested_fix"]
                    break

            issues.append(issue)

        return issues

    def _analyze_rest_response(self, resp: dict[str, Any]) -> list[TraceIssue]:
        """Analyze REST response for issues."""
        issues: list[TraceIssue] = []

        status_code = resp.get("status_code", 0)
        op_id = resp.get("operation_id", "unknown")

        # Check for error status codes
        if status_code >= 400:
            pattern = str(status_code)[:3] if status_code < 500 else "500"
            fix_info = self.common_error_patterns.get(pattern, {})

            issues.append(
                TraceIssue(
                    operation_id=op_id,
                    operation_type="REST",
                    issue_type="error",
                    message=f"HTTP {status_code}: {resp.get('error', 'Request failed')}",
                    details=resp,
                    suggested_fix=fix_info.get("suggested_fix"),
                )
            )

        return issues

    def generate_fix_suggestions(
        self,
        issues: list[TraceIssue],
        wdl: list[dict[str, Any]],
    ) -> str:
        """
        Generate Cursor-friendly fix suggestions.

        Args:
            issues: List of identified issues
            wdl: Current WDL configuration

        Returns:
            Markdown-formatted fix suggestions
        """
        lines: list[str] = []
        lines.append("# WDL Fix Suggestions\n")
        lines.append("Based on trace analysis, the following issues were identified:\n")

        for i, issue in enumerate(issues, 1):
            lines.append(f"## Issue {i}: {issue.message}\n")
            lines.append(f"- **Operation**: `{issue.operation_id}` ({issue.operation_type})")
            lines.append(f"- **Type**: {issue.issue_type}")

            if issue.suggested_fix:
                lines.append("\n### Suggested Fix\n")
                lines.append(f"{issue.suggested_fix}\n")

            if issue.details:
                lines.append("\n### Details\n")
                lines.append(f"```json\n{json.dumps(issue.details, indent=2)}\n```\n")

            lines.append("---\n")

        # Add current WDL for context
        lines.append("\n## Current WDL\n")
        lines.append("```json")
        lines.append(json.dumps(wdl, indent=2))
        lines.append("```\n")

        return "\n".join(lines)

    def load_trace_from_file(self, trace_path: Path) -> dict[str, Any]:
        """
        Load a trace from a file.

        Args:
            trace_path: Path to trace JSON file

        Returns:
            Trace dictionary
        """
        return json.loads(trace_path.read_text())
