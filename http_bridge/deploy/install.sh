#!/usr/bin/env bash
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die "run this installer as root (sudo is unavailable)"
  exec sudo -- "$0" "$@"
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WHEEL=$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'meshtastic_http_bridge-*.whl' -print -quit)
[[ -n "$WHEEL" ]] || die "deployment bundle is missing the bridge wheel"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

VERSION=$(python3 - "$WHEEL" <<'PY'
import re, sys
match = re.search(r"meshtastic_http_bridge-([^-]+)-", sys.argv[1])
if not match:
    raise SystemExit("invalid bridge wheel filename")
print(match.group(1))
PY
)
ROOT=/opt/meshtastic-http-bridge
RELEASE="$ROOT/releases/$VERSION"
CONFIG_DIR=/etc/meshtastic-http-bridge
CONFIG="$CONFIG_DIR/bridge.env"

getent group meshtastic-http-bridge >/dev/null || groupadd --system meshtastic-http-bridge
id -u meshtastic-http-bridge >/dev/null 2>&1 || useradd --system --gid meshtastic-http-bridge --home-dir /nonexistent --shell /usr/sbin/nologin meshtastic-http-bridge

if [[ ! -d "$RELEASE" ]]; then
  install -d -m 0755 "$ROOT/releases"
  python3 -m venv "$RELEASE"
  "$RELEASE/bin/pip" install --no-input --disable-pip-version-check "$WHEEL"
fi
ln -sfn "$RELEASE" "$ROOT/current.new"
mv -Tf "$ROOT/current.new" "$ROOT/current"

install -d -m 0750 -o root -g meshtastic-http-bridge "$CONFIG_DIR"
if [[ ! -f "$CONFIG" ]]; then
  listen_port=$(python3 - <<'PY'
import socket

for port in (4404, 4407, 4408, 4409, 4410):
    sock = socket.socket()
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        continue
    finally:
        sock.close()
    print(port)
    break
else:
    raise SystemExit("no free HTTP bridge port found in 4404, 4407-4410")
PY
  )
  sed "s/^MESHTASTIC_HTTP_BRIDGE_LISTEN_PORT=.*/MESHTASTIC_HTTP_BRIDGE_LISTEN_PORT=$listen_port/" \
    "$SCRIPT_DIR/bridge.env.example" > "$CONFIG"
  chown root:meshtastic-http-bridge "$CONFIG"
  chmod 0640 "$CONFIG"
  echo "Created $CONFIG with LAN listener :$listen_port and MUX upstream 127.0.0.1:4405"
else
  echo "Preserved existing $CONFIG"
fi

listen_port=$(sed -n 's/^MESHTASTIC_HTTP_BRIDGE_LISTEN_PORT=//p' "$CONFIG" | tail -n 1)
[[ "$listen_port" =~ ^[0-9]+$ ]] || die "invalid listener port in $CONFIG"

install -m 0644 "$SCRIPT_DIR/meshtastic-http-bridge.service" /etc/systemd/system/meshtastic-http-bridge.service
systemctl daemon-reload
systemctl enable --now meshtastic-http-bridge.service

for _ in {1..20}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$listen_port/healthz" >/dev/null; then
    echo "Meshtastic HTTP bridge $VERSION is healthy and connected to the TCP MUX."
    echo "LAN clients can use HTTP on this host at port $listen_port."
    exit 0
  fi
  sleep 1
done

systemctl status meshtastic-http-bridge.service --no-pager || true
journalctl -u meshtastic-http-bridge.service -n 30 --no-pager || true
die "bridge did not become healthy on port $listen_port; verify the configured port is free and the MUX is listening on 127.0.0.1:4405"
