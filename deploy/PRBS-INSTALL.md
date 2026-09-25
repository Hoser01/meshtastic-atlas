# PRBS Ubuntu deployment

This bundle is specific to `[PRBS] Preston Base Nixa` (`!f66ae274`) at
`37.0796477, -93.2799178`. It contains no credentials. Obtain the PRBS site
key from the ATLAS administrator before starting.

On the Ubuntu collector host:

```bash
cd /tmp
curl -fsSLO https://atlas.lzmesh.com/downloads/PRBS-atlas-deployment.tar.gz
curl -fsSLO https://atlas.lzmesh.com/downloads/PRBS-atlas-deployment.tar.gz.sha256
sha256sum -c PRBS-atlas-deployment.tar.gz.sha256
tar -xzf PRBS-atlas-deployment.tar.gz
cd PRBS-atlas-deployment
sudo ./PRBS-install.sh
```

The installer elevates with `sudo`, installs or upgrades the MUX from its
definitive HTTPS GitHub repository, installs the checksum-verified ATLAS
collector release, and runs end-to-end self-tests. A legacy feeder is not
required. If one is detected, it is disabled only after the replacement is
verified healthy.

## Prompt answers

- **Physical Meshtastic node host/IP:** Enter the LAN IP address of the actual
  Meshtastic radio. Do not enter `127.0.0.1` unless the radio service truly runs
  on this Ubuntu computer.
- **Physical Meshtastic TCP port:** Press Enter for `4403` unless the node was
  deliberately configured to use another port.
- **MUX client listen port:** Press Enter for `4405`.
- **ATLAS site token:** Paste the private PRBS site key supplied separately.
  Input is hidden; press Enter after pasting.

Observer ID, node ID, collector MUX address, and ATLAS API address are already
set for PRBS and are not prompted.
