"""Tests for project CRUD endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_project(client):
    resp = await client.post("/projects", json={"name": "Test Project", "description": "A test"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Project"
    assert data["description"] == "A test"
    assert data["status"] == "active"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_projects(client):
    await client.post("/projects", json={"name": "P1"})
    await client.post("/projects", json={"name": "P2"})
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_project(client):
    create = await client.post("/projects", json={"name": "Detail"})
    pid = create.json()["id"]
    resp = await client.get(f"/projects/{pid}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Detail"
    assert "processes" in resp.json()


@pytest.mark.asyncio
async def test_get_project_not_found(client):
    resp = await client.get("/projects/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_project(client):
    create = await client.post("/projects", json={"name": "Old"})
    pid = create.json()["id"]
    resp = await client.put(f"/projects/{pid}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_project(client):
    create = await client.post("/projects", json={"name": "ToDelete"})
    pid = create.json()["id"]
    resp = await client.delete(f"/projects/{pid}")
    assert resp.status_code == 204
    resp = await client.get(f"/projects/{pid}")
    assert resp.status_code == 404
