# Legacy feeder to ATLAS observer migration

This toolkit performs a staged cutover. It preserves the legacy feeder files and
does not stop the old service until a healthy ATLAS heartbeat has reached the
central server.

## 1. Provision the site on the ATLAS server

Run as the ATLAS administrator:

```bash
cd /opt/atlas
sudo deploy/provision-observer-site.sh
```

The script prompts for the observer ID, Meshtastic node ID, names, coordinates,
and API URL. It then:

- issues or rotates an independently revocable ingestion token;
- adds the trusted observer coordinates to `/etc/atlas/observer-sites.json`;
- restarts and health-checks the central API; and
- writes a root-readable site bundle under `/etc/atlas/site-kits/`.

Transfer that `.env` bundle securely to the remote site. It contains a secret.

## 2. Install or upgrade the MUX at the remote site

```bash
chmod +x migrate-install-mux.sh
sudo ./migrate-install-mux.sh
```

The script searches common legacy feeder locations for `NODE_HOST` and
`NODE_PORT`, reads an existing MUX configuration when present, and prompts the
operator to confirm the physical radio and listener endpoints. It installs the
definitive HTTPS GitHub source, enables the localhost-only audit stream, checks
for port conflicts, validates upstream reachability, starts the service, and
tests both MUX listeners.

The legacy feeder remains running during this stage.

## 3. Install the ATLAS collector and cut over

```bash
chmod +x migrate-install-collector.sh
sudo ./migrate-install-collector.sh --site-config SITE.env
```

The collector installer uses the site bundle, existing observer settings, the
installed MUX, and recognizable legacy feeder settings as defaults. Missing
required values are requested interactively. It downloads the latest signed-by-
checksum ATLAS release artifacts, installs bounded local storage and automatic
updates, validates configuration, and starts the collector.

The legacy feeder is disabled only after all of these checks pass:

1. the MUX endpoint accepts a TCP connection;
2. the ATLAS public health endpoint is reachable;
3. the collector service remains active;
4. the central observer-health endpoint reports the site online;
5. `mux_connected` is true; and
6. the delivery queue is empty.

Known `lzfeeder`, `lzmesh-feeder`, `chaos-feeder`, and `meshmap-feeder` systemd
services are stopped and disabled. Their source files and configuration remain
in place for rollback.

## Rollback

To temporarily return to the old feeder:

```bash
sudo systemctl disable --now atlas-observer.service
sudo systemctl enable --now lzfeeder.service
```

Use the actual legacy unit name reported by the migration script. The MUX may
remain installed; point the legacy feeder at the MUX client port if it previously
used the physical radio directly.
