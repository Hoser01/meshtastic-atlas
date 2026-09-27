# TFLZ Raspberry Pi collector upgrade

This bundle is specific to `[TFLZ] Perseus` (`!db51d39c`) at
`37.126, -94.4708`. It contains no credentials. Obtain the TFLZ site key from
the ATLAS administrator before starting.

On the Raspberry Pi collector host (Raspberry Pi OS/Debian):

```bash
cd /tmp
curl -fsSLO https://atlas.lzmesh.com/downloads/TFLZ-atlas-deployment.tar.gz
curl -fsSLO https://atlas.lzmesh.com/downloads/TFLZ-atlas-deployment.tar.gz.sha256
sha256sum -c TFLZ-atlas-deployment.tar.gz.sha256
tar -xzf TFLZ-atlas-deployment.tar.gz
cd TFLZ-atlas-deployment
sudo ./TFLZ-install.sh
```

The installer detects and upgrades the existing MUX, retaining its physical
radio endpoint and client listen port as the prompt defaults. It then installs
the checksum-verified ATLAS collector release and runs end-to-end self-tests.
A legacy feeder is disabled only after the replacement reports a healthy
heartbeat and live MUX traffic.

## Prompt answers

- **Physical Meshtastic node host/IP:** Normally press Enter to keep the
  detected existing MUX setting. If no value is detected, enter the LAN IP of
  the physical Meshtastic radio.
- **Physical Meshtastic TCP port:** Normally press Enter to keep the detected
  setting (`4403` is the standard default).
- **MUX client listen port:** Normally press Enter to keep the detected setting
  (`4405` is the ATLAS default).
- **ATLAS site token:** Paste the private TFLZ site key supplied separately.
  Input is hidden; press Enter after pasting.

Observer ID, node ID, collector MUX address, and ATLAS API address are already
set for TFLZ and are not prompted.
