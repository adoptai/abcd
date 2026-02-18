"""
CLI tools for ABCD.

This package provides all CLI functionality for:
- Authentication (auth.py)
- WDL workflow management (manage_wdl_action.py, etc.)
- Workspace management (workspace.py)
- Testing (test_runner.py)
- Validation (validate.py)
"""

# Lazy imports to avoid loading heavy dependencies until needed


def get_bearer_token() -> str:
    """Get authentication bearer token."""
    from .auth import get_bearer_token as _get_bearer_token

    return _get_bearer_token()


# WDL common utilities - lazy
def _get_wdl_common() -> object:
    from . import wdl_common

    return wdl_common


__all__ = [
    # Auth
    "get_bearer_token",
]
