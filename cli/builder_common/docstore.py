"""The one Doc Store client every Builder command uses (PLAN.md sections 9 and 12).

Files are addressed by ``<store name>/<relative path>``; the store name resolves to an id through
``builder.json`` ``stores`` (or a resolver that creates the store on demand). Routes, all through
the BFF:

* ``POST /v1/docstore/stores/{id}/upload-urls``, a presigned PUT, then
  ``POST /v1/docstore/stores/{id}/upload-confirm`` (BFF_ROUTES.md section 7).
* ``GET /v1/end-user/agent-harness/docstore/stores/{id}/document-download-url`` (by relative
  path), then a GET of the presigned URL.
* ``GET /v1/docstore/stores/{id}/documents`` (paged) for listings.

Uploads never overwrite unless ``replace=True`` (``override: true`` on the platform, which
soft-deletes the colliding document and gives the new upload a new document id).
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.evals.protocols import sha256_hex
from cli.harness_common.api_client import HarnessAPIError

STORE_BASE = "/v1/docstore"
CONSOLE_BASE = "/v1/end-user/agent-harness/docstore"
PAGE_SIZE = 500
TIMEOUT = 120

#: ``(store name, relative path)`` for a full path; datasets route their inputs elsewhere.
Route = Callable[[str], tuple[str, str]]


class DocStoreError(PlatformError):
    """The Doc Store refused or failed a call."""


class ConflictError(DocStoreError):
    """The path already exists and ``replace`` was not set."""


class NotFoundError(DocStoreError):
    """No such file."""


def safe_relative(path: str) -> Path:
    """A relative path with no ``..``, drive or leading slash (rows come from the platform)."""
    p = Path(path.replace("\\", "/"))
    if not path or p.is_absolute() or ".." in p.parts or ":" in p.parts[0]:
        raise UsageError(f"unsafe path from the platform: {path!r}")
    return p


def default_route(path: str) -> tuple[str, str]:
    store, _, rel = path.partition("/")
    if not store or not rel:
        raise UsageError(f"Doc Store path {path!r} must be <store>/<path>")
    return store, rel


@dataclass(frozen=True)
class Upload:
    """One file for :meth:`DocStore.put_many`; ``rel`` is the path inside the store."""

    rel: str
    data: bytes
    content_type: str | None = None


class DocStore:
    """Files by ``<store>/<path>`` over the BFF Doc Store routes.

    ``stores`` maps a store name to its id (``builder.json`` ``stores``); ``resolve`` replaces it
    when a store may have to be created on first use. ``route`` splits a full path into
    ``(store name, relative path)``. ``http_put`` and ``http_get`` default to ``requests`` (looked
    up at call time, so tests can patch it).
    """

    def __init__(
        self,
        client: Any,
        stores: Mapping[str, str] | None = None,
        *,
        resolve: Callable[[str], str] | None = None,
        route: Route = default_route,
        http_put: Callable[..., Any] | None = None,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self.client = client
        self.stores = dict(stores or {})
        self._resolve = resolve
        self.route = route
        self._http_put = http_put
        self._http_get = http_get

    # -- addressing --------------------------------------------------------------------------

    def store_id(self, name: str) -> str:
        """The id of the store named ``name`` (builder.json ``stores``, else the resolver)."""
        if self._resolve is not None:
            return self._resolve(name)
        sid = self.stores.get(name)
        if not sid:
            raise UsageError(
                f"builder.json has no store {name!r}; run `abcd builder setup` (or add its id "
                "under 'stores')"
            )
        return sid

    def split(self, path: str) -> tuple[str, str, str]:
        store, rel = self.route(path)
        sid = self.store_id(store)
        safe_relative(rel)
        return store, sid, rel

    # -- upload ------------------------------------------------------------------------------

    @staticmethod
    def _meta(item: Upload) -> dict[str, Any]:
        name = item.rel.rsplit("/", 1)[-1]
        meta: dict[str, Any] = {
            "filename": name,
            "file_type": Path(name).suffix.lstrip(".") or "bin",
            "size_bytes": len(item.data),
            "relative_path": item.rel,
        }
        if item.content_type:
            meta["content_type"] = item.content_type
        return meta

    def put_many(self, store: str, items: Iterable[Upload], *, replace: bool = False) -> None:
        """Upload ``items`` to ``store`` in one batch: upload-urls, a presigned PUT each, confirm.

        Raises :class:`ConflictError` if a path exists and ``replace`` is false.
        """
        batch = list(items)
        if not batch:
            return
        sid = self.store_id(store)
        label = batch[0].rel if len(batch) == 1 else f"{len(batch)} files in {store}"
        try:
            presign = self.client.get_upload_urls(
                sid, [self._meta(i) for i in batch], override=replace
            )
        except HarnessAPIError as exc:
            if exc.status_code == 409 or "duplicate" in str(exc.body).lower():
                raise ConflictError(f"{label} already exists") from exc
            raise DocStoreError(f"upload of {label} failed: {exc}") from exc
        urls = presign.get("upload_urls") or []
        if len(urls) != len(batch):
            raise DocStoreError(
                f"upload of {label}: asked for {len(batch)} upload URL(s), got {len(urls)}"
            )
        put = self._http_put or requests.put
        for url, item in zip(urls, batch, strict=True):
            try:
                resp = put(url["presigned_url"], data=item.data, timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise DocStoreError(f"upload of {item.rel} failed: {type(exc).__name__}") from exc
            if resp.status_code >= 400:
                raise DocStoreError(f"upload of {item.rel} failed: HTTP {resp.status_code}")
        try:
            confirm = self.client.confirm_upload(sid, presign["batch_id"])
        except HarnessAPIError as exc:
            raise DocStoreError(f"confirming the upload of {label} failed: {exc}") from exc
        confirmed = confirm.get("confirmed") if isinstance(confirm, dict) else None
        if confirmed is not None and int(confirmed) != len(batch):
            raise DocStoreError(f"only {confirmed} of {len(batch)} uploads confirmed")

    def put(self, path: str, data: bytes, *, replace: bool = False) -> str:
        """Upload ``data`` to ``<store>/<path>``; returns its SHA-256.

        Raises :class:`ConflictError` when the path exists and ``replace`` is false.
        """
        store, _sid, rel = self.split(path)
        self.put_many(store, [Upload(rel, data)], replace=replace)
        return sha256_hex(data)

    def put_text(self, path: str, text: str, *, replace: bool = False) -> str:
        return self.put(path, text.encode("utf-8"), replace=replace)

    # -- download ----------------------------------------------------------------------------

    def _download_url(self, path: str) -> str:
        _store, sid, rel = self.split(path)
        try:
            meta = self.client._request(
                "GET",
                f"{CONSOLE_BASE}/stores/{sid}/document-download-url",
                params={"relative_path": rel, "disposition": "attachment"},
            )
        except HarnessAPIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"{path} not found") from exc
            raise DocStoreError(f"download of {path} failed: {exc}") from exc
        url = meta.get("url") if isinstance(meta, dict) else None
        if not url:
            raise DocStoreError(f"download of {path}: no URL returned")
        return str(url)

    def get(self, path: str) -> bytes:
        """The file's bytes; :class:`NotFoundError` when the path is not in the store."""
        url = self._download_url(path)
        try:
            resp = (self._http_get or requests.get)(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise DocStoreError(f"download of {path} failed: {type(exc).__name__}") from exc
        if resp.status_code == 404:
            raise NotFoundError(f"{path} not found")
        if resp.status_code >= 400:
            raise DocStoreError(f"download of {path} failed: HTTP {resp.status_code}")
        content = resp.content
        return content if isinstance(content, bytes) else bytes(str(content), "utf-8")

    def exists(self, path: str) -> bool:
        try:
            self._download_url(path)
        except NotFoundError:
            return False
        return True

    def list(self, store: str, prefix: str = "") -> builtins.list[dict[str, Any]]:
        """Every document of ``store`` (flat, with ``relative_path``), optionally under ``prefix``.

        The platform has no prefix filter; this pages through ``documents`` and filters here.
        """
        return list_documents(self.client, self.store_id(store), prefix)


def list_documents(client: Any, store_id: str, prefix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        body = client._request(
            "GET",
            f"{STORE_BASE}/stores/{store_id}/documents",
            params={"page": page, "page_size": PAGE_SIZE},
        )
        docs = body.get("documents", []) if isinstance(body, dict) else (body or [])
        out.extend(d for d in docs if str(d.get("relative_path") or "").startswith(prefix))
        if len(docs) < PAGE_SIZE:
            return out
        page += 1


class DocStoreArtifacts:
    """The ``evals.protocols.ArtifactStore`` shape over a :class:`DocStore`.

    ``put`` never overwrites: re-putting the same bytes is a no-op, different bytes raise
    :class:`RefusedError`. Paths where ``replace_when(path)`` is true (client sync copies) are
    replaced. ``put`` returns a SHA-256 and ``get`` bytes, as the protocol expects.
    """

    def __init__(
        self, docs: DocStore, *, replace_when: Callable[[str], bool] = lambda _p: False
    ) -> None:
        self.docs = docs
        self.replace_when = replace_when

    def put(self, path: str, data: bytes) -> str:
        try:
            return self.docs.put(path, data, replace=self.replace_when(path))
        except ConflictError as exc:
            if self.docs.get(path) != data:
                raise RefusedError(
                    f"{path} already exists with different content (never overwrite)"
                ) from exc
            return sha256_hex(data)

    def get(self, path: str) -> bytes:
        return self.docs.get(path)
