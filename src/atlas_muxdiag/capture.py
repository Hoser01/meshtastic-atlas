"""Connection, capture, and output orchestration."""

from __future__ import annotations

import base64
import json
import logging
import random
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Protocol

from google.protobuf.message import DecodeError

from . import __version__
from .decode import ProtobufDecoder
from .framing import Frame, FrameDecoder, stream_subscription_frame

LOG = logging.getLogger("atlas_muxdiag")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class CaptureConfig:
    host: str
    port: int = 4405
    observer_id: str = "UNSET"
    connect_timeout: float = 8.0
    read_timeout: float = 5.0
    reconnect_initial: float = 1.0
    reconnect_max: float = 30.0
    max_frames: int = 0
    duration: float = 0.0
    max_raw_bytes: int = 0
    recv_bytes: int = 4096
    max_payload: int = 512
    include_payload_base64: bool = False
    local_node_num: int | None = None


class EventSink(Protocol):
    raw_bytes: int

    def event(self, value: dict[str, Any]) -> None: ...
    def raw(self, frame: Frame, maximum: int) -> bool: ...


class NullEventWriter:
    """Discard diagnostic records while allowing normalized callback output."""

    raw_bytes = 0

    def event(self, value: dict[str, Any]) -> None:
        pass

    def raw(self, frame: Frame, maximum: int) -> bool:
        return True

    def close(self) -> None:
        pass


class EventWriter:
    def __init__(self, json_path: Path | None, raw_path: Path | None) -> None:
        self._owns_json = json_path is not None
        self.json_stream: IO[str] = (
            json_path.open("a", encoding="utf-8", buffering=1) if json_path else sys.stdout
        )
        self.raw_stream: IO[bytes] | None = raw_path.open("ab", buffering=0) if raw_path else None
        self.raw_bytes = 0

    def event(self, value: dict[str, Any]) -> None:
        self.json_stream.write(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")
        self.json_stream.flush()

    def raw(self, frame: Frame, maximum: int) -> bool:
        if self.raw_stream is None:
            return True
        if maximum and self.raw_bytes + len(frame.raw) > maximum:
            return False
        self.raw_stream.write(frame.raw)
        self.raw_bytes += len(frame.raw)
        return True

    def close(self) -> None:
        if self._owns_json:
            self.json_stream.close()
        if self.raw_stream:
            self.raw_stream.close()


class CaptureRunner:
    def __init__(
        self,
        config: CaptureConfig,
        writer: EventSink,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.decoder = ProtobufDecoder(config.local_node_num)
        self.event_callback = event_callback
        self.started_monotonic = time.monotonic()
        self.frames = 0
        self.connection_epoch = 0
        self.stop_requested = False
        self.connected = False

    def stop(self) -> None:
        self.stop_requested = True

    def run(self) -> int:
        backoff = self.config.reconnect_initial
        self._event(
            "capture_started",
            version=__version__,
            read_only=True,
            live_stream_subscription=True,
        )
        try:
            while not self.stop_requested and not self._limits_reached():
                self.connection_epoch += 1
                try:
                    self._capture_connection()
                    backoff = self.config.reconnect_initial
                except (ConnectionError, OSError, TimeoutError) as exc:
                    self._event("connection_error", error=f"{type(exc).__name__}: {exc}")
                    if self._limits_reached() or self.stop_requested:
                        break
                    delay = min(self.config.reconnect_max, backoff)
                    delay *= random.uniform(0.8, 1.2)
                    self._event("reconnect_wait", delay_seconds=round(delay, 3))
                    self._interruptible_wait(delay)
                    backoff = min(self.config.reconnect_max, max(0.1, backoff * 2))
        finally:
            self._event(
                "capture_stopped",
                frames=self.frames,
                elapsed_seconds=round(time.monotonic() - self.started_monotonic, 3),
                raw_bytes=self.writer.raw_bytes,
            )
        return 0

    def _capture_connection(self) -> None:
        cfg = self.config
        self._event("connecting")
        with socket.create_connection((cfg.host, cfg.port), timeout=cfg.connect_timeout) as sock:
            self.connected = True
            sock.settimeout(min(cfg.read_timeout, 1.0) if cfg.duration else cfg.read_timeout)
            peer = sock.getpeername()
            self._event("connected", peer=f"{peer[0]}:{peer[1]}")
            sock.sendall(stream_subscription_frame())
            self._event("stream_subscription_requested", nodeless=True, mutates_radio=False)
            LOG.info(
                "connected read-only to %s:%d and requested live stream (epoch %d)",
                cfg.host,
                cfg.port,
                self.connection_epoch,
            )
            parser = FrameDecoder(cfg.max_payload)

            try:
                while not self.stop_requested and not self._limits_reached():
                    try:
                        chunk = sock.recv(cfg.recv_bytes)
                    except TimeoutError:
                        continue
                    if not chunk:
                        raise ConnectionError("peer closed the stream")
                    for frame in parser.feed(chunk):
                        self._handle_frame(frame)
                        if self._limits_reached():
                            break
            finally:
                self.connected = False

    def _handle_frame(self, frame: Frame) -> None:
        self.frames += 1
        event: dict[str, Any] = {
            "event": "frame",
            "captured_at": utc_now(),
            "observer_id": self.config.observer_id,
            "connection_epoch": self.connection_epoch,
            "frame_sequence": self.frames,
            "frame_bytes": len(frame.raw),
            "payload_bytes": len(frame.payload),
            "discarded_before": frame.discarded_before,
        }
        if self.config.include_payload_base64:
            event["payload_base64"] = base64.b64encode(frame.payload).decode("ascii")
        try:
            event.update(self.decoder.decode(frame.payload))
            summary = event.get("packet_summary")
            if summary:
                LOG.info(
                    "packet id=%s from=%s type=%s hint=%s rssi=%s snr=%s",
                    summary["id"],
                    summary["from"],
                    summary.get("portnum", summary["payload_variant"]),
                    summary["provenance_hint"],
                    summary["rx_rssi"],
                    summary["rx_snr"],
                )
            else:
                if event.get("payload_variant") == "my_info":
                    LOG.info("FromRadio my_info local_node_num=%s", event.get("local_node_num"))
                else:
                    LOG.info("FromRadio %s", event.get("payload_variant"))
        except DecodeError as exc:
            event["decode_error"] = f"{type(exc).__name__}: {exc}"
            LOG.warning("protobuf decode failed for frame %d: %s", self.frames, exc)
        if not self.writer.raw(frame, self.config.max_raw_bytes):
            event["raw_capture_skipped"] = "max_raw_bytes would be exceeded"
        self._write_event(event)

    def _event(self, kind: str, **fields: Any) -> None:
        self._write_event(
            {
                "event": kind,
                "captured_at": utc_now(),
                "observer_id": self.config.observer_id,
                "connection_epoch": self.connection_epoch,
                "target": f"{self.config.host}:{self.config.port}",
                **fields,
            }
        )

    def _write_event(self, event: dict[str, Any]) -> None:
        self.writer.event(event)
        if self.event_callback is not None:
            self.event_callback(event)

    def _limits_reached(self) -> bool:
        if self.config.max_frames and self.frames >= self.config.max_frames:
            return True
        return bool(
            self.config.duration
            and time.monotonic() - self.started_monotonic >= self.config.duration
        )

    def _interruptible_wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while (
            not self.stop_requested and not self._limits_reached() and time.monotonic() < deadline
        ):
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
