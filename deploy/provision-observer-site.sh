#!/usr/bin/env bash
set -Eeuo pipefail

API_URL_DEFAULT="https://atlas.lzmesh.com/api/v1/events"
TOKEN_FILE="/etc/atlas/observer-tokens.json"
SITE_FILE="/etc/atlas/observer-sites.json"
KIT_DIR="/etc/atlas/site-kits"
TOKEN_TOOL="/opt/atlas/.venv/bin/atlas-observer-token"

die() { echo "ERROR: $*" >&2; exit 1; }
prompt() { local label=$1 default=${2:-} value; read -r -p "$label${default:+ [$default]}: " value; printf '%s' "${value:-$default}"; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi
command -v python3 >/dev/null || die "python3 is required"
[[ -x "$TOKEN_TOOL" ]] || die "ATLAS token tool not found at $TOKEN_TOOL"

observer_id=${1:-}
[[ -n "$observer_id" ]] || observer_id=$(prompt "Observer ID, such as PZG2")
observer_id=${observer_id^^}
[[ "$observer_id" =~ ^[A-Z0-9][A-Z0-9_-]{1,31}$ ]] || die "observer ID must be 2-32 letters, numbers, _ or -"

node_id=$(prompt "Meshtastic node ID (! plus 8 hex digits)")
[[ "$node_id" =~ ^\![0-9A-Fa-f]{8}$ ]] || die "invalid Meshtastic node ID"
node_id=${node_id,,}
short_name=$(prompt "Short name" "$observer_id")
long_name=$(prompt "Long name" "$observer_id")
latitude=$(prompt "Site latitude")
longitude=$(prompt "Site longitude")
api_url=$(prompt "ATLAS ingestion URL" "$API_URL_DEFAULT")

python3 - "$latitude" "$longitude" <<'PY' || die "invalid coordinates"
import sys
lat, lon = map(float, sys.argv[1:])
if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0):
    raise SystemExit(1)
PY

install -d -m 0750 /etc/atlas "$KIT_DIR"
[[ -f "$TOKEN_FILE" ]] || install -m 0600 /dev/null "$TOKEN_FILE"
if [[ ! -s "$TOKEN_FILE" ]]; then printf '{}\n' > "$TOKEN_FILE"; fi
existing=$(python3 - "$TOKEN_FILE" "$observer_id" <<'PY'
import json,sys
try: data=json.load(open(sys.argv[1]))
except Exception: data={}
print("yes" if sys.argv[2] in data else "no")
PY
)
if [[ "$existing" == yes ]]; then
  read -r -p "A token already exists for $observer_id. Rotate it? [y/N]: " rotate
  [[ "$rotate" =~ ^[Yy]$ ]] || die "site provisioning cancelled without rotating the token"
fi
token=$($TOKEN_TOOL --file "$TOKEN_FILE" issue "$observer_id")

python3 - "$SITE_FILE" "$observer_id" "$node_id" "$short_name" "$long_name" "$latitude" "$longitude" <<'PY'
import json, os, pathlib, sys, tempfile
path=pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True,exist_ok=True)
try: data=json.loads(path.read_text()) if path.exists() else {}
except Exception as exc: raise SystemExit(f"cannot read {path}: {exc}")
observer_id,node_id,short_name,long_name=sys.argv[2:6]
lat,lon=map(float,sys.argv[6:8])
data[observer_id]={"observer_node_num":int(node_id[1:],16),"node_id":node_id,"short_name":short_name,"long_name":long_name,"latitude":lat,"longitude":lon}
fd,name=tempfile.mkstemp(prefix=".observer-sites-",dir=path.parent)
with os.fdopen(fd,"w") as out: json.dump(data,out,indent=2,sort_keys=True); out.write("\n")
os.chmod(name,0o600); os.replace(name,path)
PY

install -d -m 0755 /etc/systemd/system/atlas-api.service.d
cat > /etc/systemd/system/atlas-api.service.d/observer-files.conf <<EOF
[Service]
Environment=ATLAS_OBSERVER_CONFIG_FILE=$SITE_FILE
Environment=ATLAS_OBSERVER_TOKENS_FILE=$TOKEN_FILE
ReadOnlyPaths=$SITE_FILE $TOKEN_FILE
EOF

bundle="$KIT_DIR/${observer_id}.env"
umask 077
cat > "$bundle" <<EOF
ATLAS_OBSERVER_ID=$observer_id
ATLAS_LOCAL_NODE=$node_id
ATLAS_MUX_HOST=127.0.0.1
ATLAS_MUX_PORT=4405
ATLAS_API_URL=$api_url
ATLAS_INGEST_TOKEN=$token
ATLAS_SITE_LATITUDE=$latitude
ATLAS_SITE_LONGITUDE=$longitude
EOF
chmod 0600 "$TOKEN_FILE" "$SITE_FILE" "$bundle"

systemctl daemon-reload
systemctl restart atlas-api.service
sleep 2
systemctl is-active --quiet atlas-api.service || die "atlas-api failed after provisioning"
curl -fsS http://127.0.0.1:18081/api/v1/health >/dev/null || die "local ATLAS API health check failed"

echo
echo "Provisioned $observer_id ($node_id)."
echo "Private remote-install bundle: $bundle"
echo "Transfer that file securely; it contains the independently revocable site token."
echo "Then run on the remote host:"
echo "  sudo ./migrate-install-mux.sh"
echo "  sudo ./migrate-install-collector.sh --site-config ${observer_id}.env"
