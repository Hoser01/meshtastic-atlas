import sqlite3
import threading

import pytest

from atlas_server.store import AtlasStore
from test_store import observation


def test_reachability_read_does_not_hold_store_lock_or_block_writer(tmp_path, monkeypatch):
    store = AtlasStore(tmp_path / "atlas.db")
    first = observation("a" * 32, "A", "2026-09-18T13:00:00Z", -80)
    first["position"] = {"latitude": 37.0, "longitude": -93.0}
    assert store.ingest(first)

    original_connect = sqlite3.connect
    read_started = threading.Event()
    release_read = threading.Event()
    read_closed = threading.Event()

    class SlowReadConnection:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, *args, **kwargs):
            cursor = self.connection.execute(*args, **kwargs)
            read_started.set()
            assert release_read.wait(2)
            return cursor

        def close(self):
            self.connection.close()
            read_closed.set()

    def connect(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        return SlowReadConnection(connection) if kwargs.get("uri") else connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    result = []
    reader = threading.Thread(target=lambda: result.extend(store.list_reachability_evidence()))
    reader.start()
    try:
        assert read_started.wait(2)
        second = observation("b" * 32, "B", "2026-09-18T13:00:01Z", -81)
        second["packet_id"] = 43
        second["position"] = {"latitude": 37.1, "longitude": -93.1}
        assert store.ingest(second)
        release_read.set()
        reader.join(2)
        assert not reader.is_alive()
        assert [row["event_id"] for row in result] == [first["event_id"]]
        assert read_closed.is_set()
    finally:
        release_read.set()
        reader.join(2)
        store.close()


def test_reachability_read_closes_independent_connection_on_error(tmp_path, monkeypatch):
    store = AtlasStore(tmp_path / "atlas.db")
    original_connect = sqlite3.connect
    read_closed = threading.Event()

    class FailingReadConnection:
        row_factory = None

        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("test read failure")

        def close(self):
            read_closed.set()

    def connect(database, *args, **kwargs):
        if kwargs.get("uri"):
            return FailingReadConnection()
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="test read failure"):
            store.list_reachability_evidence()
        assert read_closed.is_set()
    finally:
        store.close()


def test_reachability_read_preserves_limit_filter_and_in_memory_semantics(tmp_path):
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        direct = observation("a" * 32, "A", "2026-09-18T13:00:00Z", -80)
        direct["position"] = {"latitude": 37.0, "longitude": -93.0}
        repeated = observation("b" * 32, "A", "2026-09-18T13:01:00Z", -81)
        repeated["packet_id"] = 43
        repeated["position"] = {"latitude": 37.1, "longitude": -93.1}
        repeated["repeat_observation"] = True
        assert store.ingest(direct)
        assert store.ingest(repeated)
        rows = store.list_reachability_evidence(limit=1)
        assert [row["event_id"] for row in rows] == [repeated["event_id"]]
        assert rows[0]["latitude"] == 37.1
    finally:
        store.close()

    memory_store = AtlasStore(":memory:")
    try:
        assert memory_store.list_reachability_evidence() == []
    finally:
        memory_store.close()
