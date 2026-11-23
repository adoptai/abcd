"""Tests for question CRUD endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_question(client):
    proj = await client.post("/projects", json={"name": "Q Project"})
    pid = proj.json()["id"]

    resp = await client.post(f"/projects/{pid}/questions", json={
        "content": "How many values in this dropdown?",
        "context_url": "https://example.com/form",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["content"] == "How many values in this dropdown?"
    assert data["status"] == "open"
    assert data["context_url"] == "https://example.com/form"
    assert data["project_id"] == pid
    assert data["process_id"] is None


@pytest.mark.asyncio
async def test_create_question_with_process(client):
    proj = await client.post("/projects", json={"name": "Q Project 2"})
    pid = proj.json()["id"]
    proc = await client.post(f"/projects/{pid}/processes", json={"name": "Proc"})
    proc_id = proc.json()["id"]

    resp = await client.post(f"/projects/{pid}/questions", json={
        "content": "What does this validation mean?",
        "process_id": proc_id,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["process_id"] == proc_id


@pytest.mark.asyncio
async def test_list_questions(client):
    proj = await client.post("/projects", json={"name": "Q List"})
    pid = proj.json()["id"]

    await client.post(f"/projects/{pid}/questions", json={"content": "Q1"})
    await client.post(f"/projects/{pid}/questions", json={"content": "Q2"})

    resp = await client.get(f"/projects/{pid}/questions")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_questions_filter_status(client):
    proj = await client.post("/projects", json={"name": "Q Filter"})
    pid = proj.json()["id"]

    q1 = await client.post(f"/projects/{pid}/questions", json={"content": "Open Q"})
    q2 = await client.post(f"/projects/{pid}/questions", json={"content": "Answered Q"})
    await client.put(f"/questions/{q2.json()['id']}", json={"status": "answered", "answer": "42"})

    resp = await client.get(f"/projects/{pid}/questions?status=open")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["content"] == "Open Q"


@pytest.mark.asyncio
async def test_update_question(client):
    proj = await client.post("/projects", json={"name": "Q Update"})
    pid = proj.json()["id"]

    q = await client.post(f"/projects/{pid}/questions", json={"content": "Original?"})
    qid = q.json()["id"]

    resp = await client.put(f"/questions/{qid}", json={
        "answer": "The answer is 42",
        "status": "answered",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "The answer is 42"
    assert data["status"] == "answered"


@pytest.mark.asyncio
async def test_delete_question(client):
    proj = await client.post("/projects", json={"name": "Q Delete"})
    pid = proj.json()["id"]

    q = await client.post(f"/projects/{pid}/questions", json={"content": "Delete me"})
    qid = q.json()["id"]

    resp = await client.delete(f"/questions/{qid}")
    assert resp.status_code == 204

    resp = await client.get(f"/questions/{qid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_question_not_found(client):
    resp = await client.get("/questions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_question_timeline_event(client):
    proj = await client.post("/projects", json={"name": "Q Timeline"})
    pid = proj.json()["id"]

    await client.post(f"/projects/{pid}/questions", json={"content": "Timeline Q"})

    resp = await client.get(f"/timeline?project_id={pid}")
    assert resp.status_code == 200
    events = resp.json()
    question_events = [e for e in events if e["event_type"] == "question"]
    assert len(question_events) >= 1
    assert "Timeline Q" in question_events[0]["summary"]
