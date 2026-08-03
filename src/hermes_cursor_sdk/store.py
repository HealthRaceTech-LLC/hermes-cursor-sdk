"""SQLite state store for Cursor SDK agents, runs, and sessions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class StateStore:
    """Small connection-per-call SQLite store."""

    def __init__(self, store_dir: str | Path) -> None:
        self.store_dir = Path(store_dir).expanduser().resolve()
        self.path = self.store_dir / "state.sqlite3"
        self._lock = threading.RLock()
        self.ensure_dir_permissions()
        self._migrate()

    def ensure_dir_permissions(self, mode: int = 0o700) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.store_dir, mode)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    runtime TEXT NOT NULL,
                    cwd TEXT,
                    repos_json TEXT,
                    model_json TEXT,
                    auto_create_pr INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    result_path TEXT,
                    usage_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_key TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                    cwd TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS idempotency (
                    operation_key TEXT PRIMARY KEY,
                    agent_id TEXT,
                    run_id TEXT,
                    payload_json TEXT,
                    created_at REAL NOT NULL
                );
                """
            )
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            if version is None:
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES(?, ?)",
                    (1, time.time()),
                )

    @staticmethod
    def _json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _loads(value: str | None) -> Any:
        if not value:
            return None
        return json.loads(value)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if "repos_json" in result:
            result["repos"] = StateStore._loads(result.pop("repos_json"))
        if "model_json" in result:
            result["model"] = StateStore._loads(result.pop("model_json"))
        if "usage_json" in result:
            result["usage"] = StateStore._loads(result.pop("usage_json"))
        if "payload_json" in result:
            result["payload"] = StateStore._loads(result.pop("payload_json"))
        if "auto_create_pr" in result:
            result["auto_create_pr"] = bool(result["auto_create_pr"])
        return result

    def upsert_agent(
        self,
        agent_id: str,
        *,
        runtime: str,
        cwd: str | Path | None = None,
        repos: Any | None = None,
        model: Any | None = None,
        auto_create_pr: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agents(
                    agent_id,
                    runtime,
                    cwd,
                    repos_json,
                    model_json,
                    auto_create_pr,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    runtime=excluded.runtime,
                    cwd=excluded.cwd,
                    repos_json=excluded.repos_json,
                    model_json=excluded.model_json,
                    auto_create_pr=excluded.auto_create_pr,
                    updated_at=excluded.updated_at
                """,
                (
                    agent_id,
                    runtime,
                    str(cwd) if cwd is not None else None,
                    self._json(repos),
                    self._json(model),
                    int(auto_create_pr),
                    now,
                    now,
                ),
            )
        return self.get_agent(agent_id) or {}

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return self._row(row)

    def upsert_run(
        self,
        run_id: str,
        *,
        agent_id: str,
        status: str,
        result_path: str | Path | None = None,
        usage: Any | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                    run_id,
                    agent_id,
                    status,
                    result_path,
                    usage_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    status=excluded.status,
                    result_path=COALESCE(excluded.result_path, runs.result_path),
                    usage_json=COALESCE(excluded.usage_json, runs.usage_json),
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    agent_id,
                    status,
                    str(result_path) if result_path is not None else None,
                    self._json(usage),
                    now,
                    now,
                ),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row(row)

    def list_runs(self, agent_id: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        args: list[Any] = []
        if agent_id:
            sql += " WHERE agent_id=?"
            args.append(agent_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [row for sqlite_row in rows if (row := self._row(sqlite_row)) is not None]

    def set_session(
        self, session_key: str, *, agent_id: str, cwd: str | Path | None = None
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(session_key, agent_id, cwd, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    cwd=excluded.cwd,
                    updated_at=excluded.updated_at
                """,
                (session_key, agent_id, str(cwd) if cwd is not None else None, now, now),
            )
        return self.get_session(session_key) or {}

    def get_session(self, session_key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_key=?", (session_key,)
            ).fetchone()
        return self._row(row)

    def delete_session(self, session_key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_key=?", (session_key,))

    def save_run_text(self, run_id: str, text: str) -> Path:
        runs_dir = self.store_dir / "runs"
        runs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = runs_dir / f"{run_id}.txt"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE runs SET result_path=?, updated_at=? WHERE run_id=?",
                (str(path), time.time(), run_id),
            )
        return path

    def get_idempotency(self, operation_key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency WHERE operation_key=?", (operation_key,)
            ).fetchone()
        return self._row(row)

    def put_idempotency(
        self,
        operation_key: str,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        payload: Any | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO idempotency(operation_key, agent_id, run_id, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(operation_key) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    run_id=excluded.run_id,
                    payload_json=excluded.payload_json
                """,
                (operation_key, agent_id, run_id, self._json(payload), time.time()),
            )
        return self.get_idempotency(operation_key) or {}


class NullResultStore:  # pragma: no cover - legacy compatibility store
    """Compatibility store implementation that intentionally drops results."""

    def save(self, result: Any) -> None:
        _ = result


class JsonlResultStore:  # pragma: no cover - legacy compatibility store
    """Compatibility JSON Lines store for legacy plugin paths."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, result: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            result
            if isinstance(result, dict)
            else getattr(result, "__dict__", {"result": str(result)})
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")


def iter_store_rows(
    rows: Iterable[sqlite3.Row],
) -> list[dict[str, Any]]:  # pragma: no cover - legacy compatibility helper
    return [dict(row) for row in rows]
