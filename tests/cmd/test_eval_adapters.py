"""The platform adapters in ``cli/builder_eval.py``: Doc Store, Data Store rows, BFF operations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cli import builder_eval as be
from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common import workspace as bws
from cli.builder_common.docstore import DocStore, DocStoreArtifacts, Route, default_route
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.protocols import sha256_hex
from cli.builder_common.rowstore import OrgRowStore
from cli.harness_common.api_client import HarnessAPIError
from tests.builder.fakes import MemoryStore


class Resp:
    def __init__(self, status: int = 200, content: bytes = b"") -> None:
        self.status_code, self.content = status, content


class FakeClient:
    """Just the HarnessAPIClient methods the adapters use."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, Any]] = []
        self.workstreams: list[dict[str, str]] = []
        self.process_ws: dict[str, list[str]] = {"p1": ["w-old"]}
        self.pending: dict[str, tuple[str, str]] = {}

    def get_upload_urls(
        self,
        store_id: str,
        files: list[dict[str, Any]],
        override: bool = False,
        embed: bool = False,
    ) -> dict[str, Any]:
        f = files[0]
        self.calls.append(("upload-urls", (store_id, f["relative_path"], override)))
        if (store_id, f["relative_path"]) in self.docs and not override:
            raise HarnessAPIError("POST", "u", 409, "DuplicateFilename")
        batch = f"b{len(self.calls)}"
        self.pending[batch] = (store_id, f["relative_path"])
        return {
            "batch_id": batch,
            "upload_urls": [{"presigned_url": f"put://{batch}", "s3_key": "k"}],
        }

    def confirm_upload(self, store_id: str, batch_id: str) -> dict[str, Any]:
        self.calls.append(("confirm", batch_id))
        return {"confirmed": 1}

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        if path.endswith("/document-download-url"):
            sid = path.split("/stores/")[1].split("/")[0]
            rel = kw["params"]["relative_path"]
            return {"url": f"get://{sid}/{rel}"}
        if path == "/v1/docstore/stores/resolve":
            return {"id": f"s-{kw['json']['name']}", "workstream_ids": []}
        if path == "/v1/org/workstreams":
            w = {"id": f"w-{len(self.workstreams) + 1}", "name": kw["json"]["name"]}
            self.calls.append(("create-ws", kw["json"]))
            self.workstreams.append(w)
            return w
        raise AssertionError(path)

    def list_workstreams(self, search: str | None = None) -> dict[str, Any]:
        return {"items": [w for w in self.workstreams if search in w["name"]]}

    def link_store_to_workstreams(self, store_id: str, ids: list[str]) -> dict[str, Any]:
        self.calls.append(("link", (store_id, ids)))
        return {}

    def get_process_workstreams(self, pid: str) -> dict[str, Any]:
        return {"workstream_ids": self.process_ws[pid]}

    def set_process_workstreams(self, pid: str, ids: list[str]) -> dict[str, Any]:
        self.process_ws[pid] = ids
        return {}

    def delete_workstream(self, wid: str) -> dict[str, Any]:
        self.calls.append(("delete-ws", wid))
        return {}


class FakeOps:
    def store_id(self, name: str) -> str:
        return f"s-{name}"


def _store(client: FakeClient, route: Route = default_route) -> DocStoreArtifacts:
    def put(url: str, data: bytes, timeout: int) -> Resp:
        sid, rel = client.pending[url.removeprefix("put://")]
        client.docs[(sid, rel)] = data
        return Resp()

    def get(url: str, timeout: int) -> Resp:
        sid, rel = url.removeprefix("get://").split("/", 1)
        return Resp(content=client.docs[(sid, rel)]) if (sid, rel) in client.docs else Resp(404)

    docs = DocStore(client, resolve=FakeOps().store_id, route=route, http_put=put, http_get=get)
    return DocStoreArtifacts(docs, replace_when=lambda p: "/_builder/" in p)


def test_docstore_put_get_and_never_overwrite() -> None:
    c = FakeClient()
    s = _store(c)
    sha = s.put("ds-returns/expected/c1/return.json", b"one")
    assert (
        sha == sha256_hex(b"one") and c.docs[("s-ds-returns", "expected/c1/return.json")] == b"one"
    )
    assert s.get("ds-returns/expected/c1/return.json") == b"one"
    assert s.put("ds-returns/expected/c1/return.json", b"one") == sha  # idempotent retry
    with pytest.raises(RefusedError, match="never overwrite"):
        s.put("ds-returns/expected/c1/return.json", b"two")
    s.put("acme-staging/_builder/instructions/x.md", b"v1")
    s.put("acme-staging/_builder/instructions/x.md", b"v2")  # client sync copies may be replaced
    assert ("upload-urls", ("s-acme-staging", "_builder/instructions/x.md", True)) in c.calls
    assert c.docs[("s-acme-staging", "_builder/instructions/x.md")] == b"v2"
    with pytest.raises(UsageError):
        s.put("nostore", b"x")


def test_dataset_inputs_route_by_layout(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ws.mkdir()
    D.create_dataset(ws, "returns", "work_product")  # per-case
    D.create_dataset(ws, "inbox", "labeled")  # shared
    route = be.dataset_route(ws)
    assert route("ds:returns/inputs/c1/a.pdf") == ("ds:returns:c1", "inputs/a.pdf")
    assert route("ds:inbox/inputs/e1/mail.eml") == ("ds:inbox", "inputs/e1/mail.eml")
    assert route("ds-returns/expected/c1/x.json") == ("ds-returns", "expected/c1/x.json")
    assert route("ds:unknown/inputs/c1/a") == ("ds:unknown", "inputs/c1/a")


def test_bff_ops_workstreams_stores_and_assignment(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ws.mkdir()
    bws.init_layout(ws)
    bws.update_builder_config(ws, workstreams={"builder-ops": "w-ops"})
    c = FakeClient()
    ops = be.BffOps(c, ws)  # type: ignore[arg-type]
    wid = ops.ensure_workstream("ds:returns:c1", "d", {"kind": "dataset"})
    assert (
        "create-ws",
        {"name": "ds:returns:c1", "description": "d", "custom_properties": {"kind": "dataset"}},
    ) in c.calls
    assert ops.ensure_workstream("ds:returns:c1") == wid and len(c.workstreams) == 1  # cached
    assert ops.workstream_id("ds:returns:c1") == wid and ops.workstream_id("nope") is None
    sid = ops.store_id("ds-returns")
    assert ("link", (sid, ["w-ops"])) in c.calls  # ds- stores link to builder-ops only
    assert bws.load_builder_config(ws)["stores"]["ds-returns"] == sid
    assert ops.store_id("ds-returns") == sid
    assert ops.assign_process("p1", [wid]) == sorted(["w-old", wid])
    ops.archive_workstream("ds:returns:c1")
    assert ("delete-ws", wid) in c.calls
    bws.update_builder_config(ws, workstreams={})
    ops2 = be.BffOps(c, ws)  # type: ignore[arg-type]
    other = tmp_path / "x"
    other.mkdir()
    bws.init_layout(other)
    with pytest.raises(UsageError, match="builder-ops"):
        be.BffOps(c, other).store_id("ds-inbox")  # type: ignore[arg-type]
    assert ops2.ensure_workstream("ds:returns:c1") == wid  # found by search, not recreated


def test_bff_ops_store_resolve_never_collapses_onto_the_workstreams_store(tmp_path: Path) -> None:
    """Live 2026-10-29: `resolve` with `workstream_id` returned the workstream's existing store
    (named `noui-bundles`) for every `ds-<dataset>`, so datasets shared one store and collided."""
    ws = tmp_path / "acme"
    ws.mkdir()
    bws.init_layout(ws)
    bws.update_builder_config(ws, workstreams={"builder-ops": "w-ops"})

    class Quirky(FakeClient):
        def _request(self, method: str, path: str, **kw: Any) -> Any:
            if path == "/v1/docstore/stores/resolve" and kw["json"].get("workstream_id"):
                self.calls.append((method, path))
                return {"id": "s-shared", "name": "noui-bundles", "workstream_ids": ["w-ops"]}
            if path == "/v1/docstore/stores":
                self.calls.append((method, path))
                return {"id": f"s-{kw['json']['name']}", "name": kw["json"]["name"]}
            if path == "/v1/docstore/stores/resolve":
                self.calls.append((method, path))
                return {"id": "s-shared", "name": "noui-bundles", "workstream_ids": []}
            return super()._request(method, path, **kw)

    c = Quirky()
    ops = be.BffOps(c, ws)  # type: ignore[arg-type]
    a = ops.store_id("ds-one")
    b = ops.store_id("ds-two")
    assert a == "s-ds-one" and b == "s-ds-two"  # created under their own names


def test_row_store_validates_and_upserts() -> None:
    backend = MemoryStore()
    rows = OrgRowStore(backend)
    rows.insert("bld_context_gaps", {"gap_key": "g1", "agent_key": "a", "case_key": "d:c1",
                                     "assigned_to": "fde", "status": "open", "hypothesis": "h"})  # fmt: skip
    assert backend.list_all("bld_context_gaps")[0]["case_key"] == "d:c1"
    rows.update("bld_context_gaps", "g1", {"status": "asked", "waive_reason": "r"})
    assert backend.list_all("bld_context_gaps")[0]["status"] == "asked"
    rows.update("bld_context_gaps", "g2", {"status": "open", "agent_key": "a"})  # missing: inserted
    assert {r["gap_key"] for r in backend.list_all("bld_context_gaps")} == {"g1", "g2"}
    assert rows.query("bld_context_gaps", {"status": "open"})[0]["gap_key"] == "g2"
    with pytest.raises(UsageError):  # enums are checked here because the Data Store does not
        rows.insert("bld_context_gaps", {"gap_key": "g3", "status": "bogus"})
    # answers can never ride along: a column that is not in the spec is refused, not dropped
    with pytest.raises(UsageError, match="unknown column 'expected'"):
        rows.insert("bld_checks", {"check_key": "k", "expected": "SECRET", "title": "t"})
    assert backend.list_all("bld_checks") == []


def test_get_services_builds_the_real_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli.harness_common.api_client import HarnessAPIClient

    ws = tmp_path / "acme"
    ws.mkdir()
    bws.init_layout(ws)
    ctx = SimpleNamespace(
        env_dir=ws,
        args=SimpleNamespace(model="claude-haiku-4-5"),
        harness_client=lambda: HarnessAPIClient("tok", "http://bff.invalid"),
    )
    svc = be.get_services(ctx)  # type: ignore[arg-type]
    assert isinstance(svc.store, DocStoreArtifacts) and isinstance(svc.rows, OrgRowStore)
    assert isinstance(svc.ops, be.BffOps) and svc.driver is not None
    assert svc.driver.model == "claude-haiku-4-5"  # type: ignore[attr-defined]
