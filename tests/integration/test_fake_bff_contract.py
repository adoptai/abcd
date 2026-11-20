"""The fake BFF itself: the documented status codes and auth rules, checked over plain HTTP.

These guard the fake against drifting from BFF_ROUTES.md, so a green end-to-end run means
something. They do not use the abcd CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from tests.integration import fake_agent as fa
from tests.integration.fake_bff import (
    BASE,
    DEFAULT_CLIENT_ID,
    DEFAULT_SECRET,
    FakeBFFServer,
    make_jwt,
    route_table,
)

pytestmark = pytest.mark.integration


def token(
    bff: FakeBFFServer, client_id: str = DEFAULT_CLIENT_ID, secret: str = DEFAULT_SECRET
) -> dict[str, str]:
    res = requests.post(
        f"{bff.url}/v1/users/api-token", json={"client_id": client_id, "secret": secret}, timeout=10
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def call(
    bff: FakeBFFServer, method: str, path: str, headers: dict[str, str], **kw: Any
) -> requests.Response:
    return requests.request(method, bff.url + path, headers=headers, timeout=10, **kw)


@pytest.fixture
def staff(bff: FakeBFFServer) -> dict[str, str]:
    bff.state.add_pat("member-client", "member-secret", roles=["Reviewer"], user_id="member-1")
    return token(bff, "member-client", "member-secret")


def test_api_token_validates_its_body(bff: FakeBFFServer) -> None:
    url = f"{bff.url}/v1/users/api-token"
    assert requests.post(url, json={"client_id": "x"}, timeout=10).status_code == 422
    assert (
        requests.post(
            url, json={"client_id": DEFAULT_CLIENT_ID, "secret": "wrong"}, timeout=10
        ).status_code
        == 401
    )
    ok = requests.post(
        url, json={"client_id": DEFAULT_CLIENT_ID, "secret": DEFAULT_SECRET}, timeout=10
    ).json()
    assert (
        set(ok) == {"access_token", "refresh_token", "expires_in"}
        and ok["access_token"].count(".") == 2
    )


def test_turn_start_validation_and_workstream_access(
    bff: FakeBFFServer, staff: dict[str, str]
) -> None:
    admin = token(bff)
    url = BASE + "/turns"
    assert (
        call(bff, "POST", url, admin, json={"conversation_id": "c"}).status_code == 422
    )  # user_message
    assert (
        call(bff, "POST", url, admin, json={"user_message": "hi"}).status_code == 422
    )  # conversation_id
    assert (
        call(
            bff, "POST", url, admin, json={"user_message": "", "conversation_id": "c0"}
        ).status_code
        == 200
    )  # uploads-only
    gone = call(
        bff,
        "POST",
        url,
        admin,
        json={"user_message": "x", "conversation_id": "c1", "workstream_id": "nope"},
    )
    assert gone.status_code == 404
    ws = bff.state.add_workstream("finance")["id"]
    assert (
        call(
            bff,
            "POST",
            url,
            staff,
            json={"user_message": "x", "conversation_id": "c2", "workstream_id": ws},
        ).status_code
        == 403
    )
    for open_ws in ("general", "unscoped"):
        res = call(
            bff,
            "POST",
            url,
            staff,
            json={"user_message": "x", "conversation_id": f"c-{open_ws}", "workstream_id": open_ws},
        )
        assert res.status_code == 200, open_ws
    bff.state.grants["member-1"] = {ws}
    assert (
        call(
            bff,
            "POST",
            url,
            staff,
            json={"user_message": "x", "conversation_id": "c3", "workstream_id": ws},
        ).status_code
        == 200
    )


def test_credential_shapes_return_requires_confirmation_with_200(bff: FakeBFFServer) -> None:
    admin = token(bff)
    for secret in ("AKIA" + "ABCDEFGHIJKLMNOP", "sk-" + "abcdefghijklmnopqrstuvwx", "ghp_" + "a" * 30, "xoxb-" + "1234567890-abcdef",
                   "Authorization: Bearer abc.def.ghi", "-----BEGIN " + "RSA PRIVATE KEY-----", make_jwt({"a": 1})):  # fmt: skip
        body = {"user_message": f"here: {secret}", "conversation_id": "cc"}
        res = call(bff, "POST", BASE + "/turns", admin, json=body)
        assert res.status_code == 200 and res.json()["requires_confirmation"] is True, secret
        assert "turn_id" not in res.json()
    assert bff.state.turns == {}
    ack = call(bff, "POST", BASE + "/turns", admin,
               json={"user_message": "AKIA" + "ABCDEFGHIJKLMNOP", "conversation_id": "cc", "credential_warning_acknowledged": True})  # fmt: skip
    assert ack.status_code == 200 and "turn_id" in ack.json()


def test_turn_reads_are_owner_scoped_and_need_a_conversation_id(
    bff: FakeBFFServer, staff: dict[str, str]
) -> None:
    admin = token(bff)
    started = call(
        bff, "POST", BASE + "/turns", admin, json={"user_message": "hi", "conversation_id": "mine"}
    ).json()
    tid = started["turn_id"]
    assert (
        call(bff, "GET", f"{BASE}/turns/{tid}/status", admin).status_code == 422
    )  # conversation_id required
    assert (
        call(
            bff, "GET", f"{BASE}/turns/{tid}/status", admin, params={"conversation_id": "mine"}
        ).json()["terminal"]
        is True
    )
    assert (
        call(
            bff, "GET", f"{BASE}/turns/{tid}/stream", staff, params={"conversation_id": "mine"}
        ).status_code
        == 403
    )
    assert (
        call(
            bff, "GET", f"{BASE}/trace/{tid}", admin, params={"conversation_id": "mine"}
        ).status_code
        == 422
    )  # + workstream_id
    assert (
        call(
            bff,
            "GET",
            f"{BASE}/trace/{tid}",
            admin,
            params={"conversation_id": "mine", "workstream_id": "unscoped"},
        ).status_code
        == 200
    )
    assert (
        call(
            bff, "GET", f"{BASE}/turns/{tid}/trace", admin, params={"conversation_id": "mine"}
        ).status_code
        == 404
    )  # workflows-only
    # org-scoped /v1/runs reads work for any JWT, whoever owns the chat
    assert (
        call(bff, "GET", "/v1/runs/detail", staff, params={"run_id": "cv:mine"}).status_code == 200
    )


def test_stream_envelope_shape(bff: FakeBFFServer) -> None:
    admin = token(bff)
    bff.state.script([fa.text("hello"), fa.builder("b-1", step_id="s1"), fa.question("Sure?")])
    tid = call(
        bff, "POST", BASE + "/turns", admin, json={"user_message": "x", "conversation_id": "env"}
    ).json()["turn_id"]
    res = call(
        bff,
        "GET",
        f"{BASE}/turns/{tid}/stream",
        admin,
        params={"conversation_id": "env", "workstream_id": "unscoped"},
    )
    assert res.headers["content-type"] == "application/x-ndjson"
    lines = [__import__("json").loads(x) for x in res.text.splitlines()]
    assert lines[0]["source"] == "keepalive"
    events = [line["event"] for line in lines if line["source"] == "redis"]
    assert all(e["type"] == "response" for e in events)
    kinds = [e["data"]["block"]["type"] for e in events if e["event_name"] == "block_delta"]
    assert kinds == ["text_delta", "builder", "process_state", "question"]
    done = events[-1]
    assert done["event_name"] == "harness_turn_complete"
    assert {
        "turn_id",
        "stop_reason",
        "completed",
        "iterations_used",
        "max_iterations",
        "failure",
    } <= set(done["data"])
    card = next(
        e["data"]["block"] for e in events if e["data"].get("block", {}).get("type") == "builder"
    )
    assert card["builder_id"] == "b-1" and card["step_id"] == "s1" and card["blocks"][0]["fields"]
    assert [line["redis_id"] for line in lines if line["source"] == "redis"][:2] == ["1-0", "2-0"]


def test_gated_step_is_never_closed_by_the_agent_itself(bff: FakeBFFServer) -> None:
    admin = token(bff)
    bff.state.script([fa.builder("b-1", step_id="s1"), fa.process_state({"s1": "done"})])
    call(bff, "POST", BASE + "/turns", admin, json={"user_message": "x", "conversation_id": "gate"})
    assert bff.state.conversations["gate"]["steps"]["s1"] == {
        "status": "review",
        "builder_id": "b-1",
    }


def test_admin_only_routes_and_the_reads_that_stay_open(
    bff: FakeBFFServer, staff: dict[str, str]
) -> None:
    admin = token(bff)
    pid = call(
        bff, "POST", "/v1/org/processes", admin, json={"name": "p", "display_name": "P"}
    ).json()["id"]
    assert (
        call(
            bff, "POST", "/v1/org/processes", staff, json={"name": "q", "display_name": "Q"}
        ).status_code
        == 403
    )
    assert call(bff, "GET", "/v1/org/processes", staff).status_code == 200
    assert call(bff, "GET", f"/v1/org/processes/{pid}", staff).status_code == 403  # admin-only read
    assert call(bff, "GET", f"/v1/org/processes/{pid}/workstreams", staff).status_code == 200
    assert (
        call(
            bff, "PUT", f"/v1/org/processes/{pid}/workstreams", staff, json={"workstream_ids": []}
        ).status_code
        == 403
    )
    assert call(bff, "POST", f"/v1/org/processes/{pid}/run", staff).status_code == 403
    assert call(bff, "POST", "/v1/org/workstreams", staff, json={"name": "w"}).status_code == 403
    assert (
        call(
            bff,
            "POST",
            "/v1/settings/secrets",
            staff,
            json={"key": "k", "value": "v", "group_name": "g"},
        ).status_code
        == 403
    )
    assert call(bff, "POST", BASE + "/skills", staff, json={}).status_code == 403
    assert call(bff, "GET", "/v1/docstore/stores", staff).status_code == 200  # no admin gate
    assert call(bff, "GET", "/v1/pipelines", staff).status_code == 200  # none either
    assert (
        call(bff, "POST", "/v1/org-db/tables/x/assign-workstream", staff, json={}).status_code
        == 403
    )


def test_end_user_run_uses_only_assigned_and_accessible_workstreams(
    bff: FakeBFFServer, staff: dict[str, str]
) -> None:
    admin = token(bff)
    a, b = bff.state.add_workstream("a")["id"], bff.state.add_workstream("b")["id"]
    pid = call(
        bff,
        "POST",
        "/v1/org/processes",
        admin,
        json={"name": "p", "display_name": "P", "is_enabled": True},
    ).json()["id"]
    call(bff, "PUT", f"/v1/org/processes/{pid}/workstreams", admin, json={"workstream_ids": [a, b]})
    assert (
        call(bff, "POST", f"/v1/processes/{pid}/run", staff).status_code == 403
    )  # no grants at all
    bff.state.grants["member-1"] = {a}
    one = call(bff, "POST", f"/v1/processes/{pid}/run", staff)
    assert one.status_code == 200 and [s["workstream_id"] for s in one.json()["started"]] == [a]
    assert (
        call(
            bff, "POST", f"/v1/processes/{pid}/run", staff, json={"workstream_ids": [b]}
        ).status_code
        == 400
    )
    assert (
        call(bff, "POST", f"/v1/org/processes/{pid}/run", admin).json()["count"] == 2
    )  # test run: all of them


def test_unknown_routes_and_methods(bff: FakeBFFServer) -> None:
    admin = token(bff)
    assert call(bff, "GET", "/v1/nope", admin).status_code == 404
    assert call(bff, "DELETE", BASE + "/turns", admin).status_code == 405
    assert call(bff, "GET", "/healthz", admin).status_code == 404
    assert [r["status"] for r in bff.requests[-3:]] == [404, 405, 404]


def test_presigned_urls_need_a_signature_and_are_not_bff_routes(bff: FakeBFFServer) -> None:
    assert (
        requests.put(f"{bff.url}/_presigned/put/k/file.txt", data=b"x", timeout=10).status_code
        == 403
    )
    assert (
        requests.put(
            f"{bff.url}/_presigned/put/k/file.txt?sig=abc", data=b"x", timeout=10
        ).status_code
        == 200
    )
    assert requests.get(f"{bff.url}/_presigned/get/k/file.txt?sig=abc", timeout=10).content == b"x"
    assert (
        requests.get(f"{bff.url}/_presigned/get/k/missing?sig=abc", timeout=10).status_code == 404
    )
    assert bff.requests == [] and len(bff.state.storage_requests) == 4


def test_readme_lists_every_implemented_route() -> None:
    readme = (Path(__file__).parent / "README.md").read_text()
    missing = [r for r in route_table() if f"`{r.rsplit(' [', 1)[0]}`" not in readme]
    assert not missing, f"README.md coverage table is missing: {missing}"
