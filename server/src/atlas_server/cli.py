"""Offline NDJSON ingestion CLI for the central development store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .store import AtlasStore, EventValidationError


def main() -> int:
    parser = argparse.ArgumentParser(prog="atlas-server-ingest")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = AtlasStore(args.database)
    accepted = duplicates = 0
    try:
        with args.input.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if store.ingest(event):
                        accepted += 1
                    else:
                        duplicates += 1
                except (json.JSONDecodeError, EventValidationError) as exc:
                    raise SystemExit(f"{args.input}:{line_number}: {exc}") from exc
        print(json.dumps({"accepted": accepted, "duplicates": duplicates, **store.stats()}))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
