"""Protobuf decoding and conservative provenance hints."""

from __future__ import annotations

from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2, telemetry_pb2


def _enum_name(wrapper: Any, value: int) -> str | None:
    try:
        return wrapper.Name(value)
    except ValueError:
        return None


def classify_packet(packet: Any, local_node_num: int | None) -> tuple[str, list[str]]:
    """Return a deliberately conservative hint, never a claim of proven RF origin."""
    reasons: list[str] = []
    if bool(getattr(packet, "via_mqtt", False)):
        return "MQTT_NETWORK", ["mesh_packet.via_mqtt=true"]

    sender = int(getattr(packet, "from"))
    if local_node_num is not None and sender == local_node_num:
        return "POSSIBLE_LOCAL_TX", ["packet sender equals local node number"]

    rssi = int(getattr(packet, "rx_rssi", 0))
    snr = float(getattr(packet, "rx_snr", 0.0))
    if rssi:
        reasons.append("nonzero rx_rssi")
    if snr:
        reasons.append("nonzero rx_snr")
    if reasons:
        reasons.append("via_mqtt is false")
        return "CANDIDATE_RF", reasons

    return "UNKNOWN", ["no positive RF proof in exposed fields"]


class ProtobufDecoder:
    def __init__(self, local_node_num: int | None = None) -> None:
        self.local_node_num: int | None = local_node_num

    def decode(self, payload: bytes) -> dict[str, Any]:
        message = mesh_pb2.FromRadio()
        message.ParseFromString(payload)
        variant = message.WhichOneof("payload_variant")
        result: dict[str, Any] = {
            "protobuf_type": "meshtastic.FromRadio",
            "payload_variant": variant,
            "decoded": MessageToDict(
                message,
                preserving_proto_field_name=True,
                use_integers_for_enums=False,
            ),
        }

        if variant == "my_info":
            self.local_node_num = int(message.my_info.my_node_num)
            result["local_node_num"] = self.local_node_num
        elif variant == "node_info":
            info = message.node_info
            result["node_num"] = int(info.num)
            if info.HasField("user"):
                result["node_info"] = self._summarize_user(info.user)
        elif variant == "metadata":
            result["device_metadata"] = self._summarize_device_metadata(message.metadata)
        elif variant == "packet":
            result["packet_summary"] = self._summarize_packet(message.packet)
        return result

    @staticmethod
    def _summarize_user(user: Any) -> dict[str, Any]:
        return {
            "node_id": user.id,
            "long_name": user.long_name,
            "short_name": user.short_name,
            "hardware_model": _enum_name(mesh_pb2.HardwareModel, int(user.hw_model)),
            "role": (
                type(user)
                .DESCRIPTOR.fields_by_name["role"]
                .enum_type.values_by_number[int(user.role)]
                .name
            ),
            "is_licensed": bool(user.is_licensed),
            "is_unmessagable": bool(user.is_unmessagable),
        }

    @staticmethod
    def _summarize_device_metadata(metadata: Any) -> dict[str, Any]:
        role_field = type(metadata).DESCRIPTOR.fields_by_name["role"].enum_type
        return {
            "firmware_version": metadata.firmware_version,
            "device_state_version": int(metadata.device_state_version),
            "hardware_model": _enum_name(mesh_pb2.HardwareModel, int(metadata.hw_model)),
            "role": role_field.values_by_number[int(metadata.role)].name,
            "has_wifi": bool(metadata.hasWifi),
            "has_bluetooth": bool(metadata.hasBluetooth),
            "has_ethernet": bool(metadata.hasEthernet),
            "has_remote_hardware": bool(metadata.hasRemoteHardware),
            "has_pki": bool(metadata.hasPKC),
        }

    def _summarize_packet(self, packet: Any) -> dict[str, Any]:
        classification, reasons = classify_packet(packet, self.local_node_num)
        decoded_variant = packet.WhichOneof("payload_variant")
        summary: dict[str, Any] = {
            "id": int(packet.id),
            "from": int(getattr(packet, "from")),
            "to": int(packet.to),
            "channel": int(packet.channel),
            "rx_time": int(packet.rx_time),
            "rx_rssi": int(packet.rx_rssi),
            "rx_snr": float(packet.rx_snr),
            "hop_limit": int(packet.hop_limit),
            "hop_start": int(packet.hop_start),
            "via_mqtt": bool(packet.via_mqtt),
            "transport_mechanism": _enum_name(
                mesh_pb2.MeshPacket.TransportMechanism,
                int(packet.transport_mechanism),
            ),
            "want_ack": bool(packet.want_ack),
            "priority": _enum_name(mesh_pb2.MeshPacket.Priority, int(packet.priority)),
            "payload_variant": decoded_variant,
            "provenance_hint": classification,
            "provenance_reasons": reasons,
            "warning": "A provenance hint is diagnostic evidence, not a verified RF assertion.",
        }
        if decoded_variant == "decoded":
            portnum = int(packet.decoded.portnum)
            summary["portnum"] = _enum_name(portnums_pb2.PortNum, portnum) or portnum
            summary["payload_bytes"] = len(packet.decoded.payload)
            summary["want_response"] = bool(packet.decoded.want_response)
            summary["request_id"] = int(packet.decoded.request_id)
            summary["reply_id"] = int(packet.decoded.reply_id)
            if portnum == portnums_pb2.PortNum.POSITION_APP and packet.decoded.payload:
                position = mesh_pb2.Position()
                try:
                    position.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:  # malformed app payload must not drop the radio frame
                    summary["position_decode_error"] = type(exc).__name__
                else:
                    latitude = int(position.latitude_i) / 10_000_000
                    longitude = int(position.longitude_i) / 10_000_000
                    if (
                        -90 <= latitude <= 90
                        and -180 <= longitude <= 180
                        and (position.latitude_i != 0 or position.longitude_i != 0)
                    ):
                        summary["position"] = {
                            "latitude": latitude,
                            "longitude": longitude,
                            "altitude": int(position.altitude),
                            "timestamp": int(position.timestamp or position.time),
                            "precision_bits": int(position.precision_bits),
                            "gps_accuracy": int(position.gps_accuracy),
                            "sats_in_view": int(position.sats_in_view),
                            "fix_quality": int(position.fix_quality),
                            "fix_type": int(position.fix_type),
                        }
            elif portnum == portnums_pb2.PortNum.NODEINFO_APP and packet.decoded.payload:
                user = mesh_pb2.User()
                try:
                    user.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:
                    summary["node_info_decode_error"] = type(exc).__name__
                else:
                    summary["node_info"] = self._summarize_user(user)
            elif portnum == portnums_pb2.PortNum.NEIGHBORINFO_APP and packet.decoded.payload:
                neighbor_info = mesh_pb2.NeighborInfo()
                try:
                    neighbor_info.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:
                    summary["neighbor_info_decode_error"] = type(exc).__name__
                else:
                    summary["neighbor_info"] = {
                        "reporter_node": int(neighbor_info.node_id or getattr(packet, "from")),
                        "last_sent_by": int(neighbor_info.last_sent_by_id),
                        "broadcast_interval_seconds": int(
                            neighbor_info.node_broadcast_interval_secs
                        ),
                        "neighbors": [
                            {
                                "node_num": int(neighbor.node_id),
                                "snr": float(neighbor.snr),
                                "last_rx_time": int(neighbor.last_rx_time),
                                "broadcast_interval_seconds": int(
                                    neighbor.node_broadcast_interval_secs
                                ),
                            }
                            for neighbor in neighbor_info.neighbors
                            if neighbor.node_id
                        ],
                    }
            elif portnum == portnums_pb2.PortNum.TRACEROUTE_APP and packet.decoded.payload:
                route = mesh_pb2.RouteDiscovery()
                try:
                    route.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:
                    summary["traceroute_decode_error"] = type(exc).__name__
                else:
                    summary["traceroute"] = {
                        "route": [int(value) for value in route.route],
                        "snr_towards": [
                            value / 4 if value != -128 else None for value in route.snr_towards
                        ],
                        "route_back": [int(value) for value in route.route_back],
                        "snr_back": [
                            value / 4 if value != -128 else None for value in route.snr_back
                        ],
                    }
            elif portnum == portnums_pb2.PortNum.TELEMETRY_APP and packet.decoded.payload:
                telemetry = telemetry_pb2.Telemetry()
                try:
                    telemetry.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:
                    summary["telemetry_decode_error"] = type(exc).__name__
                else:
                    variant = telemetry.WhichOneof("variant")
                    if variant:
                        metrics = getattr(telemetry, variant)
                        summary["telemetry"] = {
                            "time": int(telemetry.time),
                            "variant": variant,
                            "metrics": MessageToDict(
                                metrics,
                                preserving_proto_field_name=True,
                                use_integers_for_enums=False,
                            ),
                        }
            elif portnum == portnums_pb2.PortNum.ADMIN_APP and packet.decoded.payload:
                admin = admin_pb2.AdminMessage()
                try:
                    admin.ParseFromString(packet.decoded.payload)
                except DecodeError as exc:
                    summary["admin_decode_error"] = type(exc).__name__
                else:
                    if admin.WhichOneof("payload_variant") == "get_device_metadata_response":
                        summary["device_metadata"] = self._summarize_device_metadata(
                            admin.get_device_metadata_response
                        )
        elif decoded_variant == "encrypted":
            summary["encrypted_bytes"] = len(packet.encrypted)
        return summary
