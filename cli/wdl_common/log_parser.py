#!/usr/bin/env python3
"""
Network log parser for the diagnostic toolkit.

Parses network log entries from various formats into NetworkLogEntry objects.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import NetworkLogEntry


def parse_log_entry(entry: Dict[str, Any]) -> Optional[NetworkLogEntry]:
    """
    Parse a single log entry dictionary into a NetworkLogEntry.
    
    Args:
        entry: Dictionary containing log entry data
        
    Returns:
        NetworkLogEntry or None if invalid
    """
    if not isinstance(entry, dict):
        return None
    
    # Handle various field name formats (snake_case, camelCase)
    log_entry = NetworkLogEntry(
        id=str(entry.get('id', '')),
        org_id=str(entry.get('org_id', entry.get('orgId', ''))),
        api_endpoint_url=entry.get('api_endpoint_url', entry.get('apiEndpointUrl', '')),
        method=entry.get('method', 'GET'),
        base_url=entry.get('base_url', entry.get('baseUrl')),
        status=entry.get('status', 0),
        type=entry.get('type'),
        headers=entry.get('headers'),
        payload=entry.get('payload'),
        response=entry.get('response'),
        api_type=entry.get('api_type', entry.get('apiType')),
        source_type=entry.get('source_type', entry.get('sourceType')),
        auth_type=entry.get('auth_type', entry.get('authType')),
        created_at=entry.get('created_at', entry.get('createdAt')),
        updated_at=entry.get('updated_at', entry.get('updatedAt')),
        metadata=entry.get('metadata', entry.get('log_metadata'))
    )
    
    if log_entry.api_endpoint_url:
        return log_entry
    return None


def load_network_logs(json_file_path: str) -> List[NetworkLogEntry]:
    """
    Load network logs from a JSON file.
    
    The JSON file should contain an array of network log entries
    with fields matching the HttpNetworkLog model.
    
    Args:
        json_file_path: Path to the JSON file containing network logs
        
    Returns:
        List of NetworkLogEntry objects
        
    Raises:
        ValueError: If file cannot be loaded or parsed
    """
    file_path = Path(json_file_path)
    
    if not file_path.exists():
        raise ValueError(f"Network logs file not found: {json_file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in network logs file: {e}")
    
    # Handle different JSON structures
    logs_data = []
    if isinstance(data, list):
        logs_data = data
    elif isinstance(data, dict):
        # Try common keys for arrays
        for key in ['logs', 'data', 'items', 'network_logs', 'results']:
            if key in data and isinstance(data[key], list):
                logs_data = data[key]
                break
        if not logs_data:
            # Single entry
            logs_data = [data]
    
    network_logs = []
    for entry in logs_data:
        log_entry = parse_log_entry(entry)
        if log_entry:
            network_logs.append(log_entry)
    
    return network_logs


def save_logs_to_file(logs: List[NetworkLogEntry], output_file: str) -> None:
    """
    Save network logs to a JSON file.
    
    Args:
        logs: List of NetworkLogEntry objects
        output_file: Path to the output file
    """
    logs_data = [log.to_dict() for log in logs]
    
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(logs_data, f, indent=2, default=str)
    
    print(f"   💾 Saved {len(logs)} logs to: {output_path}")


def filter_logs_by_method(
    logs: List[NetworkLogEntry],
    methods: List[str]
) -> List[NetworkLogEntry]:
    """Filter logs by HTTP method."""
    methods_upper = [m.upper() for m in methods]
    return [log for log in logs if log.method.upper() in methods_upper]


def filter_logs_by_path_pattern(
    logs: List[NetworkLogEntry],
    pattern: str
) -> List[NetworkLogEntry]:
    """Filter logs by path pattern (substring match)."""
    pattern_lower = pattern.lower()
    return [log for log in logs if pattern_lower in log.parsed_path.lower()]


def filter_logs_by_host(
    logs: List[NetworkLogEntry],
    host: str
) -> List[NetworkLogEntry]:
    """Filter logs by host (substring match)."""
    host_lower = host.lower()
    return [log for log in logs if host_lower in log.host.lower()]


def get_unique_paths(logs: List[NetworkLogEntry]) -> List[str]:
    """Get unique paths from logs (without parameters)."""
    paths = set()
    for log in logs:
        paths.add(log.parsed_path)
    return sorted(paths)


def get_unique_hosts(logs: List[NetworkLogEntry]) -> List[str]:
    """Get unique hosts from logs."""
    hosts = set()
    for log in logs:
        hosts.add(log.host)
    return sorted(hosts)


def get_logs_stats(logs: List[NetworkLogEntry]) -> Dict[str, Any]:
    """Get statistics about the logs."""
    if not logs:
        return {
            'total': 0,
            'methods': {},
            'hosts': [],
            'unique_paths': 0,
        }
    
    methods: Dict[str, int] = {}
    for log in logs:
        method = log.method.upper()
        methods[method] = methods.get(method, 0) + 1
    
    return {
        'total': len(logs),
        'methods': methods,
        'hosts': get_unique_hosts(logs),
        'unique_paths': len(get_unique_paths(logs)),
    }









