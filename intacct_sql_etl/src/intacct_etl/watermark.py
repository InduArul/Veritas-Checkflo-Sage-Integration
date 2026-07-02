"""Delta watermark control table (specs/13 §13.3).

A per-`(environment, object)` datetime "bookmark" recording the last successfully processed
`WHENMODIFIED` cutoff. `get_watermark` reads it; `set_watermark` upserts it; `create_table_sql`
returns idempotent DDL. The table name comes from config and is validated (`safe_identifier`);
all values are bound parameters (never interpolated). The connection is injected (any DB-API-like
object exposing `cursor()`/`commit()`); pyodbc is never imported here so unit tests need no driver.

The watermark is advanced ONLY by the caller after a fully successful, committed load (specs/13
§13.3/§13.11) — this module does not decide when to advance, only how to read/write it.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Callable, Optional

from .config import safe_identifier


def create_table_sql(table: str) -> str:
    """Idempotent CREATE for the watermark control table (SQL Server)."""
    t = safe_identifier(table)
    return (
        f"IF OBJECT_ID('{t}', 'U') IS NULL\n"
        f"CREATE TABLE {t} (\n"
        "    environment  VARCHAR(100) NOT NULL,\n"
        "    object       VARCHAR(20)  NOT NULL,\n"
        "    watermark    DATETIME2    NOT NULL,\n"
        "    updated_at   DATETIME2    NOT NULL,\n"
        "    CONSTRAINT PK_{pk} PRIMARY KEY (environment, object)\n".format(
            pk=t.replace("[", "").replace("]", "").replace(".", "_")
        )
        + ");"
    )


def get_watermark(conn, table: str, environment: str, object_name: str) -> Optional[datetime]:
    """Return the stored watermark for `(environment, object)`, or None if not yet set."""
    t = safe_identifier(table)
    with closing(conn.cursor()) as cur:
        cur.execute(
            f"SELECT watermark FROM {t} WHERE environment = ? AND object = ?",
            (environment, object_name),
        )
        row = cur.fetchone()
    return row[0] if row else None


def set_watermark(conn, table: str, environment: str, object_name: str, value: datetime,
                  *, commit: bool = True, now: Optional[datetime] = None,
                  clock: Callable[[], datetime] = datetime.now) -> None:
    """Upsert the watermark for `(environment, object)` to `value`.

    Uses the update-then-insert-if-absent T-SQL idiom (single batch) so it works without a
    pre-existing row and does not depend on MERGE. `now` (or `clock()`) stamps `updated_at`.
    """
    t = safe_identifier(table)
    stamp = now if now is not None else clock()
    sql = (
        f"UPDATE {t} SET watermark = ?, updated_at = ? "
        "WHERE environment = ? AND object = ?; "
        f"IF @@ROWCOUNT = 0 "
        f"INSERT INTO {t} (environment, object, watermark, updated_at) "
        "VALUES (?, ?, ?, ?);"
    )
    params = (value, stamp, environment, object_name,
              environment, object_name, value, stamp)
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
    if commit:
        conn.commit()
