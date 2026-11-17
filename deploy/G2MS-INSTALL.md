# G2MS collector deployment

This bundle is specific to `[G2MS] Mattatat25 Station G2` (`!a0352874`) at
`37.2625104, -93.2472157`. It contains no credentials. Obtain the G2MS site
key from the ATLAS administrator before starting.

On the Raspberry Pi OS, Ubuntu, or Debian collector host:

```bash
cd /tmp
curl -fsSLO https://atlas.lzmesh.com/downloads/G2MS-atlas-deployment.tar.gz
curl -fsSLO https://atlas.lzmesh.com/downloads/G2MS-atlas-deployment.tar.gz.sha256
sha256sum -c G2MS-atlas-deployment.tar.gz.sha256
tar -xzf G2MS-atlas-deployment.tar.gz
cd G2MS-atlas-deployment
sudo ./G2MS-install.sh
```

The installer installs or upgrades the MUX from its definitive GitHub
repository, preserving detected endpoint settings as prompt defaults. It then
downloads the checksum-verified ATLAS collector release and performs an
end-to-end health check. The only private value entered is the G2MS site key;
input is hidden and the generated environment file is installed root-only.
If a legacy feeder is present, it is disabled only after the new collector is
verified healthy. If no legacy feeder exists, installation continues normally.
Finally, it installs an HTTP compatibility bridge on TCP port `4404` for
browser clients that cannot connect directly to the native MUX stream.

## Prompt answers

- **Physical Meshtastic node host/IP:** Keep the detected value, or enter the
  LAN address of Station G2 if this is a new MUX installation.
- **Physical Meshtastic TCP port:** Normally `4403`.
- **MUX client listen port:** Normally `4405`.
- **ATLAS site token:** Paste the private G2MS key supplied separately.

Observer ID, node ID, collector MUX address, and ATLAS API address are built
into the installer and are not prompted.

## Browser client

Choose **HTTP**, leave **HTTPS** off, and enter the collector host with port
`4404`, for example:

```text
192.168.1.235:4404
```

Port `4404` must remain limited to the trusted LAN or VPN. The bridge does not
provide authentication and must not be forwarded directly to the internet.
