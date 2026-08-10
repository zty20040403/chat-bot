from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, TypeAlias, Union, overload

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool, PoolTimeout


class DatabaseError(RuntimeError):
    """A storage operation failed without exposing credentials in its message."""


class StoreRow(Mapping[str, Any]):
    """Row compatible with both sqlite3.Row access styles."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._positions = {name: index for index, name in enumerate(self._columns)}

    @overload
    def __getitem__(self, key: str) -> Any: ...

    @overload
    def __getitem__(self, key: int) -> Any: ...

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._positions[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self) -> tuple[str, ...]:
        return self._columns


class StoreCursor(Protocol):
    rowcount: int

    @property
    def lastrowid(self) -> int | None: ...

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> StoreCursor: ...

    def executescript(self, script: str) -> StoreCursor: ...

    def fetchone(self) -> StoreRow | sqlite3.Row | None: ...

    def fetchall(self) -> list[StoreRow] | list[sqlite3.Row]: ...

    def close(self) -> None: ...


class StoreConnection(Protocol):
    dialect: str

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> StoreCursor: ...

    def cursor(self) -> StoreCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


DatabaseSource: TypeAlias = Union[str, Path, "PostgresDatabase"]


_IDENTITY_COLUMNS = {
    "conversations": "conversation_id",
    "principals": "principal_id",
    "principal_identities": "identity_id",
    "messages": "canonical_message_id",
    "embeddings": "embedding_id",
    "context_compartments": "compartment_id",
    "reminders": "reminder_id",
    "deliveries": "delivery_id",
    "delivery_attempts": "attempt_id",
    "usage_events": "usage_id",
    "bridge_sources": "source_id",
    "agent_turns": "turn_id",
    "turn_journal_events": "event_id",
    "turn_edges": "edge_id",
}


class PostgresDatabase:
    """One resilient connection pool shared by every persistent bot store."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "qq_bot",
        min_size: int = 1,
        max_size: int = 10,
        timeout_seconds: float = 10.0,
        application_name: str = "qq-deepseek-bot",
    ) -> None:
        self.dsn = dsn.strip()
        if not self.dsn:
            raise DatabaseError("AI_POSTGRES_DSN is required")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise DatabaseError("AI_POSTGRES_SCHEMA is not a valid identifier")
        self.schema = schema
        self._closed = False
        self._pool = ConnectionPool(
            conninfo=self.dsn,
            min_size=max(int(min_size), 1),
            max_size=max(int(max_size), max(int(min_size), 1)),
            timeout=max(float(timeout_seconds), 1.0),
            kwargs={"application_name": application_name},
            configure=self._configure_connection,
            check=ConnectionPool.check_connection,
            open=True,
        )
        try:
            self._pool.wait(timeout=max(float(timeout_seconds), 1.0))
        except (psycopg.Error, PoolTimeout, TimeoutError) as exc:
            self._pool.close()
            raise DatabaseError("PostgreSQL is unavailable") from exc

    def _configure_connection(self, connection: psycopg.Connection[Any]) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self.schema)
                    )
                )
                cursor.execute("SET TIME ZONE 'UTC'")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def store_connection(self) -> StoreConnection:
        if self._closed:
            raise DatabaseError("PostgreSQL pool is closed")
        return _PostgresStoreConnection(self._pool)

    def healthcheck(self) -> None:
        try:
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        except (psycopg.Error, PoolTimeout) as exc:
            raise DatabaseError("PostgreSQL health check failed") from exc

    def require_revision(self, expected_revision: str) -> None:
        statement = sql.SQL(
            "SELECT version_num FROM {}.alembic_version"
        ).format(sql.Identifier(self.schema))
        try:
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                    row = cursor.fetchone()
        except (psycopg.Error, PoolTimeout) as exc:
            raise DatabaseError(
                "PostgreSQL schema is missing; run the Alembic upgrade first"
            ) from exc
        current = str(row[0]) if row is not None else ""
        if current != expected_revision:
            raise DatabaseError(
                "PostgreSQL schema revision does not match this bot build: "
                f"expected {expected_revision}, got {current or 'none'}"
            )

    def close(self) -> None:
        if self._closed:
            return
        self._pool.close()
        self._closed = True


class _PostgresStoreConnection:
    dialect = "postgresql"

    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self._pool = pool
        self._lock = threading.RLock()
        self._active: _LivePostgresCursor | None = None
        self._active_context: Any = None
        self._closed = False

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> StoreCursor:
        self._ensure_open()
        try:
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    live = _LivePostgresCursor(cursor)
                    live.execute(query, parameters)
                    rows = live._remaining_rows()
                    return _BufferedCursor(
                        rows,
                        rowcount=live.rowcount,
                        lastrowid=live.lastrowid,
                    )
        except (psycopg.Error, PoolTimeout) as exc:
            raise DatabaseError("PostgreSQL query failed") from exc

    def cursor(self) -> StoreCursor:
        self._ensure_open()
        with self._lock:
            if self._active is not None:
                raise DatabaseError("nested store transactions are not supported")
            context = self._pool.connection()
            try:
                connection = context.__enter__()
                cursor = _LivePostgresCursor(connection.cursor())
            except (psycopg.Error, PoolTimeout) as exc:
                context.__exit__(type(exc), exc, exc.__traceback__)
                raise DatabaseError("PostgreSQL transaction could not start") from exc
            self._active = cursor
            self._active_context = context
            return cursor

    def commit(self) -> None:
        self._release(commit=True)

    def rollback(self) -> None:
        self._release(commit=False)

    def close(self) -> None:
        with self._lock:
            if self._active is not None:
                self._release(commit=False)
            self._closed = True

    def _release(self, *, commit: bool) -> None:
        with self._lock:
            cursor = self._active
            context = self._active_context
            if cursor is None or context is None:
                return
            try:
                connection = cursor.connection
                if commit:
                    connection.commit()
                else:
                    connection.rollback()
            except psycopg.Error as exc:
                raise DatabaseError("PostgreSQL transaction failed") from exc
            finally:
                cursor.close()
                self._active = None
                self._active_context = None
                context.__exit__(None, None, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise DatabaseError("store connection is closed")


class _LivePostgresCursor:
    def __init__(self, cursor: psycopg.Cursor[Any]) -> None:
        self._cursor = cursor
        self._lastrowid: int | None = None
        self.rowcount = -1

    @property
    def connection(self) -> psycopg.Connection[Any]:
        return self._cursor.connection

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> StoreCursor:
        translated, identity_column = _translate_sql(query)
        try:
            self._cursor.execute(translated, tuple(parameters))
            self.rowcount = self._cursor.rowcount
            self._lastrowid = None
            if identity_column is not None and self._cursor.description is not None:
                row = self._cursor.fetchone()
                if row is not None:
                    self._lastrowid = int(row[0])
            return self
        except psycopg.Error as exc:
            raise DatabaseError("PostgreSQL query failed") from exc

    def executescript(self, script: str) -> StoreCursor:
        raise DatabaseError("schema changes must be applied through Alembic")

    def fetchone(self) -> StoreRow | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return _make_row(self._cursor, row)

    def fetchall(self) -> list[StoreRow]:
        return [_make_row(self._cursor, row) for row in self._cursor.fetchall()]

    def _remaining_rows(self) -> list[StoreRow]:
        if self._cursor.description is None:
            return []
        return self.fetchall()

    def close(self) -> None:
        if not self._cursor.closed:
            self._cursor.close()


class _BufferedCursor:
    def __init__(
        self,
        rows: Sequence[StoreRow],
        *,
        rowcount: int,
        lastrowid: int | None,
    ) -> None:
        self._rows = list(rows)
        self._position = 0
        self.rowcount = rowcount
        self._lastrowid = lastrowid

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> StoreCursor:
        raise DatabaseError("buffered cursors cannot execute another query")

    def executescript(self, script: str) -> StoreCursor:
        raise DatabaseError("buffered cursors cannot execute scripts")

    def fetchone(self) -> StoreRow | None:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> list[StoreRow]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)
        return rows

    def close(self) -> None:
        self._rows.clear()


def open_store_connection(
    source: DatabaseSource,
) -> tuple[Path | None, StoreConnection | sqlite3.Connection]:
    if isinstance(source, PostgresDatabase):
        return None, source.store_connection()

    path = Path(source) if str(source) != ":memory:" else None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(source),
        timeout=10.0,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return path, connection


def _make_row(cursor: psycopg.Cursor[Any], values: Sequence[Any]) -> StoreRow:
    description = cursor.description or ()
    return StoreRow([column.name for column in description], values)


def _translate_sql(query: str) -> tuple[str, str | None]:
    stripped = query.strip()
    if stripped.upper() == "BEGIN IMMEDIATE":
        return "BEGIN", None

    ignored_insert = bool(re.match(r"(?is)^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", query))
    translated = re.sub(
        r"(?is)^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
        "INSERT INTO ",
        query,
        count=1,
    )
    translated = _convert_qmark_placeholders(translated)
    translated = re.sub(
        r"(?is)\bLIMIT\s+-1\s+OFFSET\s+%s",
        "OFFSET %s",
        translated,
    )
    translated = translated.strip().rstrip(";")
    if ignored_insert and "ON CONFLICT" not in translated.upper():
        translated += " ON CONFLICT DO NOTHING"

    identity_column: str | None = None
    insert = re.match(
        r'(?is)^INSERT\s+INTO\s+(?:"?[A-Za-z_][A-Za-z0-9_]*"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?',
        translated,
    )
    if insert is not None and " RETURNING " not in translated.upper():
        identity_column = _IDENTITY_COLUMNS.get(insert.group(1).lower())
        if identity_column is not None:
            translated += f' RETURNING "{identity_column}"'
    return translated, identity_column


def _convert_qmark_placeholders(query: str) -> str:
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(query):
        character = query[index]
        if quote is not None:
            output.append(character)
            if character == quote:
                if index + 1 < len(query) and query[index + 1] == quote:
                    output.append(query[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            output.append(character)
        elif character == "?":
            output.append("%s")
        else:
            output.append(character)
        index += 1
    return "".join(output)
