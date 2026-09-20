import json
from pathlib import Path

from atlas_muxdiag.delivery import DeliveryQueue, RotatingNdjson


class Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_rotating_ndjson_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    writer = RotatingNdjson(path, max_bytes=60, backups=2)
    for index in range(10):
        writer.write(json.dumps({"index": index, "padding": "x" * 20}))
    writer.close()
    files = sorted(tmp_path.glob("events.ndjson*"))
    assert len(files) == 3
    assert all(file.stat().st_size <= 60 for file in files)


def test_delivery_queue_survives_outage_and_recovers(tmp_path: Path, monkeypatch) -> None:
    attempts = 0

    def failing(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", failing)
    queue = DeliveryQueue(tmp_path / "queue.db", "https://atlas.test/events", "token")
    queue.enqueue('{"event_id":"one"}')
    assert queue.depth() == 1
    assert attempts == 1

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    queue.next_attempt = 0
    assert queue.flush() == 1
    assert queue.depth() == 0
    queue.close()


def test_delivery_queue_discards_oldest_at_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    queue = DeliveryQueue(tmp_path / "queue.db", "https://atlas.test/events", "token", max_events=2)
    queue.enqueue('{"sequence":1}')
    queue.enqueue('{"sequence":2}')
    queue.enqueue('{"sequence":3}')
    rows = queue.database.execute("SELECT payload FROM outbox ORDER BY id").fetchall()
    assert [json.loads(row[0])["sequence"] for row in rows] == [2, 3]
    queue.close()
