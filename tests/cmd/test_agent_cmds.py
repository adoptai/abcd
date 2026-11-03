"""``abcd agent new|push|pull|diff``, the Doc Store adapter and the eval row mapping."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cli.builder_common.docstore import DocStore, DocStoreArtifacts
from cli.builder_common.errors import RefusedError, UsageError
from cli.harness_common.api_client import HarnessAPIError
from tests.builder.evals_fakes import Reply, correct_handler

from .release_fixtures import AGENT, SKILL_MD, build_env, cycle

Capsys = pytest.CaptureFixture[str]


def error(doc: dict[str, Any]) -> str:
    return str(doc.get("error", ""))


def test_agent_new_writes_a_skeleton_and_refuses_to_overwrite(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    code, doc = env.run("agent", "new", "acme-fresh", "--dry-run", capsys=capsys)
    assert code == 0 and not (env.ws / "harness/agents/acme-fresh").exists()
    code, doc = env.run("agent", "new", "acme-fresh", "--display-name", "Fresh", capsys=capsys)
    assert code == 0, error(doc)
    d = env.ws / "harness/agents/acme-fresh"
    assert {p.name for p in d.iterdir()} == {
        "process.yaml",
        "instructions.md",
        "release.yaml",
        "prompts.yaml",
    }
    assert "blind:" in (d / "prompts.yaml").read_text()  # `eval run --blind` needs it
    assert "Fresh" in (d / "process.yaml").read_text()
    code, doc = env.run("agent", "new", "acme-fresh", capsys=capsys)
    assert code == 1
    code, doc = env.run("agent", "new", "Bad Name", capsys=capsys)
    assert code == 2


def test_agent_push_staged_needs_the_staged_skills_first(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    code, doc = env.run("agent", "push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "tax-calc--staged" in error(doc)
    assert env.client.writes() == []
    assert env.run("push", "tax-calc", "--staged", capsys=capsys)[0] == 0
    code, doc = env.run("agent", "push", AGENT, "--staged", capsys=capsys)
    assert code == 0, error(doc)
    assert (
        doc["data"]["version_id"] == f"{AGENT}@v1" and "ws-client" not in doc["data"]["workstreams"]
    )


def test_agent_push_live_needs_yes_and_records_the_process_id(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.client.org_skills["tax-calc"] = {"body": SKILL_MD, "aux": {}, "plugin": None}
    code, doc = env.run("agent", "push", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in error(doc)
    code, doc = env.run("agent", "push", AGENT, "--yes", "--dry-run", capsys=capsys)
    assert code == 0 and env.client.writes() == []
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    pid = env.client.find_process_by_name(AGENT)["id"]
    assert (
        json.loads((env.ws / "builder.json").read_text())["agents"][AGENT]["live_process_id"] == pid
    )
    assert env.client.assigned[pid] == ["ws-client"]
    assert env.client.grants[pid] == {"workstream_ids": ["ws-client"], "folder_prefix": "out/"}
    code, doc = env.run(
        "agent", "push", AGENT, "--yes", capsys=capsys
    )  # idempotent: patches nothing
    assert code == 0 and doc["data"]["patched"] == [] and doc["data"]["created"] is False


def test_agent_diff_and_pull_round_trip(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.client.org_skills["tax-calc"] = {"body": SKILL_MD, "aux": {}, "plugin": None}
    assert env.run("agent", "push", AGENT, "--yes", capsys=capsys)[0] == 0
    code, doc = env.run("agent", "diff", AGENT, capsys=capsys)
    assert code == 0 and doc["data"]["in_sync"] is True
    pid = doc["data"]["process_id"]
    env.client.processes[pid]["agent_instructions"] = "Edited in the UI."
    env.client.assigned[pid] = ["ws-client", "ws-staging"]
    code, doc = env.run("agent", "diff", AGENT, capsys=capsys)
    assert doc["data"]["in_sync"] is False
    assert {d["field"] for d in doc["data"]["diffs"]} == {"agent_instructions", "workstreams"}
    bundle = env.ws / f"harness/agents/{AGENT}/instructions.md"
    code, doc = env.run("agent", "pull", AGENT, "--dry-run", capsys=capsys)
    assert code == 0 and "Edited" not in bundle.read_text()
    code, doc = env.run("agent", "pull", AGENT, capsys=capsys)
    assert code == 0, error(doc)
    assert bundle.read_text().strip() == "Edited in the UI."
    assert "acme-staging" in (env.ws / f"harness/agents/{AGENT}/process.yaml").read_text()
    code, doc = env.run("agent", "diff", AGENT, capsys=capsys)
    assert doc["data"]["in_sync"] is True
    code, doc = env.run("agent", "diff", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "also client workstream" in error(doc)  # the pulled bundle now lists it


def test_agent_unknown_names(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("agent", "diff", "nope", capsys=capsys)[0] == 2
    assert env.run("push", "nope", "--staged", capsys=capsys)[0] == 2


# -- test-staged ------------------------------------------------------------------------------


def test_test_staged_is_resumable_and_writes_valid_rows(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.set_driver(lambda call: Reply(**{**correct_handler(call).__dict__, "polls": 2}))
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    assert env.run("stage", AGENT, capsys=capsys)[0] == 0
    code, doc = env.run(
        "test-staged", AGENT, "--poll-interval", "0", "--timeout", "0", capsys=capsys
    )
    assert code == 3 and "did not finish" in error(doc)
    runs = sorted((env.ws / ".builder/evals").iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    assert env.rows.find_rows("bld_eval_runs", "eval_run_id", run_id)[0]["status"] == "running"
    code, doc = env.run(
        "test-staged", AGENT, "--resume", run_id, "--poll-interval", "0", capsys=capsys
    )
    assert code == 0, error(doc)
    assert doc["data"]["run_id"] == run_id and doc["data"]["gate"]["passed"] is True
    row = env.rows.find_rows("bld_eval_runs", "eval_run_id", run_id)[0]
    assert (row["status"], row["agent_key"], row["version_id"], row["tier"]) == (
        "done",
        AGENT,
        f"{AGENT}@v1",
        "staged",
    )
    assert row["blind"] is True and row["dataset_version_key"].startswith("returns@")
    assert len(env.rows.find_rows("bld_eval_results", "eval_run_id", run_id)) == 3
    env.rows.find_rows("bld_metric_values", "scope_ref", run_id)  # written, valid
    assert env.run("test-staged", AGENT, "--resume", "no-such-run", capsys=capsys)[0] == 2


def test_test_staged_needs_a_staged_agent_and_a_driver(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    code, doc = env.run("test-staged", AGENT, capsys=capsys)
    assert code == 1 and "no staged version" in error(doc)
    cycle(env, capsys)  # also proves the default flow works end to end
    env.driver = None
    code, doc = env.run("test-staged", AGENT, capsys=capsys)
    assert code == 2 and "driver" in error(doc)


# -- the Doc Store adapter --------------------------------------------------------------------


class FakeDocClient:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], bytes] = {}
        self.pending: dict[str, tuple[str, str]] = {}

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        assert path.endswith("/document-download-url"), path
        store = path.split("/")[6]
        rel = kw["params"]["relative_path"]
        if (store, rel) not in self.docs:
            raise HarnessAPIError(method, path, 404, "not found")
        return {"url": f"https://example.test/dl/{store}:{rel.replace('/', '~')}"}

    def get_upload_urls(
        self, store_id: str, files: list[dict[str, Any]], override: bool = False
    ) -> Any:
        assert override is False
        if (store_id, files[0]["relative_path"]) in self.docs:
            raise HarnessAPIError("POST", "u", 409, "DuplicateFilename")
        url = f"https://example.test/up/{store_id}/{files[0]['relative_path']}"
        return {"batch_id": "b1", "upload_urls": [{"presigned_url": url}]}

    def confirm_upload(self, store_id: str, batch_id: str) -> Any:
        return {"confirmed": 1}


class Resp:
    def __init__(self, content: bytes = b"", status: int = 200) -> None:
        self.content, self.status_code = content, status


def test_doc_store_files_never_rewrites() -> None:
    client = FakeDocClient()

    def http_put(url: str, data: bytes, timeout: int) -> Resp:
        store, rel = url.split("/up/")[1].split("/", 1)
        client.docs[(store, rel)] = data
        return Resp()

    def http_get(url: str, timeout: int) -> Resp:
        key = url.split("/dl/")[1]
        store, rel = key.split(":", 1)
        return Resp(client.docs[(store, rel.replace("~", "/"))])

    docs = DocStoreArtifacts(
        DocStore(client, {"builder-ops": "st-ops"}, http_put=http_put, http_get=http_get)
    )
    sha = docs.put("builder-ops/releases/a/v1/report.html", b"<html>")
    assert len(sha) == 64 and docs.get("builder-ops/releases/a/v1/report.html") == b"<html>"
    assert docs.put("builder-ops/releases/a/v1/report.html", b"<html>") == sha  # same bytes: no-op
    with pytest.raises(RefusedError):
        docs.put("builder-ops/releases/a/v1/report.html", b"<other>")
    with pytest.raises(UsageError):
        docs.put("unknown-store/x.txt", b"x")
    with pytest.raises(Exception, match="not found"):
        docs.get("builder-ops/missing.txt")
