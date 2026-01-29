"""
CLI tools for Tool Builder.

This package provides all CLI functionality for:
- Authentication (auth.py)
- Agent management (agents.py)
- Tool management (list_tools.py, search_tools.py, create_tools.py, checkout_tools.py)
- API search (search_apis.py)
- WDL workflow management (manage_wdl_action.py, test_wdl_action.py, etc.)
- Diagnostics and fixes (diagnose_and_fix.py, etc.)
"""

# Re-export key functions for convenient access
from .auth import get_bearer_token
from .agents import (
    create_agent_interactive,
    delete_agent_interactive,
    select_agent_interactive,
)
from .list_tools import list_tools_interactive
from .search_tools import search_tools_interactive
from .search_apis import search_apis_interactive
from .create_tools import create_tools_interactive
from .checkout_tools import checkout_tools_interactive

# WDL common utilities
from .wdl_common import (
    WDLDocumentationProvider,
    AdoptAPIClient,
    TraceAnalyzer,
    TraceIssue,
    RoamingInstructionsBuilder,
)

__all__ = [
    # Auth
    "get_bearer_token",
    # Agents
    "create_agent_interactive",
    "delete_agent_interactive",
    "select_agent_interactive",
    # Tools
    "list_tools_interactive",
    "search_tools_interactive",
    "create_tools_interactive",
    "checkout_tools_interactive",
    # APIs
    "search_apis_interactive",
    # WDL Common
    "WDLDocumentationProvider",
    "AdoptAPIClient",
    "TraceAnalyzer",
    "TraceIssue",
    "RoamingInstructionsBuilder",
]
