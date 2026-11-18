# Meshtastic HTTP Bridge

This is a standalone HTTP compatibility service for Meshtastic TCP
multiplexers. It does not install or depend on the ATLAS collector.

The bridge exposes the firmware-compatible Meshtastic PhoneAPI endpoints on a
LAN HTTP port and connects upstream as an ordinary client of a native
Meshtastic TCP MUX:

```text
HTTP client -> bridge :4404 -> TCP MUX :4405 -> radio :4403
```

Supported endpoints:

- `PUT /api/v1/toradio`
- `GET /api/v1/fromradio`
- `GET /api/v1/fromradio?all=true`
- `OPTIONS` browser preflight requests
- `GET /healthz`

The receive queue is bounded, reconnection is automatic, CORS and browser
private-network preflight are supported, and TLS can be enabled with a local
certificate. Keep the HTTP port restricted to a trusted LAN or VPN.

## Install

Use the separately published deployment archive. It installs only the bridge;
it does not install, reconfigure, update, or stop either the TCP MUX or an ATLAS
collector. The default upstream is the local MUX at `127.0.0.1:4405`, and the
default LAN-facing HTTP endpoint is port `4404`.

```bash
tar -xzf meshtastic-http-bridge-deploy-0.1.2.tar.gz
cd meshtastic-http-bridge-deploy-0.1.2
./install.sh
```

The installer elevates through `sudo` when needed. It uses port `4404` when
available, otherwise it selects and reports an available port from `4407`
through `4410`. Check the service using the reported port:

```bash
curl -fsS http://127.0.0.1:4404/healthz
sudo systemctl status meshtastic-http-bridge.service --no-pager
```
