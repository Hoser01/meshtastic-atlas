#!/usr/bin/env bash
set -Eeuo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
binary=/opt/atlas-observer/current/bin/atlas-http-bridge
observer_env=/etc/atlas-observer/observer.env
bridge_dir=/etc/atlas-http-bridge
bridge_env=$bridge_dir/bridge.env

[[ -x "$binary" ]] || die "ATLAS release does not include atlas-http-bridge; update the observer first"
[[ -f "$script_dir/atlas-http-bridge.service" ]] || die "bundle is missing atlas-http-bridge.service"
[[ -f "$script_dir/atlas-http-bridge.env.example" ]] || die "bundle is missing bridge configuration"

install -d -m 0750 "$bridge_dir"
if [[ ! -f "$bridge_env" ]]; then
  install -m 0640 -o root -g atlas-observer "$script_dir/atlas-http-bridge.env.example" "$bridge_env"
fi
install -m 0644 "$script_dir/atlas-http-bridge.service" /etc/systemd/system/

if [[ -f "$observer_env" ]] && ! grep -q '^ATLAS_COMPANION_SERVICES=' "$observer_env"; then
  printf '\nATLAS_COMPANION_SERVICES=atlas-http-bridge.service\n' >> "$observer_env"
fi

systemctl daemon-reload
systemctl enable --now atlas-http-bridge.service

healthy=false
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:4404/healthz >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done
if [[ "$healthy" != true ]]; then
  journalctl -u atlas-http-bridge.service -n 100 --no-pager
  die "HTTP bridge did not connect to the TCP MUX"
fi

echo
echo "Meshtastic HTTP bridge is ready."
echo "  Client URL: http://$(hostname -I | awk '{print $1}'):4404"
echo "  Health:     http://127.0.0.1:4404/healthz"
echo "Keep TCP port 4404 limited to the trusted LAN or VPN."
