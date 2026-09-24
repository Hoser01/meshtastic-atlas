"""SQLite development store with lossless observations and logical transmissions."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS transmissions (
    id INTEGER PRIMARY KEY,
    packet_id INTEGER NOT NULL,
    from_node INTEGER NOT NULL,
    to_node INTEGER NOT NULL,
    portnum TEXT,
    encrypted INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(packet_id, from_node, to_node)
);

CREATE TABLE IF NOT EXISTS observations (
    event_id TEXT PRIMARY KEY,
    transmission_id INTEGER NOT NULL REFERENCES transmissions(id),
    observer_id TEXT NOT NULL,
    observer_node_num INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    rx_rssi INTEGER,
    rx_snr REAL,
    hop_start INTEGER,
    hop_limit INTEGER,
    source TEXT NOT NULL,
    raw_event TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS observations_transmission_idx
ON observations(transmission_id, observed_at);
CREATE INDEX IF NOT EXISTS observations_observer_idx
ON observations(observer_id, observed_at);
CREATE INDEX IF NOT EXISTS observations_time_idx
ON observations(observed_at DESC);
CREATE INDEX IF NOT EXISTS observations_from_time_idx
ON observations(json_extract(raw_event, '$.from_node'), observed_at DESC);
CREATE INDEX IF NOT EXISTS observations_to_time_idx
ON observations(json_extract(raw_event, '$.to_node'), observed_at DESC);
CREATE INDEX IF NOT EXISTS observations_packet_idx
ON observations(json_extract(raw_event, '$.from_node'), json_extract(raw_event, '$.packet_id'));

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    observer_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_event TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time_idx
ON events(observed_at DESC);
CREATE INDEX IF NOT EXISTS events_from_time_idx
ON events(json_extract(raw_event, '$.from_node'), observed_at DESC);
CREATE INDEX IF NOT EXISTS events_to_time_idx
ON events(json_extract(raw_event, '$.to_node'), observed_at DESC);
CREATE INDEX IF NOT EXISTS events_packet_idx
ON events(json_extract(raw_event, '$.from_node'), json_extract(raw_event, '$.packet_id'));

CREATE TABLE IF NOT EXISTS node_identity_snapshots (
    observer_id TEXT NOT NULL,
    node_num INTEGER NOT NULL,
    identity_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (observer_id, node_num)
);

CREATE TABLE IF NOT EXISTS positions (
    event_id TEXT PRIMARY KEY,
    node_num INTEGER NOT NULL,
    observer_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude >= -90 AND latitude <= 90),
    longitude REAL NOT NULL CHECK(longitude >= -180 AND longitude <= 180),
    altitude INTEGER,
    position_timestamp INTEGER,
    precision_bits INTEGER,
    gps_accuracy INTEGER,
    sats_in_view INTEGER,
    fix_quality INTEGER,
    fix_type INTEGER,
    rx_rssi INTEGER,
    rx_snr REAL
);

CREATE INDEX IF NOT EXISTS positions_node_time_idx
ON positions(node_num, observed_at DESC);
CREATE INDEX IF NOT EXISTS positions_time_idx
ON positions(observed_at DESC);

CREATE TABLE IF NOT EXISTS telemetry (
    event_id TEXT PRIMARY KEY,
    node_num INTEGER NOT NULL,
    observer_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    telemetry_time INTEGER,
    variant TEXT NOT NULL,
    metrics TEXT NOT NULL,
    rx_rssi INTEGER,
    rx_snr REAL
);
CREATE INDEX IF NOT EXISTS telemetry_node_time_idx
ON telemetry(node_num, observed_at DESC);
CREATE INDEX IF NOT EXISTS telemetry_time_idx
ON telemetry(observed_at DESC);

CREATE TABLE IF NOT EXISTS node_metadata (
    node_num INTEGER PRIMARY KEY,
    node_id TEXT,
    long_name TEXT,
    short_name TEXT,
    hardware_model TEXT,
    role TEXT,
    firmware_version TEXT,
    device_state_version INTEGER,
    has_wifi INTEGER,
    has_bluetooth INTEGER,
    has_ethernet INTEGER,
    has_remote_hardware INTEGER,
    has_pki INTEGER,
    is_licensed INTEGER,
    is_unmessagable INTEGER,
    updated_at TEXT NOT NULL,
    observer_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packet_lifecycles (
    observer_id TEXT NOT NULL,
    packet_id INTEGER NOT NULL,
    from_node INTEGER,
    to_node INTEGER,
    portnum TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_event_id TEXT NOT NULL,
    PRIMARY KEY (observer_id, packet_id)
);
CREATE INDEX IF NOT EXISTS packet_lifecycles_updated_idx
ON packet_lifecycles(updated_at DESC);

CREATE TABLE IF NOT EXISTS rf_measurements (
    event_id TEXT PRIMARY KEY,
    observer_id TEXT NOT NULL,
    receiver_node INTEGER NOT NULL,
    transmitter_node INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    position_observed_at TEXT NOT NULL,
    rssi INTEGER,
    snr REAL,
    hops INTEGER NOT NULL,
    evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rf_measurements_time_idx ON rf_measurements(observed_at DESC);

CREATE TABLE IF NOT EXISTS neighbor_links (
    event_id TEXT NOT NULL,
    observer_id TEXT NOT NULL,
    reporter_node INTEGER NOT NULL,
    neighbor_node INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    neighbor_last_rx_time INTEGER,
    snr REAL,
    PRIMARY KEY (event_id, reporter_node, neighbor_node)
);
CREATE INDEX IF NOT EXISTS neighbor_links_time_idx ON neighbor_links(observed_at DESC);

CREATE TABLE IF NOT EXISTS node_summaries (
    node_num INTEGER PRIMARY KEY,
    first_heard TEXT NOT NULL,
    last_heard TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    last_event_type TEXT NOT NULL,
    last_source TEXT NOT NULL,
    last_observer_id TEXT NOT NULL,
    last_rssi INTEGER,
    last_snr REAL,
    last_packet_seen TEXT,
    sent_observations INTEGER NOT NULL DEFAULT 0,
    received_observations INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS node_summaries_last_heard_idx
ON node_summaries(last_heard DESC);
"""


class EventValidationError(ValueError):
    pass


def validate_event(event: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "event_id",
        "event",
        "observer_id",
        "observer_node_num",
        "observer_node_id",
        "observed_at",
    }
    missing = required - event.keys()
    if missing:
        raise EventValidationError(f"missing required fields: {', '.join(sorted(missing))}")
    if event["schema_version"] != 1:
        raise EventValidationError(f"unsupported schema_version: {event['schema_version']}")
    if not isinstance(event["event_id"], str) or len(event["event_id"]) < 16:
        raise EventValidationError("event_id must be a string of at least 16 characters")


class AtlasStore:
    def __init__(self, path: Path | str) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._ensure_node_metadata_columns()
        self._ensure_node_summary_columns()
        self._normalize_mqtt_provenance()
        self._backfill_coverage_evidence()
        self._backfill_node_summaries()

    def _ensure_node_metadata_columns(self) -> None:
        existing = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(node_metadata)")
        }
        additions = {
            "firmware_version": "TEXT",
            "device_state_version": "INTEGER",
            "has_wifi": "INTEGER",
            "has_bluetooth": "INTEGER",
            "has_ethernet": "INTEGER",
            "has_remote_hardware": "INTEGER",
            "has_pki": "INTEGER",
            "is_licensed": "INTEGER",
            "is_unmessagable": "INTEGER",
        }
        for column, data_type in additions.items():
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE node_metadata ADD COLUMN {column} {data_type}"
                )
        self.connection.commit()

    def _ensure_node_summary_columns(self) -> None:
        existing = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(node_summaries)")
        }
        added = "last_packet_seen" not in existing
        if added:
            self.connection.execute("ALTER TABLE node_summaries ADD COLUMN last_packet_seen TEXT")
        needs_backfill = added or (
            self.connection.execute("SELECT count(*) FROM node_summaries").fetchone()[0] > 0
            and self.connection.execute(
                "SELECT count(*) FROM node_summaries WHERE last_packet_seen IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        if needs_backfill:
            self.connection.executescript(
                """
                CREATE TEMP TABLE atlas_packet_activity AS
                WITH packet_activity AS (
                    SELECT json_extract(raw_event, '$.from_node') AS node_num,
                           observed_at AS seen_at
                    FROM observations
                    WHERE json_type(raw_event, '$.packet_id')='integer'
                    UNION ALL
                    SELECT json_extract(raw_event, '$.to_node'), observed_at FROM observations
                    WHERE json_type(raw_event, '$.packet_id')='integer'
                    UNION ALL
                    SELECT json_extract(raw_event, '$.from_node'), observed_at
                    FROM events
                    WHERE json_type(raw_event, '$.packet_id')='integer'
                    UNION ALL
                    SELECT json_extract(raw_event, '$.to_node'), observed_at FROM events
                    WHERE json_type(raw_event, '$.packet_id')='integer'
                )
                SELECT node_num, max(seen_at) AS latest FROM packet_activity
                WHERE node_num IS NOT NULL AND node_num NOT IN (0, 4294967295)
                GROUP BY node_num;
                CREATE INDEX atlas_packet_activity_node_idx ON atlas_packet_activity(node_num);
                UPDATE node_summaries SET last_packet_seen=(
                    SELECT latest FROM atlas_packet_activity
                    WHERE atlas_packet_activity.node_num=node_summaries.node_num
                );
                DROP TABLE atlas_packet_activity;
                """
            )
        self.connection.commit()

    def _normalize_mqtt_provenance(self) -> None:
        """Keep detailed MQTT classification while exposing stable UI provenance."""
        self.connection.execute(
            """
            UPDATE events
            SET raw_event=json_set(raw_event, '$.source', 'MQTT_NETWORK')
            WHERE event_type='network_packet'
              AND json_extract(raw_event, '$.transport_source')='LZ_MQTT'
              AND json_extract(raw_event, '$.source')!='MQTT_NETWORK'
            """
        )
        self.connection.execute(
            """
            UPDATE positions SET source='MQTT_NETWORK'
            WHERE event_id IN (
                SELECT event_id FROM events
                WHERE event_type='network_packet'
                  AND json_extract(raw_event, '$.transport_source')='LZ_MQTT'
            ) AND source!='MQTT_NETWORK'
            """
        )
        self.connection.commit()

    def _backfill_coverage_evidence(self) -> None:
        """Seed new evidence tables from the lossless event archive on upgrade."""
        if self.connection.execute("SELECT count(*) FROM rf_measurements").fetchone()[0] == 0:
            for row in self.connection.execute("SELECT raw_event FROM observations"):
                self._ingest_rf_measurement(json.loads(row["raw_event"]))
        if self.connection.execute("SELECT count(*) FROM neighbor_links").fetchone()[0] == 0:
            archives = "SELECT raw_event FROM observations UNION ALL SELECT raw_event FROM events"
            for row in self.connection.execute(archives):
                self._ingest_neighbor_info(json.loads(row["raw_event"]))
        self.connection.commit()

    def _backfill_node_summaries(self) -> None:
        if self.connection.execute("SELECT count(*) FROM node_summaries").fetchone()[0]:
            return
        archives = (
            "SELECT observed_at, raw_event FROM observations UNION ALL "
            "SELECT observed_at, raw_event FROM events ORDER BY observed_at"
        )
        for row in self.connection.execute(archives):
            self._ingest_node_summary(json.loads(row["raw_event"]))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def ingest(self, event: dict[str, Any]) -> bool:
        """Ingest once by event_id. Return False when the event is a duplicate."""
        with self.lock:
            validate_event(event)
            raw = json.dumps(event, separators=(",", ":"), sort_keys=True)
            if event["event"] == "rf_observation":
                return self._ingest_observation(event, raw)
            if event["event"] == "node_identity" and event.get("source") == "NODE_DB":
                self._ingest_node_info(event)
                node_num = event.get("from_node")
                if isinstance(node_num, int):
                    identity_json = json.dumps(
                        {
                            "node_info": event.get("node_info"),
                            "device_metadata": event.get("device_metadata"),
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    previous = self.connection.execute(
                        """
                        SELECT identity_json FROM node_identity_snapshots
                        WHERE observer_id=? AND node_num=?
                        """,
                        (event["observer_id"], node_num),
                    ).fetchone()
                    self.connection.execute(
                        """
                        INSERT INTO node_identity_snapshots
                            (observer_id, node_num, identity_json, observed_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(observer_id, node_num) DO UPDATE SET
                            identity_json=excluded.identity_json,
                            observed_at=excluded.observed_at
                        """,
                        (event["observer_id"], node_num, identity_json, event["observed_at"]),
                    )
                    if previous is not None and previous["identity_json"] == identity_json:
                        self.connection.commit()
                        return False
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    event["event_id"],
                    event["event"],
                    event["observer_id"],
                    event["observed_at"],
                    raw,
                ),
            )
            if cursor.rowcount == 1:
                self._ingest_position(event)
                self._ingest_telemetry(event)
                self._ingest_node_info(event)
                self._ingest_lifecycle(event)
                self._ingest_neighbor_info(event)
                self._ingest_node_summary(event)
            self.connection.commit()
            return cursor.rowcount == 1

    def _ingest_lifecycle(self, event: dict[str, Any]) -> None:
        status = event.get("lifecycle_status")
        packet_id = event.get("packet_id")
        if not isinstance(status, str) or not isinstance(packet_id, int):
            return
        self.connection.execute(
            """
            INSERT INTO packet_lifecycles
                (observer_id, packet_id, from_node, to_node, portnum, status,
                 created_at, updated_at, evidence_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observer_id, packet_id) DO UPDATE SET
                from_node=coalesce(packet_lifecycles.from_node, excluded.from_node),
                to_node=coalesce(packet_lifecycles.to_node, excluded.to_node),
                portnum=coalesce(packet_lifecycles.portnum, excluded.portnum),
                status=excluded.status,
                updated_at=excluded.updated_at,
                evidence_event_id=excluded.evidence_event_id
            WHERE excluded.updated_at >= packet_lifecycles.updated_at
            """,
            (
                event["observer_id"],
                packet_id,
                event.get("from_node"),
                event.get("to_node"),
                event.get("portnum"),
                status,
                event["observed_at"],
                event["observed_at"],
                event["event_id"],
            ),
        )

    def list_packet_lifecycles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM packet_lifecycles ORDER BY updated_at DESC LIMIT ?", (limit,)
                )
            ]
        now = datetime.now(timezone.utc)
        for row in rows:
            if row["status"] in {"QUEUED", "FORWARDED", "RADIO_ACCEPTED"}:
                updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
                if (now - updated).total_seconds() > 120:
                    row["status"] = "EXPIRED"
        return rows

    def _ingest_observation(self, event: dict[str, Any], raw: str) -> bool:
        for field in ("packet_id", "from_node", "to_node"):
            if field not in event:
                raise EventValidationError(f"rf_observation missing {field}")
        existing = self.connection.execute(
            "SELECT 1 FROM observations WHERE event_id = ?", (event["event_id"],)
        ).fetchone()
        if existing:
            return False

        self.connection.execute(
            """
            INSERT INTO transmissions
                (packet_id, from_node, to_node, portnum, encrypted, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(packet_id, from_node, to_node) DO UPDATE SET
                first_seen = min(first_seen, excluded.first_seen),
                last_seen = max(last_seen, excluded.last_seen),
                portnum = coalesce(transmissions.portnum, excluded.portnum),
                encrypted = max(transmissions.encrypted, excluded.encrypted)
            """,
            (
                event["packet_id"],
                event["from_node"],
                event["to_node"],
                event.get("portnum"),
                int(bool(event.get("encrypted"))),
                event["observed_at"],
                event["observed_at"],
            ),
        )
        transmission = self.connection.execute(
            "SELECT id FROM transmissions WHERE packet_id=? AND from_node=? AND to_node=?",
            (event["packet_id"], event["from_node"], event["to_node"]),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO observations
                (event_id, transmission_id, observer_id, observer_node_num, observed_at,
                 rx_rssi, rx_snr, hop_start, hop_limit, source, raw_event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                transmission["id"],
                event["observer_id"],
                event["observer_node_num"],
                event["observed_at"],
                event.get("rx_rssi"),
                event.get("rx_snr"),
                event.get("hop_start"),
                event.get("hop_limit"),
                event.get("source", "RF_OBSERVED"),
                raw,
            ),
        )
        self._ingest_position(event)
        self._ingest_telemetry(event)
        self._ingest_node_info(event)
        self._ingest_rf_measurement(event)
        self._ingest_neighbor_info(event)
        self._ingest_node_summary(event)
        self.connection.commit()
        return True

    def _ingest_node_summary(self, event: dict[str, Any]) -> None:
        sender = event.get("from_node")
        if not isinstance(sender, int):
            return
        observed_at = event["observed_at"]
        source = str(event.get("source", "UNKNOWN"))
        packet_seen = observed_at if isinstance(event.get("packet_id"), int) else None
        values = (
            sender,
            observed_at,
            observed_at,
            event["event_id"],
            event["event"],
            source,
            event["observer_id"],
            event.get("rx_rssi"),
            event.get("rx_snr"),
            packet_seen,
        )
        self.connection.execute(
            """
            INSERT INTO node_summaries
                (node_num, first_heard, last_heard, last_event_id, last_event_type,
                 last_source, last_observer_id, last_rssi, last_snr, last_packet_seen,
                 sent_observations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(node_num) DO UPDATE SET
                first_heard=min(node_summaries.first_heard, excluded.first_heard),
                last_heard=max(node_summaries.last_heard, excluded.last_heard),
                last_event_id=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_event_id ELSE node_summaries.last_event_id END,
                last_event_type=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_event_type ELSE node_summaries.last_event_type END,
                last_source=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_source ELSE node_summaries.last_source END,
                last_observer_id=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_observer_id ELSE node_summaries.last_observer_id END,
                last_rssi=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_rssi ELSE node_summaries.last_rssi END,
                last_snr=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_snr ELSE node_summaries.last_snr END,
                last_packet_seen=CASE
                    WHEN excluded.last_packet_seen IS NOT NULL AND
                         (node_summaries.last_packet_seen IS NULL OR
                          excluded.last_packet_seen>=node_summaries.last_packet_seen)
                    THEN excluded.last_packet_seen ELSE node_summaries.last_packet_seen END,
                sent_observations=node_summaries.sent_observations+1
            """,
            values,
        )
        destination = event.get("to_node")
        if not isinstance(destination, int) or destination in {0, 0xFFFFFFFF}:
            return
        self.connection.execute(
            """
            INSERT INTO node_summaries
                (node_num, first_heard, last_heard, last_event_id, last_event_type,
                 last_source, last_observer_id, last_rssi, last_snr, last_packet_seen,
                 received_observations)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 1)
            ON CONFLICT(node_num) DO UPDATE SET
                first_heard=min(node_summaries.first_heard, excluded.first_heard),
                last_heard=max(node_summaries.last_heard, excluded.last_heard),
                last_event_id=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_event_id ELSE node_summaries.last_event_id END,
                last_event_type=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_event_type ELSE node_summaries.last_event_type END,
                last_source=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_source ELSE node_summaries.last_source END,
                last_observer_id=CASE WHEN excluded.last_heard>=node_summaries.last_heard
                    THEN excluded.last_observer_id ELSE node_summaries.last_observer_id END,
                last_packet_seen=CASE
                    WHEN excluded.last_packet_seen IS NOT NULL AND
                         (node_summaries.last_packet_seen IS NULL OR
                          excluded.last_packet_seen>=node_summaries.last_packet_seen)
                    THEN excluded.last_packet_seen ELSE node_summaries.last_packet_seen END,
                received_observations=node_summaries.received_observations+1
            """,
            (
                destination,
                observed_at,
                observed_at,
                event["event_id"],
                event["event"],
                source,
                event["observer_id"],
                packet_seen,
            ),
        )

    def _ingest_rf_measurement(self, event: dict[str, Any]) -> None:
        if event.get("source") != "RF_OBSERVED":
            return
        hop_start = int(event.get("hop_start") or 0)
        hop_limit = int(event.get("hop_limit") or 0)
        hops = max(0, hop_start - hop_limit)
        if hops != 0:
            return
        sender = event.get("from_node")
        receiver = event.get("observer_node_num")
        if not isinstance(sender, int) or not isinstance(receiver, int):
            return
        position = self.connection.execute(
            """
            SELECT latitude, longitude, observed_at FROM positions
            WHERE node_num=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 1
            """,
            (sender, event["observed_at"]),
        ).fetchone()
        if position is None:
            return
        self.connection.execute(
            """
            INSERT OR IGNORE INTO rf_measurements
                (event_id, observer_id, receiver_node, transmitter_node, observed_at,
                 latitude, longitude, position_observed_at, rssi, snr, hops, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["observer_id"],
                receiver,
                sender,
                event["observed_at"],
                position["latitude"],
                position["longitude"],
                position["observed_at"],
                event.get("rx_rssi"),
                event.get("rx_snr"),
                hops,
                "DIRECT_RF_OBSERVATION",
            ),
        )

    def _ingest_neighbor_info(self, event: dict[str, Any]) -> None:
        info = event.get("neighbor_info")
        if not isinstance(info, dict):
            return
        reporter = info.get("reporter_node", event.get("from_node"))
        if not isinstance(reporter, int):
            return
        for neighbor in info.get("neighbors", []):
            node_num = neighbor.get("node_num") if isinstance(neighbor, dict) else None
            if not isinstance(node_num, int):
                continue
            self.connection.execute(
                """
                INSERT OR IGNORE INTO neighbor_links
                    (event_id, observer_id, reporter_node, neighbor_node, observed_at,
                     neighbor_last_rx_time, snr)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["observer_id"],
                    reporter,
                    node_num,
                    event["observed_at"],
                    neighbor.get("last_rx_time"),
                    neighbor.get("snr"),
                ),
            )

    def _ingest_position(self, event: dict[str, Any]) -> None:
        position = event.get("position")
        if not isinstance(position, dict):
            return
        latitude = position.get("latitude")
        longitude = position.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            raise EventValidationError("position requires numeric latitude and longitude")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise EventValidationError("position coordinates are out of range")
        node_num = event.get("from_node", event.get("local_node_num"))
        if not isinstance(node_num, int):
            raise EventValidationError("position event requires a source node")
        self.connection.execute(
            """
            INSERT OR IGNORE INTO positions
                (event_id, node_num, observer_id, observed_at, source, latitude, longitude,
                 altitude, position_timestamp, precision_bits, gps_accuracy, sats_in_view,
                 fix_quality, fix_type, rx_rssi, rx_snr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                node_num,
                event["observer_id"],
                event["observed_at"],
                event.get("source", "UNKNOWN"),
                latitude,
                longitude,
                position.get("altitude"),
                position.get("timestamp"),
                position.get("precision_bits"),
                position.get("gps_accuracy"),
                position.get("sats_in_view"),
                position.get("fix_quality"),
                position.get("fix_type"),
                event.get("rx_rssi"),
                event.get("rx_snr"),
            ),
        )

    def _ingest_telemetry(self, event: dict[str, Any]) -> None:
        telemetry = event.get("telemetry")
        node_num = event.get("from_node")
        if not isinstance(telemetry, dict) or not isinstance(node_num, int):
            return
        variant = telemetry.get("variant")
        metrics = telemetry.get("metrics")
        if not isinstance(variant, str) or not isinstance(metrics, dict):
            raise EventValidationError("telemetry requires a variant and metrics object")
        self.connection.execute(
            """
            INSERT OR IGNORE INTO telemetry
                (event_id, node_num, observer_id, observed_at, source, telemetry_time,
                 variant, metrics, rx_rssi, rx_snr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                node_num,
                event["observer_id"],
                event["observed_at"],
                event.get("source", "UNKNOWN"),
                telemetry.get("time"),
                variant,
                json.dumps(metrics, separators=(",", ":"), sort_keys=True),
                event.get("rx_rssi"),
                event.get("rx_snr"),
            ),
        )
        self.connection.execute(
            """
            DELETE FROM telemetry WHERE node_num=? AND event_id NOT IN (
                SELECT event_id FROM telemetry WHERE node_num=?
                ORDER BY observed_at DESC LIMIT 10000
            )
            """,
            (node_num, node_num),
        )

    def list_telemetry(self, limit: int = 500, node_num: int | None = None) -> list[dict[str, Any]]:
        with self.lock:
            where = " WHERE node_num=?" if node_num is not None else ""
            params: tuple[Any, ...] = (node_num, limit) if node_num is not None else (limit,)
            rows = self.connection.execute(
                f"SELECT * FROM telemetry{where} ORDER BY observed_at DESC LIMIT ?", params
            )
            result = [dict(row) for row in rows]
        for row in result:
            row["metrics"] = json.loads(row["metrics"])
        return result

    def latest_telemetry_by_node(self) -> dict[int, dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT t.* FROM telemetry t
                WHERE t.event_id=(
                    SELECT latest.event_id FROM telemetry latest
                    WHERE latest.node_num=t.node_num
                    ORDER BY latest.observed_at DESC LIMIT 1
                )
                """
            )
            result = {int(row["node_num"]): dict(row) for row in rows}
        for row in result.values():
            row["metrics"] = json.loads(row["metrics"])
        return result

    def _ingest_node_info(self, event: dict[str, Any]) -> None:
        node_info = event.get("node_info")
        device_metadata = event.get("device_metadata")
        node_num = event.get("from_node")
        if not isinstance(node_num, int) or not (
            isinstance(node_info, dict) or isinstance(device_metadata, dict)
        ):
            return
        node_info = node_info if isinstance(node_info, dict) else {}
        device_metadata = device_metadata if isinstance(device_metadata, dict) else {}
        self.connection.execute(
            """
            INSERT INTO node_metadata
                (node_num, node_id, long_name, short_name, hardware_model, role,
                 firmware_version, device_state_version, has_wifi, has_bluetooth,
                 has_ethernet, has_remote_hardware, has_pki, is_licensed,
                 is_unmessagable, updated_at, observer_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_num) DO UPDATE SET
                node_id=coalesce(excluded.node_id, node_metadata.node_id),
                long_name=coalesce(excluded.long_name, node_metadata.long_name),
                short_name=coalesce(excluded.short_name, node_metadata.short_name),
                hardware_model=coalesce(excluded.hardware_model, node_metadata.hardware_model),
                role=coalesce(excluded.role, node_metadata.role),
                firmware_version=coalesce(excluded.firmware_version, node_metadata.firmware_version),
                device_state_version=coalesce(excluded.device_state_version, node_metadata.device_state_version),
                has_wifi=coalesce(excluded.has_wifi, node_metadata.has_wifi),
                has_bluetooth=coalesce(excluded.has_bluetooth, node_metadata.has_bluetooth),
                has_ethernet=coalesce(excluded.has_ethernet, node_metadata.has_ethernet),
                has_remote_hardware=coalesce(excluded.has_remote_hardware, node_metadata.has_remote_hardware),
                has_pki=coalesce(excluded.has_pki, node_metadata.has_pki),
                is_licensed=coalesce(excluded.is_licensed, node_metadata.is_licensed),
                is_unmessagable=coalesce(excluded.is_unmessagable, node_metadata.is_unmessagable),
                updated_at=excluded.updated_at,
                observer_id=excluded.observer_id
            WHERE excluded.updated_at >= node_metadata.updated_at
            """,
            (
                node_num,
                node_info.get("node_id"),
                node_info.get("long_name"),
                node_info.get("short_name"),
                device_metadata.get("hardware_model") or node_info.get("hardware_model"),
                device_metadata.get("role") or node_info.get("role"),
                device_metadata.get("firmware_version") or None,
                device_metadata.get("device_state_version"),
                device_metadata.get("has_wifi"),
                device_metadata.get("has_bluetooth"),
                device_metadata.get("has_ethernet"),
                device_metadata.get("has_remote_hardware"),
                device_metadata.get("has_pki"),
                node_info.get("is_licensed"),
                node_info.get("is_unmessagable"),
                event["observed_at"],
                event["observer_id"],
            ),
        )

    def stats(self) -> dict[str, int]:
        with self.lock:
            result: dict[str, int] = {}
            for table in ("transmissions", "observations", "events", "positions"):
                result[table] = self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[
                    0
                ]
            result["observers"] = self.connection.execute(
                "SELECT count(DISTINCT observer_id) FROM observations"
            ).fetchone()[0]
            result["rf_observations"] = result["observations"]
            result["mqtt_packets"] = self.connection.execute(
                "SELECT count(*) FROM events WHERE event_type='network_packet'"
            ).fetchone()[0]
            result["positioned_nodes"] = self.connection.execute(
                "SELECT count(DISTINCT node_num) FROM positions"
            ).fetchone()[0]
            return result

    def list_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent normalized events, including RF observations, newest first."""
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT observed_at, raw_event FROM (
                    SELECT observed_at, raw_event FROM events
                    UNION ALL
                    SELECT observed_at, raw_event FROM observations
                ) ORDER BY observed_at DESC LIMIT ?
                """,
                (limit,),
            )
            return [json.loads(row["raw_event"]) for row in rows]

    def activity_timeline(self, minutes: int = 15) -> dict[str, Any]:
        """Return complete minute bins without sending every event to the browser."""
        end = datetime.now(timezone.utc)
        minute_start = end.replace(second=0, microsecond=0) - timedelta(minutes=minutes - 1)
        start = minute_start
        cutoff = start.isoformat().replace("+00:00", "Z")
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT minute, source, count(*) AS event_count FROM (
                    SELECT strftime('%Y-%m-%dT%H:%M:00Z', observed_at) AS minute,
                           json_extract(raw_event, '$.source') AS source
                    FROM events WHERE observed_at>=?
                    UNION ALL
                    SELECT strftime('%Y-%m-%dT%H:%M:00Z', observed_at) AS minute,
                           source
                    FROM observations WHERE observed_at>=?
                ) GROUP BY minute, source
                """,
                (cutoff, cutoff),
            ).fetchall()
        bins = [
            {
                "start": (minute_start + timedelta(minutes=index))
                .isoformat()
                .replace("+00:00", "Z"),
                "rf": 0,
                "mqtt": 0,
                "other": 0,
            }
            for index in range(minutes)
        ]
        for row in rows:
            if not row["minute"]:
                continue
            timestamp = datetime.fromisoformat(row["minute"].replace("Z", "+00:00"))
            index = int((timestamp - minute_start).total_seconds() // 60)
            if not 0 <= index < minutes:
                continue
            key = (
                "rf"
                if row["source"] == "RF_OBSERVED"
                else "mqtt"
                if row["source"] == "MQTT_NETWORK"
                else "other"
            )
            bins[index][key] += int(row["event_count"])
        return {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "bins": bins,
        }

    def list_transmissions(
        self, limit: int = 100, before: str | None = None
    ) -> list[dict[str, Any]]:
        with self.lock:
            query = """
                SELECT t.*, count(o.event_id) AS observation_count,
                       count(DISTINCT o.observer_id) AS observer_count
                FROM transmissions t JOIN observations o ON o.transmission_id=t.id
            """
            params: list[Any] = []
            if before:
                query += " WHERE t.last_seen < ?"
                params.append(before)
            query += " GROUP BY t.id ORDER BY t.last_seen DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in self.connection.execute(query, params)]

    def get_transmission(self, transmission_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM transmissions WHERE id=?", (transmission_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["observations"] = [
                dict(value)
                for value in self.connection.execute(
                    """
                    SELECT event_id, observer_id, observer_node_num, observed_at, rx_rssi,
                           rx_snr, hop_start, hop_limit, source
                    FROM observations WHERE transmission_id=? ORDER BY observed_at
                    """,
                    (transmission_id,),
                )
            ]
            return result

    def list_observations(
        self, limit: int = 100, observer_id: str | None = None, before: str | None = None
    ) -> list[dict[str, Any]]:
        with self.lock:
            conditions: list[str] = []
            params: list[Any] = []
            if observer_id:
                conditions.append("o.observer_id=?")
                params.append(observer_id)
            if before:
                conditions.append("o.observed_at<?")
                params.append(before)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)
            return [
                dict(row)
                for row in self.connection.execute(
                    f"""
                SELECT o.event_id, o.transmission_id, o.observer_id, o.observer_node_num,
                       o.observed_at, o.rx_rssi, o.rx_snr, o.hop_start, o.hop_limit,
                       t.packet_id, t.from_node, t.to_node, t.portnum, t.encrypted
                FROM observations o JOIN transmissions t ON t.id=o.transmission_id
                {where} ORDER BY o.observed_at DESC LIMIT ?
                """,
                    params,
                )
            ]

    def list_observers(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                SELECT r.*, m.node_id, m.long_name, m.short_name, m.hardware_model, m.role
                       ,p.latitude, p.longitude, p.observed_at AS position_observed_at
                FROM (
                    SELECT o.observer_id,
                           (SELECT latest.observer_node_num FROM observations latest
                            WHERE latest.observer_id=o.observer_id
                            ORDER BY latest.observed_at DESC LIMIT 1) AS observer_node_num,
                           count(*) AS observation_count,
                           count(DISTINCT transmission_id) AS transmission_count,
                           min(observed_at) AS first_observed_at,
                           max(observed_at) AS last_observed_at,
                           round(avg(rx_rssi), 2) AS average_rssi,
                           round(avg(rx_snr), 2) AS average_snr
                    FROM observations o GROUP BY o.observer_id
                ) r
                LEFT JOIN node_metadata m ON m.node_num=r.observer_node_num
                LEFT JOIN positions p ON p.event_id=(
                    SELECT latest_position.event_id FROM positions latest_position
                    WHERE latest_position.node_num=r.observer_node_num
                    ORDER BY latest_position.observed_at DESC LIMIT 1
                )
                ORDER BY r.observer_id
                """
                )
            ]

    def list_observer_health(self) -> list[dict[str, Any]]:
        """Return the latest collector heartbeat and RF activity for each observer."""
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT e.observer_id, e.observed_at AS last_heartbeat,
                       json_extract(e.raw_event, '$.observer_node_num') AS observer_node_num,
                       json_extract(e.raw_event, '$.mux_connected') AS mux_connected,
                       json_extract(e.raw_event, '$.delivery_queue_depth') AS delivery_queue_depth,
                       json_extract(e.raw_event, '$.collector_uptime_seconds') AS collector_uptime_seconds,
                       json_extract(e.raw_event, '$.mux_frames') AS mux_frames,
                       json_extract(e.raw_event, '$.connection_epoch') AS connection_epoch,
                       (SELECT max(o.observed_at) FROM observations o
                        WHERE o.observer_id=e.observer_id) AS last_rf_observation,
                       (SELECT count(*) FROM events errors
                        WHERE errors.observer_id=e.observer_id
                          AND errors.event_type='observer_connection_error'
                          AND errors.observed_at>=strftime(
                              '%Y-%m-%dT%H:%M:%fZ', 'now', '-24 hours'
                          )) AS errors_24h
                FROM events e
                JOIN (
                    SELECT observer_id, max(observed_at) AS latest
                    FROM events WHERE event_type='collector_heartbeat'
                    GROUP BY observer_id
                ) newest ON newest.observer_id=e.observer_id AND newest.latest=e.observed_at
                WHERE e.event_type='collector_heartbeat'
                ORDER BY e.observer_id
                """
            )
            return [dict(row) for row in rows]

    def list_positions(
        self, limit: int = 500, node_num: int | None = None, before: str | None = None
    ) -> list[dict[str, Any]]:
        with self.lock:
            conditions: list[str] = []
            params: list[Any] = []
            if node_num is not None:
                conditions.append("node_num=?")
                params.append(node_num)
            if before:
                conditions.append("observed_at<?")
                params.append(before)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)
            return [
                dict(row)
                for row in self.connection.execute(
                    f"SELECT * FROM positions{where} ORDER BY observed_at DESC LIMIT ?",
                    params,
                )
            ]

    def list_rf_measurements(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                SELECT r.*,
                       json_extract(o.raw_event, '$.packet_id') AS packet_id,
                       receiver.latitude AS receiver_latitude,
                       receiver.longitude AS receiver_longitude,
                       receiver.observed_at AS receiver_position_observed_at
                FROM rf_measurements r
                LEFT JOIN observations o ON o.event_id=r.event_id
                LEFT JOIN positions receiver ON receiver.event_id=(
                    SELECT p.event_id FROM positions p
                    WHERE p.node_num=r.receiver_node AND p.observed_at<=r.observed_at
                    ORDER BY p.observed_at DESC LIMIT 1
                )
                ORDER BY r.observed_at DESC LIMIT ?
                """,
                    (limit,),
                )
            ]

    def list_neighbor_measurements(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                SELECT n.*,
                       tx.latitude, tx.longitude, tx.observed_at AS position_observed_at,
                       rx.latitude AS receiver_latitude, rx.longitude AS receiver_longitude,
                       rx.observed_at AS receiver_position_observed_at
                FROM neighbor_links n
                JOIN positions tx ON tx.event_id=(
                    SELECT p.event_id FROM positions p WHERE p.node_num=n.neighbor_node
                    AND p.observed_at<=n.observed_at ORDER BY p.observed_at DESC LIMIT 1)
                JOIN positions rx ON rx.event_id=(
                    SELECT p.event_id FROM positions p WHERE p.node_num=n.reporter_node
                    AND p.observed_at<=n.observed_at ORDER BY p.observed_at DESC LIMIT 1)
                ORDER BY n.observed_at DESC LIMIT ?
                """,
                    (limit,),
                )
            ]

    def list_nodes(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return the latest truthful position for each node."""
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                SELECT p.*, m.long_name, m.short_name, m.hardware_model, m.role,
                       CASE WHEN p.source='RF_OBSERVED' AND p.observer_id LIKE 'MQTT:%'
                            THEN 'REMOTE_GATEWAY_RF' ELSE p.source END AS map_source
                FROM positions p
                JOIN (
                    SELECT node_num, max(observed_at) AS latest
                    FROM positions GROUP BY node_num
                ) newest ON newest.node_num=p.node_num AND newest.latest=p.observed_at
                LEFT JOIN node_metadata m ON m.node_num=p.node_num
                ORDER BY p.observed_at DESC LIMIT ?
                """,
                    (limit,),
                )
            ]

    def list_node_summaries(self, limit: int = 5000) -> list[dict[str, Any]]:
        """Return one row per known node, ordered by its latest accepted evidence."""
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                WITH source_counts AS (
                    SELECT from_node AS node_num,
                           sum(CASE WHEN source='RF_OBSERVED' AND observer_id NOT LIKE 'MQTT:%' THEN 1 ELSE 0 END) AS rf_observations,
                           sum(CASE WHEN source='RF_OBSERVED' AND observer_id LIKE 'MQTT:%' THEN 1 ELSE 0 END) AS remote_rf_observations,
                           sum(CASE WHEN source='MQTT_NETWORK' THEN 1 ELSE 0 END) AS mqtt_observations
                    FROM (
                        SELECT json_extract(raw_event, '$.from_node') AS from_node,
                               json_extract(raw_event, '$.source') AS source,
                               json_extract(raw_event, '$.observer_id') AS observer_id FROM observations
                        UNION ALL
                        SELECT json_extract(raw_event, '$.from_node') AS from_node,
                               json_extract(raw_event, '$.source') AS source,
                               json_extract(raw_event, '$.observer_id') AS observer_id FROM events
                    ) WHERE from_node IS NOT NULL GROUP BY from_node
                ), latest_direct_rf AS (
                    SELECT node_num, observer_id, observed_at FROM (
                        SELECT t.from_node AS node_num, o.observer_id, o.observed_at,
                               row_number() OVER (
                                   PARTITION BY t.from_node ORDER BY o.observed_at DESC
                               ) AS rank
                        FROM observations o
                        JOIN transmissions t ON t.id=o.transmission_id
                        WHERE o.observer_id NOT LIKE 'MQTT:%'
                    ) WHERE rank=1
                )
                SELECT s.*, m.node_id, m.long_name, m.short_name, m.hardware_model, m.role,
                       m.firmware_version, m.device_state_version, m.has_wifi,
                       m.has_bluetooth, m.has_ethernet, m.has_remote_hardware,
                       m.has_pki, m.is_licensed, m.is_unmessagable,
                       m.updated_at AS identity_updated_at,
                       p.latitude, p.longitude, p.altitude,
                       p.observed_at AS position_observed_at,
                       latest_rf.observer_id AS latest_rf_observer_id,
                       latest_rf.observed_at AS latest_rf_observed_at,
                       CASE WHEN p.event_id IS NULL THEN 0 ELSE 1 END AS positioned,
                       coalesce(c.rf_observations, 0) AS rf_observations,
                       coalesce(c.remote_rf_observations, 0) AS remote_rf_observations,
                       coalesce(c.mqtt_observations, 0) AS mqtt_observations,
                       CASE WHEN coalesce(c.rf_observations, 0)>0 THEN 'RF_OBSERVED'
                            WHEN coalesce(c.remote_rf_observations, 0)>0 THEN 'REMOTE_GATEWAY_RF'
                            WHEN coalesce(c.mqtt_observations, 0)>0 THEN 'MQTT_NETWORK'
                            ELSE s.last_source END AS display_provenance
                FROM node_summaries s
                LEFT JOIN node_metadata m ON m.node_num=s.node_num
                LEFT JOIN source_counts c ON c.node_num=s.node_num
                LEFT JOIN positions p ON p.event_id=(
                    SELECT latest.event_id FROM positions latest
                    WHERE latest.node_num=s.node_num
                    ORDER BY latest.observed_at DESC LIMIT 1
                )
                LEFT JOIN latest_direct_rf latest_rf ON latest_rf.node_num=s.node_num
                ORDER BY s.last_heard DESC LIMIT ?
                """,
                    (limit,),
                )
            ]

    def get_node_summary(self, node_num: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                """
                WITH source_counts AS (
                    SELECT from_node AS node_num,
                           sum(CASE WHEN source='RF_OBSERVED' AND observer_id NOT LIKE 'MQTT:%' THEN 1 ELSE 0 END) AS rf_observations,
                           sum(CASE WHEN source='RF_OBSERVED' AND observer_id LIKE 'MQTT:%' THEN 1 ELSE 0 END) AS remote_rf_observations,
                           sum(CASE WHEN source='MQTT_NETWORK' THEN 1 ELSE 0 END) AS mqtt_observations
                    FROM (
                        SELECT json_extract(raw_event, '$.from_node') AS from_node,
                               json_extract(raw_event, '$.source') AS source,
                               json_extract(raw_event, '$.observer_id') AS observer_id FROM observations
                        UNION ALL
                        SELECT json_extract(raw_event, '$.from_node') AS from_node,
                               json_extract(raw_event, '$.source') AS source,
                               json_extract(raw_event, '$.observer_id') AS observer_id FROM events
                    ) WHERE from_node=? GROUP BY from_node
                )
                SELECT s.*, m.node_id, m.long_name, m.short_name, m.hardware_model, m.role,
                       m.firmware_version, m.device_state_version, m.has_wifi,
                       m.has_bluetooth, m.has_ethernet, m.has_remote_hardware,
                       m.has_pki, m.is_licensed, m.is_unmessagable,
                       p.latitude, p.longitude, p.altitude,
                       p.observed_at AS position_observed_at,
                       CASE WHEN p.event_id IS NULL THEN 0 ELSE 1 END AS positioned,
                       coalesce(c.rf_observations, 0) AS rf_observations,
                       coalesce(c.remote_rf_observations, 0) AS remote_rf_observations,
                       coalesce(c.mqtt_observations, 0) AS mqtt_observations,
                       CASE WHEN coalesce(c.rf_observations, 0)>0 THEN 'RF_OBSERVED'
                            WHEN coalesce(c.remote_rf_observations, 0)>0 THEN 'REMOTE_GATEWAY_RF'
                            WHEN coalesce(c.mqtt_observations, 0)>0 THEN 'MQTT_NETWORK'
                            ELSE s.last_source END AS display_provenance
                FROM node_summaries s
                LEFT JOIN node_metadata m ON m.node_num=s.node_num
                LEFT JOIN source_counts c ON c.node_num=s.node_num
                LEFT JOIN positions p ON p.event_id=(
                    SELECT latest.event_id FROM positions latest
                    WHERE latest.node_num=s.node_num
                    ORDER BY latest.observed_at DESC LIMIT 1
                )
                WHERE s.node_num=?
                """,
                (node_num, node_num),
            ).fetchone()
            return dict(row) if row else None

    def get_node_detail(self, node_num: int) -> dict[str, Any] | None:
        """Return a node summary plus truthful RF recency and receiver evidence."""
        summary = self.get_node_summary(node_num)
        if summary is None:
            return None
        with self.lock:
            last_rf = self.connection.execute(
                """
                SELECT max(observed_at) FROM observations
                WHERE source='RF_OBSERVED'
                  AND (json_extract(raw_event, '$.from_node')=?
                    OR json_extract(raw_event, '$.to_node')=?)
                """,
                (node_num, node_num),
            ).fetchone()[0]
            heard_rows = self.connection.execute(
                """
                SELECT o.observer_id, o.observer_node_num,
                       max(o.observed_at) AS last_observed_at,
                       count(*) AS observation_count,
                       round(avg(o.rx_rssi), 1) AS average_rssi,
                       round(avg(o.rx_snr), 1) AS average_snr,
                       max(o.rx_rssi) AS best_rssi,
                       (SELECT max(0, coalesce(latest.hop_start, 0) - coalesce(latest.hop_limit, 0))
                        FROM observations latest
                        WHERE latest.observer_id=o.observer_id
                          AND json_extract(latest.raw_event, '$.from_node')=?
                        ORDER BY latest.observed_at DESC LIMIT 1) AS latest_hops,
                       m.node_id, m.short_name, m.long_name
                FROM observations o
                LEFT JOIN node_metadata m ON m.node_num=o.observer_node_num
                WHERE o.source='RF_OBSERVED'
                  AND json_extract(o.raw_event, '$.from_node')=?
                GROUP BY o.observer_id, o.observer_node_num
                ORDER BY last_observed_at DESC
                LIMIT 50
                """,
                (node_num, node_num),
            )
        return {
            **summary,
            "last_any_seen": summary["last_heard"],
            "last_rf_seen": last_rf,
            "recently_heard_by": [dict(row) for row in heard_rows],
        }

    def quality_metrics(self) -> dict[str, Any]:
        """Summarize logical packets separately from per-gateway observations."""
        with self.lock:
            packet_rows = self.connection.execute(
                """
                SELECT json_extract(raw_event, '$.from_node') AS sender,
                       json_extract(raw_event, '$.packet_id') AS packet_id,
                       json_extract(raw_event, '$.source') AS source,
                       json_extract(raw_event, '$.portnum') AS portnum,
                       json_extract(raw_event, '$.transport_source') AS transport_source,
                       json_extract(raw_event, '$.encrypted') AS encrypted,
                       observer_id, observed_at
                FROM observations
                WHERE json_extract(raw_event, '$.packet_id') IS NOT NULL
                  AND json_extract(raw_event, '$.from_node') IS NOT NULL
                UNION ALL
                SELECT json_extract(raw_event, '$.from_node'),
                       json_extract(raw_event, '$.packet_id'),
                       json_extract(raw_event, '$.source'),
                       json_extract(raw_event, '$.portnum'),
                       json_extract(raw_event, '$.transport_source'),
                       json_extract(raw_event, '$.encrypted'),
                       observer_id, observed_at
                FROM events
                WHERE event_type='network_packet'
                  AND json_extract(raw_event, '$.packet_id') IS NOT NULL
                  AND json_extract(raw_event, '$.from_node') IS NOT NULL
                """
            ).fetchall()
            connection_errors = self.connection.execute(
                "SELECT count(*) FROM events WHERE event_type='observer_connection_error'"
            ).fetchone()[0]

        packets: dict[tuple[int, int], list[sqlite3.Row]] = {}
        for row in packet_rows:
            key = (int(row["sender"]), int(row["packet_id"]))
            packets.setdefault(key, []).append(row)
        rf_unique = mqtt_unique = 0
        portnums: dict[str, int] = {}
        active_gateways: set[str] = set()
        mqtt_decodable = mqtt_encrypted_unknown = 0
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        for observations in packets.values():
            rf_wins = any(row["source"] == "RF_OBSERVED" for row in observations)
            if rf_wins:
                rf_unique += 1
            elif any(row["source"] == "MQTT_NETWORK" for row in observations):
                mqtt_unique += 1
            portnum = next((row["portnum"] for row in observations if row["portnum"]), "UNKNOWN")
            portnums[str(portnum)] = portnums.get(str(portnum), 0) + 1
            for row in observations:
                if row["transport_source"] != "LZ_MQTT":
                    continue
                if bool(row["encrypted"]):
                    mqtt_encrypted_unknown += 1
                else:
                    mqtt_decodable += 1
                observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
                if observed.timestamp() >= cutoff:
                    active_gateways.add(str(row["observer_id"]))
        total_observations = len(packet_rows)
        unique_packets = len(packets)
        return {
            "unique_packets": unique_packets,
            "repeated_observations": max(0, total_observations - unique_packets),
            "total_packet_observations": total_observations,
            "rf_unique_packets": rf_unique,
            "mqtt_unique_packets": mqtt_unique,
            "active_gateways_1h": len(active_gateways),
            "mqtt_decodable": mqtt_decodable,
            "mqtt_encrypted_unknown": mqtt_encrypted_unknown,
            "decrypt_success_percent": round(
                100 * mqtt_decodable / max(1, mqtt_decodable + mqtt_encrypted_unknown), 1
            ),
            "collector_errors": int(connection_errors),
            "packet_types": dict(sorted(portnums.items(), key=lambda item: item[1], reverse=True)),
            "deduplication_rule": "sender+packet_id; RF_OBSERVED wins provenance",
        }

    def _quality_packet_events(self, hours: float | None = None) -> list[dict[str, Any]]:
        conditions = ""
        params: list[Any] = []
        if hours is not None:
            cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
            cutoff_text = (
                datetime.fromtimestamp(cutoff, timezone.utc).isoformat().replace("+00:00", "Z")
            )
            conditions = " AND observed_at>=?"
            params.append(cutoff_text)
        rows = self.connection.execute(
            f"""
            SELECT raw_event FROM observations
            WHERE json_extract(raw_event, '$.packet_id') IS NOT NULL
              AND json_extract(raw_event, '$.from_node') IS NOT NULL {conditions}
            UNION ALL
            SELECT raw_event FROM events
            WHERE event_type='network_packet'
              AND json_extract(raw_event, '$.packet_id') IS NOT NULL
              AND json_extract(raw_event, '$.from_node') IS NOT NULL {conditions}
            """,
            params + params,
        )
        return [json.loads(row["raw_event"]) for row in rows]

    @staticmethod
    def _logical_packet_summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(group, key=lambda event: event["observed_at"])
        preferred = next(
            (event for event in ordered if event.get("source") == "RF_OBSERVED"), ordered[0]
        )
        gateways = {event.get("observer_id") for event in group if event.get("observer_id")}
        rf_observations = sum(event.get("source") == "RF_OBSERVED" for event in group)
        mqtt_observations = sum(event.get("source") == "MQTT_NETWORK" for event in group)
        destinations = {event.get("to_node") for event in group if event.get("to_node") is not None}
        portnums = {event.get("portnum") for event in group if event.get("portnum")}
        resolved_destination = preferred.get("to_node")
        if resolved_destination is None:
            resolved_destination = next((value for value in destinations), None)
        resolved_portnum = preferred.get("portnum") or next(
            (event.get("portnum") for event in ordered if event.get("portnum")), "UNKNOWN"
        )
        decodable = sum(
            event.get("transport_source") == "LZ_MQTT" and not bool(event.get("encrypted"))
            for event in group
        )
        encrypted_unknown = sum(
            event.get("transport_source") == "LZ_MQTT" and bool(event.get("encrypted"))
            for event in group
        )
        return {
            "sender": preferred["from_node"],
            "packet_id": preferred["packet_id"],
            "to_node": resolved_destination,
            "portnum": resolved_portnum,
            "first_observed_at": ordered[0]["observed_at"],
            "last_observed_at": ordered[-1]["observed_at"],
            "provenance": "RF_OBSERVED"
            if rf_observations
            else "MQTT_NETWORK"
            if mqtt_observations
            else preferred.get("source", "UNKNOWN"),
            "observation_count": len(group),
            "repeat_count": max(0, len(group) - 1),
            "gateway_count": len(gateways),
            "rf_observations": rf_observations,
            "mqtt_observations": mqtt_observations,
            "decodable_observations": decodable,
            "encrypted_unknown_observations": encrypted_unknown,
            "conflicting_destination": len(destinations) > 1,
            "conflicting_portnum": len(portnums) > 1,
        }

    def list_logical_packets(self, limit: int = 100, hours: float = 24) -> list[dict[str, Any]]:
        with self.lock:
            events = self._quality_packet_events(hours)
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for event in events:
            grouped.setdefault((int(event["from_node"]), int(event["packet_id"])), []).append(event)
        summaries = [self._logical_packet_summary(group) for group in grouped.values()]
        summaries.sort(key=lambda item: item["last_observed_at"], reverse=True)
        return summaries[:limit]

    def get_logical_packet(self, sender: int, packet_id: int) -> dict[str, Any] | None:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT raw_event FROM observations
                WHERE json_extract(raw_event, '$.from_node')=?
                  AND json_extract(raw_event, '$.packet_id')=?
                UNION ALL
                SELECT raw_event FROM events
                WHERE event_type='network_packet'
                  AND json_extract(raw_event, '$.from_node')=?
                  AND json_extract(raw_event, '$.packet_id')=?
                """,
                (sender, packet_id, sender, packet_id),
            )
            events = [json.loads(row["raw_event"]) for row in rows]
        if not events:
            return None
        summary = self._logical_packet_summary(events)
        summary["observations"] = sorted(events, key=lambda event: event["observed_at"])
        return summary

    def list_gateway_quality(self, hours: float = 24) -> list[dict[str, Any]]:
        with self.lock:
            events = [
                event
                for event in self._quality_packet_events(hours)
                if event.get("transport_source") == "LZ_MQTT"
            ]
            metadata = {
                int(row["node_num"]): dict(row)
                for row in self.connection.execute("SELECT * FROM node_metadata")
            }
            positions = {
                int(row["node_num"]): dict(row)
                for row in self.connection.execute(
                    """
                    SELECT p.* FROM positions p JOIN (
                        SELECT node_num, max(observed_at) latest FROM positions GROUP BY node_num
                    ) n ON n.node_num=p.node_num AND n.latest=p.observed_at
                    """
                )
            }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            grouped.setdefault(str(event["observer_id"]), []).append(event)
        now = datetime.now(timezone.utc)
        result = []
        for observer_id, group in grouped.items():
            latest = max(group, key=lambda event: event["observed_at"])
            node_num = int(latest["observer_node_num"])
            identity = metadata.get(node_num, {})
            position = positions.get(node_num)
            position_age = None
            if position:
                timestamp = datetime.fromisoformat(position["observed_at"].replace("Z", "+00:00"))
                position_age = max(0, (now - timestamp).total_seconds())
            packet_keys = {(event["from_node"], event["packet_id"]) for event in group}
            direct = [event for event in group if event.get("source") == "RF_OBSERVED"]
            metrics = [event for event in group if event.get("rx_rssi") or event.get("rx_snr")]
            decodable = sum(not bool(event.get("encrypted")) for event in group)
            warnings = []
            if not position:
                warnings.append("MISSING_POSITION")
            elif position_age is not None and position_age > 24 * 3600:
                warnings.append("STALE_POSITION")
            if not identity.get("long_name") and not identity.get("short_name"):
                warnings.append("UNKNOWN_IDENTITY")
            if direct and not metrics:
                warnings.append("MISSING_SIGNAL_METRICS")
            repeat_ratio = max(0, len(group) - len(packet_keys)) / max(1, len(group))
            if len(group) >= 10 and repeat_ratio >= 0.5:
                warnings.append("HIGH_DUPLICATE_RATIO")
            result.append(
                {
                    "observer_id": observer_id,
                    "observer_node_num": node_num,
                    "node_id": identity.get("node_id") or f"!{node_num:08x}",
                    "short_name": identity.get("short_name"),
                    "long_name": identity.get("long_name"),
                    "hardware_model": identity.get("hardware_model"),
                    "last_report": latest["observed_at"],
                    "observation_count": len(group),
                    "unique_packets": len(packet_keys),
                    "repeat_observations": max(0, len(group) - len(packet_keys)),
                    "repeat_ratio": round(repeat_ratio, 3),
                    "direct_rf_observations": len(direct),
                    "average_rssi": round(
                        sum(event.get("rx_rssi") or 0 for event in metrics) / len(metrics), 2
                    )
                    if metrics
                    else None,
                    "average_snr": round(
                        sum(event.get("rx_snr") or 0 for event in metrics) / len(metrics), 2
                    )
                    if metrics
                    else None,
                    "decodable": decodable,
                    "encrypted_unknown": len(group) - decodable,
                    "decode_success_percent": round(100 * decodable / max(1, len(group)), 1),
                    "latitude": position.get("latitude") if position else None,
                    "longitude": position.get("longitude") if position else None,
                    "position_observed_at": position.get("observed_at") if position else None,
                    "position_age_seconds": position_age,
                    "trusted_for_coverage": bool(
                        position
                        and position_age is not None
                        and position_age <= 24 * 3600
                        and direct
                    ),
                    "warnings": warnings,
                }
            )
        result.sort(key=lambda item: item["last_report"], reverse=True)
        return result

    def get_gateway_quality(self, node_num: int, hours: float = 24) -> dict[str, Any] | None:
        gateway = next(
            (
                item
                for item in self.list_gateway_quality(hours)
                if item["observer_node_num"] == node_num
            ),
            None,
        )
        if gateway is None:
            return None
        with self.lock:
            all_events = self._quality_packet_events(hours)
        keys = {
            (int(event["from_node"]), int(event["packet_id"]))
            for event in all_events
            if event.get("observer_node_num") == node_num
        }
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for event in all_events:
            key = (int(event["from_node"]), int(event["packet_id"]))
            if key in keys:
                grouped.setdefault(key, []).append(event)
        packets = [self._logical_packet_summary(group) for group in grouped.values()]
        packets.sort(key=lambda item: item["last_observed_at"], reverse=True)
        return {**gateway, "packets": packets[:250]}

    def list_quality_warnings(self, limit: int = 200, hours: float = 24) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        for gateway in self.list_gateway_quality(hours):
            for kind in gateway["warnings"]:
                warnings.append(
                    {
                        "severity": "high" if kind == "MISSING_POSITION" else "medium",
                        "kind": kind,
                        "scope": "gateway",
                        "observer_node_num": gateway["observer_node_num"],
                        "observer_id": gateway["observer_id"],
                        "title": kind.replace("_", " ").title(),
                        "detail": f"Gateway {gateway['node_id']} requires review before its evidence is trusted.",
                        "observed_at": gateway["last_report"],
                    }
                )
        with self.lock:
            raw_events = self._quality_packet_events(hours)
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for event in raw_events:
            grouped.setdefault((int(event["from_node"]), int(event["packet_id"])), []).append(event)
        for group in grouped.values():
            packet = self._logical_packet_summary(group)
            if packet["conflicting_destination"] or packet["conflicting_portnum"]:
                warnings.append(
                    {
                        "severity": "high",
                        "kind": "CONFLICTING_PACKET_METADATA",
                        "scope": "packet",
                        "sender": packet["sender"],
                        "packet_id": packet["packet_id"],
                        "title": "Conflicting Packet Metadata",
                        "detail": "Observers reported different destination or application metadata.",
                        "observed_at": packet["last_observed_at"],
                    }
                )
            if any(
                event.get("source") == "RF_OBSERVED"
                and not (event.get("rx_rssi") or event.get("rx_snr"))
                for event in group
            ):
                warnings.append(
                    {
                        "severity": "high",
                        "kind": "RF_WITHOUT_SIGNAL_METRICS",
                        "scope": "packet",
                        "sender": packet["sender"],
                        "packet_id": packet["packet_id"],
                        "title": "RF Without Signal Metrics",
                        "detail": "An RF observation lacks RSSI and SNR evidence.",
                        "observed_at": packet["last_observed_at"],
                    }
                )
            if any(
                max(0, int(event.get("hop_start") or 0) - int(event.get("hop_limit") or 0)) > 7
                for event in group
            ):
                warnings.append(
                    {
                        "severity": "medium",
                        "kind": "SUSPICIOUS_HOP_COUNT",
                        "scope": "packet",
                        "sender": packet["sender"],
                        "packet_id": packet["packet_id"],
                        "title": "Suspicious Hop Count",
                        "detail": "At least one observation reports more than seven consumed hops.",
                        "observed_at": packet["last_observed_at"],
                    }
                )
            if any(
                event.get("source") == "RF_OBSERVED"
                and event.get("from_node") == event.get("observer_node_num")
                for event in group
            ):
                warnings.append(
                    {
                        "severity": "high",
                        "kind": "GATEWAY_SELF_UPLINK_AS_RF",
                        "scope": "packet",
                        "sender": packet["sender"],
                        "packet_id": packet["packet_id"],
                        "title": "Gateway Self-Uplink Classified As RF",
                        "detail": "A publishing gateway appears to have classified its own transmission as RF.",
                        "observed_at": packet["last_observed_at"],
                    }
                )
        warnings.sort(
            key=lambda item: (item["severity"] != "high", item["observed_at"]), reverse=False
        )
        return warnings[:limit]

    def list_node_activity(self, node_num: int, limit: int = 250) -> list[dict[str, Any]]:
        """Return sent and received evidence for a node without duplicating storage."""
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT observed_at, raw_event FROM (
                    SELECT observed_at, raw_event FROM observations
                    WHERE json_extract(raw_event, '$.from_node')=?
                       OR json_extract(raw_event, '$.to_node')=?
                    UNION ALL
                    SELECT observed_at, raw_event FROM events
                    WHERE json_extract(raw_event, '$.from_node')=?
                       OR json_extract(raw_event, '$.to_node')=?
                ) ORDER BY observed_at DESC LIMIT ?
                """,
                (node_num, node_num, node_num, node_num, limit),
            )
            result = []
            for row in rows:
                event = json.loads(row["raw_event"])
                sent = event.get("from_node") == node_num
                received = event.get("to_node") == node_num
                event["node_direction"] = (
                    "both" if sent and received else "sent" if sent else "received"
                )
                result.append(event)
            return result

    def transmission(self, packet_id: int, from_node: int, to_node: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM transmissions WHERE packet_id=? AND from_node=? AND to_node=?",
            (packet_id, from_node, to_node),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["observations"] = [
            dict(value)
            for value in self.connection.execute(
                """
                SELECT observer_id, observer_node_num, observed_at, rx_rssi, rx_snr,
                       hop_start, hop_limit, source
                FROM observations WHERE transmission_id=? ORDER BY observed_at
                """,
                (row["id"],),
            )
        ]
        return result
