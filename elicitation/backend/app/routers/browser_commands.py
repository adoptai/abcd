"""REST endpoints for the browser command queue.

The Chrome extension polls GET /browser-commands/pending to pick up commands
and POSTs results to POST /browser-commands/{command_id}/result.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.browser_bridge import get_pending_commands, set_result

router = APIRouter(prefix="/browser-commands", tags=["browser-commands"])


class BrowserCommandOut(BaseModel):
    id: str
    command_type: str
    params: dict


class CommandResultIn(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None


@router.get("/pending", response_model=list[BrowserCommandOut])
async def get_pending():
    """Return pending commands for the Chrome extension to execute."""
    commands = get_pending_commands()
    return [
        BrowserCommandOut(id=cmd.id, command_type=cmd.command_type, params=cmd.params)
        for cmd in commands
    ]


@router.post("/{command_id}/result")
async def post_result(command_id: str, body: CommandResultIn):
    """Receive execution result from the Chrome extension."""
    result_dict = {
        "success": body.success,
        "data": body.data,
        "error": body.error,
    }
    found = set_result(command_id, result_dict)
    if not found:
        raise HTTPException(404, f"Command {command_id} not found or already completed")
    return {"status": "ok"}
