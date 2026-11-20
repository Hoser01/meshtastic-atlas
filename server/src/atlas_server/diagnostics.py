"""Rate-limited latency diagnostics that never include request or event data."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Self


class RateLimitedWarning:
    """Emit at most one warning per key in each configured interval."""

    def __init__(
        self,
        logger: logging.Logger,
        interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.logger = logger
        self.interval_seconds = max(0.0, interval_seconds)
        self.clock = clock
        self.lock = threading.Lock()
        self.last_emitted: dict[str, float] = {}
        self.suppressed: dict[str, int] = {}

    def emit(self, key: str, message: str, *args: Any) -> bool:
        now = self.clock()
        with self.lock:
            previous = self.last_emitted.get(key)
            if previous is not None and now - previous < self.interval_seconds:
                self.suppressed[key] = self.suppressed.get(key, 0) + 1
                return False
            suppressed = self.suppressed.pop(key, 0)
            self.last_emitted[key] = now
        self.logger.warning(message + " suppressed_since_last=%d", *args, suppressed)
        return True


class TimedRLock:
    """RLock-compatible context manager with bounded wait diagnostics."""

    def __init__(
        self,
        logger: logging.Logger,
        slow_seconds: float = 0.1,
        interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self.slow_seconds = max(0.0, slow_seconds)
        self.clock = clock
        self.warnings = RateLimitedWarning(logger, interval_seconds, clock)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        started = self.clock()
        acquired = self._lock.acquire(blocking, timeout)
        waited = self.clock() - started
        if acquired and waited >= self.slow_seconds:
            self.warnings.emit(
                "atlas-store-lock",
                "slow store lock acquisition wait_seconds=%.3f",
                waited,
            )
        return acquired

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
