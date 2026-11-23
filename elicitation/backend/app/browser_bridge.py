"""In-memory command queue for browser <-> backend communication.

Commands are created by MCP tool handlers and consumed by the Chrome extension
via polling. Results flow back through asyncio.Event synchronization.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 30


@dataclass
class BrowserCommand:
    """A command queued for the Chrome extension to execute."""

    id: str
    command_type: str
    params: dict
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict | None = None


# Commands waiting to be picked up by the extension
_pending: dict[str, BrowserCommand] = {}

# Commands currently being executed by the extension
_active: dict[str, BrowserCommand] = {}


def create_command(command_type: str, params: dict) -> BrowserCommand:
    cmd = BrowserCommand(
        id=str(uuid.uuid4()),
        command_type=command_type,
        params=params,
    )
    _pending[cmd.id] = cmd
    logger.info("Browser command created: %s (%s)", cmd.id, command_type)
    return cmd


def get_pending_commands() -> list[BrowserCommand]:
    """Get all pending commands and move them to active."""
    if not _pending:
        return []
    commands = list(_pending.values())
    for cmd in commands:
        _pending.pop(cmd.id)
        _active[cmd.id] = cmd
    return commands


def set_result(cmd_id: str, result: dict) -> bool:
    """Set the result for an active command and signal the waiting MCP tool."""
    cmd = _active.pop(cmd_id, None)
    if cmd is None:
        logger.warning("set_result called for unknown command: %s", cmd_id)
        return False
    cmd.result = result
    cmd.event.set()
    logger.info("Browser command result set: %s", cmd_id)
    return True


async def execute_command(command_type: str, params: dict) -> dict:
    """Create a command, wait for the extension to execute it, return the result.

    Raises:
        TimeoutError: If the extension does not respond within COMMAND_TIMEOUT_SECONDS.
    """
    cmd = create_command(command_type, params)
    try:
        await asyncio.wait_for(cmd.event.wait(), timeout=COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _pending.pop(cmd.id, None)
        _active.pop(cmd.id, None)
        raise TimeoutError(
            f"Browser command '{command_type}' timed out after {COMMAND_TIMEOUT_SECONDS}s. "
            "Is the Chrome extension running and connected?"
        )
    return cmd.result
