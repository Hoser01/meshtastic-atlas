#!/usr/bin/env bash
set -Eeuo pipefail

OBSERVER_ID="MTVF"
ENV_FILE="/etc/atlas-observer/observer.env"
HEALTH_URL="https://atlas.lzmesh.com/api/v1/observer-health"
SITE_TOKEN="Nze92XFW1O-kOy4xQgcILYkID6W8fYImvr2llVpEtzJpCtheAp22xzAQSuP70Hiz"

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

[[ -f "$ENV_FILE" ]] || die "$ENV_FILE does not exist"
token=$SITE_TOKEN
[[ ${#token} -eq 64 ]] || die "site key is ${#token} characters; expected exactly 64"
[[ "$token" =~ ^[A-Za-z0-9_-]{64}$ ]] || die "site key contains invalid characters"

cp -a "$ENV_FILE" "$ENV_FILE.before-token-repair"
python3 - "$ENV_FILE" "$token" <<'PY'
import os, pathlib, sys, tempfile
path=pathlib.Path(sys.argv[1]); token=sys.argv[2]
lines=path.read_text().splitlines()
replacement=f"ATLAS_INGEST_TOKEN={token}"
found=False
for index,line in enumerate(lines):
    if line.startswith("ATLAS_INGEST_TOKEN="):
        lines[index]=replacement; found=True
if not found: lines.append(replacement)
fd,name=tempfile.mkstemp(prefix=".observer-env-",dir=path.parent)
with os.fdopen(fd,"w") as stream: stream.write("\n".join(lines)+"\n")
os.chmod(name,0o600); os.replace(name,path)
PY
unset token

systemctl restart atlas-observer.service
systemctl is-active --quiet atlas-observer.service || {
  journalctl -u atlas-observer.service -n 80 --no-pager
  die "collector did not restart"
}

echo "Waiting for a verified MTVF heartbeat..."
verified=false
for _ in $(seq 1 24); do
  if curl -fsS -A "ATLAS-Installer/1" "$HEALTH_URL" | python3 -c '
import json,sys
observer=sys.argv[1]
try: rows=json.load(sys.stdin)
except Exception: raise SystemExit(1)
row=next((item for item in rows if item.get("observer_id")==observer),None)
if not row: raise SystemExit(1)
if row.get("status")!="online" or not row.get("mux_connected"): raise SystemExit(1)
if row.get("delivery_queue_depth") not in (0,None): raise SystemExit(1)
print(f"verified: status={row['"'"'status'"'"']} mux_connected={row['"'"'mux_connected'"'"']} queue={row.get('"'"'delivery_queue_depth'"'"',0)}")
' "$OBSERVER_ID"
  then verified=true; break; fi
  sleep 5
done
[[ "$verified" == true ]] || {
  journalctl -u atlas-observer.service -n 100 --no-pager
  die "MTVF did not verify centrally; legacy feeder remains enabled"
}

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
  echo "Disabling verified legacy feeder: $unit"
  systemctl disable --now "$unit"
done

echo "MTVF token repair and central verification completed successfully."
if ((${#legacy_services[@]}==0)); then echo "No recognized legacy feeder service was found."; fi
