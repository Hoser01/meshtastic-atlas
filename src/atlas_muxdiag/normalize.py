"""Versioned Phase 1 normalization and local-response correlation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1


def parse_node_num(value: str) -> int:
    """Accept Meshtastic !hex IDs, conventional hex, or decimal node numbers."""
    clean = value.strip().lower()
    if clean.startswith("!"):
        return int(clean[1:], 16)
    if clean.startswith("0x"):
        return int(clean, 16)
    return int(clean, 10)


def node_id(value: int) -> str:
    return f"!{value:08x}"


def _timestamp_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@dataclass
class PendingTransmission:
    packet_id: int
    queued_at: str


class EventNormalizer:
    """Translate diagnostic records into conservative ATLAS domain events."""

    def __init__(
        self,
        observer_id: str,
        local_node_num: int,
        correlation_window_seconds: float = 600.0,
    ) -> None:
        self.observer_id = observer_id
        self.local_node_num = local_node_num
        self.stream_local_node_num = local_node_num
        self.correlation_window_seconds = correlation_window_seconds
        self.pending: dict[int, PendingTransmission] = {}

    def process(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        captured_at = record.get("captured_at")
        if not captured_at:
            return []
        self._expire(captured_at)

        kind = record.get("event")
        if kind in {"capture_started", "connected", "connection_error", "capture_stopped"}:
            return [self._status(record)]
        if kind != "frame":
            return []

        variant = record.get("payload_variant")
        if variant == "my_info" and isinstance(record.get("local_node_num"), int):
            self.stream_local_node_num = int(record["local_node_num"])
            if self.stream_local_node_num != self.local_node_num:
                return [
                    self._base(
                        "observer_identity_mismatch",
                        record["captured_at"],
                        configured_node_num=self.local_node_num,
                        configured_node_id=node_id(self.local_node_num),
                        stream_node_num=self.stream_local_node_num,
                        stream_node_id=node_id(self.stream_local_node_num),
                    )
                ]
            return []
        if variant == "queueStatus":
            return self._queue_events(record)
        if variant == "node_info" and isinstance(record.get("node_num"), int):
            snapshot_node = int(record["node_num"])
            return [
                self._base(
                    "node_identity",
                    record["captured_at"],
                    source="NODE_DB",
                    evidence=["FromRadio node_info snapshot"],
                    from_node=snapshot_node,
                    from_node_id=node_id(snapshot_node),
                    node_info=record.get("node_info"),
                )
            ]
        if variant == "metadata" and isinstance(record.get("device_metadata"), dict):
            return [
                self._base(
                    "node_device_metadata",
                    record["captured_at"],
                    source="LOCAL_METADATA",
                    evidence=["FromRadio device metadata"],
                    from_node=self.stream_local_node_num,
                    from_node_id=node_id(self.stream_local_node_num),
                    device_metadata=record["device_metadata"],
                )
            ]
        if variant == "packet" and record.get("packet_summary"):
            return self._packet_events(record)
        return []

    def _queue_events(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        status = record.get("decoded", {}).get("queueStatus", {})
        packet_id = int(status.get("mesh_packet_id", 0))
        if not packet_id:
            return []
        queued_at = record["captured_at"]
        self.pending[packet_id] = PendingTransmission(packet_id, queued_at)
        return [
            self._base(
                "local_transmission_queued",
                queued_at,
                packet_id=packet_id,
                source="LOCAL_TX",
                local_node_num=self.stream_local_node_num,
                local_node_id=node_id(self.stream_local_node_num),
                queue_free=int(status.get("free", 0)),
                queue_max=int(status.get("maxlen", 0)),
            )
        ]

    def _packet_events(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        summary = record["packet_summary"]
        sender = int(summary["from"])
        via_mqtt = bool(summary.get("via_mqtt", False))
        rssi = int(summary.get("rx_rssi", 0))
        snr = float(summary.get("rx_snr", 0.0))

        if via_mqtt:
            source = "MQTT_NETWORK"
            event_type = "network_packet"
            evidence = ["via_mqtt=true"]
        elif sender == self.stream_local_node_num:
            source = "LOCAL_TX"
            event_type = "local_transmission"
            evidence = ["sender equals stream-reported local node"]
        elif rssi or snr:
            source = "RF_OBSERVED"
            event_type = "rf_observation"
            evidence = ["via_mqtt=false", "nonlocal sender", "reception metrics present"]
        else:
            source = "UNKNOWN"
            event_type = "unclassified_packet"
            evidence = ["insufficient positive provenance evidence"]

        event = self._base(
            event_type,
            record["captured_at"],
            source=source,
            evidence=evidence,
            packet_id=int(summary["id"]),
            from_node=sender,
            from_node_id=node_id(sender),
            to_node=int(summary["to"]),
            to_node_id=node_id(int(summary["to"])),
            portnum=summary.get("portnum"),
            encrypted=summary.get("payload_variant") == "encrypted",
            rx_rssi=rssi if source == "RF_OBSERVED" or via_mqtt else None,
            rx_snr=snr if source == "RF_OBSERVED" or via_mqtt else None,
            hop_start=int(summary.get("hop_start", 0)),
            hop_limit=int(summary.get("hop_limit", 0)),
            via_mqtt=via_mqtt,
            transport_mechanism=summary.get("transport_mechanism"),
            channel=int(summary.get("channel", 0)),
            want_ack=bool(summary.get("want_ack", False)),
            want_response=bool(summary.get("want_response", False)),
            request_id=int(summary.get("request_id", 0)),
            reply_id=int(summary.get("reply_id", 0)),
            position=summary.get("position"),
            node_info=summary.get("node_info"),
            device_metadata=summary.get("device_metadata"),
            neighbor_info=summary.get("neighbor_info"),
            traceroute=summary.get("traceroute"),
            telemetry=summary.get("telemetry"),
        )
        events = [event]

        request_id = int(
            record.get("decoded", {}).get("packet", {}).get("decoded", {}).get("request_id", 0)
        )
        pending = self.pending.pop(request_id, None) if request_id else None
        if pending is not None:
            events.append(
                self._base(
                    "local_transmission_response",
                    record["captured_at"],
                    source=source,
                    request_packet_id=request_id,
                    response_packet_id=int(summary["id"]),
                    response_from=sender,
                    response_from_id=node_id(sender),
                    response_portnum=summary.get("portnum"),
                    queued_at=pending.queued_at,
                    latency_ms=round(
                        (
                            _timestamp_seconds(record["captured_at"])
                            - _timestamp_seconds(pending.queued_at)
                        )
                        * 1000
                    ),
                )
            )
        return events

    def _status(self, record: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "capture_started": "collector_started",
            "connected": "observer_connected",
            "connection_error": "observer_connection_error",
            "capture_stopped": "collector_stopped",
        }
        fields = {key: record[key] for key in ("error", "peer", "frames") if key in record}
        return self._base(mapping[record["event"]], record["captured_at"], **fields)

    def _base(self, event: str, observed_at: str, **fields: Any) -> dict[str, Any]:
        identity = json.dumps(
            [self.observer_id, event, observed_at, fields],
            separators=(",", ":"),
            sort_keys=True,
        )
        event_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "event": event,
            "observer_id": self.observer_id,
            "observer_node_num": self.stream_local_node_num,
            "observer_node_id": node_id(self.stream_local_node_num),
            "observed_at": observed_at,
            **fields,
        }

    def _expire(self, now: str) -> None:
        cutoff = _timestamp_seconds(now) - self.correlation_window_seconds
        self.pending = {
            packet_id: pending
            for packet_id, pending in self.pending.items()
            if _timestamp_seconds(pending.queued_at) >= cutoff
        }
