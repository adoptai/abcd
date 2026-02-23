#!/usr/bin/env python3
"""
Data models for the diagnostic toolkit.

Contains dataclasses for network logs, match results, and diagnostic issues.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class NetworkLogEntry:
    """Represents a network log entry from db_org_http_network_logs."""

    id: str
    org_id: str
    api_endpoint_url: str
    method: str
    base_url: str | None = None
    status: int = 0
    type: str | None = None
    headers: dict | None = None
    payload: dict | None = None
    response: dict | None = None
    api_type: str | None = None
    source_type: str | None = None
    auth_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict | None = None

    @property
    def parsed_path(self) -> str:
        """Extract path from the full URL."""
        parsed = urlparse(self.api_endpoint_url)
        return parsed.path

    @property
    def has_trailing_slash(self) -> bool:
        """Check if the original path has a trailing slash."""
        path = self.parsed_path
        # Root path is special case
        if path == "/":
            return False
        return path.endswith("/") and len(path) > 1

    @property
    def path_segments(self) -> list[str]:
        """Get path segments (excluding empty strings)."""
        return [s for s in self.parsed_path.strip("/").split("/") if s]

    @property
    def query_string(self) -> str:
        """Extract query string from URL."""
        parsed = urlparse(self.api_endpoint_url)
        return parsed.query

    @property
    def host(self) -> str:
        """Extract host from URL."""
        parsed = urlparse(self.api_endpoint_url)
        return parsed.netloc

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "org_id": self.org_id,
            "api_endpoint_url": self.api_endpoint_url,
            "method": self.method,
            "base_url": self.base_url,
            "status": self.status,
            "type": self.type,
            "headers": self.headers,
            "payload": self.payload,
            "response": self.response,
            "api_type": self.api_type,
            "source_type": self.source_type,
            "auth_type": self.auth_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "parsed_path": self.parsed_path,
            "has_trailing_slash": self.has_trailing_slash,
        }


@dataclass
class MatchResult:
    """Result of matching an API to network logs."""

    api_id: str
    api_title: str
    canonical_path: str
    api_method: str = ""
    matched_logs: list[NetworkLogEntry] = field(default_factory=list)
    match_scores: list[float] = field(default_factory=list)
    best_match_score: float = 0.0
    original_has_trailing_slash: bool | None = None
    canonical_has_trailing_slash: bool = False
    needs_fix: bool = False
    corrected_path: str | None = None
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    method_mismatch: bool = False

    @property
    def best_match(self) -> NetworkLogEntry | None:
        """Get the best matching log entry."""
        if self.matched_logs:
            return self.matched_logs[0]
        return None

    @property
    def top_rejection_reason(self) -> str | None:
        """Get the most common rejection reason."""
        if self.rejection_reasons:
            return max(self.rejection_reasons.items(), key=lambda x: x[1])[0]
        return None


@dataclass
class MatchConfidence:
    """Confidence metrics for a log match."""

    score: float
    segment_scores: list[float] = field(default_factory=list)
    method_match: bool = True
    path_length_match: bool = True
    has_placeholder_matches: bool = False

    @property
    def is_high_confidence(self) -> bool:
        """Check if this is a high confidence match."""
        return self.score >= 0.9 and self.method_match and self.path_length_match


@dataclass
class DiagnosticIssue:
    """Represents a diagnostic issue found in an API or tool."""

    id: str  # Unique issue ID
    tool_id: str | None
    tool_title: str | None
    api_id: str | None
    api_title: str | None
    issue_type: str  # trailing_slash, missing_workflow_arguments, etc.
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    confidence: float
    details: dict[str, Any] = field(default_factory=dict)
    affected_wdl_blocks: list[dict[str, Any]] = field(default_factory=list)
    suggested_fix: dict[str, Any] | None = None
    investigation_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "tool_title": self.tool_title,
            "api_id": self.api_id,
            "api_title": self.api_title,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "details": self.details,
            "affected_wdl_blocks": self.affected_wdl_blocks,
            "suggested_fix": self.suggested_fix,
            "investigation_hints": self.investigation_hints,
        }


@dataclass
class RollbackEntry:
    """Represents a single change that can be rolled back."""

    entry_type: str  # 'api' or 'tool'
    id: str
    title: str
    original_value: Any  # Original path for APIs, original WDL for tools
    new_value: Any  # New path for APIs, new WDL for tools
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "entry_type": self.entry_type,
            "id": self.id,
            "title": self.title,
            "original_value": self.original_value,
            "new_value": self.new_value,
            "timestamp": self.timestamp,
        }


@dataclass
class FixResult:
    """Result of applying a fix."""

    success: bool
    fix_id: str
    tool_id: str | None
    api_id: str | None
    message: str
    changes_applied: list[dict[str, Any]] = field(default_factory=list)
    evaluation_result: dict[str, Any] | None = None
    rollback_entry: RollbackEntry | None = None


# Issue type constants
class IssueType:
    """Constants for issue types."""

    TRAILING_SLASH_MISMATCH = "trailing_slash_mismatch"
    MISSING_WORKFLOW_ARGUMENTS = "missing_workflow_arguments_prefix"
    MISSING_QUERY_PARAMETERS = "missing_query_parameters"
    MISSING_REQUIRED_INPUTS = "missing_required_inputs"
    API_PATH_MISMATCH = "api_path_mismatch"
    METHOD_MISMATCH = "method_mismatch"
    ORPHANED_API_REFERENCE = "orphaned_api_reference"
    INVALID_WDL_STRUCTURE = "invalid_wdl_structure"


class Severity:
    """Constants for severity levels."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
