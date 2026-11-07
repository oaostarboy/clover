"""Real SessionDB COMMIT-pending lock tests (private DELETE-mode database)."""
import sqlite3
import sys
import threading
import time

import pytest

from clover_state import SessionDB


def _bare_agent_for_stop():
    """Construct a bare AIAgent with the state used by public hard_interrupt."""
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    for name, value in {
        "_interrupt_requested": False,
        "_interrupt_message": None,
        "_hard_interrupt_requested": threading.Event(),
        "_execution_thread_id": None,
        "_interrupt_thread_signal_pending": False,
        "_active_children": [],
        "_active_children_lock": threading.Lock(),
        "quiet_mode": True,
        "provider": "openrouter",
        "model": "test/model",
        "_base_url": "http://localhost:1234",
    }.items():
        setattr(agent, name, value)
    return agent


def _delete_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db._conn.execute("PRAGMA journal_mode=DELETE")
    db._wal_active = False
    db._conn.execute("PRAGMA busy_timeout=1")
    db.create_session("writer", source="test")
    return db


def test_append_waits_in_commit_for_reader_release_without_replaying_body(tmp_path):
    db = _delete_db(tmp_path)
    db._TRANSCRIPT_WRITE_PATIENCE_S = 0.2
    reader = sqlite3.connect(
        db.db_path, isolation_level=None, timeout=0, check_same_thread=False
    )
    reader.execute("BEGIN")
    cursor = reader.execute("SELECT id FROM messages")
    cursor.fetchone()
    started = threading.Event()
    trace = []

    def record(sql):
        trace.append(sql)
        if sql == "COMMIT":
            started.set()

    db._conn.set_trace_callback(record)

    def release_reader():
        assert started.wait(2), "actual SessionDB write never reached COMMIT"
        time.sleep(0.04)
        reader.rollback()

    releaser = threading.Thread(target=release_reader)
    releaser.start()
    began = time.monotonic()
    try:
        db.append_message("writer", "user", "single attempt")
        elapsed = time.monotonic() - began
        assert elapsed < 0.25
        assert sum(sql == "BEGIN IMMEDIATE" for sql in trace) == 1
        assert db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content='single attempt'"
        ).fetchone()[0] == 1
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1
    finally:
        releaser.join(2)
        cursor.close()
        reader.close()
        db._conn.set_trace_callback(None)
        db.close()


def test_commit_timeout_exhaustion_restores_prior_policy_and_does_not_duplicate(tmp_path):
    db = _delete_db(tmp_path)
    db._conn.execute("PRAGMA busy_timeout=17")
    db._TRANSCRIPT_WRITE_PATIENCE_S = 0.04
    reader = sqlite3.connect(db.db_path, isolation_level=None, timeout=0)
    reader.execute("BEGIN")
    cursor = reader.execute("SELECT id FROM messages")
    cursor.fetchone()
    body_calls = 0

    def body(conn):
        nonlocal body_calls
        body_calls += 1
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ("writer", "user", "timeout"),
        )

    try:
        began = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="phase=commit"):
            db._execute_write(body, patience_s=0.04)
        elapsed = time.monotonic() - began
        assert elapsed < 0.25
        # SQLite's busy-handler behavior differs by bundled SQLite version:
        # a failed COMMIT may consume the whole deadline (one body attempt),
        # or return early enough for the established definite-BUSY retry
        # (two attempts). In either case the rolled-back insert is not durable.
        assert 1 <= body_calls <= 2
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 17
        assert db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content='timeout'"
        ).fetchone()[0] == 0
    finally:
        cursor.close()
        reader.close()
        db.close()


def test_ambiguous_post_commit_error_is_not_replayed_and_timeout_is_restored(tmp_path):
    db = _delete_db(tmp_path)
    db._conn.execute("PRAGMA busy_timeout=29")
    body_calls = 0
    real_conn = db._conn

    class CommitAcknowledgementLost:
        def __getattr__(self, name):
            return getattr(real_conn, name)

        def commit(self):
            real_conn.commit()
            raise sqlite3.OperationalError("interrupted after commit acknowledgment lost")

    db._conn = CommitAcknowledgementLost()

    def body(conn):
        nonlocal body_calls
        body_calls += 1
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ("writer", "user", "committed once"),
        )

    try:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            db._execute_write(body, patience_s=0.2)
        assert body_calls == 1
        assert real_conn.execute("PRAGMA busy_timeout").fetchone()[0] == 29
        assert real_conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content='committed once'"
        ).fetchone()[0] == 1
    finally:
        db._conn = real_conn
        db.close()


def test_100ms_commit_budget_bounds_stop_and_ui_response(tmp_path):
    db = _delete_db(tmp_path)
    db._TRANSCRIPT_WRITE_PATIENCE_S = 0.1
    reader = sqlite3.connect(
        db.db_path, isolation_level=None, timeout=0, check_same_thread=False
    )
    reader.execute("BEGIN")
    cursor = reader.execute("SELECT id FROM messages")
    cursor.fetchone()
    commit_started = threading.Event()
    stop_returned = threading.Event()
    ui_done = threading.Event()
    trace = []

    def record(sql):
        trace.append(sql)
        if sql == "COMMIT":
            commit_started.set()

    db._conn.set_trace_callback(record)
    write_errors = []

    def write():
        try:
            db.append_message("writer", "user", "stopped while blocked")
        except BaseException as exc:
            write_errors.append(exc)

    stop_agent = _bare_agent_for_stop()

    def stop_and_read():
        assert commit_started.wait(2)
        # Exercise the public AIAgent hard-stop path. It must acknowledge
        # promptly without waiting for SessionDB's write lock; the UI read may
        # queue behind SQLite, but remains bounded by the existing deadline.
        stop_agent.hard_interrupt("Stop requested")
        stop_returned.set()
        db.get_session("writer")  # UI-facing read queues only on SessionDB's lock.
        ui_done.set()

    writer_thread = threading.Thread(target=write)
    stop_thread = threading.Thread(target=stop_and_read)
    began = time.monotonic()
    try:
        writer_thread.start()
        stop_thread.start()
        writer_thread.join(0.22)
        stop_thread.join(0.22)
        elapsed = time.monotonic() - began
        assert not writer_thread.is_alive()
        assert not stop_thread.is_alive()
        assert len(write_errors) == 1
        assert isinstance(write_errors[0], sqlite3.OperationalError)
        assert "phase=commit" in str(write_errors[0]).lower()
        assert getattr(stop_agent, "_hard_interrupt_requested").is_set()
        assert stop_returned.is_set() and ui_done.is_set()
        assert elapsed < 0.25
        assert sum(sql == "BEGIN IMMEDIATE" for sql in trace) == 1
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1
        assert db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content='stopped while blocked'"
        ).fetchone()[0] == 0
    finally:
        reader.rollback()
        cursor.close()
        reader.close()
        db._conn.set_trace_callback(None)
        stop_agent.clear_interrupt()
        db.close()


def _rollback_pending_reader(reader, release, errors):
    try:
        reader.rollback()
    except BaseException as exc:
        errors.append(exc)
    finally:
        release.set()


def _cleanup_pending_fixture(events, threads, closers, *, active_error=None, join_timeout: float = 2.0):
    """Keep shared connections alive until workers stop; retain primary failures."""
    failures = []
    for event in events:
        event.set()
    for worker in threads:
        if worker.ident is not None:
            try:
                worker.join(join_timeout)
            except Exception as exc:
                failures.append(exc)
    if any(worker.is_alive() for worker in threads):
        failures.append(RuntimeError("pending fixture worker still alive; connections left open"))
    else:
        for close in closers:
            try:
                close()
            except Exception as exc:
                failures.append(exc)
    if failures:
        detail = "; ".join(str(exc) for exc in failures)
        if active_error is not None:
            active_error.add_note("Pending fixture cleanup: " + detail)
        else:
            raise RuntimeError("Pending fixture cleanup: " + detail) from failures[0]


def test_new_reader_retries_one_pending_lock_on_existing_read_path(tmp_path):
    db = _delete_db(tmp_path)
    db._TRANSCRIPT_WRITE_PATIENCE_S = 0.1
    reader = sqlite3.connect(
        db.db_path, isolation_level=None, timeout=0, check_same_thread=False
    )
    reader.execute("BEGIN")
    cursor = reader.execute("SELECT id FROM messages")
    cursor.fetchone()
    commit_started = threading.Event()
    first_busy = threading.Event()
    release = threading.Event()
    trace = []

    def record(sql):
        trace.append(sql)
        if sql == "COMMIT":
            commit_started.set()

    db._conn.set_trace_callback(record)
    observed = {}
    errors = []

    def new_reader():
        conn = None
        try:
            assert commit_started.wait(2), "writer did not reach COMMIT"
            if release.is_set():
                return
            conn = sqlite3.connect(db.db_path, isolation_level=None, timeout=0)
            # COMMIT's trace callback runs before SQLite acquires PENDING.
            # A separate real read must witness BUSY before the tested read.
            deadline = time.monotonic() + 0.08
            while True:
                try:
                    conn.execute("SELECT COUNT(*) FROM messages").fetchone()
                except sqlite3.OperationalError as exc:
                    if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                        raise
                    observed["probe_busy"] = exc.sqlite_errorcode
                    break
                assert time.monotonic() < deadline, "PENDING lock not observed"
                release.wait(0.001)

            attempts = 0

            def query():
                nonlocal attempts
                attempts += 1
                try:
                    return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                except sqlite3.OperationalError as exc:
                    if attempts == 1 and exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                        observed["first_busy"] = exc.sqlite_errorcode
                        first_busy.set()
                    raise

            observed["rows"] = db._retry_locked_read(query, patience_s=0.2)
            observed["attempts"] = attempts
        except BaseException as exc:
            errors.append(exc)
        finally:
            release.set()
            if conn is not None:
                conn.close()

    def release_reader():
        try:
            assert first_busy.wait(0.2), "tested read did not encounter SQLITE_BUSY"
        except BaseException as exc:
            errors.append(exc)
        finally:
            _rollback_pending_reader(reader, release, errors)

    new_thread = threading.Thread(target=new_reader)
    release_thread = threading.Thread(target=release_reader)
    began = time.monotonic()
    try:
        new_thread.start()
        release_thread.start()
        db.append_message("writer", "user", "read retry")
        new_thread.join(1)
        release_thread.join(1)
        assert not new_thread.is_alive()
        assert not release_thread.is_alive()
        assert not errors, errors
        assert observed["probe_busy"] == sqlite3.SQLITE_BUSY
        assert observed["first_busy"] == sqlite3.SQLITE_BUSY
        assert observed["attempts"] >= 2
        assert observed["rows"] == 1
        assert time.monotonic() - began < 0.25
        assert sum(sql == "BEGIN IMMEDIATE" for sql in trace) == 1
        assert db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content='read retry'"
        ).fetchone()[0] == 1
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1
    finally:
        _cleanup_pending_fixture(
            [commit_started, first_busy, release], [new_thread, release_thread],
            [cursor.close, reader.close, lambda: db._conn.set_trace_callback(None), db.close],
            active_error=sys.exc_info()[1],
        )


@pytest.mark.parametrize("failed_start", [1, 2])
def test_pending_fixture_thread_start_failure_keeps_original_and_closes_db(tmp_path, monkeypatch, failed_start):
    original = RuntimeError("injected start failure")
    real_start = threading.Thread.start
    real_delete_db = _delete_db
    captured = []
    starts = 0

    def track_db(path):
        db = real_delete_db(path)
        captured.append(db)
        return db

    def start(worker):
        nonlocal starts
        starts += 1
        if starts == failed_start:
            raise original
        return real_start(worker)

    monkeypatch.setattr(sys.modules[__name__], "_delete_db", track_db)
    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="injected start failure") as caught:
        test_new_reader_retries_one_pending_lock_on_existing_read_path(tmp_path)
    assert caught.value is original
    assert captured[0]._conn is None


def test_pending_reader_rollback_failure_is_collected_and_signaled():
    failure = sqlite3.OperationalError("injected rollback failure")
    released = threading.Event()
    errors = []

    class FailedRollback:
        def rollback(self):
            raise failure

    _rollback_pending_reader(FailedRollback(), released, errors)
    assert errors == [failure]
    assert released.is_set()


def test_pending_fixture_cleanup_handles_unstarted_threads():
    closed = []
    event = threading.Event()
    worker = threading.Thread(target=lambda: None)
    _cleanup_pending_fixture([event], [worker], [lambda: closed.append(True)])
    assert event.is_set()
    assert closed == [True]


def test_pending_fixture_cleanup_does_not_close_live_worker_connections():
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait)
    closed = []
    worker.start()
    try:
        with pytest.raises(RuntimeError, match="worker.*alive"):
            _cleanup_pending_fixture([], [worker], [lambda: closed.append(True)], join_timeout=0.01)
        assert not closed
    finally:
        stop.set()
        worker.join(2)
    assert not worker.is_alive()
    _cleanup_pending_fixture([], [worker], [lambda: closed.append(True)])
    assert closed == [True]


def test_pending_fixture_cleanup_preserves_original_failure():
    original = RuntimeError("thread start failed")
    closed = []

    def broken_close():
        raise RuntimeError("cleanup failed")

    with pytest.raises(RuntimeError, match="thread start failed") as caught:
        try:
            raise original
        finally:
            _cleanup_pending_fixture(
                [], [threading.Thread(target=lambda: None)],
                [broken_close, lambda: closed.append(True)], active_error=original,
            )
    assert caught.value is original
    assert closed == [True]
    assert any("cleanup failed" in note for note in original.__notes__)
