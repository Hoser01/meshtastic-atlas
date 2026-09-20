from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2

from atlas_muxdiag.decode import ProtobufDecoder, classify_packet


def packet(sender: int = 123) -> mesh_pb2.MeshPacket:
    value = mesh_pb2.MeshPacket()
    setattr(value, "from", sender)
    value.to = 0xFFFFFFFF
    value.id = 42
    return value


def test_mqtt_is_not_classified_as_rf() -> None:
    value = packet()
    value.via_mqtt = True
    classification, reasons = classify_packet(value, None)
    assert classification == "MQTT_NETWORK"
    assert reasons == ["mesh_packet.via_mqtt=true"]


def test_metrics_only_make_candidate_rf() -> None:
    value = packet()
    value.rx_rssi = -90
    value.rx_snr = 7.5
    classification, reasons = classify_packet(value, None)
    assert classification == "CANDIDATE_RF"
    assert "nonzero rx_rssi" in reasons


def test_local_sender_is_possible_local_tx() -> None:
    classification, _ = classify_packet(packet(123), 123)
    assert classification == "POSSIBLE_LOCAL_TX"


def test_decode_from_radio_packet_summary() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet())
    message.packet.decoded.portnum = portnums_pb2.PortNum.POSITION_APP
    decoded = ProtobufDecoder().decode(message.SerializeToString())
    assert decoded["payload_variant"] == "packet"
    assert decoded["packet_summary"]["id"] == 42
    assert decoded["packet_summary"]["portnum"] == "POSITION_APP"


def test_decode_position_app_payload() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet())
    message.packet.decoded.portnum = portnums_pb2.PortNum.POSITION_APP
    position = mesh_pb2.Position(
        latitude_i=302130000,
        longitude_i=-937510000,
        altitude=12,
        timestamp=1_789_000_000,
        precision_bits=20,
        gps_accuracy=350,
        sats_in_view=8,
        fix_quality=1,
        fix_type=3,
    )
    message.packet.decoded.payload = position.SerializeToString()

    summary = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"]

    assert summary["position"] == {
        "latitude": 30.213,
        "longitude": -93.751,
        "altitude": 12,
        "timestamp": 1_789_000_000,
        "precision_bits": 20,
        "gps_accuracy": 350,
        "sats_in_view": 8,
        "fix_quality": 1,
        "fix_type": 3,
    }


def test_zero_position_is_not_presented_as_location() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet())
    message.packet.decoded.portnum = portnums_pb2.PortNum.POSITION_APP
    message.packet.decoded.payload = mesh_pb2.Position().SerializeToString()
    summary = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"]
    assert "position" not in summary


def test_decode_node_info_names() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet(0xA0352614))
    message.packet.decoded.portnum = portnums_pb2.PortNum.NODEINFO_APP
    message.packet.decoded.payload = mesh_pb2.User(
        id="!a0352614", long_name="HAVOC", short_name="HVG2"
    ).SerializeToString()

    info = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"]["node_info"]

    assert info["node_id"] == "!a0352614"
    assert info["long_name"] == "HAVOC"
    assert info["short_name"] == "HVG2"


def test_decode_device_metadata_admin_response() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet(0xA0352614))
    message.packet.decoded.portnum = portnums_pb2.PortNum.ADMIN_APP
    admin = admin_pb2.AdminMessage()
    admin.get_device_metadata_response.firmware_version = "2.7.11.abc123"
    admin.get_device_metadata_response.hw_model = mesh_pb2.HardwareModel.STATION_G2
    admin.get_device_metadata_response.hasWifi = True
    message.packet.decoded.payload = admin.SerializeToString()

    metadata = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"][
        "device_metadata"
    ]

    assert metadata["firmware_version"] == "2.7.11.abc123"
    assert metadata["hardware_model"] == "STATION_G2"
    assert metadata["has_wifi"] is True


def test_decode_neighbor_info() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet(100))
    message.packet.decoded.portnum = portnums_pb2.PortNum.NEIGHBORINFO_APP
    info = mesh_pb2.NeighborInfo(node_id=100, last_sent_by_id=100)
    info.neighbors.add(node_id=200, snr=6.25, last_rx_time=1_789_000_000)
    message.packet.decoded.payload = info.SerializeToString()

    decoded = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"][
        "neighbor_info"
    ]

    assert decoded["reporter_node"] == 100
    assert decoded["neighbors"][0]["node_num"] == 200
    assert decoded["neighbors"][0]["snr"] == 6.25


def test_decode_traceroute_and_response_correlation_fields() -> None:
    message = mesh_pb2.FromRadio()
    message.packet.CopyFrom(packet(300))
    message.packet.to = 100
    message.packet.decoded.portnum = portnums_pb2.PortNum.TRACEROUTE_APP
    message.packet.decoded.request_id = 987
    message.packet.decoded.reply_id = 654
    route = mesh_pb2.RouteDiscovery(
        route=[200, 250],
        snr_towards=[24, -128, -8],
        route_back=[260],
        snr_back=[16, 8],
    )
    message.packet.decoded.payload = route.SerializeToString()

    summary = ProtobufDecoder().decode(message.SerializeToString())["packet_summary"]

    assert summary["request_id"] == 987
    assert summary["reply_id"] == 654
    assert summary["traceroute"]["route"] == [200, 250]
    assert summary["traceroute"]["snr_towards"] == [6.0, None, -2.0]
    assert summary["traceroute"]["route_back"] == [260]


def test_decode_node_database_identity() -> None:
    message = mesh_pb2.FromRadio()
    message.node_info.num = 0xAB3E20F9
    message.node_info.user.id = "!ab3e20f9"
    message.node_info.user.long_name = "PezTag"
    message.node_info.user.short_name = "PEZ"
    decoded = ProtobufDecoder().decode(message.SerializeToString())
    assert decoded["payload_variant"] == "node_info"
    assert decoded["node_num"] == 0xAB3E20F9
    assert decoded["node_info"]["long_name"] == "PezTag"


def test_my_info_tracks_local_node() -> None:
    decoder = ProtobufDecoder()
    message = mesh_pb2.FromRadio()
    message.my_info.my_node_num = 77
    decoder.decode(message.SerializeToString())
    assert decoder.local_node_num == 77
