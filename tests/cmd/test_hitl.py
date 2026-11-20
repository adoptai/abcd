"""``abcd hitl`` commands against a fake BFF."""

from __future__ import annotations

import json
import re

import pytest

from tests.cmd.session_fixtures import (
    _BASE,
    CARD,
    FakeBFF,
    complete,
    review_state,
    run,
    start_turn_route,
)

TURNS = f"{_BASE}/turns"
FP = "0123456789abcdef0123456789abcdef"

pytest_plugins = ["tests.cmd.session_fixtures"]


def _transcript(bff: FakeBFF, *extra: dict) -> None:  # type: ignore[type-arg]
    bff.on(
        "GET",
        f"{_BASE}/conversations/c1/transcript",
        {
            "events": [
                {"kind": "user_message", "text": "go", "turn_id": "t1"},
                {"kind": "builder", **CARD},
                *extra,
            ]
        },
    )


def _resolve(*extra: str) -> tuple[str, ...]:
    return ("hitl", "resolve", "c1", "--builder-id", "b-1", *extra)


def test_resolve_builds_exact_envelope_and_reports_step_left_review(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    _transcript(bff)
    start_turn_route(bff, "t2", [review_state("done"), complete()])
    code, doc, _ = run(capsys, *_resolve("--value", "decision=approve", "--value", "note=ok"))
    assert code == 0
    msg = bff.sent("POST", TURNS)[0]["json"]["user_message"]
    expected = (
        '<builder_data>{"submission":{"blocks":[{"id":"blk1","title":"Plan",'
        '"values":{"decision":"approve","note":"ok"}}]},"step_id":"approve_plan","builder_id":"b-1"}'
        "</builder_data>"
    )
    assert msg == expected
    assert bff.sent("POST", TURNS)[0]["json"]["conversation_id"] == "c1"
    assert doc["data"]["step_status"] == "done" and doc["data"]["left_review"] is True


def test_resolve_reports_step_still_in_review(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    _transcript(bff)
    start_turn_route(bff, "t2", [review_state("review"), complete()])
    code, doc, _ = run(capsys, *_resolve("--values", json.dumps({"decision": "approve"})))
    assert code == 0 and doc["data"]["left_review"] is False
    assert doc["data"]["status"] == "needs_input"


def test_resolve_refuses_missing_required_field_and_unknown_key(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    _transcript(bff)
    code, doc, _ = run(capsys, *_resolve("--value", "note=only"))
    assert code == 1 and "decision" in doc["error"]
    code, doc, _ = run(capsys, *_resolve("--value", "decison=approve"))
    assert code == 2 and "unknown field" in doc["error"]
    assert not bff.sent("POST", TURNS)


def test_resolve_refuses_unknown_card(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    _transcript(bff)
    code, doc, _ = run(capsys, "hitl", "resolve", "c1", "--builder-id", "nope", "--value", "a=b")
    assert code == 1 and "no pending builder card" in doc["error"]


def test_resolved_card_is_no_longer_pending(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    answer = '<builder_data>{"submission":{},"builder_id":"b-1"}</builder_data>'
    _transcript(bff, {"kind": "user_message", "text": answer})
    bff.on(
        "GET",
        "/v1/end-user/agent-escalations",
        {"data": [{"id": "e1", "conversation_id": "c1", "question": "ok?"}]},
    )
    bff.on(
        "GET",
        "/v1/runs",
        {"runs": [{"id": "cv:c1", "agent_name": "a", "status": "waiting_on_hitl"}]},
    )
    code, doc, _ = run(capsys, "hitl", "list", "--conversation", "c1")
    assert code == 0
    assert doc["data"]["conversation"]["builders"] == []
    assert doc["data"]["escalations"][0]["id"] == "e1" and doc["data"]["runs"][0]["id"] == "cv:c1"
    assert bff.sent("GET", "/v1/end-user/agent-escalations")[0]["params"]["view"] == "tasks"
    assert bff.sent("GET", "/v1/runs")[0]["params"]["hitl"] == "any"


def test_answer_sends_plain_text_turn(bff: FakeBFF, capsys: pytest.CaptureFixture[str]) -> None:
    start_turn_route(bff, "t2", [complete()])
    code, doc, _ = run(capsys, "hitl", "answer", "c1", "--text", "Q2")
    assert code == 0 and doc["data"]["status"] == "completed"
    body = bff.sent("POST", TURNS)[0]["json"]
    assert body["user_message"] == "Q2" and body["conversation_id"] == "c1"


def test_decide_posts_decision_and_notes_record_only(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    path = f"{_BASE}/escalations/policy-9/decide"
    bff.on("POST", path, {"recorded": True})
    code, doc, _ = run(
        capsys, "hitl", "decide", "policy-9", "--decision", "approve", "--note", "fine"
    )
    assert code == 1 and not bff.sent("POST", path)  # approve needs --yes
    code, doc, _ = run(
        capsys, "hitl", "decide", "policy-9", "--decision", "approve", "--note", "fine", "--yes"
    )
    assert code == 0
    assert bff.sent("POST", path)[0]["json"] == {"decision": "approve", "note": "fine"}
    assert "follow-up turn" in doc["data"]["note"]
    code, doc, _ = run(capsys, "hitl", "decide", "policy-9", "--decision", "reject")
    assert code == 0 and bff.sent("POST", path)[-1]["json"] == {"decision": "reject", "note": ""}


def test_approve_skill_sends_exact_token_and_needs_yes(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    start_turn_route(bff, "t2", [complete()])
    code, doc, _ = run(capsys, "hitl", "approve-skill", "c1", "--fingerprint", FP)
    assert code == 1 and "--yes" in doc["error"] and not bff.sent("POST", TURNS)
    code, doc, _ = run(capsys, "hitl", "approve-skill", "c1", "--fingerprint", FP, "--yes")
    assert code == 0
    msg = bff.sent("POST", TURNS)[0]["json"]["user_message"]
    assert msg == f'<!--adopt:skill-approval v="1" fp="{FP}"-->'
    assert re.fullmatch(r'<!--\s*adopt:skill-approval\s+v="1"\s+fp="([0-9a-f]{32})"\s*-->', msg)
    code, doc, _ = run(capsys, "hitl", "approve-skill", "c1", "--fingerprint", "XYZ", "--yes")
    assert code == 2


def test_decide_text_says_the_platform_continues_the_turn(
    bff: FakeBFF, ws_root, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    from cli.abcd import main

    bff.on("POST", f"{_BASE}/escalations/policy-9/decide", {"recorded": True})
    assert main(["hitl", "decide", "policy-9", "--decision", "reject", "--env", "acme"]) == 0
    out = capsys.readouterr().out
    assert "follow-up turn" in out and "nothing resumes" not in out


def test_decide_recorded_false_is_a_failure(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    """The BFF answers 200 {recorded: false} and re-opens the card when nothing was stored."""
    bff.on("POST", f"{_BASE}/escalations/policy-9/decide", {"recorded": False})
    code, doc, _ = run(capsys, "hitl", "decide", "policy-9", "--decision", "reject")
    assert code == 3 and "did not record" in doc["error"]


def test_decide_on_a_non_policy_card_points_at_resolve(
    bff: FakeBFF, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live: the BFF answers 404 'Escalation not found' for a card that is not a policy."""
    from cli.harness_common.api_client import HarnessAPIError

    path = f"{_BASE}/escalations/builder-toolu_x/decide"
    bff.on("POST", path, HarnessAPIError("POST", path, 404, '{"detail":"Escalation not found"}'))
    code, doc, _ = run(capsys, "hitl", "decide", "builder-toolu_x", "--decision", "reject")
    assert code == 3 and "abcd hitl resolve" in doc["error"]
