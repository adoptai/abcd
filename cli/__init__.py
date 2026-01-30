"""
CLI tools for Tool Builder.

This package provides all CLI functionality for:
- Authentication (auth.py)
- Agent management (agents.py)
- Tool management (list_tools.py, search_tools.py, create_tools.py, checkout_tools.py)
- API search (search_apis.py)
- WDL workflow management (manage_wdl_action.py, test_wdl_action.py, etc.)
- Workspace management (workspace.py)
"""

# Lazy imports to avoid loading heavy dependencies (faiss, etc.) until needed

def get_bearer_token():
    """Get authentication bearer token."""
    from .auth import get_bearer_token as _get_bearer_token
    return _get_bearer_token()


def create_agent_interactive():
    """Create agent interactively."""
    from .agents import create_agent_interactive as _fn
    return _fn()


def delete_agent_interactive():
    """Delete agent interactively."""
    from .agents import delete_agent_interactive as _fn
    return _fn()


def select_agent_interactive():
    """Select agent interactively."""
    from .agents import select_agent_interactive as _fn
    return _fn()


def list_tools_interactive():
    """List tools interactively."""
    from .list_tools import list_tools_interactive as _fn
    return _fn()


def search_tools_interactive():
    """Search tools interactively."""
    from .search_tools import search_tools_interactive as _fn
    return _fn()


def search_apis_interactive():
    """Search APIs interactively."""
    from .search_apis import search_apis_interactive as _fn
    return _fn()


def create_tools_interactive():
    """Create tools interactively."""
    from .create_tools import create_tools_interactive as _fn
    return _fn()


def checkout_tools_interactive():
    """Checkout tools interactively."""
    from .checkout_tools import checkout_tools_interactive as _fn
    return _fn()


# WDL common utilities - also lazy
def _get_wdl_common():
    from . import wdl_common
    return wdl_common


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
]
