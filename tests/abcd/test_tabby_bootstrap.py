"""cli/tabby_bootstrap.py -- local Tabby from the pinned submodule (stubbed I/O)."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import httpx
import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from cli import tabby_bootstrap as tb  # noqa: E402


def fake_tabby(
    tenants: list[dict] | None = None, *, login_status: int = 200
) -> tuple[httpx.Client, list[tuple[str, str, dict]]]:
    calls: list[tuple[str, str, dict]] = []
    state = {"tenants": list(tenants or [])}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        auth = request.headers.get("authorization")
        if request.url.path == "/login":
            if login_status != 200:
                return httpx.Response(login_status, json={"message": "bad credentials"})
            return httpx.Response(200, json={"token": "admin-jwt", "expires_at": "x"})
        assert auth == "Bearer admin-jwt"
        if request.url.path == "/tenants" and request.method == "GET":
            return httpx.Response(200, json={"data": state["tenants"], "meta": {}})
        if request.url.path == "/tenants" and request.method == "POST":
            t = {"id": "11111111-2222-3333-4444-555555555555", "name": body["name"]}
            state["tenants"].append(t)
            return httpx.Response(201, json={"data": t})  # Tabby wraps responses
        if request.url.path == "/admin/agent-clients":
            assert body["unrestricted_profiles"] is True
            return httpx.Response(
                201,
                json={
                    "client_id": "agent_cl_abc",
                    "client_secret": "secret_sk_xyz",
                    "tenant_id": body["tenant_id"],
                },
            )
        return httpx.Response(404)

    return httpx.Client(base_url="http://tabby.test", transport=httpx.MockTransport(handler)), calls


def test_provision_creates_tenant_and_agent_client() -> None:
    client, calls = fake_tabby()
    creds = tb.provision("http://tabby.test", admin_email="a@b", admin_password="pw", client=client)
    assert creds == {
        "TABBY_API_URL": "http://tabby.test",
        "TABBY_CLIENT_ID": "agent_cl_abc",
        "TABBY_CLIENT_SECRET": "secret_sk_xyz",
        "TABBY_ADMIN_TOKEN": "admin-jwt",
        "TABBY_TENANT_ID": "11111111-2222-3333-4444-555555555555",
        "NOUI_TABBY_AUTH_MODE": "agent_token",
    }
    assert [(m, p) for m, p, _ in calls] == [
        ("POST", "/login"),
        ("GET", "/tenants"),
        ("POST", "/tenants"),
        ("POST", "/admin/agent-clients"),
    ]


def test_provision_reuses_an_existing_tenant() -> None:
    client, calls = fake_tabby([{"id": "existing-tenant", "name": tb.TENANT_NAME}])
    creds = tb.provision("http://tabby.test", admin_email="a@b", admin_password="pw", client=client)
    assert creds["TABBY_TENANT_ID"] == "existing-tenant"
    assert ("POST", "/tenants") not in [(m, p) for m, p, _ in calls]


def test_provision_reports_login_failure() -> None:
    client, _ = fake_tabby(login_status=401)
    with pytest.raises(tb.BootstrapError, match="admin login failed"):
        tb.provision("http://tabby.test", admin_email="a@b", admin_password="bad", client=client)


def test_upsert_env_file_replaces_and_appends(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# keep me\nADOPT_CLIENT_ID=x\nTABBY_API_URL=http://old\n")
    tb.upsert_env_file(env, {"TABBY_API_URL": "http://new", "TABBY_CLIENT_ID": "cid"})
    text = env.read_text()
    assert "# keep me" in text and "ADOPT_CLIENT_ID=x" in text
    assert "TABBY_API_URL=http://new" in text and "http://old" not in text
    assert text.count("TABBY_API_URL=") == 1
    assert "TABBY_CLIENT_ID=cid" in text
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_write_compose_shifts_every_host_port(tmp_path: Path) -> None:
    tabby = tmp_path / "tabby"
    tabby.mkdir()
    (tabby / "docker-compose.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "postgres": {"image": "postgres", "ports": ["5432:5432"]},
                    "nats": {"image": "nats", "ports": ["4222:4222", "8222:8222"]},
                    "worker": {"image": "x"},
                    "minio": {
                        "image": "minio/minio:latest",
                        "ports": ["9000:9000"],
                        "healthcheck": {"test": ["CMD", "mc", "ready", "local"]},
                    },
                }
            }
        )
    )
    path = tb.write_compose(tb.LocalState(tmp_path / "state"), 20000, tabby_dir=tabby)
    doc = yaml.safe_load(path.read_text())
    assert doc["services"]["postgres"]["ports"] == ["25432:5432"]
    assert doc["services"]["nats"]["ports"] == ["24222:4222", "28222:8222"]
    assert "ports" not in doc["services"]["worker"]
    minio = doc["services"]["minio"]
    assert minio["image"] == tb.DEFAULT_MINIO_IMAGE and "healthcheck" not in minio
    assert minio["ports"] == ["29000:9000"]


def test_api_environment_tracks_offset_and_is_stable(tmp_path: Path) -> None:
    state = tb.LocalState(tmp_path)
    env = tb.api_environment(state, 18000, 20000)
    assert env["API_PORT"] == "18000"
    assert env["REDIS_URL"] == "redis://localhost:26379"
    assert env["DATABASE_URL"].endswith(":25432/browser_hitl")
    assert env["MINIO_PORT"] == "29000"
    again = tb.api_environment(state, 18000, 20000)
    assert again["JWT_SIGNING_KEY"] == env["JWT_SIGNING_KEY"]  # reused, tokens survive restarts
    moved = tb.api_environment(state, 18000, 0)
    assert moved["REDIS_URL"] == "redis://localhost:6379"
    assert oct(state.env_file.stat().st_mode)[-3:] == "600"


def test_busy_ports_detects_a_listener() -> None:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        s.listen()
        port = s.getsockname()[1]
        assert tb.busy_ports([port]) == [port]


def test_recording_schema_version(tmp_path: Path) -> None:
    (tmp_path / tb.SCHEMA_FILE).parent.mkdir(parents=True)
    assert tb.recording_schema_version(tmp_path) is None
    (tmp_path / tb.SCHEMA_FILE).write_text("export const RECORDING_SCHEMA_VERSION = 5;\n")
    assert tb.recording_schema_version(tmp_path) == 5


def test_pinned_submodule_has_the_schema_noui_needs() -> None:
    """The pin itself: tabby must be a gitlink and (when checked out) record v5+."""
    sha = tb.pinned_commit()
    assert len(sha) == 40
    if tb.submodule_initialized():
        version = tb.recording_schema_version()
        assert version is not None and version >= tb.MIN_RECORDING_SCHEMA


def test_is_alive_uses_health_live() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert tb.is_alive("http://tabby.test/", client=client)
    assert seen == ["/health/live"]
    assert not tb.is_alive("http://127.0.0.1:1")
