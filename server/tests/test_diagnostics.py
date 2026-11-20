import logging

from atlas_server.diagnostics import RateLimitedWarning, TimedRLock


def test_rate_limited_warning_bounds_repeated_messages(caplog):
    times = iter((0.0, 1.0, 61.0))
    warning = RateLimitedWarning(
        logging.getLogger("atlas-test-diagnostic"),
        interval_seconds=60,
        clock=lambda: next(times),
    )
    with caplog.at_level(logging.WARNING, logger="atlas-test-diagnostic"):
        assert warning.emit("same", "safe diagnostic value=%d", 1)
        assert not warning.emit("same", "safe diagnostic value=%d", 2)
        assert warning.emit("same", "safe diagnostic value=%d", 3)
    assert len(caplog.records) == 2
    assert "suppressed_since_last=1" in caplog.records[-1].message


def test_timed_lock_reports_wait_without_application_data(caplog):
    times = iter((0.0, 0.2, 0.2))
    lock = TimedRLock(
        logging.getLogger("atlas-test-lock"),
        slow_seconds=0.1,
        clock=lambda: next(times),
    )
    with caplog.at_level(logging.WARNING, logger="atlas-test-lock"), lock:
        pass
    assert "slow store lock acquisition wait_seconds=0.200" in caplog.text
