from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.builder_common.drift import (
    SEV_INFO,
    DriftItem,
    adopt_baseline,
    audit_drift,
    build_drift_report,
    check_default_pins,
    compare_process,
    compare_skill,
    fetch_live_skill,
    find_default_shadows,
    pull_plan,
)
from cli.builder_common.errors import PlatformError
from cli.builder_common.versions import (
    VersionLedger,
    VersionRecord,
    hash_skill_dir,
    process_record,
    sha256_text,
    skill_md_digest,
    skill_record,
)
from cli.harness_common.api_client import HarnessAPIClient
from tests.builder.fakes import FakeBFF, FakePlatform, MemoryStore

MD = "---\nname: a\n---\nbody\n"
PROC = {
    "display_name": "Prep",
    "description": "d",
    "components": [{"type": "skill", "source": "org", "ref": "a"}],
    "agent_instructions": "go",
    "is_enabled": True,
}


def rec_for(
    md: str, aux: dict[str, str], tmp_path: Path, name: str = "a", state: str = "live"
) -> VersionRecord:
    d = tmp_path / f"{name}-{state}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(md)
    for rel, content in aux.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return skill_record(name, hash_skill_dir(d), version_id=f"{name}@v1", state=state)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeBFF, FakePlatform, HarnessAPIClient]:
    plat = FakePlatform()
    bff = FakeBFF(plat).install(monkeypatch)
    return bff, plat, HarnessAPIClient("tok", "https://bff.test")


def kinds(rep: Any) -> list[str]:
    return sorted(i.kind for i in rep.items)


# -- fetch_live_skill -----------------------------------------------------------------


def test_fetch_live_skill_missing_and_hashes_body(env) -> None:
    _, plat, c = env
    assert fetch_live_skill(c, "nope") is None
    plat.add_org_skill("a", MD, {"f.txt": "abc"})
    live = fetch_live_skill(c, "a")
    assert live is not None and live.skill_md_sha256 == skill_md_digest(MD)
    assert live.aux == [{"path": "f.txt", "size": 3, "partial": False}]
    live2 = fetch_live_skill(c, "a", fetch_aux=True)
    assert live2 is not None and live2.aux[0]["sha256"] == sha256_text("abc")


def test_live_hash_matches_the_real_readback_shape() -> None:
    """Live (2026-10-29): GET /skills/{name} gives description + body without the frontmatter."""
    md = '---\nname: a--staged\ndescription: "Staging copy. Sums."\n---\n\n# a\n\nSteps.\n'

    class Real:
        def get_skill(self, name: str) -> dict:
            return {
                "description": "Staging copy. Sums.",
                "body": "# a\n\nSteps.\n",
                "aux_files": [],
            }

    live = fetch_live_skill(Real(), "a--staged")
    assert live is not None and live.skill_md_sha256 == skill_md_digest(md)
    changed = fetch_live_skill(
        type(
            "R2", (), {"get_skill": lambda self, n: {"description": "x", "body": "# a\n\nSteps.\n"}}
        )(),
        "a--staged",
    )
    assert changed is not None and changed.skill_md_sha256 != skill_md_digest(md)


def test_fetch_aux_marks_binary_truncated_and_size_mismatch_partial(env) -> None:
    _, plat, c = env
    plat.add_org_skill(
        "a",
        MD,
        {"img.png": b"\x89PNG\x00", "big.txt": "x" * 50, "ok.txt": "ok"},
        truncated=["big.txt"],
    )
    live = fetch_live_skill(c, "a", fetch_aux=True)
    assert live is not None
    flags = {a["path"]: (a["partial"], "sha256" in a) for a in live.aux}
    assert flags == {"big.txt": (True, False), "img.png": (True, False), "ok.txt": (False, True)}


def test_fetch_error_is_platform_error(env) -> None:
    _, _, c = env
    c._request = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        __import__("cli.harness_common.api_client", fromlist=["x"]).HarnessAPIError(
            "GET", "u", 500, "x"
        )
    )
    with pytest.raises(PlatformError):
        fetch_live_skill(c, "a")


# -- skill drift --------------------------------------------------------------------------


def test_no_drift_when_identical(env, tmp_path: Path) -> None:
    _, plat, c = env
    plat.add_org_skill("a", MD, {"f.txt": "abc"})
    rec = rec_for(MD, {"f.txt": "abc"}, tmp_path)
    rep = compare_skill(rec, fetch_live_skill(c, "a", fetch_aux=True))
    assert not rep.has_drift and rep.items == [] and rep.partial == []


def test_skill_drift_kinds(env, tmp_path: Path) -> None:
    _, plat, c = env
    plat.add_org_skill("a", MD + "edited\n", {"f.txt": "abcd", "new.txt": "n", "same.txt": "XYZ"})
    rec = rec_for(MD, {"f.txt": "abc", "gone.txt": "g", "same.txt": "xyz"}, tmp_path)
    rep = compare_skill(rec, fetch_live_skill(c, "a", fetch_aux=True))
    assert kinds(rep) == [
        "aux_added",
        "aux_content_changed",
        "aux_removed",
        "aux_size_changed",
        "skill_md_changed",
    ]
    assert rep.has_drift
    by = {i.kind: i for i in rep.items}
    assert by["aux_added"].severity == "warning" and by["aux_added"].name == "a:new.txt"
    assert by["aux_content_changed"].name == "a:same.txt"
    assert by["skill_md_changed"].detail == "a@v1"


def test_missing_skill_is_drift(tmp_path: Path) -> None:
    rep = compare_skill(rec_for(MD, {}, tmp_path), None)
    assert kinds(rep) == ["skill_missing"] and rep.has_drift


def test_binary_and_truncated_not_counted_as_drift(env, tmp_path: Path) -> None:
    _, plat, c = env
    plat.add_org_skill(
        "a", MD, {"img.png": b"\x89PNG\x00\x01", "big.txt": "x" * 40}, truncated=["big.txt"]
    )
    d = tmp_path / "src"
    d.mkdir()
    (d / "SKILL.md").write_text(MD)
    (d / "img.png").write_bytes(b"\x89PNG\x00\x01")
    (d / "big.txt").write_text("x" * 40)
    rec = skill_record("a", hash_skill_dir(d), version_id="a@v1", state="live")
    rep = compare_skill(rec, fetch_live_skill(c, "a", fetch_aux=True))
    assert not rep.has_drift and rep.items == []
    assert {p["path"] for p in rep.partial} == {"img.png", "big.txt"}
    assert all(p["reason"] == "binary_or_truncated" for p in rep.partial)


def test_size_change_of_binary_is_still_drift(env, tmp_path: Path) -> None:
    _, plat, c = env
    plat.add_org_skill("a", MD, {"img.png": b"\x89PNG\x00\x01\x02"})
    d = tmp_path / "src"
    d.mkdir()
    (d / "SKILL.md").write_text(MD)
    (d / "img.png").write_bytes(b"\x89PNG\x00\x01")
    rec = skill_record("a", hash_skill_dir(d), version_id="a@v1", state="live")
    assert kinds(compare_skill(rec, fetch_live_skill(c, "a", fetch_aux=True))) == [
        "aux_size_changed"
    ]


def test_without_fetch_aux_same_size_is_unverified_not_drift(env, tmp_path: Path) -> None:
    _, plat, c = env
    plat.add_org_skill("a", MD, {"f.txt": "abd"})
    rep = compare_skill(rec_for(MD, {"f.txt": "abc"}, tmp_path), fetch_live_skill(c, "a"))
    assert not rep.has_drift
    assert rep.partial == [{"name": "a", "path": "f.txt", "reason": "content_not_verified"}]


# -- process drift -------------------------------------------------------------------------


def test_process_no_drift_ignores_noise_and_stripped_compiled_flow() -> None:
    rec = process_record("prep", PROC, version_id="prep@v1", state="live")
    live = {**PROC, "id": "p1", "name": "prep", "updated_at": "x", "description": "d\n"}
    assert not compare_process(rec, live, "prep").has_drift
    assert kinds(compare_process(rec, None)) == ["process_missing"]


def test_process_drift_names_field() -> None:
    rec = process_record("prep", PROC, version_id="prep@v1", state="live")
    live = {**PROC, "agent_instructions": "go elsewhere", "is_enabled": False}
    rep = compare_process(rec, live, "prep")
    assert sorted(i.name for i in rep.items) == ["prep.agent_instructions", "prep.is_enabled"]
    assert all(i.kind == "process_field_changed" and i.severity == "error" for i in rep.items)


def test_process_drift_without_field_hashes_reports_all_fields() -> None:
    rec = process_record("prep", PROC, version_id="prep@v1", state="live")
    rec.manifest = None
    rep = compare_process(rec, {**PROC, "description": "other"}, "prep")
    assert len(rep.items) == 6


# -- shadows and pins -------------------------------------------------------------------------


def test_default_shadows_known_and_probe(env) -> None:
    _, plat, c = env
    plat.add_default_skill("pdf", "p", "3.1")
    plat.operator = True
    out = find_default_shadows(
        ["mine", "web", "pdf", "web--staged"], known_default_names=["web"], client=c
    )
    assert [(i.name, i.kind) for i in out] == [("pdf", "default_shadow"), ("web", "default_shadow")]
    assert out[0].detail == "3.1" and out[0].severity == "warning"
    assert find_default_shadows(["web--staged"], known_default_names=["web--staged"]) == []


def test_default_shadow_probe_forbidden_is_skipped(env) -> None:
    _, plat, c = env
    plat.add_default_skill("pdf", "p", "3.1")  # operator False -> 403
    assert find_default_shadows(["pdf"], client=c) == []


def test_default_pin_changes() -> None:
    rec = VersionRecord(
        "prep@v1",
        "agent",
        "prep",
        "h",
        pinned_components={
            "default_skills": {"web": "1.0", "pdf": "2.0", "old": None, "gone": "1"}
        },
    )
    out = check_default_pins(rec, {"web": "1.1", "pdf": "2.0", "old": "5"})
    assert [(i.kind, i.name, i.expected, i.actual) for i in out] == [
        ("default_bundle_moved", "web", "1.0", "1.1")
    ]
    assert check_default_pins(VersionRecord("x@v1", "agent", "x", "h"), {"web": "1"}) == []


# -- build_drift_report / audit_drift --------------------------------------------------------------


def test_build_report_uses_newest_live_and_staged_names(tmp_path: Path) -> None:
    old = rec_for("old", {}, tmp_path, state="superseded")
    live = rec_for(MD, {}, tmp_path, state="live")
    staged = rec_for(MD + "s", {}, tmp_path, state="staged")
    draft = rec_for("draft", {}, tmp_path, state="draft")
    from cli.builder_common.drift import LiveSkill

    skills = {
        "a": LiveSkill("a", skill_md_digest(MD)),
        "a--staged": LiveSkill("a--staged", sha256_text("elsewhere")),
    }
    rep = build_drift_report([old, live, staged, draft], skills, {})
    assert rep.checked == ["a", "a--staged"]
    assert [(i.kind, i.name) for i in rep.items] == [("skill_md_changed", "a--staged")]
    shadow = DriftItem("default_shadow", "q", "e", "a", "info")
    assert not build_drift_report([], {}, {}, shadows=[shadow]).has_drift
    assert shadow.severity == SEV_INFO


def test_audit_drift_end_to_end(env, tmp_path: Path) -> None:
    _, plat, c = env
    store = MemoryStore()
    ledger = VersionLedger(store)
    ledger.record_live(rec_for(MD, {"f.txt": "abc"}, tmp_path))
    ledger.record_live(process_record("prep", PROC, version_id="prep@v1"))
    plat.add_org_skill("a", MD, {"f.txt": "abc"})
    plat.add_process("p1", "prep", **PROC)
    plat.add_default_skill("web", "w")
    plat.add_org_skill("web", "shadow body")  # org hides default
    clean = audit_drift(c, ledger, fetch_aux=True, known_default_names=["web"])
    assert [(i.kind, i.name) for i in clean.items] == [("default_shadow", "web")]
    assert clean.has_drift  # a shadow is a warning

    plat.org_skills["a"]["body"] = MD + "x"
    plat.processes["p1"]["agent_instructions"] = "changed"
    rep = audit_drift(c, ledger, fetch_aux=True, known_default_names=["web"])
    assert kinds(rep) == ["default_shadow", "process_field_changed", "skill_md_changed"]


def test_audit_skips_plugin_skills_for_shadows_and_missing_process(env, tmp_path: Path) -> None:
    _, plat, c = env
    ledger = VersionLedger(MemoryStore())
    ledger.record_live(process_record("gone", PROC, version_id="gone@v1"))
    plat.add_default_skill("web", "w")
    plat.add_org_skill("web", "x", plugin="plug")
    rep = audit_drift(c, ledger, known_default_names=["web"])
    assert kinds(rep) == ["process_missing"]


# -- pull_plan ----------------------------------------------------------------------------------------


def test_pull_plan(env, tmp_path: Path) -> None:
    _, plat, c = env
    harness = tmp_path / "harness"
    (harness / "skills" / "have").mkdir(parents=True)
    (harness / "skills" / "have" / "SKILL.md").write_text(MD)
    (harness / "agents" / "known").mkdir(parents=True)
    plat.add_org_skill("have", MD)
    plat.add_org_skill(
        "new", MD, {"t.txt": "text", "i.png": b"\x00\x01", "b.txt": "y" * 30}, truncated=["b.txt"]
    )
    plat.add_org_skill("bundled", MD, plugin="plug")
    plat.add_org_skill("new--staged", MD)
    plat.add_default_skill("web", "w")
    plat.add_process("p1", "known", **PROC)
    plat.add_process("p2", "fresh", **PROC)
    plat.add_process("p3", "fresh-staged", **PROC)

    plan = pull_plan(c, harness)
    assert plan.already_in_repo == ["skill:have", "agent:known"]
    assert [(i.kind, i.name) for i in plan.items] == [("skill", "new"), ("process", "fresh")]
    skill = plan.items[0]
    assert skill.skill_md == MD and skill.partial
    files = {f.path: (f.status, f.reason) for f in skill.files}
    assert files == {
        "b.txt": ("partial", "truncated"),
        "i.png": ("partial", "binary"),
        "t.txt": ("text", ""),
    }
    assert next(f for f in skill.files if f.path == "t.txt").content == "text"
    assert {s["name"]: s["reason"] for s in plan.skipped} == {
        "bundled": "bundled in plugin plug",
        "new--staged": "staged shadow",
        "fresh-staged": "staged shadow",
    }
    assert plan.items[1].process is not None and plan.items[1].process["display_name"] == "Prep"
    d = plan.to_dict()
    assert d["import"][0]["partial"] is True and "content" not in str(d["import"][0]["files"])
    assert pull_plan(c, harness, with_content=False).items[0].files[2].content is None


def test_pull_plan_writes_nothing(env, tmp_path: Path) -> None:
    bff, plat, c = env
    plat.add_org_skill("new", MD)
    harness = tmp_path / "h"
    harness.mkdir()
    pull_plan(c, harness)
    assert list(harness.iterdir()) == []
    assert all(m == "GET" for m, _, _ in bff.calls)


# -- adopt_baseline -------------------------------------------------------------------------------------


def test_adopt_baseline_records_v0(env) -> None:
    _, plat, c = env
    plat.add_org_skill("a", MD, {"f.txt": "abc", "i.png": b"\x00"})
    plat.add_org_skill("a--staged", MD)
    plat.add_org_skill("bundled", MD, plugin="plug")
    plat.add_default_skill("web", "w", "7.0")
    plat.add_process(
        "p1",
        "prep",
        components=[
            {"type": "skill", "source": "org", "ref": "a"},
            {"type": "skill", "source": "default", "ref": "web"},
        ],
    )
    plat.add_process("p2", "prep-staged")
    recs = adopt_baseline(
        c, author="ann", git_sha="abc123", fetch_aux=True, default_versions={"web": "7.0"}
    )
    assert [r.version_id for r in recs] == ["a@v0", "prep@v0"]
    skill, agent = recs
    assert skill.state == agent.state == "live" and skill.reason_ref == "adopt-baseline"
    assert skill.author_user_id == "ann" and skill.git_sha == "abc123"
    assert skill.skill_md_sha256 == skill_md_digest(MD)
    assert {a["path"]: "sha256" in a for a in skill.aux} == {"f.txt": True, "i.png": False}
    assert all("partial" not in a for a in skill.aux)
    pins = agent.pinned_components
    assert pins is not None and pins["default_skills"] == {"web": "7.0"}
    assert pins["components"][0]["version_id"] == "a@v0"
    # baseline round-trips into the ledger and the next push is v1
    ledger = VersionLedger(MemoryStore())
    for r in recs:
        ledger.record_live(r)
    assert ledger.next_version_id("skill", "a") == "a@v1"
    # and a fresh audit of the same platform state reports no content drift
    rep = audit_drift(c, ledger, fetch_aux=True)
    assert [i for i in rep.items if i.kind != "default_shadow"] == []


def test_adopt_baseline_probe_defaults(env) -> None:
    _, plat, c = env
    plat.operator = True
    plat.add_default_skill("web", "w", "8.1")
    plat.add_process(
        "p1", "prep", components=[{"type": "skill", "source": "default", "ref": "web"}]
    )
    recs = adopt_baseline(c, author="ann", probe_defaults=True)
    assert recs[0].pinned_components is not None
    assert recs[0].pinned_components["default_skills"] == {"web": "8.1"}
