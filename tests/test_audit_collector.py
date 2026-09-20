from atlas_muxdiag.audit_collector import normalize_audit


def test_normalizes_safe_outbound_packet_metadata() -> None:
    event = normalize_audit(
        {
            "schema_version": 1,
            "event": "client_frame",
            "direction": "client_to_radio",
            "disposition": "queued",
            "observed_at": "2026-09-19T04:00:00.000Z",
            "client_id": 3,
            "packet_id": 123,
            "from_node": 2687837716,
            "to_node": 42,
            "application": "TEXT_MESSAGE_APP",
            "channel": 0,
            "want_response": True,
            "request_id": 77,
            "reply_id": 66,
            "encrypted": False,
            "text": "must never be copied",
        },
        "LZG2",
        2687837716,
    )
    assert event is not None
    assert event["event"] == "local_transmission"
    assert event["portnum"] == "TEXT_MESSAGE_APP"
    assert event["to_node"] == 42
    assert event["want_response"] is True
    assert event["request_id"] == 77
    assert event["reply_id"] == 66
    assert "text" not in event


def test_lifecycle_records_are_normalized_without_payloads() -> None:
    assert (
        normalize_audit(
            {"event": "client_frame", "direction": "client_to_radio", "disposition": "queued"},
            "LZG2",
            1,
        )
        is None
    )
    forwarded = normalize_audit(
        {
            "event": "forward_result",
            "direction": "client_to_radio",
            "disposition": "forwarded",
            "packet_id": 1,
            "observed_at": "2026-09-19T04:00:01.000Z",
        },
        "LZG2",
        1,
    )
    assert forwarded is not None
    assert forwarded["lifecycle_status"] == "FORWARDED"
    accepted = normalize_audit(
        {
            "event": "queue_status",
            "observed_at": "2026-09-19T04:00:02.000Z",
            "queue": {"mesh_packet_id": 1, "result": 0},
        },
        "LZG2",
        1,
    )
    assert accepted is not None
    assert accepted["lifecycle_status"] == "RADIO_ACCEPTED"
    response = normalize_audit(
        {
            "event": "radio_frame",
            "application": "ROUTING_APP",
            "request_id": 1,
            "observed_at": "2026-09-19T04:00:03.000Z",
        },
        "LZG2",
        1,
    )
    assert response is not None
    assert response["lifecycle_status"] == "RESPONSE_RECEIVED"
