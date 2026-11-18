# Meshtastic HTTP Bridge

This standalone sidecar adds the Meshtastic HTTP API to an existing native TCP
MUX. It neither installs nor changes the MUX, physical radio, or ATLAS
collector.

```text
Meshtastic Web -> HTTPS :4408 -> bridge -> TCP MUX :4405 -> radio :4403
```

The bridge provides HTTP on port `4407` and, by default, HTTPS on port `4408`.
The installer creates a self-signed certificate containing the host's LAN IP.
Each client IP and allowed browser origin receives an independent bounded
receive queue and persistent MUX connection.

Supported endpoints:

- `PUT /api/v1/toradio`
- `GET /api/v1/fromradio`
- `GET /api/v1/fromradio?all=true`
- `OPTIONS` browser preflight requests
- `GET /healthz`

## Install

Extract the separately published deployment archive and run:

```bash
./install.sh
```

The default allowed browser origin is `https://client.meshtastic.org`. Override
it or the detected LAN address when necessary:

```bash
./install.sh --origin https://client.meshtastic.org --public-host 10.10.10.30
```

After installation, first visit the reported HTTPS endpoint directly and
accept the local certificate warning. Then configure Meshtastic Web with the
reported IP and port, with **Use HTTPS** enabled.

Keep both bridge ports restricted to a trusted LAN or VPN. CORS limits browser
origins but is not user authentication.

## Status

```bash
sudo systemctl status meshtastic-http-bridge.service --no-pager
sudo journalctl -u meshtastic-http-bridge.service -n 50 --no-pager
curl -fsS http://127.0.0.1:4407/healthz
```

The bridge is based in part on the MIT-licensed G2MS HTTP bridge supplied for
interoperability testing. Its license notice is included in `LICENSE-MIT`.
