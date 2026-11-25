# ATLAS server and live API

This component ingests normalized schema-v1 events and separates logical
transmissions from physical observer receptions. The current production service
uses SQLite in WAL mode. PostgreSQL/PostGIS remains a possible future migration,
but is not required for the present multi-observer deployment.

Install independently:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Ingest a collector NDJSON file:

```bash
.venv/bin/atlas-server-ingest \
  --database data/atlas.db \
  --input ../captures/lzg2.normalized.ndjson
```

Ingestion is idempotent by `event_id`. Observations from multiple observers with
the same Meshtastic packet/source/destination identity remain separate rows linked
to one logical transmission.

## Development API

```bash
export ATLAS_DATABASE=/path/to/atlas.db
export ATLAS_INGEST_TOKEN='replace-with-a-random-secret'
export ATLAS_ALLOWED_ORIGINS='http://127.0.0.1:4175,http://localhost:4175'
.venv/bin/atlas-server --host 127.0.0.1 --port 18081
```

Port `18081` is the recommended local ATLAS API port; confirm it is unused
before binding. Browser origins are explicit and comma-separated. Do not use a
wildcard origin for a deployed ingest service.

Read endpoints:

- `GET /api/v1/health`
- `GET /api/v1/observers`
- `GET /api/v1/transmissions?limit=100&before=<UTC timestamp>`
- `GET /api/v1/transmissions/{id}`
- `GET /api/v1/observations?observer_id=LZG2&limit=100`
- `GET /api/v1/positions?node_num=<decimal node number>&limit=500`
- `GET /api/v1/nodes?limit=500` — latest decoded position for each node
- `GET /api/v1/coverage/measurements?limit=5000&include_neighbors=true` — direct
  RF measurement samples plus explicitly lower-confidence NeighborInfo evidence
- `GET /api/v1/live` — browser-compatible Server-Sent Events
- `GET /docs` — generated OpenAPI interface

Ingestion is disabled unless `ATLAS_INGEST_TOKEN` is configured. Submit normalized
events with `POST /api/v1/events` and the token in `X-Atlas-Ingest-Token`.
Accepted events are persisted idempotently and broadcast to live SSE clients.

### Health checks

- `GET /api/v1/healthz` is process liveness and deliberately performs no
  database work.
- `GET /api/v1/readyz` performs a bounded read-only SQLite metadata check. It is
  the correct readiness probe and does not calculate dashboard summaries.
- `GET /api/v1/health` returns dashboard counts. It is not a readiness probe;
  selected expensive summaries may be briefly cached.

### SQLite concurrency model

Ingestion uses one shared writer connection protected by a reentrant lock.
Dashboard and reporting endpoints use short-lived independent read-only
connections against the WAL database and close them after each operation. Long
reads must not be moved back onto the writer connection merely for convenience.

Any new list, reporting, map, coverage, or quality endpoint should:

1. use `AtlasStore.read_connection()` unless it is part of a write transaction;
2. have a bounded result set or aggregation window;
3. inspect its production-sized query plan before adding an index;
4. avoid logging payloads, tokens, headers, query strings, or client addresses;
5. include a concurrency regression test when it could overlap ingestion.

The transmission list first selects its bounded recent page and then aggregates
only those observations. Observer health uses partial indexes for collector
heartbeats and connection errors. Expensive identical dashboard summaries use
brief stale-while-refresh caching with cold-load single-flight protection.

Slow request and writer-lock diagnostics are rate-limited. Configure their
thresholds with `ATLAS_SLOW_REQUEST_SECONDS`,
`ATLAS_SLOW_LOCK_WAIT_SECONDS`, and
`ATLAS_DIAGNOSTIC_LOG_INTERVAL_SECONDS` only when operationally justified.

### Delivery retries and duplicate safety

Collectors use durable local outboxes and may retry when an HTTP response is
lost or arrives after their timeout. The server treats `event_id` as the stable
idempotency key. A retry after a successful commit returns a duplicate result
without adding a second event or refreshing derived node state. Increasing a
collector timeout may be a secondary safeguard, but must not replace fixing a
slow writer or a read operation that contends with ingestion.

### Production verification

After a server change, verify liveness, readiness, observer state, queues, and
recent contention diagnostics. Example local checks:

```bash
curl -fsS http://127.0.0.1:18081/api/v1/healthz
curl -fsS http://127.0.0.1:18081/api/v1/readyz
curl -fsS http://127.0.0.1:18081/api/v1/observer-health
sudo journalctl -u atlas-api.service --since "15 minutes ago" \
  | grep -E "slow API request method=POST|slow store lock"
```

The normal release workflow is: preserve unrelated changes, update the
changelog and version, run the complete server tests, create a rollback archive,
commit and push over HTTPS, restart only `atlas-api.service`, and verify live
ingestion under concurrent dashboard traffic.

Simulation is disabled in the normal server command. When explicitly enabled by
an embedding/test configuration, `/api/v1/simulate` broadcasts events with
`simulated=true` and `persistence=broadcast_only`. Synthetic events cannot be
persisted through the ingestion endpoint.

The health response reports current observer counts and mode flags. Consumers
must use the returned values rather than assuming a single-observer deployment.
