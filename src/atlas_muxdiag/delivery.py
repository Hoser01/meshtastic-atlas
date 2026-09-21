"""Bounded local evidence and resilient central delivery for remote observers."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__

LOG = logging.getLogger("atlas_delivery")


class RotatingNdjson:
    """Append NDJSON while retaining a bounded number of bounded files."""

    def __init__(self, path: Path | None, max_bytes: int, backups: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.backups = max(0, backups)
        self.lock = threading.Lock()
        self.stream = path.open("a", encoding="utf-8", buffering=1) if path else None

    def write(self, encoded: str) -> None:
        if self.stream is None or self.path is None:
            return
        line = encoded + "\n"
        with self.lock:
            if self.max_bytes and self.stream.tell() + len(line.encode("utf-8")) > self.max_bytes:
                self._rotate()
            self.stream.write(line)
            self.stream.flush()

    def _rotate(self) -> None:
        assert self.path is not None and self.stream is not None
        self.stream.close()
        if self.backups:
            oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
            oldest.unlink(missing_ok=True)
            for index in range(self.backups - 1, 0, -1):
                source = self.path.with_name(f"{self.path.name}.{index}")
                if source.exists():
                    os.replace(source, self.path.with_name(f"{self.path.name}.{index + 1}"))
            if self.path.exists():
                os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        else:
            self.path.unlink(missing_ok=True)
        self.stream = self.path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        if self.stream is not None:
            with self.lock:
                self.stream.close()


class DeliveryQueue:
    """SQLite outbox with bounded growth and opportunistic ordered delivery."""

    def __init__(
        self,
        path: Path,
        api_url: str,
        api_token: str,
        *,
        max_events: int = 50_000,
        max_bytes: int = 256 * 1024 * 1024,
        timeout: float = 5.0,
    ) -> None:
        self.path = path
        self.api_url = api_url
        self.api_token = api_token
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.lock = threading.RLock()
        self.next_attempt = 0.0
        self.backoff = 1.0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(path, check_same_thread=False)
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute("PRAGMA synchronous=NORMAL")
        self.database.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)"
        )
        self.database.commit()

    def enqueue(self, encoded: str) -> None:
        with self.lock:
            try:
                self.database.execute(
                    "INSERT INTO outbox(created, payload) VALUES (?, ?)", (time.time(), encoded)
                )
                count, payload_bytes = self.database.execute(
                    "SELECT count(*), coalesce(sum(length(payload)), 0) FROM outbox"
                ).fetchone()
                overflow = int(count) - self.max_events
                if self.max_bytes and payload_bytes > self.max_bytes and count:
                    average = max(1, payload_bytes // count)
                    overflow = max(
                        overflow, (payload_bytes - self.max_bytes + average - 1) // average
                    )
                if overflow > 0:
                    self.database.execute(
                        "DELETE FROM outbox WHERE id IN (SELECT id FROM outbox ORDER BY id LIMIT ?)",
                        (overflow,),
                    )
                    LOG.error("delivery queue full; discarded %d oldest events", overflow)
                self.database.commit()
                if overflow > 0:
                    self.database.execute("PRAGMA incremental_vacuum(128)")
            except sqlite3.DatabaseError:
                self.database.rollback()
                raise
        self.flush(100)

    def flush(self, limit: int = 500) -> int:
        if time.monotonic() < self.next_attempt:
            return 0
        delivered = 0
        with self.lock:
            rows = self.database.execute(
                "SELECT id, payload FROM outbox ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
            for row_id, payload in rows:
                request = urllib.request.Request(
                    self.api_url,
                    data=payload.encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-Atlas-Ingest-Token": self.api_token,
                        "User-Agent": f"ATLAS-Observer/{__version__}",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        if response.status != 202:
                            raise RuntimeError(f"ATLAS ingestion returned HTTP {response.status}")
                except (OSError, RuntimeError, TimeoutError, urllib.error.URLError) as exc:
                    LOG.warning("central delivery deferred: %s", exc)
                    self.next_attempt = time.monotonic() + self.backoff
                    self.backoff = min(60.0, self.backoff * 2)
                    break
                self.database.execute("DELETE FROM outbox WHERE id=?", (row_id,))
                delivered += 1
            if delivered:
                self.database.commit()
                self.backoff = 1.0
                self.next_attempt = 0.0
                self.database.execute("PRAGMA incremental_vacuum(64)")
        return delivered

    def depth(self) -> int:
        with self.lock:
            return int(self.database.execute("SELECT count(*) FROM outbox").fetchone()[0])

    def close(self) -> None:
        with self.lock:
            self.database.commit()
            self.database.close()


class DurableEventWriter:
    def __init__(
        self,
        output: Path | None,
        *,
        output_max_bytes: int,
        output_backups: int,
        api_url: str | None,
        api_token: str | None,
        spool: Path | None,
        spool_max_events: int,
        spool_max_bytes: int,
        api_timeout: float,
    ) -> None:
        self.local = RotatingNdjson(output, output_max_bytes, output_backups)
        self.queue = (
            DeliveryQueue(
                spool or Path("atlas-delivery.db"),
                api_url,
                api_token or "",
                max_events=spool_max_events,
                max_bytes=spool_max_bytes,
                timeout=api_timeout,
            )
            if api_url
            else None
        )

    def write(self, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
        self.local.write(encoded)
        if self.queue is not None:
            self.queue.enqueue(encoded)

    def queue_depth(self) -> int:
        return self.queue.depth() if self.queue else 0

    def close(self) -> None:
        self.local.close()
        if self.queue is not None:
            self.queue.flush(100)
            self.queue.close()
