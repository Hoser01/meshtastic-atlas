"""Nonblocking stale snapshots, coordinated across Gunicorn processes.

Only refresh threads contact upstream. Optional local shared files are atomically
replaced; readers never take the refresh lock. This module is Linux-specific.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
from pathlib import Path


class SnapshotCache:
    def __init__(self, name, fetch, empty, ttl=20, retry_base=5, retry_max=60,
                 directory=None, logger=None, clock=time.time):
        self.name, self.fetch, self.empty = name, fetch, empty
        self.ttl = max(0.1, ttl)
        self.retry_base = max(0.1, retry_base)
        self.retry_max = max(self.retry_base, retry_max)
        self.directory = Path(directory) if directory else None
        self.logger = logger or logging.getLogger(__name__)
        self.clock = clock
        self._state = {"payload": None, "fetched_at": None, "failures": 0, "retry_at": 0}
        self._refresh_lock = threading.Lock()
        self._thread = None
        self._file_version = None
        self._contended_until = 0

    @property
    def path(self):
        return self.directory / (self.name + ".json")

    def _sync(self):
        if self.directory is None:
            return
        try:
            stat = self.path.stat()
            version = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if version != self._file_version:
                state = json.loads(self.path.read_text())
                if not isinstance(state, dict) or not all(k in state for k in self._state):
                    raise ValueError("invalid shared snapshot")
                self._state = state
                self._file_version = version
        except FileNotFoundError:
            pass

    def _publish(self, state):
        if self.directory is not None:
            # Each writer owns the cross-process flock. A crash leaves the old
            # complete snapshot intact; incomplete .tmp files are never read.
            temp = self.path.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            self._file_version = None
        self._state = state

    def _age(self, state):
        at = state["fetched_at"]
        return max(0, self.clock() - at) if at is not None else None

    def _due(self):
        state = self._state
        age = self._age(state)
        return ((age is None or age >= self.ttl) and
                self.clock() >= state["retry_at"] and
                self.clock() >= self._contended_until)

    def status(self):
        state = self._state
        age = self._age(state)
        return {"ready": state["fetched_at"] is not None,
                "age_seconds": round(age, 3) if age is not None else None,
                "stale": age is None or age >= self.ttl,
                "refreshing": self._refresh_lock.locked()}

    def get(self):
        """Return a complete snapshot immediately; never wait for a refresh."""
        try:
            self._sync()
        except (OSError, ValueError, TypeError, KeyError):
            # Retain this process's last complete snapshot on local read errors.
            # The refresh thread logs the sanitized error if it persists.
            pass
        if self._due() and self._refresh_lock.acquire(blocking=False):
            try:
                self._thread = threading.Thread(target=self._refresh,
                                                name="atlas-" + self.name, daemon=True)
                self._thread.start()
            except Exception as error:
                self._failure(error, time.monotonic(), publish=False)
                self._refresh_lock.release()
        payload = self._state["payload"]
        return payload if payload is not None else self.empty()

    def _log(self, level, result, started, error=None, retry=None):
        status = self.status()
        event = {"event": "atlas_refresh", "feed": self.name, "result": result,
                 "duration_ms": round((time.monotonic() - started) * 1000, 2),
                 "cache_age_seconds": status["age_seconds"],
                 "error_type": type(error).__name__ if error else None,
                 "http_status": getattr(getattr(error, "response", None), "status_code", None),
                 "retry_seconds": retry}
        # Never log URLs, exception messages, credentials, or response bodies.
        self.logger.log(level, json.dumps(event, allow_nan=False))

    def _failure(self, error, started, publish=True):
        state = dict(self._state)
        state["failures"] += 1
        retry = min(self.retry_max, self.retry_base * 2 ** min(state["failures"] - 1, 16))
        state["retry_at"] = self.clock() + retry
        try:
            if publish:
                self._publish(state)
            else:
                self._state = state
        except (OSError, ValueError, TypeError):
            self._state = state
        self._log(logging.WARNING, "failure", started, error, retry)

    def _refresh(self):
        started = time.monotonic()
        shared_lock = None
        owns_shared_lock = self.directory is None
        try:
            if self.directory is not None:
                shared_lock = open(self.directory / (self.name + ".lock"), "a")
                os.chmod(shared_lock.name, 0o600)
                try:
                    fcntl.flock(shared_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    owns_shared_lock = True
                except BlockingIOError:
                    self._contended_until = self.clock() + 0.25
                    return
                # Another worker may have refreshed or failed since scheduling.
                self._sync()
                if not self._due():
                    return
            payload = self.fetch()
            self._publish({"payload": payload, "fetched_at": self.clock(),
                           "failures": 0, "retry_at": 0})
            self._log(logging.INFO, "success", started)
        except Exception as error:
            self._failure(error, started, publish=owns_shared_lock)
        finally:
            if shared_lock is not None:
                shared_lock.close()
            self._refresh_lock.release()

    def join(self, timeout=3):
        """Used by isolated tests; never called from a request handler."""
        thread = self._thread
        if thread:
            thread.join(timeout)
