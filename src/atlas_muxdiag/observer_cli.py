"""Environment-driven remote observer service entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .collector_cli import main as collector_main


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"required observer setting is missing: {name}")
    return value


def main() -> int:
    observer_id = required("ATLAS_OBSERVER_ID")
    local_node = required("ATLAS_LOCAL_NODE")
    mux_host = os.environ.get("ATLAS_MUX_HOST", "127.0.0.1")
    mux_port = os.environ.get("ATLAS_MUX_PORT", "4405")
    state_dir = Path(os.environ.get("ATLAS_STATE_DIR", "/var/lib/atlas-observer"))
    api_url = required("ATLAS_API_URL")
    if "--check-config" in sys.argv:
        if not api_url.startswith("https://") and api_url not in {
            "http://127.0.0.1:18081/api/v1/events",
            "http://localhost:18081/api/v1/events",
        }:
            raise SystemExit("ATLAS_API_URL must use HTTPS for a remote observer")
        int(mux_port)
        return 0
    state_dir.mkdir(parents=True, exist_ok=True)
    sys.argv = [
        "atlas-collector",
        "--host",
        mux_host,
        "--port",
        mux_port,
        "--observer-id",
        observer_id,
        "--local-node",
        local_node,
        "--api-url",
        api_url,
        "--spool",
        str(state_dir / "delivery.db"),
        "--spool-max-events",
        os.environ.get("ATLAS_SPOOL_MAX_EVENTS", "50000"),
        "--spool-max-bytes",
        os.environ.get("ATLAS_SPOOL_MAX_BYTES", "268435456"),
        "--output",
        str(state_dir / "events.ndjson"),
        "--output-max-bytes",
        os.environ.get("ATLAS_OUTPUT_MAX_BYTES", "104857600"),
        "--output-backups",
        os.environ.get("ATLAS_OUTPUT_BACKUPS", "3"),
        "--heartbeat-seconds",
        os.environ.get("ATLAS_HEARTBEAT_SECONDS", "60"),
    ]
    return collector_main()


if __name__ == "__main__":
    raise SystemExit(main())
