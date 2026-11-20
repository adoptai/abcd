"""ws push-bundles / pull-bundles through the noui-bundles Doc Store (fake presigned URLs)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.builder_common import workspace as bws
from tests.cmd.ws_fixtures import WsBFF, git, make_workspace, run, use_client, workspaces_env


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


@pytest.fixture
def world(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, WsBFF]:
    ws = make_workspace(ws_dir)
    assert run(capsys, "ws", "init", "--env", "acme")[0] == 0
    bff = WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    assert run(capsys, "builder", "setup", "--env", "acme")[0] == 0
    (ws / "harness" / "bundles" / "login").mkdir(parents=True)
    (ws / "harness" / "bundles" / "login" / "capture.json").write_text('{"har": "big"}')
    (ws / "harness" / "skills" / "crm").mkdir(parents=True)
    (ws / "harness" / "skills" / "crm" / "recording_bundle.json").write_text('{"events": []}')
    bff.calls.clear()
    return ws, bff


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_push_then_pull_round_trip(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    code, doc = run(capsys, "ws", "push-bundles", "--env", "acme")
    assert code == 0, doc
    assert {u["path"] for u in doc["data"]["uploaded"]} == {
        "harness/bundles/login/capture.json",
        "harness/skills/crm/recording_bundle.json",
    }
    store = bff.store_named("noui-bundles")
    assert store is not None and len(bff.docs[store["id"]]) == 2

    entries = bws.load_bundles_lock(ws)["entries"]
    cap = entries["harness/bundles/login/capture.json"]
    assert cap["sha256"] == sha(ws / "harness/bundles/login/capture.json")
    assert cap["doc_store_path"] == f"harness/bundles/login/{cap['sha256'][:12]}/capture.json"
    assert {d["relative_path"] for d in bff.docs[store["id"]]} == {
        e["doc_store_path"] for e in entries.values()
    }
    # bundles stay out of git
    for rel in entries:
        assert git(ws, "check-ignore", rel) == rel

    # unchanged: nothing is uploaded again
    posts = bff.count("POST", f"/v1/docstore/stores/{store['id']}/upload-urls")
    code, doc = run(capsys, "ws", "push-bundles", "--env", "acme")
    assert code == 0 and doc["data"]["uploaded"] == []
    assert bff.count("POST", f"/v1/docstore/stores/{store['id']}/upload-urls") == posts

    # a changed bundle gets a new path; nothing is overwritten
    (ws / "harness/bundles/login/capture.json").write_text('{"har": "bigger"}')
    assert run(capsys, "ws", "push-bundles", "--env", "acme")[0] == 0
    assert len(bff.docs[store["id"]]) == 3

    # restore on "another machine": delete, then pull
    (ws / "harness/bundles/login/capture.json").unlink()
    (ws / "harness/skills/crm/recording_bundle.json").unlink()
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme")
    assert code == 0, doc
    assert (ws / "harness/bundles/login/capture.json").read_text() == '{"har": "bigger"}'
    assert (ws / "harness/skills/crm/recording_bundle.json").read_text() == '{"events": []}'
    assert sorted(doc["data"]["restored"]) == [
        "harness/bundles/login/capture.json",
        "harness/skills/crm/recording_bundle.json",
    ]
    # idempotent
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme")
    assert code == 0 and doc["data"]["restore"] == []


def test_pull_bundles_does_not_clobber_a_changed_local_file(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _ = world
    assert run(capsys, "ws", "push-bundles", "--env", "acme")[0] == 0
    local = ws / "harness/bundles/login/capture.json"
    local.write_text("edited locally")
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme")
    assert code == 0 and local.read_text() == "edited locally"
    assert "differs" in doc["data"]["skipped"][0]["reason"]
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme", "--yes")
    assert code == 0 and local.read_text() == '{"har": "big"}'


def test_pull_bundles_refuses_content_that_does_not_match_the_lock(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    assert run(capsys, "ws", "push-bundles", "--env", "acme")[0] == 0
    for key in list(bff.objects):
        if "/get/" in key:
            bff.objects[key] = b"tampered"
    (ws / "harness/bundles/login/capture.json").unlink()
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme")
    assert code == 1 and "does not match" in doc["error"]
    assert not (ws / "harness/bundles/login/capture.json").exists()


def test_bundle_commands_dry_run_make_no_calls(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    code, doc = run(capsys, "ws", "push-bundles", "--env", "acme", "--dry-run")
    assert code == 0 and len(doc["data"]["uploaded"]) == 2
    assert bff.calls == [] and bws.load_bundles_lock(ws)["entries"] == {}
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme", "--dry-run")
    assert code == 0 and bff.calls == []


def test_push_bundles_needs_the_store_in_builder_json(
    ws_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(ws_dir)
    bws.init_layout(ws)
    (ws / "harness" / "bundles").mkdir(parents=True, exist_ok=True)
    (ws / "harness" / "bundles" / "a.json").write_text("{}")
    WsBFF().install(monkeypatch)
    use_client(monkeypatch)
    code, doc = run(capsys, "ws", "push-bundles", "--env", "acme")
    assert code == 2 and "builder setup" in doc["error"]


def test_pull_bundles_rejects_unsafe_lock_paths(
    world: tuple[Path, WsBFF], capsys: pytest.CaptureFixture[str]
) -> None:
    ws, bff = world
    bws.save_bundles_lock(
        ws,
        {"entries": {"../evil.json": {"doc_store_path": "x", "sha256": "0" * 64, "size": 1}}},
    )
    code, doc = run(capsys, "ws", "pull-bundles", "--env", "acme")
    assert code == 0 and doc["data"]["skipped"][0]["reason"] == "unsafe path in the lock"
    assert not (ws.parent / "evil.json").exists()
