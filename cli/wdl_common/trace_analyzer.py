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
            "sandbox_expired": {
                "issue_type": "error",
                "message": "Sandbox session expired or was terminated",
                "suggested_fix": (
                    "Increase sandbox timeout_minutes or check keepalive configuration"
                ),
            },
            "sandbox_creation_failed": {
                "issue_type": "error",
                "message": "Failed to create sandbox container",
                "suggested_fix": (
                    "Check OpenSandbox cluster availability and image name. "
                    "Use 'python:3.11' for SANDBOX or ensure adopt-lambda-runtime is available."
                ),
            },
            "lambda_not_found": {
                "issue_type": "error",
                "message": "Lambda not found in registry",
                "suggested_fix": (
                    "Check lambda_name or lambda_id in the EXECUTE_LAMBDA step. "
                    "Run 'python cli/manage_lambda.py --list' to see available lambdas."
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

            # Add operation-specific suggestions
            if op_type == "EXECUTE_LAMBDA" and not issue.suggested_fix:
                issue.suggested_fix = (
                    "Check lambda execution logs with: "
                    "python cli/lambda_logs.py --execution-id <exec_id>"
                )
            elif op_type == "SANDBOX" and not issue.suggested_fix:
                action = op.get("action", "unknown")
                issue.suggested_fix = (
                    f"SANDBOX '{action}' step failed. "
                    "Check sandbox session details in the execution trace."
                )

            issues.append(issue)

        # Add info-level details for sandbox/lambda operations
        if op_type == "EXECUTE_LAMBDA" and status != "error":
            output = op.get("output", {})
            if isinstance(output, dict):
                exec_id = output.get("execution_id")
                exit_code = output.get("exit_code")
                duration = output.get("duration_ms")
                downloaded = output.get("downloaded_files", [])
                if exec_id:
                    details = {
                        "execution_id": exec_id,
                        "exit_code": exit_code,
                        "duration_ms": duration,
                        "downloaded_files_count": len(downloaded),
                    }
                    issues.append(
                        TraceIssue(
                            operation_id=op_id,
                            operation_type=op_type,
                            issue_type="suggestion",
                            message=f"Lambda executed in {duration}ms (exit={exit_code})",
                            details=details,
                            suggested_fix=(
                                f"Full logs: python cli/lambda_logs.py --execution-id {exec_id}"
                                if exit_code != 0
                                else None
                            ),
                        )
                    )

        elif op_type == "SANDBOX" and status != "error":
            output = op.get("output", {})
            if isinstance(output, dict):
                sandbox_id = output.get("sandbox_id")
                endpoints = output.get("endpoints", {})
                downloaded = output.get("downloaded_files", [])
                if sandbox_id or endpoints or downloaded:
                    issues.append(
                        TraceIssue(
                            operation_id=op_id,
                            operation_type=op_type,
                            issue_type="suggestion",
                            message=(
                                f"Sandbox session: {sandbox_id or 'active'}, "
                                f"endpoints: {len(endpoints)}, "
                                f"downloaded: {len(downloaded)} files"
                            ),
                            details=output,
                        )
                    )

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
