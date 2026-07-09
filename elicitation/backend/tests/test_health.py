import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "elicitation-agent-backend"


@pytest.mark.asyncio
async def test_ping(client):
    response = await client.post(
        "/sessions/ping",
        json={"message": "hello from test"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] == "hello from test"
    assert data["echo"] == "pong"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_ping_default_message(client):
    response = await client.post("/sessions/ping", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["received"] == "hello"
    assert data["echo"] == "pong"
