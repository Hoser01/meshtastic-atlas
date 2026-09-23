"""Read-only LZMesh MQTT packet diagnostic collector."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

from .decode import ProtobufDecoder
from .mqtt_normalize import MqttEventNormalizer

LOG = logging.getLogger("atlas_mqttdiag")
DEFAULT_PSK = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _expand_psk(encoded: str) -> bytes:
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) == 1:
        if raw[0] == 0:
            return b""
        expanded = bytearray(DEFAULT_PSK)
        expanded[-1] = (expanded[-1] + raw[0] - 1) & 0xFF
        return bytes(expanded)
    if len(raw) < 16:
        return raw.ljust(16, b"\0")
    if len(raw) not in {16, 32}:
        return raw.ljust(32, b"\0")
    return raw


def load_channel_keys(path: Path) -> dict[int, tuple[str, bytes]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("channel key file must contain a JSON object")
    result: dict[int, tuple[str, bytes]] = {}
    for index_text, settings in document.items():
        if not isinstance(settings, dict) or not isinstance(settings.get("psk"), str):
            raise TypeError(f"channel {index_text} requires a base64 psk")
        index = int(index_text)
        result[index] = (
            str(settings.get("name", f"channel-{index}")),
            _expand_psk(settings["psk"]),
        )
    return result


def decrypt_data(
    packet: mesh_pb2.MeshPacket, keys: dict[int, tuple[str, bytes]]
) -> tuple[mesh_pb2.Data | None, int | None]:
    if not packet.HasField("encrypted"):
        return packet.decoded if packet.HasField("decoded") else None, int(packet.channel)
    nonce = int(packet.id).to_bytes(8, "little") + int(getattr(packet, "from")).to_bytes(
        8, "little"
    )
    ordered = sorted(keys, key=lambda index: index != int(packet.channel))
    for index in ordered:
        _name, key = keys[index]
        if not key:
            continue
        plaintext = (
            Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor().update(packet.encrypted)
        )
        decoded = mesh_pb2.Data()
        try:
            decoded.ParseFromString(plaintext)
        except DecodeError:
            continue
        if decoded.portnum == portnums_pb2.PortNum.UNKNOWN_APP:
            continue
        if decoded.portnum not in portnums_pb2.PortNum.values() or len(decoded.payload) > 233:
            continue
        return decoded, index
    return None, None


def analyze_payload(
    topic: str, payload: bytes, keys: dict[int, tuple[str, bytes]]
) -> dict[str, Any]:
    envelope = mqtt_pb2.ServiceEnvelope()
    envelope.ParseFromString(payload)
    if not envelope.HasField("packet"):
        raise ValueError("MQTT payload has no MeshPacket")
    packet = mesh_pb2.MeshPacket()
    packet.CopyFrom(envelope.packet)
    was_encrypted = packet.HasField("encrypted")
    decoded, key_index = decrypt_data(packet, keys)
    if decoded is not None and not packet.HasField("decoded"):
        packet.ClearField("encrypted")
        packet.decoded.CopyFrom(decoded)
    wrapper = mesh_pb2.FromRadio(packet=packet)
    decoded_record = ProtobufDecoder().decode(wrapper.SerializeToString())
    summary = decoded_record["packet_summary"]
    gateway_id = envelope.gateway_id or topic.rstrip("/").split("/")[-1]
    gateway_node_num: int | None = None
    try:
        gateway_node_num = int(gateway_id.removeprefix("!"), 16)
    except ValueError:
        pass
    hops = max(0, int(summary.get("hop_start", 0)) - int(summary.get("hop_limit", 0)))
    metrics_present = bool(summary.get("rx_rssi") or summary.get("rx_snr"))
    if summary.get("via_mqtt"):
        classification = "MQTT_NETWORK"
    elif gateway_node_num == int(summary["from"]):
        classification = "MQTT_GATEWAY_LOCAL_UPLINK"
    elif gateway_id and metrics_present and hops == 0:
        classification = "MQTT_GATEWAY_DIRECT_RF"
    elif gateway_id and metrics_present:
        classification = "MQTT_GATEWAY_MULTIHOP_RF"
    else:
        classification = "MQTT_UNVERIFIED"
    return {
        "schema_version": 1,
        "event": "mqtt_packet_analysis",
        "observed_at": _utc_now(),
        "topic": topic,
        "gateway_id": gateway_id,
        "channel_id": envelope.channel_id,
        "channel_index": int(summary.get("channel", 0)),
        "matched_key_index": key_index,
        "matched_key_name": keys[key_index][0] if key_index in keys else None,
        "encryption": "decrypted"
        if was_encrypted and decoded is not None
        else "encrypted_unknown"
        if was_encrypted
        else "decoded",
        "classification": classification,
        "rf_transmitter_attributable": classification == "MQTT_GATEWAY_DIRECT_RF",
        "hops": hops,
        "packet": summary,
    }


def analyze_message(
    topic: str, payload: bytes, keys: dict[int, tuple[str, bytes]]
) -> dict[str, Any]:
    """Analyze every LZ broker message without retaining arbitrary payload content."""
    if "/e/" in topic:
        return analyze_payload(topic, payload, keys)

    record: dict[str, Any] = {
        "schema_version": 1,
        "event": "mqtt_broker_traffic",
        "observed_at": _utc_now(),
        "topic": topic,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "format": "json" if "/json/" in topic else "map" if "/map/" in topic else "other",
    }
    if record["format"] == "json":
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            record["json_valid"] = False
        else:
            record["json_valid"] = True
            if isinstance(document, dict):
                # Whitelist routing/radio metadata only. Never copy payload/text fields.
                for field in (
                    "id",
                    "sender",
                    "from",
                    "to",
                    "channel",
                    "type",
                    "gatewayId",
                    "gateway_id",
                    "rssi",
                    "snr",
                    "hopLimit",
                    "hopStart",
                    "viaMqtt",
                ):
                    value = document.get(field)
                    if isinstance(value, (str, int, float, bool)):
                        record[field] = value
    return record


class DiagnosticCollector:
    def __init__(
        self,
        args: argparse.Namespace,
        keys: dict[int, tuple[str, bytes]],
        stream: Any,
        event_writer: Any | None = None,
    ) -> None:
        self.args = args
        self.keys = keys
        self.stream = stream
        self.count = 0
        self.event_writer = event_writer
        self.normalizer = MqttEventNormalizer()
        self.stop_event = threading.Event()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"atlas-mqttdiag-{os.getpid()}",
            clean_session=True,
        )
        if args.username:
            self.client.username_pw_set(args.username, args.password or "")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: object,
        _flags: object,
        reason_code: object,
        _properties: object,
    ) -> None:
        if reason_code != 0:
            LOG.error("MQTT connection rejected: %s", reason_code)
            self.stop_event.set()
            return
        for topic in self.args.topic:
            client.subscribe(topic, qos=0)
        LOG.info(
            "connected to %s:%d; subscribed to %d read-only topic filters",
            self.args.host,
            self.args.port,
            len(self.args.topic),
        )

    def _on_message(
        self, _client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage
    ) -> None:
        try:
            record = analyze_message(message.topic, bytes(message.payload), self.keys)
        except Exception as exc:  # noqa: BLE001 - isolate malformed/unexpected broker messages
            record = {
                "schema_version": 1,
                "event": "mqtt_packet_analysis_error",
                "observed_at": _utc_now(),
                "topic": message.topic,
                "error": type(exc).__name__,
            }
        self.stream.write(record)
        if self.event_writer is not None:
            for event in self.normalizer.process(record):
                try:
                    self.event_writer.write(event)
                except Exception:
                    LOG.exception("normalized MQTT event delivery failed")
        self.count += 1
        if self.args.max_messages and self.count >= self.args.max_messages:
            self.stop_event.set()

    def run(self) -> int:
        self.client.connect(self.args.host, self.args.port, keepalive=60)
        self.client.loop_start()
        timer = (
            threading.Timer(self.args.duration, self.stop_event.set) if self.args.duration else None
        )
        if timer:
            timer.start()
        try:
            self.stop_event.wait()
        finally:
            if timer:
                timer.cancel()
            self.client.disconnect()
            self.client.loop_stop()
        LOG.info("capture complete: %d messages", self.count)
        return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Read-only LZMesh MQTT packet diagnostic collector"
    )
    result.add_argument("--host", default="mqtt.lzmesh.com")
    result.add_argument("--port", type=int, default=1883)
    result.add_argument("--username", default=os.environ.get("ATLAS_MQTT_USERNAME"))
    result.add_argument("--password", default=os.environ.get("ATLAS_MQTT_PASSWORD"))
    result.add_argument("--topic", action="append", default=["msh/LZ/#"])
    result.add_argument("--keys-file", type=Path, required=True)
    result.add_argument("--output", type=Path)
    result.add_argument("--events-output", type=Path)
    result.add_argument("--api-url", help="optional ATLAS /api/v1/events ingestion URL")
    result.add_argument("--duration", type=float, default=0, help="seconds; 0 means unlimited")
    result.add_argument("--max-messages", type=int, default=0, help="0 means unlimited")
    result.add_argument("--output-max-bytes", type=int, default=100 * 1024 * 1024)
    result.add_argument("--output-backups", type=int, default=3)
    result.add_argument("--spool", type=Path, help="persistent central-delivery queue")
    result.add_argument("--spool-max-events", type=int, default=50_000)
    result.add_argument("--spool-max-bytes", type=int, default=256 * 1024 * 1024)
    result.add_argument("--api-timeout", type=float, default=5.0)
    result.add_argument("-v", "--verbose", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.host != "mqtt.lzmesh.com":
        parser().error("this diagnostic is restricted to mqtt.lzmesh.com")
    if (
        args.duration < 0
        or args.max_messages < 0
        or args.output_max_bytes <= 0
        or args.output_backups < 0
        or args.spool_max_events <= 0
        or args.spool_max_bytes <= 0
        or args.api_timeout <= 0
    ):
        parser().error(
            "duration and max-messages must be nonnegative; storage limits must be positive"
        )
    if not args.username or not args.password:
        parser().error("set ATLAS_MQTT_USERNAME and ATLAS_MQTT_PASSWORD")
    keys = load_channel_keys(args.keys_file)
    api_token = os.environ.get("ATLAS_INGEST_TOKEN")
    if args.api_url and not api_token:
        parser().error("--api-url requires ATLAS_INGEST_TOKEN in the environment")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from .collector_cli import NormalizedWriter

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    stream = NormalizedWriter(
        args.output,
        output_max_bytes=args.output_max_bytes,
        output_backups=args.output_backups,
    )
    event_writer = None
    if args.events_output or args.api_url:
        if args.events_output:
            args.events_output.parent.mkdir(parents=True, exist_ok=True)
        event_writer = NormalizedWriter(
            args.events_output,
            args.api_url,
            api_token,
            spool=args.spool,
            output_max_bytes=args.output_max_bytes,
            output_backups=args.output_backups,
            spool_max_events=args.spool_max_events,
            spool_max_bytes=args.spool_max_bytes,
            api_timeout=args.api_timeout,
        )
    collector = DiagnosticCollector(args, keys, stream, event_writer)
    signal.signal(signal.SIGINT, lambda *_: collector.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: collector.stop_event.set())
    try:
        return collector.run()
    finally:
        stream.close()
        if event_writer is not None:
            event_writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
