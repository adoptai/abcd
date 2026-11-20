"""Staged rewrite round trips (PLAN.md section 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cli.builder_common.errors import BuilderError, RefusedError, UsageError
from cli.builder_common.staging import (
    NOUI_BYTE_FILES,
    build_staged_plugin,
    build_staged_skill,
    join_frontmatter,
    live_upload_dir,
    restore_live,
    rewrite_flow,
    rewrite_text,
    split_frontmatter,
    staged_matches_source,
    staged_name,
)

SKILL_MD = """---
name: tb-reconcile
description: "Reconcile: trial balances"
keywords: [tb, close]
featured: true
process:
  title: Monthly close
  steps:
    - "Step one: pull"
---
# Reconcile

Use fetch_skill('tb-reconcile') then fetch_skill('tb-load'). Not tb-load-v2.
"""


def _skill(root: Path, text: str = SKILL_MD) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(text)
    return root


def test_frontmatter_round_trip_preserves_keys_and_body() -> None:
    meta, body = split_frontmatter(SKILL_MD)
    again = join_frontmatter(meta, body)
    meta2, body2 = split_frontmatter(again)
    assert meta2 == meta and body2 == body
    assert list(meta2) == ["name", "description", "keywords", "featured", "process"]


def test_multiline_and_quoted_description() -> None:
    text = '---\nname: a-b\ndescription: |\n  line one: x\n  "two"\n---\nbody\n'
    meta, body = split_frontmatter(text)
    assert meta["description"] == 'line one: x\n"two"'
    assert split_frontmatter(join_frontmatter(meta, body)) == (meta, body)


def test_bad_frontmatter_refused() -> None:
    with pytest.raises(UsageError):
        split_frontmatter("no frontmatter")
    with pytest.raises(UsageError):
        split_frontmatter("---\nname: a: b: c\n  bad\n---\nx\n")


def test_stage_skill_rewrites_and_drops_process(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    out = tmp_path / "out"
    res = build_staged_skill(src, out, {"tb-load": "tb-load--staged"})
    meta, body = split_frontmatter((out / "SKILL.md").read_text())
    assert res.staged_name == "tb-reconcile--staged" and res.dropped_process
    assert meta["name"] == "tb-reconcile--staged"
    assert "process" not in meta
    assert meta["description"] == "Staging copy, do not use. Reconcile: trial balances"
    assert meta["keywords"] == ["tb", "close"] and meta["featured"] is True
    assert "fetch_skill('tb-reconcile--staged')" in body
    assert "fetch_skill('tb-load--staged')" in body
    assert "Not tb-load-v2." in body  # longer identifier is not a reference
    # source untouched
    assert split_frontmatter(src.joinpath("SKILL.md").read_text())[0]["name"] == "tb-reconcile"


def test_no_process_block_and_no_description(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src", "---\nname: plain\n---\nhello\n")
    res = build_staged_skill(src, tmp_path / "out")
    meta, body = split_frontmatter((tmp_path / "out" / "SKILL.md").read_text())
    assert not res.dropped_process
    assert meta == {"name": "plain--staged", "description": "Staging copy, do not use."}
    assert body == "hello\n"


def test_noui_files_byte_identical(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    payloads = {
        n: (b'{"x": 1,\r\n "y":  "\xc3\xa9"}  \n\x00' + n.encode()) for n in NOUI_BYTE_FILES
    }
    for n, data in payloads.items():
        (src / n).write_bytes(data)
    (src / "scripts").mkdir()
    (src / "scripts" / "run.py").write_text("print(1)\n")
    res = build_staged_skill(src, tmp_path / "out")
    for n, data in payloads.items():
        assert (tmp_path / "out" / n).read_bytes() == data
    assert (tmp_path / "out" / "scripts" / "run.py").exists()
    assert set(NOUI_BYTE_FILES) <= set(res.files)


def test_excluded_files_not_staged(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    (src / ".env").write_text("SECRET=1")
    (src / "tests").mkdir()
    (src / "tests" / "t.py").write_text("x")
    build_staged_skill(src, tmp_path / "out")
    assert not (tmp_path / "out" / ".env").exists()
    assert not (tmp_path / "out" / "tests").exists()


def test_refuses_nonempty_out_and_already_staged(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(UsageError):
        build_staged_skill(src, out)
    with pytest.raises(RefusedError):
        staged_name("a--staged")
    staged_src = _skill(tmp_path / "s2", "---\nname: a--staged\n---\nb\n")
    with pytest.raises(RefusedError):
        build_staged_skill(staged_src, tmp_path / "o2")
    with pytest.raises(RefusedError):
        live_upload_dir(staged_src)


def test_staged_matches_source(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    out = tmp_path / "out"
    build_staged_skill(src, out)
    assert staged_matches_source(out, src)
    (src / "SKILL.md").write_text(SKILL_MD + "more\n")
    assert not staged_matches_source(out, src)


def test_live_upload_dir_validates_source(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    assert live_upload_dir(src) == src and restore_live(src) == src
    out = tmp_path / "out"
    build_staged_skill(src, out)
    with pytest.raises(RefusedError):
        live_upload_dir(out)
    with pytest.raises(UsageError):
        live_upload_dir(tmp_path / "missing")


def test_rewrite_text_and_flow() -> None:
    m = {"a-b": "a-b--staged", "a": "a--staged"}
    assert (
        rewrite_text("use a-b and a, not a-b-c or x_a", m)
        == "use a-b--staged and a--staged, not a-b-c or x_a"
    )
    flow = {"spine": [{"ref": "a-b", "note": "call a-b"}], "n": 3}
    assert rewrite_flow(flow, m) == {
        "spine": [{"ref": "a-b--staged", "note": "call a-b--staged"}],
        "n": 3,
    }
    assert "a-b" in flow["spine"][0]["ref"]  # input not mutated


def _plugin(root: Path) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "acme-kit", "version": "1.0.0", "description": "Kit"})
    )
    for slug, text in (
        (
            "acme-read",
            "---\nname: acme-read\ndescription: Read\nprocess: {title: t}\n---\nsee acme-write\n",
        ),
        ("acme-write", "---\nname: acme-write\ndescription: Write\n---\nuses acme-kit\n"),
    ):
        (root / "skills" / slug).mkdir(parents=True)
        (root / "skills" / slug / "SKILL.md").write_text(text)
    (root / "skills" / "acme-read" / "operations.json").write_bytes(b'{"operations": []}\n')
    (root / "README.md").write_text("readme\n")
    return root


def test_staged_plugin(tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = build_staged_plugin(_plugin(tmp_path / "p"), out)
    assert res.staged_name == "acme-kit--staged"
    assert res.skills == {"acme-read": "acme-read--staged", "acme-write": "acme-write--staged"}
    assert res.dropped_process == ["acme-read"]
    manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
    assert manifest == {
        "name": "acme-kit--staged",
        "version": "1.0.0",
        "description": "Staging copy, do not use. Kit",
    }
    read = out / "skills" / "acme-read--staged"
    meta, body = split_frontmatter((read / "SKILL.md").read_text())
    assert meta["name"] == "acme-read--staged" and "process" not in meta
    assert "see acme-write--staged" in body
    assert (read / "operations.json").read_bytes() == b'{"operations": []}\n'
    assert not (out / "skills" / "acme-read").exists()
    assert (out / "README.md").exists()
    wmeta, wbody = split_frontmatter(
        (out / "skills" / "acme-write--staged" / "SKILL.md").read_text()
    )
    assert wmeta["name"] == "acme-write--staged" and "acme-kit--staged" in wbody


def test_staged_plugin_passes_push_plugin_validation(tmp_path: Path) -> None:
    from cli.harness_skill import build_plugin_zip

    out = tmp_path / "out"
    build_staged_plugin(_plugin(tmp_path / "p"), out)
    _, meta, skills, _ = build_plugin_zip(out)
    assert meta["name"] == "acme-kit--staged"
    assert skills == ["acme-read--staged", "acme-write--staged"]


def test_plugin_errors(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        build_staged_plugin(tmp_path, tmp_path / "o")
    p = _plugin(tmp_path / "p")
    (p / "skills" / "acme-write" / "SKILL.md").write_text("---\nname: other\n---\nx\n")
    with pytest.raises((UsageError, BuilderError)):
        build_staged_plugin(p, tmp_path / "o2")


def test_yaml_dump_is_loadable_by_platform_parser(tmp_path: Path) -> None:
    src = _skill(tmp_path / "src")
    build_staged_skill(src, tmp_path / "out")
    head = (tmp_path / "out" / "SKILL.md").read_text().split("---\n")[1]
    assert yaml.safe_load(head)["name"] == "tb-reconcile--staged"


def test_platform_client_stop_turn_sends_conversation_id() -> None:
    from typing import Any

    from cli.builder_common.platform import BuilderPlatformClient

    calls: list[tuple[str, str, dict[str, Any]]] = []

    class Recorder(BuilderPlatformClient):
        def _request(self, method: str, path: str, **kw: Any) -> Any:
            calls.append((method, path, kw))
            return {"ok": True}

    client = Recorder("tok", "https://bff.example")
    assert client.stop_turn("t-1", "c-9") == {"ok": True}
    assert calls == [
        (
            "POST",
            "/v1/end-user/agent-harness/turns/t-1/stop",
            {"json": {"conversation_id": "c-9"}},
        )
    ]
