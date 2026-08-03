from __future__ import annotations

import os
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from hermes_cursor_sdk.store import StateStore


def table_names(store: StateStore) -> set[str]:
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_create_schema_and_dir_mode(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")

    assert store.path.exists()
    assert {"schema_version", "agents", "runs", "sessions", "idempotency"} <= table_names(store)
    assert stat.S_IMODE(store.store_dir.stat().st_mode) == 0o700


def test_upsert_agent_run_session_and_idempotency(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")

    agent = store.upsert_agent(
        "agent-1",
        runtime="cloud",
        repos=[{"id": "repo-1"}],
        model={"id": "composer-2.5"},
        auto_create_pr=True,
    )
    run = store.upsert_run(
        "run-1",
        agent_id="agent-1",
        status="running",
        usage={"input_tokens": 1},
    )
    session = store.set_session("session-1", agent_id="agent-1", cwd=tmp_path)
    idem = store.put_idempotency(
        "operation-1",
        agent_id="agent-1",
        run_id="run-1",
        payload={"ok": True},
    )

    assert agent["repos"] == [{"id": "repo-1"}]
    assert agent["auto_create_pr"] is True
    assert run["usage"] == {"input_tokens": 1}
    assert session["agent_id"] == "agent-1"
    assert idem["payload"] == {"ok": True}


def test_upserts_are_idempotent_and_update_rows(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.upsert_agent("agent-1", runtime="local", cwd="/old")
    store.upsert_agent("agent-1", runtime="local", cwd="/new")
    store.upsert_run("run-1", agent_id="agent-1", status="running")
    store.upsert_run("run-1", agent_id="agent-1", status="finished", usage={"total_tokens": 2})
    store.set_session("session-1", agent_id="agent-1", cwd="/old")
    store.set_session("session-1", agent_id="agent-1", cwd="/new")

    assert store.get_agent("agent-1")["cwd"] == "/new"  # type: ignore[index]
    assert store.get_run("run-1")["status"] == "finished"  # type: ignore[index]
    assert store.get_run("run-1")["usage"] == {"total_tokens": 2}  # type: ignore[index]
    assert store.get_session("session-1")["cwd"] == "/new"  # type: ignore[index]
    assert len(store.list_runs("agent-1")) == 1


def test_save_run_text_updates_path_and_permissions(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.upsert_agent("agent-1", runtime="local", cwd=tmp_path)
    store.upsert_run("run-1", agent_id="agent-1", status="finished")

    result_path = store.save_run_text("run-1", "hello")

    assert result_path.read_text(encoding="utf-8") == "hello"
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o600
    assert store.get_run("run-1")["result_path"] == str(result_path)  # type: ignore[index]


@pytest.mark.slow
def test_concurrent_writers(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    errors: list[Exception] = []

    def writer(prefix: str) -> None:
        try:
            for index in range(25):
                agent_id = f"{prefix}-agent-{index}"
                run_id = f"{prefix}-run-{index}"
                store.upsert_agent(agent_id, runtime="local", cwd=tmp_path)
                store.upsert_run(run_id, agent_id=agent_id, status="finished")
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(prefix,)) for prefix in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(store.list_runs(limit=100)) == 50


def test_stale_get_returns_none(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")

    assert store.get_agent("missing") is None
    assert store.get_run("missing") is None
    assert store.get_session("missing") is None
    assert store.get_idempotency("missing") is None


def test_existing_dir_permissions_are_repaired(tmp_path: Path) -> None:
    store_dir = tmp_path / "state"
    store_dir.mkdir()
    os.chmod(store_dir, 0o755)

    StateStore(store_dir)

    assert stat.S_IMODE(store_dir.stat().st_mode) == 0o700
