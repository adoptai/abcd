"""BffHarnessDriver against a faked HarnessAPIClient (BFF_ROUTES.md section 4)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from cli.builder_common.errors import PlatformError, UsageError
from cli.builder_common.evals import runner as R  # noqa: N812
from cli.builder_common.evals.bff_driver import BffHarnessDriver
from cli.builder_common.evals.protocols import HarnessDriver, TurnHandle

from .evals_fakes import AGENT, PROCESS, MemStore, work_product_ws


class FakeClient:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.status: dict[str, Any] = {"status": "COMPLETED", "terminal": True}
        self.events: list[dict[str, Any]] = []
        self.start_reply: dict[str, Any] | None = None
        self.headers = {"Authorization": "Bearer x"}

    def start_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.started.append(payload)
        return self.start_reply or {
            "turn_id": "turn-1",
            "workflow_id": "wf",
            "conversation_id": payload["conversation_id"],
        }

    def turn_status(self, turn_id: str, conversation_id: str) -> dict[str, Any]:
        self.requests.append(("status", turn_id, {"conversation_id": conversation_id}))
        return self.status

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        self.requests.append((method, path, kw))
        if path.endswith("/transcript"):
            return {"events": self.events}
        return {}

    def _url(self, path: str) -> str:
        return f"https://bff.invalid{path}"


class Resp:
    def __init__(self, content: bytes, code: int = 200) -> None:
        self.content, self.status_code = content, code


H = TurnHandle("turn-1", "conv-1", "ds:returns:c1")


def test_is_a_harness_driver() -> None:
    assert isinstance(BffHarnessDriver(FakeClient()), HarnessDriver)  # type: ignore[arg-type]


def test_start_turn_payload_matches_the_bff() -> None:
    c = FakeClient()
    h = BffHarnessDriver(c).start_turn(workstream="ds:returns:c1", process_id="p1", message="go")  # type: ignore[arg-type]
    p = c.started[0]
    assert set(p) == {"user_message", "conversation_id", "workstream_id", "process_id"}
    assert (
        p["user_message"] == "go"
        and p["workstream_id"] == "ds:returns:c1"
        and p["process_id"] == "p1"
    )
    assert re.fullmatch(r"[0-9a-f]{32}", p["conversation_id"])  # live: a prefix is never created
    assert h == TurnHandle("turn-1", p["conversation_id"], "ds:returns:c1")


def test_model_is_sent_only_when_chosen() -> None:
    """Live: the org default is the priciest model, evals need ``--model`` (GET /models ids)."""
    c = FakeClient()
    BffHarnessDriver(c, model="claude-haiku-4-5").start_turn(  # type: ignore[arg-type]
        workstream="ds:returns:c1", process_id="p1", message="go"
    )
    assert c.started[0]["model"] == "claude-haiku-4-5"


def test_continues_a_conversation_when_given_an_id() -> None:
    c = FakeClient()
    h = BffHarnessDriver(c).start_turn(
        workstream="w", process_id="p", message="m", conversation_id="conv-7"
    )  # type: ignore[arg-type]
    assert c.started[0]["conversation_id"] == "conv-7" and h.conversation_id == "conv-7"


def test_credential_confirmation_is_never_acknowledged() -> None:
    c = FakeClient()
    c.start_reply = {"requires_confirmation": True, "confirmation_type": "credential_in_message"}
    with pytest.raises(PlatformError, match="confirmation"):
        BffHarnessDriver(c).start_turn(workstream="w", process_id="p", message="m")  # type: ignore[arg-type]
    assert "credential_warning_acknowledged" not in c.started[0]


def test_missing_turn_id_is_an_error() -> None:
    c = FakeClient()
    c.start_reply = {"workflow_id": "x"}
    with pytest.raises(PlatformError, match="turn_id"):
        BffHarnessDriver(c).start_turn(workstream="w", process_id="p", message="m")  # type: ignore[arg-type]


def test_status_mapping() -> None:
    c = FakeClient()
    d = BffHarnessDriver(c)  # type: ignore[arg-type]
    c.status = {"status": "RUNNING", "terminal": False}
    assert d.turn_status(H).state == "running"
    c.status = {"status": "UNKNOWN", "terminal": False}
    assert d.turn_status(H).state == "running"
    for raw in ("FAILED", "TIMED_OUT", "TERMINATED", "CANCELED"):
        c.status = {"status": raw, "terminal": True}
        s = d.turn_status(H)
        assert s.state == "failed" and raw in (s.error or "")
    assert ("status", "turn-1", {"conversation_id": "conv-1"}) in c.requests


def test_completed_turn_reads_final_text_and_usage_from_the_transcript() -> None:
    c = FakeClient()
    c.events = [
        {"kind": "user_message", "turn_id": "turn-1", "text": "go"},
        {"kind": "final", "turn_id": "turn-1", "text": "Done."},
        {"kind": "final", "turn_id": "turn-0", "text": "OLD"},
        {
            "kind": "usage",
            "turn_id": "turn-1",
            "steps": 5,
            "tokens": 900,
            "cost_usd": 0.12,
            "duration_s": 8.5,
        },
    ]
    s = BffHarnessDriver(c).turn_status(H)  # type: ignore[arg-type]
    assert s.state == "completed" and s.final_text == "Done."
    assert (s.steps, s.tokens, s.cost_usd, s.duration_s, s.hitl_count) == (5, 900, 0.12, 8.5, 0)
    method, path, kw = c.requests[-1]
    assert method == "GET" and path == "/v1/end-user/agent-harness/conversations/conv-1/transcript"
    assert kw["params"] == {"workstream_id": "ds:returns:c1"}


def test_held_question_or_form_is_needs_input_even_though_the_turn_completed() -> None:
    c = FakeClient()
    c.events = [
        {"kind": "question", "turn_id": "turn-1", "text": "Which method?"},
        {"kind": "builder", "turn_id": "turn-1", "title": "Approve"},
    ]
    s = BffHarnessDriver(c).turn_status(H)  # type: ignore[arg-type]
    assert s.state == "needs_input" and s.question == "Which method?" and s.hitl_count == 2


def test_fetch_outputs_downloads_artifacts_of_this_turn() -> None:
    c = FakeClient()
    c.events = [
        {"kind": "artifact", "turn_id": "turn-1", "path": "return.json"},
        {"kind": "artifact", "turn_id": "turn-1", "path": "return.json"},
        {"kind": "artifact", "turn_id": "turn-0", "path": "old.json"},
        {"kind": "final", "turn_id": "turn-1", "text": "x"},
    ]
    gets: list[tuple[str, dict[str, Any]]] = []

    def http_get(url: str, **kw: Any) -> Resp:
        gets.append((url, kw["params"]))
        return Resp(b'{"a": 1}')

    out = BffHarnessDriver(c, http_get=http_get).fetch_outputs(H)  # type: ignore[arg-type]
    assert out == {"return.json": b'{"a": 1}'}
    assert gets == [
        (
            "https://bff.invalid/v1/end-user/agent-harness/conversations/conv-1/outputs/download",
            {"workstream_id": "ds:returns:c1", "path": "return.json"},
        )
    ]


def test_download_error_is_a_platform_error() -> None:
    c = FakeClient()
    c.events = [{"kind": "artifact", "turn_id": "turn-1", "path": "x.json"}]
    d = BffHarnessDriver(c, http_get=lambda url, **kw: Resp(b"", 403))  # type: ignore[arg-type]
    with pytest.raises(PlatformError, match="403"):
        d.fetch_outputs(H)


def test_transcript_and_stop() -> None:
    c = FakeClient()
    c.events = [
        {"kind": "final", "turn_id": "turn-1", "text": "a"},
        {"kind": "user_message", "text": "b"},
    ]
    d = BffHarnessDriver(c)  # type: ignore[arg-type]
    assert len(d.fetch_transcript(H).splitlines()) == 2
    d.stop_turn(H)
    method, path, kw = c.requests[-1]
    assert (method, path) == ("POST", "/v1/end-user/agent-harness/turns/turn-1/stop")
    assert kw["json"] == {"conversation_id": "conv-1", "workstream_id": "ds:returns:c1"}


def test_runner_works_end_to_end_over_the_bff_driver(tmp_path) -> None:
    """The runner needs nothing from the driver beyond the protocol."""
    ws, store = work_product_ws(tmp_path, 1)
    c = FakeClient()
    c.events = [
        {"kind": "final", "turn_id": "turn-1", "text": "ok"},
        {"kind": "artifact", "turn_id": "turn-1", "path": "return.json"},
    ]
    body = b'{"entity": "Zenith Orchard Partners", "total_tax": 48214.77}'
    d = BffHarnessDriver(c, http_get=lambda url, **kw: Resp(body))  # type: ignore[arg-type]
    res = R.run_eval(
        ws,
        driver=d,
        store=store,
        dataset="returns",
        agent=AGENT,
        process_id=PROCESS,
        sleep=lambda s: None,
    )
    assert res.cases[0].passed is True
    assert c.started[0]["user_message"].startswith("Prepare the return")
    assert isinstance(store, MemStore)


def test_workstream_names_resolve_to_ids_and_unknown_ones_are_refused() -> None:
    c = FakeClient()
    d = BffHarnessDriver(c, workstream_ids={"ds:returns:c1": "ws-111"})  # type: ignore[arg-type]
    h = d.start_turn(workstream="ds:returns:c1", process_id="p1", message="go")
    assert c.started[0]["workstream_id"] == "ws-111" and h.workstream == "ws-111"
    d.start_turn(workstream="ws-111", process_id="p1", message="go")  # an id passes through
    assert c.started[1]["workstream_id"] == "ws-111"
    with pytest.raises(UsageError, match="ds:returns:c9.*no recorded id"):
        d.start_turn(workstream="ds:returns:c9", process_id="p1", message="go")
    assert len(c.started) == 2  # nothing was sent for the unknown one


def test_fetch_outputs_reads_the_platforms_artifact_event_fields() -> None:
    c = FakeClient()
    c.events = [
        {"kind": "artifact", "turn_id": "turn-1", "relative_path": "outputs/r.json",
         "filename": "r.json", "artifact_id": "a1", "sandbox_path": "/workspace/outputs/r.json"},
        {"kind": "artifact", "turn_id": "turn-1", "filename": "only-name.txt"},
        {"kind": "artifact", "turn_id": "turn-0", "relative_path": "old.json"},
    ]  # fmt: skip
    got: list[str] = []

    def http_get(url: str, **kw: Any) -> Resp:
        got.append(kw["params"]["path"])
        return Resp(b"x")

    out = BffHarnessDriver(c, http_get=http_get).fetch_outputs(H)  # type: ignore[arg-type]
    assert sorted(out) == sorted(got) == ["only-name.txt", "outputs/r.json"]
