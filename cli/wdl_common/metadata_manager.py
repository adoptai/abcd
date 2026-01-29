#!/usr/bin/env python3
"""
Centralized Metadata Manager for WDL Workspaces.

Provides a single source of truth for all workspace state:
- Remote action linking and recovery
- Version tracking with local change detection
- Automatic sync status management
- WDL hash calculation for change detection

This replaces the fragmented tracking in version_tracker.py and
current_version.txt with a unified approach.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RemoteState:
    """Remote action state."""
    action_id: Optional[str] = None
    linked_at: Optional[str] = None
    last_synced_at: Optional[str] = None
    sync_status: str = "unknown"  # synced, pending_save, pending_publish, conflict, unknown


@dataclass
class WorkingVersion:
    """Currently active version being worked on."""
    number: Optional[int] = None
    status: str = "draft"  # draft, approved
    is_published: bool = False
    local_wdl_hash: Optional[str] = None
    remote_wdl_hash: Optional[str] = None
    has_local_changes: bool = False
    description: Optional[str] = None
    checked_out_at: Optional[str] = None


@dataclass
class RemoteVersions:
    """Summary of remote version state."""
    latest_draft: Optional[int] = None
    latest_published: Optional[int] = None
    current: Optional[int] = None


@dataclass
class VersionInfo:
    """Information about a specific version."""
    version_number: int
    status: str = "draft"
    is_published: bool = False
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    checked_out_at: Optional[str] = None
    has_local_copy: bool = False


@dataclass
class WorkspaceMetadata:
    """Complete workspace metadata."""
    workflow_id: str
    title: str = ""
    agent_name: Optional[str] = None
    remote: RemoteState = field(default_factory=RemoteState)
    working_version: WorkingVersion = field(default_factory=WorkingVersion)
    remote_versions: RemoteVersions = field(default_factory=RemoteVersions)
    versions: Dict[str, VersionInfo] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MetadataManager:
    """
    Centralized manager for workspace metadata.

    Provides:
    - Loading/saving metadata with automatic migration
    - WDL hash calculation for change detection
    - Action ID protection and recovery
    - Sync status tracking
    """

    def __init__(self, workspace: Path):
        """
        Initialize metadata manager for a workspace.

        Args:
            workspace: Path to the workspace directory
        """
        self.workspace = Path(workspace)
        self.metadata_path = self.workspace / "metadata.json"
        self.action_id_path = self.workspace / ".action_id"
        self.wdl_path = self.workspace / "widdle.json"
        self._metadata: Optional[WorkspaceMetadata] = None

    # =========================================================================
    # Core Metadata Operations
    # =========================================================================

    def load(self) -> WorkspaceMetadata:
        """
        Load metadata from disk, migrating from old format if necessary.

        Returns:
            WorkspaceMetadata object
        """
        if not self.metadata_path.exists():
            # Create new metadata
            self._metadata = WorkspaceMetadata(
                workflow_id=self.workspace.name,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            return self._metadata

        try:
            raw_data = json.loads(self.metadata_path.read_text())
            self._metadata = self._migrate_and_parse(raw_data)

            # Ensure action_id is protected
            if self._metadata.remote.action_id:
                self._save_protected_action_id(self._metadata.remote.action_id)

            return self._metadata
        except (json.JSONDecodeError, Exception) as e:
            # Return minimal metadata on error
            self._metadata = WorkspaceMetadata(
                workflow_id=self.workspace.name,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            return self._metadata

    def save(self, metadata: Optional[WorkspaceMetadata] = None) -> None:
        """
        Save metadata to disk.

        Args:
            metadata: Metadata to save (uses cached if not provided)
        """
        if metadata:
            self._metadata = metadata

        if not self._metadata:
            return

        self._metadata.updated_at = datetime.now().isoformat()

        # Convert to dict and save
        data = self._to_dict(self._metadata)
        self.metadata_path.write_text(json.dumps(data, indent=2))

        # Ensure action_id is protected
        if self._metadata.remote.action_id:
            self._save_protected_action_id(self._metadata.remote.action_id)

    def _migrate_and_parse(self, raw_data: Dict[str, Any]) -> WorkspaceMetadata:
        """
        Parse raw metadata dict, migrating from old format if necessary.

        Args:
            raw_data: Raw metadata dict from file

        Returns:
            Parsed WorkspaceMetadata
        """
        # Check if already in new format
        if "remote" in raw_data and "working_version" in raw_data:
            return self._parse_new_format(raw_data)

        # Migrate from old format
        return self._migrate_from_old_format(raw_data)

    def _parse_new_format(self, data: Dict[str, Any]) -> WorkspaceMetadata:
        """Parse metadata in new format."""
        remote_data = data.get("remote", {})
        remote = RemoteState(
            action_id=remote_data.get("action_id"),
            linked_at=remote_data.get("linked_at"),
            last_synced_at=remote_data.get("last_synced_at"),
            sync_status=remote_data.get("sync_status", "unknown"),
        )

        working_data = data.get("working_version", {})
        working_version = WorkingVersion(
            number=working_data.get("number"),
            status=working_data.get("status", "draft"),
            is_published=working_data.get("is_published", False),
            local_wdl_hash=working_data.get("local_wdl_hash"),
            remote_wdl_hash=working_data.get("remote_wdl_hash"),
            has_local_changes=working_data.get("has_local_changes", False),
            description=working_data.get("description"),
            checked_out_at=working_data.get("checked_out_at"),
        )

        remote_versions_data = data.get("remote_versions", {})
        remote_versions = RemoteVersions(
            latest_draft=remote_versions_data.get("latest_draft"),
            latest_published=remote_versions_data.get("latest_published"),
            current=remote_versions_data.get("current"),
        )

        versions = {}
        for v_num, v_data in data.get("versions", {}).items():
            versions[v_num] = VersionInfo(
                version_number=v_data.get("version_number", int(v_num)),
                status=v_data.get("status", "draft"),
                is_published=v_data.get("is_published", False),
                description=v_data.get("description"),
                created_at=v_data.get("created_at"),
                updated_at=v_data.get("updated_at"),
                checked_out_at=v_data.get("checked_out_at"),
                has_local_copy=v_data.get("has_local_copy", False),
            )

        return WorkspaceMetadata(
            workflow_id=data.get("workflow_id", self.workspace.name),
            title=data.get("title", ""),
            agent_name=data.get("agent_name"),
            remote=remote,
            working_version=working_version,
            remote_versions=remote_versions,
            versions=versions,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def _migrate_from_old_format(self, data: Dict[str, Any]) -> WorkspaceMetadata:
        """
        Migrate from old metadata format.

        Old format had:
        - action_id at top level
        - current_version, checked_out_version as numbers
        - versions map with different structure
        """
        # Extract action_id from old locations
        action_id = data.get("action_id")
        if not action_id:
            # Try to get from protected file
            action_id = self._load_protected_action_id()

        # Build remote state
        remote = RemoteState(
            action_id=action_id,
            linked_at=data.get("created_at"),
            last_synced_at=data.get("updated_at"),
            sync_status="unknown",
        )

        # Build working version from checked_out_version or current_version
        checked_out = data.get("checked_out_version")
        current = data.get("current_version")
        working_num = checked_out or current

        # Get version info from old versions map
        old_versions = data.get("versions", {})
        working_info = old_versions.get(str(working_num), {}) if working_num else {}

        working_version = WorkingVersion(
            number=working_num,
            status=working_info.get("status", "draft"),
            is_published=working_info.get("is_published", False),
            description=working_info.get("description"),
            checked_out_at=working_info.get("checked_out_at"),
        )

        # Calculate local hash to detect changes later
        if self.wdl_path.exists():
            working_version.local_wdl_hash = self.calculate_wdl_hash()

        # Build remote versions summary
        latest_draft = None
        latest_published = None
        for v_num_str, v_data in old_versions.items():
            try:
                v_num = int(v_num_str)
            except ValueError:
                continue

            if v_data.get("is_published", False) or v_data.get("status") == "approved":
                if latest_published is None or v_num > latest_published:
                    latest_published = v_num
            else:
                if latest_draft is None or v_num > latest_draft:
                    latest_draft = v_num

        remote_versions = RemoteVersions(
            latest_draft=latest_draft,
            latest_published=latest_published,
            current=current,
        )

        # Migrate versions map
        versions = {}
        for v_num_str, v_data in old_versions.items():
            try:
                v_num = int(v_num_str)
            except ValueError:
                continue

            # Check if local copy exists
            local_copy_path = self.workspace / "versions" / f"v{v_num}_widdle.json"

            versions[v_num_str] = VersionInfo(
                version_number=v_num,
                status=v_data.get("status", "draft"),
                is_published=v_data.get("is_published", False),
                description=v_data.get("description"),
                created_at=v_data.get("created_at"),
                updated_at=v_data.get("updated_at"),
                checked_out_at=v_data.get("checked_out_at"),
                has_local_copy=local_copy_path.exists(),
            )

        return WorkspaceMetadata(
            workflow_id=data.get("workflow_id", self.workspace.name),
            title=data.get("title", ""),
            agent_name=data.get("agent_name"),
            remote=remote,
            working_version=working_version,
            remote_versions=remote_versions,
            versions=versions,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def _to_dict(self, metadata: WorkspaceMetadata) -> Dict[str, Any]:
        """Convert WorkspaceMetadata to dict for JSON serialization."""
        versions_dict = {}
        for v_num, v_info in metadata.versions.items():
            versions_dict[str(v_num)] = {
                "version_number": v_info.version_number,
                "status": v_info.status,
                "is_published": v_info.is_published,
                "description": v_info.description,
                "created_at": v_info.created_at,
                "updated_at": v_info.updated_at,
                "checked_out_at": v_info.checked_out_at,
                "has_local_copy": v_info.has_local_copy,
            }

        return {
            "workflow_id": metadata.workflow_id,
            "title": metadata.title,
            "agent_name": metadata.agent_name,
            "remote": {
                "action_id": metadata.remote.action_id,
                "linked_at": metadata.remote.linked_at,
                "last_synced_at": metadata.remote.last_synced_at,
                "sync_status": metadata.remote.sync_status,
            },
            "working_version": {
                "number": metadata.working_version.number,
                "status": metadata.working_version.status,
                "is_published": metadata.working_version.is_published,
                "local_wdl_hash": metadata.working_version.local_wdl_hash,
                "remote_wdl_hash": metadata.working_version.remote_wdl_hash,
                "has_local_changes": metadata.working_version.has_local_changes,
                "description": metadata.working_version.description,
                "checked_out_at": metadata.working_version.checked_out_at,
            },
            "remote_versions": {
                "latest_draft": metadata.remote_versions.latest_draft,
                "latest_published": metadata.remote_versions.latest_published,
                "current": metadata.remote_versions.current,
            },
            "versions": versions_dict,
            "created_at": metadata.created_at,
            "updated_at": metadata.updated_at,
            # Keep backward compatibility fields
            "action_id": metadata.remote.action_id,
            "current_version": metadata.working_version.number,
            "checked_out_version": metadata.working_version.number if metadata.working_version.checked_out_at else None,
        }

    # =========================================================================
    # Action ID Protection & Recovery
    # =========================================================================

    def get_action_id(self) -> Optional[str]:
        """
        Get action_id with fallback chain.

        Priority:
        1. .action_id file (protected)
        2. metadata.json remote.action_id
        3. None (caller should attempt recovery)

        Returns:
            Action ID or None
        """
        # Check protected file first
        protected_id = self._load_protected_action_id()
        if protected_id:
            return protected_id

        # Check metadata
        metadata = self.load()
        if metadata.remote.action_id:
            # Also save to protected file
            self._save_protected_action_id(metadata.remote.action_id)
            return metadata.remote.action_id

        return None

    def set_action_id(self, action_id: str) -> None:
        """
        Set action_id in both protected file and metadata.

        Args:
            action_id: The action ID to save
        """
        self._save_protected_action_id(action_id)

        metadata = self.load()
        metadata.remote.action_id = action_id
        metadata.remote.linked_at = datetime.now().isoformat()
        metadata.remote.sync_status = "synced"
        self.save(metadata)

    def _load_protected_action_id(self) -> Optional[str]:
        """Load action_id from protected .action_id file."""
        if self.action_id_path.exists():
            action_id = self.action_id_path.read_text().strip()
            if action_id:
                return action_id
        return None

    def _save_protected_action_id(self, action_id: str) -> None:
        """Save action_id to protected .action_id file."""
        self.action_id_path.write_text(action_id)

    # =========================================================================
    # WDL Hash & Change Detection
    # =========================================================================

    def calculate_wdl_hash(self, wdl: Optional[List[Dict[str, Any]]] = None) -> str:
        """
        Calculate SHA256 hash of WDL content.

        Args:
            wdl: WDL to hash (loads from file if not provided)

        Returns:
            SHA256 hash string
        """
        if wdl is None:
            if not self.wdl_path.exists():
                return ""
            wdl = json.loads(self.wdl_path.read_text())

        # Normalize JSON for consistent hashing
        normalized = json.dumps(wdl, sort_keys=True, separators=(',', ':'))
        return f"sha256:{hashlib.sha256(normalized.encode()).hexdigest()}"

    def check_local_changes(self) -> bool:
        """
        Check if local WDL has changed from last saved version.

        Returns:
            True if there are unsaved local changes
        """
        if not self.wdl_path.exists():
            return False

        metadata = self.load()
        if not metadata.working_version.remote_wdl_hash:
            # No remote hash means first save needed
            return True

        current_hash = self.calculate_wdl_hash()
        return current_hash != metadata.working_version.remote_wdl_hash

    def update_wdl_hashes(self, is_saved: bool = False) -> None:
        """
        Update WDL hashes after WDL modification or save.

        Args:
            is_saved: If True, mark as synced (no local changes)
        """
        metadata = self.load()
        current_hash = self.calculate_wdl_hash()

        metadata.working_version.local_wdl_hash = current_hash

        if is_saved:
            metadata.working_version.remote_wdl_hash = current_hash
            metadata.working_version.has_local_changes = False
            metadata.remote.sync_status = "synced"
            metadata.remote.last_synced_at = datetime.now().isoformat()
        else:
            metadata.working_version.has_local_changes = (
                current_hash != metadata.working_version.remote_wdl_hash
            )
            if metadata.working_version.has_local_changes:
                metadata.remote.sync_status = "pending_save"

        self.save(metadata)

    # =========================================================================
    # Version Management
    # =========================================================================

    def set_working_version(
        self,
        version_number: int,
        status: str = "draft",
        is_published: bool = False,
        description: Optional[str] = None,
    ) -> None:
        """
        Set the current working version.

        Args:
            version_number: Version number
            status: "draft" or "approved"
            is_published: Whether version is published
            description: Version description
        """
        metadata = self.load()

        metadata.working_version.number = version_number
        metadata.working_version.status = status
        metadata.working_version.is_published = is_published
        metadata.working_version.description = description
        metadata.working_version.checked_out_at = datetime.now().isoformat()

        # Update local hash
        if self.wdl_path.exists():
            metadata.working_version.local_wdl_hash = self.calculate_wdl_hash()

        self.save(metadata)

    def update_version_info(
        self,
        version_number: int,
        status: Optional[str] = None,
        is_published: Optional[bool] = None,
        description: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        """
        Update information for a specific version.

        Args:
            version_number: Version number to update
            status: New status (optional)
            is_published: Published flag (optional)
            description: Version description (optional)
            created_at: Creation timestamp (optional)
            updated_at: Update timestamp (optional)
        """
        metadata = self.load()

        v_key = str(version_number)
        if v_key not in metadata.versions:
            # Check for local copy
            local_copy_path = self.workspace / "versions" / f"v{version_number}_widdle.json"

            metadata.versions[v_key] = VersionInfo(
                version_number=version_number,
                has_local_copy=local_copy_path.exists(),
            )

        v_info = metadata.versions[v_key]

        if status is not None:
            v_info.status = status
        if is_published is not None:
            v_info.is_published = is_published
        if description is not None:
            v_info.description = description
        if created_at is not None:
            v_info.created_at = created_at
        if updated_at is not None:
            v_info.updated_at = updated_at

        # Check for local copy
        local_copy_path = self.workspace / "versions" / f"v{version_number}_widdle.json"
        v_info.has_local_copy = local_copy_path.exists()

        self.save(metadata)

    def sync_versions_from_api(self, versions_from_api: List[Dict[str, Any]]) -> None:
        """
        Sync all versions from API response.

        Args:
            versions_from_api: List of version dicts from API
        """
        metadata = self.load()

        latest_draft = None
        latest_published = None
        current = None

        for v in versions_from_api:
            v_num = v.get("version_number")
            if not v_num:
                continue

            v_key = str(v_num)
            status = v.get("status", "pending_approval")
            is_published = status == "approved"

            # Track remote version state
            if v.get("is_current_version", False):
                current = v_num

            if is_published:
                if latest_published is None or v_num > latest_published:
                    latest_published = v_num
            else:
                if latest_draft is None or v_num > latest_draft:
                    latest_draft = v_num

            # Check for local copy
            local_copy_path = self.workspace / "versions" / f"v{v_num}_widdle.json"

            # Preserve local data if exists
            existing = metadata.versions.get(v_key)

            metadata.versions[v_key] = VersionInfo(
                version_number=v_num,
                status=status,
                is_published=is_published,
                description=v.get("change_reason", existing.description if existing else None),
                created_at=v.get("created_at", existing.created_at if existing else None),
                updated_at=v.get("updated_at", existing.updated_at if existing else None),
                checked_out_at=existing.checked_out_at if existing else None,
                has_local_copy=local_copy_path.exists(),
            )

        # Update remote versions summary
        metadata.remote_versions.latest_draft = latest_draft
        metadata.remote_versions.latest_published = latest_published
        metadata.remote_versions.current = current
        metadata.remote.last_synced_at = datetime.now().isoformat()

        self.save(metadata)

    # =========================================================================
    # Test Version Selection (Transparent Testing)
    # =========================================================================

    @dataclass
    class TestTarget:
        """Test target information."""
        version: Optional[int]
        allow_draft: bool
        reason: str

    def determine_test_version(self) -> "MetadataManager.TestTarget":
        """
        Automatically determine which version to test.

        Priority order:
        1. If has_local_changes=True: needs save first (caller handles)
        2. If working_version exists: test that version
        3. If remote has draft: test latest draft
        4. Fallback: test latest published

        Returns:
            TestTarget with version, allow_draft flag, and reason
        """
        metadata = self.load()

        # Check for local changes
        has_changes = self.check_local_changes()
        if has_changes:
            metadata.working_version.has_local_changes = True
            self.save(metadata)

        # If we have a working version, use it
        working = metadata.working_version
        if working.number:
            return self.TestTarget(
                version=working.number,
                allow_draft=not working.is_published,
                reason=f"Testing working version {working.number} ({working.status})"
                       + (" - has local changes" if working.has_local_changes else ""),
            )

        # Fallback to remote latest
        remote = metadata.remote_versions
        if remote.latest_draft:
            return self.TestTarget(
                version=remote.latest_draft,
                allow_draft=True,
                reason=f"Testing latest draft version {remote.latest_draft}",
            )

        if remote.latest_published:
            return self.TestTarget(
                version=remote.latest_published,
                allow_draft=False,
                reason=f"Testing latest published version {remote.latest_published}",
            )

        if remote.current:
            return self.TestTarget(
                version=remote.current,
                allow_draft=False,
                reason=f"Testing current version {remote.current}",
            )

        # No version info available
        return self.TestTarget(
            version=None,
            allow_draft=False,
            reason="Testing latest published (default - no version info)",
        )

    def needs_save_before_test(self) -> bool:
        """
        Check if workspace needs to save before testing.

        Returns:
            True if there are unsaved local changes and action_id exists
        """
        metadata = self.load()

        # Need to save if:
        # 1. Has local changes
        # 2. Has action_id (can save to remote)
        if not metadata.remote.action_id:
            return False

        return self.check_local_changes()


# =========================================================================
# Helper Functions (for backward compatibility)
# =========================================================================

def load_metadata(workspace: Path) -> WorkspaceMetadata:
    """Load metadata from workspace (convenience function)."""
    manager = MetadataManager(workspace)
    return manager.load()


def get_action_id(workspace: Path) -> Optional[str]:
    """Get action_id from workspace (convenience function)."""
    manager = MetadataManager(workspace)
    return manager.get_action_id()


def set_action_id(workspace: Path, action_id: str) -> None:
    """Set action_id for workspace (convenience function)."""
    manager = MetadataManager(workspace)
    manager.set_action_id(action_id)

