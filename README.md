# ATLAS RF Collector

## Monorepo layout

```text
atlas/
├── src/atlas_muxdiag/   # independently packaged remote collector
├── server/              # central ingestion and correlation
├── schemas/             # shared versioned event contracts
├── web/                 # MapLibre live-map application
├── deploy/              # component-specific deployment assets
├── docs/                # architecture and validation evidence
├── CHANGELOG.md         # running production and release change record
└── tests/               # collector tests
```

The repository-root Python distribution is intentionally the collector because
it is the component installed most often at remote observer sites. The central
server has its own `server/pyproject.toml` and can be installed independently.
Neither package depends on the web application.

Release and production changes are recorded in
[CHANGELOG.md](CHANGELOG.md). Add an `Unreleased` entry with every behavior,
schema, operational, or deployment change; never include credentials, private
channel keys, payload contents, or site-specific secrets.

ATLAS is independent of CHAOS. The Phase 0 utility passively connects to a
Meshtastic TCP MUX, parses the Meshtastic stream framing, decodes official
`FromRadio` protobuf messages, and emits newline-delimited JSON (NDJSON) for
offline analysis. It never sends bytes to the MUX or changes radio state.

The Phase 1 collector uses the validated Phase 0 evidence to emit normalized,
versioned events. Its provenance
labels are deliberately conservative:

- `MQTT_NETWORK`: the packet explicitly has `via_mqtt=true`.
- `POSSIBLE_LOCAL_TX`: the sender matches `my_info.my_node_num`.
- `CANDIDATE_RF`: `via_mqtt=false` and reception metrics are present.
- `UNKNOWN`: there is not enough positive evidence.

The diagnostic command retains conservative `CANDIDATE_RF` labels. The Phase 1
collector promotes packets to `RF_OBSERVED` only under the rules documented in
[docs/PHASE0_FINDINGS.md](docs/PHASE0_FINDINGS.md).

## Requirements and installation

- Python 3.10 or newer
- Network access to a Meshtastic TCP stream/MUX
- No access to CHAOS is required

```bash
cd /opt/atlas
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

The official Meshtastic Python dependency is pinned for reproducible decoding.
Update it deliberately after testing new protobuf definitions against saved raw
captures.

## Run

Bounded five-minute capture to separate NDJSON and raw stream files:

```bash
mkdir -p captures
.venv/bin/atlas-muxdiag \
  --host 127.0.0.1 \
  --port 4405 \
  --observer-id LZG2 \
  --duration 300 \
  --json-output captures/lzg2.ndjson \
  --raw-output captures/lzg2.stream.bin
```

Without `--json-output`, NDJSON is written to stdout. Human-readable summaries
go to stderr, so redirection remains safe. Use `--max-frames`, `--duration`, and
`--max-raw-bytes` to bound collection. Zero means unlimited. Raw output is the
exact concatenation of accepted framed messages and can be replayed offline by
feeding it into `FrameDecoder`.

`--include-payload-base64` duplicates protobuf payload bytes into NDJSON for
portable debugging; it is off by default because it increases file size and may
retain sensitive packet content. Protect all captures as potentially sensitive.

The client reconnects with exponential backoff and jitter. Every connection has
a `connection_epoch`. This matters because some MUX deployments replay cached
frames to new clients; delivery immediately after connecting is not necessarily
evidence that the packet was received at that moment.

## Phase 1 normalized collection

The observer node identity is required configuration so packets can be classified
correctly before or without a `my_info` message:

```bash
.venv/bin/atlas-collector \
  --host 127.0.0.1 \
  --port 4405 \
  --observer-id LZG2 \
  --local-node '!a2e9f268' \
  --output captures/lzg2.normalized.ndjson
```

The collector remains receive-only with respect to the MUX and does not publish
to MQTT by default. Add
`--diagnostic-output` and `--raw-output` when a simultaneous forensic capture is
needed. Normalized schema details are in
[docs/EVENT_SCHEMA.md](docs/EVENT_SCHEMA.md).
The central REST/SSE behavior is documented in [docs/API.md](docs/API.md).

For continuous central ingestion, set `ATLAS_INGEST_TOKEN` and add
`--api-url http://127.0.0.1:18081/api/v1/events`. NodeInfo advertisements are
stored as current node metadata; a newer advertisement replaces the saved long
name, short name, hardware model, and role.

MQTT transport is optional and requires an explicit safety switch; it is never
enabled by merely supplying a broker address:

```bash
.venv/bin/pip install -e '.[mqtt]'
.venv/bin/atlas-collector ... \
  --enable-mqtt \
  --mqtt-host test-broker.example \
  --mqtt-prefix atlas/test
```

Events publish at QoS 1 without retain under `observation`, `transmission`,
`network`, `unknown`, or `status` beneath the configured prefix. Do not point a
development collector at production topics until its deployment is approved.

Replay a Phase 0 NDJSON capture through exactly the same normalizer:

```bash
.venv/bin/atlas-replay \
  --input captures/lzg2-post-mux-fix.ndjson \
  --output captures/lzg2-post-mux-fix.normalized.ndjson \
  --observer-id LZG2 \
  --local-node '!a2e9f268'
```

## Read-only LZ MQTT analysis

`atlas-mqttdiag` is a separate diagnostic collector for encrypted Meshtastic
`ServiceEnvelope` traffic. It is deliberately restricted to the LZ MQTT server,
subscribes read-only to the full `msh/LZ/#` namespace, and has no publish code path. It
does not feed records into the ATLAS API or database.

Install the optional dependencies, then copy the example key file outside the
repository and populate channels 0, 1, and 3 from the authoritative
[LZ Quick Setup page](https://lzmesh.com/resources/lz-quick-setup/). Protect that
file even when the configured channels are public.

```bash
cd /opt/atlas
.venv/bin/pip install -e '.[mqtt]'
sudo install -d -m 0750 -o root -g atlas /etc/atlas
sudo install -m 0640 -o root -g atlas \
  examples/atlas-mqttdiag.channels.example.json \
  /etc/atlas/mqttdiag.channels.json
sudoedit /etc/atlas/mqttdiag.channels.json
```

Pass broker credentials through the environment rather than command-line
arguments so they do not appear in process listings or shell history:

```bash
export ATLAS_MQTT_USERNAME='set-from-LZ-Quick-Setup'
export ATLAS_MQTT_PASSWORD='set-from-LZ-Quick-Setup'
.venv/bin/atlas-mqttdiag \
  --keys-file /etc/atlas/mqttdiag.channels.json \
  --duration 300 \
  --max-messages 5000 \
  --output data/lz-mqtt-analysis.ndjson
```

The safe NDJSON includes gateway, routing, radio metrics, application type,
payload byte count, and decoded NodeInfo/position/neighbor/traceroute structure.
JSON, map, and other LZ broker branches are also counted and fingerprinted so the
capture represents all broker traffic. It intentionally omits text-message
bodies, arbitrary unknown payloads, and raw decrypted payloads. Its RF
labels are conservative:

- `MQTT_GATEWAY_DIRECT_RF`: gateway and reception metrics are present,
  `via_mqtt=false`, and no consumed hop is visible. This is candidate evidence
  of direct reception at that gateway.
- `MQTT_GATEWAY_MULTIHOP_RF`: the gateway received the mesh packet, but the
  original sender cannot be attributed as the gateway's direct RF neighbor.
- `MQTT_GATEWAY_LOCAL_UPLINK`: the packet sender is the publishing gateway;
  this is transport evidence, not an RF reception at that gateway.
- `MQTT_NETWORK`: the packet explicitly carries `via_mqtt=true`.
- `MQTT_UNVERIFIED`: insufficient evidence for a stronger classification.

Channel keys enable payload interpretation; they do not establish RF
provenance. Private channel keys and message contents must never be committed or
placed into redistributable captures.

For continuous ATLAS ingestion, add `--events-output` and `--api-url`. The
collector deduplicates broker repeats by gateway, sender, destination, and packet
ID while retaining a separate observation when another gateway reports the same
packet. Only qualified zero-hop gateway receptions become `RF_OBSERVED` coverage
evidence; multihop, gateway-local, and explicitly MQTT-carried packets remain
`MQTT_NETWORK` events with their detailed classification preserved separately.

Previously captured safe diagnostics can be replayed idempotently:

```bash
ATLAS_INGEST_TOKEN='set-locally' .venv/bin/atlas-mqtt-replay \
  --input data/lz-mqtt-analysis.ndjson \
  --output data/lz-mqtt-events.ndjson \
  --api-url http://127.0.0.1:18081/api/v1/events
```

## NDJSON records

Lifecycle records include `capture_started`, `connecting`, `connected`,
`connection_error`, `reconnect_wait`, and `capture_stopped`. Frame records include
capture time, observer ID, connection epoch, sequence, raw sizes, full decoded
protobuf data, and a compact packet summary focused on packet identity, routing,
RSSI, SNR, application type, encryption state, and provenance clues.

## Safety properties

- Receive-only socket use: the program has no socket-send code path.
- No radio handshake, configuration request, MQTT publication, or database use.
- Incremental parsing handles partial/coalesced frames and corrupt-byte resync.
- Exact raw capture is optional and bounded when requested.
- Production addresses and observer IDs are configuration, never hardcoded.

## Real-world validation

Follow [docs/VALIDATION.md](docs/VALIDATION.md). Run disruptive scenarios such as
a MUX restart only during an approved maintenance window. The utility itself does
not restart or modify the MUX.

## Redistribution

The code is GPL-3.0-only and the official Meshtastic Python package is an external,
pinned GPL dependency. No CHAOS or MUX source is copied into ATLAS. Site-specific
captures, addresses, node identities, and secrets must not be included in a
redistributed package.
