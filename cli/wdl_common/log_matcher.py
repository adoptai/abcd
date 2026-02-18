#!/usr/bin/env python3
"""
Log matcher for the diagnostic toolkit.

Matches APIs to network logs using fuzzy path matching with placeholder awareness.
"""

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import unquote, urlparse

from .models import MatchConfidence, MatchResult, NetworkLogEntry


def normalize_path_segment(segment: str) -> str:
    """
    Normalize a path segment for comparison.

    - Lowercase
    - URL decode
    - Remove common suffixes like 's' for pluralization differences
    """
    segment = unquote(segment).lower().strip()
    return segment


def is_placeholder_segment(segment: str) -> bool:
    """Check if a segment looks like a placeholder (e.g., {user_id}, {id})."""
    return segment.startswith("{") and segment.endswith("}")


def extract_placeholder_name(segment: str) -> str:
    """Extract the name from a placeholder segment."""
    if is_placeholder_segment(segment):
        return segment[1:-1]
    return segment


def is_likely_id_value(value: str) -> bool:
    """Check if a value looks like an ID (numeric, UUID, alphanumeric hash)."""
    if not value:
        return False

    # Numeric ID
    if value.isdigit():
        return True

    # UUID pattern
    uuid_pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    if re.match(uuid_pattern, value):
        return True

    # MongoDB ObjectId (24 hex chars)
    if re.match(r"^[0-9a-fA-F]{24}$", value):
        return True

    # General alphanumeric hash (8+ chars, mixed letters and numbers)
    if len(value) >= 8 and re.match(r"^[a-zA-Z0-9]+$", value):
        has_letters = any(c.isalpha() for c in value)
        has_numbers = any(c.isdigit() for c in value)
        if has_letters and has_numbers:
            return True

    # Short numeric string that could be an ID
    return bool(len(value) <= 10 and value.isdigit())


def segment_match_score(canonical_segment: str, original_segment: str) -> float:
    """
    Calculate match score between a canonical path segment and an original segment.

    Uses strict matching to avoid false positives.

    Returns:
        Score between 0.0 and 1.0
    """
    # If canonical is a placeholder (e.g., {user_id}, {slug}), it matches any value
    if is_placeholder_segment(canonical_segment):
        return 1.0

    # Direct comparison
    canonical_norm = normalize_path_segment(canonical_segment)
    original_norm = normalize_path_segment(original_segment)

    if canonical_norm == original_norm:
        return 1.0

    # Check for singular/plural variations
    if canonical_norm.rstrip("s") == original_norm.rstrip("s"):
        return 0.95

    # Check for common prefix/suffix patterns
    if canonical_norm in original_norm or original_norm in canonical_norm:
        len_ratio = min(len(canonical_norm), len(original_norm)) / max(
            len(canonical_norm), len(original_norm)
        )
        if len_ratio < 0.8:
            return 0.3
        return 0.6

    # Length check
    len_ratio = (
        min(len(canonical_norm), len(original_norm)) / max(len(canonical_norm), len(original_norm))
        if max(len(canonical_norm), len(original_norm)) > 0
        else 0
    )
    if len_ratio < 0.5:
        return 0.2

    # Fuzzy string matching
    similarity = SequenceMatcher(None, canonical_norm, original_norm).ratio()
    adjusted_similarity = similarity * len_ratio

    return min(adjusted_similarity, 0.7)


def calculate_path_match_score(
    canonical_path: str,
    original_url: str,
    method: str | None = None,
    api_method: str | None = None,
    ignore_method: bool = False,
) -> tuple[float, str]:
    """
    Calculate how well a canonical path matches an original URL.

    Args:
        canonical_path: The canonical API path (e.g., /users/{user_id}/posts)
        original_url: The original full URL
        method: HTTP method from the network log
        api_method: HTTP method from the API definition
        ignore_method: If True, don't penalize method mismatches

    Returns:
        Tuple of (match score between 0.0 and 1.0, rejection reason or empty string)
    """
    # Parse original URL
    parsed = urlparse(original_url)
    original_path = parsed.path

    # Handle empty paths
    if not original_path or not canonical_path:
        return 0.0, "empty_path"

    # Method mismatch handling
    method_penalty = 1.0
    if method and api_method and method.upper() != api_method.upper():
        if ignore_method:
            method_penalty = 0.8
        else:
            return 0.0, f"method_mismatch:{method}!={api_method}"

    # Split into segments
    canonical_segments = [s for s in canonical_path.strip("/").split("/") if s]
    original_segments = [s for s in original_path.strip("/").split("/") if s]

    # Must have same number of segments for a good match
    if len(canonical_segments) != len(original_segments):
        if abs(len(canonical_segments) - len(original_segments)) > 1:
            return (
                0.0,
                f"segment_count_mismatch:{len(canonical_segments)}vs{len(original_segments)}",
            )
        return (
            0.3 * method_penalty,
            f"segment_count_off_by_one:{len(canonical_segments)}vs{len(original_segments)}",
        )

    if not canonical_segments and not original_segments:
        return 1.0 * method_penalty, ""

    if not canonical_segments or not original_segments:
        return 0.0, "empty_segments"

    # Calculate segment-by-segment scores
    segment_scores = []
    for canon_seg, orig_seg in zip(canonical_segments, original_segments):
        score = segment_match_score(canon_seg, orig_seg)
        segment_scores.append(score)

    if not segment_scores:
        return 0.0, "no_segment_scores"

    # Overall score is weighted average (earlier segments are more important)
    weights = [1.0 / (i + 1) for i in range(len(segment_scores))]
    total_weight = sum(weights)

    weighted_score = sum(s * w for s, w in zip(segment_scores, weights)) / total_weight

    # Bonus for exact segment count match
    if len(canonical_segments) == len(original_segments):
        weighted_score = min(1.0, weighted_score * 1.05)

    # Strict segment score requirements
    min_seg_score = min(segment_scores)

    if min_seg_score < 0.4:
        return 0.0, f"low_segment_score:{min_seg_score:.2f}"

    if min_seg_score < 0.7:
        weighted_score *= 0.5

    low_score_segments = sum(1 for s in segment_scores if s < 0.8)
    if low_score_segments > 1:
        weighted_score *= 0.7

    weighted_score *= method_penalty

    return weighted_score, ""


def find_matching_logs(
    canonical_path: str,
    api_method: str,
    network_logs: list[NetworkLogEntry],
    min_score: float = 0.7,
    max_results: int = 10,
    ignore_method: bool = False,
) -> tuple[list[tuple[NetworkLogEntry, float]], dict[str, int]]:
    """
    Find network logs that match a canonical API path.

    Args:
        canonical_path: The canonical API path
        api_method: The API HTTP method
        network_logs: List of network log entries to search
        min_score: Minimum match score to include
        max_results: Maximum number of results to return
        ignore_method: If True, match logs regardless of HTTP method

    Returns:
        Tuple of:
        - List of (NetworkLogEntry, score) tuples, sorted by score descending
        - Dict of rejection reasons with counts
    """
    matches = []
    rejection_reasons: dict[str, int] = {}

    for log in network_logs:
        score, reason = calculate_path_match_score(
            canonical_path,
            log.api_endpoint_url,
            method=log.method,
            api_method=api_method,
            ignore_method=ignore_method,
        )

        if score >= min_score:
            matches.append((log, score))
        else:
            if not reason:
                reason = f"score_below_threshold:{score:.2f}"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    matches.sort(key=lambda x: x[1], reverse=True)

    return matches[:max_results], rejection_reasons


def determine_trailing_slash_from_logs(
    matched_logs: list[tuple[NetworkLogEntry, float]],
) -> bool | None:
    """
    Determine if the original URLs had trailing slashes.

    Uses voting among matched logs, weighted by match score.

    Args:
        matched_logs: List of (NetworkLogEntry, score) tuples

    Returns:
        True if original had trailing slash, False if not, None if uncertain
    """
    if not matched_logs:
        return None

    trailing_slash_votes = 0.0
    no_trailing_slash_votes = 0.0

    for log, score in matched_logs:
        if log.has_trailing_slash:
            trailing_slash_votes += score
        else:
            no_trailing_slash_votes += score

    total_votes = trailing_slash_votes + no_trailing_slash_votes

    if total_votes == 0:
        return None

    if trailing_slash_votes / total_votes > 0.6:
        return True
    elif no_trailing_slash_votes / total_votes > 0.6:
        return False

    # Unclear - use the best match as tie-breaker
    best_log, _ = matched_logs[0]
    return best_log.has_trailing_slash


def match_api_to_network_logs(
    api: dict[str, Any],
    network_logs: list[NetworkLogEntry],
    min_match_score: float = 0.7,
    ignore_method: bool = False,
) -> MatchResult:
    """
    Match an API to its originating network logs and determine trailing slash status.

    Args:
        api: The API dictionary from AdoptAI
        network_logs: List of network log entries
        min_match_score: Minimum score for a match
        ignore_method: If True, match logs regardless of HTTP method

    Returns:
        MatchResult with matched logs and trailing slash analysis
    """
    api_id = api.get("id", "")
    api_title = api.get("title") or api.get("name") or "Untitled"
    canonical_path = api.get("path") or api.get("canonical_api_endpoint", "")
    api_method = api.get("method", "GET")

    result = MatchResult(
        api_id=api_id,
        api_title=api_title,
        canonical_path=canonical_path,
        api_method=api_method,
        canonical_has_trailing_slash=canonical_path.endswith("/") and len(canonical_path) > 1,
    )

    if not canonical_path:
        return result

    # Find matching logs
    matched, rejection_reasons = find_matching_logs(
        canonical_path,
        api_method,
        network_logs,
        min_score=min_match_score,
        ignore_method=ignore_method,
    )

    result.rejection_reasons = rejection_reasons

    if matched:
        result.matched_logs = [log for log, _ in matched]
        result.match_scores = [score for _, score in matched]
        result.best_match_score = matched[0][1]

        # Check if the best match has a different HTTP method
        best_log = matched[0][0]
        if best_log.method and api_method:
            result.method_mismatch = best_log.method.upper() != api_method.upper()

        # Determine if original had trailing slash
        result.original_has_trailing_slash = determine_trailing_slash_from_logs(matched)

        # Check if fix is needed
        if result.original_has_trailing_slash is not None:
            if result.original_has_trailing_slash != result.canonical_has_trailing_slash:
                result.needs_fix = True
                if result.original_has_trailing_slash:
                    result.corrected_path = canonical_path.rstrip("/") + "/"
                else:
                    result.corrected_path = canonical_path.rstrip("/")

    return result


def analyze_match_confidence(match_result: MatchResult) -> MatchConfidence:
    """
    Analyze the confidence of a match result.

    Args:
        match_result: The match result to analyze

    Returns:
        MatchConfidence with detailed confidence metrics
    """
    confidence = MatchConfidence(
        score=match_result.best_match_score,
        method_match=not match_result.method_mismatch,
    )

    if match_result.matched_logs:
        best_log = match_result.matched_logs[0]
        canonical_segments = [s for s in match_result.canonical_path.strip("/").split("/") if s]
        original_segments = best_log.path_segments

        confidence.path_length_match = len(canonical_segments) == len(original_segments)
        confidence.has_placeholder_matches = any(
            is_placeholder_segment(seg) for seg in canonical_segments
        )

        # Calculate individual segment scores for the best match
        if len(canonical_segments) == len(original_segments):
            confidence.segment_scores = [
                segment_match_score(canon, orig)
                for canon, orig in zip(canonical_segments, original_segments)
            ]

    return confidence
