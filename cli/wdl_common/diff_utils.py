#!/usr/bin/env python3
"""
Diff utilities for the diagnostic toolkit.

Provides colored diff generation for WDL and API changes.
"""

import json
from difflib import unified_diff
from typing import Any, Dict, List, Optional

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False
    # Fallback if colorama not available
    class Fore:
        RED = ''
        GREEN = ''
        YELLOW = ''
        CYAN = ''
        WHITE = ''
        RESET = ''
    class Style:
        BRIGHT = ''
        RESET_ALL = ''


def generate_json_diff(
    old_data: Any,
    new_data: Any,
    context_lines: int = 3,
    old_label: str = "old",
    new_label: str = "new",
) -> str:
    """
    Generate a unified diff between two JSON objects.
    
    Args:
        old_data: Original data (dict, list, or other JSON-serializable)
        new_data: New data (dict, list, or other JSON-serializable)
        context_lines: Number of context lines around changes
        old_label: Label for the old file in diff header
        new_label: Label for the new file in diff header
        
    Returns:
        Unified diff string
    """
    old_str = json.dumps(old_data, indent=2, sort_keys=True)
    new_str = json.dumps(new_data, indent=2, sort_keys=True)
    
    old_lines = old_str.splitlines(keepends=True)
    new_lines = new_str.splitlines(keepends=True)
    
    diff = unified_diff(
        old_lines,
        new_lines,
        fromfile=old_label,
        tofile=new_label,
        n=context_lines,
    )
    
    return ''.join(diff)


def generate_wdl_diff(
    old_wdl: List[Dict],
    new_wdl: List[Dict],
    context_lines: int = 3,
) -> str:
    """
    Generate a unified diff between two WDL structures.
    
    Args:
        old_wdl: Original WDL operations list
        new_wdl: New WDL operations list
        context_lines: Number of context lines around changes
        
    Returns:
        Unified diff string
    """
    return generate_json_diff(
        old_wdl,
        new_wdl,
        context_lines=context_lines,
        old_label="old_wdl.json",
        new_label="new_wdl.json",
    )


def colorize_diff(diff_str: str) -> str:
    """
    Add color to a unified diff string.
    
    Args:
        diff_str: Unified diff string
        
    Returns:
        Colorized diff string
    """
    if not diff_str:
        return ""
    
    lines = diff_str.split('\n')
    colored_lines = []
    
    for line in lines:
        if line.startswith('+++') or line.startswith('---'):
            colored_lines.append(f"{Style.BRIGHT}{Fore.CYAN}{line}{Style.RESET_ALL}")
        elif line.startswith('@@'):
            colored_lines.append(f"{Fore.CYAN}{line}{Style.RESET_ALL}")
        elif line.startswith('+'):
            colored_lines.append(f"{Fore.GREEN}{line}{Style.RESET_ALL}")
        elif line.startswith('-'):
            colored_lines.append(f"{Fore.RED}{line}{Style.RESET_ALL}")
        else:
            colored_lines.append(line)
    
    return '\n'.join(colored_lines)


def display_diff(
    old_data: Any,
    new_data: Any,
    title: str = "Changes",
    colorize: bool = True,
) -> None:
    """
    Display a formatted diff between two data structures.
    
    Args:
        old_data: Original data
        new_data: New data
        title: Title to display above the diff
        colorize: Whether to colorize the output
    """
    diff_str = generate_json_diff(old_data, new_data)
    
    if not diff_str:
        print(f"\n{title}: No changes detected")
        return
    
    print(f"\n{'='*60}")
    print(f"📝 {title}")
    print('='*60)
    
    if colorize and COLORAMA_AVAILABLE:
        print(colorize_diff(diff_str))
    else:
        print(diff_str)
    
    print('='*60)


def summarize_changes(
    old_wdl: List[Dict],
    new_wdl: List[Dict],
) -> Dict[str, Any]:
    """
    Summarize the changes between two WDL structures.
    
    Args:
        old_wdl: Original WDL
        new_wdl: New WDL
        
    Returns:
        Summary of changes
    """
    summary = {
        'operations_changed': 0,
        'operations_added': 0,
        'operations_removed': 0,
        'field_changes': [],
    }
    
    old_by_id = {op.get('id'): op for op in old_wdl if isinstance(op, dict) and op.get('id')}
    new_by_id = {op.get('id'): op for op in new_wdl if isinstance(op, dict) and op.get('id')}
    
    old_ids = set(old_by_id.keys())
    new_ids = set(new_by_id.keys())
    
    summary['operations_added'] = len(new_ids - old_ids)
    summary['operations_removed'] = len(old_ids - new_ids)
    
    # Check for changes in common operations
    common_ids = old_ids & new_ids
    for op_id in common_ids:
        old_op = old_by_id[op_id]
        new_op = new_by_id[op_id]
        
        if old_op != new_op:
            summary['operations_changed'] += 1
            
            # Find changed fields
            for key in set(old_op.keys()) | set(new_op.keys()):
                old_val = old_op.get(key)
                new_val = new_op.get(key)
                if old_val != new_val:
                    summary['field_changes'].append({
                        'operation_id': op_id,
                        'field': key,
                        'old_value': str(old_val)[:100] if old_val else None,
                        'new_value': str(new_val)[:100] if new_val else None,
                    })
    
    return summary


def format_path_change(old_path: str, new_path: str) -> str:
    """
    Format a path change for display.
    
    Args:
        old_path: Original path
        new_path: New path
        
    Returns:
        Formatted string showing the change
    """
    if COLORAMA_AVAILABLE:
        return f"{Fore.RED}{old_path}{Style.RESET_ALL} → {Fore.GREEN}{new_path}{Style.RESET_ALL}"
    else:
        return f"{old_path} → {new_path}"









