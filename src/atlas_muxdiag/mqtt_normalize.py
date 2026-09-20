"""Normalize safe MQTT diagnostic records into ATLAS domain events."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .normalize import SCHEMA_VERSION, node_id


def gateway_node_num(gateway_id: str) -> int | None:
    try:
        return int(gateway_id.removeprefix("!"), 16)
    except (AttributeError, ValueError):
        return None


class MqttEventNormalizer:
    """Preserve one observation per gateway while deduplicating broker repeats."""

    def process(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        if record.get("event") != "mqtt_packet_analysis":
            return []
        summary = record.get("packet")
        gateway_id = record.get("gateway_id")
        if not isinstance(summary, dict) or not isinstance(gateway_id, str):
            return []
        observer_node = gateway_node_num(gateway_id)
        if observer_node is None:
            return []

        sender = summary.get("from")
        destination = summary.get("to")
        packet_id = summary.get("id")
        if not all(isinstance(value, int) for value in (sender, destination, packet_id)):
            return []

        classification = str(record.get("classification", "MQTT_UNVERIFIED"))
        direct_rf = classification == "MQTT_GATEWAY_DIRECT_RF"
        event_type = "rf_observation" if direct_rf else "network_packet"
        # Keep the established UI provenance vocabulary; retain finer classification separately.
        source = "RF_OBSERVED" if direct_rf else "MQTT_NETWORK"
        observed_at = str(record["observed_at"])
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self._event_id(gateway_id, int(sender), int(destination), int(packet_id)),
            "event": event_type,
            "observer_id": f"MQTT:{node_id(observer_node)}",
            "observer_node_num": observer_node,
            "observer_node_id": node_id(observer_node),
            "observed_at": observed_at,
            "source": source,
            "transport_source": "LZ_MQTT",
            "mqtt_gateway_id": gateway_id,
            "mqtt_classification": classification,
            "evidence": self._evidence(classification),
            "packet_id": int(packet_id),
            "from_node": int(sender),
            "from_node_id": node_id(int(sender)),
            "to_node": int(destination),
            "to_node_id": node_id(int(destination)),
            "portnum": summary.get("portnum"),
            "encrypted": record.get("encryption") == "encrypted_unknown",
            "rx_rssi": summary.get("rx_rssi") or None,
            "rx_snr": summary.get("rx_snr") or None,
            "hop_start": int(summary.get("hop_start", 0)),
            "hop_limit": int(summary.get("hop_limit", 0)),
            "via_mqtt": bool(summary.get("via_mqtt", False)),
            "transport_mechanism": summary.get("transport_mechanism"),
            "channel": int(summary.get("channel", 0)),
            "channel_id": record.get("channel_id"),
            "want_ack": bool(summary.get("want_ack", False)),
            "want_response": bool(summary.get("want_response", False)),
            "request_id": int(summary.get("request_id", 0)),
            "reply_id": int(summary.get("reply_id", 0)),
            "position": summary.get("position"),
            "node_info": summary.get("node_info"),
            "device_metadata": summary.get("device_metadata"),
            "neighbor_info": summary.get("neighbor_info"),
            "traceroute": summary.get("traceroute"),
        }
        return [event]

    @staticmethod
    def _event_id(gateway_id: str, sender: int, destination: int, packet_id: int) -> str:
        # Broker duplicates at one gateway collapse; reports from different gateways remain distinct.
        identity = json.dumps(
            ["LZ_MQTT", gateway_id.lower(), sender, destination, packet_id],
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode()).hexdigest()[:32]

    @staticmethod
    def _evidence(classification: str) -> list[str]:
        evidence = ["received in Meshtastic ServiceEnvelope from LZ MQTT broker"]
        if classification == "MQTT_GATEWAY_DIRECT_RF":
            evidence.extend(
                [
                    "publishing gateway differs from sender",
                    "via_mqtt=false",
                    "zero consumed hops",
                    "reception metrics present",
                ]
            )
        elif classification == "MQTT_GATEWAY_MULTIHOP_RF":
            evidence.append("multihop gateway reception; original sender not directly attributable")
        elif classification == "MQTT_GATEWAY_LOCAL_UPLINK":
            evidence.append("sender equals publishing gateway; not an RF reception")
        elif classification == "MQTT_NETWORK":
            evidence.append("via_mqtt=true")
        else:
            evidence.append("insufficient evidence for RF attribution")
        return evidence
