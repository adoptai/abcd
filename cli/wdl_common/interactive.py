#!/usr/bin/env python3
"""
Interactive prompts for the diagnostic toolkit.

Provides user interaction for confirming changes and navigating options.
"""

from typing import Any

try:
    from colorama import Fore, Style
    from colorama import init as colorama_init

    colorama_init()
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

    class Fore:  # type: ignore[no-redef]
        RED = ""
        GREEN = ""
        YELLOW = ""
        CYAN = ""
        WHITE = ""
        RESET = ""

    class Style:  # type: ignore[no-redef]
        BRIGHT = ""
        RESET_ALL = ""


def prompt_confirmation(
    message: str,
    default: bool = False,
) -> bool:
    """
    Prompt user for yes/no confirmation.

    Args:
        message: Message to display
        default: Default value if user just presses Enter

    Returns:
        True if user confirmed, False otherwise
    """
    default_str = "Y/n" if default else "y/N"

    while True:
        response = input(f"{message} [{default_str}]: ").strip().lower()

        if not response:
            return default
        elif response in ("y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        else:
            print("⚠️  Please enter 'y' or 'n'")


def prompt_fix_action(
    fix_number: int,
    total_fixes: int,
    fix_summary: str,
) -> str:
    """
    Prompt user for action on a fix.

    Args:
        fix_number: Current fix number (1-indexed)
        total_fixes: Total number of fixes
        fix_summary: Summary of the fix

    Returns:
        One of: 'apply', 'skip', 'apply_all', 'quit'
    """
    print(f"\n{'=' * 60}")
    print(f"Fix {fix_number}/{total_fixes}: {fix_summary}")
    print(f"{'=' * 60}")
    print("[y] Apply this fix")
    print("[n] Skip this fix")
    print("[a] Apply all remaining fixes")
    print("[q] Quit (save progress)")
    print(f"{'=' * 60}")

    while True:
        response = input("👉 Choose action: ").strip().lower()

        if response in ("y", "yes"):
            return "apply"
        elif response in ("n", "no", "s", "skip"):
            return "skip"
        elif response in ("a", "all"):
            return "apply_all"
        elif response in ("q", "quit", "exit"):
            return "quit"
        else:
            print("⚠️  Please enter 'y', 'n', 'a', or 'q'")


def prompt_api_confirmation(
    api_id: str,
    api_title: str,
    old_path: str,
    new_path: str,
    match_score: float,
    matched_url: str | None = None,
) -> str:
    """
    Prompt user to confirm an API path change.

    Args:
        api_id: The API ID
        api_title: The API title
        old_path: Current path
        new_path: New path
        match_score: Match score from log matching
        matched_url: The matched log URL

    Returns:
        One of: 'apply', 'skip', 'apply_all', 'quit'
    """
    print(f"\n{'=' * 70}")
    print("📝 API PATH UPDATE")
    print(f"{'=' * 70}")
    print(f"API: {api_title}")
    print(f"ID:  {api_id}")
    print(
        f"\n{Fore.RED if COLORAMA_AVAILABLE else ''}Current Path: {old_path}{Style.RESET_ALL if COLORAMA_AVAILABLE else ''}"
    )
    print(
        f"{Fore.GREEN if COLORAMA_AVAILABLE else ''}New Path:     {new_path}{Style.RESET_ALL if COLORAMA_AVAILABLE else ''}"
    )
    print(f"\nMatch Score: {match_score:.2%}")
    if matched_url:
        print(f"Matched URL: {matched_url[:80]}...")
    print(f"{'=' * 70}")
    print("[y] Apply  [n] Skip  [a] Apply All  [q] Quit")
    print(f"{'=' * 70}")

    while True:
        response = input("👉 Action: ").strip().lower()

        if response in ("y", "yes"):
            return "apply"
        elif response in ("n", "no", "s"):
            return "skip"
        elif response in ("a", "all"):
            return "apply_all"
        elif response in ("q", "quit"):
            return "quit"
        else:
            print("⚠️  Please enter 'y', 'n', 'a', or 'q'")


def prompt_tool_confirmation(
    tool_id: str,
    tool_title: str,
    changes_summary: str,
    show_diff: bool = True,
) -> str:
    """
    Prompt user to confirm a tool WDL change.

    Args:
        tool_id: The tool ID
        tool_title: The tool title
        changes_summary: Summary of changes
        show_diff: Whether diff was already shown

    Returns:
        One of: 'apply', 'skip', 'apply_all', 'quit'
    """
    print(f"\n{'=' * 70}")
    print("🔧 TOOL WDL UPDATE")
    print(f"{'=' * 70}")
    print(f"Tool: {tool_title}")
    print(f"ID:   {tool_id}")
    print(f"\nChanges: {changes_summary}")
    print(f"{'=' * 70}")
    print("[y] Apply  [n] Skip  [a] Apply All  [q] Quit")
    print(f"{'=' * 70}")

    while True:
        response = input("👉 Action: ").strip().lower()

        if response in ("y", "yes"):
            return "apply"
        elif response in ("n", "no", "s"):
            return "skip"
        elif response in ("a", "all"):
            return "apply_all"
        elif response in ("q", "quit"):
            return "quit"
        else:
            print("⚠️  Please enter 'y', 'n', 'a', or 'q'")


def display_api_review_prompt(
    api_id: str,
    api_title: str,
    canonical_path: str,
    corrected_path: str,
    match_score: float,
    matched_url: str,
) -> None:
    """
    Display a review prompt for an API path change (without user input).

    Args:
        api_id: The API ID
        api_title: The API title
        canonical_path: Current canonical path
        corrected_path: Suggested corrected path
        match_score: Match confidence score
        matched_url: The matched network log URL
    """
    print(f"\n{'=' * 70}")
    print(f"📝 API: {api_title}")
    print(f"   ID: {api_id}")
    print(f"   Current Path:   {canonical_path}")
    print(f"   Corrected Path: {corrected_path}")
    print(f"   Match Score:    {match_score:.2%}")
    print(f"   Evidence:       {matched_url[:60]}...")
    print(f"{'=' * 70}")


def display_tool_review_prompt(
    tool_id: str,
    tool_title: str,
    changes: list[dict[str, Any]],
) -> None:
    """
    Display a review prompt for tool WDL changes (without user input).

    Args:
        tool_id: The tool ID
        tool_title: The tool title
        changes: List of change dictionaries
    """
    print(f"\n{'=' * 70}")
    print(f"🔧 Tool: {tool_title}")
    print(f"   ID: {tool_id}")
    print(f"   Changes ({len(changes)}):")
    for change in changes[:5]:  # Show first 5 changes
        field = change.get("field", "unknown")
        old_val = str(change.get("old_value", ""))[:40]
        new_val = str(change.get("new_value", ""))[:40]
        print(f"      • {field}: {old_val} → {new_val}")
    if len(changes) > 5:
        print(f"      ... and {len(changes) - 5} more")
    print(f"{'=' * 70}")


def display_progress(
    current: int,
    total: int,
    description: str = "",
) -> None:
    """
    Display a progress indicator.

    Args:
        current: Current item number
        total: Total items
        description: Optional description
    """
    percentage = (current / total * 100) if total > 0 else 0
    bar_length = 30
    filled = int(bar_length * current / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_length - filled)

    desc = f" {description}" if description else ""
    print(f"\r[{bar}] {current}/{total} ({percentage:.0f}%){desc}", end="", flush=True)


def print_success(message: str) -> None:
    """Print a success message."""
    if COLORAMA_AVAILABLE:
        print(f"{Fore.GREEN}✅ {message}{Style.RESET_ALL}")
    else:
        print(f"✅ {message}")


def print_error(message: str) -> None:
    """Print an error message."""
    if COLORAMA_AVAILABLE:
        print(f"{Fore.RED}❌ {message}{Style.RESET_ALL}")
    else:
        print(f"❌ {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    if COLORAMA_AVAILABLE:
        print(f"{Fore.YELLOW}⚠️  {message}{Style.RESET_ALL}")
    else:
        print(f"⚠️  {message}")


def print_info(message: str) -> None:
    """Print an info message."""
    if COLORAMA_AVAILABLE:
        print(f"{Fore.CYAN}ℹ️  {message}{Style.RESET_ALL}")
    else:
        print(f"ℹ️  {message}")
