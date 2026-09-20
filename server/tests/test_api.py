import asyncio
import json

from atlas_server.api import create_app
from atlas_server.live import LiveBroker
from fastapi.testclient import TestClient


def observation(event_id: str = "a" * 32) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event": "rf_observation",
        "observer_id": "LZG2",
        "observer_node_num": 2733240936,
        "observer_node_id": "!a2e9f268",
        "observed_at": "2026-09-18T13:00:00Z",
        "source": "RF_OBSERVED",
        "packet_id": 42,
        "from_node": 100,
        "to_node": 0xFFFFFFFF,
        "portnum": "POSITION_APP",
        "encrypted": False,
        "rx_rssi": -80,
        "rx_snr": 5.5,
        "hop_start": 3,
        "hop_limit": 2,
        "position": {
            "latitude": 30.213,
            "longitude": -93.751,
            "altitude": 12,
            "precision_bits": 20,
        },
    }


def test_read_api_and_authenticated_ingestion(tmp_path) -> None:
    app = create_app(tmp_path / "atlas.db", ingest_token="secret")
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["single_observer_mode"]
        assert client.post("/api/v1/events", json=observation()).status_code == 401
        response = client.post(
            "/api/v1/events",
            json=observation(),
            headers={"X-Atlas-Ingest-Token": "secret"},
        )
        assert response.status_code == 202
        assert response.json() == {"accepted": True, "duplicate": False}
        duplicate = client.post(
            "/api/v1/events",
            json=observation(),
            headers={"X-Atlas-Ingest-Token": "secret"},
        )
        assert duplicate.json() == {"accepted": False, "duplicate": True}

        health = client.get("/api/v1/health").json()
        assert health["transmissions"] == 1
        assert health["observations"] == 1
        assert health["multi_observer_field_validated"] is False
        assert client.get("/api/v1/observers").json()[0]["observer_id"] == "LZG2"
        assert client.get("/api/v1/activity").json()[0]["event_id"] == "a" * 32
        health = client.get("/api/v1/health").json()
        assert health["rf_observations"] == 1
        assert health["positioned_nodes"] == 1
        transmissions = client.get("/api/v1/transmissions").json()
        assert transmissions[0]["observation_count"] == 1
        detail = client.get(f"/api/v1/transmissions/{transmissions[0]['id']}").json()
        assert detail["observations"][0]["rx_rssi"] == -80
        assert client.get("/api/v1/observations").json()[0]["packet_id"] == 42
        nodes = client.get("/api/v1/nodes").json()
        assert nodes[0]["node_num"] == 100
        assert nodes[0]["latitude"] == 30.213
        summaries = client.get("/api/v1/node-summaries").json()
        assert len(summaries) == 1
        assert summaries[0]["node_num"] == 100
        node_detail = client.get("/api/v1/nodes/100").json()
        assert node_detail["last_source"] == "RF_OBSERVED"
        assert node_detail["last_rf_seen"] == "2026-09-18T13:00:00Z"
        assert node_detail["last_any_seen"] == "2026-09-18T13:00:00Z"
        assert node_detail["recently_heard_by"][0]["observer_id"] == "LZG2"
        node_activity = client.get("/api/v1/nodes/100/activity").json()
        assert node_activity[0]["node_direction"] == "sent"
        quality = client.get("/api/v1/quality").json()
        assert quality["unique_packets"] == 1
        assert quality["rf_unique_packets"] == 1
        assert (
            client.get("/api/v1/quality/packets?hours=720").json()[0]["provenance"] == "RF_OBSERVED"
        )
        packet = client.get("/api/v1/quality/packets/100/42").json()
        assert packet["observation_count"] == 1
        assert len(packet["observations"]) == 1
        assert client.get("/api/v1/quality/gateways").json() == []
        assert client.get("/api/v1/quality/warnings").status_code == 200
        assert client.get("/api/v1/positions?node_num=100").json()[0]["longitude"] == -93.751


def test_ingestion_disabled_without_token(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "atlas.db")) as client:
        assert client.post("/api/v1/events", json=observation()).status_code == 503


def test_observer_specific_ingestion_token(tmp_path) -> None:
    app = create_app(tmp_path / "atlas.db", observer_tokens={"LZG2": "site-secret"})
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/events",
                json=observation(),
                headers={"X-Atlas-Ingest-Token": "site-secret"},
            ).status_code
            == 202
        )
        other = {**observation("b" * 32), "observer_id": "OTHER"}
        assert (
            client.post(
                "/api/v1/events",
                json=other,
                headers={"X-Atlas-Ingest-Token": "site-secret"},
            ).status_code
            == 401
        )


def test_measured_coverage_api_includes_distance_and_age(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "ATLAS_OBSERVER_CONFIG",
        json.dumps(
            {
                "LZG2": {"latitude": 30.20, "longitude": -93.75},
            }
        ),
    )
    event = {**observation(), "hop_limit": 3}
    app = create_app(tmp_path / "atlas.db", ingest_token="secret")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/events", json=event, headers={"X-Atlas-Ingest-Token": "secret"}
            ).status_code
            == 202
        )
        samples = client.get("/api/v1/coverage/measurements?include_neighbors=false").json()
        assert len(samples) == 1
        assert samples[0]["evidence"] == "DIRECT_RF_OBSERVATION"
        assert samples[0]["confidence"] == "measured"
        assert samples[0]["rssi"] == -80
        assert samples[0]["distance_km"] > 1
        assert samples[0]["position_age_seconds"] == 0


def test_remote_gateway_rf_is_distinct_from_direct_collector_rf(tmp_path) -> None:
    event = {
        **observation(),
        "observer_id": "MQTT:!40974814",
        "observer_node_num": 1083656212,
        "observer_node_id": "!40974814",
        "transport_source": "LZ_MQTT",
        "mqtt_classification": "MQTT_GATEWAY_DIRECT_RF",
    }
    app = create_app(tmp_path / "atlas.db", ingest_token="secret")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/events", json=event, headers={"X-Atlas-Ingest-Token": "secret"}
            ).status_code
            == 202
        )
        node = client.get("/api/v1/nodes").json()[0]
        assert node["source"] == "RF_OBSERVED"
        assert node["map_source"] == "REMOTE_GATEWAY_RF"
        summary = client.get("/api/v1/node-summaries").json()[0]
        assert summary["rf_observations"] == 0
        assert summary["remote_rf_observations"] == 1
        assert summary["display_provenance"] == "REMOTE_GATEWAY_RF"


def test_browser_api_cors_for_configured_origin(tmp_path) -> None:
    app = create_app(
        tmp_path / "atlas.db",
        allowed_origins=["http://127.0.0.1:4175"],
    )
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://127.0.0.1:4175",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4175"


def test_simulation_is_broadcast_only(tmp_path) -> None:
    app = create_app(tmp_path / "atlas.db", ingest_token="secret", allow_simulation=True)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/simulate",
            json=observation(),
            headers={"X-Atlas-Ingest-Token": "secret"},
        )
        assert response.json() == {"broadcast": True, "persisted": False}
        assert client.get("/api/v1/health").json()["observations"] == 0
        persisted_simulation = {**observation(), "simulated": True}
        assert (
            client.post(
                "/api/v1/events",
                json=persisted_simulation,
                headers={"X-Atlas-Ingest-Token": "secret"},
            ).status_code
            == 422
        )


def test_live_broker_fanout() -> None:
    async def exercise() -> None:
        broker = LiveBroker(queue_size=2)
        async with broker.subscribe() as first, broker.subscribe() as second:
            event = observation()
            await broker.publish(event)
            assert await first.get() == event
            assert await second.get() == event

    asyncio.run(exercise())
