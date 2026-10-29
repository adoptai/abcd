"""`HarnessDriver` backed by the adoptwebui BFF (BFF_ROUTES.md section 4).

    start   POST /v1/end-user/agent-harness/turns
            {user_message, conversation_id (client-generated, required), workstream_id, process_id}
    status  GET  /turns/{id}/status?conversation_id=
    detail  GET  /conversations/{id}/transcript  (final text, questions, HITL cards, artifacts)
    files   GET  /conversations/{id}/outputs/download?workstream_id=&path=

The BFF has no pause state: a turn that asked a question or raised a form ends ``COMPLETED``,
so the driver reads the transcript to tell a held turn (``needs_input``) from a finished one.
A ``requires_confirmation`` reply means a credential-shaped string was in the message; the
driver never acknowledges it and fails instead.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import requests

from cli.builder_common.errors import PlatformError, UsageError
from cli.builder_common.evals.protocols import (
    TURN_COMPLETED,
    TURN_FAILED,
    TURN_NEEDS_INPUT,
    TURN_RUNNING,
    TurnHandle,
    TurnStatus,
)
from cli.harness_common.api_client import HarnessAPIClient

_BASE = "/v1/end-user/agent-harness"
_FAILED = {"FAILED", "TIMED_OUT", "TERMINATED", "CANCELED"}
_RUNNING = {"RUNNING", "UNKNOWN"}
_HELD_KINDS = {"question", "builder"}


class BffHarnessDriver:
    def __init__(
        self,
        client: HarnessAPIClient,
        http_get: Callable[..., Any] = requests.get,
        workstream_ids: Mapping[str, str] | None = None,
        model: str | None = None,
    ) -> None:
        self.client = client
        self.model = model  # a model id (``GET /models``); None = the org's default
        # name -> id. When given, every workstream a turn names must resolve to an id (the BFF
        # only knows ids); without it the value is sent as it is.
        self._workstream_ids = workstream_ids
        self._http_get = http_get  # the binary download route is not JSON, so it is injectable

    def resolve_workstream(self, workstream: str) -> str:
        known = self._workstream_ids
        if known is None or workstream in known.values():
            return workstream
        if workstream not in known:
            raise UsageError(
                f"workstream {workstream!r} has no recorded id; the platform needs ids. Create it "
                "with `abcd dataset create`/`add-case`, or record a live workstream with "
                "`abcd builder setup --agent-workstream NAME`"
            )
        return known[workstream]

    # -- turns -------------------------------------------------------------

    def start_turn(
        self,
        *,
        workstream: str,
        process_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnHandle:
        # plain 32-hex only: an "eval-" prefix starts a turn whose conversation is never created
        # (status, transcript and stream all 404 "Conversation not found"; live 2026-10-29)
        conv = conversation_id or uuid.uuid4().hex
        workstream = self.resolve_workstream(workstream)
        payload = {
            "user_message": message,
            "conversation_id": conv,
            "workstream_id": workstream,
            "process_id": process_id,
        }
        if self.model:
            payload["model"] = self.model
        resp = self.client.start_turn(payload)
        if resp.get("requires_confirmation"):
            raise PlatformError(
                "the platform wants confirmation for a credential-shaped string in the message; "
                "the eval driver does not acknowledge it"
            )
        turn_id = resp.get("turn_id")
        if not turn_id:
            raise PlatformError(f"turn start returned no turn_id: {sorted(resp)}")
        return TurnHandle(str(turn_id), str(resp.get("conversation_id", conv)), workstream)

    def _transcript(self, handle: TurnHandle) -> list[dict[str, Any]]:
        t = self.client._request(
            "GET",
            f"{_BASE}/conversations/{handle.conversation_id}/transcript",
            params={"workstream_id": handle.workstream},
        )
        events = t.get("events", []) if isinstance(t, dict) else []
        return [e for e in events if isinstance(e, dict)]

    def turn_status(self, handle: TurnHandle) -> TurnStatus:
        s = self.client.turn_status(handle.turn_id, handle.conversation_id)
        raw = str(s.get("status", "UNKNOWN")).upper()
        if raw in _RUNNING and not s.get("terminal"):
            return TurnStatus(state=TURN_RUNNING)
        if raw in _FAILED:
            return TurnStatus(state=TURN_FAILED, error=f"turn ended {raw}")
        events = self._transcript(handle)
        mine = [e for e in events if e.get("turn_id", handle.turn_id) == handle.turn_id]
        final = "".join(str(e.get("text", "")) for e in mine if e.get("kind") == "final")
        held = [e for e in mine if e.get("kind") in _HELD_KINDS]
        usage = next((e for e in reversed(mine) if e.get("kind") == "usage"), {})
        out = TurnStatus(
            state=TURN_NEEDS_INPUT if held else TURN_COMPLETED,
            final_text=final,
            steps=int(usage.get("steps", 0) or 0),
            tokens=int(usage.get("tokens", 0) or 0),
            cost_usd=float(usage.get("cost_usd", 0.0) or 0.0),
            duration_s=float(usage.get("duration_s", 0.0) or 0.0),
            hitl_count=len(held),
        )
        if held:
            out.question = str(held[0].get("text") or held[0].get("title") or "")
        return out

    def fetch_outputs(self, handle: TurnHandle) -> dict[str, bytes]:
        # the platform's ArtifactEvent carries ``relative_path`` and ``filename`` (adoptai-workflows
        # agent_harness/models.py); ``path`` is accepted for older transcripts
        paths = [
            str(e.get("relative_path") or e.get("path") or e.get("filename"))
            for e in self._transcript(handle)
            if e.get("kind") == "artifact"
            and (e.get("relative_path") or e.get("path") or e.get("filename"))
            and e.get("turn_id", handle.turn_id) == handle.turn_id
        ]
        out: dict[str, bytes] = {}
        for p in sorted(set(paths)):
            out[p] = self._download(handle, p)
        return out

    def _download(self, handle: TurnHandle, path: str) -> bytes:
        url = self.client._url(f"{_BASE}/conversations/{handle.conversation_id}/outputs/download")
        r = self._http_get(
            url,
            headers=self.client.headers,
            params={"workstream_id": handle.workstream, "path": path},
            timeout=120,
        )
        if r.status_code >= 400:
            raise PlatformError(f"output download {path} failed: {r.status_code}")
        return bytes(r.content)

    def fetch_transcript(self, handle: TurnHandle) -> str:
        return "\n".join(json.dumps(e, sort_keys=True) for e in self._transcript(handle))

    def stop_turn(self, handle: TurnHandle) -> None:
        self.client._request(
            "POST",
            f"{_BASE}/turns/{handle.turn_id}/stop",
            json={"conversation_id": handle.conversation_id, "workstream_id": handle.workstream},
        )
