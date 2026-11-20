"""The wave-1 platform clients (Data Store, Doc Store, grants, pipelines, vault) against the fake."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.platform import BuilderPlatformClient
from cli.builder_common.tables import TABLES, ensure_all_tables, get_spec
from cli.harness_common.api_client import (
    HarnessAPIClient,
    HarnessAPIError,
    get_harness_client_for_env,
)
from tests.integration import fake_agent as fa
from tests.integration.fake_bff import FakeBFFServer

pytestmark = pytest.mark.integration


@pytest.fixture
def client(acme: Path) -> HarnessAPIClient:
    return get_harness_client_for_env("acme")


@pytest.fixture
def orgdb(client: HarnessAPIClient) -> OrgDBClient:
    return OrgDBClient.from_client(client)


def _row(table: str, key: str, **extra: Any) -> dict[str, Any]:
    spec = get_spec(table)
    return {spec.unique_key: key, **extra}


# -- Data Store ----------------------------------------------------------------------------
def test_ensure_all_tables_is_idempotent(orgdb: OrgDBClient, bff: FakeBFFServer) -> None:
    ws = {"ops": bff.state.add_workstream("builder-ops")["id"],
          "context": bff.state.add_workstream("builder-context")["id"]}  # fmt: skip
    first = ensure_all_tables(orgdb, ws)
    assert len(first) == len(TABLES) and all(r["created"] for r in first.values())
    again = ensure_all_tables(orgdb, ws)
    assert not any(r["created"] or r["added_columns"] for r in again.values())
    assert len(bff.state.tables) == len(TABLES)
    some = next(iter(bff.state.tables.values()))
    assert some["workstreams"] and all(w["permission_mode"] == "write" for w in some["workstreams"])


def test_rows_insert_one_by_one_and_unique_key_is_409(
    orgdb: OrgDBClient, bff: FakeBFFServer
) -> None:
    ensure_all_tables(orgdb, {"ops": bff.state.add_workstream("ops")["id"],
                              "context": bff.state.add_workstream("ctx")["id"]})  # fmt: skip
    table = "bld_dev_events"
    spec = get_spec(table)
    tid = orgdb.table_id(table)
    rows = [_row(table, f"evt-{i}") for i in range(3)]
    first = orgdb.insert_rows(table, rows)
    assert first.ok and first.inserted == 3
    again = orgdb.insert_rows(table, rows + [_row(table, "evt-new")])
    assert again.ok and again.duplicates == 3 and again.inserted == 1  # 409 = already written
    assert len(orgdb.list_all(table)) == 4

    # a raw batch with a duplicate in the middle: earlier rows stay, the call answers 409
    with pytest.raises(HarnessAPIError) as dup:
        orgdb._request("POST", f"/v1/org-db/tables/{tid}/rows",
                       json={"rows": [_row(table, "evt-a"), _row(table, "evt-0"), _row(table, "evt-b")]})  # fmt: skip
    assert dup.value.status_code == 409
    keys = {r[spec.unique_key] for r in orgdb.list_all(table)}
    assert "evt-a" in keys and "evt-b" not in keys  # not atomic
    with pytest.raises(HarnessAPIError) as too_many:
        orgdb._request("POST", f"/v1/org-db/tables/{tid}/rows",
                       json={"rows": [_row(table, f"k{i}") for i in range(501)]})  # fmt: skip
    assert too_many.value.status_code == 422


def test_sql_is_read_only_and_org_wide(orgdb: OrgDBClient, bff: FakeBFFServer) -> None:
    ensure_all_tables(orgdb, {"ops": bff.state.add_workstream("ops")["id"],
                              "context": bff.state.add_workstream("ctx")["id"]})  # fmt: skip
    orgdb.insert_rows("bld_dev_events", [_row("bld_dev_events", f"e{i}") for i in range(3)])
    spec = get_spec("bld_dev_events")
    got = orgdb.query_sql(
        f"SELECT {spec.unique_key} FROM bld_dev_events ORDER BY {spec.unique_key}"
    )
    assert (
        got["row_count"] == 3 and got["rows"][0] == ["e0"] and got["columns"] == [spec.unique_key]
    )
    count = orgdb.query_sql("WITH x AS (SELECT * FROM bld_dev_events) SELECT count(*) AS n FROM x")
    assert count["rows"] == [[3]]
    for bad in ("DELETE FROM bld_dev_events", "SELECT 1; SELECT 2", "DROP TABLE bld_dev_events",
                "SELECT * FROM information_schema.tables"):  # fmt: skip
        with pytest.raises(HarnessAPIError) as exc:
            orgdb.query_sql(bad)
        assert exc.value.status_code == 400, bad


def test_table_delete_needs_its_name_as_confirmation(
    orgdb: OrgDBClient, bff: FakeBFFServer
) -> None:
    made = orgdb.create_table(
        "scratch_table", [{"name": "k", "type": "text", "unique": True, "nullable": False}]
    )
    tid = made["id"]
    with pytest.raises(HarnessAPIError) as exc:
        orgdb._request("DELETE", f"/v1/org-db/tables/{tid}", json={"confirmation": "wrong"})
    assert exc.value.status_code == 400
    with pytest.raises(HarnessAPIError) as dup:
        orgdb.create_table("scratch_table", [{"name": "k", "type": "text"}])
    assert dup.value.status_code == 409
    with pytest.raises(HarnessAPIError) as bad_name:
        orgdb.create_table("Bad-Name", [{"name": "k", "type": "text"}])
    assert bad_name.value.status_code == 422
    orgdb.delete_table(tid, "scratch_table")
    assert bff.state.tables == {}


def test_add_columns_and_workstream_links(orgdb: OrgDBClient, bff: FakeBFFServer) -> None:
    ws = bff.state.add_workstream("ops")["id"]
    tid = orgdb.create_table("notes_t", [{"name": "k", "type": "text"}])["id"]
    orgdb.add_columns(tid, [{"name": "extra", "type": "int"}])
    names = [c["name"] for c in orgdb.get_table(tid)["column_schema"]]
    assert "extra" in names
    orgdb.link_table_workstream(tid, ws, "read")
    orgdb.link_table_workstream(tid, ws, "write")  # re-link replaces, never duplicates
    links = orgdb.get_table(tid)["workstreams"]
    assert len(links) == 1 and links[0]["permission_mode"] == "write"
    orgdb.unlink_table_workstream(tid, ws)
    assert orgdb.get_table(tid)["workstreams"] == []


# -- Doc Store -----------------------------------------------------------------------------
def test_docstore_upload_confirm_list_and_download(
    cli, client: HarnessAPIClient, bff: FakeBFFServer, tmp_path: Path
) -> None:
    assert cli("workstream", "ensure", "ops").code == 0
    sample = tmp_path / "invoices.csv"
    sample.write_text("id,amount\nA-1,100\n")
    seeded = cli("workstream", "seed", "ops", str(sample))
    assert seeded.code == 0, seeded.text
    assert "Confirmed 1 document" in seeded.out

    # presigned PUT went to the fake's storage endpoint, never through the BFF log
    assert [r["method"] for r in bff.state.storage_requests] == ["PUT"]
    assert all(r["path"].startswith("/v1/") for r in bff.requests)
    store = bff.state.stores[next(sid for sid, docs in bff.state.documents.items() if docs)]
    docs = client._request("GET", f"/v1/docstore/stores/{store['id']}/documents")
    assert docs["total"] >= 1
    doc = next(d for d in docs["documents"] if d["filename"] == "invoices.csv")
    assert doc["size_bytes"] == sample.stat().st_size and doc["relative_path"] == "invoices.csv"
    url = client._request(
        "GET", f"/v1/docstore/stores/{store['id']}/documents/{doc['id']}/preview-url"
    )["url"]
    assert url.startswith(bff.url)
    assert requests.get(url, timeout=10).content == sample.read_bytes()
    # re-seeding the same file works because the CLI passes override=true: new document id
    again = cli("workstream", "seed", "ops", str(sample))
    assert again.code == 0, again.text
    live = [d for d in bff.state.documents[store["id"]].values() if d["status"] != "deleted"]
    assert len(live) == 1 and live[0]["id"] != doc["id"]


def test_docstore_collision_rules(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    store = client.create_store("zenith-docs")
    sid = store["id"]
    meta = [
        {
            "filename": "a.pdf",
            "file_type": "pdf",
            "size_bytes": 3,
            "relative_path": "Reports/Q1/a.pdf",
        }
    ]
    batch = client.get_upload_urls(sid, meta)
    for item in batch["upload_urls"]:
        assert requests.put(item["presigned_url"], data=b"abc", timeout=10).status_code == 200
    assert client.confirm_upload(sid, batch["batch_id"])["confirmed"] == 1
    clash = [
        {
            "filename": "A.PDF",
            "file_type": "pdf",
            "size_bytes": 3,
            "relative_path": "reports/q1/A.pdf",
        }
    ]
    with pytest.raises(HarnessAPIError) as exc:  # case-insensitive relative_path is the key
        client.get_upload_urls(sid, clash)
    assert exc.value.status_code == 409
    with pytest.raises(HarnessAPIError) as same_batch:
        client.get_upload_urls(sid, meta + meta)
    assert same_batch.value.status_code == 409
    client.get_upload_urls(sid, clash, override=True)  # soft-deletes the old document
    assert [d["status"] for d in bff.state.documents[sid].values()] == ["deleted"]
    # unconfirmed objects that were never PUT are not documents
    batch2 = client.get_upload_urls(
        sid, [{"filename": "b.txt", "file_type": "txt", "size_bytes": 1}]
    )
    assert client.confirm_upload(sid, batch2["batch_id"])["confirmed"] == 0


def test_workstream_rules(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    ws = client.create_workstream("Finance ops", "desc")
    assert ws["members"] == [] and ws["member_count"] == 0
    with pytest.raises(HarnessAPIError) as dup:
        client.create_workstream("finance OPS")
    assert dup.value.status_code == 409  # case-insensitive
    # a default store is auto-created for every workstream
    assert any(ws["id"] in s["workstream_ids"] for s in bff.state.stores.values())
    listed = client.list_workstreams(search="finance")
    assert [w["id"] for w in listed["items"]] == [ws["id"]]
    store = client.create_store("extra")
    assert client.link_store_to_workstreams(store["id"], [ws["id"]])["linked"] == [ws["id"]]
    with pytest.raises(HarnessAPIError) as general:
        client.link_store_to_workstreams(store["id"], ["general"])
    assert general.value.status_code == 400
    client.delete_workstream(ws["id"])
    assert client.list_workstreams(search="finance")["items"] == []
    with pytest.raises(HarnessAPIError) as gone:
        client._request("GET", f"/v1/org/workstreams/{ws['id']}")
    assert gone.value.status_code == 404


# -- processes: write grants, components --------------------------------------------------
def test_process_write_grants_and_component_sanitising(
    client: HarnessAPIClient, bff: FakeBFFServer
) -> None:
    platform = BuilderPlatformClient.wrap(client)
    ws = client.create_workstream("ops")["id"]
    proc = client.create_process(
        "p1", "P1", components=[{"type": "skill", "source": "org", "ref": "s1", "junk": 1},
                                {"type": "skill", "ref": ""}, {"source": "org"}])  # fmt: skip
    assert proc["components"] == [{"type": "skill", "source": "org", "ref": "s1"}]  # junk dropped
    pid = proc["id"]
    assert [p["name"] for p in client.list_processes()["processes"]] == ["p1"]
    assert "compiled_flow" not in client.list_processes()["processes"][0]
    with pytest.raises(HarnessAPIError) as dup:
        client.create_process("p1", "again")
    assert dup.value.status_code == 409
    with pytest.raises(HarnessAPIError) as unknown:
        client.set_process_workstreams(pid, ["nope"])
    assert unknown.value.status_code == 400
    assert platform.set_write_workstreams(pid, [ws], "outputs/p1")["folder_prefix"] == "outputs/p1"
    with pytest.raises(HarnessAPIError) as no_prefix:
        platform.set_write_workstreams(pid, [ws])
    assert no_prefix.value.status_code == 400
    with pytest.raises(HarnessAPIError) as mixed:
        platform.set_write_workstreams(pid, ["*", ws], "x")
    assert mixed.value.status_code == 400
    assert platform.set_write_workstreams(pid, [])["workstream_ids"] == []  # revoke needs no prefix
    patched = client.update_process(
        pid, is_enabled=True, compiled_flow={"starters": [{"label": "L", "phrase": "p"}]}
    )
    assert patched["is_enabled"] is True
    assert client.list_processes()["processes"][0]["starters"] == [{"label": "L", "phrase": "p"}]
    client.delete_process(pid)
    assert client.list_processes()["processes"] == []
    with pytest.raises(HarnessAPIError) as soft:
        client.get_process(pid)
    assert soft.value.status_code == 404
    assert platform.find_process_by_name("p1") is None


def test_process_run_rules(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    pid = client.create_process("p2", "P2")["id"]
    with pytest.raises(HarnessAPIError) as disabled:
        client.run_process(pid)
    assert disabled.value.status_code == 409
    client.update_process(pid, is_enabled=True)
    with pytest.raises(HarnessAPIError) as no_ws:
        client.run_process(pid)
    assert no_ws.value.status_code == 400 and "no workstreams" in no_ws.value.body


# -- escalations ---------------------------------------------------------------------------
def test_policy_escalation_list_decide_and_double_decide(
    cli, client: HarnessAPIClient, bff: FakeBFFServer
) -> None:
    ws = client.create_workstream("ops")["id"]
    pid = client.create_process("esc", "Esc", is_enabled=True)["id"]
    client.set_process_workstreams(pid, [ws])
    bff.state.script([fa.builder("policy-pay-1", title="Approve payment", assignee={"kind": "role", "value": "admin"})],
                     process="esc")  # fmt: skip
    started = client.start_turn(
        {"user_message": "pay", "conversation_id": "c-esc", "process_id": pid, "workstream_id": ws}
    )
    assert started["conversation_id"] == "c-esc"
    tasks = client._request(
        "GET", "/v1/end-user/agent-escalations", params={"org_id": "x", "view": "tasks"}
    )
    assert [t["builder_id"] for t in tasks["data"]] == ["policy-pay-1"] and tasks["pagination"][
        "total"
    ] == 1
    card = client._request("GET", "/v1/end-user/agent-harness/escalations/policy-pay-1/card")
    assert card["status"] == "open"
    with pytest.raises(HarnessAPIError) as bad:
        client._request(
            "POST",
            "/v1/end-user/agent-harness/escalations/policy-pay-1/decide",
            json={"decision": "maybe"},
        )
    assert bad.value.status_code == 422
    ok = client._request("POST", "/v1/end-user/agent-harness/escalations/policy-pay-1/decide",
                         json={"decision": "approve", "note": "ok"})  # fmt: skip
    assert ok == {"recorded": True}
    with pytest.raises(HarnessAPIError) as again:
        client._request(
            "POST",
            "/v1/end-user/agent-harness/escalations/policy-pay-1/decide",
            json={"decision": "approve"},
        )
    assert again.value.status_code == 409
    done = client._request(
        "GET", "/v1/end-user/agent-escalations", params={"org_id": "x", "view": "tasks"}
    )
    assert done["data"] == []
    # recording the decision does not resume the held call: the conversation just stays as it was
    assert bff.state.escalations["policy-pay-1"]["decision"] == "approve"
    # a policy escalation can also be resolved through a <builder_data> turn
    bff.state.script([fa.builder("policy-pay-2", title="Second")], process="esc", message="again")
    client.start_turn(
        {
            "user_message": "again",
            "conversation_id": "c-esc",
            "process_id": pid,
            "workstream_id": ws,
        }
    )
    payload = {
        "submission": {"blocks": [{"id": "b1", "title": "t", "values": {"decision": "reject"}}]},
        "builder_id": "policy-pay-2",
    }
    client.start_turn({"user_message": f"<builder_data>{json.dumps(payload)}</builder_data>",
                       "conversation_id": "c-esc", "process_id": pid, "workstream_id": ws})  # fmt: skip
    assert bff.state.escalations["policy-pay-2"]["decision"] == "reject"


def test_pipeline_hitl_run_shows_up_in_runs_and_resolves(
    client: HarnessAPIClient, bff: FakeBFFServer
) -> None:
    pipe = client._request("POST", "/v1/pipelines", json={"name": "legacy", "workstream_ids": []})
    run = bff.state.add_pipeline_run(pipe["id"], status="succeeded")
    eid = bff.state.add_pipeline_escalation(pipe["id"], run["id"], "Which account?")
    held = client.list_runs(hitl="any")
    assert [r["id"] for r in held["runs"]] == [f"pr:{run['id']}"] and held["runs"][0][
        "has_open_hitl"
    ]
    tasks = client._request(
        "GET", "/v1/end-user/agent-escalations", params={"org_id": "x", "view": "tasks"}
    )
    assert tasks["data"][0]["pipeline_run_id"] == run["id"]
    client._request("POST", f"/v1/pipelines/{pipe['id']}/runs/{run['id']}/resolve",
                    json={"escalation_id": eid, "message": "Use 1234"})  # fmt: skip
    assert client.list_runs(hitl="any")["runs"] == []


# -- pipelines -----------------------------------------------------------------------------
def test_process_pipeline_lifecycle(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    ws = client.create_workstream("ops")["id"]
    pid = client.create_process("sched", "Scheduled")["id"]
    client.set_process_workstreams(pid, [ws])
    body = {"name": "daily", "process_id": pid, "schedule_type": "cron", "cron_expr": "0 6 * * *"}
    with pytest.raises(HarnessAPIError) as disabled:  # the process must be enabled first
        client._request("POST", "/v1/pipelines", json=body)
    assert disabled.value.status_code == 400
    client.update_process(pid, is_enabled=True)
    with pytest.raises(HarnessAPIError) as mixed:  # workstreams come from the process
        client._request("POST", "/v1/pipelines", json={**body, "workstream_ids": [ws]})
    assert mixed.value.status_code == 400
    with pytest.raises(HarnessAPIError) as missing:
        client._request("POST", "/v1/pipelines", json={**body, "process_id": "nope"})
    assert missing.value.status_code == 404
    pipe = client._request("POST", "/v1/pipelines", json=body)
    assert pipe["state"] == "draft" and pipe["workstream_ids"] == [ws]
    assert pipe["wdl"][0]["operation"] == "RUN_PROCESS" and pipe["created_by"].startswith("pat-")
    assert pipe["next_run_at"] is None  # the scheduler owns this, and nothing fires in draft

    running = client._request(
        "POST", f"/v1/pipelines/{pipe['id']}/update", json={"state": "running"}
    )
    assert running["state"] == "running"
    with pytest.raises(HarnessAPIError) as typ:
        client._request("POST", f"/v1/pipelines/{pipe['id']}/update", json={"skills": ["x"]})
    assert typ.value.status_code == 400
    with pytest.raises(HarnessAPIError) as bad_state:
        client._request("POST", f"/v1/pipelines/{pipe['id']}/update", json={"state": "exploded"})
    assert bad_state.value.status_code == 422
    paused = client._request("POST", f"/v1/pipelines/{pipe['id']}/update", json={"state": "paused"})
    assert paused["state"] == "paused"

    listed = client._request("GET", "/v1/pipelines", params={"state": "paused"})
    assert listed["total"] == 1 and listed["items"][0]["id"] == pipe["id"]
    assert client._request("GET", "/v1/pipelines", params={"state": "running"})["items"] == []
    bff.state.add_pipeline_run(pipe["id"], status="failed", error="boom")
    runs = client._request("GET", f"/v1/pipelines/{pipe['id']}/runs")
    assert runs["total"] == 1 and runs["items"][0]["error_message"] == "boom"
    with pytest.raises(HarnessAPIError) as active:  # a workstream with a live pipeline can't go
        client.delete_workstream(ws)
    assert active.value.status_code == 409
    client._request("DELETE", f"/v1/pipelines/{pipe['id']}")
    assert client._request("GET", "/v1/pipelines")["items"] == []
    with pytest.raises(HarnessAPIError) as gone:
        client._request("GET", f"/v1/pipelines/{pipe['id']}")
    assert gone.value.status_code == 404


# -- vault ---------------------------------------------------------------------------------
def test_vault_values_are_write_only(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    with pytest.raises(HarnessAPIError) as no_group:
        client._request(
            "POST", "/v1/settings/secrets", json={"key": "SANDBOX_TOKEN", "value": "s3cret-value"}
        )
    assert no_group.value.status_code == 422
    made = client._request("POST", "/v1/settings/secrets",
                           json={"key": "SANDBOX_TOKEN", "value": "s3cret-value", "group_name": "netsuite"})  # fmt: skip
    assert "value" not in made and made["group_name"] == "netsuite"
    with pytest.raises(HarnessAPIError) as dup:
        client._request(
            "POST",
            "/v1/settings/secrets",
            json={"key": "SANDBOX_TOKEN", "value": "x", "group_name": "g"},
        )
    assert dup.value.status_code == 409
    listing = client._request("GET", "/v1/settings/secrets")
    assert [s["key"] for s in listing["items"]] == ["SANDBOX_TOKEN"]
    assert "s3cret-value" not in json.dumps(listing) and "s3cret-value" not in json.dumps(
        bff.requests
    )
    assert bff.state.vault["SANDBOX_TOKEN"]["value"] == "s3cret-value"  # stored, never returned
    client._request("DELETE", "/v1/settings/secrets/SANDBOX_TOKEN")
    assert client._request("GET", "/v1/settings/secrets")["items"] == []


def test_every_call_stayed_on_the_bff(client: HarnessAPIClient, bff: FakeBFFServer) -> None:
    client.list_processes()
    client.list_skills()
    assert bff.forbidden_calls() == []
    r = requests.get(
        f"{bff.url}/api/v1/wdl/execute", timeout=10
    )  # a workflows route does not exist here
    assert r.status_code == 404
    assert [x["path"] for x in bff.forbidden_calls()] == ["/api/v1/wdl/execute"]
    r = requests.get(f"{bff.url}/v1/org/processes", headers={"X-Workflows-Secret": "x"}, timeout=10)
    assert r.status_code == 401 and bff.forbidden_calls()[-1]["x_workflows_secret"] is True
