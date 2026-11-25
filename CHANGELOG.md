# ATLAS changelog

This file is the running record of user-visible, operational, schema, deployment,
and performance changes. Every production change should add an entry under
`Unreleased` before it is committed. At release time, move those entries beneath
a dated version heading. Record behavior and migration impact, but never tokens,
channel keys, private addresses, packet payloads, or other secrets.

The repository version is authoritative. Deployment-specific incident evidence
and rollback archives stay on the server and are not committed.

## Unreleased

### Map accuracy

- Distinguished incomplete traceroute requests from completed RouteDiscovery
  replies. MQTT gateways can report separate flooded copies of one request;
  those partial paths now stop at their last evidenced relay instead of being
  joined to the intended destination.
- Added a dotted blue partial-trace style and clarified the map legend. Solid
  blue is reserved for completed traceroute responses.
- Added regression coverage using the duplicate-gateway route pattern that
  exposed the issue.

## 0.3.52 — 2026-11-25

### Documentation

- Established this running changelog as a required part of the production
  change workflow.
- Documented the production SQLite WAL concurrency model, independent dashboard
  readers, health-check roles, retry idempotency, performance diagnostics,
  validation steps, and release/rollback workflow.
- Removed stale single-observer and development-only SQLite descriptions.

## 0.3.51 — 2026-11-25

### Performance and reliability

- Isolated dashboard and reporting reads from ingestion with short-lived,
  read-only SQLite WAL connections. The shared connection and lock remain the
  serialized writer path.
- Rewrote the transmission list to select the requested recent transmissions
  before aggregating their observations.
- Added targeted indexes for transmission recency, collector heartbeats, and
  observer connection errors.
- Moved SQLite ingestion work off the asyncio event loop while retaining the
  writer lock and idempotent event processing.
- Moved observations, positions, nodes, telemetry, activity, observer health,
  quality, gateway, and map-support reads away from the writer lock.
- Added cold-cache single-flight behavior and a brief gateway-quality cache so
  identical expensive dashboard requests share one calculation.
- Added concurrency regression coverage proving a stalled dashboard read does
  not hold the writer lock or delay ingestion.

### Measured result

- Reduced the production transmission list from approximately 2.66 seconds to
  0.28 seconds and observer health from approximately 2.30 seconds to 0.11
  seconds in the deployment validation run.
- Concurrent dashboard load produced no slow ingestion or store-lock warnings;
  collector outboxes remained empty.

## 0.3.50 — 2026-11-20

- Eliminated a duplicate 24-hour packet materialization in quality warnings.
- Added a 60-second stale-while-refresh cache for repeated quality-warning reads.
- Reduced a measured quality-warning request from approximately 10.2 seconds to
  4.9 seconds cold and milliseconds when cached.

## 0.3.49 — 2026-11-20

- Added rate-limited slow-request and shared store-lock wait diagnostics. Logs
  include method, path, status, and duration, but omit payloads, query strings,
  headers, client addresses, and secrets.
- Added a narrow partial index for the network-packet health summary and briefly
  cached that expensive aggregate while keeping inexpensive table counts fresh.
- Reduced a measured health request from roughly 1.5–2.3 seconds to about
  0.2 seconds, including while a coverage export was running.

## 0.3.48 — 2026-11-20

- Moved reachability/coverage exports to an independent read-only SQLite WAL
  connection so long exports no longer hold ingestion's shared writer lock.
- Added regression tests for concurrent coverage reads, writer progress, reader
  cleanup on failure, and in-memory database behavior.
