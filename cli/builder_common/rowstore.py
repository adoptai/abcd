"""The ``RowStore`` every Builder command writes quality rows through, over the Data Store.

The library (runner, metrics, gaps, instructions, datasets) already emits rows in the ``bld_*``
column shape, so this class only validates and writes: ``tables.validate_row`` rejects an unknown
column, a bad enum or a malformed path or hash before anything leaves the machine, which is also
what keeps an answer value out of every row (a value has no column to sit in).
"""

from __future__ import annotations

from typing import Any

from cli.builder_common.errors import PlatformError, RefusedError
from cli.builder_common.tables import get_spec, validate_row


class OrgRowStore:
    """``evals.protocols.RowStore`` over a backend with ``insert_rows``, ``update_row`` and
    ``list_all`` (``OrgDBClient``, or ``MemoryStore`` in tests)."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def insert(self, table: str, row: dict[str, Any]) -> None:
        validate_row(table, row)
        res = self.backend.insert_rows(table, [row])
        failed = getattr(res, "failed", None)
        if failed:
            raise PlatformError(f"{table}: insert failed: {failed}")

    def update(self, table: str, key: str, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into the row whose unique key is ``key`` (insert it if it is missing)."""
        unique = get_spec(table).unique_key
        fields = {k: v for k, v in fields.items() if k != unique}
        if not fields:
            return
        validate_row(table, fields, partial=True)
        try:
            self.backend.update_row(table, unique, key, fields)
        except (KeyError, RefusedError):
            self.insert(table, {unique: key, **fields})

    def query(self, table: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self.backend.list_all(table)
        return [r for r in rows if all(r.get(k) == v for k, v in (where or {}).items())]
