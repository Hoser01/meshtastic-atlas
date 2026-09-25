#!/usr/bin/env bash
set -Eeuo pipefail

MANIFEST_URL="https://github.com/Hoser01/meshtastic-atlas/releases/latest/download/observer-manifest.json"
DEFAULT_API_URL="https://atlas.lzmesh.com/api/v1/events"

die() { echo "ERROR: $*" >&2; exit 1; }
prompt() { local label=$1 default=${2:-} value; read -r -p "$label${default:+ [$default]}: " value; printf '%s' "${value:-$default}"; }
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( 1 <= $1 && $1 <= 65535 )); }
config_value() {
  [[ -n "${site_config:-}" && -f "$site_config" ]] || return 0
  python3 - "$site_config" "$1" <<'PY'
import sys
key=sys.argv[2]
for raw in open(sys.argv[1],encoding="utf-8"):
    line=raw.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    name,value=line.split("=",1)
    if name.strip()==key: print(value.strip()); break
PY
}

site_config=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-config) site_config=${2:-}; shift 2 ;;
    --site-config=*) site_config=${1#*=}; shift ;;
    -h|--help) echo "Usage: sudo $0 [--site-config SITE.env]"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  if [[ -n "$site_config" ]]; then exec sudo -- "$0" --site-config "$site_config"; fi
  exec sudo -- "$0"
fi
[[ -z "$site_config" || -f "$site_config" ]] || die "site config not found: $site_config"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv ca-certificates curl tar

existing_env=/etc/atlas-observer/observer.env
existing_value() {
  [[ -f "$existing_env" ]] || return 0
  python3 - "$existing_env" "$1" <<'PY'
import sys
key=sys.argv[2]
for raw in open(sys.argv[1],encoding="utf-8"):
    if raw.startswith(key+"="): print(raw.split("=",1)[1].strip()); break
PY
}

observer_id=$(config_value ATLAS_OBSERVER_ID); observer_id=${observer_id:-$(existing_value ATLAS_OBSERVER_ID)}
local_node=$(config_value ATLAS_LOCAL_NODE); local_node=${local_node:-$(existing_value ATLAS_LOCAL_NODE)}
mux_host=$(config_value ATLAS_MUX_HOST); mux_host=${mux_host:-$(existing_value ATLAS_MUX_HOST)}
mux_port=$(config_value ATLAS_MUX_PORT); mux_port=${mux_port:-$(existing_value ATLAS_MUX_PORT)}
api_url=$(config_value ATLAS_API_URL); api_url=${api_url:-$(existing_value ATLAS_API_URL)}
token=$(config_value ATLAS_INGEST_TOKEN); token=${token:-$(existing_value ATLAS_INGEST_TOKEN)}

if [[ -z "$mux_port" && -f /opt/meshtastic-tcp-mux/meshtastic_tcp_mux.py ]]; then
  mux_port=$(python3 - /opt/meshtastic-tcp-mux/meshtastic_tcp_mux.py <<'PY'
import ast,sys
for node in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=="LISTEN_PORT":
        try: print(ast.literal_eval(node.value)); break
        except Exception: pass
PY
  )
fi

if [[ -z "$observer_id" ]]; then
  legacy_id=$(python3 <<'PY'
import ast,glob
for pattern in ("/opt/lzmesh-feeder/*.py","/opt/lzfeeder/*.py","/home/*/lzmesh-feeder/*.py","/home/*/chaos/lzmesh-feeder/*.py"):
  for name in glob.glob(pattern):
    try: tree=ast.parse(open(name,errors="replace").read())
    except Exception: continue
    for node in tree.body:
      if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=="FEEDER_ID":
        try: print(ast.literal_eval(node.value)); raise SystemExit
        except Exception: pass
PY
  )
  observer_id=$legacy_id
fi

observer_id=$(prompt "ATLAS observer ID" "$observer_id"); observer_id=${observer_id^^}
local_node=$(prompt "Local Meshtastic node ID (! plus 8 hex digits)" "$local_node")
mux_host=$(prompt "Local MUX host" "${mux_host:-127.0.0.1}")
mux_port=$(prompt "Local MUX port" "${mux_port:-4405}")
api_url=$(prompt "ATLAS ingestion URL" "${api_url:-$DEFAULT_API_URL}")
if [[ -z "$token" || "$token" == replace-with-observer-token ]]; then
  read -r -s -p "ATLAS site token: " token; echo
fi

[[ "$observer_id" =~ ^[A-Z0-9][A-Z0-9_-]{1,31}$ ]] || die "invalid observer ID"
[[ "$local_node" =~ ^\![0-9A-Fa-f]{8}$ ]] || die "invalid local node ID"
local_node=${local_node,,}
valid_port "$mux_port" || die "invalid MUX port"
[[ "$api_url" == https://* ]] || die "remote ATLAS API URL must use HTTPS"
[[ -n "$token" ]] || die "ATLAS site token is required"

python3 - "$mux_host" "$mux_port" <<'PY' || die "MUX is not reachable"
import socket,sys
with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5): pass
PY
curl -fsS "${api_url%/api/v1/events}/api/v1/health" >/dev/null || die "ATLAS API health endpoint is unavailable"

work_dir=$(mktemp -d /tmp/atlas-collector-migration.XXXXXX)
trap 'case "$work_dir" in /tmp/atlas-collector-migration.*) rm -rf -- "$work_dir";; esac' EXIT
curl -fsSL "$MANIFEST_URL" -o "$work_dir/observer-manifest.json"
readarray -t release_info < <(python3 - "$work_dir/observer-manifest.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); print(d["version"]); print(d["url"]); print(d["sha256"])
PY
)
version=${release_info[0]}; wheel_url=${release_info[1]}; wheel_sha=${release_info[2]}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid release version"
[[ "$wheel_url" == https://github.com/Hoser01/meshtastic-atlas/releases/* ]] || die "untrusted wheel URL"
[[ "$wheel_sha" =~ ^[0-9a-f]{64}$ ]] || die "invalid wheel checksum"
wheel="$work_dir/${wheel_url##*/}"
curl -fsSL "$wheel_url" -o "$wheel"
printf '%s  %s\n' "$wheel_sha" "$wheel" | sha256sum -c -

kit_name="atlas-observer-deploy-${version}.tar.gz"
kit_url="https://github.com/Hoser01/meshtastic-atlas/releases/download/v${version}/${kit_name}"
curl -fsSL "$kit_url" -o "$work_dir/$kit_name"
curl -fsSL "$kit_url.sha256" -o "$work_dir/$kit_name.sha256"
(cd "$work_dir" && sha256sum -c "$kit_name.sha256")
tar -xzf "$work_dir/$kit_name" -C "$work_dir"
installer=$(find "$work_dir" -path '*/install-observer.sh' -type f -print -quit)
[[ -n "$installer" ]] || die "release kit does not contain install-observer.sh"
chmod +x "$installer"
"$installer" "$wheel"

install -d -m 0750 /etc/atlas-observer
if [[ -f "$existing_env" ]]; then
  cp -a "$existing_env" "$existing_env.before-migration"
fi
umask 077
cat > "$existing_env" <<EOF
ATLAS_OBSERVER_ID=$observer_id
ATLAS_LOCAL_NODE=$local_node
ATLAS_MUX_HOST=$mux_host
ATLAS_MUX_PORT=$mux_port
ATLAS_API_URL=$api_url
ATLAS_INGEST_TOKEN=$token
ATLAS_STATE_DIR=/var/lib/atlas-observer
ATLAS_SPOOL_MAX_EVENTS=50000
ATLAS_SPOOL_MAX_BYTES=268435456
ATLAS_OUTPUT_MAX_BYTES=104857600
ATLAS_OUTPUT_BACKUPS=3
ATLAS_HEARTBEAT_SECONDS=60
ATLAS_UPDATE_MANIFEST_URL=$MANIFEST_URL
ATLAS_RELEASE_ROOT=/opt/atlas-observer
ATLAS_RELEASES_TO_KEEP=2
ATLAS_OBSERVER_SERVICE=atlas-observer.service
EOF
chmod 0600 "$existing_env"

set -a
# This root-owned file was generated immediately above.
# shellcheck disable=SC1090
source "$existing_env"
set +a
/opt/atlas-observer/current/bin/atlas-observer --check-config
systemctl daemon-reload
validation_started=$(date -u +%s)
systemctl enable --now atlas-observer.service atlas-observer-update.timer
sleep 4
systemctl is-active --quiet atlas-observer.service || { journalctl -u atlas-observer.service -n 100 --no-pager; die "collector service failed"; }

health_url="${api_url%/api/v1/events}/api/v1/observer-health"
echo "Waiting for the first verified collector heartbeat..."
verified=false
for _ in $(seq 1 18); do
  if python3 - "$health_url" "$observer_id" "$validation_started" <<'PY'
import datetime,json,sys,urllib.request
request=urllib.request.Request(sys.argv[1],headers={"User-Agent":"ATLAS-Installer/1"})
rows=json.load(urllib.request.urlopen(request,timeout=10))
row=next((x for x in rows if x.get("observer_id")==sys.argv[2]),None)
if not row: raise SystemExit(1)
heartbeat=row.get("last_heartbeat")
if not heartbeat: raise SystemExit(1)
heartbeat_epoch=datetime.datetime.fromisoformat(heartbeat.replace("Z","+00:00")).timestamp()
if heartbeat_epoch < float(sys.argv[3]): raise SystemExit(1)
if row.get("status")!="online" or not row.get("mux_connected") or row.get("delivery_queue_depth") not in (0,None): raise SystemExit(1)
if int(row.get("mux_frames") or 0) < 1: raise SystemExit(1)
print(f"verified live stream: status={row['status']} mux_connected={row['mux_connected']} frames={row['mux_frames']} queue={row.get('delivery_queue_depth',0)}")
PY
  then verified=true; break; fi
  sleep 5
done
[[ "$verified" == true ]] || { journalctl -u atlas-observer.service -n 100 --no-pager; die "no healthy ATLAS heartbeat received; legacy feeder was not disabled"; }

mapfile -t legacy_services < <(
  while read -r unit _; do
    [[ "$unit" == *.service ]] || continue
    [[ "$unit" != atlas-observer.service && "$unit" != meshtastic-tcp-mux.service ]] || continue
    if [[ "$unit" =~ (lzmesh|lzfeeder|chaos-feeder|meshmap-feeder) ]] || systemctl cat "$unit" 2>/dev/null | grep -Eqi 'chaos_feeder\.py|lzmesh-feeder|lzfeeder'; then
      printf '%s\n' "$unit"
    fi
  done < <(systemctl list-unit-files --type=service --no-legend)
)
for unit in "${legacy_services[@]}"; do
  echo "Disabling legacy feeder service: $unit"
  systemctl disable --now "$unit"
done

systemctl is-active --quiet atlas-observer.service || die "collector stopped after legacy cutover"
systemctl is-active --quiet meshtastic-tcp-mux.service || die "MUX stopped after legacy cutover"
echo
echo "ATLAS collector migration passed all self-tests."
echo "  observer: $observer_id ($local_node)"
echo "  MUX:      $mux_host:$mux_port"
echo "  release:  $version"
if ((${#legacy_services[@]})); then
  echo "  disabled legacy services: ${legacy_services[*]}"
else
  echo "  no systemd-managed legacy feeder service was found"
fi
echo "The previous feeder files were preserved for rollback."
