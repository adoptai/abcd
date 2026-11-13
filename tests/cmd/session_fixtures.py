"""Fixtures for the session/hitl/pipeline command tests: a fake BFF client, no network."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from cli.abcd import main
from cli.builder_session import SessionClient
from cli.core.context import (
    WORKSPACES_ENV_VAR,
    Context,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.harness_common.api_client import HarnessAPIError
from cli.wdl_common import workspace_manager as wm

Handler = Callable[[str, str, dict[str, Any]], Any]


def env_(name: str, **data: Any) -> dict[str, Any]:
    """A stream envelope for ``event_name``."""
    return {"source": "redis", "event": {"event_name": name, "type": "response", "data": data}}


def blk(btype: str, **fields: Any) -> dict[str, Any]:
    return env_("block_delta", block={"type": btype, **fields})


def complete(stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return env_(
        "harness_turn_complete",
        turn_id="t1",
        stop_reason=stop_reason,
        completed=True,
        iterations_used=3,
        max_iterations=20,
        failure=None,
        **extra,
    )


CARD = {
    "type": "builder",
    "builder_id": "b-1",
    "step_id": "approve_plan",
    "title": "Approve the plan",
    "builder_kind": "form",
    "blocks": [
        {
            "id": "blk1",
            "title": "Plan",
            "fields": [
                {"name": "decision", "field_type": "select", "required": True},
                {"name": "note", "field_type": "text", "required": False},
            ],
        }
    ],
}


def review_state(status: str = "review") -> dict[str, Any]:
    return blk(
        "process_state",
        state={"steps": [{"id": "approve_plan", "title": "Approve", "status": status}]},
    )


class FakeBFF(SessionClient):
    """A ``HarnessAPIClient`` whose transport is a dict of handlers; records every call."""

    def __init__(self) -> None:
        super().__init__("fake-token", "https://bff.invalid")
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.handlers: dict[tuple[str, str], Any] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[str, bytes] = {}

    def on(self, method: str, path: str, response: Any) -> None:
        self.handlers[(method, path)] = response

    def _request(self, method: str, path: str, timeout: int = 60, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        handler = self.handlers.get((method, path))
        if handler is None:
            raise HarnessAPIError(method, path, 404, "no fake route")
        if isinstance(handler, Exception):
            raise handler
        return handler(kwargs) if callable(handler) else handler

    def stream_turn(  # type: ignore[override]
        self, turn_id: str, conversation_id: str, workstream_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(("STREAM", turn_id, {"workstream_id": workstream_id}))
        yield from self.streams.get(turn_id, [])

    def download_output(self, conversation_id: str, workstream_id: str, path: str) -> bytes:
        self.calls.append(("DOWNLOAD", path, {"workstream_id": workstream_id}))
        return self.files[path]

    def sent(self, method: str, path: str) -> list[dict[str, Any]]:
        return [kw for m, p, kw in self.calls if (m, p) == (method, path)]


_BASE = "/v1/end-user/agent-harness"
PROCESS = {"id": "proc-1", "name": "acme-agent", "display_name": "Acme", "is_enabled": True}


@pytest.fixture
def ws_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    original = wm.WORKSPACES_DIR
    target = tmp_path / "workspaces"
    (target / "acme").mkdir(parents=True)
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(target))
    apply_workspaces_override()
    yield target
    monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
    set_workspaces_dir(original)


@pytest.fixture
def bff(monkeypatch: pytest.MonkeyPatch, ws_root: Path) -> FakeBFF:
    fake = FakeBFF()
    fake.on("GET", "/v1/org/processes", {"processes": [PROCESS]})
    fake.on("GET", "/v1/org/workstreams", {"items": [{"id": "ws-1", "name": "acme-ws"}]})
    monkeypatch.setattr(Context, "harness_client", lambda self: fake)
    return fake


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any], str]:
    """Run ``abcd <argv> --env acme --json``; returns (exit code, document, stderr)."""
    code = main([*argv, "--env", "acme", "--json"])
    cap = capsys.readouterr()
    doc = json.loads(cap.out.strip().splitlines()[-1]) if cap.out.strip() else {}
    return code, doc, cap.err


def start_turn_route(fake: FakeBFF, turn_id: str, events: list[dict[str, Any]]) -> None:
    fake.on(
        "POST", f"{_BASE}/turns", {"turn_id": turn_id, "workflow_id": "wf", "conversation_id": "c"}
    )
    # The real BFF stamps the owning turn id on harness_turn_complete; the canned ones say "t1".
    stamped = []
    for e in events:
        ev = e.get("event") or {}
        if ev.get("event_name") == "harness_turn_complete":
            e = {**e, "event": {**ev, "data": {**(ev.get("data") or {}), "turn_id": turn_id}}}
        stamped.append(e)
    fake.streams[turn_id] = stamped
