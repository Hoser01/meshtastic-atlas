import base64
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

from atlas_muxdiag.mqtt_diag import (
    DEFAULT_PSK,
    _expand_psk,
    analyze_message,
    analyze_payload,
    parser,
)

TEST_KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
KEYS = {1: ("test-channel", TEST_KEY)}


def test_collector_defaults_to_continuous_bounded_operation(tmp_path) -> None:
    args = parser().parse_args(["--keys-file", str(tmp_path / "keys.json")])

    assert args.duration == 0
    assert args.max_messages == 0
    assert args.output_max_bytes == 100 * 1024 * 1024
    assert args.output_backups == 3
    assert args.spool_max_events == 50_000
    assert args.spool_max_bytes == 256 * 1024 * 1024


def _envelope(
    data: mesh_pb2.Data,
    *,
    hops: int = 0,
    via_mqtt: bool = False,
    rssi: int = -82,
    gateway_id: str = "!a2e9f268",
) -> bytes:
    sender = 0xA0352614
    packet_id = 123456
    nonce = packet_id.to_bytes(8, "little") + sender.to_bytes(8, "little")
    encryptor = Cipher(algorithms.AES(TEST_KEY), modes.CTR(nonce)).encryptor()
    ciphertext = encryptor.update(data.SerializeToString()) + encryptor.finalize()

    packet = mesh_pb2.MeshPacket(
        id=packet_id,
        to=0xFFFFFFFF,
        channel=1,
        encrypted=ciphertext,
        hop_start=3,
        hop_limit=3 - hops,
        via_mqtt=via_mqtt,
        rx_rssi=rssi,
        rx_snr=5.5,
    )
    setattr(packet, "from", sender)
    return mqtt_pb2.ServiceEnvelope(
        packet=packet,
        channel_id="LongFast",
        gateway_id=gateway_id,
    ).SerializeToString()


def test_default_psk_shorthand_expansion() -> None:
    assert _expand_psk(base64.b64encode(b"\x01").decode()) == DEFAULT_PSK
    changed = bytearray(DEFAULT_PSK)
    changed[-1] = (changed[-1] + 1) & 0xFF
    assert _expand_psk(base64.b64encode(b"\x02").decode()) == bytes(changed)


def test_decrypts_position_and_attributes_direct_gateway_rf() -> None:
    position = mesh_pb2.Position(latitude_i=370930000, longitude_i=-945334000)
    data = mesh_pb2.Data(
        portnum=portnums_pb2.PortNum.POSITION_APP,
        payload=position.SerializeToString(),
    )

    result = analyze_payload("msh/LZ/2/e/LongFast/!a2e9f268", _envelope(data), KEYS)

    assert result["encryption"] == "decrypted"
    assert result["matched_key_index"] == 1
    assert result["classification"] == "MQTT_GATEWAY_DIRECT_RF"
    assert result["rf_transmitter_attributable"] is True
    assert result["packet"]["position"]["latitude"] == 37.093


def test_multihop_and_mqtt_are_not_attributed_to_original_sender_rf() -> None:
    data = mesh_pb2.Data(portnum=portnums_pb2.PortNum.NODEINFO_APP)
    multihop = analyze_payload("topic", _envelope(data, hops=2), KEYS)
    network = analyze_payload("topic", _envelope(data, via_mqtt=True), KEYS)

    assert multihop["classification"] == "MQTT_GATEWAY_MULTIHOP_RF"
    assert multihop["rf_transmitter_attributable"] is False
    assert network["classification"] == "MQTT_NETWORK"
    assert network["rf_transmitter_attributable"] is False


def test_gateway_own_uplink_is_not_mistaken_for_rf_reception() -> None:
    data = mesh_pb2.Data(portnum=portnums_pb2.PortNum.TELEMETRY_APP)
    result = analyze_payload(
        "topic",
        _envelope(data, gateway_id="!a0352614"),
        KEYS,
    )

    assert result["classification"] == "MQTT_GATEWAY_LOCAL_UPLINK"
    assert result["rf_transmitter_attributable"] is False


def test_text_message_body_is_never_emitted() -> None:
    secret_body = b"private diagnostic text that must not be retained"
    data = mesh_pb2.Data(
        portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
        payload=secret_body,
    )

    result = analyze_payload("topic", _envelope(data), KEYS)
    serialized = json.dumps(result)

    assert secret_body.decode() not in serialized
    assert result["packet"]["payload_bytes"] == len(secret_body)
    assert "payload" not in result["packet"]


def test_json_broker_traffic_omits_message_content() -> None:
    payload = json.dumps(
        {
            "from": 123,
            "to": 456,
            "type": "text",
            "rssi": -90,
            "payload": {"text": "private broker message"},
        }
    ).encode()

    result = analyze_message("msh/LZ/2/json/LongFast/!gateway", payload, KEYS)
    serialized = json.dumps(result)

    assert result["format"] == "json"
    assert result["from"] == 123
    assert result["rssi"] == -90
    assert "private broker message" not in serialized
    assert "payload" not in result


def test_unknown_broker_traffic_keeps_only_metadata_and_fingerprint() -> None:
    result = analyze_message("msh/LZ/status/example", b"arbitrary private data", KEYS)

    assert result["format"] == "other"
    assert result["payload_bytes"] == 22
    assert "arbitrary private data" not in json.dumps(result)
