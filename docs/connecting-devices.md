**English** · [Français](fr/connecting-devices.md) · [Español](es/connecting-devices.md)

# Connecting devices

Repositories, caches and software: one command per device, or a group policy for the whole domain. To manage the devices themselves (inventory, updates, remote control), see [My fleet](fleet.md).

In **Linux › Packages**, first create the repositories you need: distribution, optional version
number and code name (for example `debian/trixie` or `ubuntu/noble`). A future release is added
from this page, without changing the code. Each repository has its own files and signed indexes.

Devices trust the local authority (`ca.crt`, deployed by GPO or from the
*Administration › System* page), then:

```bash
# Debian / Ubuntu: local repository + APT cache
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh
# … first connection, authority not yet imported (shows its fingerprint)
curl -fsSL http://<ip>:8088/linux/setup.sh | sudo sh -s -- --trust-ca
# Cache only, without the local repository
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh -s -- --cache-only

# Rocky Linux, AlmaLinux, RHEL 8+: local elN repository + official repositories through the cache (--epel: EPEL too)
curl -fsSL https://<ip>:8443/rpm/setup.sh | sudo sh

# Flatpak: Flathub through the cache + local "loco" repository
curl -fsSL https://<ip>:8443/flatpak/setup.sh | sudo sh
```

```powershell
# Windows, administrator PowerShell: installs one or more programs from the catalog
irm 'https://<ip>:8443/win/i/7-zip,vlc' | iex
```

The Windows script checks the SHA-256 of each installer, gives each one 30 minutes (`$env:LOCO_TIMEOUT`)
and recognizes the "reboot required", "already installed" and "not applicable" codes.

For Windows Update, there are two ways to send the devices' downloads to loco, chosen on the
*Windows Update* page: **by DNS** (the network's DNS returns the server's IP for the update domains; a
script for an Active Directory DNS is provided) or **by proxy, without DNS** — loco ships a caching proxy (Squid) and
the agent sets the WinHTTP proxy of the managed Windows devices to loco (per device, reversible, removed if loco becomes
unreachable). Both cache the same immutable files and relay HTTPS without decrypting it. The
manual `netsh winhttp set proxy` command is also offered (*Windows Update* and *Enrollment* pages).

The Linux script reads `/etc/os-release` and selects the repository matching the device. If that repository
does not exist, it stops before replacing the configuration. Derivatives can choose explicitly with
`--distribution ubuntu --suite noble`. APT sources use the deb822 format (`loco.sources`).

**Upgrading from 1.0.0**: the old `/apt/` repository and the devices already connected keep working.
The *Copy to another repository* action keeps the originals. Check that each package is compatible,
then run the script again on the devices. `--legacy` explicitly keeps the old shared repository.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
