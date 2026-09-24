# MTVF Raspberry Pi migration

This bundle is specific to `[MTVF] Mt Vernon Feeder` (`!9716e428`). It contains
no credentials. Obtain the MTVF site key from the ATLAS administrator before
starting.

On the Raspberry Pi:

```bash
cd /tmp
curl -fsSLO https://atlas.lzmesh.com/downloads/MTVF-atlas-migration.tar.gz
curl -fsSLO https://atlas.lzmesh.com/downloads/MTVF-atlas-migration.tar.gz.sha256
sha256sum -c MTVF-atlas-migration.tar.gz.sha256
tar -xzf MTVF-atlas-migration.tar.gz
cd MTVF-atlas-migration
sudo ./MTVF-install.sh
```

The installer prompts for the physical Meshtastic TCP endpoint, checks ports,
installs or updates the MUX from its definitive HTTPS GitHub repository,
installs the current checksum-verified ATLAS collector release, and prompts
silently for the MTVF site key. It disables a recognized legacy feeder service
only after the new collector passes its end-to-end health check.
