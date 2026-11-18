"""Meshtastic HTTP(S) API sidecar for an existing Meshtastic TCP MUX.

No third-party Python packages are required. Each client IP + Origin has its
own bounded receive queue and persistent downstream MUX connection. The MUX
continues to own the single connection to the physical radio.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import ssl
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__

VERSION = __version__
MAGIC = b"\x94\xc3"
MAX_PAYLOAD = 512
PROTO_TYPE = "application/x-protobuf"
SCHEMA = "https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto"
LOGGER = logging.getLogger("meshtastic_http_bridge")


def origin_of(value: str, *, allow_path: bool = False) -> str:
    """Canonicalize an HTTP origin, rejecting credentials and opaque origins."""
    if not value or any(ord(c) < 33 for c in value):
        raise ValueError("A complete http:// or https:// web instance URL is required")
    url = urlsplit(value)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or (not allow_path and (url.path not in ("", "/") or url.query or url.fragment))
    ):
        raise ValueError("Expected an HTTP(S) URL without credentials")
    host = url.hostname.encode("idna").decode("ascii").lower()
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-:_" for c in host):
        raise ValueError("Invalid hostname in web instance URL")
    port = url.port  # Also validates malformed/out-of-range ports.
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and port != (443 if url.scheme == "https" else 80):
        host += ":" + str(port)
    return url.scheme + "://" + host


@dataclass(frozen=True)
class Settings:
    http_host: str = "0.0.0.0"
    http_port: int = 4407
    mux_host: str = "127.0.0.1"
    mux_port: int = 4405
    allowed_origins: tuple[str, ...] = ()
    https_port: int = 0
    tls_cert: str = ""
    tls_key: str = ""
    max_sessions: int = 8
    max_queue_frames: int = 2048
    max_queue_bytes: int = 1048576
    idle_seconds: float = 120.0
    connect_timeout: float = 2.0
    max_http_threads: int = 32

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        origins = tuple(
            origin_of(v.strip())
            for v in e.get("BRIDGE_ALLOWED_ORIGINS", "").split(",")
            if v.strip()
        )
        result = cls(
            http_host=e.get("BRIDGE_HTTP_HOST", "0.0.0.0"),
            http_port=int(e.get("BRIDGE_HTTP_PORT", "4407")),
            mux_host=e.get("BRIDGE_MUX_HOST", "127.0.0.1"),
            mux_port=int(e.get("BRIDGE_MUX_PORT", "4405")),
            allowed_origins=origins,
            https_port=int(e.get("BRIDGE_HTTPS_PORT", "0")),
            tls_cert=e.get("BRIDGE_TLS_CERT", ""),
            tls_key=e.get("BRIDGE_TLS_KEY", ""),
            max_sessions=int(e.get("BRIDGE_MAX_SESSIONS", "8")),
            max_queue_frames=int(e.get("BRIDGE_MAX_QUEUE_FRAMES", "2048")),
            max_queue_bytes=int(e.get("BRIDGE_MAX_QUEUE_BYTES", "1048576")),
            idle_seconds=float(e.get("BRIDGE_IDLE_SECONDS", "120")),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not self.allowed_origins:
            raise ValueError("BRIDGE_ALLOWED_ORIGINS must list your Meshtastic Web origin")
        if any(origin_of(v) != v for v in self.allowed_origins):
            raise ValueError("Allowed origins must be canonical HTTP(S) origins")
        if any(not 1 <= p <= 65535 for p in (self.http_port, self.mux_port)):
            raise ValueError("HTTP and MUX ports must be between 1 and 65535")
        if not 0 <= self.https_port <= 65535:
            raise ValueError("HTTPS port must be 0 (disabled) or a valid port")
        ports = [self.http_port, self.mux_port] + ([self.https_port] if self.https_port else [])
        if len(ports) != len(set(ports)):
            raise ValueError("HTTP, HTTPS and MUX must use different ports")
        if self.https_port and not (self.tls_cert and self.tls_key):
            raise ValueError("HTTPS requires BRIDGE_TLS_CERT and BRIDGE_TLS_KEY")
        if (
            not self.http_host
            or not self.mux_host
            or self.idle_seconds <= 0
            or min(
                self.max_sessions,
                self.max_queue_frames,
                self.max_queue_bytes,
                self.max_http_threads,
            )
            <= 0
        ):
            raise ValueError("Hosts and resource limits must be nonempty/positive")


class Unavailable(Exception):
    """Safe, payload-free error suitable for an HTTP 503 response."""


class FrameParser:
    """Handle fragmented/coalesced TCP frames without exposing stream headers."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(MAGIC)
            if start < 0:
                # Keep a possible first magic byte split across recv() calls.
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == MAGIC[:1] else b""
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            length = struct.unpack(">H", self.buffer[2:4])[0]
            if length > MAX_PAYLOAD:
                del self.buffer[0]
                continue
            if len(self.buffer) < 4 + length:
                break
            payload = bytes(self.buffer[4 : 4 + length])
            del self.buffer[: 4 + length]
            if payload:
                frames.append(payload)
        return frames


def stream_frame(payload: bytes) -> bytes:
    if not 1 <= len(payload) <= MAX_PAYLOAD:
        raise ValueError("A ToRadio payload must contain 1 to 512 bytes")
    return MAGIC + struct.pack(">H", len(payload)) + payload


class Session:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.queue: deque[bytes] = deque()
        self.queue_bytes = 0
        self.last_seen = time.monotonic()
        self.error: str | None = None
        self.sock: socket.socket | None = None
        self.reader: threading.Thread | None = None

    def touch(self) -> None:
        with self.lock:
            self.last_seen = time.monotonic()

    def failed(self) -> bool:
        with self.lock:
            return self.error is not None

    def _connect_locked(self) -> socket.socket:
        # Caller holds io_lock; never retry a write after a partial send.
        with self.lock:
            if self.error:
                raise Unavailable(self.error)
            if self.sock is not None:
                return self.sock
        try:
            sock = socket.create_connection(
                (self.settings.mux_host, self.settings.mux_port),
                timeout=self.settings.connect_timeout,
            )
            sock.settimeout(1.0)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            self.close("MUX connection failed; reconnect the web client")
            raise Unavailable("MUX connection failed; reconnect the web client") from None
        with self.lock:
            if self.error:
                sock.close()
                raise Unavailable(self.error)
            self.sock = sock
            self.reader = threading.Thread(target=self._read_loop, args=(sock,), daemon=True)
            self.reader.start()
        return sock

    def ensure_connected(self) -> None:
        with self.io_lock:
            self._connect_locked()

    def send(self, payload: bytes) -> None:
        framed = stream_frame(payload)
        with self.io_lock:
            sock = self._connect_locked()
            try:
                sock.sendall(framed)
            except OSError:
                self.close("MUX write failed; delivery is unknown; reconnect the web client")
                raise Unavailable(
                    "MUX write failed; delivery is unknown; reconnect the web client"
                ) from None

    def receive(self, all_messages: bool = False) -> bytes:
        self.ensure_connected()
        with self.lock:
            if self.error:
                raise Unavailable(self.error)
            if not self.queue:
                return b""
            if all_messages:
                result = b"".join(self.queue)
                self.queue.clear()
                self.queue_bytes = 0
                return result
            result = self.queue.popleft()
            self.queue_bytes -= len(result)
            return result

    def _read_loop(self, sock: socket.socket) -> None:
        parser = FrameParser()
        try:
            while not self.failed():
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    break
                for payload in parser.feed(data):
                    with self.lock:
                        if self.error:
                            return
                        overflow = (
                            len(self.queue) >= self.settings.max_queue_frames
                            or self.queue_bytes + len(payload) > self.settings.max_queue_bytes
                        )
                        if not overflow:
                            self.queue.append(payload)
                            self.queue_bytes += len(payload)
                    if overflow:
                        self.close("Receive queue overflow; reconnect the web client")
                        return
        except OSError:
            pass
        finally:
            self.close("MUX disconnected; reconnect the web client")

    def close(self, reason: str) -> None:
        with self.lock:
            if self.error:
                return
            self.error = reason
            self.queue.clear()
            self.queue_bytes = 0
            sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class Sessions:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.Lock()
        self.items: dict[tuple[str, str], Session] = {}
        self.stopped = threading.Event()
        self.janitor = threading.Thread(target=self._reap_loop, daemon=True)
        self.janitor.start()

    def get(self, key: tuple[str, str], *, writing: bool = False) -> Session:
        with self.lock:
            if self.stopped.is_set():
                raise Unavailable("Bridge is stopping")
            self._reap_locked()
            session = self.items.get(key)
            # A fresh PUT (normally want_config_id) starts a new connection after
            # failure. GETs keep returning 503 so an outage is not hidden.
            if session is not None and writing and session.failed():
                del self.items[key]
                session = None
            if session is None:
                if len(self.items) >= self.settings.max_sessions:
                    raise Unavailable("Web client session limit reached")
                session = self.items[key] = Session(self.settings)
            session.touch()
            return session

    def _reap_locked(self) -> None:
        now = time.monotonic()
        for key, session in list(self.items.items()):
            with session.lock:
                expired = now - session.last_seen >= self.settings.idle_seconds
            if expired:
                del self.items[key]
                session.close("Web client session expired")

    def _reap_loop(self) -> None:
        while not self.stopped.wait(min(5.0, self.settings.idle_seconds)):
            with self.lock:
                self._reap_locked()

    def close(self) -> None:
        self.stopped.set()
        with self.lock:
            sessions = list(self.items.values())
            self.items.clear()
        for session in sessions:
            session.close("Bridge is stopping")
        self.janitor.join(timeout=2)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], sessions: Sessions) -> None:
        self.sessions = sessions
        self.tls_context: ssl.SSLContext | None = None
        self.slots = threading.BoundedSemaphore(sessions.settings.max_http_threads)
        super().__init__(address, Handler)

    def get_request(self) -> tuple[socket.socket, tuple]:
        request, address = super().get_request()
        if self.tls_context is not None:
            try:
                # The handshake happens in a bounded worker, not the accept loop.
                request = self.tls_context.wrap_socket(
                    request, server_side=True, do_handshake_on_connect=False
                )
            except Exception:
                request.close()
                raise
        return request, address

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self.slots.acquire(blocking=False):
            # Do not create more threads or block the accept loop on a slow peer.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: socket.socket, client_address: tuple) -> None:
        LOGGER.warning("HTTP client connection ended unexpectedly")


class Handler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"
    server_version = "MeshtasticHTTPBridge/" + VERSION
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)
        if isinstance(self.connection, ssl.SSLSocket):
            self.connection.do_handshake()

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log protobufs, request bodies, tokens, or query strings.
        LOGGER.debug("HTTP request completed")

    def _origin(self) -> str:
        values = self.headers.get_all("Origin", [])
        if len(values) > 1:
            raise ValueError("Multiple Origin headers are not supported")
        if not values:
            # Normal curl/CLI requests have no Origin. Reject browser no-cors
            # subresource requests so they cannot drain a session's queue.
            if self.headers.get("Sec-Fetch-Mode") == "no-cors":
                raise ValueError("Cross-origin API requests must use CORS")
            return ""
        origin = origin_of(values[0])
        if origin not in self.server.sessions.settings.allowed_origins:
            raise ValueError("Web origin is not allowed; update BRIDGE_ALLOWED_ORIGINS")
        return origin

    def _reply(
        self,
        status: int,
        body: bytes = b"",
        kind: str = "application/json",
        origin: str = "",
        *,
        preflight: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        if preflight:
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        if kind == PROTO_TYPE:
            self.send_header("X-Protobuf-Schema", SCHEMA)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: dict, origin: str = "") -> None:
        self._reply(status, json.dumps(value, separators=(",", ":")).encode(), origin=origin)

    def _dispatch(self) -> None:
        origin = ""
        try:
            if len(self.path) > 4096:
                self._json(414, {"error": "Request URL is too long"})
                return
            url = urlsplit(self.path)
            if url.scheme or url.netloc:
                self._json(400, {"error": "Absolute request targets are not supported"})
                return
            if url.path in ("/", "/healthz") and self.command in ("GET", "HEAD"):
                # Reachability checks never create a session or drain radio data.
                raw_origin = self.headers.get("Origin", "")
                if raw_origin:
                    origin = self._origin()
                if url.path == "/healthz":
                    settings = self.server.sessions.settings
                    try:
                        with socket.create_connection(
                            (settings.mux_host, settings.mux_port), timeout=settings.connect_timeout
                        ):
                            pass
                        reachable = True
                    except OSError:
                        reachable = False
                    self._json(
                        200 if reachable else 503,
                        {
                            "service": "meshtastic-http-bridge",
                            "version": VERSION,
                            "mux_tcp_reachable": reachable,
                            "radio_link_verified": False,
                        },
                        origin,
                    )
                else:
                    self._json(
                        200,
                        {
                            "service": "meshtastic-http-bridge",
                            "version": VERSION,
                            "api": ["/api/v1/toradio", "/api/v1/fromradio"],
                        },
                        origin,
                    )
                return
            if url.path not in ("/api/v1/toradio", "/api/v1/fromradio"):
                self._json(404, {"error": "Unknown endpoint"})
                return
            origin = self._origin()
            if self.command == "OPTIONS":
                requested = {
                    v.strip().lower()
                    for v in self.headers.get("Access-Control-Request-Headers", "").split(",")
                    if v.strip()
                }
                if requested - {"content-type", "accept"}:
                    self._json(400, {"error": "Unsupported CORS request header"}, origin)
                    return
                self._reply(204, origin=origin, preflight=True)
                return
            key = (self.client_address[0], origin)
            # The direct peer is deliberate: never trust forwarded-IP headers
            # from LAN clients. Proxy deployments require a separate instance
            # per user, as described in README.md.
            if self.command == "GET" and url.path == "/api/v1/fromradio":
                query = parse_qs(url.query)
                if query.get("chunked", ["false"])[0].lower() == "true":
                    self._json(400, {"error": "Chunked API streaming is not supported"}, origin)
                    return
                session = self.server.sessions.get(key)
                payload = session.receive(query.get("all", ["false"])[0].lower() == "true")
                self._reply(200, payload, PROTO_TYPE, origin)
                return
            if self.command == "PUT" and url.path == "/api/v1/toradio":
                if self.headers.get("Transfer-Encoding") is not None:
                    self.close_connection = True
                    self._json(
                        400,
                        {"error": "Use Content-Length; chunked request bodies are unsupported"},
                        origin,
                    )
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1:
                    self.close_connection = True
                    self._json(411, {"error": "One Content-Length header is required"}, origin)
                    return
                try:
                    length = int(lengths[0])
                except ValueError:
                    length = -1
                if not 1 <= length <= MAX_PAYLOAD:
                    self.close_connection = True
                    self._json(
                        413 if length > MAX_PAYLOAD else 400,
                        {"error": "A ToRadio body must contain 1 to 512 bytes"},
                        origin,
                    )
                    return
                if self.headers.get_content_type() not in (PROTO_TYPE, "application/octet-stream"):
                    self.close_connection = True
                    self._json(415, {"error": "Use Content-Type: application/x-protobuf"}, origin)
                    return
                payload = self.rfile.read(length)
                if len(payload) != length:
                    self.close_connection = True
                    self._json(400, {"error": "Incomplete request body"}, origin)
                    return
                session = self.server.sessions.get(key, writing=True)
                session.send(payload)
                # Matches the device API's byte-for-byte PUT echo. This means
                # handed to MUX TCP, not RF acknowledgement or filter approval.
                self._reply(200, payload, PROTO_TYPE, origin)
                return
            self._json(405, {"error": "Method is not supported for this endpoint"}, origin)
        except ValueError:
            self.close_connection = True
            self._json(403, {"error": "Invalid or disallowed web origin/request"})
        except Unavailable as exc:
            self._json(503, {"error": str(exc)}, origin)
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_PUT = _dispatch
    do_OPTIONS = _dispatch
    do_POST = _dispatch
    do_DELETE = _dispatch


def build_servers(settings: Settings, sessions: Sessions) -> list[BridgeServer]:
    servers: list[BridgeServer] = []
    try:
        servers.append(BridgeServer((settings.http_host, settings.http_port), sessions))
        if settings.https_port:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(settings.tls_cert, settings.tls_key)
            server = BridgeServer((settings.http_host, settings.https_port), sessions)
            servers.append(server)
            server.tls_context = context
        return servers
    except Exception:
        for server in servers:
            server.server_close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    if args.check_config:
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "http_port": settings.http_port,
                    "https_port": settings.https_port,
                    "mux": f"{settings.mux_host}:{settings.mux_port}",
                    "allowed_origins": settings.allowed_origins,
                }
            )
        )
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stop.set())
    sessions = Sessions(settings)
    servers: list[BridgeServer] = []
    threads: list[threading.Thread] = []
    try:
        servers = build_servers(settings, sessions)
        for server in servers:
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
            )
            thread.start()
            threads.append(thread)
        LOGGER.info(
            "Bridge %s ready: HTTP=%d HTTPS=%d; MUX=%s:%d",
            VERSION,
            settings.http_port,
            settings.https_port,
            settings.mux_host,
            settings.mux_port,
        )
        while not stop.wait(1.0):
            if not all(thread.is_alive() for thread in threads):
                raise RuntimeError("HTTP listener exited unexpectedly")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        sessions.close()


if __name__ == "__main__":
    main()
