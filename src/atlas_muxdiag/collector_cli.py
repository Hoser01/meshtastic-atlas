"""Live Phase 1 normalized collector entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capture import CaptureConfig, CaptureRunner, EventWriter, NullEventWriter
from .cli import positive_int
from .delivery import DurableEventWriter
from .normalize import EventNormalizer, parse_node_num


class NormalizedWriter:
    """Compatibility wrapper around the durable remote-observer writer."""

    def __init__(
        self,
        path: Path | None,
        api_url: str | None = None,
        api_token: str | None = None,
        *,
        spool: Path | None = None,
        output_max_bytes: int = 100 * 1024 * 1024,
        output_backups: int = 3,
        spool_max_events: int = 50_000,
        spool_max_bytes: int = 256 * 1024 * 1024,
        api_timeout: float = 5.0,
    ) -> None:
        self.stdout = path is None
        self.writer = DurableEventWriter(
            path,
            output_max_bytes=output_max_bytes,
            output_backups=output_backups,
            api_url=api_url,
            api_token=api_token,
            spool=spool,
            spool_max_events=spool_max_events,
            spool_max_bytes=spool_max_bytes,
            api_timeout=api_timeout,
        )

    def write(self, event: dict[str, Any]) -> None:
        if self.stdout:
            import json

            sys.stdout.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
            sys.stdout.flush()
        self.writer.write(event)

    def queue_depth(self) -> int:
        return self.writer.queue_depth()

    def close(self) -> None:
        self.writer.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="atlas-collector",
        description="Read-only ATLAS Phase 1 Meshtastic RF observation collector.",
    )
    result.add_argument("--host", required=True)
    result.add_argument("--port", type=int, default=4405)
    result.add_argument("--observer-id", required=True)
    result.add_argument(
        "--local-node",
        required=True,
        type=parse_node_num,
        help="observer node number as !hex, 0xhex, or decimal",
    )
    result.add_argument("--output", type=Path, help="normalized NDJSON (default: stdout)")
    result.add_argument("--output-max-bytes", type=positive_int, default=100 * 1024 * 1024)
    result.add_argument("--output-backups", type=int, default=3)
    result.add_argument("--api-url", help="optional ATLAS /api/v1/events ingestion URL")
    result.add_argument("--api-timeout", type=float, default=5.0)
    result.add_argument("--spool", type=Path, help="persistent central-delivery queue")
    result.add_argument("--spool-max-events", type=positive_int, default=50_000)
    result.add_argument("--spool-max-bytes", type=positive_int, default=256 * 1024 * 1024)
    result.add_argument("--heartbeat-seconds", type=float, default=60.0)
    result.add_argument("--diagnostic-output", type=Path, help="optional Phase 0 frame NDJSON")
    result.add_argument("--raw-output", type=Path, help="optional exact framed-byte capture")
    result.add_argument("--duration", type=float, default=0)
    result.add_argument("--max-frames", type=positive_int, default=0)
    result.add_argument("--max-raw-bytes", type=positive_int, default=0)
    result.add_argument("--correlation-window", type=float, default=600.0)
    result.add_argument("--enable-mqtt", action="store_true", help="explicitly enable MQTT output")
    result.add_argument("--mqtt-host")
    result.add_argument("--mqtt-port", type=int, default=1883)
    result.add_argument("--mqtt-prefix", default="atlas/test")
    result.add_argument("--mqtt-username")
    result.add_argument("--mqtt-password")
    result.add_argument("--mqtt-tls", action="store_true")
    result.add_argument("-v", "--verbose", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.duration < 0 or args.correlation_window <= 0 or args.heartbeat_seconds < 0:
        parser().error("duration must be nonnegative and correlation window must be positive")
    if args.output_backups < 0 or args.api_timeout <= 0:
        parser().error("output backups must be nonnegative and API timeout must be positive")
    if args.enable_mqtt and not args.mqtt_host:
        parser().error("--enable-mqtt requires --mqtt-host")
    api_token = os.environ.get("ATLAS_INGEST_TOKEN")
    if args.api_url and not api_token:
        parser().error("--api-url requires ATLAS_INGEST_TOKEN in the environment")
    if args.api_url and args.spool is None:
        parser().error("--api-url requires --spool for resilient delivery")
    for path in (args.output, args.diagnostic_output, args.raw_output, args.spool):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    output = NormalizedWriter(
        args.output,
        args.api_url,
        api_token,
        spool=args.spool,
        output_max_bytes=args.output_max_bytes,
        output_backups=args.output_backups,
        spool_max_events=args.spool_max_events,
        spool_max_bytes=args.spool_max_bytes,
        api_timeout=args.api_timeout,
    )
    mqtt_output = None
    if args.enable_mqtt:
        from .mqtt_output import MqttPublisher

        mqtt_output = MqttPublisher(
            args.mqtt_host,
            args.mqtt_port,
            args.mqtt_prefix,
            f"atlas-{args.observer_id}",
            args.mqtt_username,
            args.mqtt_password,
            args.mqtt_tls,
        )
    normalizer = EventNormalizer(args.observer_id, args.local_node, args.correlation_window)

    def normalize(record: dict[str, Any]) -> None:
        for event in normalizer.process(record):
            output.write(event)
            if mqtt_output is not None:
                mqtt_output.write(event)

    diagnostic = (
        EventWriter(args.diagnostic_output, args.raw_output)
        if args.diagnostic_output is not None
        else NullEventWriter()
    )
    if args.raw_output is not None and args.diagnostic_output is None:
        parser().error("--raw-output requires --diagnostic-output")

    runner = CaptureRunner(
        CaptureConfig(
            host=args.host,
            port=args.port,
            observer_id=args.observer_id,
            local_node_num=args.local_node,
            duration=args.duration,
            max_frames=args.max_frames,
            max_raw_bytes=args.max_raw_bytes,
        ),
        diagnostic,
        normalize,
    )
    signal.signal(signal.SIGINT, lambda *_: runner.stop())
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    heartbeat_stop = threading.Event()

    def heartbeat() -> None:
        while args.heartbeat_seconds and not heartbeat_stop.wait(args.heartbeat_seconds):
            output.write(
                {
                    "schema_version": 1,
                    "event_id": uuid.uuid4().hex,
                    "event": "collector_heartbeat",
                    "observer_id": args.observer_id,
                    "observer_node_num": args.local_node,
                    "observer_node_id": f"!{args.local_node:08x}",
                    "observed_at": datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                    "source": "UNKNOWN",
                    "collector_uptime_seconds": round(
                        time.monotonic() - runner.started_monotonic, 1
                    ),
                    "delivery_queue_depth": output.queue_depth(),
                    "mux_connected": runner.connected,
                    "mux_frames": runner.frames,
                    "connection_epoch": runner.connection_epoch,
                }
            )

    heartbeat_thread = threading.Thread(target=heartbeat, name="atlas-heartbeat", daemon=True)
    heartbeat_thread.start()
    try:
        return runner.run()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        diagnostic.close()
        if mqtt_output is not None:
            mqtt_output.close()
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
