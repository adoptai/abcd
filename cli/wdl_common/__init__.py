"""
WDL Common Utilities Package.

Shared utilities for WDL action CLI tools:
- API client for Adopt
- Tool/API discovery from platform
- Workspace management (integrates with tool_builder_agents/)
- Documentation path provider for Roaming RAG
- Trace analyzer for test failures
- Roaming instructions builder for Cursor
"""

from .wdl_documentation import WDLDocumentationProvider
from .api_client import AdoptAPIClient
from .trace_analyzer import TraceAnalyzer, TraceIssue
from .cursor_prompt_builder import RoamingInstructionsBuilder
from .tool_discovery import ToolDiscovery
from .workspace_manager import WorkspaceManager
from .workspace_manager import (
    HierarchicalWorkspaceManager,
    WorkspaceType,
    get_workspace_manager,
    WORKSPACES_DIR,
)
from .metadata_manager import (
    MetadataManager,
    WorkspaceMetadata,
    RemoteState,
    WorkingVersion,
    RemoteVersions,
    VersionInfo,
    load_metadata,
    get_action_id,
    set_action_id,
)
from .validator import (
    WDLValidator,
    ValidationResult,
    validate_wdl,
    validate_wdl_file,
)
from .error_patterns import (
    enhance_error_message,
    detect_error_type,
    is_auto_fixable,
    get_fix_suggestion,
    format_api_error,
)

__all__ = [
    "WDLDocumentationProvider",
    "AdoptAPIClient",
    "TraceAnalyzer",
    "TraceIssue",
    "RoamingInstructionsBuilder",
    "ToolDiscovery",
    "WorkspaceManager",
    "HierarchicalWorkspaceManager",
    "WorkspaceType",
    "get_workspace_manager",
    "WORKSPACES_DIR",
    "MetadataManager",
    "WorkspaceMetadata",
    "RemoteState",
    "WorkingVersion",
    "RemoteVersions",
    "VersionInfo",
    "load_metadata",
    "get_action_id",
    "set_action_id",
    "WDLValidator",
    "ValidationResult",
    "validate_wdl",
    "validate_wdl_file",
    "enhance_error_message",
    "detect_error_type",
    "is_auto_fixable",
    "get_fix_suggestion",
    "format_api_error",
]
