"""The shared Doc Store client: stores by name, no-overwrite put, replace, get, exists, list."""

from __future__ import annotations

from typing import Any

import pytest

from cli.builder_common.docstore import (
    ConflictError,
    DocStore,
    DocStoreArtifacts,
    NotFoundError,
    Upload,
)
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.protocols import ArtifactStore, sha256_hex
from cli.harness_common.api_client import HarnessAPIError


class Resp:
    def __init__(self, status: int = 200, content: bytes = b"") -> None:
        self.status_code, self.content = status, content


class Client:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.pending: dict[str, tuple[str, str]] = {}
        self.uploads: list[bool] = []

    def get_upload_urls(self, sid: str, files: list[dict[str, Any]], override: bool = False) -> Any:
        self.uploads.append(override)
        for f in files:
            if (sid, f["relative_path"]) in self.files and not override:
                raise HarnessAPIError("POST", "u", 409, "DuplicateFilename")
        self.pending.clear()
        urls = []
        for f in files:
            url = f"put://{len(self.pending)}"
            self.pending[url] = (sid, f["relative_path"])
            urls.append({"presigned_url": url})
        return {"batch_id": "b", "upload_urls": urls}

    def confirm_upload(self, sid: str, batch: str) -> Any:
        return {"confirmed": len(self.pending)}

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        sid = path.split("/stores/")[1].split("/")[0]
        if path.endswith("/document-download-url"):
            rel = kw["params"]["relative_path"]
            if (sid, rel) not in self.files:
                raise HarnessAPIError(method, path, 404, "nf")
            return {"url": f"get://{sid}/{rel}"}
        assert path.endswith("/documents")
        return {"documents": [{"relative_path": r} for (s, r) in self.files if s == sid]}


def make() -> tuple[Client, DocStore]:
    c = Client()

    def put(url: str, data: bytes, timeout: int) -> Resp:
        c.files[c.pending[url]] = data
        return Resp()

    def get(url: str, timeout: int) -> Resp:
        sid, rel = url.removeprefix("get://").split("/", 1)
        return Resp(200, c.files[(sid, rel)])

    return c, DocStore(c, {"ops": "s1"}, http_put=put, http_get=get)


def test_put_get_exists_list_and_no_overwrite() -> None:
    c, docs = make()
    assert docs.put("ops/a/b.txt", b"one") == sha256_hex(b"one")
    assert docs.get("ops/a/b.txt") == b"one" and docs.exists("ops/a/b.txt")
    assert not docs.exists("ops/missing.txt")
    with pytest.raises(NotFoundError):
        docs.get("ops/missing.txt")
    with pytest.raises(ConflictError):
        docs.put_text("ops/a/b.txt", "two")
    docs.put_text("ops/a/b.txt", "two", replace=True)
    assert docs.get("ops/a/b.txt") == b"two" and c.uploads == [False, False, True]
    docs.put("ops/z.txt", b"z")
    assert [d["relative_path"] for d in docs.list("ops", "a/")] == ["a/b.txt"]
    with pytest.raises(UsageError):
        docs.put("nostore/x", b"x")
    with pytest.raises(UsageError):
        docs.put("ops/../x", b"x")


def test_put_many_is_one_batch() -> None:
    c, docs = make()
    docs.put_many("ops", [Upload("a.json", b"1", "application/json"), Upload("b.bin", b"2")])
    assert c.uploads == [False] and set(c.files) == {("s1", "a.json"), ("s1", "b.bin")}


def test_artifact_shape_is_idempotent_and_never_rewrites() -> None:
    _c, docs = make()
    store = DocStoreArtifacts(docs, replace_when=lambda p: "/_builder/" in p)
    assert isinstance(store, ArtifactStore)
    sha = store.put("ops/r.json", b"x")
    assert store.put("ops/r.json", b"x") == sha and store.get("ops/r.json") == b"x"
    with pytest.raises(RefusedError, match="never overwrite"):
        store.put("ops/r.json", b"y")
    store.put("ops/_builder/i.md", b"1")
    store.put("ops/_builder/i.md", b"2")
    assert store.get("ops/_builder/i.md") == b"2"
