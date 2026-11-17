"""Meshtastic HTTP PhoneAPI bridge backed by a native TCP MUX connection."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import ssl
import threading
import time
from collections import deque
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from google.protobuf.message import DecodeError

from .framing import DEFAULT_MAX_PAYLOAD, FrameDecoder, frame_payload

LOGGER = logging.getLogger("atlas_http_bridge")
PROTOBUF_CONTENT_TYPE = "application/x-protobuf"
PROTOBUF_SCHEMA = (
    "https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto"
)


class BridgeUnavailable(RuntimeError):
    """Raised when the bridge cannot currently write to the TCP MUX."""


class PayloadQueue:
    """Thread-safe, oldest-first queue with hard frame and byte limits."""

    def __init__(self, max_frames: int, max_bytes: int) -> None:
        if max_frames < 1 or max_bytes < 1:
            raise ValueError("queue limits must be positive")
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._items: deque[bytes] = deque()
        self._bytes = 0
        self._dropped = 0
        self._lock = threading.Lock()

    def put(self, payload: bytes) -> None:
        if not payload:
            return
        with self._lock:
            if len(payload) > self.max_bytes:
                self._dropped += 1
                return
            while self._items and (
                len(self._items) >= self.max_frames or self._bytes + len(payload) > self.max_bytes
            ):
                self._bytes -= len(self._items.popleft())
                self._dropped += 1
            self._items.append(payload)
            self._bytes += len(payload)

    def pop(self, drain_all: bool) -> bytes:
        with self._lock:
            if not self._items:
                return b""
            if not drain_all:
                payload = self._items.popleft()
                self._bytes -= len(payload)
                return payload
            payload = b"".join(self._items)
            self._items.clear()
            self._bytes = 0
            return payload

    def clear(self) -> int:
        with self._lock:
            removed = len(self._items)
            self._items.clear()
            self._bytes = 0
            return removed

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued_frames": len(self._items),
                "queued_bytes": self._bytes,
                "dropped_frames": self._dropped,
            }


class MuxBridge:
    """Maintain one reconnecting MUX connection and translate its framed stream."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        reconnect_seconds: float = 2.0,
        connect_timeout: float = 5.0,
        send_timeout: float = 3.0,
        max_payload: int = DEFAULT_MAX_PAYLOAD,
        queue_frames: int = 4096,
        queue_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.host = host
        self.port = port
        self.reconnect_seconds = reconnect_seconds
        self.connect_timeout = connect_timeout
        self.send_timeout = send_timeout
        self.max_payload = max_payload
        self.queue = PayloadQueue(queue_frames, queue_bytes)
        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self._socket: socket.socket | None = None
        self._socket_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._started_at = time.monotonic()
        self._connections = 0
        self._disconnects = 0
        self._frames_from_mux = 0
        self._frames_to_mux = 0
        self._errors = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="atlas-http-mux", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self._socket_lock:
            sock, self._socket = self._socket, None
        self.connected.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _increment(self, field: str) -> None:
        with self._stats_lock:
            setattr(self, field, getattr(self, field) + 1)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                sock.settimeout(1.0)
                with self._socket_lock:
                    self._socket = sock
                self.connected.set()
                self._increment("_connections")
                LOGGER.info("connected to Meshtastic TCP MUX at %s:%d", self.host, self.port)
                decoder = FrameDecoder(self.max_payload)
                while not self.stop_event.is_set():
                    try:
                        chunk = sock.recv(16384)
                    except TimeoutError:
                        continue
                    if not chunk:
                        raise ConnectionError("MUX closed the connection")
                    for frame in decoder.feed(chunk):
                        self.queue.put(frame.payload)
                        self._increment("_frames_from_mux")
            except (OSError, ConnectionError) as exc:
                if not self.stop_event.is_set():
                    self._increment("_errors")
                    LOGGER.warning("MUX connection unavailable: %s", exc)
            finally:
                was_connected = self.connected.is_set()
                self.connected.clear()
                with self._socket_lock:
                    if self._socket is sock:
                        self._socket = None
                if sock is not None:
                    sock.close()
                if was_connected:
                    self._increment("_disconnects")
            self.stop_event.wait(self.reconnect_seconds)

    def send(self, payload: bytes) -> None:
        if not payload:
            raise ValueError("ToRadio payload must not be empty")
        if len(payload) > self.max_payload:
            raise ValueError(f"ToRadio payload exceeds {self.max_payload} bytes")
        if self._is_config_request(payload):
            removed = self.queue.clear()
            if removed:
                LOGGER.info("cleared %d stale frames before configuration handshake", removed)
        if not self.connected.wait(self.send_timeout):
            raise BridgeUnavailable("TCP MUX is not connected")
        framed = frame_payload(payload)
        try:
            with self._socket_lock:
                if self._socket is None:
                    raise BridgeUnavailable("TCP MUX is not connected")
                self._socket.sendall(framed)
        except OSError as exc:
            self._increment("_errors")
            raise BridgeUnavailable(f"TCP MUX write failed: {exc}") from exc
        self._increment("_frames_to_mux")

    @staticmethod
    def _is_config_request(payload: bytes) -> bool:
        try:
            from meshtastic.protobuf import mesh_pb2

            message = mesh_pb2.ToRadio()
            message.ParseFromString(payload)
            return message.WhichOneof("payload_variant") == "want_config_id"
        except (DecodeError, ImportError):
            return False

    def receive(self, drain_all: bool) -> bytes:
        return self.queue.pop(drain_all)

    def snapshot(self) -> dict[str, int | float | bool | str]:
        with self._stats_lock:
            result: dict[str, int | float | bool | str] = {
                "status": "ok" if self.connected.is_set() else "degraded",
                "mux_connected": self.connected.is_set(),
                "mux_host": self.host,
                "mux_port": self.port,
                "uptime_seconds": round(time.monotonic() - self._started_at, 1),
                "connections": self._connections,
                "disconnects": self._disconnects,
                "frames_from_mux": self._frames_from_mux,
                "frames_to_mux": self._frames_to_mux,
                "errors": self._errors,
            }
        result.update(self.queue.snapshot())
        return result


class BridgeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ATLAS-Meshtastic-HTTP-Bridge/0.1"

    def __init__(self, *args: object, bridge: MuxBridge, **kwargs: object) -> None:
        self.bridge = bridge
        super().__init__(*args, **kwargs)

    def _cors_headers(self, methods: str) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", methods)
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def _respond(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = PROTOBUF_CONTENT_TYPE,
        methods: str = "GET, PUT, OPTIONS",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Protobuf-Schema", PROTOBUF_SCHEMA)
        self._cors_headers(methods)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/api/v1/toradio", "/api/v1/fromradio"):
            self._respond(404, content_type="text/plain; charset=utf-8")
            return
        self._respond(204)

    def do_GET(self) -> None:
        target = urlsplit(self.path)
        if target.path == "/api/v1/fromradio":
            drain_all = parse_qs(target.query).get("all", ["false"])[0].lower() == "true"
            self._respond(200, self.bridge.receive(drain_all), methods="GET, OPTIONS")
            return
        if target.path == "/healthz":
            body = (json.dumps(self.bridge.snapshot(), separators=(",", ":")) + "\n").encode()
            self._respond(
                200 if self.bridge.connected.is_set() else 503,
                body,
                content_type="application/json",
            )
            return
        if target.path == "/":
            body = b"ATLAS Meshtastic HTTP bridge\n"
            self._respond(200, body, content_type="text/plain; charset=utf-8")
            return
        self._respond(404, b"not found\n", content_type="text/plain; charset=utf-8")

    def do_PUT(self) -> None:
        if urlsplit(self.path).path != "/api/v1/toradio":
            self._respond(404, b"not found\n", content_type="text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 1:
            self._respond(400, b"ToRadio body is required\n", content_type="text/plain")
            return
        if length > self.bridge.max_payload:
            self._respond(413, b"ToRadio body is too large\n", content_type="text/plain")
            return
        payload = self.rfile.read(length)
        if len(payload) != length:
            self._respond(400, b"incomplete request body\n", content_type="text/plain")
            return
        try:
            self.bridge.send(payload)
        except (BridgeUnavailable, ValueError) as exc:
            self._respond(503, (str(exc) + "\n").encode(), content_type="text/plain")
            return
        # Match current firmware behavior: echo the accepted naked protobuf.
        self._respond(200, payload, methods="PUT, OPTIONS")

    def log_message(self, message: str, *args: object) -> None:
        LOGGER.debug("%s - %s", self.client_address[0], message % args)


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_server(host: str, port: int, bridge: MuxBridge) -> BridgeHTTPServer:
    return BridgeHTTPServer((host, port), partial(BridgeRequestHandler, bridge=bridge))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _mux_env(http_name: str, observer_name: str, default: str) -> str:
    return os.environ.get(http_name) or os.environ.get(observer_name) or default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge Meshtastic HTTP clients to a TCP MUX")
    parser.add_argument(
        "--mux-host", default=_mux_env("ATLAS_HTTP_MUX_HOST", "ATLAS_MUX_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--mux-port",
        type=int,
        default=int(_mux_env("ATLAS_HTTP_MUX_PORT", "ATLAS_MUX_PORT", "4405")),
    )
    parser.add_argument("--listen-host", default=_env("ATLAS_HTTP_LISTEN_HOST", "127.0.0.1"))
    parser.add_argument("--listen-port", type=int, default=int(_env("ATLAS_HTTP_LISTEN_PORT", "4404")))
    parser.add_argument("--queue-frames", type=int, default=int(_env("ATLAS_HTTP_QUEUE_FRAMES", "4096")))
    parser.add_argument(
        "--queue-bytes", type=int, default=int(_env("ATLAS_HTTP_QUEUE_BYTES", "2097152"))
    )
    parser.add_argument("--tls-cert", default=_env("ATLAS_HTTP_TLS_CERT", ""))
    parser.add_argument("--tls-key", default=_env("ATLAS_HTTP_TLS_KEY", ""))
    parser.add_argument("--log-level", default=_env("ATLAS_HTTP_LOG_LEVEL", "INFO"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.mux_port <= 65535 or not 1 <= args.listen_port <= 65535:
        raise SystemExit("ports must be between 1 and 65535")
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SystemExit("both --tls-cert and --tls-key are required for TLS")
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bridge = MuxBridge(
        args.mux_host,
        args.mux_port,
        queue_frames=args.queue_frames,
        queue_bytes=args.queue_bytes,
    )
    server = build_server(args.listen_host, args.listen_port, bridge)
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    scheme = "https" if args.tls_cert else "http"
    LOGGER.info(
        "serving Meshtastic HTTP API at %s://%s:%d", scheme, args.listen_host, args.listen_port
    )

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bridge.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
