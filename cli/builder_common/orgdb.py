"""Data Store (org-db) client: ``/v1/org-db/*`` through the adoptwebui BFF.

Routes re-verified against adoptwebui ``origin/dev``
``backend/app/routes/org_db.py`` and ``schemas/org_db.py`` and adoptai-workflows
``api/app/services/org_db/org_db_service.py``:

* ``POST   /v1/org-db/provision``                    body ``{deployment}`` (routes/org_db.py:135)
* ``GET    /v1/org-db/tables``                       query page, page_size<=100, search, status (:204)
* ``GET    /v1/org-db/tables/{id}``                  ``column_schema``, ``workstreams`` (:247)
* ``POST   /v1/org-db/tables``                       body TableCreateRequest, 201 (:271; schemas:87)
* ``DELETE /v1/org-db/tables/{id}``                  body ``{confirmation: <table_name>}`` (:314)
* ``PATCH  /v1/org-db/tables/{id}/schema``           body ``{operations:[add_column...]}`` (:336)
* ``GET    /v1/org-db/tables/{id}/rows``             query page, page_size<=500, filter_col/filter_val
  (exact equality, one column), sort_col, sort_dir (:365; workflows org_db_service.py:922-940)
* ``POST   /v1/org-db/tables/{id}/rows``             body ``{rows: [<=500]}`` -> ``{inserted: n}`` (:396;
  schemas:106). Rows are inserted ONE BY ONE with no transaction
  (org_db_service.py:1117-1175): a unique violation answers 409 after the
  earlier rows of the same call were already written.
* ``PATCH  /v1/org-db/tables/{id}/rows/{row_id}``    body ``{data: {...}}`` (:430)
* ``DELETE /v1/org-db/tables/{id}/rows/{row_id}``    (:453)
* ``POST   /v1/org-db/tables/{id}/workstreams``      body ``{workstream_id, permission_mode: read|write}`` (:618;
  re-linking soft-deletes then re-inserts: org_db_service in the BFF, services/org_db_service.py:306)
* ``POST   /v1/org-db/sql``                          body ``{sql, limit<=10000}``; SELECT / WITH...SELECT
  only, one statement (:501; org_db_sql_guard.py)

BFF gaps found while verifying (also in the report): ``TableCreateRequest`` has
no ``workstream_scoped`` field, so the plan's ``workstream_scoped = false`` is
silently dropped (pydantic ignores extras) and the workflows default (True)
applies; there is no upsert; the list route has no exact-name filter.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import requests

from cli.builder_common.errors import PlatformError, RefusedError
from cli.builder_common.tables import TableSpec, get_spec, validate_row
from cli.harness_common.api_client import HarnessAPIClient, HarnessAPIError

BASE = "/v1/org-db"
MAX_ROWS_PER_INSERT = 500  # schemas/org_db.py:107 (max_length=500)
LIST_PAGE_SIZE = 100  # routes/org_db.py:208 (le=100)
ROWS_PAGE_SIZE = 500  # routes/org_db.py:369 (le=500)
SCHEMA_OPS_MAX = 20  # schemas/org_db.py:103
# ``workstream_id`` is added by the platform to every workstream-scoped table (the default, and
# the BFF cannot turn it off); a spec column of that name makes the create 500 ("specified more
# than once", live 2026-10-29), so specs must not use it.
IMPLICIT_COLUMNS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "is_deleted",
        "source_id",
        "created_by",
        "updated_by",
        "workstream_id",
    }
)

# Failures that retrying row by row cannot fix.
_FATAL_STATUS = {401, 403, 404}


CREATED_SOURCE = "ai"  # the BFF accepts only ui, ai, wdl or migration


class OrgDBError(PlatformError):
    """The Data Store refused or failed a call."""


@dataclass
class InsertResult:
    """Outcome of an idempotent, chunked insert."""

    inserted: int = 0
    duplicates: int = 0  # unique-key 409: already written, counted as success
    failed: list[dict[str, Any]] = field(default_factory=list)  # {index, error, status}

    @property
    def ok(self) -> bool:
        return not self.failed

    def merge(self, other: InsertResult, offset: int = 0) -> None:
        self.inserted += other.inserted
        self.duplicates += other.duplicates
        for f in other.failed:
            self.failed.append({**f, "index": f["index"] + offset})

    def raise_for_failures(self) -> None:
        if self.failed:
            first = self.failed[0]
            raise OrgDBError(
                f"{len(self.failed)} row(s) not written (inserts are not atomic; "
                f"{self.inserted} written, {self.duplicates} already present). "
                f"First failure, row {first['index']}: {first['error']}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "failed": list(self.failed),
            "ok": self.ok,
        }


@dataclass
class EnsureResult:
    table_id: str
    created: bool
    added_columns: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


class OrgDBClient(HarnessAPIClient):
    """Data Store client. Extends the harness client (Bearer JWT, BFF only)."""

    def __init__(self, bearer_token: str, webui_endpoint: str) -> None:
        super().__init__(bearer_token, webui_endpoint)
        self._table_ids: dict[str, str] = {}

    @classmethod
    def from_client(cls, client: HarnessAPIClient) -> OrgDBClient:
        return cls(client._bearer_token, client.webui_endpoint)

    # -- provisioning and tables ------------------------------------------

    def provision(self, deployment: str = "container") -> dict[str, Any]:
        return self._request("POST", f"{BASE}/provision", json={"deployment": deployment})

    def list_tables(
        self, page: int = 1, page_size: int = LIST_PAGE_SIZE, search: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        return self._request("GET", f"{BASE}/tables", params=params)

    def iter_tables(self, search: str | None = None) -> Iterator[dict[str, Any]]:
        page = 1
        seen = 0
        while True:
            data = self.list_tables(page=page, page_size=LIST_PAGE_SIZE, search=search)
            items = data.get("items", []) if isinstance(data, dict) else []
            yield from items
            seen += len(items)
            if not items or len(items) < LIST_PAGE_SIZE or seen >= int(data.get("total", seen)):
                return
            page += 1

    def find_table(self, name: str) -> dict[str, Any] | None:
        """Exact-name lookup (the list route only has a fuzzy ``search``)."""
        for item in self.iter_tables(search=name):
            if item.get("table_name") == name:
                return item
        return None

    def get_table(self, table_id: str) -> dict[str, Any]:
        return self._request("GET", f"{BASE}/tables/{table_id}")

    def create_table(
        self, name: str, columns: Sequence[Mapping[str, Any]], description: str | None = None
    ) -> dict[str, Any]:
        # workstream_scoped is what the plan wants; today's BFF model drops it.
        # created_source is an enum on the real BFF: ui | ai | wdl | migration (live 422 otherwise).
        body = {
            "table_name": name,
            "description": description,
            "columns": list(columns),
            "created_source": CREATED_SOURCE,
            "workstream_scoped": False,
        }
        return self._request("POST", f"{BASE}/tables", json=body)

    def delete_table(self, table_id: str, table_name: str) -> dict[str, Any]:
        """``confirmation`` must equal the table name (org_db_service.py:841)."""
        self._table_ids.pop(table_name, None)
        return self._request(
            "DELETE", f"{BASE}/tables/{table_id}", json={"confirmation": table_name}
        )

    def add_columns(self, table_id: str, columns: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        cols = list(columns)
        for i in range(0, len(cols), SCHEMA_OPS_MAX):
            ops = [{"action": "add_column", "column": c} for c in cols[i : i + SCHEMA_OPS_MAX]]
            result = self._request(
                "PATCH", f"{BASE}/tables/{table_id}/schema", json={"operations": ops}
            )
        return result

    def link_table_workstream(
        self, table_id: str, workstream_id: str, permission_mode: str = "write"
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{BASE}/tables/{table_id}/workstreams",
            json={"workstream_id": workstream_id, "permission_mode": permission_mode},
        )

    def link_table_workstreams(self, table_id: str, workstream_ids: Sequence[str]) -> list[str]:
        """Link with write access, skipping workstreams already linked as write.

        Re-linking is safe but churns rows server-side, so it is avoided.
        """
        detail = self.get_table(table_id)
        have = {
            w.get("workstream_id"): w.get("permission_mode")
            for w in detail.get("workstreams", [])
            if isinstance(w, dict)
        }
        for ws in workstream_ids:
            if have.get(ws) != "write":
                self.link_table_workstream(table_id, ws, "write")
        return list(workstream_ids)

    def unlink_table_workstream(self, table_id: str, workstream_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"{BASE}/tables/{table_id}/workstreams/{workstream_id}")

    def table_id(self, name: str) -> str:
        """Resolve a table name to its id (cached)."""
        if name not in self._table_ids:
            found = self.find_table(name)
            if not found:
                raise OrgDBError(f"table {name!r} does not exist; run `abcd builder setup`")
            self._table_ids[name] = str(found["id"])
        return self._table_ids[name]

    def ensure_table(self, spec: TableSpec) -> EnsureResult:
        """Create the table, or verify an existing one and add missing columns.

        Additive only: missing columns are added; a column that exists with a
        different type or unique flag is reported in ``mismatches`` (nothing is
        altered), and extra columns are ignored.
        """
        found = self.find_table(spec.name)
        created = False
        if found is None:
            try:
                made = self.create_table(spec.name, spec.api_columns(), spec.purpose)
                table_id = str(made.get("id") or made.get("table_id") or "")
                created = True
            except HarnessAPIError as exc:
                if exc.status_code != 409:
                    raise
                found = self.find_table(spec.name)  # lost a race: verify instead
                if found is None:
                    raise
                table_id = str(found["id"])
            if not table_id:
                refetched = self.find_table(spec.name)
                table_id = str(refetched["id"]) if refetched else ""
            if not table_id:
                raise OrgDBError(f"created {spec.name!r} but could not resolve its id")
            self._table_ids[spec.name] = table_id
            return EnsureResult(table_id=table_id, created=True)
        table_id = str(found["id"])
        self._table_ids[spec.name] = table_id
        live = {c["name"]: c for c in self.get_table(table_id).get("column_schema", [])}
        missing: list[dict[str, Any]] = []
        mismatches: list[str] = []
        for col in spec.columns:
            api = col.api_dict()
            cur = live.get(col.name)
            if cur is None:
                if col.name == spec.unique_key:
                    mismatches.append(
                        f"{col.name}: unique key column missing (cannot be added to a populated table)"
                    )
                    continue
                api["unique"] = False
                api["nullable"] = True
                missing.append(api)
                continue
            if str(cur.get("type", "")).lower() != api["type"].lower():
                mismatches.append(f"{col.name}: type {cur.get('type')!r}, expected {api['type']!r}")
            if bool(cur.get("unique")) != bool(api["unique"]) and col.name == spec.unique_key:
                mismatches.append(f"{col.name}: unique flag is {cur.get('unique')!r}")
        if missing:
            self.add_columns(table_id, missing)
        return EnsureResult(
            table_id=table_id,
            created=created,
            added_columns=[m["name"] for m in missing],
            mismatches=mismatches,
        )

    # -- rows ---------------------------------------------------------------

    def insert_rows_raw(self, table_id: str, rows: Sequence[Mapping[str, Any]]) -> InsertResult:
        """Chunked, idempotent insert by table id.

        Each chunk (<=500 rows) is one POST. Because the server inserts row by
        row without a transaction, a failed chunk may be partly written, so any
        failure falls back to one POST per row of that chunk: a 409 on a row is
        "already written" (counted in ``duplicates``), other errors are reported
        per row in ``failed``. 401/403/404 raise at once. Never raises for
        row-level failures; call ``raise_for_failures``.
        """
        result = InsertResult()
        rows = list(rows)
        for start in range(0, len(rows), MAX_ROWS_PER_INSERT):
            chunk = rows[start : start + MAX_ROWS_PER_INSERT]
            try:
                resp = self._request("POST", f"{BASE}/tables/{table_id}/rows", json={"rows": chunk})
                n = resp.get("inserted") if isinstance(resp, dict) else None
                result.inserted += n if isinstance(n, int) else len(chunk)
                continue
            except HarnessAPIError as exc:
                if exc.status_code in _FATAL_STATUS:
                    raise OrgDBError(str(exc)) from exc
            except requests.RequestException:
                pass
            result.merge(self._insert_one_by_one(table_id, chunk), offset=start)
        return result

    def _insert_one_by_one(self, table_id: str, chunk: Sequence[Mapping[str, Any]]) -> InsertResult:
        result = InsertResult()
        for i, row in enumerate(chunk):
            try:
                self._request("POST", f"{BASE}/tables/{table_id}/rows", json={"rows": [row]})
                result.inserted += 1
            except HarnessAPIError as exc:
                if exc.status_code == 409:
                    result.duplicates += 1
                elif exc.status_code in _FATAL_STATUS:
                    raise OrgDBError(str(exc)) from exc
                else:
                    result.failed.append(
                        {"index": i, "error": exc.body[:300], "status": exc.status_code}
                    )
            except requests.RequestException as exc:
                result.failed.append({"index": i, "error": str(exc)[:300], "status": None})
        return result

    def insert_rows(
        self, table: str, rows: Sequence[Mapping[str, Any]], *, validate: bool = True
    ) -> InsertResult:
        """Insert into a Builder table by name; rows are validated first."""
        if validate:
            spec = get_spec(table)
            for row in rows:
                validate_row(spec, row)
        return self.insert_rows_raw(self.table_id(table), rows)

    def list_rows(
        self,
        table: str,
        *,
        page: int = 1,
        page_size: int = ROWS_PAGE_SIZE,
        filter_col: str | None = None,
        filter_val: str | None = None,
        sort_col: str | None = None,
        sort_dir: str = "asc",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size, "sort_dir": sort_dir}
        if filter_col:
            params["filter_col"] = filter_col
            params["filter_val"] = filter_val
        if sort_col:
            params["sort_col"] = sort_col
        return self._request("GET", f"{BASE}/tables/{self.table_id(table)}/rows", params=params)

    def find_rows(self, table: str, column: str, value: str) -> list[dict[str, Any]]:
        """All live rows where ``column`` equals ``value`` (exact match)."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.list_rows(table, page=page, filter_col=column, filter_val=str(value))
            items = data.get("items", [])
            out.extend(items)
            if len(items) < ROWS_PAGE_SIZE or len(out) >= int(data.get("total", len(out))):
                return out
            page += 1

    def list_all(self, table: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.list_rows(table, page=page)
            items = data.get("items", [])
            out.extend(items)
            if len(items) < ROWS_PAGE_SIZE or len(out) >= int(data.get("total", len(out))):
                return out
            page += 1

    def query_sql(self, sql: str, limit: int = 100) -> dict[str, Any]:
        """Read-only SQL (SELECT / WITH ... SELECT, one statement)."""
        return self._request("POST", f"{BASE}/sql", json={"sql": sql, "limit": limit})

    def update_row(
        self, table: str, key_column: str, key: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Patch the row whose ``key_column`` equals ``key`` (found by filter, then PATCH by id)."""
        spec = get_spec(table) if table.startswith("bld_") else None
        if spec is not None:
            validate_row(spec, data, partial=True)
        rows = self.find_rows(table, key_column, key)
        if not rows:
            raise RefusedError(f"{table}: no row with {key_column}={key!r}")
        row_id = rows[0]["id"]
        return self._request(
            "PATCH",
            f"{BASE}/tables/{self.table_id(table)}/rows/{row_id}",
            json={"data": dict(data)},
        )

    def delete_row(self, table: str, row_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"{BASE}/tables/{self.table_id(table)}/rows/{row_id}")
