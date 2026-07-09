from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/sessions", tags=["sessions"])


class PingRequest(BaseModel):
    message: str = "hello"


class PingResponse(BaseModel):
    received: str
    timestamp: str
    echo: str = "pong"


@router.post("/ping", response_model=PingResponse)
async def ping(request: PingRequest):
    """Proof-of-life endpoint. Echoes back the message with a timestamp."""
    return PingResponse(
        received=request.message,
        timestamp=datetime.now(timezone.utc).isoformat(),
        echo="pong",
    )
