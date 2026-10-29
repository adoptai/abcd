"""Instruction pages, the leak check (positives and negatives), the skill compile, client sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cli.builder_common import instructions as I  # noqa: N812
from cli.builder_common.errors import RefusedError, UsageError

from .evals_fakes import MemRows, MemStore, jbytes

PROV = [{"source": "gap", "ref": "gap-1", "by": "sme-1"}]
EXPECTED = {
    "entity": "Zenith Orchard Partners",
    "total_tax": 48213.77,
    "tax_year": 2024,
    "flag": True,
    "short": "ab",
    "lines": [{"desc": "Depreciation schedule 7B", "amt": 1250000}],
}


@pytest.fixture
def env(tmp_path: Path):
    store = MemStore()
    ref = {
        "path": "ds-returns/expected/c1/return.json",
        "sha256": store.put("ds-returns/expected/c1/return.json", jbytes(EXPECTED)),
    }
    return tmp_path, store, [ref]


def leak(body: str, env, **kw):
    ws, store, refs = env
    return I.check_leaks(body, case_ids=["c1", "case-0042"], expected_refs=refs, store=store, **kw)


def kinds(res) -> set[str]:
    return {f["kind"] for f in res["findings"]}


GENERIC = (
    "# Depreciation\n\nUse the straight-line method for assets placed in service after the "
    "election date, and MACRS otherwise. Round to whole dollars at the schedule level.\n"
)


def test_clean_generic_instruction(env) -> None:
    res = leak(GENERIC, env)
    assert res["status"] == "clean" and res["findings"] == []


def test_flags_case_id_exact_only(env) -> None:
    assert "case_id" in kinds(leak("See c1 for how this works in general.", env))
    assert "case_id" in kinds(leak("Mirrors CASE-0042.", env))
    assert leak("Use c10 or abc1 style codes; cases are fine.", env)["status"] == "clean"


def test_flags_quoted_expected_strings_and_numbers(env) -> None:
    assert "expected_value" in kinds(leak("The entity is Zenith Orchard Partners.", env))
    assert "expected_value" in kinds(leak("zenith orchard partners files late", env)), (
        "case-insensitive"
    )
    assert "expected_value" in kinds(leak("Total tax is 48213.77 here.", env))
    assert "expected_value" in kinds(leak("The amount 1,250,000 applies.", env))
    assert "expected_value" in kinds(leak("Look for Depreciation schedule 7B.", env))


def test_does_not_flag_trivial_values(env) -> None:
    for body in (
        "Tax year 2024 returns are due in March.",
        "Set the flag to true.",
        "ab is a prefix.",
        "Zenith is a client.",
    ):
        assert leak(body, env)["status"] == "clean", body
    assert leak("Use 482137 sometimes; 48213.7 too.", env)["status"] == "clean", (
        "partial numbers are not the value"
    )


def test_finding_never_quotes_the_value(env) -> None:
    res = leak("The entity is Zenith Orchard Partners and tax 48213.77.", env)
    blob = json.dumps(res)
    assert "Zenith" not in blob and "48213" not in blob
    assert "ds-returns/expected/c1/return.json" in blob and "entity" in blob


def test_flags_single_case_wording_and_input_names(env) -> None:
    assert "single_case" in kinds(
        leak("This only applies in this case because the client moved.", env)
    )
    assert "single_case" in kinds(leak("Just for this file, skip the totals.", env))
    assert "single_case" in kinds(leak("That was a one-off.", env))
    assert "single_case" in kinds(
        leak(
            "As in Statement_Q3.pdf, use the last page.",
            env,
            input_names=["inputs/Statement_Q3.pdf"],
        )
    )
    assert (
        leak("Statement pages vary.", env, input_names=["inputs/Statement_Q3.pdf"])["status"]
        == "clean"
    )


def test_leak_check_fetches_expected_at_check_time_and_verifies_hash(env) -> None:
    ws, store, refs = env
    store.gets.clear()
    leak(GENERIC, env)
    assert store.gets == [refs[0]["path"]]
    store.files[refs[0]["path"]] = b'{"x": "tampered"}'
    with pytest.raises(RefusedError, match="hash"):
        leak(GENERIC, env)


def test_text_expected_files_use_long_lines(env) -> None:
    ws, store, _ = env
    ref = {
        "path": "ds-returns/expected/c2/notes.txt",
        "sha256": store.put(
            "ds-returns/expected/c2/notes.txt", b"short\nthe golden return footnote text\n"
        ),
    }
    res = I.check_leaks(
        "it says the golden return footnote text", case_ids=[], expected_refs=[ref], store=store
    )
    assert res["status"] == "flagged"
    assert (
        I.check_leaks("it says short", case_ids=[], expected_refs=[ref], store=store)["status"]
        == "clean"
    )


# --------------------------------------------------------------------------- pages and index


def save(ws, store, name="depreciation", scope="agent", body=GENERIC, **kw):
    return I.save_instruction(
        ws,
        store,
        agent="acme-tax",
        name=name,
        scope=scope,
        body=body,
        gaps=["gap-1"],
        provenance=PROV,
        **kw,
    )


def test_page_has_provenance_and_roundtrips(env) -> None:
    ws, store, _ = env
    rec = save(ws, store)
    assert rec["page_path"] == "builder-context/kg/instructions/acme-tax/depreciation.md"
    meta, body = I.parse_page(store.files[rec["page_path"]].decode())
    assert (
        meta["provenance"] == PROV
        and meta["gaps"] == ["gap-1"]
        and meta["instruction_key"] == "acme-tax:depreciation"
    )
    assert body == GENERIC.strip()
    assert I.load_body(store, rec) == GENERIC.strip()
    assert rec["status"] == "draft" and rec["leak_check"]["status"] == "unchecked"
    # the index never holds the body
    assert "straight-line" not in I.index_path(ws, "acme-tax").read_text()


def test_provenance_and_inputs_required(env) -> None:
    ws, store, _ = env
    with pytest.raises(UsageError, match="provenance"):
        I.save_instruction(
            ws, store, agent="a", name="n", scope="org", body="b", gaps=[], provenance=[]
        )
    with pytest.raises(UsageError, match="provenance"):
        I.save_instruction(
            ws,
            store,
            agent="a",
            name="n",
            scope="org",
            body="b",
            gaps=[],
            provenance=[{"ref": "x"}],
        )
    with pytest.raises(UsageError, match="client"):
        save(ws, store, scope="client")
    with pytest.raises(UsageError, match="scope"):
        save(ws, store, scope="galaxy")
    with pytest.raises(UsageError, match="name"):
        save(ws, store, name="Bad Name")
    with pytest.raises(UsageError, match="empty"):
        save(ws, store, body="  ")


def test_edit_makes_new_revision_path(env) -> None:
    ws, store, _ = env
    save(ws, store)
    rec = save(ws, store, body=GENERIC + "More.\n")
    assert rec["revision"] == 2 and rec["page_path"].endswith("depreciation.r2.md")
    assert len([p for p in store.files if "instructions/acme-tax" in p]) == 2


def test_vet_requires_clean_leak_check(env) -> None:
    ws, store, refs = env
    save(ws, store)
    with pytest.raises(RefusedError, match="unchecked"):
        I.vet(ws, "acme-tax", "acme-tax:depreciation", by="fde-1")
    rec = I.run_leak_check(
        ws, store, "acme-tax", "acme-tax:depreciation", case_ids=["c1"], expected_refs=refs
    )
    assert rec["leak_check"]["status"] == "clean"
    assert I.vet(ws, "acme-tax", "acme-tax:depreciation", by="fde-1")["status"] == "vetted"
    with pytest.raises(UsageError):
        I.vet(ws, "acme-tax", "acme-tax:nope", by="x")


def test_flagged_page_cannot_be_vetted_and_is_pulled_back(env) -> None:
    ws, store, refs = env
    save(ws, store, name="leaky", body="The entity is Zenith Orchard Partners.")
    rec = I.run_leak_check(ws, store, "acme-tax", "acme-tax:leaky", case_ids=[], expected_refs=refs)
    assert rec["leak_check"]["status"] == "flagged"
    with pytest.raises(RefusedError, match="flagged"):
        I.vet(ws, "acme-tax", "acme-tax:leaky", by="fde")
    assert "Zenith" not in I.index_path(ws, "acme-tax").read_text()


def prep(ws, store, refs, name="depreciation", scope="agent", body=GENERIC, **kw):
    save(ws, store, name=name, scope=scope, body=body, **kw)
    key = f"acme-tax:{name}"
    I.run_leak_check(ws, store, "acme-tax", key, case_ids=["c1"], expected_refs=refs)
    return I.vet(ws, "acme-tax", key, by="fde-1")


# --------------------------------------------------------------------------- compile


def test_compile_instruction_skill(env) -> None:
    ws, store, refs = env
    rows = MemRows()
    prep(ws, store, refs)
    prep(
        ws,
        store,
        refs,
        name="rounding",
        scope="org",
        body="# Rounding\n\nRound at the schedule level.\n",
    )
    prep(
        ws,
        store,
        refs,
        name="acme-quirks",
        scope="client",
        body="# Quirks\n\nAcme sends scans.\n",
        client="acme",
    )
    out = I.compile_instruction_skill(
        ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs, rows=rows
    )
    sd = ws / "harness/skills/acme-tax-instructions"
    assert out["skill_name"] == "acme-tax-instructions" and out["included"] == [
        "acme-tax:depreciation",
        "acme-tax:rounding",
    ]
    assert out["client_scope_not_compiled"] == ["acme-tax:acme-quirks"]
    skill = (sd / "SKILL.md").read_text()
    meta = yaml.safe_load(skill.split("---\n")[1])
    assert meta["name"] == "acme-tax-instructions" and meta["description"]
    assert "references/depreciation.md" in skill and "Depreciation" in skill
    assert (sd / "references/rounding.md").exists() and not (
        sd / "references/acme-quirks.md"
    ).exists()
    ref_text = (sd / "references/depreciation.md").read_text()
    assert (
        "## Provenance" in ref_text
        and "gap: gap-1" in ref_text
        and "Closes gaps: gap-1" in ref_text
    )
    idx = I.load_index(ws, "acme-tax")
    assert idx["acme-tax:depreciation"]["status"] == "compiled"
    assert idx["acme-tax:depreciation"]["skill_version"] == out["skill_version"]
    assert idx["acme-tax:acme-quirks"]["skill_version"] is None
    assert (
        rows.tables["bld_instructions"]["acme-tax:rounding"]["compiled_into_version"]
        == out["skill_version"]
    )


def test_frontmatter_passes_the_harness_lint(env) -> None:
    from cli.harness_skill import _lint_frontmatter

    ws, store, refs = env
    prep(ws, store, refs)
    I.compile_instruction_skill(ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs)
    p = ws / "harness/skills/acme-tax-instructions/SKILL.md"
    assert _lint_frontmatter(p.read_text(), p)["name"] == "acme-tax-instructions"


def test_compile_is_deterministic_and_replaces_stale_files(env) -> None:
    ws, store, refs = env
    prep(ws, store, refs)
    a = I.compile_instruction_skill(ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs)
    stale = ws / "harness/skills/acme-tax-instructions/references/old.md"
    stale.write_text("old")
    b = I.compile_instruction_skill(ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs)
    assert a["skill_version"] == b["skill_version"] and not stale.exists()
    prep(ws, store, refs, name="extra", body="# Extra\n\nAnother rule.\n")
    assert (
        I.compile_instruction_skill(ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs)[
            "skill_version"
        ]
        != a["skill_version"]
    )


def test_compile_refuses_flagged_and_unvetted(env) -> None:
    ws, store, refs = env
    with pytest.raises(UsageError, match="no vetted"):
        I.compile_instruction_skill(ws, store, "acme-tax", case_ids=[], expected_refs=refs)
    prep(ws, store, refs)
    save(ws, store, name="draft-only")
    # an expected value that appears later (the golden dataset gained a case) is caught at compile
    ws2_ref = {
        "path": "ds-returns/expected/c9/r.json",
        "sha256": store.put(
            "ds-returns/expected/c9/r.json", jbytes({"note": "straight-line method"})
        ),
    }
    with pytest.raises(RefusedError, match="leak check flagged"):
        I.compile_instruction_skill(
            ws, store, "acme-tax", case_ids=["c1"], expected_refs=[*refs, ws2_ref]
        )
    assert not (ws / "harness/skills/acme-tax-instructions").exists()
    assert (
        I.load_index(ws, "acme-tax")["acme-tax:depreciation"]["leak_check"]["status"] == "flagged"
    )


def test_compile_refuses_tampered_page(env) -> None:
    ws, store, refs = env
    rec = prep(ws, store, refs)
    store.files[rec["page_path"]] = b"---\ninstruction_key: x\n---\n\nchanged\n"
    with pytest.raises(RefusedError, match="hash"):
        I.compile_instruction_skill(ws, store, "acme-tax", case_ids=["c1"], expected_refs=refs)


def test_compile_bad_skill_name_is_a_usage_error(env) -> None:
    ws, store, refs = env
    I.save_instruction(
        ws,
        store,
        agent="Acme_Tax",
        name="r",
        scope="org",
        body="# R\n\nA rule.\n",
        gaps=[],
        provenance=PROV,
    )
    I.run_leak_check(ws, store, "Acme_Tax", "Acme_Tax:r", case_ids=[], expected_refs=refs)
    I.vet(ws, "Acme_Tax", "Acme_Tax:r", by="fde")
    with pytest.raises(UsageError, match="slug"):
        I.compile_instruction_skill(ws, store, "Acme_Tax", case_ids=[], expected_refs=refs)


# --------------------------------------------------------------------------- client sync


def test_client_sync_plan_and_apply(env) -> None:
    ws, store, refs = env
    prep(
        ws,
        store,
        refs,
        name="acme-quirks",
        scope="client",
        body="# Quirks\n\nAcme sends scans.\n",
        client="acme",
    )
    prep(
        ws,
        store,
        refs,
        name="other",
        scope="client",
        body="# O\n\nOther client rule.\n",
        client="zenith",
    )
    save(ws, store, name="unvetted", scope="client", client="acme")
    prep(ws, store, refs)  # agent scope: not part of client sync
    plan = I.client_sync_plan(ws, "acme-tax", "acme", ["ws-acme", "ds:returns:c1", "ws-acme"])
    assert [p["target"] for p in plan] == [
        "ds:returns:c1/_builder/instructions/acme-quirks.md",
        "ws-acme/_builder/instructions/acme-quirks.md",
    ]
    written = I.apply_sync_plan(plan, store)
    assert written == [p["target"] for p in plan]
    assert store.files[written[0]] == store.files[plan[0]["source"]]
    store.files[plan[0]["source"]] = b"changed"
    with pytest.raises(RefusedError, match="changed"):
        I.apply_sync_plan(plan, store)


def test_flagged_client_page_is_not_synced(env) -> None:
    ws, store, refs = env
    prep(ws, store, refs, name="q", scope="client", body="# Q\n\nA rule.\n", client="acme")
    I.run_leak_check(ws, store, "acme-tax", "acme-tax:q", case_ids=["rule"], expected_refs=refs)
    assert I.client_sync_plan(ws, "acme-tax", "acme", ["w"]) == []
