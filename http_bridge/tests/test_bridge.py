from __future__ import annotations

import http.client
import socket
import threading
import time

import pytest

from meshtastic_http_bridge.bridge import MuxBridge, PayloadQueue, build_server
from meshtastic_http_bridge.framing import FrameDecoder, frame_payload


class FakeMux:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.connected = threading.Event()
        self.connection: socket.socket | None = None
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        self.connection, _ = self.listener.accept()
        self.connection.settimeout(2)
        self.connected.set()

    def send(self, payload: bytes) -> None:
        assert self.connected.wait(2)
        assert self.connection is not None
        framed = frame_payload(payload)
        self.connection.sendall(framed[:2])
        self.connection.sendall(framed[2:])

    def receive(self) -> bytes:
        assert self.connected.wait(2)
        assert self.connection is not None
        decoder = FrameDecoder()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            for frame in decoder.feed(self.connection.recv(4096)):
                return frame.payload
        raise AssertionError("timed out waiting for a framed payload")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.listener.close()
        self.thread.join(timeout=2)


@pytest.fixture
def running_bridge():
    mux = FakeMux()
    bridge = MuxBridge("127.0.0.1", mux.port, reconnect_seconds=0.05, send_timeout=0.5)
    bridge.start()
    assert mux.connected.wait(2)
    server = build_server("127.0.0.1", 0, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield mux, bridge, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        bridge.stop()
        mux.close()
        thread.join(timeout=2)


def request(port: int, method: str, path: str, body: bytes | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {"Content-Type": "application/x-protobuf"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def wait_for_queue(bridge: MuxBridge, frames: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if bridge.queue.snapshot()["queued_frames"] >= frames:
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for bridge queue")


def test_payload_queue_is_bounded_and_oldest_first():
    queue = PayloadQueue(max_frames=2, max_bytes=6)
    queue.put(b"one")
    queue.put(b"two")
    queue.put(b"tri")
    assert queue.snapshot() == {"queued_frames": 2, "queued_bytes": 6, "dropped_frames": 1}
    assert queue.pop(False) == b"two"
    assert queue.pop(True) == b"tri"


def test_get_fromradio_removes_tcp_framing(running_bridge):
    mux, bridge, port = running_bridge
    mux.send(b"from-radio")
    wait_for_queue(bridge, 1)
    status, headers, body = request(port, "GET", "/api/v1/fromradio")
    assert status == 200
    assert headers["Content-Type"] == "application/x-protobuf"
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert body == b"from-radio"


def test_get_all_drains_available_payloads(running_bridge):
    mux, bridge, port = running_bridge
    mux.send(b"first")
    mux.send(b"second")
    wait_for_queue(bridge, 2)
    assert request(port, "GET", "/api/v1/fromradio?all=true")[2] == b"firstsecond"
    assert request(port, "GET", "/api/v1/fromradio?all=true")[2] == b""


def test_put_toradio_adds_tcp_framing_and_echoes_body(running_bridge):
    mux, _bridge, port = running_bridge
    status, _headers, body = request(port, "PUT", "/api/v1/toradio", b"to-radio")
    assert status == 200
    assert body == b"to-radio"
    assert mux.receive() == b"to-radio"


def test_configuration_request_clears_stale_receive_queue(running_bridge):
    mux, bridge, port = running_bridge
    bridge.queue.put(b"stale-live-packet")
    request_payload = b"\x18\xac\x9e\x04"  # ToRadio(want_config_id=69420)
    assert request(port, "PUT", "/api/v1/toradio", request_payload)[0] == 200
    assert mux.receive() == request_payload
    assert bridge.queue.snapshot()["queued_frames"] == 0


def test_options_supports_browser_private_network_preflight(running_bridge):
    _mux, _bridge, port = running_bridge
    status, headers, body = request(port, "OPTIONS", "/api/v1/toradio")
    assert status == 204
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert "PUT" in headers["Access-Control-Allow-Methods"]
    assert body == b""


def test_health_reports_mux_connection(running_bridge):
    _mux, _bridge, port = running_bridge
    status, headers, body = request(port, "GET", "/healthz")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert b'"mux_connected":true' in body


def test_rejects_empty_and_oversized_toradio(running_bridge):
    _mux, bridge, port = running_bridge
    assert request(port, "PUT", "/api/v1/toradio", b"")[0] == 400
    assert request(port, "PUT", "/api/v1/toradio", b"x" * (bridge.max_payload + 1))[0] == 413
