#!/usr/bin/env bash
set -Eeuo pipefail

OBSERVER_ID="PRBS"
LOCAL_NODE="!f66ae274"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

[[ -r /etc/os-release ]] || die "Ubuntu or Debian is required"
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  ubuntu:*|debian:*|*:debian*) ;;
  *) die "unsupported OS: ${PRETTY_NAME:-unknown}; this package supports Ubuntu and Debian" ;;
esac

for script in migrate-install-mux.sh migrate-install-collector.sh; do
  [[ -f "$SCRIPT_DIR/$script" ]] || die "bundle is missing $script"
  chmod +x "$SCRIPT_DIR/$script"
done

echo "ATLAS deployment for ${OBSERVER_ID} (${LOCAL_NODE})"
echo "Site: Preston Base Nixa (37.0796477, -93.2799178)"
echo "Host: ${PRETTY_NAME:-unknown} / $(uname -m)"
echo
echo "Stage 1 installs or upgrades the Meshtastic TCP multiplexer."
echo "Enter the LAN IP address of the physical Meshtastic node when prompted."
"$SCRIPT_DIR/migrate-install-mux.sh"

site_config=$(mktemp /tmp/atlas-PRBS-site.XXXXXX)
trap 'case "${site_config:-}" in /tmp/atlas-PRBS-site.*) rm -f -- "$site_config";; esac' EXIT
chmod 0600 "$site_config"
cat > "$site_config" <<EOF
ATLAS_OBSERVER_ID=$OBSERVER_ID
ATLAS_LOCAL_NODE=$LOCAL_NODE
ATLAS_MUX_HOST=127.0.0.1
ATLAS_MUX_PORT=4405
ATLAS_API_URL=https://atlas.lzmesh.com/api/v1/events
EOF

echo
echo "Stage 2 installs the collector. Enter the PRBS site key when prompted."
echo "The key is hidden while typed. If a legacy feeder exists, it is disabled"
echo "only after ATLAS verifies a healthy heartbeat and live MUX traffic."
echo "If no legacy feeder exists, the installer reports that and continues normally."
"$SCRIPT_DIR/migrate-install-collector.sh" --site-config "$site_config"

echo
echo "PRBS deployment is complete. Useful checks:"
echo "  sudo systemctl status meshtastic-tcp-mux.service atlas-observer.service --no-pager"
echo "  sudo journalctl -u atlas-observer.service -n 50 --no-pager"
