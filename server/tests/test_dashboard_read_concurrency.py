import sqlite3
import threading
import time

from atlas_server.store import AtlasStore
from test_store import observation


def test_slow_transmission_reader_does_not_hold_writer_lock(tmp_path, monkeypatch):
    store = AtlasStore(tmp_path / "atlas.db")
    first = observation("a" * 32, "A", "2026-11-25T12:00:00Z", -80)
    assert store.ingest(first)

    original_connect = sqlite3.connect
    read_started = threading.Event()
    release_read = threading.Event()

    class SlowReadConnection:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, query, *args, **kwargs):
            if "WITH recent AS" in query:
                read_started.set()
                assert release_read.wait(2)
            return self.connection.execute(query, *args, **kwargs)

        def close(self):
            self.connection.close()

    def connect(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        return SlowReadConnection(connection) if kwargs.get("uri") else connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    result = []
    reader = threading.Thread(target=lambda: result.extend(store.list_transmissions()))
    reader.start()
    try:
        assert read_started.wait(2)
        second = observation("b" * 32, "B", "2026-11-25T12:00:01Z", -81)
        second["packet_id"] = 43
        started = time.monotonic()
        assert store.ingest(second)
        assert time.monotonic() - started < 0.5
        release_read.set()
        reader.join(2)
        assert not reader.is_alive()
        assert result
    finally:
        release_read.set()
        reader.join(2)
        store.close()


def test_dashboard_query_indexes_and_limited_first_plan(tmp_path):
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        indexes = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index'"
            )
        }
        assert "transmissions_last_seen_idx" in indexes
        assert "events_heartbeat_observer_time_idx" in indexes
        assert "events_connection_error_observer_time_idx" in indexes

        plan = store.connection.execute(
            """
            EXPLAIN QUERY PLAN
            WITH recent AS (
                SELECT * FROM transmissions ORDER BY last_seen DESC LIMIT 100
            )
            SELECT r.*, count(o.event_id), count(DISTINCT o.observer_id)
            FROM recent r LEFT JOIN observations o ON o.transmission_id=r.id
            GROUP BY r.id ORDER BY r.last_seen DESC
            """
        ).fetchall()
        details = " ".join(row[3] for row in plan)
        assert "transmissions_last_seen_idx" in details
        assert "observations_transmission_idx" in details
    finally:
        store.close()
