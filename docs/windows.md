**English** · [Français](fr/windows.md) · [Español](es/windows.md)

# Native Windows server

## Installing

Windows 11 Pro / Enterprise or Windows Server 2022 / 2025, x64. Windows Server Core
and Desktop Experience use the same installer. Plan a stable address, a local NTFS volume
with enough space for the caches and an Internet connection to download the components.
Start PowerShell 5.1 or later **as administrator**, get the project (ZIP archive or `git clone`)
then run the installer from the `windows` folder:

```powershell
Invoke-WebRequest https://github.com/ShaNouhAr/loco/archive/refs/heads/main.zip -OutFile loco.zip
Expand-Archive loco.zip -DestinationPath .
cd loco-main
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

The component archives come from Python, nginx, WinSW and PyPI; their versions
and SHA-256 are pinned in `windows/components.json` and checked before installation. No other software
is installed on the system: no GnuPG, no change to the `PATH`. The embedded Python
stays private to the application. The `loco` service starts automatically under the virtual account
`NT SERVICE\loco`; the portal is served by Waitress and nginx. The backup, certificate
renewal and statistics tasks are part of the service.

The script shows the HTTPS address, the authority's fingerprint and the initial `admin` password, to change
at the first sign-in. Deploy `C:\ProgramData\loco\etc\tls\ca.crt` to the devices to use
HTTPS, for example by GPO. The existing authority and keys are kept when running the installer again.

Example with folders on another volume and explicit networks:

```powershell
.\windows\install.ps1 -HostAddress 10.20.0.15 `
  -Allow 10.20.0.0/16 -AdminAllow 10.20.99.0/24 `
  -DataPath D:\loco-data -BackupPath D:\loco-backups
```

The data and backup folders must be local, separate, not nested and without spaces.
The program is in `%ProgramFiles%\loco`; the configuration and the state are in
`%ProgramData%\loco`. The ACLs give the service the rights it needs on its data,
its logs and its certificates; the installer's configuration stays reserved for the administrator.

| Parameter | Effect |
|---|---|
| `-HostAddress` | IPv4 or DNS name announced to the devices |
| `-Allow`, `-AdminAllow` | networks of the devices and of the interface; private networks by default |
| `-HttpsPort`, `-HttpPort`, `-NoHttp` | HTTPS 8443, HTTP bootstrap 8088 or HTTPS only |
| `-Proxy http://proxy:3128`, `-NoProxy` | outbound proxy or removal of the configured proxy |
| `-NoWU`, `-NoRpm`, `-NoFlatpak` | turn off the Windows Update cache, the RPM features or the Flathub cache |
| `-WuCacheSize`, `-AptCacheSize`, `-RpmCacheSize`, `-FlatpakCacheSize` | cap of each cache, for example `50g` |
| `-Resolvers 1.1.1.1,9.9.9.9` | outbound DNS for Windows Update; must not point back to the local server |
| `-MaxUpload 8g` | maximum size of an upload |
| `-BackupKeep 3` | daily backups kept; `0` turns the daily task off |

Parameters left out when running again keep the existing configuration. The firewall rules
of the `loco` group are limited to the nginx program and to the configured networks. The application portal
8089 stays on the local loopback, like the neighboring port 8090 of the remote sessions relay. The APT cache listens on 3142; Windows Update uses 80 and 443.
The installer refuses ports already in use, especially by IIS/HTTP.sys. Free these ports or use
`-NoWU` if the Windows Update cache is not needed.

## Connecting devices

The portal's commands are the same for Windows and Linux servers. APT repositories are organized
by distribution and release name; RPM repositories by major version `el8`, `el9`, `el10`.
Adding a Debian/Ubuntu release in the interface does not require changing the program.
New packages are indexed and signed by the program: OpenPGP signatures (RSA 4096 key,
`%ProgramData%\loco\state\signing-key.json`, readable only by the service and administrators),
checked by the devices with gpgv, sqv (Debian 13's apt) or dnf as for a Linux server. The publishers'
signatures are kept for the caches' contents. On Windows, the APT cache is provided by nginx instead of apt-cacher-ng.

Windows Update is connected through the DNS settings offered in the portal. HTTP downloads
are cached; HTTPS is relayed to the Microsoft server without decryption. No WSUS role
is installed. The Windows software catalog remains available separately.

The Flathub cache works for Linux devices. Publishing a **local Flatpak repository** requires
ostree and is only available when the loco server runs on Linux; the Windows
interface shows this limit and does not offer local Flatpak imports.

## Administering, updating and restoring

```powershell
$lr = "$env:ProgramFiles\loco\loco.cmd"
& $lr status
& $lr restart
& $lr user list
& $lr user add operator viewer
& $lr user passwd admin
& $lr fingerprint
& $lr backup create
& $lr backup list
& $lr backup restore 'D:\loco-backups\loco-server-YYYYMMDD-HHMMSS.tar.gz'
```

Run these commands as administrator. Restoring stops the service, restores the repositories,
accounts and keys, then restarts the service. Download caches are not backed up.
The address, the paths and the networks of the current server are kept. These backups are meant
for a Windows installation; they are not a migration format between Linux and Windows.
Copy the archives regularly to another storage, with suitable rights: they contain
the private keys and the accounts.

To update, get the new sources then run `windows\install.ps1` again as administrator.
Before a major update, create a backup. Components are downloaded and checked
before the service stops. See the logs in `%ProgramData%\loco\logs` and the state in
the portal's *System* page.

```powershell
# Removes the service and the program; keeps the data, accounts, keys and backups.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1"

# Full erasure, with confirmation.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1" -Purge
```

Windows 11 blocks PowerShell scripts by default: `-ExecutionPolicy Bypass` allows it
for this single command, without changing the machine's policy.

After a simple uninstallation, running the installer again from the sources finds the data.
`-Purge -Yes` allows a non-interactive erasure for automated deployments.

## Sizing and validation

nginx for Windows uses a single worker here and at most 1,000 connections; its publisher describes it
as a beta version, suited to a limited load. For a large fleet or heavy concurrent
throughput, prefer the Linux server and measure the load before deploying.
See the [official limits of nginx for Windows](https://nginx.org/en/docs/windows.html).

The CI runs the native installation on Windows Server 2022 and 2025, with real packages built
on Linux: imports over HTTPS, signature checks, APT cache MISS/HIT, backup/restore,
re-run, reinstallation, uninstallation keeping the data and purge. The Python package readers
are also compared with the Debian/RPM tools on Linux, and the OpenPGP signatures checked by
gpg, gpgv and sqv. Windows 11 (25H2) was verified in a separate test machine, with real devices:
Debian 13 (local HTTPS repository checked by sqv, Debian packages through the APT cache) and Rocky Linux 9 (`el9` repository
checked by dnf, BaseOS through the RPM cache), as well as a 88 MB Windows update served by the cache.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
