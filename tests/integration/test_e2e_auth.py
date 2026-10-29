"""Scenario (d): the PAT exchange, per-env token caching, lock-outs and secret hygiene."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.integration.conftest import run_inproc, write_env_file
from tests.integration.fake_bff import (
    DEFAULT_CLIENT_ID,
    DEFAULT_SECRET,
    LOCKED_CLIENT_ID,
    LOCKED_MESSAGE,
    ORG_ID,
    FakeBFFServer,
    decode_jwt,
)

pytestmark = pytest.mark.integration

LOCKED_SECRET = "locked-secret"


def _cache(env_dir: Path) -> dict:
    return json.loads((env_dir / "harness" / ".token_cache.json").read_text())


def test_exchange_is_cached_per_env_until_expiry(cli, bff: FakeBFFServer, acme: Path) -> None:
    assert cli("agent", "list").code == 0
    assert cli("runs", "list").code == 0
    assert cli("conversations", "list").code == 0
    assert bff.state.exchanges == [DEFAULT_CLIENT_ID]  # one exchange served three commands

    cached = _cache(acme)
    claims = decode_jwt(cached["access_token"])
    assert claims is not None
    assert claims["tenantId"] == ORG_ID and claims["roles"] == ["Admin"]
    assert claims["sub"] == f"pat-{DEFAULT_CLIENT_ID}" and claims["userId"] != claims["sub"]
    assert claims["email"].endswith("@example.test")
    assert cached["expires_at"] > time.time() + 3000

    # an expired cache entry is refreshed, a fresh one is not
    (acme / "harness" / ".token_cache.json").write_text(
        json.dumps({"access_token": cached["access_token"], "expires_at": time.time() - 5})
    )
    assert cli("agent", "list").code == 0
    assert len(bff.state.exchanges) == 2
    assert cli("agent", "list").code == 0
    assert len(bff.state.exchanges) == 2


def test_each_env_has_its_own_cache_and_pat(
    cli, bff: FakeBFFServer, acme: Path, ws_dir: Path
) -> None:
    assert run_inproc("ws", "create", "--id", "zenith", "--name", "Zenith").code == 0
    bff.state.add_pat("zenith-client", "zenith-secret", user_id="zenith-user")
    write_env_file(ws_dir / "zenith", bff.url, "zenith-client", "zenith-secret")
    assert cli("agent", "list", env="acme").code == 0
    assert cli("agent", "list", env="zenith").code == 0
    assert cli("agent", "list", env="zenith").code == 0
    assert bff.state.exchanges == [DEFAULT_CLIENT_ID, "zenith-client"]
    assert _cache(acme)["access_token"] != _cache(ws_dir / "zenith")["access_token"]
    assert decode_jwt(_cache(ws_dir / "zenith")["access_token"])["userId"] == "zenith-user"


def test_bad_secret_is_a_clear_error_and_never_echoed(cli, bff: FakeBFFServer, acme: Path) -> None:
    write_env_file(acme, bff.url, DEFAULT_CLIENT_ID, "wrong-secret-value-123")
    res = cli("agent", "list", "--verbose")
    assert res.code == 3  # platform refused the exchange
    assert "401" in res.text and "PAT exchange failed" in res.text
    assert "wrong-secret-value-123" not in res.text
    assert not (acme / "harness" / ".token_cache.json").exists()
    assert bff.state.exchanges == [] and bff.state.failed_exchanges == 1


def test_locked_out_pat_surfaces_the_platforms_message_without_secrets(
    cli, bff: FakeBFFServer, acme: Path
) -> None:
    write_env_file(acme, bff.url, LOCKED_CLIENT_ID, LOCKED_SECRET)
    res = cli("agent", "list", "--verbose")
    assert res.code != 0
    assert "400" in res.text and LOCKED_MESSAGE in res.text
    assert LOCKED_SECRET not in res.text and LOCKED_CLIENT_ID not in res.out
    assert bff.state.exchanges == []
    # nothing past the exchange was attempted
    assert [r["path"] for r in bff.requests] == ["/v1/users/api-token"]


@pytest.mark.parametrize("secret_kind", ["locked", "bad"])
def test_failed_exchange_exits_3(secret_kind, cli, bff: FakeBFFServer, acme: Path) -> None:
    secret = LOCKED_SECRET if secret_kind == "locked" else "nope"
    cid = LOCKED_CLIENT_ID if secret_kind == "locked" else DEFAULT_CLIENT_ID
    write_env_file(acme, bff.url, cid, secret)
    assert cli("agent", "list").code == 3


def test_secrets_never_reach_stdout_logs_or_events(
    cli, bff: FakeBFFServer, acme: Path, fixture_skill: Path
) -> None:
    outputs = [
        cli("skill", "push", str(fixture_skill), "--verbose"),
        cli("agent", "list", "--json", "--verbose"),
        cli("runs", "list", "--verbose"),
    ]
    token = _cache(acme)["access_token"]
    for res in outputs:
        assert DEFAULT_SECRET not in res.text
        assert token not in res.text
    events = (acme / ".builder" / "events.jsonl").read_text()
    assert DEFAULT_SECRET not in events and token not in events
    log = json.dumps(bff.requests)
    assert DEFAULT_SECRET not in log and token not in log  # the fake logs keys, never values


def test_pre_issued_bearer_token_skips_the_exchange(
    cli, bff: FakeBFFServer, acme: Path, monkeypatch
) -> None:
    from tests.integration.fake_bff import make_jwt

    claims = {"tenantId": ORG_ID, "roles": ["Admin"], "sub": "s", "userId": "u", "email": "a@b.test",
              "exp": int(time.time()) + 600}  # fmt: skip
    (acme / ".env").write_text(
        f"ADOPT_WEBUI_ENDPOINT={bff.url}\nADOPT_HARNESS_BEARER_TOKEN={make_jwt(claims)}\n"
    )
    assert cli("agent", "list").code == 0
    assert bff.state.exchanges == [] and bff.state.failed_exchanges == 0


def test_a_forged_or_expired_bearer_is_401(bff: FakeBFFServer) -> None:
    import requests

    from tests.integration.fake_bff import make_jwt

    url = f"{bff.url}/v1/end-user/agent-harness/me"
    assert requests.get(url, timeout=10).status_code == 401
    assert (
        requests.get(url, headers={"Authorization": "Bearer a.b.c"}, timeout=10).status_code == 401
    )
    expired = make_jwt({"tenantId": ORG_ID, "exp": int(time.time()) - 10})
    assert (
        requests.get(url, headers={"Authorization": f"Bearer {expired}"}, timeout=10).status_code
        == 401
    )
    no_tenant = make_jwt({"exp": int(time.time()) + 60})
    assert (
        requests.get(url, headers={"Authorization": f"Bearer {no_tenant}"}, timeout=10).status_code
        == 401
    )


def test_me_reports_what_the_pat_resolves_to(cli, bff: FakeBFFServer) -> None:
    from cli.harness_common.api_client import get_harness_client_for_env

    me = get_harness_client_for_env("acme")._request("GET", "/v1/end-user/agent-harness/me")
    assert me["org_id"] == ORG_ID and me["role"] == "admin"
    assert me["user_id"] == f"user-{DEFAULT_CLIENT_ID}"  # userId, not the token id (sub)
