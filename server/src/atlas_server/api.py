"""Central ATLAS REST and live SSE API."""

from __future__ import annotations

import asyncio
import hmac
import json
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .live import LiveBroker
from .store import AtlasStore, EventValidationError


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radians = math.pi / 180
    dlat = (lat2 - lat1) * radians
    dlon = (lon2 - lon1) * radians
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1 * radians) * math.cos(lat2 * radians) * math.sin(dlon / 2) ** 2
    )
    return 6371.0088 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def create_app(
    database: Path | str | None = None,
    ingest_token: str | None = None,
    allow_simulation: bool = False,
    allowed_origins: list[str] | None = None,
    observer_tokens: dict[str, str] | None = None,
) -> FastAPI:
    database = database or os.environ.get("ATLAS_DATABASE", "data/atlas.db")
    if ingest_token is None:
        ingest_token = os.environ.get("ATLAS_INGEST_TOKEN")
    if allowed_origins is None:
        configured_origins = os.environ.get(
            "ATLAS_ALLOWED_ORIGINS",
            "http://127.0.0.1:4175,http://localhost:4175",
        )
        allowed_origins = [
            origin.strip() for origin in configured_origins.split(",") if origin.strip()
        ]
    store = AtlasStore(database)
    broker = LiveBroker()
    observer_config = json.loads(os.environ.get("ATLAS_OBSERVER_CONFIG", "{}"))
    if observer_tokens is None:
        tokens_file = os.environ.get("ATLAS_OBSERVER_TOKENS_FILE")
        if tokens_file:
            with Path(tokens_file).open(encoding="utf-8") as stream:
                observer_tokens = json.load(stream)
        else:
            observer_tokens = json.loads(os.environ.get("ATLAS_OBSERVER_TOKENS", "{}"))
    if not isinstance(observer_tokens, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in observer_tokens.items()
    ):
        raise RuntimeError("observer tokens must be a string-to-string JSON object")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        store.close()

    app = FastAPI(title="ATLAS API", version="0.3.2", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Atlas-Ingest-Token"],
    )
    app.state.store = store
    app.state.broker = broker

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        stats = store.stats()
        return {
            "status": "ok",
            "schema_version": 1,
            "single_observer_mode": stats["observers"] < 2,
            "multi_observer_field_validated": False,
            **stats,
        }

    @app.get("/api/v1/observers")
    def observers() -> list[dict[str, Any]]:
        rows = store.list_observers()
        for row in rows:
            row.update(observer_config.get(row["observer_id"], {}))
        return rows

    @app.get("/api/v1/observer-health")
    def observer_health() -> list[dict[str, Any]]:
        rows = {row["observer_id"]: row for row in store.list_observer_health()}
        now = datetime.now(timezone.utc)
        for observer_id, configured in observer_config.items():
            row = rows.setdefault(observer_id, {"observer_id": observer_id, "last_heartbeat": None})
            row.update({key: value for key, value in configured.items() if key not in row})
        for row in rows.values():
            timestamp = row.get("last_heartbeat")
            age = None
            if timestamp:
                age = max(
                    0.0,
                    (
                        now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    ).total_seconds(),
                )
            row["heartbeat_age_seconds"] = age
            row["status"] = (
                "offline"
                if age is None or age > 180
                else "degraded"
                if not row.get("mux_connected") or row.get("delivery_queue_depth", 0) > 0
                else "online"
            )
        return sorted(rows.values(), key=lambda row: row["observer_id"])

    @app.get("/api/v1/transmissions")
    def transmissions(
        limit: int = Query(100, ge=1, le=1000), before: str | None = None
    ) -> list[dict[str, Any]]:
        return store.list_transmissions(limit, before)

    @app.get("/api/v1/transmissions/{transmission_id}")
    def transmission(transmission_id: int) -> dict[str, Any]:
        result = store.get_transmission(transmission_id)
        if result is None:
            raise HTTPException(404, "transmission not found")
        return result

    @app.get("/api/v1/observations")
    def observations(
        limit: int = Query(100, ge=1, le=1000),
        observer_id: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        return store.list_observations(limit, observer_id, before)

    @app.get("/api/v1/positions")
    def positions(
        limit: int = Query(500, ge=1, le=5000),
        node_num: int | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        return store.list_positions(limit, node_num, before)

    @app.get("/api/v1/nodes")
    def nodes(limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
        return store.list_nodes(limit)

    @app.get("/api/v1/node-summaries")
    def node_summaries(limit: int = Query(5000, ge=1, le=10000)) -> list[dict[str, Any]]:
        return store.list_node_summaries(limit)

    @app.get("/api/v1/quality")
    def quality() -> dict[str, Any]:
        return store.quality_metrics()

    @app.get("/api/v1/quality/gateways")
    def quality_gateways(hours: float = Query(24, gt=0, le=24 * 30)) -> list[dict[str, Any]]:
        return store.list_gateway_quality(hours)

    @app.get("/api/v1/quality/gateways/{node_num}")
    def quality_gateway(
        node_num: int,
        hours: float = Query(24, gt=0, le=24 * 30),
    ) -> dict[str, Any]:
        result = store.get_gateway_quality(node_num, hours)
        if result is None:
            raise HTTPException(404, "gateway not found")
        return result

    @app.get("/api/v1/quality/packets")
    def quality_packets(
        limit: int = Query(100, ge=1, le=2000),
        hours: float = Query(24, gt=0, le=24 * 30),
    ) -> list[dict[str, Any]]:
        return store.list_logical_packets(limit, hours)

    @app.get("/api/v1/quality/packets/{sender}/{packet_id}")
    def quality_packet(sender: int, packet_id: int) -> dict[str, Any]:
        result = store.get_logical_packet(sender, packet_id)
        if result is None:
            raise HTTPException(404, "logical packet not found")
        return result

    @app.get("/api/v1/quality/warnings")
    def quality_warnings(
        limit: int = Query(200, ge=1, le=2000),
        hours: float = Query(24, gt=0, le=24 * 30),
    ) -> list[dict[str, Any]]:
        return store.list_quality_warnings(limit, hours)

    @app.get("/api/v1/nodes/{node_num}")
    def node_summary(node_num: int) -> dict[str, Any]:
        result = store.get_node_detail(node_num)
        if result is None:
            raise HTTPException(404, "node not found")
        return result

    @app.get("/api/v1/nodes/{node_num}/activity")
    def node_activity(
        node_num: int,
        limit: int = Query(250, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        if store.get_node_summary(node_num) is None:
            raise HTTPException(404, "node not found")
        return store.list_node_activity(node_num, limit)

    @app.get("/api/v1/coverage/measurements")
    def coverage_measurements(
        limit: int = Query(5000, ge=1, le=20000),
        include_neighbors: bool = True,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)

        def age_seconds(value: str) -> float:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return max(0.0, (now - parsed).total_seconds())

        result: list[dict[str, Any]] = []
        for row in store.list_rf_measurements(limit):
            configured = observer_config.get(row["observer_id"], {})
            receiver_lat = configured.get("latitude", row.get("receiver_latitude"))
            receiver_lon = configured.get("longitude", row.get("receiver_longitude"))
            distance = None
            if isinstance(receiver_lat, (int, float)) and isinstance(receiver_lon, (int, float)):
                distance = _haversine_km(
                    row["latitude"], row["longitude"], receiver_lat, receiver_lon
                )
            result.append(
                {
                    **row,
                    "receiver_node": configured.get("observer_node_num", row["receiver_node"]),
                    "receiver_latitude": receiver_lat,
                    "receiver_longitude": receiver_lon,
                    "distance_km": distance,
                    "age_seconds": age_seconds(row["observed_at"]),
                    "position_age_seconds": max(
                        0.0,
                        age_seconds(row["position_observed_at"]) - age_seconds(row["observed_at"]),
                    ),
                    "confidence": "measured",
                }
            )
        if include_neighbors:
            for row in store.list_neighbor_measurements(min(limit, 5000)):
                result.append(
                    {
                        **row,
                        "transmitter_node": row["neighbor_node"],
                        "receiver_node": row["reporter_node"],
                        "rssi": None,
                        "hops": 0,
                        "evidence": "NEIGHBORINFO_SNR",
                        "distance_km": _haversine_km(
                            row["latitude"],
                            row["longitude"],
                            row["receiver_latitude"],
                            row["receiver_longitude"],
                        ),
                        "age_seconds": age_seconds(row["observed_at"]),
                        "position_age_seconds": max(
                            0.0,
                            age_seconds(row["position_observed_at"])
                            - age_seconds(row["observed_at"]),
                        ),
                        "confidence": "neighbor_report",
                    }
                )
        result.sort(key=lambda item: item["observed_at"], reverse=True)
        return result[:limit]

    @app.get("/api/v1/activity")
    def activity(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return store.list_activity(limit)

    @app.get("/api/v1/packet-lifecycles")
    def packet_lifecycles(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return store.list_packet_lifecycles(limit)

    def authorize(token: str | None, observer_id: str | None = None) -> None:
        if not observer_tokens and not ingest_token:
            raise HTTPException(503, "live ingestion is disabled")
        expected = observer_tokens.get(observer_id) if observer_id else None
        expected = expected or ingest_token
        if not expected:
            raise HTTPException(401, "observer is not authorized")
        if token is None or not hmac.compare_digest(token, expected):
            raise HTTPException(401, "invalid ingestion token")

    @app.post("/api/v1/events", status_code=202)
    async def ingest_event(
        event: dict[str, Any], x_atlas_ingest_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_atlas_ingest_token, event.get("observer_id"))
        if event.get("simulated"):
            raise HTTPException(422, "simulated events cannot be persisted")
        try:
            accepted = store.ingest(event)
        except EventValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        if accepted:
            await broker.publish(event)
        return {"accepted": accepted, "duplicate": not accepted}

    @app.post("/api/v1/simulate", status_code=202)
    async def simulate(
        event: dict[str, Any], x_atlas_ingest_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(x_atlas_ingest_token)
        if not allow_simulation:
            raise HTTPException(403, "simulation is disabled")
        simulated = {**event, "simulated": True, "persistence": "broadcast_only"}
        await broker.publish(simulated)
        return {"broadcast": True, "persisted": False}

    @app.get("/api/v1/live", response_class=EventSourceResponse)
    async def live() -> AsyncIterator[ServerSentEvent]:
        async with broker.subscribe() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield ServerSentEvent(
                        data=json.dumps(event, separators=(",", ":"), sort_keys=True),
                        event=event.get("event", "atlas_event"),
                        id=event.get("event_id"),
                    )
                except TimeoutError:
                    yield ServerSentEvent(comment="keepalive")

    return app
