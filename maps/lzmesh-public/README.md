# LZMesh Public Map

The public LZMesh node map is an ATLAS presentation surface. It reads the
curated ATLAS map feed and does not collect Meshtastic traffic itself.

## Configuration

Runtime configuration stays outside Git. Supported environment variables are:

- `ATLAS_FEED_URL`
- `ATLAS_TIMEOUT_SECONDS`
- `ATLAS_CACHE_SECONDS`
- `NEXT_PUBLIC_CARTO_API_KEY`

## Local check

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run --port 5010
curl -fsS http://127.0.0.1:5010/health
```

Generated packages and credentials must not be committed.

## Snapshot refresh behavior

Node and observer requests return the last complete snapshot immediately. A stale
or missing snapshot schedules a daemon refresh; no request waits for upstream
HTTP or for a refresh lock. A cold node feed returns `{}` and a cold observer feed
returns `[]` with `Cache-Control: no-store`; `/health` returns 503 (`warming`)
until a successful node snapshot exists. Existing node filtering and field shapes
are unchanged. A stale successful snapshot remains available on upstream failure;
cache age is exposed in `X-Atlas-Cache-Age` and health metadata. Stale data retains
the upstream ageMin fields from its last successful snapshot.

- `ATLAS_TIMEOUT_SECONDS`: 2.5 by default; Requests connect/read inactivity timeout,
  not a hard wall-clock deadline for a slowly streaming response.
- `ATLAS_CACHE_SECONDS`: node freshness window, default 20 seconds.
- `ATLAS_RETRY_SECONDS`: initial retry delay after failure, default 5 seconds.
- `ATLAS_RETRY_MAX_SECONDS`: maximum exponential retry delay, default 60 seconds.
- `ATLAS_CACHE_DIRECTORY`: optional writable local directory. Production sets this
  to `/var/lib/lzmesh-map` through a systemd StateDirectory drop-in.

Production retains two sync Gunicorn worker processes. Each feed uses an advisory
nonblocking flock to permit only one upstream refresh across all workers; complete
snapshots and failure retry deadlines are shared using atomic local JSON files.
Readers take no flock. Without a shared directory, caches are process-local.
Threads start on demand after worker fork, not during module import. The two feeds
(nodes and observers) have independent locks. On a worker exit the OS releases its
flock; persisted snapshots survive service restarts. Use local storage, not NFS.

Refresh logs are structured JSON with feed, result, duration_ms, cache age,
exception type, HTTP status when available, and retry delay. They intentionally
omit upstream URLs, exception messages, credentials, and response bodies.

Run checks using the existing environment:

```bash
/opt/lzmesh-map/venv/bin/python -m unittest discover -s tests -v
```

The deployed service drop-in is
`/etc/systemd/system/lzmesh-map.service.d/30-atlas-cache.conf`:

```ini
[Service]
StateDirectory=lzmesh-map
StateDirectoryMode=0700
Environment=ATLAS_CACHE_DIRECTORY=/var/lib/lzmesh-map
```

Systemd creates the state directory for the existing service user. No credentials
belong in this drop-in or in the shared snapshots. Existing service/environment
files and the two-worker Gunicorn command remain unchanged.

The isolated end-to-end check starts a separate two-worker Gunicorn instance and a
controllable loopback upstream, with private temporary snapshot storage. It does
not stop or reconfigure the production service or ATLAS:

```bash
/opt/lzmesh-map/venv/bin/python tests/verify_isolated.py "$PWD"
```
