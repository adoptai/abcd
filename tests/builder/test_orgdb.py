from __future__ import annotations

import pytest

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.orgdb import InsertResult, OrgDBClient, OrgDBError
from cli.builder_common.tables import get_spec
from cli.harness_common.api_client import HarnessAPIError
from tests.builder.fakes import FakeBFF


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeBFF, OrgDBClient]:
    bff = FakeBFF().install(monkeypatch)
    return bff, OrgDBClient("tok", "https://bff.test")


def _vrow(i: int) -> dict[str, object]:
    return {"version_id": f"a@v{i}", "object_kind": "skill", "object_name": "a"}


def _made(env: tuple[FakeBFF, OrgDBClient]) -> str:
    return env[1].ensure_table(get_spec("bld_versions")).table_id


def test_ensure_table_create_then_verify_adds_missing_column(env) -> None:
    bff, c = env
    r = c.ensure_table(get_spec("bld_versions"))
    assert r.created and not r.added_columns
    sent = bff.calls_to("POST", "/tables")[0]
    assert sent["created_source"] in {"ui", "ai", "wdl", "migration"}  # real BFF enum
    # drop a column live, ensure adds it back; unique flag mismatch is reported
    tid = r.table_id
    bff.tables[tid]["column_schema"] = [
        col for col in bff.tables[tid]["column_schema"] if col["name"] != "notes_path"
    ]
    r2 = c.ensure_table(get_spec("bld_versions"))
    assert not r2.created and r2.added_columns == ["notes_path"]
    added = bff.calls_to("PATCH", "/schema")[0]["operations"][0]["column"]
    assert added["unique"] is False and added["nullable"] is True


def test_ensure_table_reports_mismatches_without_altering(env) -> None:
    bff, c = env
    tid = _made(env)
    for col in bff.tables[tid]["column_schema"]:
        if col["name"] == "version_id":
            col["unique"] = False
        if col["name"] == "object_name":
            col["type"] = "int"
    r = c.ensure_table(get_spec("bld_versions"))
    assert any("version_id: unique flag" in m for m in r.mismatches)
    assert any("object_name: type 'int'" in m for m in r.mismatches)
    assert bff.calls_to("PATCH", "/schema") == []


def test_insert_chunks_of_at_most_500(env) -> None:
    bff, c = env
    tid = _made(env)
    rows = [_vrow(i) for i in range(1203)]
    res = c.insert_rows("bld_versions", rows)
    assert res.ok and res.inserted == 1203 and res.duplicates == 0
    sizes = [len(b["rows"]) for b in bff.calls_to("POST", f"/tables/{tid}/rows")]
    assert sizes == [500, 500, 203]
    assert len(bff.rows[tid]) == 1203


def test_insert_validates_before_sending(env) -> None:
    bff, c = env
    _made(env)
    with pytest.raises(UsageError, match="unknown column"):
        c.insert_rows("bld_versions", [{**_vrow(1), "expected": "x"}])
    assert bff.insert_calls == 0
    c.insert_rows_raw(c.table_id("bld_versions"), [{"anything": 1, "version_id": "z"}])
    assert bff.insert_calls == 1


def test_409_after_partial_write_is_idempotent(env) -> None:
    bff, c = env
    tid = _made(env)
    c.insert_rows("bld_versions", [_vrow(1), _vrow(2)])
    # the server writes v3, then 409s on v1: v3 is already stored, so the row-by-row
    # fallback sees it as a duplicate too; only v4 is genuinely new
    rows = [_vrow(3), _vrow(1), _vrow(2), _vrow(4)]
    res = c.insert_rows("bld_versions", rows)
    assert res.ok and res.inserted == 1 and res.duplicates == 3
    assert sorted(r["version_id"] for r in bff.rows[tid]) == [f"a@v{i}" for i in (1, 2, 3, 4)]
    again = c.insert_rows("bld_versions", rows)
    assert again.inserted == 0 and again.duplicates == 4 and again.ok


def test_partial_failure_falls_back_row_by_row_and_reports_index(env) -> None:
    bff, c = env
    tid = _made(env)
    bff.fail[(tid, 1)] = 500  # whole chunk call fails, then per-row calls succeed
    res = c.insert_rows("bld_versions", [_vrow(i) for i in range(3)])
    assert res.ok and res.inserted == 3
    # a persistently failing row is reported with its index, others are written
    bff2_rows = [_vrow(10), _vrow(11), _vrow(12)]
    base = bff.insert_calls
    # call base+1 = the chunk (fails), base+2 = row 0 (ok), base+3 = row 1 (fails), base+4 = row 2 (ok)
    bff.fail = {(tid, base + 1): 500, (tid, base + 3): 503}
    res2 = c.insert_rows("bld_versions", bff2_rows)
    assert res2.inserted == 2 and len(res2.failed) == 1
    assert res2.failed[0]["index"] == 1 and res2.failed[0]["status"] == 503
    with pytest.raises(OrgDBError, match="not written"):
        res2.raise_for_failures()


def test_failed_index_is_offset_across_chunks(env) -> None:
    bff, c = env
    tid = _made(env)
    rows = [_vrow(i) for i in range(501)]
    # chunk 1 ok (call1); chunk 2 call2 fails -> per-row call3 fails
    bff.fail = {(tid, 2): 500, (tid, 3): 500}
    res = c.insert_rows("bld_versions", rows)
    assert res.inserted == 500 and [f["index"] for f in res.failed] == [500]


def test_fatal_status_raises_immediately(env) -> None:
    bff, c = env
    tid = _made(env)
    bff.fail[(tid, 1)] = 403
    with pytest.raises(OrgDBError):
        c.insert_rows("bld_versions", [_vrow(1)])
    assert bff.insert_calls == 1
    with pytest.raises(OrgDBError):
        c.insert_rows_raw("missing-table", [_vrow(1)])


def test_insert_result_merge_and_dict() -> None:
    a = InsertResult(inserted=1)
    a.merge(
        InsertResult(inserted=2, duplicates=1, failed=[{"index": 0, "error": "e", "status": 1}]), 10
    )
    assert a.to_dict() == {
        "inserted": 3,
        "duplicates": 1,
        "failed": [{"index": 10, "error": "e", "status": 1}],
        "ok": False,
    }


def test_query_is_select_only(env) -> None:
    bff, c = env
    out = c.query_sql("SELECT 1", limit=5)
    assert out["row_count"] == 1
    assert bff.calls_to("POST", "/sql")[0] == {"sql": "SELECT 1", "limit": 5}
    with pytest.raises(HarnessAPIError) as ei:
        c.query_sql("DELETE FROM x")
    assert ei.value.status_code == 400


def test_find_rows_list_all_and_paging(env) -> None:
    bff, c = env
    _made(env)
    c.insert_rows("bld_versions", [_vrow(i) for i in range(620)])
    assert len(c.list_all("bld_versions")) == 620
    assert [r["version_id"] for r in c.find_rows("bld_versions", "version_id", "a@v7")] == ["a@v7"]
    assert c.find_rows("bld_versions", "version_id", "nope") == []
    assert c.list_rows("bld_versions", page_size=500)["page_size"] == 500


def test_update_row_finds_by_key_then_patches_by_id(env) -> None:
    bff, c = env
    tid = _made(env)
    c.insert_rows("bld_versions", [_vrow(1)])
    c.update_row("bld_versions", "version_id", "a@v1", {"state": "live"})
    assert bff.rows[tid][0]["state"] == "live"
    with pytest.raises(UsageError, match="enum|not in"):
        c.update_row("bld_versions", "version_id", "a@v1", {"state": "bogus"})
    with pytest.raises(RefusedError, match="no row"):
        c.update_row("bld_versions", "version_id", "zzz", {"state": "live"})


def test_delete_row(env) -> None:
    bff, c = env
    tid = _made(env)
    c.insert_rows("bld_versions", [_vrow(1)])
    rid = bff.rows[tid][0]["id"]
    c.delete_row("bld_versions", rid)
    assert bff.rows[tid] == []


def test_delete_table_needs_matching_confirmation(env) -> None:
    bff, c = env
    tid = _made(env)
    with pytest.raises(HarnessAPIError) as ei:
        c.delete_table(tid, "wrong-name")
    assert ei.value.status_code == 400 and tid in bff.tables
    c.delete_table(tid, "bld_versions")
    assert bff.calls_to("DELETE", f"/tables/{tid}")[-1] == {"confirmation": "bld_versions"}
    assert tid not in bff.tables
    with pytest.raises(OrgDBError, match="does not exist"):
        c.table_id("bld_versions")


def test_workstream_links_skip_existing_write_links(env) -> None:
    bff, c = env
    tid = _made(env)
    c.link_table_workstreams(tid, ["w1", "w2"])
    n = len(bff.calls_to("POST", f"/tables/{tid}/workstreams"))
    assert n == 2
    c.link_table_workstreams(tid, ["w1", "w2", "w3"])
    assert len(bff.calls_to("POST", f"/tables/{tid}/workstreams")) == 3
    c.unlink_table_workstream(tid, "w1")
    assert "w1" not in bff.workstreams[tid]


def test_find_table_exact_among_fuzzy_and_paging(env) -> None:
    bff, c = env
    c.create_table("bld_versions_x", [{"name": "k", "type": "text"}])
    c.create_table("bld_versions", [{"name": "k", "type": "text"}])
    found = c.find_table("bld_versions")
    assert found is not None and found["table_name"] == "bld_versions"
    assert c.find_table("absent") is None
    assert len(list(c.iter_tables())) == 2


def test_provision(env) -> None:
    assert env[1].provision()["status"] == "ready"
