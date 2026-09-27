#!/usr/bin/env bash
set -Eeuo pipefail

OBSERVER_ID="TFLZ"
LOCAL_NODE="!db51d39c"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

[[ -r /etc/os-release ]] || die "Raspberry Pi OS, Ubuntu, or Debian is required"
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  ubuntu:*|debian:*|*:debian*) ;;
  *) die "unsupported OS: ${PRETTY_NAME:-unknown}; this package supports Raspberry Pi OS, Ubuntu, and Debian" ;;
esac

for script in migrate-install-mux.sh migrate-install-collector.sh; do
  [[ -f "$SCRIPT_DIR/$script" ]] || die "bundle is missing $script"
  chmod +x "$SCRIPT_DIR/$script"
done

echo "ATLAS deployment for ${OBSERVER_ID} (${LOCAL_NODE})"
echo "Site: Perseus (37.126, -94.4708)"
echo "Host: ${PRETTY_NAME:-unknown} / $(uname -m)"
echo
echo "Stage 1 upgrades the existing Meshtastic TCP multiplexer."
echo "The current radio endpoint and listen port are detected and offered as defaults."
"$SCRIPT_DIR/migrate-install-mux.sh"

site_config=$(mktemp /tmp/atlas-TFLZ-site.XXXXXX)
trap 'case "${site_config:-}" in /tmp/atlas-TFLZ-site.*) rm -f -- "$site_config";; esac' EXIT
chmod 0600 "$site_config"
cat > "$site_config" <<EOF
ATLAS_OBSERVER_ID=$OBSERVER_ID
ATLAS_LOCAL_NODE=$LOCAL_NODE
ATLAS_MUX_HOST=127.0.0.1
ATLAS_MUX_PORT=4405
ATLAS_API_URL=https://atlas.lzmesh.com/api/v1/events
EOF

echo
echo "Stage 2 installs the collector. Enter the TFLZ site key when prompted."
echo "The key is hidden while typed. If a legacy feeder exists, it is disabled"
echo "only after ATLAS verifies a healthy heartbeat and live MUX traffic."
"$SCRIPT_DIR/migrate-install-collector.sh" --site-config "$site_config"

echo
echo "TFLZ deployment is complete. Useful checks:"
echo "  sudo systemctl status meshtastic-tcp-mux.service atlas-observer.service --no-pager"
echo "  sudo journalctl -u atlas-observer.service -n 50 --no-pager"
