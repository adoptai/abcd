"""
WDL Common Utilities Package.

Shared utilities for WDL action CLI tools:
- API client for Adopt
- Action/API discovery from platform (FAISS + fuzzy search)
- Hierarchical workspace management (Environment required)
- Documentation path provider for Roaming RAG
- Trace analyzer for test failures
- Roaming instructions builder for Cursor

**IMPORTANT**: All operations require an environment. The 'default' environment
is auto-created and activated. Use `workspace.py env` commands to manage environments.
"""

from .api_client import AdoptAPIClient, get_api_client_for_env
from .cursor_prompt_builder import RoamingInstructionsBuilder
from .discovery import Discovery, DiscoveryCache, EmbeddingManager, get_discovery
from .error_patterns import (
    detect_error_type,
    enhance_error_message,
    format_api_error,
    get_fix_suggestion,
    is_auto_fixable,
)
from .metadata_manager import (
    MetadataManager,
    RemoteState,
    RemoteVersions,
    VersionInfo,
    WorkingVersion,
    WorkspaceMetadata,
    get_action_id,
    load_metadata,
    set_action_id,
)
from .trace_analyzer import TraceAnalyzer, TraceIssue
from .validator import (
    ValidationResult,
    WDLValidator,
    validate_wdl,
    validate_wdl_file,
)
from .wdl_documentation import WDLDocumentationProvider
from .workspace_manager import (
    DEFAULT_ENV,
    WORKSPACES_DIR,
    HierarchicalWorkspaceManager,
    WorkspaceManager,
    WorkspaceType,
    get_workspace_manager,
)

__all__ = [
    "WDLDocumentationProvider",
    "AdoptAPIClient",
    "get_api_client_for_env",
    "TraceAnalyzer",
    "TraceIssue",
    "RoamingInstructionsBuilder",
    "Discovery",
    "DiscoveryCache",
    "EmbeddingManager",
    "get_discovery",
    "WorkspaceManager",
    "HierarchicalWorkspaceManager",
    "WorkspaceType",
    "get_workspace_manager",
    "WORKSPACES_DIR",
    "DEFAULT_ENV",
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
