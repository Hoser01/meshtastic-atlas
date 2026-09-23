from datetime import datetime, timedelta, timezone

from atlas_server.store import AtlasStore, EventValidationError


def observation(event_id: str, observer: str, at: str, rssi: int) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event": "rf_observation",
        "observer_id": observer,
        "observer_node_num": 1 if observer == "A" else 2,
        "observer_node_id": "!00000001" if observer == "A" else "!00000002",
        "observed_at": at,
        "source": "RF_OBSERVED",
        "packet_id": 42,
        "from_node": 100,
        "to_node": 0xFFFFFFFF,
        "portnum": "POSITION_APP",
        "encrypted": False,
        "rx_rssi": rssi,
        "rx_snr": 5.5,
        "hop_start": 3,
        "hop_limit": 2,
    }


def test_multiple_observers_are_retained_on_one_transmission(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        first = observation("a" * 32, "A", "2026-09-18T13:00:00.100Z", -80)
        second = observation("b" * 32, "B", "2026-09-18T13:00:00.300Z", -100)
        assert store.ingest(first)
        assert store.ingest(second)
        assert not store.ingest(second)
        assert store.stats() == {
            "transmissions": 1,
            "observations": 2,
            "events": 0,
            "positions": 0,
            "observers": 2,
            "rf_observations": 2,
            "mqtt_packets": 0,
            "positioned_nodes": 0,
        }
        value = store.transmission(42, 100, 0xFFFFFFFF)
        assert value is not None
        assert [item["observer_id"] for item in value["observations"]] == ["A", "B"]
        assert [item["rx_rssi"] for item in value["observations"]] == [-80, -100]
    finally:
        store.close()


def test_non_observation_event_is_stored_idempotently(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    event = {
        "schema_version": 1,
        "event_id": "c" * 32,
        "event": "collector_started",
        "observer_id": "A",
        "observer_node_num": 1,
        "observer_node_id": "!00000001",
        "observed_at": "2026-09-18T13:00:00Z",
    }
    try:
        assert store.ingest(event)
        assert not store.ingest(event)
    finally:
        store.close()


def test_telemetry_history_and_latest_are_retained(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        first = observation("1" * 32, "A", "2026-09-18T13:00:00Z", -80)
        first["portnum"] = "TELEMETRY_APP"
        first["telemetry"] = {
            "time": 1_789_000_000,
            "variant": "device_metrics",
            "metrics": {"battery_level": 82, "voltage": 4.08},
        }
        latest = observation("2" * 32, "A", "2026-09-18T13:05:00Z", -79)
        latest["packet_id"] = 43
        latest["portnum"] = "TELEMETRY_APP"
        latest["telemetry"] = {
            "time": 1_789_000_300,
            "variant": "environment_metrics",
            "metrics": {"temperature": 24.5, "relative_humidity": 61.2},
        }
        assert store.ingest(first)
        assert store.ingest(latest)
        history = store.list_telemetry(node_num=100)
        assert len(history) == 2
        assert history[0]["metrics"]["temperature"] == 24.5
        current = store.latest_telemetry_by_node()[100]
        assert current["variant"] == "environment_metrics"
    finally:
        store.close()


def test_observer_health_counts_only_connection_errors_from_last_24_hours(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    now = datetime.now(timezone.utc)

    def timestamp(delta: timedelta) -> str:
        return (now + delta).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    heartbeat = {
        "schema_version": 1,
        "event_id": "d" * 32,
        "event": "collector_heartbeat",
        "observer_id": "A",
        "observer_node_num": 1,
        "observer_node_id": "!00000001",
        "observed_at": timestamp(timedelta()),
        "mux_connected": True,
        "delivery_queue_depth": 0,
    }
    recent_error = {
        **heartbeat,
        "event_id": "e" * 32,
        "event": "observer_connection_error",
        "observed_at": timestamp(timedelta(hours=-1)),
    }
    old_error = {
        **heartbeat,
        "event_id": "f" * 32,
        "event": "observer_connection_error",
        "observed_at": timestamp(timedelta(hours=-25)),
    }
    try:
        assert store.ingest(heartbeat)
        assert store.ingest(recent_error)
        assert store.ingest(old_error)
        assert store.list_observer_health()[0]["errors_24h"] == 1
    finally:
        store.close()


def test_packet_lifecycle_keeps_identity_and_latest_status(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    now = datetime.now(timezone.utc)
    queued = {
        "schema_version": 1,
        "event_id": "7" * 32,
        "event": "local_transmission",
        "observer_id": "A",
        "observer_node_num": 1,
        "observer_node_id": "!00000001",
        "observed_at": now.isoformat(),
        "packet_id": 77,
        "from_node": 1,
        "to_node": 2,
        "portnum": "TEXT_MESSAGE_APP",
        "lifecycle_status": "QUEUED",
    }
    forwarded = {
        "schema_version": 1,
        "event_id": "8" * 32,
        "event": "packet_lifecycle",
        "observer_id": "A",
        "observer_node_num": 1,
        "observer_node_id": "!00000001",
        "observed_at": (now + timedelta(seconds=1)).isoformat(),
        "packet_id": 77,
        "lifecycle_status": "FORWARDED",
    }
    try:
        assert store.ingest(queued)
        assert store.ingest(forwarded)
        lifecycle = store.list_packet_lifecycles()[0]
        assert lifecycle["from_node"] == 1
        assert lifecycle["to_node"] == 2
        assert lifecycle["portnum"] == "TEXT_MESSAGE_APP"
        assert lifecycle["status"] == "FORWARDED"
    finally:
        store.close()


def test_rejects_unsupported_schema(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        event = observation("d" * 32, "A", "2026-09-18T13:00:00Z", -80)
        event["schema_version"] = 99
        try:
            store.ingest(event)
        except EventValidationError as exc:
            assert "unsupported" in str(exc)
        else:
            raise AssertionError("expected validation error")
    finally:
        store.close()


def test_position_history_and_latest_node_are_retained(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        first = observation("e" * 32, "A", "2026-09-18T13:00:00Z", -80)
        first["position"] = {"latitude": 30.1, "longitude": -93.7, "altitude": 10}
        latest = observation("f" * 32, "A", "2026-09-18T13:05:00Z", -78)
        latest["packet_id"] = 43
        latest["position"] = {
            "latitude": 30.2,
            "longitude": -93.8,
            "altitude": 12,
            "sats_in_view": 8,
        }
        assert store.ingest(first)
        assert store.ingest(latest)
        assert [item["latitude"] for item in store.list_positions(node_num=100)] == [30.2, 30.1]
        assert store.list_nodes()[0]["latitude"] == 30.2
        assert store.stats()["positions"] == 2
    finally:
        store.close()


def test_newer_node_info_replaces_names_and_older_data_does_not(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        first = observation("1" * 32, "A", "2026-09-18T13:00:00Z", -80)
        first["position"] = {"latitude": 37.0, "longitude": -94.5}
        first["node_info"] = {"long_name": "Old Name", "short_name": "OLD"}
        newer = observation("2" * 32, "A", "2026-09-18T13:05:00Z", -79)
        newer["packet_id"] = 43
        newer["node_info"] = {"long_name": "New Name", "short_name": "NEW"}
        older = observation("3" * 32, "A", "2026-09-18T12:55:00Z", -78)
        older["packet_id"] = 44
        older["node_info"] = {"long_name": "Stale Name", "short_name": "BAD"}
        assert store.ingest(first)
        assert store.ingest(newer)
        assert store.ingest(older)
        node = store.list_nodes()[0]
        assert node["long_name"] == "New Name"
        assert node["short_name"] == "NEW"
    finally:
        store.close()


def test_device_metadata_enriches_node_without_erasing_identity(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        identity = observation("9" * 32, "A", "2026-09-18T13:00:00Z", -80)
        identity["node_info"] = {
            "long_name": "Field Node",
            "short_name": "FLD",
            "role": "CLIENT",
            "hardware_model": "HELTEC_V3",
            "is_licensed": True,
        }
        metadata = observation("a9" * 16, "A", "2026-09-18T13:01:00Z", -79)
        metadata["packet_id"] = 43
        metadata["device_metadata"] = {
            "firmware_version": "2.7.11.test",
            "hardware_model": "HELTEC_V3",
            "role": "CLIENT",
            "has_wifi": True,
            "has_bluetooth": True,
        }
        assert store.ingest(identity)
        assert store.ingest(metadata)
        node = store.get_node_summary(100)
        assert node is not None
        assert node["long_name"] == "Field Node"
        assert node["firmware_version"] == "2.7.11.test"
        assert node["role"] == "CLIENT"
        assert node["has_wifi"] == 1
        assert node["is_licensed"] == 1
    finally:
        store.close()


def test_node_summaries_are_unique_ordered_and_include_received_activity(tmp_path) -> None:
    store = AtlasStore(tmp_path / "atlas.db")
    try:
        first = observation("4" * 32, "A", "2026-09-18T13:00:00Z", -80)
        first["to_node"] = 200
        first["node_info"] = {"long_name": "Sender", "short_name": "SND"}
        second = observation("5" * 32, "A", "2026-09-18T13:05:00Z", -75)
        second["packet_id"] = 43
        second["to_node"] = 200
        assert store.ingest(first)
        assert store.ingest(second)

        summaries = store.list_node_summaries()
        assert [row["node_num"] for row in summaries] == [100, 200]
        assert summaries[0]["short_name"] == "SND"
        assert summaries[0]["display_provenance"] == "RF_OBSERVED"
        assert summaries[0]["rf_observations"] == 2
        assert summaries[0]["sent_observations"] == 2
        assert summaries[0]["last_packet_seen"] == "2026-09-18T13:05:00Z"
        assert summaries[1]["received_observations"] == 2
        assert summaries[1]["last_packet_seen"] == "2026-09-18T13:05:00Z"
        assert store.list_node_activity(200)[0]["node_direction"] == "received"
        assert len(store.list_node_activity(100)) == 2
        quality = store.quality_metrics()
        assert quality["unique_packets"] == 2
        assert quality["repeated_observations"] == 0
        assert quality["rf_unique_packets"] == 2
        assert quality["deduplication_rule"].endswith("RF_OBSERVED wins provenance")
    finally:
        store.close()


def test_unknown_packet_metadata_does_not_conflict_with_decoded_value() -> None:
    base = {
        "from_node": 100,
        "packet_id": 42,
        "to_node": 0xFFFFFFFF,
        "observed_at": "2026-09-18T13:00:00Z",
        "observer_id": "LZG2",
        "source": "RF_OBSERVED",
    }
    summary = AtlasStore._logical_packet_summary(
        [
            {**base, "portnum": None},
            {
                **base,
                "observed_at": "2026-09-18T13:00:01Z",
                "observer_id": "MQTT:!a2e9f268",
                "source": "MQTT_NETWORK",
                "portnum": "NODEINFO_APP",
            },
        ]
    )
    assert summary["portnum"] == "NODEINFO_APP"
    assert summary["conflicting_portnum"] is False
