from atlas_muxdiag.mqtt_normalize import MqttEventNormalizer


def record(classification: str = "MQTT_GATEWAY_DIRECT_RF") -> dict:
    return {
        "event": "mqtt_packet_analysis",
        "observed_at": "2026-09-19T20:00:00.000Z",
        "gateway_id": "!a2e9f268",
        "channel_id": "LZMesh",
        "classification": classification,
        "encryption": "decrypted",
        "packet": {
            "id": 42,
            "from": 0xA0352614,
            "to": 0xAB3E20F9,
            "portnum": "POSITION_APP",
            "rx_rssi": -88,
            "rx_snr": 5.25,
            "hop_start": 3,
            "hop_limit": 3,
            "channel": 1,
            "position": {"latitude": 37.093, "longitude": -94.5334},
        },
    }


def test_direct_gateway_reception_becomes_rf_observation() -> None:
    event = MqttEventNormalizer().process(record())[0]

    assert event["event"] == "rf_observation"
    assert event["source"] == "RF_OBSERVED"
    assert event["observer_id"] == "MQTT:!a2e9f268"
    assert event["observer_node_num"] == 0xA2E9F268
    assert event["from_node"] == 0xA0352614
    assert event["position"]["latitude"] == 37.093


def test_multihop_reception_does_not_become_rf_observation() -> None:
    event = MqttEventNormalizer().process(record("MQTT_GATEWAY_MULTIHOP_RF"))[0]

    assert event["event"] == "network_packet"
    assert event["source"] == "MQTT_NETWORK"
    assert event["mqtt_classification"] == "MQTT_GATEWAY_MULTIHOP_RF"


def test_same_packet_at_same_gateway_has_stable_event_id() -> None:
    normalizer = MqttEventNormalizer()
    first = normalizer.process(record())[0]
    later = record()
    later["observed_at"] = "2026-09-19T20:01:00.000Z"
    second = normalizer.process(later)[0]

    assert first["event_id"] == second["event_id"]


def test_same_packet_at_different_gateway_keeps_separate_observation() -> None:
    normalizer = MqttEventNormalizer()
    first = normalizer.process(record())[0]
    other = record()
    other["gateway_id"] = "!a038f590"
    second = normalizer.process(other)[0]

    assert first["event_id"] != second["event_id"]
