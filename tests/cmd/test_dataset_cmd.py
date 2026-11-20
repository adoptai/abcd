"""``abcd dataset``: lifecycle, where each file goes, rows, approval and dry-run."""

from __future__ import annotations

import json

from tests.builder.evals_fakes import AGENT, PROCESS

from .quality_fixtures import (
    CLIENT,
    PROBE,
    SENTINEL_ENTITY,
    QEnv,
    qenv,  # noqa: F401
)

SENTINEL_STR = SENTINEL_ENTITY
SENTINEL_NUM = "48214.77"


def test_create_add_synth_show_retire(qenv: QEnv) -> None:  # noqa: F811
    d = qenv.ok("dataset", "create", "returns", "--kind", "work_product", "--client", CLIENT,
                "--scorer", "field_match", "--agent", AGENT)  # fmt: skip
    assert d["version"] == 1 and d["expected_store"] == "ds-returns"
    for i in (1, 2):
        inp, exp = qenv.case_files(f"c{i}", 100.0 + i)
        d = qenv.ok("dataset", "add-case", "returns", f"c{i}", "--input", inp, "--expected", exp,
                    "--tag", "edge")  # fmt: skip
        assert d["version"] == i + 1
    show = qenv.ok("dataset", "show", "returns")
    assert [c["case_id"] for c in show["cases"]] == ["c1", "c2"] and show["version"] == 3
    assert qenv.ok("dataset", "show", "returns", "--tag", "edge")["cases"]
    assert qenv.ok("dataset", "show", "returns", "--source", "synthetic")["cases"] == []
    assert qenv.ok("dataset", "show")["datasets"][0]["cases"] == 2

    # labelled dataset + synth, deterministic for a seed
    qenv.ok("dataset", "create", "inbox", "--kind", "labeled", "--client", CLIENT,
            "--scorer", "label_match")  # fmt: skip
    tpl = qenv.file("t.json", {"templates": [{"id": "inv", "subject": "Invoice {n} for {client}",
                                              "body": "pay {amount}", "doc_types": ["invoice"]}],
                               "clients": {"zenith": {"workstream": "ws-zenith"}, "acme": {}}})  # fmt: skip
    d = qenv.ok("dataset", "synth", "inbox", "--from", tpl, "--count", "4", "--seed", "7")
    assert d["version"] == 2 and len(d["changed"]) == 4
    again = qenv.ok("dataset", "show", "inbox", "--source", "synthetic")
    assert len(again["cases"]) == 4
    assert all("synthetic" in c["tags"] for c in again["cases"])

    # retire needs --yes and keeps the files
    code, doc = qenv.run("dataset", "retire-case", "returns", "c1")
    assert code == 1 and "--yes" in doc["error"]
    d = qenv.ok("dataset", "retire-case", "returns", "c1", "--yes")
    assert d["version"] == 4 and d["cases"] == 1
    assert [c["case_id"] for c in qenv.ok("dataset", "show", "returns")["cases"]] == ["c2"]
    assert len(qenv.ok("dataset", "show", "returns", "--retired")["cases"]) == 2
    assert any(p.endswith("c1/c1-return.json") or "expected/c1/" in p for p in qenv.store.files)
    # one commit per operation
    assert sum(1 for s in qenv.commits() if s.startswith("dataset returns")) == 4


def test_expected_values_never_reach_repo_rows_or_dataset_workstream(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    qenv.ok("dataset", "create", "crm", "--kind", "read_your_writes", "--client", CLIENT,
            "--target-env", "CRM_TEST_DB", "--probe", PROBE, "--agent", AGENT)  # fmt: skip
    state = qenv.file("state.json", {"owner": "Zenith Orchard Partners", "balance": 48214.77})
    qenv.ok("dataset", "add-case", "crm", "k1", "--message", "update the record",
            "--expect-state", state, "--tag", "overwrite")  # fmt: skip
    qenv.ok("dataset", "create", "inbox", "--kind", "labeled", "--client", CLIENT)
    qenv.ok("dataset", "add-case", "inbox", "e1", "--input", qenv.file("e1.eml", "hello"),
            "--label", "client=zenith-orchard", "--label", "doc_type=invoice")  # fmt: skip
    qenv.ok("dataset", "update-case", "returns", "c1", "--expected",
            qenv.file("c1-fixed.json", {"entity": SENTINEL_STR, "total_tax": 48214.77}),
            "--approved-by", "lead")  # fmt: skip

    secrets = [SENTINEL_STR, SENTINEL_NUM, "zenith-orchard"]
    # 1. the workspace (files and git history)
    history = qenv.git_history()
    for p in qenv.ws.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            text = p.read_text(errors="ignore")
            for s in secrets:
                assert s not in text, f"{s!r} found in {p}"
    for s in secrets:
        assert s not in history
    # 2. rows
    for table in ("bld_datasets", "bld_dataset_versions", "bld_dataset_cases"):
        dump = json.dumps(qenv.rows(table))
        for s in secrets:
            assert s not in dump
    case_rows = {r["case_key"]: r for r in qenv.rows("bld_dataset_cases")}
    assert case_rows["returns:c1"]["expected_path"].startswith("ds-returns/expected/c1/")
    assert len(case_rows["returns:c1"]["expected_sha256"]) == 64
    assert case_rows["crm:k1"]["expected_path"] == "ds-crm/expected/k1/state.json"
    # 3. every upload holding a secret sits in a ds-<dataset> store, never a dataset workstream
    holders = [p for p, b in qenv.store.files.items() if any(s.encode() in b for s in secrets)]
    assert holders and all(p.startswith("ds-") for p in holders), holders
    assert any(p.startswith("ds:returns/inputs/c1/") for p in qenv.store.files)
    # 4. the ds-<dataset> store is linked to builder-ops only
    assert qenv.ops.stores["ds-returns"] == "builder-ops"
    assert "ds-returns" in qenv.ops.stores and "ds-crm" in qenv.ops.stores


def test_workstreams_assignments_and_manifest_upload(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)  # per-case layout
    assert set(qenv.ops.workstreams) == {"ds:returns:c1", "ds:returns:c2"}
    assert qenv.ops.workstreams["ds:returns:c1"]["props"] == {"kind": "dataset"}
    assert qenv.ops.assignments[PROCESS] == {w["id"] for w in qenv.ops.workstreams.values()}
    # shared layouts get one workstream, the staged agent and the probe are assigned to it
    qenv.ok("dataset", "create", "crm", "--kind", "read_your_writes", "--client", CLIENT,
            "--target-env", "CRM_TEST_DB", "--probe", PROBE, "--agent", AGENT)  # fmt: skip
    assert "ds:crm" in qenv.ops.workstreams
    wid = qenv.ops.workstreams["ds:crm"]["id"]
    assert wid in qenv.ops.assignments[PROCESS] and wid in qenv.ops.assignments["probe-1"]
    assert "ds-returns/manifests/v3.json" in qenv.store.files
    # per-case inputs live in the case's own workstream store, the path keeps the dataset prefix
    cases = {r["case_key"]: r for r in qenv.rows("bld_dataset_cases")}
    assert cases["returns:c1"]["case_workstream_id"] == "ds:returns:c1"
    assert qenv.rows("bld_datasets")[0]["expected_store_id"].startswith("ds-")


def test_update_loosening_needs_approval_and_archive_refused_while_referenced(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    fixed = qenv.file("fixed.json", {"entity": "x", "total_tax": 1})
    code, doc = qenv.run("dataset", "update-case", "returns", "c1", "--expected", fixed)
    assert code == 1 and "--approved-by" in doc["error"]
    assert qenv.ok("dataset", "show", "returns")["version"] == 3  # unchanged
    d = qenv.ok(
        "dataset", "update-case", "returns", "c1", "--expected", fixed, "--approved-by", "lead"
    )
    assert d["version"] == 4 and d["approved_by"] == "lead"
    # the corrected expectation is at a new path, the old one is kept
    exp_paths = [p for p in qenv.store.files if p.startswith("ds-returns/expected/c1/")]
    assert len(exp_paths) == 2
    # a non-loosening change (a tag) needs no approval
    assert qenv.ok("dataset", "update-case", "returns", "c2", "--tag", "edge")["version"] == 5

    qenv.ok(
        "criteria",
        "set",
        AGENT,
        "--dataset",
        "returns",
        "--metric",
        "pass_rate",
        "--op",
        ">=",
        "--target",
        "0.9",
    )
    code, doc = qenv.run("dataset", "archive", "returns", "--yes")
    assert code == 1 and "referenced by criteria" in doc["error"]
    # an unreferenced dataset archives: workstreams soft-deleted, frozen, writes refused
    qenv.make_dataset(1, "scratch")
    code, doc = qenv.run("dataset", "archive", "scratch")
    assert code == 1 and "--yes" in doc["error"]
    d = qenv.ok("dataset", "archive", "scratch", "--yes")
    assert d["archived_workstreams"] == ["ds:scratch:c1"] and qenv.ops.archived == ["ds:scratch:c1"]
    code, doc = qenv.run("dataset", "add-case", "scratch", "z", "--expected", fixed)
    assert code == 1 and "archived" in doc["error"]


def test_usage_errors_and_dry_run(qenv: QEnv) -> None:  # noqa: F811
    code, doc = qenv.run("dataset", "create", "crm", "--kind", "read_your_writes")
    assert code == 2 and "--target-env" in doc["error"]
    code, doc = qenv.run("dataset", "create", "x", "--kind", "work_product", "--agent", "nobody")
    assert code == 2
    qenv.make_dataset(1)
    inp = qenv.file("i.txt", "i")
    code, doc = qenv.run("dataset", "add-case", "returns", "c9", "--input", inp)
    assert code == 1 and "expectation" in doc["error"]
    code, doc = qenv.run("dataset", "add-case", "returns", "c1", "--input", inp, "--label", "a=b")
    assert code == 2 and "already exists" in doc["error"]
    code, doc = qenv.run("dataset", "add-case", "returns", "c9", "--label", "nokv")
    assert code == 2
    before = dict(qenv.store.files)
    d = qenv.ok("dataset", "add-case", "returns", "c9", "--label", "a=b", "--dry-run")
    assert d == {"dry_run": True} and qenv.store.files == before
    assert [c["case_id"] for c in qenv.ok("dataset", "show", "returns")["cases"]] == ["c1"]
    # --json shape
    code, doc = qenv.run("dataset", "show", "returns")
    assert set(doc) >= {"ok", "exit_code", "command", "data"} and doc["command"] == "dataset show"


def test_dataset_records_the_ids_of_the_workstreams_it_creates(qenv: QEnv) -> None:  # noqa: F811
    from cli.builder_common import datasets as D  # noqa: N812

    qenv.make_dataset(2)
    ids = D.load_dataset(qenv.ws, "returns").manifest["workstream_ids"]
    assert ids == {n: w["id"] for n, w in qenv.ops.workstreams.items()}
    assert set(ids) == {"ds:returns:c1", "ds:returns:c2"}
    assert D.recorded_workstream_ids(qenv.ws)["ds:returns:c2"] == ids["ds:returns:c2"]
