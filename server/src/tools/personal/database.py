"""Free local SQLite tools constrained to Trajecta virtual paths."""

from __future__ import annotations

import sqlite3
from typing import Any

from server.src.tools.personal.documents import VirtualPathResolver


class DatabaseTools:
    def __init__(self, workspace_root: str, uploads_root: str) -> None:
        self._paths = VirtualPathResolver(workspace_root, uploads_root)

    def sqlite_query(
        self,
        database_path: str,
        sql: str,
        parameters: list[Any] | None = None,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        path = self._paths.resolve(database_path)
        if not path.exists():
            raise ValueError(f"Database does not exist: {database_path}")
        # SQLite URI mode=ro prevents writes even when a query tries them.
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql, tuple(parameters or []))
            columns = [item[0] for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchmany(max(1, min(limit, 5_000)))]
            return {"columns": columns, "rows": rows, "count": len(rows)}
        finally:
            conn.close()

    def sqlite_execute(
        self,
        database_path: str,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> dict[str, Any]:
        path = self._paths.resolve(database_path, writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            cursor = conn.execute(sql, tuple(parameters or []))
            conn.commit()
            return {"rowcount": cursor.rowcount, "lastrowid": cursor.lastrowid}
        finally:
            conn.close()

    def sqlite_schema(self, database_path: str) -> list[dict[str, Any]]:
        return self.sqlite_query(
            database_path,
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table','view','index') ORDER BY type, name",
            limit=2_000,
        )["rows"]
