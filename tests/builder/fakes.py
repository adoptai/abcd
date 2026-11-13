"""In-memory fakes for Builder tests: BFF (org-db + platform routes), store, platform.

No network: ``FakeBFF.install`` replaces ``requests.request``.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import pytest

from cli.builder_common.orgdb import InsertResult
from cli.builder_common.tables import get_spec

TRUNC = "[... truncated: only the first 262144 bytes shown]"


class FakeResponse:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ""
        self.content = self.text.encode() if body is not None else b""

    def json(self) -> Any:
        return self._body


class FakePlatform:
    """Skills catalog (org/default), skill detail, files, processes."""

    def __init__(self) -> None:
        self.org_skills: dict[str, dict[str, Any]] = {}
        self.default_skills: dict[str, dict[str, Any]] = {}
        self.default_versions: dict[str, str] = {}
        self.processes: dict[str, dict[str, Any]] = {}
        self.plugin_of: dict[str, str] = {}
        self.operator = False

    # -- seeding -----------------------------------------------------------
    @staticmethod
    def _skill(
        body: str,
        aux: Mapping[str, str | bytes] | None = None,
        truncated: Sequence[str] = (),
    ) -> dict[str, Any]:
        return {"body": body, "aux": dict(aux or {}), "truncated": set(truncated)}

    def add_org_skill(
        self,
        name: str,
        body: str,
        aux: Mapping[str, str | bytes] | None = None,
        truncated: Sequence[str] = (),
        plugin: str | None = None,
    ) -> None:
        self.org_skills[name] = self._skill(body, aux, truncated)
        if plugin:
            self.plugin_of[name] = plugin

    def add_default_skill(
        self,
        name: str,
        body: str,
        version: str = "1.0.0",
        aux: Mapping[str, str | bytes] | None = None,
    ) -> None:
        self.default_skills[name] = self._skill(body, aux)
        self.default_versions[name] = version

    def add_process(self, pid: str, name: str, **fields: Any) -> dict[str, Any]:
        proc = {
            "id": pid,
            "name": name,
            "display_name": fields.pop("display_name", name),
            "description": None,
            "components": [],
            "agent_instructions": None,
            "is_enabled": True,
            "compiled_flow": None,
        }
        proc.update(fields)
        self.processes[pid] = proc
        return proc

    # -- routing -----------------------------------------------------------
    def _effective(self) -> dict[str, tuple[str, dict[str, Any]]]:
        out = {n: ("default", s) for n, s in self.default_skills.items()}
        out.update({n: ("org", s) for n, s in self.org_skills.items()})  # org wins
        return out

    def handle(self, method: str, path: str, params: Mapping[str, Any], body: Any) -> FakeResponse:
        base = "/v1/end-user/agent-harness"
        if path == "/v1/org/processes" and method == "GET":
            items = []
            for p in self.processes.values():
                items.append({k: v for k, v in p.items() if k != "compiled_flow"})
            return FakeResponse(200, {"items": items})
        m = re.fullmatch(r"/v1/org/processes/([^/]+)", path)
        if m and method == "GET":
            proc = self.processes.get(m.group(1))
            return FakeResponse(200, proc) if proc else FakeResponse(404, {"detail": "nope"})
        if path == f"{base}/skills" and method == "GET":
            items = []
            for name, (source, _sk) in sorted(self._effective().items()):
                items.append(
                    {
                        "name": name,
                        "description": "",
                        "source": source,
                        "plugin": self.plugin_of.get(name),
                    }
                )
            return FakeResponse(200, {"items": items})
        m = re.fullmatch(rf"{base}/skills/([^/]+)", path)
        if m and method == "GET":
            eff = self._effective().get(m.group(1))
            if eff is None:
                return FakeResponse(404, {"detail": "not found"})
            source, sk = eff
            aux = [
                {"path": p, "size": len(c if isinstance(c, bytes) else c.encode())}
                for p, c in sorted(sk["aux"].items())
            ]
            return FakeResponse(
                200,
                {
                    "name": m.group(1),
                    "source": source,
                    "description": "",
                    "body": sk["body"],
                    "aux_files": aux,
                },
            )
        m = re.fullmatch(rf"{base}/skills/([^/]+)/files", path)
        if m and method == "GET":
            eff = self._effective().get(m.group(1))
            rel = params.get("path")
            if eff is None or rel not in eff[1]["aux"]:
                return FakeResponse(404, {"detail": "missing"})
            content = eff[1]["aux"][rel]
            if isinstance(content, bytes):
                return FakeResponse(415, {"detail": "binary file"})
            if rel in eff[1]["truncated"]:
                content = content[:10] + TRUNC
            return FakeResponse(200, {"path": rel, "content": content})
        m = re.fullmatch(rf"{base}/default-skills/([^/]+)", path)
        if m and method == "GET":
            if not self.operator:
                return FakeResponse(403, {"detail": "adopt only"})
            name = m.group(1)
            if name not in self.default_skills:  # the real route answers 200, published false
                return FakeResponse(
                    200,
                    {"skill_name": name, "published": False, "bundle_version": None,
                     "content_dir": None},
                )  # fmt: skip
            return FakeResponse(
                200,
                {
                    "skill_name": name,
                    "published": True,
                    "bundle_version": self.default_versions[name],
                    "content_dir": f"d/{name}",
                },
            )
        if path == f"{base}/skills" and method == "POST":
            name = body["skill_name"]
            if (name in self.org_skills or name in self.default_skills) and not body.get("replace"):
                return FakeResponse(409, {"detail": "exists"})
            md = base64.b64decode(body["skill_md_b64"]).decode()
            aux = {
                a["path"]: base64.b64decode(a["content_b64"]).decode()
                for a in body.get("aux_files", [])
            }
            self.org_skills[name] = self._skill(md, aux)
            return FakeResponse(200, {"keys": [name]})
        return FakeResponse(404, {"detail": f"unrouted {method} {path}"})


class FakeBFF:
    """Monkeypatch for ``requests.request``: org-db routes plus a FakePlatform."""

    def __init__(self, platform: FakePlatform | None = None) -> None:
        self.platform = platform or FakePlatform()
        self.tables: dict[str, dict[str, Any]] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.workstreams: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self._next = 1
        self.fail: dict[tuple[str, int], int] = {}  # (table_id, nth insert call 1-based) -> status
        self.insert_calls = 0
        self.last_sql: str | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeBFF:
        monkeypatch.setattr("requests.request", self.request)
        return self

    # -- helpers -----------------------------------------------------------
    def _id(self) -> str:
        self._next += 1
        return f"t{self._next}"

    def _unique_cols(self, tid: str) -> list[str]:
        return [c["name"] for c in self.tables[tid]["column_schema"] if c.get("unique")]

    def calls_to(self, method: str, suffix: str) -> list[Any]:
        return [b for m, p, b in self.calls if m == method and p.endswith(suffix)]

    # -- entry point ---------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        timeout: Any = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        **_: Any,
    ) -> FakeResponse:
        path = urlparse(url).path
        self.calls.append((method, path, json if json is not None else dict(params or {})))
        if not path.startswith("/v1/org-db"):
            return self.platform.handle(method, path, params or {}, json)
        return self._orgdb(method, path[len("/v1/org-db") :], params or {}, json)

    def _orgdb(self, method: str, path: str, params: Mapping[str, Any], body: Any) -> FakeResponse:
        if path == "/provision" and method == "POST":
            return FakeResponse(200, {"db_id": "db1", "status": "ready", "deployment": "container"})
        if path == "/tables" and method == "GET":
            items = list(self.tables.values())
            search = params.get("search")
            if search:
                items = [t for t in items if search in t["table_name"]]
            size = int(params.get("page_size", 20))
            page = int(params.get("page", 1))
            chunk = items[(page - 1) * size : page * size]
            return FakeResponse(
                200,
                {
                    "items": [{"id": t["id"], "table_name": t["table_name"]} for t in chunk],
                    "total": len(items),
                    "page": page,
                    "page_size": size,
                },
            )
        if path == "/tables" and method == "POST":
            name = body["table_name"]
            if any(t["table_name"] == name for t in self.tables.values()):
                return FakeResponse(409, {"detail": "table exists"})
            tid = self._id()
            self.tables[tid] = {
                "id": tid,
                "table_name": name,
                "column_schema": list(body["columns"]),
                "workstreams": [],
            }
            self.rows[tid] = []
            return FakeResponse(201, {"id": tid, "table_name": name})
        m = re.fullmatch(r"/tables/([^/]+)", path)
        if m:
            tid = m.group(1)
            if tid not in self.tables:
                return FakeResponse(404, {"detail": "Table not found"})
            if method == "GET":
                t = dict(self.tables[tid])
                t["workstreams"] = [
                    {"workstream_id": w, "permission_mode": pm}
                    for w, pm in self.workstreams.get(tid, {}).items()
                ]
                return FakeResponse(200, t)
            if method == "DELETE":
                if (body or {}).get("confirmation") != self.tables[tid]["table_name"]:
                    return FakeResponse(400, {"detail": "confirmation mismatch"})
                del self.tables[tid]
                self.rows.pop(tid, None)
                return FakeResponse(200, {"deleted": True})
        m = re.fullmatch(r"/tables/([^/]+)/schema", path)
        if m and method == "PATCH":
            tid = m.group(1)
            for op in body["operations"]:
                assert len(body["operations"]) <= 20
                self.tables[tid]["column_schema"].append(op["column"])
            return FakeResponse(200, {"applied": len(body["operations"])})
        m = re.fullmatch(r"/tables/([^/]+)/workstreams", path)
        if m and method == "POST":
            self.workstreams.setdefault(m.group(1), {})[body["workstream_id"]] = body[
                "permission_mode"
            ]
            return FakeResponse(200, {"id": "l", **body})
        m = re.fullmatch(r"/tables/([^/]+)/workstreams/([^/]+)", path)
        if m and method == "DELETE":
            self.workstreams.get(m.group(1), {}).pop(m.group(2), None)
            return FakeResponse(204)
        m = re.fullmatch(r"/tables/([^/]+)/rows", path)
        if m:
            return self._rows(method, m.group(1), params, body)
        m = re.fullmatch(r"/tables/([^/]+)/rows/([^/]+)", path)
        if m:
            tid, rid = m.groups()
            row = next((r for r in self.rows.get(tid, []) if r["id"] == rid), None)
            if row is None:
                return FakeResponse(404, {"detail": "row"})
            if method == "PATCH":
                row.update(body["data"])
                return FakeResponse(200, row)
            if method == "DELETE":
                self.rows[tid].remove(row)
                return FakeResponse(200, {"deleted": 1})
        if path == "/sql" and method == "POST":
            self.last_sql = body["sql"]
            if not re.match(r"\s*(select|with)\b", body["sql"], re.I) or ";" in body[
                "sql"
            ].strip().rstrip(";"):
                return FakeResponse(400, {"detail": "only a single SELECT is allowed"})
            return FakeResponse(
                200,
                {"columns": ["n"], "rows": [[len(self.rows)]], "row_count": 1, "duration_ms": 1},
            )
        return FakeResponse(404, {"detail": f"unrouted org-db {method} {path}"})

    def _rows(self, method: str, tid: str, params: Mapping[str, Any], body: Any) -> FakeResponse:
        if tid not in self.tables:
            return FakeResponse(404, {"detail": "Table not found"})
        if method == "GET":
            items = list(self.rows[tid])
            col, val = params.get("filter_col"), params.get("filter_val")
            if col:
                items = [r for r in items if str(r.get(col)) == val]
            size, page = int(params.get("page_size", 50)), int(params.get("page", 1))
            if size > 500:
                return FakeResponse(422, {"detail": "page_size"})
            return FakeResponse(
                200,
                {
                    "items": items[(page - 1) * size : page * size],
                    "total": len(items),
                    "page": page,
                    "page_size": size,
                },
            )
        rows = body["rows"]
        if not 1 <= len(rows) <= 500:
            return FakeResponse(422, {"detail": "rows must be 1..500"})
        self.insert_calls += 1
        forced = self.fail.get((tid, self.insert_calls))
        if forced:
            return FakeResponse(forced, {"detail": "forced failure"})
        uniq = self._unique_cols(tid)
        inserted = 0
        for row in rows:  # row by row, no transaction
            for col in uniq:
                if any(r.get(col) == row.get(col) for r in self.rows[tid]):
                    return FakeResponse(409, {"detail": f"duplicate {col}={row.get(col)!r}"})
            self.rows[tid].append({"id": f"r{len(self.rows[tid]) + 1}-{self._next}", **row})
            self._next += 1
            inserted += 1
        return FakeResponse(201, {"inserted": inserted})


class MemoryStore:
    """RowStore in memory; validates like OrgDBClient.insert_rows; unique key -> duplicate."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.fail_inserts: set[str] = set()

    def insert_rows(
        self, table: str, rows: Sequence[Mapping[str, Any]], *, validate: bool = True
    ) -> InsertResult:
        spec = get_spec(table) if table.startswith("bld_") else None
        res = InsertResult()
        for i, row in enumerate(rows):
            if table in self.fail_inserts:
                res.failed.append({"index": i, "error": "boom", "status": 500})
                continue
            if spec is not None and validate:
                from cli.builder_common.tables import validate_row

                validate_row(spec, row)
            existing = self.tables.setdefault(table, [])
            key = spec.unique_key if spec else None
            if key and any(r.get(key) == row.get(key) for r in existing):
                res.duplicates += 1
                continue
            existing.append({"id": f"{table}-{len(existing) + 1}", **dict(row)})
            res.inserted += 1
        return res

    def find_rows(self, table: str, column: str, value: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.tables.get(table, []) if str(r.get(column)) == str(value)]

    def update_row(self, table: str, key_column: str, key: str, data: Mapping[str, Any]) -> Any:
        for r in self.tables.get(table, []):
            if str(r.get(key_column)) == str(key):
                r.update(data)
                return dict(r)
        raise KeyError(key)

    def list_all(self, table: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.tables.get(table, [])]
