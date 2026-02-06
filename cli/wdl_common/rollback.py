#!/usr/bin/env python3
"""
Rollback management for the diagnostic toolkit.

Manages saving and loading rollback state for API and tool changes.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import RollbackEntry


class RollbackManager:
    """Manages rollback state for diagnostic fix operations."""
    
    def __init__(self, rollback_dir: str = "diagnostics"):
        """
        Initialize the rollback manager.
        
        Args:
            rollback_dir: Directory to store rollback files (relative to ABCD repo root)
        """
        self.repo_root = Path(__file__).parent.parent.parent
        self.rollback_dir = self.repo_root / rollback_dir
        self.rollback_dir.mkdir(parents=True, exist_ok=True)
        
        self._current_entries: List[RollbackEntry] = []
        self._session_file: Optional[Path] = None
    
    def start_session(self) -> Path:
        """
        Start a new rollback session.
        
        Returns:
            Path to the session rollback file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_file = self.rollback_dir / f"rollback_{timestamp}.json"
        self._current_entries = []
        return self._session_file
    
    def add_api_change(
        self,
        api_id: str,
        api_title: str,
        original_path: str,
        new_path: str,
    ) -> None:
        """
        Record an API path change for rollback.
        
        Args:
            api_id: The API ID
            api_title: The API title
            original_path: Original path before change
            new_path: New path after change
        """
        entry = RollbackEntry(
            entry_type='api',
            id=api_id,
            title=api_title,
            original_value=original_path,
            new_value=new_path,
            timestamp=datetime.now().isoformat(),
        )
        self._current_entries.append(entry)
        self._save_current_state()
    
    def add_tool_change(
        self,
        tool_id: str,
        tool_title: str,
        original_wdl: List[Dict],
        new_wdl: List[Dict],
    ) -> None:
        """
        Record a tool WDL change for rollback.
        
        Args:
            tool_id: The tool ID
            tool_title: The tool title
            original_wdl: Original WDL before change
            new_wdl: New WDL after change
        """
        entry = RollbackEntry(
            entry_type='tool',
            id=tool_id,
            title=tool_title,
            original_value=original_wdl,
            new_value=new_wdl,
            timestamp=datetime.now().isoformat(),
        )
        self._current_entries.append(entry)
        self._save_current_state()
    
    def _save_current_state(self) -> None:
        """Save current rollback entries to file."""
        if not self._session_file:
            self.start_session()
        
        data = {
            'created_at': datetime.now().isoformat(),
            'version': '1.0',
            'entries_count': len(self._current_entries),
            'entries': [e.to_dict() for e in self._current_entries],
        }
        
        with open(self._session_file, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def get_session_file(self) -> Optional[Path]:
        """Get the current session rollback file path."""
        return self._session_file
    
    def get_entry_count(self) -> int:
        """Get the number of entries in the current session."""
        return len(self._current_entries)


def save_rollback_state(
    apis_to_fix: List[Dict[str, Any]],
    tools_to_patch: List[Dict[str, Any]],
    output_file: str,
) -> None:
    """
    Save the current state of APIs and tools before making changes.
    
    Args:
        apis_to_fix: List of APIs with current_path and corrected_path
        tools_to_patch: List of tools with original WDL
        output_file: Path to save the rollback file
    """
    rollback_data = {
        'created_at': datetime.now().isoformat(),
        'version': '1.0',
        'apis': [],
        'tools': [],
    }
    
    for api in apis_to_fix:
        rollback_data['apis'].append({
            'id': api.get('id'),
            'title': api.get('title'),
            'original_path': api.get('current_path'),
            'new_path': api.get('corrected_path'),
        })
    
    for tool in tools_to_patch:
        rollback_data['tools'].append({
            'id': tool.get('id'),
            'title': tool.get('title'),
            'original_wdl': tool.get('original_wdl'),
            'old_path': tool.get('old_path'),
            'new_path': tool.get('new_path'),
        })
    
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(rollback_data, f, indent=2)
    
    print(f"   💾 Rollback state saved to: {output_file}")
    print(f"      - {len(rollback_data['apis'])} API(s)")
    print(f"      - {len(rollback_data['tools'])} tool(s)")


def load_rollback_state(rollback_file: str) -> Dict[str, Any]:
    """
    Load a rollback file.
    
    Args:
        rollback_file: Path to the rollback file
        
    Returns:
        The rollback data dictionary
    """
    with open(rollback_file, 'r') as f:
        return json.load(f)


def display_rollback_contents(rollback_file: str) -> None:
    """
    Display the contents of a rollback file.
    
    Args:
        rollback_file: Path to the rollback file
    """
    data = load_rollback_state(rollback_file)
    
    print(f"\n{'='*70}")
    print(f"📋 ROLLBACK FILE CONTENTS")
    print(f"{'='*70}")
    print(f"Created: {data.get('created_at', 'unknown')}")
    print(f"Version: {data.get('version', 'unknown')}")
    
    apis = data.get('apis', [])
    if apis:
        print(f"\n📝 APIs ({len(apis)}):")
        for api in apis:
            print(f"   • {api.get('title', 'Unknown')}")
            print(f"     '{api.get('new_path')}' → '{api.get('original_path')}'")
    
    tools = data.get('tools', [])
    if tools:
        print(f"\n🔧 Tools ({len(tools)}):")
        for tool in tools:
            print(f"   • {tool.get('title', 'Unknown')}")
            print(f"     Will restore original WDL")
    
    # Handle new format with entries
    entries = data.get('entries', [])
    if entries and not apis and not tools:
        print(f"\n📋 Entries ({len(entries)}):")
        for entry in entries:
            entry_type = entry.get('entry_type', 'unknown')
            title = entry.get('title', 'Unknown')
            if entry_type == 'api':
                print(f"   • [API] {title}")
                print(f"     '{entry.get('new_value')}' → '{entry.get('original_value')}'")
            elif entry_type == 'tool':
                print(f"   • [Tool] {title}")
                print(f"     Will restore original WDL")
    
    print(f"{'='*70}")


def list_rollback_files(rollback_dir: str = "diagnostics") -> List[Path]:
    """
    List available rollback files.
    
    Args:
        rollback_dir: Directory containing rollback files
        
    Returns:
        List of rollback file paths, sorted by modification time
    """
    repo_root = Path(__file__).parent.parent.parent
    dir_path = repo_root / rollback_dir
    
    if not dir_path.exists():
        return []
    
    files = list(dir_path.glob("rollback_*.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    return files









