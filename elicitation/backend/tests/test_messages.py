"""Tests for message endpoints."""

import pytest


@pytest.fixture
async def project_id(client):
    resp = await client.post("/projects", json={"name": "Msg Project"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_message(client, project_id):
    resp = await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content": "Hello, world!",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["role"] == "human"
    assert data["content"] == "Hello, world!"
    assert data["content_type"] == "text"


@pytest.mark.asyncio
async def test_create_message_requires_context(client):
    resp = await client.post("/messages", json={
        "role": "human",
        "content": "No context",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_messages_by_project(client, project_id):
    await client.post("/messages", json={
        "project_id": project_id, "role": "human", "content": "First",
    })
    await client.post("/messages", json={
        "project_id": project_id, "role": "agent", "content": "Second",
    })
    resp = await client.get(f"/messages?project_id={project_id}")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "First"
    assert msgs[1]["content"] == "Second"


@pytest.mark.asyncio
async def test_list_messages_by_process(client, project_id):
    proc = await client.post(f"/projects/{project_id}/processes", json={"name": "Flow"})
    proc_id = proc.json()["id"]
    await client.post("/messages", json={
        "process_id": proc_id, "role": "human", "content": "In process",
    })
    resp = await client.get(f"/messages?process_id={proc_id}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_messages_requires_filter(client):
    resp = await client.get("/messages")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_voice_transcript_message(client, project_id):
    resp = await client.post("/messages", json={
        "project_id": project_id,
        "role": "human",
        "content_type": "voice_transcript",
        "content": "This was spoken",
    })
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "voice_transcript"
