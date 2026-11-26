**English** · [Français](fr/linux.md) · [Español](es/linux.md)

# Operations guide: Linux server

This guide covers deploying loco in an enterprise network (lab, site, branch) and its
day-to-day operation. For a quick introduction, see the [README](../README.md).
The routine, backups, updates and incidents, for every kind of installation: [Running loco day to day](operations.md).

## 1. Requirements

| Item | Value |
|---|---|
| System | Debian 12/13, Ubuntu 22.04/24.04/26.04, Rocky Linux 8/9/10 (amd64 verified, VM recommended, see § 14 for LXC) |
| Resources | 2 vCPU, 2 GB of RAM; disk depending on the caches (50 GB or more advised) |
| Rights | root (installation), no Docker container |
| Internet access | HTTP/HTTPS to the Debian/Ubuntu repositories, Microsoft (Windows Update), `dl.flathub.org`, RPM mirrors (§ 12); direct or through a proxy (§ 5) |

Ports open on the server:

| Port | Service | Who reaches it |
|---|---|---|
| 8443/tcp | Interface (HTTPS) + the devices' repositories, installers and scripts | devices (`--allow`), administrators (`--admin-allow`) |
| 8088/tcp | HTTP bootstrap of the devices (can be turned off: `--no-http`) | devices |
| 3142/tcp | APT cache (apt-cacher-ng) | Linux devices |
| 80/tcp, 443/tcp | Windows Update cache (can be turned off: `--no-wu`) | Windows devices (through DNS) |

## 2. Installation

```bash
git clone https://github.com/ShaNouhAr/loco.git
cd loco
sudo ./install.sh --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24"
```

The installation shows the interface's address, the fingerprint of the certificate authority and an
**initial password** for the `admin` account, to change at the first sign-in. It is written nowhere
on disk; if it is lost: `sudo loco-user passwd admin`.

All the options: `sudo ./install.sh --help`. The settings are kept in
`/etc/loco/loco.conf`; running `install.sh` again (with or without options) applies the changes
and updates the software. apt's detailed output is in `/var/log/loco-install.log`.

## 3. Allowed networks

- `--allow`: networks that use the repositories and caches (apt-cacher-ng, Windows Update cache, Flathub,
  local repositories, scripts). By default: private networks (RFC 1918, IPv6 ULA).
- `--admin-allow`: networks that open the interface and `/metrics`. By default: like `--allow`.
  Recommended: the administration network only.

Filtering is applied by nginx (all the web ports) and by apt-cacher-ng itself (tcpwrappers). The server itself (127.0.0.1) is always allowed. A network firewall upstream is still advised.

## 4. Certificate authority and HTTPS

The installation creates a local authority (`/etc/loco/tls/ca.crt`, 10 years) that signs the server's
certificate (825 days, renewed by `install.sh` if it expires in less than 30 days or if the IP changes).
The devices must trust this authority to use HTTPS.

**Through GPO (recommended on a domain)**: Group Policy Management › Computer Configuration ›
Policies › Windows Settings › Security Settings › Public Key Policies ›
Trusted Root Certification Authorities › Import `ca.crt`.

**By hand**: commands of the *Administration › System* page (Windows and Linux). Check the SHA-256
fingerprint shown by the installation before importing.

**Linux, first connection without the authority**: `curl -fsSL http://SERVER:8088/linux/setup.sh | sudo sh -s -- --trust-ca`
imports the authority (fingerprint shown, to compare) then configures the repository over HTTPS.

**Enterprise certificate**: `sudo ./install.sh --tls-cert server.crt --tls-key server.key`
(back to the local authority: delete `/etc/loco/tls/custom` then run it again).

Once the authority is deployed everywhere, `--no-http` closes port 8088.

## 5. Corporate proxy

```bash
sudo ./install.sh --proxy http://proxy.company:3128
```

apt-cacher-ng and the portal's downloads (Windows software, packages by link) go through the proxy.
The **Windows Update cache** and the **Flathub cache** go out directly: allow the server to reach
`*.windowsupdate.com`, `*.dl.delivery.mp.microsoft.com` and `dl.flathub.org` on ports 80 and 443.

The Windows Update cache resolves names with `--resolvers` (1.1.1.1 and 9.9.9.9 by default). If public DNS
is blocked, give internal resolvers **that do not redirect** the Windows Update domains to this
server (for example the Active Directory forwarders), otherwise the cache would query itself.

## 6. Windows Update

The network's DNS must answer the server's IP for `windowsupdate.com` and `dl.delivery.mp.microsoft.com`
(subdomains included). For an Active Directory DNS, the *Windows Update* page provides `loco-dns.ps1`
to run on a domain controller (`-Remove` to go back).

Only these domains are relayed; only immutable files (name containing a fingerprint or a unique
identifier) are kept in the cache, the certificate lists and metadata always going to Microsoft.

## 7. Accounts and audit

- Shipped profiles: **Administrator** (everything), **Operator** (inventory, updates, deployments; no terminal,
  desktop or restart) and **Read-only**; others are created in *Administration › Access profiles*
  (see [Accounts, profiles and SSO](accounts.md)).
- Creation in *Administration › Accounts*: a temporary password is shown only once and must
  be changed at the first sign-in (12 characters minimum, without the account's name).
- A session idle for 8 h is closed; changing the password closes the account's other sessions.
- After 5 failures on an account (or 20 from one address) within 15 min, sign-in is blocked for 15 min.
- *Administration › Audit log*: sign-ins, failures, changes, with account and address; also in
  journald (`journalctl -u loco-portal`).

On the command line (root): `loco-user list | roles | add NAME [PROFILE] | set-role NAME PROFILE | passwd NAME | enable NAME | disable NAME`.

## 8. Backup and restore

A daily backup (03:30) is written to `/var/backups/loco`; the 3 most recent are
kept (`--backup-keep`, `--backup-dir`). It contains configuration, accounts, audit log, keys (authority,
GPG), APT repository, Windows catalog and Flatpak repository, **without the caches**. The archives contain private
keys: copy them to protected storage.

```bash
sudo loco-backup create            # on demand
sudo loco-backup list
```

**Restore (same server or new server)**:

```bash
sudo ./install.sh                        # on the new server, same --data as before
sudo loco-backup restore loco-OLD-20261013-033000.tar.gz
sudo ./install.sh                        # regenerates the services and the server certificate for the new IP
```

The server's own configuration (IP, ports, networks) is kept; the authority is restored, so the
devices have nothing to redo. Remember to update the DNS (Windows Update) if the IP changes.

## 9. Updating, monitoring, uninstalling

- **Update**: `git pull && sudo ./install.sh`. The database migrations are automatic.
  Before a major update of the system or the application, create a backup and validate
  the target on a test VM. Support for a future version must be checked by the tests.
- **The server's system** (kernel, OpenSSL, Squid, nginx, Python…): **Administration → Updates** lists the
  pending packages, security fixes first (Debian/Ubuntu `*-security` repository, Rocky's DNF security
  advisories), with the running and installed kernel, the reasons for a restart and the services still using
  old libraries. *Install security fixes* or *Update everything*, then *Restart the
  server* if needed. A separate root agent (`loco-agent`) validates the requests again and runs them: the portal stays
  unprivileged. The configuration files modified by loco are kept (`--force-confold`) and the affected loco
  services are restarted. Automatic check every day. On the console: `loco-update status|check|security|
  system|reboot`. Rights: *Update the server*, *Restart the server*. In an LXC container, the kernel is
  the host's (to update on Proxmox).
- **Monitoring**: `https://SERVER:8443/metrics` (Prometheus format, `--admin-allow` networks): state of the
  services, sizes of the caches, disk, days before the certificate expires, date of the last backup.
  Simple check: `https://SERVER:8443/healthz` → `ok`.
- **Uninstalling**: `sudo /opt/loco/remove.sh` (keeps the data) or `--purge` (erases everything except
  the backups). Only the packages installed by loco are removed.

## 10. Troubleshooting

| Symptom | Lead |
|---|---|
| The installation stops | The message gives the line at fault; apt details: `/var/log/loco-install.log` |
| "port N is already in use" | Free the port or change `--https-port` / `--http-port`; `--no-wu` frees 80/443 |
| Interface: 403 | The device's address is not in `--admin-allow` |
| Linux device: certificate error | Authority not imported: `setup.sh … --trust-ca` or GPO |
| Windows: `irm` fails over HTTPS | Authority missing from the machine's "Trusted Root Certification Authorities" store |
| Windows software stuck | No silent argument: fill it in (often `/S`); time limit: `$env:LOCO_TIMEOUT` |
| Red Hat device: "elN repository unavailable" | Create the version in *Linux › Packages*, or `--no-local` |
| Red Hat device: package not found after `--remove` | Normal: the original repositories are restored; `dnf makecache` |
| Flatpak on Ubuntu 24.04 **in an LXC container**: "Couldn't connect" | Ubuntu's `flatpak` AppArmor profile blocks the network under a Proxmox kernel: `ln -s /etc/apparmor.d/flatpak /etc/apparmor.d/disable/ && apparmor_parser -R /etc/apparmor.d/flatpak`, or use a VM |
| Logs | `journalctl -u loco-portal -u nginx -u apt-cacher-ng`, `/var/log/nginx/loco-*.log` |

## 11. APT repositories and new Linux versions

In **Linux › Packages**, add a repository with its distribution (`debian` or `ubuntu`), its **exact
code name** and, optionally, the version number shown. For example `debian/trixie` or
`ubuntu/noble`. The repository starts empty; upload the compatible `.deb` files to the selected repository.
The signed indexes and the files are independent under `/srv/loco/apt/repositories/DISTRIBUTION/SUITE/`.
The architectures are those of the packages (`amd64`, `arm64`, `all`…) and APT picks the device's.

To add an upcoming version, the same procedure is enough: no version number is hard-coded
in the application. **Creating a repository does not certify the compatibility of the application or of the packages**:
validate the dependencies, the installation and the restore on the target distribution before deploying.
The code names come from the [Debian releases](https://www.debian.org/releases/) and the
[Ubuntu releases](https://ubuntu.com/project/docs/release-team/list-of-releases/).

On the command line:

```bash
sudo loco-apt add debian trixie 13
sudo loco-apt add ubuntu noble 24.04
sudo loco-apt list
sudo loco-apt rebuild
```

The devices' script detects `ID` and `VERSION_CODENAME` in `/etc/os-release`, then downloads a
dedicated deb822 source. A missing repository stops it before the existing source is replaced.
For a derivative, choose its base explicitly:

```bash
curl -fsSL https://SERVER:8443/linux/setup.sh | sudo sh -s -- --distribution ubuntu --suite noble
```

`--cache-only` configures the APT cache without a local repository. `--no-proxy` configures the repository without a cache.
The device's official repositories stay configured as before; those served over HTTPS go out directly.
After a major upgrade of the device, run the script again to select its new repository.
When the repository changes, the `.deb` files cached on the device are cleaned to avoid reusing
a file from the old version. The packages already installed are not reinstalled
automatically; for a different build, use a new version of the package.

### Moving from the legacy repository

Update 1.1.0 keeps `/apt/`, its content, the GPG key and the configuration of the devices already
connected. No compatibility of the old packages is guessed. Use *Copy to another repository*
after validation; the original stays available for the other devices. Then run `setup.sh` again
on the devices concerned. `--legacy` explicitly keeps the shared legacy repository.

The backup contains all the repositories and their registry. The restore, followed by `install.sh`,
regenerates their indexes and signatures. Two files with the same name/version/architecture can
coexist in different repositories; within one repository, a different content requires a new
version of the package.

### Maintaining the compatibility matrix

`tests/platforms.json` is the list of the systems checked by the workflow: distribution, code name,
number, official image and fingerprint manifest. A new version is added in this file;
there is no duplicated list to maintain in the workflow.
The test VMs are disposable and their logs are archived. The active workflow checks
every push/PR, every Monday and every manual run.

To reproduce a test, install `qemu-system-x86`, `qemu-utils`, `genisoimage`, `curl`, `openssh-client`
and `git`, then run `tests/vm-e2e.sh debian-13` from a clone with the tags. KVM speeds up the test
if `/dev/kvm` is available; otherwise QEMU uses emulation. The results are in `test-results/`.
The images only boot after comparison with the fingerprint downloaded over HTTPS from the official server.

## 12. Red Hat family (RHEL, Rocky Linux, AlmaLinux)

**Cache of the official repositories.** Rocky Linux (`dl.rockylinux.org`), AlmaLinux (`repo.almalinux.org`) and EPEL
(`dl.fedoraproject.org`) are relayed under `/rpmcache/` with their certificates checked. The `.rpm` files
and the uniquely named metadata are kept for 6 months (`--rpm-cache-size`), `repomd.xml` for two minutes.
The devices check each package with their distribution's GPG keys, as with direct access. This cache
goes out directly, even with `--proxy`: allow these three mirrors on port 443.

**Local repositories.** One repository per major version, common to the whole family: `el9` serves RHEL 9, Rocky
Linux 9 and AlmaLinux 9. `el8`, `el9` and `el10` are created at installation; a future version is added
in *Linux › Packages* ("+ New repository", Rocky, AlmaLinux, RHEL), without changing the application. The metadata
(`repomd.xml`) is signed by the repository's key and checked by dnf (`repo_gpgcheck=1`); it contains
each package's fingerprint. A package marked for another version (`.el8` uploaded to `el9`) is refused,
as is a package with the same name/version and a different content.

**Devices.**

```bash
curl -fsSL https://SERVER:8443/rpm/setup.sh | sudo sh               # local repository + cache
curl -fsSL https://SERVER:8443/rpm/setup.sh | sudo sh -s -- --epel  # installs EPEL, through the cache
curl -fsSL http://SERVER:8088/rpm/setup.sh | sudo sh -s -- --trust-ca   # authority not imported yet
curl -fsSL https://SERVER:8443/rpm/setup.sh | sudo sh -s -- --remove    # back to the original state
```

The script reads `/etc/os-release`, picks `elN` and checks that it exists before changing the device. On
Rocky Linux and AlmaLinux, it creates `/etc/yum.repos.d/loco-cache.repo` (BaseOS, AppStream,
CRB or PowerTools, extras and EPEL through the cache, same GPG keys) and disables the equivalent original
repositories, recorded in `/etc/loco-rpm.state`; `--remove` restores them exactly.
`--no-cache` only adds the local repository; `--no-local`, only the cache.

**RHEL.** The Red Hat repositories are reserved to subscribed systems (certificates specific to each machine):
they stay in direct access, or go through Red Hat Satellite where there is one. On RHEL, loco provides
the local `elN` repository and EPEL through the cache (`--epel` installs `epel-release`; also enable CodeReady Builder
with `subscription-manager`). CentOS Stream and Oracle Linux are treated as RHEL.

**Validation.** `tests/vm-rpm-e2e.sh` starts a Debian 13 server and one device per entry of
`tests/rpm-platforms.json` (Rocky Linux and AlmaLinux 8, 9 and 10), installs from the local repository, from
the cache and from EPEL, checks the service through the cache, then checks the exact return to the original state.
A new version of Rocky Linux or AlmaLinux is added in this file (official cloud image and
fingerprint manifest).


## 13. Docker deployment

The image uses the same code and the same configuration as the native installation: at each start,
`linux/docker/entrypoint.sh` translates the variables of `.env` into options of `linux/install.sh --container`, which regenerates
the configuration (nginx, apt-cacher-ng, portal), then `supervisord` starts the services.

With the image published with each version (`ghcr.io/shanouhar/loco`), `docker-compose.yml` is enough — no need to
clone the project:

```bash
mkdir loco && cd loco
curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/docker/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/docker/.env.example -o .env   # at least LOCO_HOST, the address announced to the devices
docker compose up -d
docker compose logs loco  # address, authority fingerprint, initial password (only once)
```

`LOCO_IMAGE` chooses the image: by default the latest published (`…:latest`), or a precise version
(`ghcr.io/shanouhar/loco:2.165.0`). The image is rebuilt with each version of loco, with the Debian packages of
the moment.

From the sources, `sudo ./up.sh` (in `linux/docker`) builds the image on the spot instead of downloading it: it
adds `docker-compose.build.yml` to `docker-compose.yml`.

**Variables.** They match the options of `install.sh` (`LOCO_ALLOW` ↔ `--allow`, `LOCO_PROXY` ↔ `--proxy`,
`LOCO_WU=0` ↔ `--no-wu`, `LOCO_HTTP=0` ↔ `--no-http`…, commented list in `.env.example`) and are authoritative: a
variable removed goes back to its default value at the next start (`docker compose up -d`). Only the
cache sizes left empty are computed at the first start, then kept. Enterprise
certificate: mount the folder (commented line of `docker-compose.yml`) and fill in `LOCO_TLS_CERT` and
`LOCO_TLS_KEY`; to go back to the local authority, delete `/etc/loco/tls/custom` in the container.

**Network.** The container uses the host's network (`network_mode: host`): the Windows Update cache listens
on 80 and 443, and the per-network filtering sees the devices' real address. Ports to open and network
requirements: the same as the native installation (sections 1 and 3). At start, a port already taken on the host
stops the container with an explicit message (`docker compose logs`).

**Data.** Named volumes: `loco-data` (`/srv/loco`, repositories and caches, the largest),
`loco-state` (database, signing key), `loco-etc` (configuration, authority, certificates),
`loco-apt-cache`, `loco-logs` (nginx logs, for the Windows Update statistics) and
`loco-backups`. `LOCO_DATA_DIR` and `LOCO_BACKUP_DIR` replace them with folders of the host. The
`loco` (990) and `apt-cacher-ng` (991) accounts have fixed identifiers: the volumes stay valid from one
image to the next.

**Operation.**

```bash
docker compose exec loco loco-user passwd admin       # lost access
docker compose exec loco loco-backup create           # immediate backup
docker compose exec loco loco-backup restore /var/backups/loco/ARCHIVE
docker compose restart loco                                  # after a restore
docker compose pull && docker compose up -d            # update (published image)
git pull && sudo ./up.sh                           # update (from the sources)
docker compose down                                                # stop (data kept)
```

The tasks of the systemd timers are done by `loco-cron`: size of the caches every
15 minutes, nginx reload when the list of Windows Update domains changes, log rotation
then backup (if `LOCO_BACKUP_KEEP` > 0) every day at 03:30, in the time of `TZ`. Services' logs:
`docker compose logs`. State: `/healthz` (Docker health check), *System* page and `/metrics`.

**Security.** The container starts without any Linux capability, except those needed by the root processes
(change of owner and user, ports 80/443), with `no-new-privileges`. The portal, nginx
(worker processes) and apt-cacher-ng run under their dedicated accounts, as in native. On the other hand, the
portal's systemd confinement (read-only file system, system call filtering) does not exist
in the container: the native installation stays recommended when possible.

**Validation.** `tests/vm-docker-e2e.sh` installs Docker (Debian packages) in a disposable Debian 13 VM,
builds the image, connects the VM itself as a device (local repository and cache), checks the services, the
backup, the preservation of the data and the authority when the container is recreated, the application
of the variables (Windows Update and HTTP turned off then back on), then the start with `docker-compose.yml`
alone, outside the sources.

## 14. Proxmox VE LXC container

`linux/lxc/proxmox-lxc.sh` runs as root on the Proxmox VE host (7, 8 or 9, amd64), alone or from a copy
of the project. It creates an **unprivileged** container (`nesting=1`, started with the host), waits for the network, then
does the native installation of `install.sh` in the container: same systemd services, same options, same
portal as on a Debian machine.

```bash
curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/lxc/proxmox-lxc.sh
bash proxmox-lxc.sh --help
bash proxmox-lxc.sh --hostname loco --ip 10.20.0.15/24 --gw 10.20.0.1 --vlan 20 --disk 400 \
    -- --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24" --proxy http://proxy.example.lan:3128
```

**Automatic choices.** Next free number, disk storage `local-lvm`, then `local-zfs`, then the
first active storage that accepts containers; templates on `local` or the first compatible storage;
bridge `vmbr0` or the first `vmbr*` bridge; Debian 13 template (Debian 12 on Proxmox 7 or if Debian 13 is not
offered), downloaded by `pveam` if missing, with fingerprint check. Each choice can be forced with an
option (`--storage`, `--template-storage`, `--bridge`, `--ctid`…).

**Installed version.** Run alone, the script installs the latest version published on GitHub (archive
downloaded by the container over HTTPS); `--version v1.4.0` pins a version, `--source DIR` installs a
local copy of the project (host without access to GitHub, test version). Run from a copy of the project, it
installs that copy.

**Disk.** Repositories and caches are on the container's disk (`--disk`, 100 GB by default); the maximum sizes
of the caches are computed at installation from the free space. To grow it afterwards:
`pct resize N rootfs +200G`, then adjust the sizes with `install.sh` (`--wu-cache-size`… options).

**Operation from the host.**

```bash
pct exec N -- loco-user passwd admin              # lost access
pct exec N -- loco-backup create                  # immediate backup
bash proxmox-lxc.sh --ctid N --update                   # update (latest published version)
bash proxmox-lxc.sh --ctid N --update -- --no-wu        # update and change of option
pct destroy N --purge                                   # complete removal
```

The container's Proxmox backups (`vzdump`) come on top of loco's. The caches
rebuild themselves: excluding them avoids backing up tens of gigabytes, for example
`vzdump N --exclude-path /srv/loco/wucache --exclude-path /srv/loco/flatpakcache
--exclude-path /srv/loco/rpmcache --exclude-path /var/cache/apt-cacher-ng`.

**Specifics.** The Proxmox firewall is enabled on the interface (`firewall=1`) but only has an effect if it
is enabled for the container: then open the ports of section 1. apt-cacher-ng's filtering relies
on tcpwrappers, which works in a container. On a host whose AppArmor blocks Flatpak in containers,
install with `-- --no-flatpak`.

**Validation.** Tested on Proxmox VE 9.2: creation in DHCP from the script alone (download of the
published version), `--update` from a local copy, then a complete end-to-end test
(`tests/e2e.sh`: devices, Flatpak, backup, restore, uninstallation) in the container.


## 15. Native server on Rocky Linux

The same `sudo ./install.sh` supports Rocky Linux 8, 9 and 10 (amd64 validated). The installer uses
DNF, enables PowerTools (8) or CRB (9/10) and installs EPEL if needed. The portal has an isolated Python environment,
with dependencies and SHA-256 fingerprints pinned in `lib/requirements-rocky.txt` (Python 3.11 on Rocky 8 and 9).
The databases, keys, paths, options and administration commands are the same as on Debian.

SELinux stays in enforcing mode: contexts of the files and caches, HTTP ports and nginx outbound connections are
configured. If firewalld is active, the `loco` service opens only the ports of the enabled features,
in the active zones. The nginx ACLs keep limiting the client and administrator networks.
The apt-cacher-ng backend only listens on `127.0.0.1:23142`; nginx filters the connections to port
3142. Port 23142 must never be exposed.

On Rocky 10, apt-cacher-ng not being available in EPEL, the Debian 3.7.5-1.3 source and its
patches are downloaded with pinned fingerprints then built in `/usr/local/lib/loco/acng`.
A rerun reuses that build. A timer handles the cache's daily expiry and logrotate keeps seven logs. The versions of the sources and dependencies must be updated
with the project's next releases; they do not follow DNF automatically.

The uninstallation metadata is reserved to root in `/etc/loco/install-state` and stays specific to the server during a restore.

`sudo ./remove.sh` removes the packages added by the project and restores the nginx, SELinux and
firewalld settings it added. Packages already present stay installed. The data and backups
are kept, unless a purge of the data is requested. Changes made by hand to
`nginx.conf` after installation are kept during uninstallation.

Validation: `tests/vm-rocky-server-e2e.sh rocky-8 rocky-9 rocky-10`, also run by the CI. The official VMs
are checked by fingerprint; the tests cover active SELinux, firewalld, APT/RPM import, DNF installation
of a local package, APT cache, refusal of a client outside the ACL, rerun, backup/restore and purge.


## 16. ISO appliance (Alpine Linux, BusyBox)

The x86_64 ISO installs Alpine 3.24 and loco in a single procedure, with a
visual wizard or an answer file. Disk, DHCP or fixed address,
keyboard, root/admin passwords, allowed networks and features are chosen
before the installation. The disk is erased after confirmation. The packages
are embedded: the installation works without Internet.

After ejecting the ISO and restarting, the console shows the portal and
the CA fingerprint. `setup-loco` changes the network, the name, the passwords
and SSH access. In the portal, **Administration → Configuration** and
**Updates** have the CLI equivalents `loco-config` and `loco-update`.
The operations are validated again by a separate root agent; the portal stays
unprivileged. Failed loco installations and settings have an
automatic rollback of the configuration and the database. System updates
follow `apk upgrade` in the installed Alpine branch.

The APT cache goes through nginx; OpenRC and supervisord replace systemd.
This cache (appliance and Windows server) reaches the repositories over HTTP or HTTPS: the repositories known to answer
over HTTPS (Debian, Ubuntu, Launchpad PPAs, Zorin, Raspberry Pi) are reached that way first, the others over HTTP, and
the other way is tried when the first one does not answer — a network that only lets HTTPS out is
therefore fine. An index (`InRelease`, `Packages`) is never served again stale when the repository no longer answers: the device
gets a clean failure (504), keeps its lists and updates the rest. A repository outside this list, reached over HTTP
behind a firewall that swallows HTTP without answering, costs five seconds per file before the fallback: then open
HTTP to that repository.
Minimum: 2 GB of RAM, 8 GiB disk, BIOS or UEFI without Secure Boot.
The details, the answer file, the build and the BIOS/UEFI tests
are in the [appliance guide](../linux/iso/README.md).

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
