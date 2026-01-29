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

__all__ = [
    "WDLDocumentationProvider",
    "AdoptAPIClient",
    "TraceAnalyzer",
    "TraceIssue",
    "RoamingInstructionsBuilder",
    "ToolDiscovery",
    "WorkspaceManager",
]
