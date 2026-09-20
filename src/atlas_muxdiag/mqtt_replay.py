"""Replay safe MQTT analysis NDJSON into normalized ATLAS events."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from .collector_cli import NormalizedWriter
from .mqtt_normalize import MqttEventNormalizer

LOG = logging.getLogger("atlas_mqtt_replay")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Replay MQTT diagnostic records into ATLAS")
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--output", type=Path)
    result.add_argument("--api-url", help="optional ATLAS /api/v1/events ingestion URL")
    return result


def main() -> int:
    args = parser().parse_args()
    api_token = os.environ.get("ATLAS_INGEST_TOKEN")
    if args.api_url and not api_token:
        parser().error("--api-url requires ATLAS_INGEST_TOKEN in the environment")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = NormalizedWriter(args.output, args.api_url, api_token)
    normalizer = MqttEventNormalizer()
    records = events = malformed = 0
    try:
        with args.input.open(encoding="utf-8") as stream:
            for line in stream:
                records += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                for event in normalizer.process(record):
                    writer.write(event)
                    events += 1
    finally:
        writer.close()
    LOG.warning("replay complete: records=%d events=%d malformed=%d", records, events, malformed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
