#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || { echo "Run as root." >&2; exit 1; }
  exec sudo -- "$0" "$@"
fi

systemctl disable --now meshtastic-http-bridge.service 2>/dev/null || true
rm -f /etc/systemd/system/meshtastic-http-bridge.service
systemctl daemon-reload
echo "Bridge service removed. Configuration and releases were retained."
