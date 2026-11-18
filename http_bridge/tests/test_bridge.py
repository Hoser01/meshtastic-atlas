"""Real-socket tests; run with: python3 -m unittest discover -s tests -v."""

import http.client
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from meshtastic_http_bridge import bridge

ORIGIN_A = "http://web.test:8080"
ORIGIN_B = "https://other-web.test"
WANT_CONFIG = b"\x18\x7b"


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for test condition")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeMux:
    """Controllable TCP wire peer used as a MUX or simulated physical radio."""

    def __init__(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(16)
        self.listener.settimeout(0.1)
        self.connections = []
        self.received = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                sock, _addr = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            sock.settimeout(0.1)
            with self.lock:
                index = len(self.connections)
                self.connections.append(sock)
            threading.Thread(target=self._read, args=(index, sock), daemon=True).start()

    def _read(self, index, sock):
        parser = bridge.FrameParser()
        try:
            while not self.stop.is_set():
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    return
                for payload in parser.feed(data):
                    with self.lock:
                        self.received.append((index, payload))
        except OSError:
            pass

    def send(self, index, payloads, fragment=False):
        raw = b"".join(bridge.stream_frame(value) for value in payloads)
        sock = self.connections[index]
        if fragment:
            # Split both magic bytes and the payload across TCP writes.
            for piece in (raw[:1], raw[1:3], raw[3:5], raw[5:]):
                if piece:
                    sock.sendall(piece)
        else:
            sock.sendall(raw)

    def disconnect(self, index):
        sock = self.connections[index]
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        for index in range(len(self.connections)):
            self.disconnect(index)
        self.thread.join(timeout=2)


class Harness:
    def __init__(self, mux_port, **overrides):
        self.settings = replace(
            bridge.Settings(), mux_port=mux_port, allowed_origins=(ORIGIN_A, ORIGIN_B), **overrides
        )
        self.sessions = bridge.Sessions(self.settings)
        self.server = bridge.BridgeServer(("127.0.0.1", 0), self.sessions)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def request(
        self,
        method="GET",
        path="/api/v1/fromradio?all=false",
        body=None,
        origin=ORIGIN_A,
        headers=None,
        tls_context=None,
    ):
        request_headers = {} if origin is None else {"Origin": origin}
        if body is not None:
            request_headers["Content-Type"] = bridge.PROTO_TYPE
        request_headers.update(headers or {})
        if tls_context is not None:
            conn = http.client.HTTPSConnection(
                "127.0.0.1", self.port, context=tls_context, timeout=3
            )
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=request_headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.sessions.close()
        self.thread.join(timeout=2)


class FrameTests(unittest.TestCase):
    def test_every_possible_split_and_coalesced_frames(self):
        expected = [b"\x08\x01", b"\x08\x02", b"x" * 512]
        raw = b"".join(bridge.stream_frame(value) for value in expected)
        for split in range(len(raw) + 1):
            parser = bridge.FrameParser()
            self.assertEqual(parser.feed(raw[:split]) + parser.feed(raw[split:]), expected)

    def test_debug_noise_invalid_length_and_split_magic_resynchronize(self):
        parser = bridge.FrameParser()
        self.assertEqual(parser.feed(b"debug text" * 10000 + b"\x94"), [])
        self.assertEqual(parser.feed(b"\xc3\xff\xffjunk\x94"), [])
        self.assertEqual(parser.feed(bridge.stream_frame(b"\x08\x01")[1:]), [b"\x08\x01"])
        self.assertLessEqual(len(parser.buffer), 515)

    def test_outbound_payload_size_limits(self):
        for payload in (b"", b"x" * 513):
            with self.assertRaises(ValueError):
                bridge.stream_frame(payload)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.mux = FakeMux()
        self.web = Harness(self.mux.port)

    def tearDown(self):
        self.web.close()
        self.mux.close()

    def establish(self, origin=ORIGIN_A):
        response = self.web.request("PUT", "/api/v1/toradio", WANT_CONFIG, origin=origin)
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], WANT_CONFIG)
        return response

    def test_http_to_tcp_and_fragmented_tcp_to_http(self):
        response = self.establish()
        self.assertEqual(response[1]["Access-Control-Allow-Origin"], ORIGIN_A)
        wait_for(lambda: self.mux.received)
        self.assertEqual(self.mux.received, [(0, WANT_CONFIG)])
        self.mux.send(0, [b"\x08\x01", b"\x08\x02"], fragment=True)
        wait_for(lambda: next(iter(self.web.sessions.items.values())).queue_bytes == 4)
        for value in (b"\x08\x01", b"\x08\x02", b""):
            status, headers, body = self.web.request()
            self.assertEqual((status, body), (200, value))
            self.assertEqual(headers["Content-Type"], bridge.PROTO_TYPE)

    def test_all_true_matches_firmware_concatenation(self):
        self.establish()
        wait_for(lambda: self.mux.received)
        self.mux.send(0, [b"\x08\x01", b"\x08\x02"])
        wait_for(lambda: next(iter(self.web.sessions.items.values())).queue_bytes == 4)
        self.assertEqual(
            self.web.request(path="/api/v1/fromradio?all=true")[2], b"\x08\x01\x08\x02"
        )
        self.assertEqual(self.web.request()[2], b"")

    def test_independent_browser_queues_and_tcp_connections(self):
        self.establish(ORIGIN_A)
        self.establish(ORIGIN_B)
        wait_for(lambda: len(self.mux.received) == 2)
        self.mux.send(0, [b"\x08\x01"])
        self.mux.send(1, [b"\x08\x02"])
        wait_for(lambda: all(s.queue_bytes == 2 for s in self.web.sessions.items.values()))
        self.assertEqual(self.web.request(origin=ORIGIN_A)[2], b"\x08\x01")
        self.assertEqual(self.web.request(origin=ORIGIN_B)[2], b"\x08\x02")

    def test_root_and_options_do_not_open_mux_or_consume_data(self):
        for path in ("/", "/api/v1/toradio", "/api/v1/fromradio"):
            status = self.web.request("GET" if path == "/" else "OPTIONS", path)[0]
            self.assertEqual(status, 200 if path == "/" else 204)
        self.assertEqual(len(self.mux.connections), 0)
        status, _headers, body = self.web.request(path="/healthz", origin=None)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["mux_tcp_reachable"])
        self.assertFalse(json.loads(body)["radio_link_verified"])
        self.assertFalse(self.web.sessions.items)

    def test_preflight_cors_and_private_network_headers(self):
        status, headers, body = self.web.request(
            "OPTIONS",
            "/api/v1/toradio",
            headers={
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN_A)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertIn("PUT", headers["Access-Control-Allow-Methods"])

    def test_disallowed_origins_cannot_read_or_write(self):
        for origin in ("https://untrusted.test", "null", "http://web.test:8080@evil.test"):
            self.assertEqual(self.web.request(origin=origin)[0], 403)
            self.assertEqual(
                self.web.request("PUT", "/api/v1/toradio", WANT_CONFIG, origin=origin)[0], 403
            )
        self.assertFalse(self.mux.connections)

    def test_no_cors_browser_requests_cannot_drain_api(self):
        self.assertEqual(
            self.web.request(origin=None, headers={"Sec-Fetch-Mode": "no-cors"})[0], 403
        )
        # The no-cors root probe used by the web client is still allowed.
        self.assertEqual(
            self.web.request(path="/", origin=None, headers={"Sec-Fetch-Mode": "no-cors"})[0], 200
        )

    def test_invalid_bodies_are_rejected_before_opening_mux(self):
        for body, headers, status in (
            (b"", {}, 400),
            (b"x" * 513, {}, 413),
            (WANT_CONFIG, {"Content-Type": "text/plain"}, 415),
            (WANT_CONFIG, {"Transfer-Encoding": "chunked"}, 400),
        ):
            self.assertEqual(
                self.web.request("PUT", "/api/v1/toradio", body, headers=headers)[0], status
            )
        self.assertFalse(self.mux.connections)

    def test_mux_disconnect_is_reported_then_new_put_reconnects(self):
        self.establish()
        wait_for(lambda: self.mux.received)
        self.mux.disconnect(0)
        wait_for(lambda: next(iter(self.web.sessions.items.values())).failed())
        self.assertEqual(self.web.request()[0], 503)
        self.assertEqual(self.web.request()[0], 503)
        self.establish()
        wait_for(lambda: len(self.mux.received) == 2)
        self.assertEqual(self.mux.received, [(0, WANT_CONFIG), (1, WANT_CONFIG)])

    def test_queue_overflow_forces_reconnect_instead_of_silent_data_loss(self):
        self.web.close()
        self.web = Harness(self.mux.port, max_queue_frames=2)
        self.establish()
        wait_for(lambda: self.mux.received)
        self.mux.send(0, [b"\x08\x01", b"\x08\x02", b"\x08\x03"])
        wait_for(lambda: next(iter(self.web.sessions.items.values())).failed())
        status, _headers, body = self.web.request()
        self.assertEqual(status, 503)
        self.assertIn(b"overflow", body)

    def test_session_limit_leaves_existing_client_usable(self):
        self.web.close()
        self.web = Harness(self.mux.port, max_sessions=1)
        self.establish(ORIGIN_A)
        self.assertEqual(self.web.request(origin=ORIGIN_B)[0], 503)
        self.assertEqual(self.web.request(origin=ORIGIN_A)[0], 200)

    def test_idle_session_expires_and_next_request_can_start_new_one(self):
        self.web.close()
        self.web = Harness(self.mux.port, idle_seconds=0.1)
        self.establish()
        wait_for(lambda: not self.web.sessions.items)
        self.assertEqual(self.web.request()[0], 200)

    def test_unavailable_mux_returns_503(self):
        self.web.close()
        self.web = Harness(free_port())
        self.assertEqual(self.web.request()[0], 503)
        self.assertEqual(self.web.request(path="/healthz")[0], 503)


class ConfigTests(unittest.TestCase):
    def test_origin_canonicalization(self):
        self.assertEqual(bridge.origin_of("HTTPS://WEB.TEST:443/"), "https://web.test")
        self.assertEqual(bridge.origin_of("http://[::1]:8080"), "http://[::1]:8080")
        self.assertEqual(
            bridge.origin_of("http://web.test/path", allow_path=True), "http://web.test"
        )

    def test_invalid_origins(self):
        for value in (
            "*",
            "null",
            "web.test",
            "https://user:pass@web.test",
            "http://web.test/path",
            "http://web.test\r\nX:1",
        ):
            with self.assertRaises(ValueError):
                bridge.origin_of(value)

    def test_missing_origin_and_port_collisions_rejected(self):
        with self.assertRaises(ValueError):
            bridge.Settings().validate()
        with self.assertRaises(ValueError):
            bridge.Settings(allowed_origins=(ORIGIN_A,), http_port=4405).validate()


@unittest.skipUnless(shutil.which("openssl"), "openssl is required for HTTPS test")
class TLSTests(unittest.TestCase):
    def test_trusted_tls_and_slow_handshake_do_not_block_next_client(self):
        mux = FakeMux()
        web = Harness(mux.port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cert, key = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
                subprocess.run(
                    [
                        "openssl",
                        "req",
                        "-x509",
                        "-newkey",
                        "rsa:2048",
                        "-nodes",
                        "-days",
                        "1",
                        "-subj",
                        "/CN=localhost",
                        "-addext",
                        "subjectAltName=IP:127.0.0.1,DNS:localhost",
                        "-keyout",
                        str(key),
                        "-out",
                        str(cert),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(cert, key)
                web.server.tls_context = context
                client_context = ssl.create_default_context(cafile=str(cert))
                with socket.create_connection(("127.0.0.1", web.port)):
                    # An accepted client that never starts TLS must not stall accept().
                    status, _headers, body = web.request(path="/", tls_context=client_context)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["version"], bridge.VERSION)
                status, _headers, body = web.request(
                    "PUT", "/api/v1/toradio", WANT_CONFIG, tls_context=client_context
                )
                self.assertEqual((status, body), (200, WANT_CONFIG))
                wait_for(lambda: mux.received)
        finally:
            web.close()
            mux.close()


if __name__ == "__main__":
    unittest.main()
