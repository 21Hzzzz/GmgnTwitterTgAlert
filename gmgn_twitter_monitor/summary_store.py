import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from typing import Any


class SummaryStore:
    """SQLite storage for per-Telegram-target AI summaries."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def init(self) -> None:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summary_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    internal_id TEXT,
                    tweet_id TEXT,
                    author_handle TEXT,
                    action TEXT,
                    received_at INTEGER NOT NULL,
                    tweet_timestamp INTEGER,
                    content_text TEXT,
                    reference_text TEXT,
                    message_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_messages_unique_internal
                ON summary_messages(group_key, chat_id, internal_id)
                WHERE internal_id IS NOT NULL AND internal_id != ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_summary_messages_window
                ON summary_messages(group_key, chat_id, received_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summary_runs (
                    group_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    last_run_at INTEGER NOT NULL,
                    window_start INTEGER,
                    window_end INTEGER,
                    status TEXT NOT NULL,
                    message_id INTEGER,
                    error TEXT,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (group_key, chat_id)
                )
                """
            )
            self._ensure_summary_runs_column(conn, "last_message_id", "INTEGER NOT NULL DEFAULT 0")

    def insert_message(
        self,
        group_key: str,
        chat_id: str,
        message: dict[str, Any],
        received_at: int | None = None,
    ) -> bool:
        received_at = received_at if received_at is not None else int(time.time())
        author = message.get("author") or {}
        content = message.get("content") or {}
        reference = message.get("reference") or {}

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO summary_messages (
                    group_key,
                    chat_id,
                    internal_id,
                    tweet_id,
                    author_handle,
                    action,
                    received_at,
                    tweet_timestamp,
                    content_text,
                    reference_text,
                    message_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_key,
                    chat_id,
                    message.get("internal_id"),
                    message.get("tweet_id"),
                    author.get("handle"),
                    message.get("action"),
                    received_at,
                    message.get("timestamp"),
                    content.get("text"),
                    reference.get("text"),
                    json.dumps(message, ensure_ascii=False),
                ),
            )
            return cursor.rowcount > 0

    def fetch_messages(
        self,
        group_key: str,
        chat_id: str,
        since_ts: int,
        until_ts: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    group_key,
                    chat_id,
                    internal_id,
                    tweet_id,
                    author_handle,
                    action,
                    received_at,
                    tweet_timestamp,
                    content_text,
                    reference_text,
                    message_json
                FROM summary_messages
                WHERE group_key = ?
                  AND chat_id = ?
                  AND received_at >= ?
                  AND received_at <= ?
                ORDER BY received_at ASC, id ASC
                """,
                (group_key, chat_id, since_ts, until_ts),
            ).fetchall()
            return [dict(row) for row in rows]

    def fetch_messages_by_id_range(
        self,
        group_key: str,
        chat_id: str,
        after_id: int,
        until_id: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    group_key,
                    chat_id,
                    internal_id,
                    tweet_id,
                    author_handle,
                    action,
                    received_at,
                    tweet_timestamp,
                    content_text,
                    reference_text,
                    message_json
                FROM summary_messages
                WHERE group_key = ?
                  AND chat_id = ?
                  AND id > ?
                  AND id <= ?
                ORDER BY id ASC
                """,
                (group_key, chat_id, after_id, until_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_max_message_id(self, group_key: str, chat_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(id), 0) AS max_id
                FROM summary_messages
                WHERE group_key = ? AND chat_id = ?
                """,
                (group_key, chat_id),
            ).fetchone()
            return int(row["max_id"]) if row else 0

    def get_last_run_at(self, group_key: str, chat_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT last_run_at
                FROM summary_runs
                WHERE group_key = ? AND chat_id = ?
                """,
                (group_key, chat_id),
            ).fetchone()
            return int(row["last_run_at"]) if row else None

    def get_last_message_id(self, group_key: str, chat_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT last_message_id
                FROM summary_runs
                WHERE group_key = ? AND chat_id = ?
                """,
                (group_key, chat_id),
            ).fetchone()
            return int(row["last_message_id"]) if row else 0

    def get_run(self, group_key: str, chat_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    group_key,
                    chat_id,
                    last_run_at,
                    window_start,
                    window_end,
                    status,
                    message_id,
                    error,
                    last_message_id
                FROM summary_runs
                WHERE group_key = ? AND chat_id = ?
                """,
                (group_key, chat_id),
            ).fetchone()
            return dict(row) if row else None

    def record_run(
        self,
        group_key: str,
        chat_id: str,
        *,
        last_run_at: int,
        window_start: int,
        window_end: int,
        status: str,
        message_id: int | None = None,
        error: str | None = None,
        last_message_id: int | None = None,
    ) -> None:
        previous_last_message_id = self.get_last_message_id(group_key, chat_id)
        next_last_message_id = (
            last_message_id if last_message_id is not None else previous_last_message_id
        )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO summary_runs (
                    group_key,
                    chat_id,
                    last_run_at,
                    window_start,
                    window_end,
                    status,
                    message_id,
                    error,
                    last_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_key, chat_id) DO UPDATE SET
                    last_run_at = excluded.last_run_at,
                    window_start = excluded.window_start,
                    window_end = excluded.window_end,
                    status = excluded.status,
                    message_id = excluded.message_id,
                    error = excluded.error,
                    last_message_id = excluded.last_message_id
                """,
                (
                    group_key,
                    chat_id,
                    last_run_at,
                    window_start,
                    window_end,
                    status,
                    message_id,
                    error,
                    next_last_message_id,
                ),
            )

    @staticmethod
    def _ensure_summary_runs_column(
        conn: sqlite3.Connection,
        column_name: str,
        column_sql: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(summary_runs)").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE summary_runs ADD COLUMN {column_name} {column_sql}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
