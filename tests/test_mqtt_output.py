from atlas_muxdiag.mqtt_output import event_topic


def test_event_topics() -> None:
    assert event_topic("atlas/test/", {"event": "rf_observation", "observer_id": "LZG2"}) == (
        "atlas/test/observation/LZG2"
    )
    assert event_topic("atlas/test", {"event": "collector_started", "observer_id": "LZG2"}) == (
        "atlas/test/status/LZG2"
    )
