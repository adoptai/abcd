"""Shared helpers for the ws / setup / plan command tests (no network, no GitHub).

``WsBFF`` extends ``tests.builder.fakes.FakeBFF`` (not edited) with the workstream, Doc Store and
process-workstream routes the commands use, plus the presigned PUT/GET hops.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from cli.abcd import main
from cli.core.context import (
    WORKSPACES_ENV_VAR,
    Context,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.harness_common.api_client import HarnessAPIClient
from cli.wdl_common import workspace_manager as wm
from tests.builder.fakes import FakeBFF, FakePlatform, FakeResponse

S3 = "https://s3.example.test"


class WsBFF(FakeBFF):
    """FakeBFF plus workstreams, Doc Stores (with presigned upload/download) and process links."""

    def __init__(self, platform: FakePlatform | None = None) -> None:
        super().__init__(platform)
        self.ws_rows: dict[str, dict[str, Any]] = {}
        self.stores: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, list[dict[str, Any]]] = {}
        self.batches: dict[str, list[dict[str, Any]]] = {}
        self.objects: dict[str, bytes] = {}
        self.process_ws: dict[str, list[str]] = {}
        self.n = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> WsBFF:
        super().install(monkeypatch)
        monkeypatch.setattr("requests.put", self._put)
        monkeypatch.setattr("requests.get", self._get)
        return self

    # -- helpers -------------------------------------------------------------
    def _new(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    def add_workstream(self, name: str) -> str:
        wid = self._new("ws")
        self.ws_rows[wid] = {"id": wid, "name": name}
        self.add_store("Workstream Files", [wid])  # the platform auto-creates a default store
        return wid

    def add_store(self, name: str, workstream_ids: list[str]) -> str:
        sid = self._new("st")
        self.stores[sid] = {"id": sid, "name": name, "workstream_ids": list(workstream_ids)}
        self.docs[sid] = []
        return sid

    def store_named(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.stores.values() if s["name"] == name), None)

    def ws_id(self, name: str) -> str:
        return next(w["id"] for w in self.ws_rows.values() if w["name"] == name)

    def count(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.calls if m == method and p == path)

    # -- presigned hops ------------------------------------------------------------
    def _put(self, url: str, data: bytes = b"", timeout: Any = None, **_: Any) -> FakeResponse:
        self.objects[url] = bytes(data)
        return FakeResponse(200)

    def _get(self, url: str, timeout: Any = None, **_: Any) -> Any:
        resp = FakeResponse(200 if url in self.objects else 404)
        resp.content = self.objects.get(url, b"")
        return resp

    # -- routing ---------------------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        timeout: Any = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        **kw: Any,
    ) -> FakeResponse:
        path = urlparse(url).path
        handled = self._mine(method, path, params or {}, json)
        if handled is None:
            return super().request(method, url, headers, timeout, params, json, **kw)
        self.calls.append((method, path, json if json is not None else dict(params or {})))
        return handled

    def _mine(
        self, method: str, path: str, params: Mapping[str, Any], body: Any
    ) -> FakeResponse | None:
        if path == "/v1/org/workstreams":
            if method == "GET":
                items = list(self.ws_rows.values())
                if params.get("search"):
                    items = [w for w in items if params["search"].lower() in w["name"].lower()]
                return FakeResponse(200, {"items": items, "total": len(items)})
            if method == "POST":
                if any(w["name"].lower() == body["name"].lower() for w in self.ws_rows.values()):
                    return FakeResponse(409, {"detail": "exists"})
                wid = self.add_workstream(body["name"])
                return FakeResponse(201, {"id": wid, "name": body["name"]})
        if path == "/v1/docstore/stores":
            if method == "GET":
                items = list(self.stores.values())
                if params.get("workstream_id"):
                    items = [s for s in items if params["workstream_id"] in s["workstream_ids"]]
                return FakeResponse(200, {"stores": items, "total": len(items)})
            if method == "POST":
                wids = [body["workstream_id"]] if body.get("workstream_id") else []
                sid = self.add_store(body["name"], wids)
                return FakeResponse(201, self.stores[sid])
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/workstreams", path)
        if m and method == "POST":
            store = self.stores[m.group(1)]
            for w in body["workstream_ids"]:
                if w not in store["workstream_ids"]:
                    store["workstream_ids"].append(w)
            return FakeResponse(200, {"linked": body["workstream_ids"], "store_id": m.group(1)})
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/upload-urls", path)
        if m and method == "POST":
            sid = m.group(1)
            existing = {d["relative_path"] for d in self.docs[sid]}
            clash = [f["relative_path"] for f in body["files"] if f["relative_path"] in existing]
            if clash and not body.get("override"):
                return FakeResponse(409, {"detail": f"duplicate {clash[0]}"})
            batch = self._new("b")
            self.batches[batch] = []
            urls = []
            for f in body["files"]:
                key = f"{sid}/{batch}/{f['relative_path']}"
                self.batches[batch].append({**f, "key": key})
                urls.append(
                    {"filename": f["filename"], "s3_key": key, "presigned_url": f"{S3}/put/{key}"}
                )
            return FakeResponse(201, {"batch_id": batch, "store_id": sid, "upload_urls": urls})
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/upload-confirm", path)
        if m and method == "POST":
            sid = m.group(1)
            confirmed = []
            for f in self.batches[body["batch_id"]]:
                put_url = f"{S3}/put/{f['key']}"
                if put_url not in self.objects:
                    continue
                doc = {"id": self._new("d"), "relative_path": f["relative_path"], "key": f["key"]}
                self.docs[sid].append(doc)
                self.objects[f"{S3}/get/{doc['id']}"] = self.objects[put_url]
                confirmed.append(doc)
            return FakeResponse(
                200,
                {"batch_id": body["batch_id"], "confirmed": len(confirmed), "documents": confirmed},
            )
        m = re.fullmatch(r"/v1/docstore/stores/([^/]+)/documents", path)
        if m and method == "GET":
            docs = self.docs[m.group(1)]
            return FakeResponse(200, {"documents": docs, "total": len(docs)})
        m = re.fullmatch(
            r"/v1/end-user/agent-harness/docstore/stores/([^/]+)/document-download-url", path
        )
        if m and method == "GET":
            found = [
                d
                for d in self.docs.get(m.group(1), [])
                if d["relative_path"] == params["relative_path"]
            ]
            if not found:
                return FakeResponse(404, {"detail": "not found"})
            return FakeResponse(200, {"url": f"{S3}/get/{found[-1]['id']}", "expires_in": 60})
        m = re.fullmatch(r"/v1/org/processes/([^/]+)/workstreams", path)
        if m and method == "GET":
            return FakeResponse(200, {"workstream_ids": self.process_ws.get(m.group(1), [])})
        m = re.fullmatch(r"/v1/org/processes/([^/]+)/write-workstreams", path)
        if m and method == "GET":
            return FakeResponse(200, {"grants": [], "workstream_ids": [], "folder_prefix": ""})
        return None


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def workspaces_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temp workspaces dir (wired through ABCD_WORKSPACES_DIR) and a git-safe environment."""
    original = wm.WORKSPACES_DIR
    target = tmp_path / "workspaces"
    target.mkdir()
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(target))
    for var in ("ABCD_GITHUB_TOKEN", "SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.test")
    apply_workspaces_override()
    yield target
    monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
    set_workspaces_dir(original)


def use_client(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make ``Context.harness_client`` return a client on the fake BFF; counts the calls."""
    built: list[int] = []

    def fake(self: Context) -> HarnessAPIClient:
        built.append(1)
        return HarnessAPIClient("test-token", "https://bff.example.test")

    monkeypatch.setattr(Context, "harness_client", fake)
    return built


def make_workspace(ws_dir: Path, name: str = "acme", **meta: Any) -> Path:
    d = ws_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "env.json").write_text(json.dumps({"env_id": name, "name": name.title(), **meta}))
    return d


def bare_repo(tmp_path: Path, name: str = "remote.git") -> Path:
    path = tmp_path / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Run ``abcd <argv> --json``; return the exit code and the JSON document."""
    capsys.readouterr()
    code = main([*argv, "--json"])
    out = capsys.readouterr().out.strip()
    doc: dict[str, Any] = json.loads(out.splitlines()[-1]) if out else {}
    return code, doc
