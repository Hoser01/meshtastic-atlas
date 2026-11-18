#!/usr/bin/env bash
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die "run this installer as root (sudo is unavailable)"
  exec sudo -- "$0" "$@"
fi

ORIGIN=https://client.meshtastic.org
PUBLIC_HOST=""
HTTPS_ENABLED=1

while (($#)); do
  case "$1" in
    --origin) [[ $# -ge 2 ]] || die "--origin requires a URL"; ORIGIN=$2; shift 2 ;;
    --public-host) [[ $# -ge 2 ]] || die "--public-host requires an IP or hostname"; PUBLIC_HOST=$2; shift 2 ;;
    --http-only) HTTPS_ENABLED=0; shift ;;
    -h|--help)
      echo "Usage: $0 [--origin https://client.meshtastic.org] [--public-host LAN_IP] [--http-only]"
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WHEEL=$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'meshtastic_http_bridge-*.whl' -print -quit)
[[ -n "$WHEEL" ]] || die "deployment bundle is missing the bridge wheel"

for command_name in python3 curl openssl systemctl install ss; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done

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
CERT="$CONFIG_DIR/server.crt"
KEY="$CONFIG_DIR/server.key"
SERVICE=meshtastic-http-bridge.service

getent group meshtastic-http-bridge >/dev/null || groupadd --system meshtastic-http-bridge
id -u meshtastic-http-bridge >/dev/null 2>&1 || useradd --system --gid meshtastic-http-bridge --home-dir /nonexistent --shell /usr/sbin/nologin meshtastic-http-bridge

if [[ ! -d "$RELEASE" ]]; then
  install -d -m 0755 "$ROOT/releases"
  python3 -m venv "$RELEASE"
  "$RELEASE/bin/pip" install --no-input --disable-pip-version-check "$WHEEL"
fi

origin=$("$RELEASE/bin/python" - "$ORIGIN" <<'PY'
import sys
from meshtastic_http_bridge.bridge import origin_of
print(origin_of(sys.argv[1], allow_path=True))
PY
)

if [[ -z "$PUBLIC_HOST" ]]; then
  PUBLIC_HOST=$(python3 - <<'PY'
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.connect(("1.1.1.1", 53))
    print(sock.getsockname()[0])
finally:
    sock.close()
PY
  )
fi

san=$(python3 - "$PUBLIC_HOST" <<'PY'
import ipaddress, re, sys
host = sys.argv[1]
try:
    ipaddress.ip_address(host)
    print("IP:" + host + ",IP:127.0.0.1,DNS:localhost")
except ValueError:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise SystemExit("--public-host must be a plain IP address or hostname")
    print("DNS:" + host + ",IP:127.0.0.1,DNS:localhost")
PY
)

mux_host=127.0.0.1
mux_port=4405
http_port=4407
if [[ -f "$CONFIG" ]]; then
  value=$(sed -n 's/^BRIDGE_MUX_HOST=//p; s/^MESHTASTIC_HTTP_BRIDGE_MUX_HOST=//p' "$CONFIG" | tail -n 1)
  [[ -z "$value" ]] || mux_host=$value
  value=$(sed -n 's/^BRIDGE_MUX_PORT=//p; s/^MESHTASTIC_HTTP_BRIDGE_MUX_PORT=//p' "$CONFIG" | tail -n 1)
  [[ -z "$value" ]] || mux_port=$value
  value=$(sed -n 's/^BRIDGE_HTTP_PORT=//p; s/^MESHTASTIC_HTTP_BRIDGE_LISTEN_PORT=//p' "$CONFIG" | tail -n 1)
  [[ -z "$value" ]] || http_port=$value
fi

https_port=0
if ((HTTPS_ENABLED)); then
  https_port=4408
  if [[ "$https_port" == "$http_port" || "$https_port" == "$mux_port" ]]; then
    https_port=4409
  fi
fi

python3 - "$mux_host" "$mux_port" <<'PY'
import socket, sys
try:
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3):
        pass
except OSError as exc:
    raise SystemExit(f"TCP MUX is not reachable at {sys.argv[1]}:{sys.argv[2]}: {exc}")
PY

bridge_pid=$(systemctl show "$SERVICE" -p MainPID --value 2>/dev/null || true)
for port in "$http_port" "$https_port"; do
  [[ "$port" != 0 ]] || continue
  listener=$(ss -H -ltnp "sport = :$port" || true)
  if [[ -n "$listener" ]]; then
    [[ "$bridge_pid" =~ ^[1-9][0-9]*$ && "$listener" == *"pid=$bridge_pid,"* ]] ||
      die "port $port is already used by another service"
  fi
done

install -d -m 0750 -o root -g meshtastic-http-bridge "$CONFIG_DIR"
if ((HTTPS_ENABLED)); then
  regenerate=1
  if [[ -s "$CERT" && -s "$KEY" ]] && openssl x509 -in "$CERT" -noout -checkend 86400 >/dev/null 2>&1; then
    if [[ "$san" == IP:* ]] && openssl x509 -in "$CERT" -noout -checkip "$PUBLIC_HOST" >/dev/null 2>&1; then
      regenerate=0
    elif [[ "$san" == DNS:* ]] && openssl x509 -in "$CERT" -noout -checkhost "$PUBLIC_HOST" >/dev/null 2>&1; then
      regenerate=0
    fi
  fi
  if ((regenerate)); then
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365       -subj "/CN=$PUBLIC_HOST" -addext "subjectAltName=$san"       -keyout "$KEY" -out "$CERT" >/dev/null 2>&1
  fi
  chown root:meshtastic-http-bridge "$CERT" "$KEY"
  chmod 0640 "$CERT" "$KEY"
fi

install -m 0640 -o root -g meshtastic-http-bridge /dev/null "$CONFIG"
{
  echo "BRIDGE_ALLOWED_ORIGINS=$origin"
  echo "BRIDGE_HTTP_HOST=0.0.0.0"
  echo "BRIDGE_HTTP_PORT=$http_port"
  echo "BRIDGE_MUX_HOST=$mux_host"
  echo "BRIDGE_MUX_PORT=$mux_port"
  echo "BRIDGE_HTTPS_PORT=$https_port"
  echo "BRIDGE_TLS_CERT=$CERT"
  echo "BRIDGE_TLS_KEY=$KEY"
  echo "BRIDGE_MAX_SESSIONS=8"
  echo "BRIDGE_MAX_QUEUE_FRAMES=2048"
  echo "BRIDGE_MAX_QUEUE_BYTES=1048576"
  echo "BRIDGE_IDLE_SECONDS=120"
} > "$CONFIG"

ln -sfn "$RELEASE" "$ROOT/current.new"
mv -Tf "$ROOT/current.new" "$ROOT/current"
install -m 0644 "$SCRIPT_DIR/meshtastic-http-bridge.service" /etc/systemd/system/meshtastic-http-bridge.service
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

healthy=0
for _ in {1..20}; do
  if curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$http_port/healthz" >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if ((!healthy)); then
  systemctl status "$SERVICE" --no-pager || true
  journalctl -u "$SERVICE" -n 30 --no-pager || true
  die "bridge did not become healthy"
fi

if ((HTTPS_ENABLED)); then
  curl --noproxy '*' --cacert "$CERT" -fsS --max-time 3     "https://$PUBLIC_HOST:$https_port/healthz" >/dev/null ||
    die "HTTPS listener did not pass its certificate-aware self-test"
fi

echo
echo "Meshtastic HTTP bridge $VERSION is healthy and connected to TCP MUX $mux_host:$mux_port."
echo "Allowed web client: $origin"
echo "HTTP endpoint:  http://$PUBLIC_HOST:$http_port"
if ((HTTPS_ENABLED)); then
  echo "HTTPS endpoint: https://$PUBLIC_HOST:$https_port"
  echo "Open that HTTPS URL once and accept its certificate warning."
  echo "Then use $PUBLIC_HOST:$https_port in Meshtastic Web with HTTPS enabled."
fi
