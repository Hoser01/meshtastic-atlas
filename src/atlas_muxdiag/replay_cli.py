"""Replay Phase 0 diagnostic NDJSON through the Phase 1 normalizer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .collector_cli import NormalizedWriter
from .normalize import EventNormalizer, parse_node_num


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="atlas-replay",
        description="Normalize a saved atlas-muxdiag NDJSON capture.",
    )
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--output", type=Path, help="normalized NDJSON (default: stdout)")
    result.add_argument("--observer-id", required=True)
    result.add_argument("--local-node", required=True, type=parse_node_num)
    result.add_argument("--correlation-window", type=float, default=600.0)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.correlation_window <= 0:
        parser().error("--correlation-window must be positive")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    normalizer = EventNormalizer(args.observer_id, args.local_node, args.correlation_window)
    output = NormalizedWriter(args.output)
    try:
        with args.input.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"{args.input}:{line_number}: invalid JSON: {exc}") from exc
                for event in normalizer.process(record):
                    output.write(event)
    finally:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
