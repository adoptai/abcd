"""Browser-skill gate: file check plus member approval token."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common.browser_gate import (
    APPROVAL_TOKEN_RE,
    GateRefusal,
    GateRefusedError,
    approved_fingerprints,
    bundle_digest,
    check_browser_skill,
    current_fingerprint,
    is_browser_skill,
    steps_digest,
)
from cli.builder_common.errors import PlatformError, UsageError
from cli.harness_common.api_client import HarnessAPIError

OPS = {
    "operations": [
        {
            "name": "download_statement",
            "kind": "read",
            "tool": "call_web_browser",
            "steps": [{"command": "navigate", "url": "https://bank.example/login"}],
            "parameters": [{"name": "month", "default": "last"}],
        }
    ]
}
CONV = "conv-123"
RESULTS = [{"name": "download_statement", "goal_reached": True}]
BUNDLE: dict[str, Any] = {
    "click_events": [{"candidates": [{"value": "button|Pay"}], "selector": "#go"}]
}


def write_manifest(
    root: Path, bundle: dict[str, Any] | None = BUNDLE, *, steps: str | None = None
) -> None:
    ops = json.loads((root / "operations.json").read_text())["operations"]
    prov: dict[str, str] = {"steps_sha256": steps or steps_digest(ops)}
    if bundle is not None:
        (root / "recording_bundle.json").write_text(json.dumps(bundle))
        prov["bundle_sha256"] = bundle_digest(bundle)
    (root / "manifest.json").write_text(
        json.dumps({"runtime": {"operation_style": "browser"}, "provenance": prov})
    )


def token(fp: str) -> str:
    return f'<!--adopt:skill-approval v="1" fp="{fp}"-->'


class FakeClient:
    def __init__(self, events: list[dict[str, Any]] | Exception) -> None:
        self.events = events
        self.calls: list[str] = []

    def transcript_events(self, conversation_id: str) -> list[dict[str, Any]]:
        self.calls.append(conversation_id)
        if isinstance(self.events, Exception):
            raise self.events
        return self.events


def make_skill(
    root: Path, *, approval: dict[str, Any] | None | str = "ok", conversation: str | None = CONV
) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: bank-statement\ndescription: d\n---\nbody\n")
    (root / "operations.json").write_text(json.dumps(OPS))
    write_manifest(root)
    fp = current_fingerprint(root)
    if approval == "ok":
        doc: dict[str, Any] = {"installable": True, "fingerprint": fp, "operations": RESULTS}
        if conversation:
            doc["conversation_id"] = conversation
        (root / "replay_approval.json").write_text(json.dumps(doc))
    elif isinstance(approval, dict):
        (root / "replay_approval.json").write_text(json.dumps(approval))
    return root


def user(text: str) -> dict[str, Any]:
    return {"kind": "user_message", "text": text}


def test_non_browser_passes_through(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: plain\n---\nx\n")
    (d / "operations.json").write_text(
        json.dumps({"operations": [{"name": "a", "steps": [{"command": "http"}]}]})
    )
    res = check_browser_skill(d, None)
    assert not res.browser and res.mode == "not_browser"


def test_detection_is_fail_closed(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\n---\nuse call_web_browser\n")
    assert is_browser_skill(d)
    e = tmp_path / "e"
    e.mkdir()
    (e / "SKILL.md").write_text("---\nname: x\n---\nhi\n")
    (e / "operations.json").write_text(
        json.dumps({"operations": [{"name": "o", "steps": [{"command": "click_by_text"}]}]})
    )
    assert is_browser_skill(e)


def test_accepts_member_token(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    fp = current_fingerprint(d)
    client = FakeClient([user("hello"), user(f"Approve {token(fp)}")])
    res = check_browser_skill(d, client)
    assert (res.browser, res.mode, res.fingerprint, res.conversation_id) == (
        True,
        "conversation",
        fp,
        CONV,
    )
    assert client.calls == [CONV]


def test_refuses_no_approval_file(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval=None)
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([]))
    assert e.value.reason == "no_approval_file" and e.value.code == 1


def test_refuses_not_approved(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval={"installable": False, "fingerprint": "x"})
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([]))
    assert e.value.reason == "not_approved"


def test_gate_refused_error_alias() -> None:
    assert GateRefusal is GateRefusedError


def test_digests_match_noui(tmp_path: Path) -> None:
    from noui_core.compile import provenance

    from cli.builder_common.browser_gate import _noui  # noqa: F401  (puts noui on sys.path)

    assert steps_digest(OPS["operations"]) == provenance.steps_digest(OPS["operations"])
    assert bundle_digest(BUNDLE) == provenance.bundle_digest(BUNDLE)


def test_steps_digest_ignores_names_and_descriptions() -> None:
    renamed = [{**OPS["operations"][0], "name": "other", "description": "x"}]
    assert steps_digest(renamed) == steps_digest(OPS["operations"])


def reason(d: Path, client: Any = None) -> str:
    with pytest.raises(GateRefusedError) as e:
        check_browser_skill(d, client or FakeClient([]))
    return e.value.reason


def test_refuses_rewritten_steps_before_anything_else(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval=None)  # no approval file either
    ops = json.loads((d / "operations.json").read_text())
    ops["operations"][0]["steps"].append({"command": "click_by_text", "text": "Pay"})
    (d / "operations.json").write_text(json.dumps(ops))
    assert reason(d) == "steps_changed"


def test_rename_does_not_trip_steps_digest(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    ops = json.loads((d / "operations.json").read_text())
    ops["operations"][0]["name"] = "renamed"
    (d / "operations.json").write_text(json.dumps(ops))
    fp = current_fingerprint(d)
    (d / "replay_approval.json").write_text(
        json.dumps(
            {"installable": True, "fingerprint": fp, "conversation_id": CONV, "operations": RESULTS}
        )
    )
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"


def test_refuses_unstamped_manifest(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    write_manifest(d, bundle=None)
    assert reason(d) == "no_provenance"


def test_refuses_missing_bundle(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    (d / "recording_bundle.json").unlink()
    assert reason(d) == "no_recording_bundle"


def test_refuses_unreadable_bundle(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    (d / "recording_bundle.json").write_text("[1]")
    assert reason(d) == "bundle_unreadable"


def test_refuses_bundle_digest_mismatch(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    (d / "recording_bundle.json").write_text(json.dumps({**BUNDLE, "extra": 1}))
    assert reason(d) == "bundle_mismatch"


def test_refuses_bundle_without_observed_locators(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    write_manifest(d, {"click_events": []})
    assert reason(d) == "no_observed_locators"


def test_refuses_invented_locator_but_allows_scoped_and_typed_values(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    bundle = {"click_events": [{"candidates": [{"value": "#menu"}, {"value": "#item"}]}]}
    ops = json.loads((d / "operations.json").read_text())
    steps = ops["operations"][0]["steps"]
    steps[:] = [
        {"command": "click_element", "selector": "#menu #item"},  # observed scoped inside observed
        {"command": "type_into_label", "label": "#menu", "text": "{{from_date}}"},
    ]
    (d / "operations.json").write_text(json.dumps(ops))
    write_manifest(d, bundle)
    (d / "replay_approval.json").write_text(
        json.dumps(
            {
                "installable": True,
                "fingerprint": current_fingerprint(d),
                "conversation_id": CONV,
                "operations": RESULTS,
            }
        )
    )
    fp = current_fingerprint(d)
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"
    steps.append({"command": "click_element", "selector": "#made-up"})
    (d / "operations.json").write_text(json.dumps(ops))
    write_manifest(d, bundle)
    (d / "replay_approval.json").write_text(
        json.dumps(
            {
                "installable": True,
                "fingerprint": current_fingerprint(d),
                "conversation_id": CONV,
                "operations": RESULTS,
            }
        )
    )
    assert reason(d) == "invented_locators"


def test_refuses_abandoned_download_goal(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    write_manifest(
        d,
        {"click_events": [{"candidates": [{"value": "#go"}], "outcome": {"download": True}}]},
    )
    assert reason(d) == "abandoned_goals"


def test_refuses_fingerprint_mismatch(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    ops = json.loads((d / "operations.json").read_text())
    ops["operations"][0]["steps"].append({"command": "click_by_text", "text": "Pay"})
    (d / "operations.json").write_text(json.dumps(ops))
    write_manifest(d)  # re-stamp so only the approval fingerprint is stale
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([]))
    assert e.value.reason == "fingerprint_mismatch"


def test_description_change_does_not_move_fingerprint(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    ops = json.loads((d / "operations.json").read_text())
    ops["operations"][0]["description"] = "reworded"
    (d / "operations.json").write_text(json.dumps(ops))
    fp = current_fingerprint(d)
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"


def test_refuses_unapproved_amendments(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    (d / "amendments.json").write_text(
        json.dumps(
            {
                "amendments": [
                    {
                        "operation": "download_statement",
                        "step_index": 0,
                        "replacement": {"command": "click_by_text", "text": "Go"},
                    }
                ]
            }
        )
    )
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([user(token(current_fingerprint(d)))]))
    assert e.value.reason == "amendments_unapproved"


def test_refuses_no_token(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([user("approve it please")]))
    assert e.value.reason == "no_token"


def test_refuses_token_for_other_fingerprint(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient([user(token("0" * 32))]))
    assert e.value.reason == "token_fingerprint_mismatch"


def test_refuses_token_only_in_assistant_or_tool(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    fp = current_fingerprint(d)
    events = [
        {"kind": "assistant_message", "text": token(fp)},
        {"type": "tool_result", "data": {"content": f"ok {token(fp)}"}},
    ]
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, FakeClient(events))
    assert e.value.reason == "token_not_in_user_message"


def test_refuses_without_recorded_conversation_and_local_flag_accepts(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", conversation=None)
    with pytest.raises(GateRefusal) as e:
        check_browser_skill(d, None)
    assert e.value.reason == "no_conversation_recorded"
    res = check_browser_skill(d, None, local_approval=True, approver="fde@example.com")
    assert (res.mode, res.approver) == ("local", "fde@example.com")
    with pytest.raises(UsageError):
        check_browser_skill(d, None, local_approval=True)


def test_local_flag_falls_back_but_token_wins(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    res = check_browser_skill(d, FakeClient([]), local_approval=True, approver="fde")
    assert res.mode == "local" and res.conversation_id == CONV
    fp = current_fingerprint(d)
    res2 = check_browser_skill(
        d, FakeClient([user(token(fp))]), local_approval=True, approver="fde"
    )
    assert res2.mode == "conversation"


def test_transcript_failure(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    err = HarnessAPIError("GET", "u", 403, "nope")
    with pytest.raises(PlatformError):
        check_browser_skill(d, FakeClient(err))
    assert (
        check_browser_skill(d, FakeClient(err), local_approval=True, approver="a").mode == "local"
    )


# -- regex / rule parity with adoptai-workflows activities.py ---------------

PLATFORM_REGEX = r'<!--\s*adopt:skill-approval\s+v="1"\s+fp="([0-9a-f]{32})"\s*-->'


def test_regex_text_identical_to_platform() -> None:
    assert APPROVAL_TOKEN_RE.pattern == PLATFORM_REGEX


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (token("a" * 32), {"a" * 32}),
        (f'<!--  adopt:skill-approval   v="1"\n fp="{"b" * 32}"  -->', {"b" * 32}),
        (token("A" * 32), set()),
        (token("a" * 31), set()),
        (token("a" * 33), set()),
        (f'<!--adopt:skill-approval v="2" fp="{"a" * 32}"-->', set()),
        (token("a" * 32) + token("c" * 32), {"a" * 32, "c" * 32}),
    ],
)
def test_token_vectors(text: str, expected: set[str]) -> None:
    assert approved_fingerprints([user(text)]) == expected


def test_only_user_message_kind_counts() -> None:
    t = token("a" * 32)
    assert (
        approved_fingerprints(
            [{"kind": "assistant", "text": t}, {"text": t}, "junk", {"kind": "user_message"}]
        )
        == set()
    )  # type: ignore[list-item]


def rewrite_approval(d: Path, **over: Any) -> None:
    doc = {
        "installable": True,
        "fingerprint": current_fingerprint(d),
        "conversation_id": CONV,
        "operations": RESULTS,
        **over,
    }
    (d / "replay_approval.json").write_text(json.dumps(doc))


def test_refuses_contradictory_manifest_even_before_browser_detection(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    ops = {"operations": [{"name": "o", "tool": "call_web_api", "steps": [{"command": "http"}]}]}
    (d / "operations.json").write_text(json.dumps(ops))
    assert reason(d) == "contradictory_manifest"


def test_refuses_unstamped_manifest_before_approval_checks(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval=None)
    write_manifest(d, bundle=None)
    assert reason(d) == "no_provenance"


def test_refuses_not_compiled_when_no_manifest_and_no_approval(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval=None)
    (d / "manifest.json").unlink()
    assert reason(d) == "not_compiled"


def test_unreadable_approval_is_reported_as_such(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", approval=None)
    (d / "replay_approval.json").write_text("{not json")
    assert reason(d) == "approval_unreadable"


def test_refuses_approval_without_operation_results(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    for ops_result in ([], None, "x"):
        rewrite_approval(d, operations=ops_result)
        assert reason(d) == "approval_no_operations"


def test_failing_operation_needs_named_waiver(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    failed = [{"name": "download_statement", "goal_reached": False}]
    rewrite_approval(d, operations=failed)
    assert reason(d) == "failing_not_waived"
    rewrite_approval(d, operations=failed, accepted_failing=[{"operation": "other"}])
    assert reason(d) == "failing_not_waived"
    rewrite_approval(d, operations=failed, accepted_failing=[{"operation": "download_statement"}])
    fp = current_fingerprint(d)
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"


def test_operation_that_only_awaited_the_start_did_not_fail(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s")
    waiting = [
        {
            "name": "download_statement",
            "goal_reached": False,
            "steps": [{"command": "await_member_at_start", "status": "needs_approval"}],
        }
    ]
    rewrite_approval(d, operations=waiting)
    fp = current_fingerprint(d)
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"
    waiting[0]["steps"].append({"command": "click_by_text", "status": "failed"})
    rewrite_approval(d, operations=waiting)
    assert reason(d) == "failing_not_waived"
    waiting[0]["steps"] = 5  # malformed shape must refuse, not raise
    rewrite_approval(d, operations=waiting)
    assert reason(d) == "failing_not_waived"


def test_amendment_approved_by_id_passes(tmp_path: Path) -> None:
    from noui_core.compile.amendments import amendment_id

    d = make_skill(tmp_path / "s")
    amendment = {
        "operation": "download_statement",
        "step_index": 0,
        "replacement": {"command": "click_by_text", "text": "Go"},
    }
    (d / "amendments.json").write_text(json.dumps({"amendments": [amendment]}))
    rewrite_approval(d, approved_amendments=[amendment_id(amendment)])
    fp = current_fingerprint(d)
    assert check_browser_skill(d, FakeClient([user(token(fp))])).mode == "conversation"
