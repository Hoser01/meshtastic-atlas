"""Consume the MUX metadata-only audit stream and emit ATLAS events."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any

from .collector_cli import NormalizedWriter
from .normalize import parse_node_num

LOG = logging.getLogger("atlas_mux_audit")


def normalize_audit(
    record: dict[str, Any], observer_id: str, observer_node: int
) -> dict[str, Any] | None:
    """Convert safe outbound/lifecycle audit metadata into an ATLAS event."""
    record_type = record.get("event")
    packet_id = record.get("packet_id")
    if record_type == "client_frame" and record.get("direction") == "client_to_radio":
        if record.get("disposition") != "queued":
            return None
        status = "QUEUED"
    elif record_type == "forward_result" and record.get("direction") == "client_to_radio":
        status = "FORWARDED" if record.get("disposition") == "forwarded" else "FAILED"
    elif record_type == "queue_status":
        queue = record.get("queue")
        if (
            not isinstance(queue, dict)
            or not isinstance(queue.get("mesh_packet_id"), int)
            or not queue["mesh_packet_id"]
        ):
            return None
        packet_id = queue["mesh_packet_id"]
        status = "RADIO_ACCEPTED" if queue.get("result") == 0 else "FAILED"
    elif record_type == "radio_frame" and record.get("application") == "ROUTING_APP":
        request_id = record.get("request_id") or record.get("reply_id")
        if not isinstance(request_id, int) or not request_id:
            return None
        packet_id = request_id
        status = "RESPONSE_RECEIVED"
    else:
        return None
    if not isinstance(packet_id, int):
        return None
    source = record.get("from_node")
    destination = record.get("to_node")
    if status == "QUEUED" and (not isinstance(source, int) or not isinstance(destination, int)):
        return None
    stable = "|".join(
        str(record.get(key, ""))
        for key in (
            "event",
            "disposition",
            "observed_at",
            "client_id",
            "packet_id",
            "from_node",
            "to_node",
        )
    )
    event_id = hashlib.sha256(f"mux-audit|{stable}".encode()).hexdigest()[:32]
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "event": "local_transmission" if status == "QUEUED" else "packet_lifecycle",
        "observer_id": observer_id,
        "observer_node_num": observer_node,
        "observer_node_id": f"!{observer_node & 0xFFFFFFFF:08x}",
        "observed_at": record["observed_at"],
        "source": "LOCAL_TX",
        "packet_id": packet_id,
        "lifecycle_status": status,
        "portnum": record.get("application"),
        "channel": record.get("channel"),
        "hop_limit": record.get("hop_limit"),
        "hop_start": record.get("hop_start"),
        "want_ack": bool(record.get("want_ack")),
        "want_response": bool(record.get("want_response")),
        "via_mqtt": bool(record.get("via_mqtt")),
        "encrypted": bool(record.get("encrypted")),
        "transport_mechanism": record.get("transport", "MUX_CLIENT_TO_RADIO"),
        "mux_client_id": record.get("client_id"),
        "correlation_id": record.get("correlation_id"),
        "request_id": record.get("request_id"),
        "reply_id": record.get("reply_id"),
        "evidence": [f"MUX audit {record_type}", "metadata-only audit stream"],
    }
    if isinstance(source, int):
        event["from_node"] = source
        event["from_node_id"] = f"!{source & 0xFFFFFFFF:08x}"
    if isinstance(destination, int):
        event["to_node"] = destination
        event["to_node_id"] = f"!{destination & 0xFFFFFFFF:08x}"
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="ATLAS consumer for the MUX audit stream")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4406)
    parser.add_argument("--observer-id", required=True)
    parser.add_argument("--observer-node", required=True, type=parse_node_num)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--api-url", required=True)
    args = parser.parse_args()
    token = os.environ.get("ATLAS_INGEST_TOKEN")
    if not token:
        parser.error("ATLAS_INGEST_TOKEN is required")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    writer = NormalizedWriter(args.output, args.api_url, token)
    stopped = False

    def stop(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    delay = 1.0
    try:
        while not stopped:
            try:
                with socket.create_connection((args.host, args.port), timeout=8) as connection:
                    connection.settimeout(1.0)
                    LOG.info("connected to MUX audit stream %s:%d", args.host, args.port)
                    delay = 1.0
                    buffer = bytearray()
                    while not stopped:
                        try:
                            chunk = connection.recv(8192)
                        except TimeoutError:
                            continue
                        if not chunk:
                            raise ConnectionError("MUX audit stream closed")
                        buffer.extend(chunk)
                        while b"\n" in buffer:
                            raw_line, _, remainder = buffer.partition(b"\n")
                            buffer = bytearray(remainder)
                            try:
                                record = json.loads(raw_line)
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                LOG.warning("discarded malformed audit record")
                                continue
                            event = normalize_audit(record, args.observer_id, args.observer_node)
                            if event is not None:
                                writer.write(event)
                                LOG.info(
                                    "packet lifecycle id=%s status=%s from=%s to=%s type=%s",
                                    event["packet_id"],
                                    event.get("lifecycle_status"),
                                    event.get("from_node_id", "unknown"),
                                    event.get("to_node_id", "unknown"),
                                    event.get("portnum"),
                                )
            except (ConnectionError, OSError, TimeoutError) as exc:
                if stopped:
                    break
                LOG.warning("audit connection failed: %s; retrying in %.1fs", exc, delay)
                time.sleep(delay)
                delay = min(30.0, delay * 2)
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
