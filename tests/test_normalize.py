from atlas_muxdiag.normalize import EventNormalizer, node_id, parse_node_num


def record(summary: dict, decoded: dict | None = None, at: str = "2026-09-18T13:00:00Z") -> dict:
    return {
        "event": "frame",
        "captured_at": at,
        "payload_variant": "packet",
        "packet_summary": {
            "id": 10,
            "from": 20,
            "to": 30,
            "portnum": "TEXT_MESSAGE_APP",
            "payload_variant": "decoded",
            "rx_rssi": 0,
            "rx_snr": 0.0,
            "hop_start": 7,
            "hop_limit": 6,
            "via_mqtt": False,
            "channel": 0,
            "want_ack": True,
            **summary,
        },
        "decoded": decoded or {},
    }


def test_node_number_parsing() -> None:
    assert parse_node_num("!a0352614") == 2687837716
    assert parse_node_num("0xa0352614") == 2687837716
    assert parse_node_num("2687837716") == 2687837716
    assert node_id(2687837716) == "!a0352614"


def test_mqtt_overrides_metrics_and_transport() -> None:
    normalizer = EventNormalizer("LZG2", 100)
    event = normalizer.process(
        record(
            {
                "via_mqtt": True,
                "rx_rssi": -18,
                "rx_snr": 6.25,
                "transport_mechanism": "TRANSPORT_LORA",
            }
        )
    )[0]
    assert event["source"] == "MQTT_NETWORK"
    assert event["event"] == "network_packet"


def test_nonlocal_packet_with_metrics_is_rf_observed() -> None:
    normalizer = EventNormalizer("LZG2", 100)
    event = normalizer.process(record({"rx_rssi": -36, "rx_snr": 6.5}))[0]
    assert event["source"] == "RF_OBSERVED"
    assert event["rx_rssi"] == -36


def test_local_sender_is_local_transmission() -> None:
    normalizer = EventNormalizer("LZG2", 20)
    event = normalizer.process(record({"from": 20, "rx_rssi": 0}))[0]
    assert event["source"] == "LOCAL_TX"
    assert event["rx_rssi"] is None


def test_stream_reported_local_identity_is_used_and_mismatch_is_exposed() -> None:
    normalizer = EventNormalizer("LZG2", 0xA2E9F268)
    mismatch = normalizer.process(
        {
            "event": "frame",
            "captured_at": "2026-09-18T13:00:00Z",
            "payload_variant": "my_info",
            "local_node_num": 0xA0352614,
        }
    )[0]
    assert mismatch["event"] == "observer_identity_mismatch"
    assert mismatch["configured_node_id"] == "!a2e9f268"
    assert mismatch["stream_node_id"] == "!a0352614"
    local = normalizer.process(record({"from": 0xA0352614, "rx_rssi": 0}))[0]
    assert local["source"] == "LOCAL_TX"


def test_position_is_preserved_with_packet_provenance() -> None:
    normalizer = EventNormalizer("LZG2", 100)
    event = normalizer.process(
        record(
            {
                "portnum": "POSITION_APP",
                "rx_rssi": -82,
                "position": {"latitude": 30.213, "longitude": -93.751, "altitude": 12},
            }
        )
    )[0]
    assert event["source"] == "RF_OBSERVED"
    assert event["position"]["latitude"] == 30.213


def test_queue_correlates_response_request_id() -> None:
    normalizer = EventNormalizer("LZG2", 100)
    queued = {
        "event": "frame",
        "captured_at": "2026-09-18T13:00:00Z",
        "payload_variant": "queueStatus",
        "decoded": {"queueStatus": {"free": 15, "maxlen": 16, "mesh_packet_id": 1234}},
    }
    assert normalizer.process(queued)[0]["event"] == "local_transmission_queued"
    response = record(
        {"id": 99, "rx_rssi": -37, "rx_snr": 6.75, "portnum": "TRACEROUTE_APP"},
        {"packet": {"decoded": {"request_id": 1234}}},
        "2026-09-18T13:00:02.250Z",
    )
    events = normalizer.process(response)
    assert [event["event"] for event in events] == [
        "rf_observation",
        "local_transmission_response",
    ]
    assert events[1]["request_packet_id"] == 1234
    assert events[1]["latency_ms"] == 2250


def test_stale_queue_entry_does_not_correlate() -> None:
    normalizer = EventNormalizer("LZG2", 100, correlation_window_seconds=1)
    normalizer.process(
        {
            "event": "frame",
            "captured_at": "2026-09-18T13:00:00Z",
            "payload_variant": "queueStatus",
            "decoded": {"queueStatus": {"mesh_packet_id": 1234}},
        }
    )
    events = normalizer.process(
        record(
            {"rx_rssi": -37},
            {"packet": {"decoded": {"request_id": 1234}}},
            "2026-09-18T13:00:02Z",
        )
    )
    assert len(events) == 1


def test_node_database_identity_becomes_storable_event() -> None:
    normalizer = EventNormalizer("LZG2", 100)
    events = normalizer.process(
        {
            "event": "frame",
            "captured_at": "2026-09-18T13:00:00Z",
            "payload_variant": "node_info",
            "node_num": 0xAB3E20F9,
            "node_info": {"node_id": "!ab3e20f9", "long_name": "PezTag", "short_name": "PEZ"},
        }
    )
    assert events[0]["event"] == "node_identity"
    assert events[0]["from_node"] == 0xAB3E20F9
    assert events[0]["node_info"]["short_name"] == "PEZ"
