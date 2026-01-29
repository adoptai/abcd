#!/usr/bin/env python3
"""
Version tracking utilities for WDL workflow management.

Provides centralized functions for reading/writing version metadata
in both current_version.txt and metadata.json formats.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def read_current_version(workspace: Path) -> Optional[Dict[str, Any]]:
    """
    Read current_version.txt and return parsed data.
    
    Args:
        workspace: Workspace directory path
        
    Returns:
        Dictionary with version info or None if file doesn't exist
    """
    version_file = workspace / "current_version.txt"
    if not version_file.exists():
        return None
    
    data: Dict[str, Any] = {}
    for line in version_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            
            # Parse boolean values
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            # Parse integer values
            elif key in ("version", "version_number") and value.isdigit():
                value = int(value)
            
            data[key] = value
    
    return data if data else None


def read_metadata(workspace: Path) -> Dict[str, Any]:
    """
    Read metadata.json and return full metadata dict.
    
    Args:
        workspace: Workspace directory path
        
    Returns:
        Metadata dictionary (empty dict if file doesn't exist)
    """
    metadata_path = workspace / "metadata.json"
    if not metadata_path.exists():
        return {}
    
    try:
        return json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, Exception):
        return {}


def get_version_info(workspace: Path, version_number: int) -> Optional[Dict[str, Any]]:
    """
    Get full version info from metadata.json versions map.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number to lookup
        
    Returns:
        Version info dict or None if not found
    """
    metadata = read_metadata(workspace)
    versions = metadata.get("versions", {})
    return versions.get(str(version_number))


def get_current_version_info(workspace: Path) -> Optional[Dict[str, Any]]:
    """
    Get current version info (looks up current_version number in versions map).
    
    Args:
        workspace: Workspace directory path
        
    Returns:
        Version info dict or None if not found
    """
    metadata = read_metadata(workspace)
    current_version = metadata.get("current_version")
    if current_version is None:
        return None
    
    return get_version_info(workspace, current_version)


def get_checked_out_version_info(workspace: Path) -> Optional[Dict[str, Any]]:
    """
    Get checked-out version info (looks up checked_out_version number in versions map).
    
    Args:
        workspace: Workspace directory path
        
    Returns:
        Version info dict or None if not found
    """
    metadata = read_metadata(workspace)
    checked_out_version = metadata.get("checked_out_version")
    if checked_out_version is None:
        return None
    
    return get_version_info(workspace, checked_out_version)


def update_current_version(
    workspace: Path,
    version_number: int,
    status: str,
    is_published: bool,
    description: Optional[str] = None,
) -> None:
    """
    Update current_version.txt with version info.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number
        status: Version status ("pending_approval" for draft, "approved" for published)
        is_published: Whether version is published (True if status == "approved")
        description: Optional version description
    """
    version_file = workspace / "current_version.txt"
    timestamp = datetime.now().isoformat()
    
    lines = [
        f"version: {version_number}",
        f"version_number: {version_number}",
        f"status: {status}",
        f"is_published: {str(is_published).lower()}",
        f"checked_out_at: {timestamp}",
    ]
    
    if description:
        lines.append(f"description: {description}")
    
    version_file.write_text("\n".join(lines) + "\n")


def update_metadata_version(
    workspace: Path,
    version_number: int,
    status: str,
    is_published: bool,
    description: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    checked_out_at: Optional[str] = None,
) -> None:
    """
    Update metadata.json versions map with version info.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number
        status: Version status ("pending_approval" for draft, "approved" for published)
        is_published: Whether version is published (True if status == "approved")
        description: Optional version description
        created_at: Optional creation timestamp
        updated_at: Optional update timestamp
        checked_out_at: Optional checkout timestamp
    """
    metadata_path = workspace / "metadata.json"
    metadata = read_metadata(workspace)
    
    # Initialize versions map if needed
    if "versions" not in metadata:
        metadata["versions"] = {}
    
    # Get existing version data or create new
    version_key = str(version_number)
    existing_version = metadata["versions"].get(version_key, {})
    
    # Update version data
    version_data: Dict[str, Any] = {
        "version_number": version_number,
        "status": status,
        "is_published": is_published,
        "description": description or existing_version.get("description", ""),
        "created_at": created_at or existing_version.get("created_at", ""),
        "updated_at": updated_at or datetime.now().isoformat(),
    }
    
    # Preserve checked_out_at if provided or if already exists
    if checked_out_at:
        version_data["checked_out_at"] = checked_out_at
    elif "checked_out_at" in existing_version:
        version_data["checked_out_at"] = existing_version["checked_out_at"]
    
    metadata["versions"][version_key] = version_data
    
    # Write back to file
    metadata_path.write_text(json.dumps(metadata, indent=2))


def sync_versions_from_api(
    workspace: Path,
    versions_from_api: List[Dict[str, Any]],
) -> None:
    """
    Sync all versions from API to metadata.json versions map.
    Also syncs current_version to the remote current version (published/production).
    
    Args:
        workspace: Workspace directory path
        versions_from_api: List of version dicts from API
    """
    metadata_path = workspace / "metadata.json"
    metadata = read_metadata(workspace)
    
    # Initialize versions map if needed
    if "versions" not in metadata:
        metadata["versions"] = {}
    
    # Find current version from API (is_current_version=True)
    # Status is always either "pending_approval" (draft) or "approved" (published)
    remote_current_version = None
    for v in versions_from_api:
        if v.get("is_current_version", False):
            remote_current_version = v.get("version_number")
            break
    
    # Sync each version from API
    versions_dict = {}
    for v in versions_from_api:
        version_id = str(v.get("version_number", v.get("id", "")))
        if not version_id or not version_id.isdigit():
            continue
        
        # Get existing local data to preserve checked_out_at
        existing_version = metadata["versions"].get(version_id, {})
        
        # Determine status and is_published
        # Status is always either "pending_approval" (draft) or "approved" (published)
        status = v.get("status", existing_version.get("status", "pending_approval"))
        is_published = status == "approved"
        
        # Update version data
        versions_dict[version_id] = {
            "version_number": int(version_id),
            "status": status,
            "is_published": is_published,
            "description": v.get("change_reason", existing_version.get("description", "")),
            "created_at": v.get("created_at", existing_version.get("created_at", "")),
            "updated_at": v.get("updated_at", existing_version.get("updated_at", "")),
            # Preserve local checked_out_at if exists
            "checked_out_at": existing_version.get("checked_out_at", ""),
        }
    
    # Sort versions by version_number descending (higher numbers first)
    sorted_versions = dict(sorted(versions_dict.items(), key=lambda x: int(x[0]), reverse=True))
    metadata["versions"] = sorted_versions
    
    # Sync current_version to remote current version
    # Only one version can be current at a time (marked by is_current_version flag)
    if remote_current_version is not None:
        metadata["current_version"] = remote_current_version
    elif "current_version" not in metadata:
        # Fallback: find latest approved version if no current version marked
        for version_id, version_data in sorted_versions.items():
            if version_data.get("status") == "approved":
                metadata["current_version"] = version_data["version_number"]
                break
    
    # Remove old "version" field if it exists (consolidate to current_version)
    if "version" in metadata:
        del metadata["version"]
    
    # Remove old top-level "status" field (status is per-version in versions map)
    if "status" in metadata and metadata.get("current_version"):
        # Only remove if we have current_version (status is now per-version)
        del metadata["status"]
    
    # Write back to file
    metadata_path.write_text(json.dumps(metadata, indent=2))


def set_current_version(workspace: Path, version_number: int) -> None:
    """
    Set current_version number in metadata.json.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number to set as current
    """
    metadata_path = workspace / "metadata.json"
    metadata = read_metadata(workspace)
    metadata["current_version"] = version_number
    metadata_path.write_text(json.dumps(metadata, indent=2))


def set_checked_out_version(workspace: Path, version_number: int) -> None:
    """
    Set checked_out_version number in metadata.json.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number to set as checked out
    """
    metadata_path = workspace / "metadata.json"
    metadata = read_metadata(workspace)
    metadata["checked_out_version"] = version_number
    metadata_path.write_text(json.dumps(metadata, indent=2))


def get_version_description(workspace: Path, version_number: int) -> Optional[str]:
    """
    Get description for a specific version from metadata.json versions map.
    
    Args:
        workspace: Workspace directory path
        version_number: Version number
        
    Returns:
        Version description or None if not found
    """
    version_info = get_version_info(workspace, version_number)
    return version_info.get("description") if version_info else None


def prompt_for_description(
    existing_description: Optional[str] = None,
    default: str = "WDL workflow update",
) -> str:
    """
    Prompt user for version description (for interactive use).
    
    Args:
        existing_description: Existing description to show as reference
        default: Default description if user presses Enter
        
    Returns:
        Description string
    """
    if existing_description:
        print(f"   Current description: {existing_description}")
    
    prompt_text = f"\n📝 Enter version description (or press Enter for '{default}'): "
    description = input(prompt_text).strip()
    
    return description if description else default

