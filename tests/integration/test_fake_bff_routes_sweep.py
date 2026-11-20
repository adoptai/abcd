"""One request per remaining fake route, so each documented route has a test of its shape."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
import requests

from tests.integration import fake_agent as fa
from tests.integration.fake_bff import BASE, FakeBFFServer
from tests.integration.test_fake_bff_contract import call, token

pytestmark = pytest.mark.integration


@pytest.fixture
def admin(bff: FakeBFFServer) -> dict[str, str]:
    return token(bff)


def _plugin_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(".claude-plugin/plugin.json", '{"name": "sweep-plugin", "version": "2.0.0"}')
        zf.writestr("skills/s1/SKILL.md", "---\nname: s1\ndescription: Skill one.\n---\n# s1\n")
        zf.writestr("skills/s1/notes.md", "notes\n")
    return buf.getvalue()


def test_identity_and_catalog_reads(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    valid = requests.post(f"{bff.url}/v1/users/validate-token", headers=admin, timeout=10).json()
    assert valid["valid"] is True and valid["org_id"] == "adopt-test-org"
    assert requests.post(f"{bff.url}/v1/users/validate-token", timeout=10).json()["valid"] is False
    assert call(bff, "GET", "/v1/users/organization", admin).json()["total"] >= 2
    assert call(bff, "GET", BASE + "/limits", admin).json()["max_plugin_upload_bytes"] > 0
    assert call(bff, "GET", BASE + "/models", admin).json()[0]["is_default"] is True
    assert call(bff, "GET", "/v1/processes", admin).json() == {"processes": []}


def test_plugin_files_and_delete(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    files = {"plugin": ("sweep-plugin.zip", _plugin_zip(), "application/zip")}
    up = requests.post(
        f"{bff.url}{BASE}/plugins",
        headers=admin,
        files=files,
        data={"replace": "false"},
        timeout=10,
    )
    assert up.status_code == 200 and up.json()["skills"][0]["aux_count"] == 1
    assert (
        requests.post(
            f"{bff.url}{BASE}/plugins",
            headers=admin,
            files={"plugin": ("e.zip", b"", "x")},
            timeout=10,
        ).status_code
        == 422
    )
    path = f"{BASE}/plugins/sweep-plugin/skills/s1/files"
    assert call(bff, "GET", path, admin, params={"path": "notes.md"}).json()["content"] == "notes\n"
    assert call(bff, "GET", path, admin, params={"path": "nope.md"}).status_code == 404
    assert call(bff, "DELETE", f"{BASE}/plugins/sweep-plugin", admin).json() == {"deleted": 1}
    assert call(bff, "GET", f"{BASE}/plugins/sweep-plugin", admin).status_code == 404


def test_workstream_update_and_harness_listing(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    ws = call(bff, "POST", "/v1/org/workstreams", admin, json={"name": "Ops"}).json()
    upd = call(
        bff,
        "POST",
        f"/v1/org/workstreams/{ws['id']}/update",
        admin,
        json={"description": "d", "name": None},
    )
    assert upd.json()["description"] == "d" and upd.json()["name"] == "Ops"  # exclude_none
    assert call(bff, "GET", BASE + "/workstreams", admin, params={"ids": ws["id"]}).json()[
        "items"
    ] == [{"id": ws["id"], "name": "Ops"}]
    assert (
        call(bff, "GET", BASE + "/workstreams", admin, params={"search": "zzz"}).json()["items"]
        == []
    )


def test_process_write_workstreams_read(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    pid = call(
        bff, "POST", "/v1/org/processes", admin, json={"name": "p", "display_name": "P"}
    ).json()["id"]
    assert call(bff, "GET", f"/v1/org/processes/{pid}/write-workstreams", admin).json() == {
        "workstream_ids": [],
        "folder_prefix": None,
    }


def test_turn_checkpoint_outputs_trace_and_capabilities(
    bff: FakeBFFServer, admin: dict[str, str]
) -> None:
    bff.state.script([fa.artifact("outputs/a.txt", "payload"), fa.builder("b-9")])
    started = call(
        bff, "POST", BASE + "/turns", admin, json={"user_message": "x", "conversation_id": "sw"}
    ).json()
    tid = started["turn_id"]
    q: dict[str, Any] = {"conversation_id": "sw"}
    cp = call(bff, "GET", f"{BASE}/turns/{tid}/stream-checkpoint", admin, params=q).json()
    assert cp["terminal"] is True and cp["running"] is False
    out = call(
        bff,
        "GET",
        f"{BASE}/conversations/sw/outputs/download",
        admin,
        params={"workstream_id": "unscoped", "path": "outputs/a.txt"},
    )
    assert out.content == b"payload" and "attachment" in out.headers["content-disposition"]
    assert (
        call(
            bff,
            "GET",
            f"{BASE}/conversations/sw/outputs/download",
            admin,
            params={"workstream_id": "unscoped", "path": "x"},
        ).status_code
        == 404
    )
    assert (
        call(bff, "GET", f"{BASE}/conversations/sw/trace", admin).json()["traces"][0]["turn_id"]
        == tid
    )
    assert call(bff, "GET", f"{BASE}/builders/b-9/capabilities", admin).json()["can_act"] is True
    turn_trace = call(
        bff, "GET", "/v1/runs/turn-trace", admin, params={"turn_id": tid, "conversation_id": "sw"}
    )
    assert turn_trace.json()["schemaVersion"] == 7
    assert (
        call(
            bff,
            "GET",
            "/v1/runs/turn-trace",
            admin,
            params={"turn_id": "x", "conversation_id": "sw"},
        ).status_code
        == 404
    )


def test_docstore_store_lifecycle_and_console_twins(
    bff: FakeBFFServer, admin: dict[str, str]
) -> None:
    ws = bff.state.add_workstream("Docs")["id"]
    made = call(
        bff,
        "POST",
        "/v1/docstore/stores/resolve",
        admin,
        json={"name": "reports", "workstream_id": ws},
    )
    again = call(bff, "POST", "/v1/docstore/stores/resolve", admin, json={"name": "reports"})
    assert made.json()["id"] == again.json()["id"]  # create-or-get
    sid = made.json()["id"]
    assert (
        call(bff, "PUT", f"/v1/docstore/stores/{sid}", admin, json={"name": "reports-2"}).json()[
            "name"
        ]
        == "reports-2"
    )
    assert call(bff, "GET", f"/v1/docstore/stores/{sid}", admin).json()["workstream_ids"] == [ws]
    assert (
        call(
            bff, "POST", f"/v1/docstore/stores/{sid}/folders", admin, json={"folder_path": "Q1"}
        ).json()["folder_path"]
        == "Q1"
    )
    batch = call(bff, "POST", f"/v1/docstore/stores/{sid}/upload-urls", admin,
                 json={"files": [{"filename": "a.txt", "file_type": "txt", "size_bytes": 1, "relative_path": "Q1/a.txt"}]}).json()  # fmt: skip
    requests.put(batch["upload_urls"][0]["presigned_url"], data=b"a", timeout=10)
    call(
        bff,
        "POST",
        f"/v1/docstore/stores/{sid}/upload-confirm",
        admin,
        json={"batch_id": batch["batch_id"]},
    )
    twin = call(
        bff,
        "GET",
        f"{BASE}/docstore/stores/{sid}/document-download-url",
        admin,
        params={"relative_path": "Q1/a.txt"},
    )
    assert requests.get(twin.json()["url"], timeout=10).content == b"a"
    assert (
        call(
            bff,
            "GET",
            f"{BASE}/docstore/stores/{sid}/document-download-url",
            admin,
            params={"relative_path": "x"},
        ).status_code
        == 404
    )
    assert [
        s["id"] for s in call(bff, "GET", BASE + "/docstore/stores", admin).json()["stores"]
    ] == [s["id"] for s in reversed(list(bff.state.stores.values())) if s["status"] != "deleted"]
    docs = call(bff, "GET", f"{BASE}/docstore/stores/{sid}/documents", admin).json()["documents"]
    assert (
        call(
            bff, "DELETE", f"/v1/docstore/stores/{sid}/documents/{docs[0]['id']}", admin
        ).status_code
        == 204
    )
    assert (
        call(bff, "DELETE", f"/v1/docstore/stores/{sid}/workstreams/{ws}", admin).status_code == 204
    )
    assert call(bff, "GET", f"/v1/docstore/stores/{sid}", admin).json()["workstream_ids"] == []
    assert call(bff, "DELETE", f"/v1/docstore/stores/{sid}", admin).status_code == 204
    assert call(bff, "GET", f"/v1/docstore/stores/{sid}", admin).status_code == 404


def test_orgdb_provision_patch_and_row_edits(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    assert (
        call(bff, "POST", "/v1/org-db/provision", admin, json={"deployment": "container"}).json()[
            "status"
        ]
        == "ready"
    )
    t = call(
        bff,
        "POST",
        "/v1/org-db/tables",
        admin,
        json={"table_name": "sweep", "columns": [{"name": "k", "type": "text"}]},
    )
    assert t.status_code == 201
    tid = t.json()["id"]
    assert (
        call(bff, "PATCH", f"/v1/org-db/tables/{tid}", admin, json={"description": "hi"}).json()[
            "description"
        ]
        == "hi"
    )
    call(bff, "POST", f"/v1/org-db/tables/{tid}/rows", admin, json={"rows": [{"k": "one"}]})
    rid = call(
        bff,
        "GET",
        f"/v1/org-db/tables/{tid}/rows",
        admin,
        params={"filter_col": "k", "filter_val": "one"},
    ).json()["items"][0]["id"]
    assert (
        call(
            bff, "PATCH", f"/v1/org-db/tables/{tid}/rows/{rid}", admin, json={"data": {"k": "two"}}
        ).json()["k"]
        == "two"
    )
    assert (
        call(
            bff, "PATCH", f"/v1/org-db/tables/{tid}/rows/999", admin, json={"data": {}}
        ).status_code
        == 404
    )
    assert (
        call(bff, "DELETE", f"/v1/org-db/tables/{tid}/rows/{rid}", admin).json()["deleted"] is True
    )
    assert call(bff, "GET", f"/v1/org-db/tables/{tid}/rows", admin).json()["total"] == 0


def test_pipeline_detail_stop_and_test_run(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    pipe = call(bff, "POST", "/v1/pipelines", admin, json={"name": "wdl-one"}).json()
    run = bff.state.add_pipeline_run(pipe["id"], status="running")
    detail = call(bff, "GET", f"/v1/pipelines/{pipe['id']}/runs/{run['id']}/detail", admin)
    assert detail.status_code == 200 and detail.json()["id"] == run["id"]
    assert call(bff, "POST", f"/v1/pipelines/{pipe['id']}/runs/{run['id']}/stop", admin).json() == {
        "stopped": True
    }
    assert (
        call(bff, "GET", f"/v1/pipelines/{pipe['id']}/runs/nope/detail", admin).status_code == 404
    )
    body = {"pipeline_id": pipe["id"], "test_mode": False}
    assert (
        call(bff, "POST", "/v1/pipelines/workflows/test-run", admin, json=body).json()["status"]
        == "succeeded"
    )
    bff.state.add_pipeline_run(pipe["id"], status="running")
    assert (
        call(bff, "POST", "/v1/pipelines/workflows/test-run", admin, json=body).status_code == 409
    )  # one at a time


def test_vault_update(bff: FakeBFFServer, admin: dict[str, str]) -> None:
    call(
        bff,
        "POST",
        "/v1/settings/secrets",
        admin,
        json={"key": "SANDBOX_X", "value": "v1", "group_name": "g"},
    )
    upd = call(
        bff,
        "PUT",
        "/v1/settings/secrets/SANDBOX_X",
        admin,
        json={"value": "v2", "description": "new"},
    )
    assert (
        upd.status_code == 200 and "value" not in upd.json() and upd.json()["description"] == "new"
    )
    assert bff.state.vault["SANDBOX_X"]["value"] == "v2"
    assert (
        call(bff, "PUT", "/v1/settings/secrets/NOPE", admin, json={"value": "v"}).status_code == 404
    )
