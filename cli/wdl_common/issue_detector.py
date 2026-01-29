#!/usr/bin/env python3
"""
Issue detector for the diagnostic toolkit.

Detects various issues in APIs and tool WDLs by analyzing patterns and comparing
to network logs.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .models import (
    DiagnosticIssue,
    IssueType,
    MatchResult,
    NetworkLogEntry,
    Severity,
)


def detect_trailing_slash_issue(
    match_result: MatchResult,
    api: Dict[str, Any],
) -> Optional[DiagnosticIssue]:
    """
    Detect trailing slash mismatch between API canonical path and network logs.
    
    Args:
        match_result: Result of matching API to network logs
        api: The API dictionary
        
    Returns:
        DiagnosticIssue if issue found, None otherwise
    """
    if not match_result.needs_fix:
        return None
    
    if match_result.original_has_trailing_slash is None:
        return None
    
    return DiagnosticIssue(
        id=f"issue-ts-{api.get('id', 'unknown')[:8]}",
        tool_id=None,
        tool_title=None,
        api_id=api.get('id'),
        api_title=api.get('title') or api.get('name'),
        issue_type=IssueType.TRAILING_SLASH_MISMATCH,
        severity=Severity.HIGH,
        confidence=match_result.best_match_score,
        details={
            'current_canonical_path': match_result.canonical_path,
            'expected_path': match_result.corrected_path,
            'canonical_has_trailing_slash': match_result.canonical_has_trailing_slash,
            'original_has_trailing_slash': match_result.original_has_trailing_slash,
            'matched_log_url': match_result.best_match.api_endpoint_url if match_result.best_match else None,
            'matched_log_method': match_result.best_match.method if match_result.best_match else None,
            'match_score': match_result.best_match_score,
        },
        suggested_fix={
            'type': 'path_update',
            'changes': [
                {
                    'field': 'canonical_api_endpoint',
                    'old_value': match_result.canonical_path,
                    'new_value': match_result.corrected_path,
                },
                {
                    'field': 'path',
                    'old_value': match_result.canonical_path,
                    'new_value': match_result.corrected_path,
                },
            ],
        },
        investigation_hints=[
            f"Original URL: {match_result.best_match.api_endpoint_url if match_result.best_match else 'Unknown'}",
            f"Match score: {match_result.best_match_score:.2f}",
            "Verify the matched log is from the correct organization",
        ],
    )


def detect_missing_workflow_arguments(wdl: List[Dict]) -> List[DiagnosticIssue]:
    """
    Detect missing 'workflow_arguments.' prefix in URL placeholders.
    
    The 'url' field should use {workflow_arguments.param} format,
    while 'canonical_api_endpoint' uses bare {param} format.
    
    Args:
        wdl: The WDL operations list
        
    Returns:
        List of DiagnosticIssue objects
    """
    issues = []
    
    # Pattern to find bare placeholders (not workflow_arguments or security_params)
    bare_placeholder_pattern = re.compile(r'\{(?!workflow_arguments\.)(?!security_params\.)([a-zA-Z_][a-zA-Z0-9_]*)\}')
    
    for i, op in enumerate(wdl):
        if op.get('operation') != 'REST':
            continue
        
        url = op.get('url', '')
        if not url:
            continue
        
        # Find bare placeholders in URL
        matches = bare_placeholder_pattern.findall(url)
        
        if matches:
            # Filter out known non-workflow_arguments params
            known_non_workflow = {'status_codes'}
            actual_issues = [m for m in matches if m not in known_non_workflow]
            
            if actual_issues:
                issues.append(DiagnosticIssue(
                    id=f"issue-wfa-{i}",
                    tool_id=None,
                    tool_title=None,
                    api_id=None,
                    api_title=None,
                    issue_type=IssueType.MISSING_WORKFLOW_ARGUMENTS,
                    severity=Severity.HIGH,
                    confidence=1.0,
                    details={
                        'operation_index': i,
                        'operation_id': op.get('id'),
                        'current_url': url,
                        'bare_placeholders': actual_issues,
                    },
                    affected_wdl_blocks=[{
                        'block_index': i,
                        'operation': 'REST',
                        'fields_to_update': ['url'],
                    }],
                    suggested_fix={
                        'type': 'placeholder_prefix',
                        'changes': [
                            {
                                'param': param,
                                'old_pattern': f'{{{param}}}',
                                'new_pattern': f'{{workflow_arguments.{param}}}',
                            }
                            for param in actual_issues
                        ],
                    },
                    investigation_hints=[
                        f"Found {len(actual_issues)} bare placeholder(s): {', '.join(actual_issues)}",
                        "URL field should use {workflow_arguments.param} format",
                        "canonical_api_endpoint should use bare {param} format",
                    ],
                ))
    
    return issues


def detect_missing_query_parameters(
    wdl: List[Dict],
    matched_logs: List[NetworkLogEntry],
) -> List[DiagnosticIssue]:
    """
    Detect query parameters in network logs that are missing from WDL.
    
    Args:
        wdl: The WDL operations list
        matched_logs: Network logs that matched this API
        
    Returns:
        List of DiagnosticIssue objects
    """
    issues = []
    
    if not matched_logs:
        return issues
    
    # Extract query params from logs
    log_params = set()
    for log in matched_logs[:5]:  # Check first 5 matched logs
        parsed = urlparse(log.api_endpoint_url)
        if parsed.query:
            params = parse_qs(parsed.query)
            log_params.update(params.keys())
    
    if not log_params:
        return issues
    
    # Check each REST operation
    for i, op in enumerate(wdl):
        if op.get('operation') != 'REST':
            continue
        
        wdl_params = set(op.get('query_parameters', {}).keys())
        missing_params = log_params - wdl_params
        
        if missing_params:
            issues.append(DiagnosticIssue(
                id=f"issue-qp-{i}",
                tool_id=None,
                tool_title=None,
                api_id=None,
                api_title=None,
                issue_type=IssueType.MISSING_QUERY_PARAMETERS,
                severity=Severity.MEDIUM,
                confidence=0.8,  # Lower confidence since params might be optional
                details={
                    'operation_index': i,
                    'operation_id': op.get('id'),
                    'missing_params': list(missing_params),
                    'existing_params': list(wdl_params),
                    'log_params': list(log_params),
                },
                affected_wdl_blocks=[{
                    'block_index': i,
                    'operation': 'REST',
                    'fields_to_update': ['query_parameters'],
                }],
                suggested_fix={
                    'type': 'add_query_params',
                    'params_to_add': list(missing_params),
                },
                investigation_hints=[
                    f"Found {len(missing_params)} query param(s) in logs but not in WDL",
                    "These parameters may be optional - verify before adding",
                    f"Log params: {', '.join(log_params)}",
                ],
            ))
    
    return issues


def detect_missing_required_inputs(wdl: List[Dict]) -> List[DiagnosticIssue]:
    """
    Detect parameters referenced in WDL but not declared in required_inputs.
    
    Args:
        wdl: The WDL operations list
        
    Returns:
        List of DiagnosticIssue objects
    """
    issues = []
    
    # Extract all workflow_arguments references
    workflow_arg_pattern = re.compile(r'\{workflow_arguments\.([a-zA-Z_][a-zA-Z0-9_]*)\}')
    
    referenced_params = set()
    for op in wdl:
        op_str = str(op)
        matches = workflow_arg_pattern.findall(op_str)
        referenced_params.update(matches)
    
    # Find required_inputs block
    declared_params = set()
    for op in wdl:
        if 'required_inputs' in op:
            required_inputs = op.get('required_inputs', {})
            if isinstance(required_inputs, dict):
                declared_params.update(required_inputs.keys())
    
    missing_inputs = referenced_params - declared_params
    
    if missing_inputs:
        issues.append(DiagnosticIssue(
            id="issue-ri-0",
            tool_id=None,
            tool_title=None,
            api_id=None,
            api_title=None,
            issue_type=IssueType.MISSING_REQUIRED_INPUTS,
            severity=Severity.HIGH,
            confidence=1.0,
            details={
                'missing_inputs': list(missing_inputs),
                'declared_inputs': list(declared_params),
                'referenced_inputs': list(referenced_params),
            },
            suggested_fix={
                'type': 'add_required_inputs',
                'inputs_to_add': list(missing_inputs),
            },
            investigation_hints=[
                f"Found {len(missing_inputs)} referenced param(s) not in required_inputs",
                "Add these to the required_inputs block with appropriate types and defaults",
            ],
        ))
    
    return issues


def detect_wdl_structure_issues(wdl: List[Dict]) -> List[DiagnosticIssue]:
    """
    Detect structural issues in WDL.
    
    Args:
        wdl: The WDL operations list
        
    Returns:
        List of DiagnosticIssue objects
    """
    issues = []
    seen_ids = set()
    
    if not isinstance(wdl, list):
        issues.append(DiagnosticIssue(
            id="issue-struct-0",
            tool_id=None,
            tool_title=None,
            api_id=None,
            api_title=None,
            issue_type=IssueType.INVALID_WDL_STRUCTURE,
            severity=Severity.CRITICAL,
            confidence=1.0,
            details={'error': 'WDL must be a JSON array'},
            investigation_hints=["WDL should be a list of operation objects"],
        ))
        return issues
    
    for i, op in enumerate(wdl):
        if not isinstance(op, dict):
            issues.append(DiagnosticIssue(
                id=f"issue-struct-{i}",
                tool_id=None,
                tool_title=None,
                api_id=None,
                api_title=None,
                issue_type=IssueType.INVALID_WDL_STRUCTURE,
                severity=Severity.CRITICAL,
                confidence=1.0,
                details={'error': f'Operation {i} must be a JSON object'},
                investigation_hints=[f"Operation at index {i} is not a valid object"],
            ))
            continue
        
        # Skip metadata blocks
        if any(key in op for key in ['metadata', 'required_inputs', 'suggestions', 'statement']):
            continue
        
        op_id = op.get('id')
        operation = op.get('operation')
        
        if not op_id:
            issues.append(DiagnosticIssue(
                id=f"issue-struct-id-{i}",
                tool_id=None,
                tool_title=None,
                api_id=None,
                api_title=None,
                issue_type=IssueType.INVALID_WDL_STRUCTURE,
                severity=Severity.HIGH,
                confidence=1.0,
                details={'error': f"Operation {i} missing 'id' field"},
                investigation_hints=["Each operation must have a unique 'id' field"],
            ))
        elif op_id in seen_ids:
            issues.append(DiagnosticIssue(
                id=f"issue-struct-dup-{i}",
                tool_id=None,
                tool_title=None,
                api_id=None,
                api_title=None,
                issue_type=IssueType.INVALID_WDL_STRUCTURE,
                severity=Severity.HIGH,
                confidence=1.0,
                details={'error': f"Operation {i} has duplicate 'id': '{op_id}'"},
                investigation_hints=["Each operation 'id' must be unique"],
            ))
        else:
            seen_ids.add(op_id)
        
        if not operation:
            issues.append(DiagnosticIssue(
                id=f"issue-struct-op-{i}",
                tool_id=None,
                tool_title=None,
                api_id=None,
                api_title=None,
                issue_type=IssueType.INVALID_WDL_STRUCTURE,
                severity=Severity.HIGH,
                confidence=1.0,
                details={'error': f"Operation {i} missing 'operation' field"},
                investigation_hints=["Each operation must have an 'operation' type"],
            ))
    
    return issues


def detect_all_issues(
    wdl: List[Dict],
    api: Optional[Dict] = None,
    match_result: Optional[MatchResult] = None,
    matched_logs: Optional[List[NetworkLogEntry]] = None,
) -> List[DiagnosticIssue]:
    """
    Run all issue detectors and return combined results.
    
    Args:
        wdl: The WDL operations list
        api: Optional API dictionary
        match_result: Optional match result from log matching
        matched_logs: Optional list of matched network logs
        
    Returns:
        List of all detected DiagnosticIssue objects
    """
    all_issues = []
    
    # Structural issues (always run)
    all_issues.extend(detect_wdl_structure_issues(wdl))
    
    # Missing workflow_arguments prefix
    all_issues.extend(detect_missing_workflow_arguments(wdl))
    
    # Missing required_inputs
    all_issues.extend(detect_missing_required_inputs(wdl))
    
    # Trailing slash (if we have match result)
    if match_result and api:
        issue = detect_trailing_slash_issue(match_result, api)
        if issue:
            all_issues.append(issue)
    
    # Missing query parameters (if we have matched logs)
    if matched_logs:
        all_issues.extend(detect_missing_query_parameters(wdl, matched_logs))
    
    return all_issues









