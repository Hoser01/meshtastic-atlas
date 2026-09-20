"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from .capture import CaptureConfig, CaptureRunner, EventWriter


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="atlas-muxdiag",
        description="Read-only capture and decode of a Meshtastic TCP MUX stream.",
    )
    result.add_argument("--host", required=True, help="MUX hostname or IP address")
    result.add_argument("--port", type=int, default=4405)
    result.add_argument("--observer-id", default="UNSET")
    result.add_argument("--json-output", type=Path, help="append NDJSON here (default: stdout)")
    result.add_argument("--raw-output", type=Path, help="append exact framed bytes here")
    result.add_argument("--max-frames", type=positive_int, default=0, help="0 means unlimited")
    result.add_argument("--duration", type=float, default=0, help="seconds; 0 means unlimited")
    result.add_argument("--max-raw-bytes", type=positive_int, default=0, help="0 means unlimited")
    result.add_argument("--include-payload-base64", action="store_true")
    result.add_argument("--connect-timeout", type=float, default=8.0)
    result.add_argument("--read-timeout", type=float, default=5.0)
    result.add_argument("--reconnect-initial", type=float, default=1.0)
    result.add_argument("--reconnect-max", type=float, default=30.0)
    result.add_argument("--max-payload", type=int, default=512)
    result.add_argument("-v", "--verbose", action="count", default=0)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.duration < 0:
        parser().error("--duration must be zero or greater")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for path in (args.json_output, args.raw_output):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
    config = CaptureConfig(
        host=args.host,
        port=args.port,
        observer_id=args.observer_id,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        reconnect_initial=args.reconnect_initial,
        reconnect_max=args.reconnect_max,
        max_frames=args.max_frames,
        duration=args.duration,
        max_raw_bytes=args.max_raw_bytes,
        max_payload=args.max_payload,
        include_payload_base64=args.include_payload_base64,
    )
    writer = EventWriter(args.json_output, args.raw_output)
    runner = CaptureRunner(config, writer)
    signal.signal(signal.SIGINT, lambda *_: runner.stop())
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    try:
        return runner.run()
    finally:
        writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
