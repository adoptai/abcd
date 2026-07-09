"""Tests for chat endpoints.

Note: SSE streaming tests (POST /chat) are limited because sse_starlette's
event loop handling is incompatible with httpx's ASGI transport in tests.
We test the chat history endpoints and message storage directly.
"""

import pytest


@pytest.fixture
async def project_id(client):
    resp = await client.post("/projects", json={"name": "Chat Test Project"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_chat_requires_valid_project(client):
    resp = await client.post("/chat", json={
        "project_id": "nonexistent-id",
        "content": "Hello",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_history(client, project_id):
    """Test GET /chat/history returns messages in order."""
    await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content": "First message",
    })
    await client.post("/messages", json={
        "project_id": project_id,
        "role": "agent",
        "content": "First response",
    })
    await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content": "Second message",
    })

    resp = await client.get(f"/chat/history?project_id={project_id}")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 3
    assert messages[0]["content"] == "First message"
    assert messages[0]["role"] == "human"
    assert messages[1]["content"] == "First response"
    assert messages[1]["role"] == "agent"
    assert messages[2]["content"] == "Second message"


@pytest.mark.asyncio
async def test_chat_history_with_limit(client, project_id):
    """Test that limit parameter works."""
    for i in range(5):
        await client.post("/messages", json={
            "project_id": project_id,
            "role": "human",
            "content": f"Message {i}",
        })

    resp = await client.get(f"/chat/history?project_id={project_id}&limit=3")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 3


@pytest.mark.asyncio
async def test_chat_history_requires_project_id(client):
    """Test that project_id is required."""
    resp = await client.get("/chat/history")
    assert resp.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_chat_history_with_process_filter(client, project_id):
    """Test filtering chat history by process_id."""
    proc_resp = await client.post(f"/projects/{project_id}/processes", json={"name": "Test Flow"})
    process_id = proc_resp.json()["id"]

    await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content": "Project-level message",
    })
    await client.post("/messages", json={
        "project_id": project_id,
        "process_id": process_id,
        "role": "human",
        "content": "Process-level message",
    })

    resp = await client.get(f"/chat/history?project_id={project_id}&process_id={process_id}")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Process-level message"


@pytest.mark.asyncio
async def test_chat_history_message_fields(client, project_id):
    """Test that chat history returns all expected fields."""
    await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content_type": "voice_transcript",
        "content": "Spoken words",
    })

    resp = await client.get(f"/chat/history?project_id={project_id}")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 1
    msg = messages[0]
    assert "id" in msg
    assert msg["role"] == "human"
    assert msg["content"] == "Spoken words"
    assert msg["content_type"] == "voice_transcript"
    assert "timestamp" in msg


@pytest.mark.asyncio
async def test_chat_history_ordering(client, project_id):
    """Test that messages are returned in chronological order."""
    for i in range(3):
        await client.post("/messages", json={
            "project_id": project_id,
            "role": "human" if i % 2 == 0 else "agent",
            "content": f"Message {i}",
        })

    resp = await client.get(f"/chat/history?project_id={project_id}")
    messages = resp.json()
    # Should be in chronological order (ascending timestamp)
    for i in range(len(messages) - 1):
        assert messages[i]["timestamp"] <= messages[i + 1]["timestamp"]
