#!/usr/bin/env python3
"""
Workspace Context Utilities

This module provides the STANDARD way for CLI scripts to access
workspace resources. All scripts should use these functions.

Usage:
    from cli.wdl_common.context import (
        get_context,      # Get action context (loads env, resolves profile)
        get_client,       # Get API client (uses active env credentials)
        get_discovery,    # Get discovery (uses active env cache)
        get_manager,      # Get workspace manager singleton
        ensure_env,       # Ensure env is loaded, get its name
    )
"""

import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def get_manager():
    """
    Get the workspace manager singleton.
    
    Returns:
        HierarchicalWorkspaceManager instance
    """
    from cli.wdl_common.workspace_manager import get_workspace_manager
    return get_workspace_manager()


def ensure_env() -> str:
    """
    Ensure active environment is loaded and return its name.
    
    This should be called at the start of any script that needs
    environment credentials.
    
    Returns:
        Active environment name
        
    Raises:
        ValueError: If no active environment
    """
    manager = get_manager()
    return manager.ensure_env_loaded()


def get_context(action_id: str):
    """
    Get complete context for an action in the active environment.
    
    This is the RECOMMENDED entry point for scripts that work with
    a specific action. It handles:
    - Finding the action in active environment
    - Loading environment credentials
    - Resolving adopt_profile.json inheritance
    
    Args:
        action_id: Action to find
        
    Returns:
        ActionContext or None if not found
    """
    manager = get_manager()
    return manager.get_action_context(action_id)


def get_client(verbose: bool = False):
    """
    Get API client for the active environment.
    
    Credentials are automatically loaded from the environment's .env file.
    
    Args:
        verbose: Print verbose info about credential loading.
    
    Raises:
        ValueError: If no active environment
        
    Returns:
        AdoptAPIClient configured with environment credentials.
    """
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    manager = get_manager()
    
    if not manager.active_env:
        raise ValueError(
            "No active environment. Set one with: "
            "python cli/workspace.py env use <env-id>"
        )
    
    env = manager.active_env
    env_path = WORKSPACES_DIR / env

    # Load environment-specific .env file
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        if verbose:
            print(f"[VERBOSE] Loading credentials from: {env_dotenv}", file=sys.stderr)
        load_dotenv(env_dotenv, override=True)

        # Check if credentials are configured (not placeholders)
        client_id = os.getenv("ADOPT_CLIENT_ID", "")
        client_secret = os.getenv("ADOPT_CLIENT_SECRET", "")

        if "your-" in client_id.lower() or not client_id:
            print(f"⚠️  Warning: ADOPT_CLIENT_ID not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
        if "your-" in client_secret.lower() or not client_secret:
            print(f"⚠️  Warning: ADOPT_CLIENT_SECRET not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
    else:
        print(f"⚠️  Warning: No .env file in environment: {env}", file=sys.stderr)
        print(f"   Expected: {env_dotenv}", file=sys.stderr)

    from cli.wdl_common.api_client import AdoptAPIClient
    return AdoptAPIClient()


def get_discovery(verbose: bool = False):
    """
    Get Discovery instance for the active environment.
    
    Credentials and caching are per-environment.
    
    Args:
        verbose: Enable verbose debugging output
        
    Returns:
        Discovery instance configured for active environment
        
    Raises:
        ValueError: If no active environment
    """
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    manager = get_manager()
    
    if not manager.active_env:
        raise ValueError(
            "No active environment. Set one with: "
            "python cli/workspace.py env use <env-id>"
        )
    
    env = manager.active_env
    env_path = WORKSPACES_DIR / env

    # Load environment-specific .env file
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        if verbose:
            print(f"[VERBOSE] Loading credentials from: {env_dotenv}", file=sys.stderr)
        load_dotenv(env_dotenv, override=True)

        # Check if credentials are properly configured
        client_id = os.getenv("ADOPT_CLIENT_ID", "")
        client_secret = os.getenv("ADOPT_CLIENT_SECRET", "")

        if "your-" in client_id.lower() or not client_id:
            print(f"⚠️  Warning: ADOPT_CLIENT_ID not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
        if "your-" in client_secret.lower() or not client_secret:
            print(f"⚠️  Warning: ADOPT_CLIENT_SECRET not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
    else:
        print(f"⚠️  Warning: No .env file in environment: {env}", file=sys.stderr)
        print(f"   Expected: {env_dotenv}", file=sys.stderr)

    from cli.wdl_common.discovery import Discovery
    return Discovery(env_path=env_path, verbose=verbose)


def get_env_cache_path() -> Path:
    """
    Get path to active environment's cache directory.
    
    Returns:
        Path to .cache directory in active environment
        
    Raises:
        ValueError: If no active environment
    """
    manager = get_manager()
    env_path = manager.get_env_path()
    
    if not env_path:
        raise ValueError("No active environment")
    
    cache_path = env_path / ".cache"
    cache_path.mkdir(exist_ok=True)
    return cache_path

