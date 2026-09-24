#!/usr/bin/env bash
set -Eeuo pipefail

OBSERVER_ID="MTVF"
LOCAL_NODE="!9716e428"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

[[ -r /etc/os-release ]] || die "a Debian/Raspberry Pi OS host is required"
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  debian:*|raspbian:*|ubuntu:*|*:debian*) ;;
  *) die "unsupported OS: ${PRETTY_NAME:-unknown}; use Raspberry Pi OS or Debian" ;;
esac

for script in migrate-install-mux.sh migrate-install-collector.sh; do
  [[ -x "$SCRIPT_DIR/$script" ]] || die "bundle is missing $script"
done

echo "ATLAS migration for ${OBSERVER_ID} (${LOCAL_NODE})"
echo "Host: ${PRETTY_NAME:-unknown} / $(uname -m)"
echo
echo "Stage 1 installs or upgrades the Meshtastic TCP multiplexer."
echo "Confirm the physical radio endpoint when prompted (commonly 127.0.0.1:4403)."
"$SCRIPT_DIR/migrate-install-mux.sh"

site_config=$(mktemp /tmp/atlas-MTVF-site.XXXXXX)
trap 'case "${site_config:-}" in /tmp/atlas-MTVF-site.*) rm -f -- "$site_config";; esac' EXIT
chmod 0600 "$site_config"
cat > "$site_config" <<EOF
ATLAS_OBSERVER_ID=$OBSERVER_ID
ATLAS_LOCAL_NODE=$LOCAL_NODE
ATLAS_MUX_HOST=127.0.0.1
ATLAS_MUX_PORT=4405
ATLAS_API_URL=https://atlas.lzmesh.com/api/v1/events
EOF

echo
echo "Stage 2 installs the collector. Enter the MTVF site key when prompted."
echo "The key is hidden while typed. The legacy feeder is disabled only after"
echo "ATLAS verifies a healthy heartbeat, MUX connection, and empty delivery queue."
"$SCRIPT_DIR/migrate-install-collector.sh" --site-config "$site_config"

echo
echo "MTVF migration is complete. Useful checks:"
echo "  sudo systemctl status meshtastic-tcp-mux.service atlas-observer.service --no-pager"
echo "  sudo journalctl -u atlas-observer.service -n 50 --no-pager"
