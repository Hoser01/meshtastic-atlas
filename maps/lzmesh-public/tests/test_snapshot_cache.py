import concurrent.futures
import io
import json
import logging
import multiprocessing
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests
import app as map_app
from snapshot_cache import SnapshotCache


def process_reader(directory, count, entered, release):
    def fetch():
        with count.get_lock():
            count.value += 1
        entered.set()
        release.wait(3)
        return {"shared": {"ageMin": 1}}
    cache = SnapshotCache("nodes", fetch, dict, directory=directory)
    cache.get()
    cache.join(4)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.logger = logging.getLogger("cache-test-" + self.id())
        self.logger.setLevel(logging.INFO)
        self.logs = io.StringIO()
        self.handler = logging.StreamHandler(self.logs)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def cache(self, fetch, **kw):
        return SnapshotCache("nodes", fetch, dict, clock=lambda: self.now,
                             logger=self.logger, **kw)

    def prime(self, cache):
        cache.get()
        cache.join()

    def test_stalled_cold_feed_never_blocks_concurrent_requests(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def fetch():
            calls.append(1)
            entered.set()
            release.wait(3)
            return {"node": {"ageMin": 1}}
        cache = self.cache(fetch)
        try:
            started = time.monotonic()
            self.assertEqual(cache.get(), {})
            self.assertTrue(entered.wait(1))
            with concurrent.futures.ThreadPoolExecutor(16) as pool:
                values = list(pool.map(lambda _: cache.get(), range(64)))
            self.assertTrue(all(v == {} for v in values))
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            cache.join()
        self.assertIn("node", cache.get())

    def test_stale_data_returns_while_refresh_is_stalled(self):
        cache = self.cache(lambda: {"old": {"ageMin": 1}})
        self.prime(cache)
        self.now += 21
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            release.wait(3)
            return {"new": {"ageMin": 1}}
        cache.fetch = fetch
        try:
            started = time.monotonic()
            self.assertIn("old", cache.get())
            self.assertTrue(entered.wait(1))
            self.assertIn("old", cache.get())
            self.assertLess(time.monotonic() - started, 0.25)
        finally:
            release.set()
            cache.join()
        self.assertIn("new", cache.get())

    def test_failures_keep_snapshot_backoff_and_eventual_recovery(self):
        for error in [requests.Timeout("private-url-and-token"),
                      requests.HTTPError("private-body", response=Mock(status_code=503)),
                      requests.exceptions.JSONDecodeError("private-body", "!", 0),
                      ValueError("schema")]:
            with self.subTest(error=type(error).__name__):
                self.now = 100
                cache = self.cache(lambda: {"old": {}}, retry_base=5, retry_max=12)
                self.prime(cache)
                self.now += 21
                bad = Mock(side_effect=error)
                cache.fetch = bad
                self.prime(cache)
                self.assertEqual(cache.get(), {"old": {}})
                self.assertEqual(bad.call_count, 1)
                self.now += 4.9
                cache.get(); cache.join()
                self.assertEqual(bad.call_count, 1)
                self.now += 0.2
                self.prime(cache)
                self.assertEqual(bad.call_count, 2)
                self.assertEqual(cache._state["retry_at"], self.now + 10)
                self.now += 10
                self.prime(cache)
                self.assertEqual(cache._state["retry_at"], self.now + 12)
                cache.fetch = lambda: {"recovered": {}}
                self.now += 12
                self.prime(cache)
                self.assertEqual(cache.get(), {"recovered": {}})
                self.assertEqual(cache._state["failures"], 0)
        self.assertNotIn("private", self.logs.getvalue())
        events = [json.loads(l) for l in self.logs.getvalue().splitlines()]
        self.assertTrue(any(e["http_status"] == 503 for e in events))
        self.assertTrue(all("duration_ms" in e and "cache_age_seconds" in e for e in events))

    def test_valid_empty_payload_is_cached(self):
        fetch = Mock(return_value={})
        cache = self.cache(fetch)
        self.prime(cache)
        self.assertTrue(cache.status()["ready"])
        self.assertEqual(cache.get(), {})
        self.assertEqual(fetch.call_count, 1)

    def test_shared_cache_recovers_after_process_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.cache(lambda: {"old": {}}, directory=directory)
            self.prime(cache)
            other_fetch = Mock(return_value={})
            other = self.cache(other_fetch, directory=directory)
            self.assertEqual(other.get(), {"old": {}})
            other.join()
            self.assertFalse(other_fetch.called)
            self.assertEqual((cache.path.stat().st_mode & 0o777), 0o600)

    def test_shared_failure_backoff_applies_to_other_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.cache(Mock(side_effect=requests.Timeout()), directory=directory)
            self.prime(cache)
            fetch = Mock(return_value={})
            other = self.cache(fetch, directory=directory)
            self.assertEqual(other.get(), {})
            other.join()
            self.assertFalse(fetch.called)
            self.now += 5
            self.prime(other)
            self.assertEqual(fetch.call_count, 1)

    def test_shared_lock_limits_two_processes_to_one_refresh(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            count, entered, release = ctx.Value("i", 0), ctx.Event(), ctx.Event()
            a = ctx.Process(target=process_reader, args=(directory, count, entered, release))
            b = ctx.Process(target=process_reader, args=(directory, count, entered, release))
            try:
                a.start()
                self.assertTrue(entered.wait(2))
                b.start(); b.join(2)
                self.assertEqual(b.exitcode, 0)
                self.assertEqual(count.value, 1)
            finally:
                release.set()
                a.join(4)
                if b.is_alive(): b.terminate(); b.join()
                if a.is_alive(): a.terminate(); a.join()
            self.assertEqual(a.exitcode, 0)
            other = self.cache(Mock(), directory=directory)
            self.assertIn("shared", other.get())

    def test_nodes_and_summary_return_compatible_empty_shape_during_cold_failure(self):
        cache = self.cache(Mock(side_effect=requests.Timeout()))
        with patch.object(map_app, "_nodes_cache", cache):
            client = map_app.app.test_client()
            self.assertEqual(client.get("/new/nodes").get_json(), {})
            self.assertEqual(client.get("/new/summary").get_json(),
                             {"live": 0, "day": 0, "ninety": 0, "all": 0})
            health = client.get("/health")
            self.assertEqual(health.status_code, 503)
            self.assertEqual(health.get_json()["status"], "warming")
            self.assertEqual(client.get("/new/nodes").headers["Cache-Control"], "no-store")
        cache.join()

    def test_observers_empty_then_cached_list(self):
        cache = SnapshotCache("observers", lambda: [{"id": 1}], list)
        with patch.object(map_app, "_observers_cache", cache):
            client = map_app.app.test_client()
            self.assertIsInstance(client.get("/new/observers").get_json(), list)
            cache.join()
            self.assertEqual(client.get("/new/observers").get_json(), [{"id": 1}])

    def test_http_status_json_and_schema_failures_close_response(self):
        for error in [requests.HTTPError(response=Mock(status_code=503)),
                      requests.exceptions.JSONDecodeError("bad", "!", 0), None]:
            response = Mock()
            if isinstance(error, requests.HTTPError):response.raise_for_status.side_effect = error
            elif error:response.json.side_effect = error
            else:response.json.return_value = []
            with patch.object(map_app.requests, "get", return_value=response):
                with self.assertRaises((requests.HTTPError, ValueError)):
                    map_app._read_feed("http://127.0.0.1/feed", dict)
            response.close.assert_called_once()

    def test_real_slow_upstream_times_out_without_blocking_request(self):
        entered = threading.Event()
        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_GET(self):
                entered.set(); time.sleep(0.4)
                try:
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
                except (BrokenPipeError, ConnectionResetError):pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = "http://127.0.0.1:%s/feed" % server.server_port
            with patch.object(map_app, "ATLAS_TIMEOUT_SECONDS", 0.07):
                cache = self.cache(lambda: map_app._read_feed(url, dict))
                start = time.monotonic(); self.assertEqual(cache.get(), {})
                self.assertLess(time.monotonic() - start, 0.2)
                self.assertTrue(entered.wait(1))
                cache.join()
                self.assertEqual(cache._state["failures"], 1)
                self.assertFalse(cache.status()["ready"])
                self.assertIn("ReadTimeout", self.logs.getvalue())
        finally:
            server.shutdown(); server.server_close(); thread.join(1)


if __name__ == "__main__":
    unittest.main()
