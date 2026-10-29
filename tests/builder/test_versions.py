from __future__ import annotations

from pathlib import Path

import pytest

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.versions import (
    StagedLock,
    VersionLedger,
    VersionRecord,
    canonical_json,
    hash_process,
    hash_skill_dir,
    normalize_process,
    pin_components,
    probe_default_skill,
    process_field_hashes,
    process_record,
    sha256_text,
    skill_record,
    staged_name,
)
from cli.harness_common.api_client import HarnessAPIClient
from tests.builder.fakes import FakeBFF, FakePlatform, MemoryStore


def make_skill(
    root: Path, aux: dict[str, bytes] | None = None, md: str = "---\nname: a\n---\nbody\n"
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(md)
    for rel, data in (aux or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


# -- hashing ----------------------------------------------------------------


def test_hash_deterministic_across_dirs_and_creation_order(tmp_path: Path) -> None:
    a = make_skill(tmp_path / "a", {"scripts/x.py": b"1", "ref/y.md": b"2"})
    b = tmp_path / "b"
    b.mkdir()
    (b / "SKILL.md").write_text("---\nname: a\n---\nbody\n")
    (b / "ref").mkdir()
    (b / "ref" / "y.md").write_bytes(b"2")
    (b / "scripts").mkdir()
    (b / "scripts" / "x.py").write_bytes(b"1")
    ha, hb = hash_skill_dir(a), hash_skill_dir(b)
    assert ha == hb
    assert [x["path"] for x in ha["aux"]] == ["ref/y.md", "scripts/x.py"]
    assert ha["aux"][0] == {"path": "ref/y.md", "size": 1, "sha256": sha256_text("2")}
    assert len(ha["bundle_sha256"]) == 64


def test_hash_changes_on_content_size_path_and_skill_md(tmp_path: Path) -> None:
    base = hash_skill_dir(make_skill(tmp_path / "base", {"f.txt": b"aa"}))["bundle_sha256"]
    variants = {
        "content": ({"f.txt": b"ab"}, None),
        "size": ({"f.txt": b"aaa"}, None),
        "path": ({"g.txt": b"aa"}, None),
        "extra": ({"f.txt": b"aa", "h.txt": b""}, None),
        "md": ({"f.txt": b"aa"}, "---\nname: a\n---\nother\n"),
    }
    seen = {base}
    for k, (aux, md) in variants.items():
        kwargs = {"md": md} if md else {}
        h = hash_skill_dir(make_skill(tmp_path / k, aux, **kwargs))["bundle_sha256"]
        assert h not in seen, k
        seen.add(h)


def test_hash_ignore_rules(tmp_path: Path) -> None:
    clean = hash_skill_dir(make_skill(tmp_path / "c", {"f.txt": b"x"}))
    noisy = make_skill(
        tmp_path / "n",
        {
            "f.txt": b"x",
            "__pycache__/m.pyc": b"zz",
            "scripts/__pycache__/q.cpython-311.pyc": b"zz",
            ".git/HEAD": b"ref",
            ".DS_Store": b"1",
            "m.pyc": b"1",
            ".mypy_cache/a": b"1",
        },
    )
    assert hash_skill_dir(noisy) == clean


def test_hash_skips_symlinks_and_requires_skill_md(tmp_path: Path) -> None:
    d = make_skill(tmp_path / "s", {"f.txt": b"x"})
    (d / "link.txt").symlink_to(d / "f.txt")
    assert [a["path"] for a in hash_skill_dir(d)["aux"]] == ["f.txt"]
    (tmp_path / "empty").mkdir()
    with pytest.raises(UsageError, match="no SKILL.md"):
        hash_skill_dir(tmp_path / "empty")


# -- process hashing ----------------------------------------------------------


PROC = {
    "display_name": "Prep",
    "description": "d",
    "components": [{"type": "skill", "source": "org", "ref": "a"}],
    "agent_instructions": "do it",
    "is_enabled": True,
    "compiled_flow": None,
}


def test_process_hash_normalizes_noise() -> None:
    noisy = {
        **PROC,
        "id": "p1",
        "name": "prep",
        "updated_at": "now",
        "description": "d\n",
        "agent_instructions": "do it  \r\n",
        "components": [{"type": "skill", "source": "org", "ref": "a", "extra": 1}],
    }
    assert hash_process(noisy) == hash_process(PROC)


def test_process_hash_empty_equals_missing() -> None:
    a = {**PROC, "description": "", "agent_instructions": None, "compiled_flow": {}}
    b = {
        k: v
        for k, v in PROC.items()
        if k not in ("description", "agent_instructions", "compiled_flow")
    }
    assert hash_process(a) == hash_process(b)
    assert normalize_process(b)["description"] is None


def test_process_hash_sensitive_to_order_and_fields() -> None:
    two = {
        **PROC,
        "components": PROC["components"] + [{"type": "skill", "source": "default", "ref": "z"}],
    }
    rev = {**PROC, "components": list(reversed(two["components"]))}
    assert len({hash_process(PROC), hash_process(two), hash_process(rev)}) == 3
    fh, fh2 = process_field_hashes(PROC), process_field_hashes({**PROC, "is_enabled": False})
    assert [k for k in fh if fh[k] != fh2[k]] == ["is_enabled"]
    assert canonical_json({"b": 1, "a": [1]}) == '{"a":[1],"b":1}'


def test_staged_names() -> None:
    assert staged_name("skill", "a") == "a--staged"
    assert staged_name("plugin", "a") == "a--staged"
    assert staged_name("agent", "a") == "a-staged"


# -- ledger ------------------------------------------------------------------------


def _skill_rec(name: str, tmp_path: Path, n: int = 0, data: bytes = b"x") -> VersionRecord:
    h = hash_skill_dir(make_skill(tmp_path / f"{name}{n}", {"f": data}))
    return skill_record(name, h, version_id="")


def test_record_record_roundtrip_and_json_columns(tmp_path: Path) -> None:
    rec = _skill_rec("a", tmp_path)
    rec.version_id = "a@v1"
    row = rec.to_row()
    assert isinstance(row["manifest"], dict) and "state" in row and "git_sha" not in row
    back = VersionRecord.from_row({**row, "manifest": '{"aux": []}'})
    assert back.aux == [] and back.number == 1
    assert VersionRecord.from_row({**row, "version_id": "weird"}).number == -1


def test_ledger_push_stage_release_supersede(tmp_path: Path) -> None:
    store = MemoryStore()
    led = VersionLedger(store)
    ids = []
    for i in range(3):
        rec = led.record_push(_skill_rec("a", tmp_path, i, str(i).encode()))
        ids.append(rec.version_id)
    assert ids == ["a@v1", "a@v2", "a@v3"]
    h = led.history("skill", "a")
    assert [r.parent_version_id for r in h] == [None, "a@v1", "a@v2"]
    assert led.next_version_id("skill", "a") == "a@v4"
    assert led.history("agent", "a") == []

    led.record_stage("a@v1")
    led.record_stage("a@v2")  # supersedes v1's staged slot
    assert [r.state for r in led.history("skill", "a")] == ["superseded", "staged", "draft"]
    led.record_release("a@v2")
    led.record_stage("a@v3")
    led.record_release("a@v3")
    states = {r.version_id: r.state for r in led.history("skill", "a")}
    assert states == {"a@v1": "superseded", "a@v2": "superseded", "a@v3": "live"}
    assert led.latest("skill", "a", "live").version_id == "a@v3"  # type: ignore[union-attr]
    assert led.latest("skill", "nothing") is None
    assert led.get("a@v2").state == "superseded"  # type: ignore[union-attr]


def test_ledger_stage_refuses_non_draft_and_unknown(tmp_path: Path) -> None:
    led = VersionLedger(MemoryStore())
    rec = led.record_push(_skill_rec("a", tmp_path))
    led.record_release(rec.version_id)
    with pytest.raises(RefusedError, match="only a draft"):
        led.record_stage(rec.version_id)
    with pytest.raises(UsageError, match="unknown version"):
        led.record_release("nope@v1")


def test_ledger_insert_failure_raises(tmp_path: Path) -> None:
    store = MemoryStore()
    store.fail_inserts.add("bld_versions")
    with pytest.raises(PlatformError, match="could not record"):
        VersionLedger(store).record_push(_skill_rec("a", tmp_path))


def test_ledger_record_live_and_process_record() -> None:
    led = VersionLedger(MemoryStore())
    rec = process_record(
        "prep", PROC, version_id="prep@v0", state="live", pinned_components={"components": []}
    )
    led.record_live(rec)
    got = led.get("prep@v0")
    assert got is not None and got.object_kind == "agent" and got.state == "live"
    assert got.content_hash == hash_process(PROC)
    assert got.field_hashes == process_field_hashes(PROC)
    assert got.pinned_components == {"components": []}


def test_ledger_histories_separate_kinds_with_same_name(tmp_path: Path) -> None:
    led = VersionLedger(MemoryStore())
    led.record_push(_skill_rec("a", tmp_path))
    led.record_push(process_record("a", PROC, version_id=""))
    assert [r.version_id for r in led.history("agent", "a")] == ["a@v2"]
    assert [r.version_id for r in led.history("skill", "a")] == ["a@v1"]


def test_ledger_refuses_duplicate_version_id(tmp_path: Path) -> None:
    led = VersionLedger(MemoryStore())
    rec = led.record_push(_skill_rec("a", tmp_path))
    again = _skill_rec("a", tmp_path, 1)
    again.version_id = rec.version_id
    with pytest.raises(RefusedError, match="already recorded"):
        led.record_push(again)


# -- staged lock ---------------------------------------------------------------------


def test_lock_acquire_new_row_and_same_holder_keeps_since() -> None:
    store = MemoryStore()
    lock = StagedLock(store, now=lambda: "t1")
    r = lock.acquire("ag", "ann", "sha1")
    assert r.acquired and r.previous_holder is None
    assert lock.info("ag").holder == "ann"
    assert store.tables["bld_agents"][0]["phase"] == "building"
    lock2 = StagedLock(store, now=lambda: "t2")
    lock2.acquire("ag", "ann", "sha2")
    info = lock2.info("ag")
    assert (info.holder, info.sha, info.since) == ("ann", "sha2", "t1")


def test_lock_refuses_other_holder_naming_them() -> None:
    lock = StagedLock(MemoryStore(), now=lambda: "t1")
    lock.acquire("ag", "ann", "sha1")
    with pytest.raises(RefusedError, match="held by ann since t1 .*sha1.*--take"):
        lock.acquire("ag", "bob", "sha2")
    assert lock.info("ag").holder == "ann"


def test_lock_take_returns_notice_and_moves() -> None:
    lock = StagedLock(MemoryStore(), now=lambda: "t1")
    lock.acquire("ag", "ann", "sha1")
    r = lock.acquire("ag", "bob", "sha2", take=True)
    assert r.previous_holder == "ann" and "bob took the staged slot" in (r.notice or "")
    assert lock.info("ag").holder == "bob" and lock.info("ag").sha == "sha2"


def test_lock_release_rules() -> None:
    lock = StagedLock(MemoryStore(), now=lambda: "t1")
    lock.release("none", "ann")  # no row: no-op
    lock.acquire("ag", "ann", "s")
    with pytest.raises(RefusedError, match="not bob"):
        lock.release("ag", "bob")
    lock.release("ag", "bob", force=True)
    assert lock.info("ag").holder is None
    lock.release("ag", "bob")  # already free: no-op
    lock.acquire("ag", "carol", "s")  # free slot is acquirable without take
    assert lock.info("ag").holder == "carol"


# -- pin_components ----------------------------------------------------------------------


COMPS = [
    {"type": "skill", "source": "org", "ref": "a"},
    {"type": "skill", "source": "default", "ref": "web"},
    {"type": "skill", "source": "default", "ref": "pdf"},
    {"type": "plugin", "source": "org", "ref": "plug"},
]


def test_pin_components_precedence_and_org_versions() -> None:
    catalog = {"items": [{"name": "web", "source": "default", "bundle_version": "9.9"}]}
    pinned = pin_components(
        COMPS, catalog, org_versions={"a": "a@v3"}, default_versions={"web": "1.0", "pdf": "2.0"}
    )
    by_ref = {c["ref"]: c for c in pinned["components"]}
    assert by_ref["a"]["version_id"] == "a@v3"
    assert by_ref["web"]["bundle_version"] == "9.9"  # catalog beats supplied map
    assert by_ref["pdf"]["bundle_version"] == "2.0"
    assert "version_id" not in by_ref["plug"] and "bundle_version" not in by_ref["plug"]
    assert pinned["default_skills"] == {"web": "9.9", "pdf": "2.0"}
    assert [c["ref"] for c in pinned["components"]] == ["a", "web", "pdf", "plug"]


def test_pin_components_unknown_default_is_none() -> None:
    pinned = pin_components(COMPS, {"items": []})
    assert pinned["default_skills"] == {"web": None, "pdf": None}


def test_pin_components_probes_operator_route(monkeypatch: pytest.MonkeyPatch) -> None:
    plat = FakePlatform()
    plat.add_default_skill("web", "w", "4.2")
    plat.operator = True
    FakeBFF(plat).install(monkeypatch)
    client = HarnessAPIClient("tok", "https://bff.test")
    pinned = pin_components(COMPS, {"items": []}, client=client)
    assert pinned["default_skills"] == {"web": "4.2", "pdf": None}
    assert probe_default_skill(client, "absent") is None


def test_probe_default_403_is_platform_error_and_pin_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plat = FakePlatform()
    plat.add_default_skill("web", "w", "4.2")
    FakeBFF(plat).install(monkeypatch)  # operator False -> 403
    client = HarnessAPIClient("tok", "https://bff.test")
    with pytest.raises(PlatformError, match="operator-only"):
        probe_default_skill(client, "web")
    assert pin_components(COMPS, {"items": []}, client=client)["default_skills"]["web"] is None
