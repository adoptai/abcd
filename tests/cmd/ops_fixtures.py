"""Shared fakes for the ops command tests: a Doc Store, the Data Store and a workspace.

No network: ``requests.request``, ``requests.put`` and ``requests.get`` are replaced.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.tables import TABLES
from cli.builder_common.workspace import default_builder_config, save_builder_config
from cli.core.context import (
    WORKSPACES_ENV_VAR,
    Context,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.harness_common.api_client import HarnessAPIClient
from cli.wdl_common import workspace_manager as wm
from tests.builder.fakes import FakeBFF, FakeResponse

STORE_NAMES = ("builder-context", "builder-ops", "builder-status-acme")


class _Bytes:
    def __init__(self, status: int, data: bytes) -> None:
        self.status_code = status
        self.content = data
        self.text = data.decode("utf-8", "replace")


class FakeDocStore:
    """The Doc Store routes the ops commands use, plus presigned PUT and GET."""

    def __init__(self) -> None:
        self.stores: dict[str, dict[str, bytes]] = {}
        self.pending: dict[str, tuple[str, str]] = {}
        self.batches: dict[str, list[tuple[str, str]]] = {}
        self.put_log: list[str] = []
        self._n = 0

    def handle(self, method: str, path: str, params: Mapping[str, Any], body: Any) -> FakeResponse:
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/upload-urls", path)
        if m and method == "POST":
            sid = m.group(1)
            files = self.stores.setdefault(sid, {})
            urls = []
            batch: list[tuple[str, str]] = []
            self._n += 1
            bid = f"b{self._n}"
            for i, meta in enumerate(body["files"]):
                rel = meta["relative_path"]
                if rel in files and not body.get("override"):
                    return FakeResponse(409, {"detail": f"DuplicateFilename {rel}"})
                url = f"https://s3.test/{bid}/{i}"
                self.pending[url] = (sid, rel)
                batch.append((sid, rel))
                urls.append({"filename": meta["filename"], "s3_key": rel, "presigned_url": url})
            self.batches[bid] = batch
            return FakeResponse(201, {"batch_id": bid, "store_id": sid, "upload_urls": urls})
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/upload-confirm", path)
        if m and method == "POST":
            return FakeResponse(200, {"batch_id": body["batch_id"], "confirmed": 1})
        m = re.fullmatch(
            r"/v1/end-user/agent-harness/docstore/stores/([^/]+)/document-download-url", path
        )
        if m and method == "GET":
            sid, rel = m.group(1), params["relative_path"]
            if rel not in self.stores.get(sid, {}):
                return FakeResponse(404, {"detail": "not found"})
            return FakeResponse(200, {"url": f"https://s3.test/get/{sid}/{rel}"})
        return FakeResponse(404, {"detail": f"unrouted {method} {path}"})

    def put(self, url: str, data: bytes = b"", **_: Any) -> _Bytes:
        sid, rel = self.pending[url]
        self.stores.setdefault(sid, {})[rel] = data
        self.put_log.append(f"{sid}/{rel}")
        return _Bytes(200, b"")

    def get(self, url: str, **_: Any) -> _Bytes:
        m = re.fullmatch(r"https://s3\.test/get/([^/]+)/(.+)", url)
        assert m, url
        data = self.stores.get(m.group(1), {}).get(m.group(2))
        return _Bytes(200, data) if data is not None else _Bytes(404, b"")

    # -- test helpers ----------------------------------------------------
    def seed(self, store: str, rel: str, text: str) -> None:
        self.stores.setdefault(store, {})[rel] = text.encode()

    def read(self, store: str, rel: str) -> str:
        return self.stores[store][rel].decode()

    def paths(self, store: str) -> list[str]:
        return sorted(self.stores.get(store, {}))


@dataclass
class OpsEnv:
    ws: Path
    root: Path
    bff: FakeBFF
    docs: FakeDocStore
    db: OrgDBClient
    store_ids: dict[str, str] = field(default_factory=dict)

    def put_rows(self, table: str, *rows: dict[str, Any]) -> None:
        self.db.insert_rows(table, list(rows)).raise_for_failures()

    def tree(self) -> set[str]:
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()}


@contextmanager
def ops_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    original = wm.WORKSPACES_DIR
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(root))
    monkeypatch.setenv("ABCD_FDE", "dana")
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("ABCD_SESSION_ID", raising=False)
    monkeypatch.setenv("ABCD_EVENTS_UPLOAD", "0")
    apply_workspaces_override()
    ws = root / "acme"
    ws.mkdir()
    (ws / "env.json").write_text(json.dumps({"env_id": "acme"}))
    (ws / ".env").write_text("ADOPT_WEBUI_ENDPOINT=https://bff.test\n")
    store_ids = {name: f"store-{i}" for i, name in enumerate(STORE_NAMES)}
    save_builder_config(ws, default_builder_config(stores=store_ids))

    bff = FakeBFF().install(monkeypatch)
    docs = FakeDocStore()
    plain = bff.request

    def request(method: str, url: str, **kw: Any) -> Any:
        path = urlparse(url).path
        if "docstore" in path:
            bff.calls.append((method, path, kw.get("json") or dict(kw.get("params") or {})))
            return docs.handle(method, path, kw.get("params") or {}, kw.get("json"))
        return plain(method, url, **kw)

    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr("requests.put", docs.put)
    monkeypatch.setattr("requests.get", docs.get)
    client = HarnessAPIClient("tok", "https://bff.test")
    monkeypatch.setattr(Context, "harness_client", lambda self: client)
    db = OrgDBClient.from_client(client)
    for spec in TABLES:
        db.ensure_table(spec)
    try:
        yield OpsEnv(ws, root, bff, docs, db, store_ids)
    finally:
        monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
        set_workspaces_dir(original)


def run(*argv: str) -> tuple[int, dict[str, Any]]:
    """Run ``abcd <argv> --env acme --json`` in process; returns exit code and the JSON document."""
    import contextlib
    import io

    import cli.abcd as abcd_main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = abcd_main.main([*argv, "--env", "acme", "--json"])
    lines = [ln for ln in buf.getvalue().splitlines() if ln.startswith("{")]
    doc: dict[str, Any] = json.loads(lines[-1]) if lines else {}
    return code, doc
