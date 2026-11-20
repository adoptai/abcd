"""Dataset versioning, the no-answers-in-rows rule, synthetic cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.tables import validate_row

from .evals_fakes import SENTINEL_ENTITY, MemStore, add_case, jbytes


def mk(tmp_path: Path, kind: str = "work_product", **kw) -> tuple[Path, MemStore]:
    ws = tmp_path / "ws"
    D.create_dataset(ws, "returns", kind, client="acme", default_scorers=["field_match"], **kw)
    return ws, MemStore()


def test_create_is_v1_with_frozen_snapshot(tmp_path: Path) -> None:
    ws, _ = mk(tmp_path)
    ds = D.load_dataset(ws, "returns")
    assert ds.version == 1 and ds.kind == "work_product"
    assert ds.manifest["layout"] == "per_case" and ds.manifest["run_mode"] == "per_case"
    assert (ws / "harness/datasets/returns/versions/v1.json").exists()
    with pytest.raises(UsageError, match="already exists"):
        D.create_dataset(ws, "returns", "custom")


def test_layout_defaults_per_kind(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    assert D.create_dataset(ws, "m", "labeled").manifest["layout"] == "shared"
    assert D.create_dataset(ws, "m2", "labeled").manifest["run_mode"] == "batch"
    ryw = D.create_dataset(ws, "c", "read_your_writes", target_env="CRM_TEST_DB", probe_agent="p")
    assert ryw.manifest["run_mode"] == "sequential"
    with pytest.raises(UsageError, match="target-env"):
        D.create_dataset(ws, "c2", "read_your_writes")
    with pytest.raises(UsageError):
        D.create_dataset(ws, "x", "nonsense")
    with pytest.raises(UsageError):
        D.create_dataset(ws, "../evil", "custom")


def test_every_operation_makes_a_new_version(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    ds = add_case(ws, store, "returns", "c1")
    assert ds.version == 2
    ds = add_case(ws, store, "returns", "c2")
    assert ds.version == 3 and ds.active_case_ids() == ["c1", "c2"]
    D.retire_case(ws, "returns", "c1")
    ds = D.load_dataset(ws, "returns")
    assert ds.version == 4 and ds.active_case_ids() == ["c2"]
    assert ds.active_case_ids(version=3) == ["c1", "c2"], "old versions stay reproducible"
    assert (ws / "harness/datasets/returns/cases/c1.yaml").exists(), "retired case files are kept"
    hist = [h["case_count"] for h in ds.manifest["history"]]
    assert hist == [0, 1, 2, 1]
    v3 = json.loads((ws / "harness/datasets/returns/versions/v3.json").read_text())
    assert set(v3["case_hashes"]) == {"c1", "c2"}


def test_add_cases_is_one_version(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    cases = []
    for i in range(3):
        cases.append(
            {
                "case_id": f"k{i}",
                "inputs": {"files": [D.put_input(store, "returns", f"k{i}", "a", b"x")]},
                "expected": [D.put_expected(store, "returns", f"k{i}", "r.json", jbytes({"a": i}))],
            }
        )
    assert D.add_cases(ws, "returns", cases).version == 2


def test_duplicate_active_case_refused_but_retired_id_can_return(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(ws, store, "returns", "c1")
    with pytest.raises(UsageError, match="already exists"):
        add_case(ws, store, "returns", "c1")
    D.retire_case(ws, "returns", "c1")
    assert add_case(ws, store, "returns", "c1").active_case_ids() == ["c1"]


def test_update_case_new_version_and_loosening_needs_lead(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(ws, store, "returns", "c1")
    new = {
        "case_id": "c1",
        "inputs": {"files": []},
        "expected": [D.put_expected(store, "returns", "c1", "return-v2.json", jbytes({"a": 2}))],
    }
    with pytest.raises(RefusedError, match="lead"):
        D.update_case(ws, "returns", new, loosens_gate=True)
    with pytest.raises(RefusedError):
        D.update_case(ws, "returns", new, loosens_gate=True, approved_by="  ")
    ds = D.update_case(ws, "returns", new, loosens_gate=True, approved_by="lena")
    assert ds.version == 3 and "approved by lena" in ds.manifest["history"][-1]["change_note"]
    assert ds.cases["c1"]["expected"][0]["path"].endswith("return-v2.json"), (
        "new path, never overwritten"
    )
    with pytest.raises(UsageError):
        D.update_case(ws, "returns", {**new, "case_id": "zz"})


def test_expected_must_live_in_ds_store_and_be_ref_only(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    good = D.put_expected(store, "returns", "c1", "r.json", b"{}")
    base = {"case_id": "c1", "inputs": {"files": []}}
    with pytest.raises(RefusedError, match="ds-returns"):
        D.add_case(
            ws,
            "returns",
            {**base, "expected": [{"path": "ds:returns:c1/outputs/r.json", "sha256": "a" * 64}]},
        )
    with pytest.raises(RefusedError, match="never by value"):
        D.add_case(ws, "returns", {**base, "expected": [{**good, "value": 5}]})
    with pytest.raises(RefusedError, match="never by value"):
        D.add_case(ws, "returns", {**base, "expected": [{"label": "acme"}]})
    with pytest.raises(RefusedError, match="bad Doc Store path"):
        D.add_case(
            ws, "returns", {**base, "expected": [{"path": "ds-returns/../x", "sha256": "a" * 64}]}
        )
    with pytest.raises(RefusedError, match="bad sha256"):
        D.add_case(ws, "returns", {**base, "expected": [{"path": good["path"], "sha256": "zz"}]})
    with pytest.raises(RefusedError, match="expected is required"):
        D.add_case(ws, "returns", base)


def test_no_values_in_case_files_or_rows(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(
        ws,
        store,
        "returns",
        "c1",
        expected={"return.json": jbytes({"entity": SENTINEL_ENTITY})},
        tags=["q1"],
        setup={"record": "R1"},
    )
    rows = D.to_rows(ws, "returns")
    blob = json.dumps(rows) + (ws / "harness/datasets/returns/cases/c1.yaml").read_text()
    blob += (ws / "harness/datasets/returns/manifest.json").read_text()
    assert SENTINEL_ENTITY not in blob
    row = rows["bld_dataset_cases"][0]
    for table, table_rows in rows.items():
        for r in table_rows:
            validate_row(table, r)  # every column is in the spec: no value has a column to sit in
    assert row["expected_path"] == "ds-returns/expected/c1/return.json"
    assert len(row["expected_sha256"]) == 64
    assert row["case_workstream_id"] == "ds:returns:c1" and row["source"] == "known"
    assert sorted(row["tags"]) == ["known", "q1"]
    assert rows["bld_datasets"][0]["current_version"] == 2
    assert rows["bld_dataset_versions"][-1]["manifest_path"] == "ds-returns/manifests/v2.json"


def test_row_guard_refuses_values() -> None:
    base = {"case_key": "d:c", "expected_refs": [{"path": "ds-d/expected/c/a", "sha256": "a" * 64}]}
    D.assert_row_clean(base)
    with pytest.raises(RefusedError, match="allow-list"):
        D.assert_row_clean({**base, "expected": {"total": 5}})
    with pytest.raises(RefusedError):
        D.assert_row_clean(
            {**base, "expected_refs": [{"path": "p", "sha256": "a" * 64, "value": 1}]}
        )


def test_to_rows_for_old_version_skips_later_cases(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(ws, store, "returns", "c1")
    add_case(ws, store, "returns", "c2")
    assert [r["case_id"] for r in D.to_rows(ws, "returns", version=2)["bld_dataset_cases"]] == [
        "c1"
    ]


def test_source_and_tag_filters(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(ws, store, "returns", "c1", source="known", tags=["big"])
    add_case(ws, store, "returns", "c2", source="synthetic")
    ds = D.load_dataset(ws, "returns")
    assert [c["case_id"] for c in ds.active_cases(source="synthetic")] == ["c2"]
    assert [c["case_id"] for c in ds.active_cases(tag="big")] == ["c1"]
    assert D.list_datasets(ws) == ["returns"]
    with pytest.raises(UsageError):
        add_case(ws, store, "returns", "c3", source="invented")


def test_shared_layout_workstream(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ds = D.create_dataset(ws, "mail", "labeled")
    assert ds.workstream("e1") == "ds:mail"


def test_archive_refused_while_referenced(tmp_path: Path) -> None:
    ws, store = mk(tmp_path)
    add_case(ws, store, "returns", "c1")
    with pytest.raises(RefusedError, match="referenced"):
        D.archive_dataset(ws, "returns", referenced_by=["a:rate"])
    D.archive_dataset(ws, "returns")
    with pytest.raises(RefusedError, match="archived"):
        add_case(ws, store, "returns", "c2")
    with pytest.raises(RefusedError, match="archived"):
        D.retire_case(ws, "returns", "c1")


def test_scorer_spec_validation() -> None:
    assert D.normalize_scorer("field_match") == ("field_match", {})
    assert D.normalize_scorer({"name": "field_match", "tolerance": 1}) == (
        "field_match",
        {"tolerance": 1},
    )
    assert D.normalize_scorer("script:my-check")[0] == "script:my-check"
    for bad in ("nope", "script:Bad Name", 5, {"x": 1}):
        with pytest.raises(UsageError):
            D.normalize_scorer(bad)


def test_synth_is_deterministic_and_labels_known_clients() -> None:
    templates = [
        {
            "id": "t1",
            "subject": "Invoice {n} for {client}",
            "body": "Amount {amount}",
            "doc_types": ["invoice"],
            "pages": 3,
            "docs": 2,
        },
        {
            "id": "t2",
            "subject": "Hello {client}",
            "attachments": ["w2-{n}.pdf"],
            "doc_types": ["w2"],
        },
    ]
    clients = {"acme": {"workstream": "ws-acme"}, "zenith": {"workstream": "ws-zenith"}}
    a = D.synth(templates, 6, clients, seed=7)
    b = D.synth(templates, 6, clients, seed=7)
    assert [s.case_id for s in a] == [s.case_id for s in b]
    assert [s.expected for s in a] == [s.expected for s in b]
    assert D.synth(templates, 6, clients, seed=8)[0].case_id != a[0].case_id
    for s in a:
        label = json.loads(s.expected["labels.json"])
        assert label["label"] in ("ws-acme", "ws-zenith")
        assert "synthetic" in s.tags
    with pytest.raises(UsageError):
        D.synth([], 1, clients)
    with pytest.raises(UsageError):
        D.synth(templates, 1, {})


def test_add_synth_cases_uploads_and_versions(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    store = MemStore()
    D.create_dataset(ws, "mail", "labeled", default_scorers=["label_match"])
    syn = D.synth([{"id": "t", "subject": "Hi {client}"}], 4, ["acme", "zenith"], seed=1)
    ds = D.add_synth_cases(ws, "mail", syn, store)
    assert ds.version == 2 and len(ds.active_case_ids()) == 4
    assert all(c["source"] == "synthetic" for c in ds.active_cases())
    exp_paths = [p for p in store.files if "/expected/" in p]
    assert exp_paths and all(p.startswith("ds-mail/expected/") for p in exp_paths)
    row = D.to_rows(ws, "mail")["bld_dataset_cases"][0]
    assert "acme" not in json.dumps(row) and row["case_workstream_id"] == "ds:mail"
