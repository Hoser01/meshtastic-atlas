# ATLAS API behavior

The central API exposes only verified `rf_observation` rows as physical RF
observations. MQTT, local, status, and unknown events are retained separately and
do not appear in RF transmission results.

Position-aware reads are available through `GET /api/v1/positions` for history
and `GET /api/v1/nodes` for the latest decoded position per node. Each row keeps
the original RF, MQTT, or local provenance; a valid coordinate does not change
how the packet was observed.

Measured coverage evidence is available through
`GET /api/v1/coverage/measurements`. Direct zero-hop RF receptions include the
transmitter position, configured observer position, RSSI, SNR, distance, sample
age, and position age. `NEIGHBORINFO_SNR` rows are explicitly marked
`neighbor_report` and carry less confidence because they provide SNR but not a
receiver RSSI measurement. They must not be presented as equivalent to direct
observer measurements.

Terrain coverage is modeled evidence, not measured evidence. The web client can
overlay GeoJSON exported by the Meshtastic Site Planner, and keeps that layer
separate from both the RF Coverage Heatmap and recent Activity Heatmap.

## Pagination

List endpoints use a bounded `limit`. Transmission and observation lists
accept a UTC `before` cursor and return newest-first results.

## Live stream

`GET /api/v1/live` uses `text/event-stream`. Each accepted event is sent with:

- SSE `event`: the ATLAS event type;
- SSE `id`: the stable ATLAS event ID;
- SSE `data`: the complete normalized JSON event.

Idle streams send keepalive comments. Slow clients have a bounded queue; the
oldest pending live item is dropped rather than allowing one browser to exhaust
server memory. Persistence remains authoritative for recovery and history.

## Security boundary

Read endpoints are suitable for a future public map after privacy review.
Mutation requires an ingestion token. The development server should remain bound
to loopback until TLS, reverse-proxy controls, credential rotation, rate limits,
and public-field filtering are deployed.

Message payload content is not stored in normalized RF events or exposed by this
API. Packet metadata is retained.

## Liveness, readiness, and summaries

`GET /api/v1/healthz` checks process liveness without querying SQLite.
`GET /api/v1/readyz` performs a bounded read-only metadata query and is intended
for service supervision. `GET /api/v1/health` contains dashboard summaries and
must not be used as a readiness dependency.

Summary endpoints may return briefly cached data. Live ingestion and the SSE
stream remain authoritative for newly accepted events.

## Concurrency and retry semantics

Read-heavy dashboard endpoints use independent read-only WAL connections. They
do not own the serialized ingestion connection or its writer lock. New reporting
queries must retain this separation and must be tested alongside ingestion.

An HTTP client can time out after the database commit but before receiving the
response. Retrying the same stable `event_id` is supported and safe. The API
returns `accepted=false` and `duplicate=true` when the event is already stored.
Collectors should remove an outbox item only after a successful API response and
otherwise retry it with the same event identity.
