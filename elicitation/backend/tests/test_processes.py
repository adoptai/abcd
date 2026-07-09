"""Tests for process CRUD endpoints."""

import pytest


@pytest.fixture
async def project_id(client):
    resp = await client.post("/projects", json={"name": "Parent Project"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_process(client, project_id):
    resp = await client.post(
        f"/projects/{project_id}/processes",
        json={"name": "Login Flow", "description": "User login process"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Login Flow"
    assert data["project_id"] == project_id


@pytest.mark.asyncio
async def test_create_process_project_not_found(client):
    resp = await client.post("/projects/bad-id/processes", json={"name": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_processes(client, project_id):
    await client.post(f"/projects/{project_id}/processes", json={"name": "P1"})
    await client.post(f"/projects/{project_id}/processes", json={"name": "P2"})
    resp = await client.get(f"/projects/{project_id}/processes")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_process(client, project_id):
    create = await client.post(f"/projects/{project_id}/processes", json={"name": "Detail"})
    proc_id = create.json()["id"]
    resp = await client.get(f"/processes/{proc_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Detail"


@pytest.mark.asyncio
async def test_update_process(client, project_id):
    create = await client.post(f"/projects/{project_id}/processes", json={"name": "Old"})
    proc_id = create.json()["id"]
    resp = await client.put(f"/processes/{proc_id}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_process(client, project_id):
    create = await client.post(f"/projects/{project_id}/processes", json={"name": "Bye"})
    proc_id = create.json()["id"]
    resp = await client.delete(f"/processes/{proc_id}")
    assert resp.status_code == 204
    resp = await client.get(f"/processes/{proc_id}")
    assert resp.status_code == 404
