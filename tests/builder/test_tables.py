from __future__ import annotations

import dataclasses

import pytest

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.tables import (
    FORBIDDEN_VALUE_COLUMNS,
    TABLES,
    ColumnSpec,
    TableSpec,
    assert_no_answer_columns,
    ensure_all_tables,
    get_spec,
    validate_row,
    workstreams_for,
)
from tests.builder.fakes import FakeBFF


def test_all_25_tables_unique_names() -> None:
    names = [t.name for t in TABLES]
    assert len(names) == 25
    assert len(set(names)) == 25
    assert all(n.startswith("bld_") for n in names)


@pytest.mark.parametrize("spec", TABLES, ids=lambda s: s.name)
def test_no_column_named_workstream_id(spec: TableSpec) -> None:
    """Live: a ``workstream_id`` column made the create 500 (the platform adds its own)."""
    assert "workstream_id" not in spec.column_names


@pytest.mark.parametrize("spec", TABLES, ids=lambda s: s.name)
def test_unique_key_is_single_unique_not_null_column(spec: TableSpec) -> None:
    key = spec.column(spec.unique_key)
    assert key.unique and not key.nullable
    assert [c.name for c in spec.columns if c.unique] == [spec.unique_key]
    assert len(set(spec.column_names)) == len(spec.column_names)


@pytest.mark.parametrize("spec", TABLES, ids=lambda s: s.name)
def test_enums_are_sent_as_text_and_never_primary(spec: TableSpec) -> None:
    for col, api in zip(spec.columns, spec.api_columns(), strict=True):
        if col.type == "enum":
            assert col.enum_values and api["type"] == "text"
        assert api["primary"] is False
        assert api["unique"] == col.unique


def test_no_answer_columns_anywhere() -> None:
    assert_no_answer_columns()
    for spec in TABLES:
        assert not set(spec.column_names) & FORBIDDEN_VALUE_COLUMNS


def test_answer_column_is_rejected() -> None:
    bad = TableSpec(
        "bld_x", "g", "p", "k", (ColumnSpec("k", "varchar"), ColumnSpec("expected", "text"))
    )
    with pytest.raises(RefusedError, match="answers never sit"):
        assert_no_answer_columns([bad])
    bad_hash = TableSpec(
        "bld_x", "g", "p", "k", (ColumnSpec("k", "varchar"), ColumnSpec("a_sha256", "text"))
    )
    with pytest.raises(RefusedError, match="hash column"):
        assert_no_answer_columns([bad_hash])


def test_hash_columns_pair_with_paths() -> None:
    pairs = [
        (t.name, c)
        for t in TABLES
        for c in t.column_names
        if c.endswith("_sha256") and c != "skill_md_sha256"
    ]
    assert pairs, "expected *_sha256 companions to *_path columns"
    for table, col in pairs:
        prefix = col[: -len("_sha256")]
        assert f"{prefix}_path" in get_spec(table).column_names, (table, col)


def test_get_spec_unknown() -> None:
    with pytest.raises(UsageError, match="unknown Builder table"):
        get_spec("bld_nope")


def test_bld_versions_has_deviation_columns() -> None:
    spec = get_spec("bld_versions")
    for c in ("skill_md_sha256", "manifest", "pinned_components", "state", "content_hash"):
        assert c in spec.column_names
    assert spec.column("manifest").type == "jsonb"
    assert spec.column("state").enum_values == ("draft", "staged", "live", "superseded")


def test_validate_row_ok_and_partial() -> None:
    validate_row("bld_versions", {"version_id": "a@v1", "object_kind": "skill", "state": "draft"})
    validate_row("bld_versions", {"state": "live"}, partial=True)


def test_validate_row_problems_listed_together() -> None:
    with pytest.raises(UsageError) as ei:
        validate_row(
            "bld_versions",
            {"object_kind": "widget", "bogus": 1, "skill_md_sha256": "xyz", "notes_path": "a\nb"},
        )
    msg = str(ei.value)
    assert "unknown column 'bogus'" in msg
    assert "not in ['skill', 'plugin', 'agent']" in msg
    assert "missing unique key 'version_id'" in msg
    assert "64 lowercase hex" in msg
    assert "single-line path" in msg


def test_validate_row_types_and_nulls() -> None:
    with pytest.raises(UsageError, match="may not be null"):
        validate_row("bld_versions", {"version_id": None})
    with pytest.raises(UsageError, match="must be a string"):
        validate_row("bld_versions", {"version_id": 5})
    ok = "a" * 64
    validate_row(
        "bld_versions", {"version_id": "v@v1", "skill_md_sha256": ok, "manifest": {"aux": []}}
    )
    with pytest.raises(UsageError, match="missing unique key"):
        validate_row("bld_versions", {"version_id": ""})


def test_validate_row_numeric_and_int() -> None:
    num = next((t, c) for t in TABLES for c in t.columns if c.type == "numeric")
    with pytest.raises(UsageError, match="must be a number"):
        validate_row(num[0], {num[0].unique_key: "k", num[1].name: "1.5"})
    validate_row(num[0], {num[0].unique_key: "k", num[1].name: 1.5})
    integer = next((t, c) for t in TABLES for c in t.columns if c.type == "int")
    with pytest.raises(UsageError, match="must be an integer"):
        validate_row(integer[0], {integer[0].unique_key: "k", integer[1].name: True})


def test_workstreams_for_roles() -> None:
    spec = get_spec("bld_versions")
    assert workstreams_for(spec, {"ops": "w1", "context": "w2"}) == ["w1"]
    with pytest.raises(UsageError, match="no workstream id"):
        workstreams_for(spec, {"context": "w2"})
    multi = dataclasses.replace(spec, extra_workstreams=("sme",))
    assert workstreams_for(multi, {"ops": "w1", "sme": ["w3", "w1", "w4"]}) == ["w1", "w3", "w4"]
    assert workstreams_for(multi, {"ops": "w1"}) == ["w1"]


def test_ensure_all_tables_creates_and_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from cli.builder_common.orgdb import OrgDBClient

    bff = FakeBFF().install(monkeypatch)
    client = OrgDBClient("tok", "https://bff.test")
    roles = {"ops": "ws-ops", "context": "ws-ctx", "sme": ["s1"], "live_agents": ["l1"]}
    out = ensure_all_tables(client, roles)
    assert len(out) == 25 and all(v["created"] for v in out.values())
    assert len(bff.tables) == 25
    for spec in TABLES:
        tid = out[spec.name]["table_id"]
        assert set(bff.workstreams[tid]) == set(workstreams_for(spec, roles))
        assert set(bff.workstreams[tid].values()) == {"write"}
    again = ensure_all_tables(client, roles)
    assert not any(v["created"] for v in again.values())
    assert len(bff.tables) == 25
