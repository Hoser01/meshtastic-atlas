"""Exercise real two-worker Gunicorn against an isolated controllable upstream."""
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

stage = Path(sys.argv[1])
mode = "healthy"
calls = 0


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global calls
        calls += 1
        phase = mode
        if phase == "stall":
            time.sleep(0.7)
        try:
            self.send_response(503 if phase == "http" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = b'!' if phase == "json" else json.dumps(
                {"!test": {"id": "!test", "name": "isolated-" + phase, "ageMin": 1}}
            ).encode()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
thread = threading.Thread(target=upstream.serve_forever, daemon=True)
thread.start()
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
url = "http://127.0.0.1:%s/new/nodes" % port
with tempfile.TemporaryDirectory(prefix="atlas-isolated-") as directory:
    env = dict(os.environ, ATLAS_FEED_URL="http://127.0.0.1:%s/feed" % upstream.server_port,
               ATLAS_CACHE_DIRECTORY=directory, ATLAS_TIMEOUT_SECONDS="0.15",
               ATLAS_CACHE_SECONDS="0.2", ATLAS_RETRY_SECONDS="0.15",
               ATLAS_RETRY_MAX_SECONDS="0.3")
    with open(Path(directory) / "gunicorn.log", "w+") as logs:
        process = subprocess.Popen(
            ["/opt/lzmesh-map/venv/bin/gunicorn", "--workers", "2", "--bind",
             "127.0.0.1:%s" % port, "app:app"], cwd=stage, env=env,
            stdout=logs, stderr=logs, start_new_session=True)
        try:
            for _ in range(80):
                try:
                    response = requests.get(url, timeout=0.5)
                    if response.json().get("!test"):
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("isolated instance did not warm")
            assert response.json()["!test"]["name"] == "isolated-healthy"
            print("isolated warm snapshot HTTP", response.status_code)

            for phase, expected_error in [("stall", "ReadTimeout"), ("http", "HTTPError"),
                                           ("json", "JSONDecodeError")]:
                mode = phase
                time.sleep(0.35)
                times = []
                def read(_):
                    start = time.monotonic()
                    r = requests.get(url, timeout=1)
                    elapsed = time.monotonic() - start
                    assert r.status_code == 200
                    assert r.json()["!test"]["name"] == "isolated-healthy"
                    return elapsed
                with concurrent.futures.ThreadPoolExecutor(12) as pool:
                    times.extend(pool.map(read, range(48)))
                time.sleep(0.2)
                logs.flush(); logs.seek(0)
                assert expected_error in logs.read(), "missing failure log for " + phase
                assert max(times) < 0.5, "request waited on refresh"
                print("isolated", phase, "48 cached HTTP200 requests max_ms", round(max(times)*1000, 2))
                assert json.loads((Path(directory)/"nodes.json").read_text())["payload"]["!test"]["name"] == "isolated-healthy"

            mode = "recovered"
            for _ in range(50):
                r = requests.get(url, timeout=1)
                if r.json()["!test"]["name"] == "isolated-recovered":
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("no recovery")
            assert json.loads((Path(directory)/"nodes.json").read_text())["failures"] == 0
            print("isolated recovery HTTP200 new snapshot; upstream calls", calls)
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
upstream.shutdown()
upstream.server_close()
thread.join(2)
