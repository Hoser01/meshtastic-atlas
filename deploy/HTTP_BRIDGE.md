# Meshtastic HTTP bridge

The ATLAS HTTP bridge lets an HTTP-only Meshtastic client use a radio that is
already connected through `meshtastic-tcp-mux`.

```text
physical radio :4403
        |
Meshtastic TCP MUX :4405
        |-- ATLAS observer
        |-- native TCP clients
        `-- HTTP bridge :4404 -- HTTP client
```

The bridge implements the firmware-compatible PhoneAPI endpoints:

- `PUT /api/v1/toradio`
- `GET /api/v1/fromradio`
- `GET /api/v1/fromradio?all=true`
- `OPTIONS` browser preflight requests
- `GET /healthz` for local service monitoring

HTTP bodies contain naked Meshtastic protobuf messages. The bridge adds and
removes the `0x94 0xc3` TCP framing used by the MUX. Its receive queue is
strictly bounded and the MUX connection reconnects automatically.

## Installation

Install or update the ATLAS observer to a release containing
`atlas-http-bridge`, then run from the extracted deployment kit:

```bash
sudo ./install-http-bridge.sh
```

The default service listens on all LAN interfaces at port `4404` and connects
to the local MUX at `127.0.0.1:4405`. Configuration is stored in:

```text
/etc/atlas-http-bridge/bridge.env
```

Check it with:

```bash
curl -fsS http://127.0.0.1:4404/healthz
sudo systemctl status atlas-http-bridge.service --no-pager
sudo journalctl -u atlas-http-bridge.service -n 100 --no-pager
```

In an HTTP client, enter the collector's LAN address and port `4404`, for
example `192.168.1.235:4404`, with HTTPS disabled unless TLS was explicitly
configured in `bridge.env`.

## Operational boundaries

- Keep port `4404` restricted to a trusted LAN or VPN. The Meshtastic HTTP API
  has no authentication and must not be exposed directly to the internet.
- The HTTP transport uses one shared receive queue, matching Meshtastic
  firmware behavior. Use one active HTTP client at a time. Other native TCP
  clients remain independently connected through the MUX.
- The MUX remains the policy boundary. If its administrative-command filter is
  enabled, the browser can read network state and use permitted messaging
  operations but blocked radio-configuration commands remain blocked.
- Hosted HTTPS web applications may refuse an unencrypted LAN endpoint because
  of browser mixed-content rules. Configure a locally trusted certificate and
  set both `ATLAS_HTTP_TLS_CERT` and `ATLAS_HTTP_TLS_KEY` when HTTPS is needed.
