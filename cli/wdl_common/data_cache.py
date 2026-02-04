#!/usr/bin/env python3
"""
Unified data caching system for the diagnostic toolkit.

Manages caching of tools, APIs, and network logs to avoid repeated API calls.

IMPORTANT: This module now integrates with the hierarchical workspace manager.
Cache files are stored per-environment in workspaces/{env}/.cache/ by default.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import NetworkLogEntry
from .log_parser import parse_log_entry, save_logs_to_file


class DataCache:
    """
    Unified cache manager for tools, APIs, and network logs.
    
    Integrates with the hierarchical workspace manager to store cache files
    per-environment. This ensures that cached data from one client environment
    doesn't contaminate diagnostics for another.
    
    Usage:
        # Preferred: Uses active environment's cache automatically
        cache = DataCache()
        
        # Explicit path (for testing or special cases)
        cache = DataCache(cache_dir=Path("/custom/path"))
    """
    
    def __init__(self, cache_dir: Optional[Union[str, Path]] = None):
        """
        Initialize the data cache.
        
        Args:
            cache_dir: Optional explicit cache directory. If None, uses the
                       active environment's .cache directory from the workspace
                       manager. Falls back to global cache/ if no active env.
        """
        self._env_name: Optional[str] = None
        
        if cache_dir is not None:
            # Explicit path provided
            self.cache_dir = Path(cache_dir)
        else:
            # Try to use per-environment cache from workspace manager
            try:
                from cli.wdl_common.context import get_env_cache_path, ensure_env
                self._env_name = ensure_env()
                self.cache_dir = get_env_cache_path()
            except (ValueError, ImportError) as e:
                # Fallback to global cache if no active env or import error
                self.tool_builder_root = Path(__file__).parent.parent.parent
                self.cache_dir = self.tool_builder_root / "cache"
                print(f"⚠️  Using global cache (no active environment): {self.cache_dir}", 
                      file=sys.stderr)
        
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.tools_cache_file = self.cache_dir / "tools_detailed.json"
        self.apis_cache_file = self.cache_dir / "apis_detailed.json"
        self.logs_cache_file = self.cache_dir / "network_logs.json"
        
        # In-memory caches
        self._tools_cache: Optional[List[Dict]] = None
        self._apis_cache: Optional[List[Dict]] = None
        self._logs_cache: Optional[List[NetworkLogEntry]] = None
    
    @property
    def env_name(self) -> Optional[str]:
        """Get the environment name this cache is associated with."""
        return self._env_name
    
    def _get_cache_metadata(self, cache_file: Path) -> Optional[Dict[str, Any]]:
        """Get metadata from a cache file."""
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and 'metadata' in data:
                    return data['metadata']
                return None
        except (json.JSONDecodeError, IOError):
            return None
    
    def get_cache_age(self, cache_type: str) -> Optional[datetime]:
        """
        Get the timestamp when a cache was last updated.
        
        Args:
            cache_type: 'tools', 'apis', or 'logs'
            
        Returns:
            datetime of last update, or None if cache doesn't exist
        """
        cache_files = {
            'tools': self.tools_cache_file,
            'apis': self.apis_cache_file,
            'logs': self.logs_cache_file,
        }
        
        cache_file = cache_files.get(cache_type)
        if not cache_file or not cache_file.exists():
            return None
        
        metadata = self._get_cache_metadata(cache_file)
        if metadata and 'fetched_at' in metadata:
            try:
                return datetime.fromisoformat(metadata['fetched_at'])
            except ValueError:
                pass
        
        # Fallback to file modification time
        return datetime.fromtimestamp(cache_file.stat().st_mtime)
    
    def is_cache_stale(
        self,
        cache_type: str,
        max_age_hours: int = 24
    ) -> bool:
        """
        Check if a cache is stale.
        
        Args:
            cache_type: 'tools', 'apis', or 'logs'
            max_age_hours: Maximum age in hours before cache is considered stale
            
        Returns:
            True if cache is stale or doesn't exist
        """
        age = self.get_cache_age(cache_type)
        if age is None:
            return True
        
        return datetime.now() - age > timedelta(hours=max_age_hours)
    
    def invalidate_cache(self, cache_type: str = "all") -> None:
        """
        Invalidate (delete) cache files.
        
        Args:
            cache_type: 'tools', 'apis', 'logs', or 'all'
        """
        cache_files = {
            'tools': self.tools_cache_file,
            'apis': self.apis_cache_file,
            'logs': self.logs_cache_file,
        }
        
        if cache_type == "all":
            for cf in cache_files.values():
                if cf.exists():
                    cf.unlink()
            self._tools_cache = None
            self._apis_cache = None
            self._logs_cache = None
            print(f"✅ All caches invalidated")
        elif cache_type in cache_files:
            cf = cache_files[cache_type]
            if cf.exists():
                cf.unlink()
            if cache_type == 'tools':
                self._tools_cache = None
            elif cache_type == 'apis':
                self._apis_cache = None
            elif cache_type == 'logs':
                self._logs_cache = None
            print(f"✅ {cache_type} cache invalidated")
    
    # =========================================================================
    # Tools Cache
    # =========================================================================
    
    def load_tools_cache(self) -> Optional[List[Dict]]:
        """Load tools from cache file."""
        if self._tools_cache is not None:
            return self._tools_cache
        
        if not self.tools_cache_file.exists():
            return None
        
        try:
            with open(self.tools_cache_file, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and 'tools' in data:
                    self._tools_cache = data['tools']
                elif isinstance(data, list):
                    self._tools_cache = data
                else:
                    return None
                return self._tools_cache
        except (json.JSONDecodeError, IOError):
            return None
    
    def save_tools_cache(self, tools: List[Dict], include_wdl: bool = True) -> None:
        """Save tools to cache file."""
        cache_data = {
            'metadata': {
                'fetched_at': datetime.now().isoformat(),
                'count': len(tools),
                'version': '1.0',
                'include_wdl': include_wdl,
            },
            'tools': tools,
        }
        
        with open(self.tools_cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        
        self._tools_cache = tools
        print(f"   💾 Cached {len(tools)} tools to: {self.tools_cache_file}")
    
    def get_tool_by_id(self, tool_id: str) -> Optional[Dict]:
        """Get a single tool by ID from cache."""
        tools = self.load_tools_cache()
        if not tools:
            return None
        
        for tool in tools:
            if tool.get('id') == tool_id:
                return tool
        return None
    
    # =========================================================================
    # APIs Cache
    # =========================================================================
    
    def load_apis_cache(self) -> Optional[List[Dict]]:
        """Load APIs from cache file."""
        if self._apis_cache is not None:
            return self._apis_cache
        
        if not self.apis_cache_file.exists():
            return None
        
        try:
            with open(self.apis_cache_file, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and 'apis' in data:
                    self._apis_cache = data['apis']
                elif isinstance(data, list):
                    self._apis_cache = data
                else:
                    return None
                return self._apis_cache
        except (json.JSONDecodeError, IOError):
            return None
    
    def save_apis_cache(self, apis: List[Dict]) -> None:
        """Save APIs to cache file."""
        cache_data = {
            'metadata': {
                'fetched_at': datetime.now().isoformat(),
                'count': len(apis),
                'version': '1.0',
            },
            'apis': apis,
        }
        
        with open(self.apis_cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        
        self._apis_cache = apis
        print(f"   💾 Cached {len(apis)} APIs to: {self.apis_cache_file}")
    
    def get_api_by_id(self, api_id: str) -> Optional[Dict]:
        """Get a single API by ID from cache."""
        apis = self.load_apis_cache()
        if not apis:
            return None
        
        for api in apis:
            if api.get('id') == api_id:
                return api
        return None
    
    # =========================================================================
    # Network Logs Cache
    # =========================================================================
    
    def load_logs_cache(self) -> Optional[List[NetworkLogEntry]]:
        """Load network logs from cache file."""
        if self._logs_cache is not None:
            return self._logs_cache
        
        if not self.logs_cache_file.exists():
            return None
        
        try:
            with open(self.logs_cache_file, 'r') as f:
                data = json.load(f)
                
                logs_data = []
                if isinstance(data, dict) and 'logs' in data:
                    logs_data = data['logs']
                elif isinstance(data, list):
                    logs_data = data
                else:
                    return None
                
                self._logs_cache = [
                    log for log in (parse_log_entry(entry) for entry in logs_data)
                    if log is not None
                ]
                return self._logs_cache
        except (json.JSONDecodeError, IOError):
            return None
    
    def save_logs_cache(self, logs: List[NetworkLogEntry]) -> None:
        """Save network logs to cache file."""
        cache_data = {
            'metadata': {
                'fetched_at': datetime.now().isoformat(),
                'count': len(logs),
                'version': '1.0',
            },
            'logs': [log.to_dict() for log in logs],
        }
        
        with open(self.logs_cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        
        self._logs_cache = logs
        print(f"   💾 Cached {len(logs)} network logs to: {self.logs_cache_file}")
    
    def get_logs_stats(self) -> Dict[str, Any]:
        """Get statistics about cached logs."""
        from .log_parser import get_logs_stats
        
        logs = self.load_logs_cache()
        if not logs:
            return {'total': 0, 'cached': False}
        
        stats = get_logs_stats(logs)
        stats['cached'] = True
        stats['cache_age'] = self.get_cache_age('logs')
        return stats
    
    # =========================================================================
    # Cache Status
    # =========================================================================
    
    def get_cache_status(self) -> Dict[str, Any]:
        """Get status of all caches."""
        status = {}
        
        for cache_type in ['tools', 'apis', 'logs']:
            cache_age = self.get_cache_age(cache_type)
            is_stale = self.is_cache_stale(cache_type)
            
            cache_files = {
                'tools': self.tools_cache_file,
                'apis': self.apis_cache_file,
                'logs': self.logs_cache_file,
            }
            
            cache_file = cache_files[cache_type]
            exists = cache_file.exists()
            
            metadata = self._get_cache_metadata(cache_file) if exists else None
            count = metadata.get('count', 0) if metadata else 0
            
            status[cache_type] = {
                'exists': exists,
                'age': cache_age.isoformat() if cache_age else None,
                'is_stale': is_stale,
                'count': count,
                'file': str(cache_file),
            }
        
        return status
    
    def display_cache_status(self) -> None:
        """Display cache status in a formatted way."""
        status = self.get_cache_status()
        
        print("\n" + "=" * 60)
        print("📦 CACHE STATUS")
        print("=" * 60)
        
        # Show environment info
        if self._env_name:
            print(f"  📁 Environment: {self._env_name}")
        print(f"  📂 Cache Path: {self.cache_dir}")
        print()
        
        for cache_type, info in status.items():
            if info['exists']:
                age_str = info['age'][:19] if info['age'] else 'unknown'
                stale_indicator = " ⚠️ STALE" if info['is_stale'] else " ✅"
                print(f"  {cache_type.upper()}: {info['count']} items, cached at {age_str}{stale_indicator}")
            else:
                print(f"  {cache_type.upper()}: ❌ Not cached")
        
        print("=" * 60)









