**English** · [Français](README.fr.md) · [Español](README.es.md)

# loco appliance: Alpine Linux and BusyBox

The x86_64 ISO installs Alpine 3.24 and loco on disk. It boots in BIOS or
UEFI (Secure Boot disabled). The packages and the sources are on the media:
the installation works without Internet access. Minimum: 2 GB of RAM and an
8 GiB disk; plan for more for the repositories and caches.

## Installation with the wizard

Download `loco-VERSION-x86_64.iso` and `SHA256SUMS` from the
[releases](https://github.com/ShaNouhAr/loco/releases), then check the fingerprint:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

1. Boot the machine or the VM on the ISO. The visual wizard opens on
   console 7 (Alt+F7). `loco-installer` starts it again from Alt+F1.
2. Choose the keyboard, the disk (**entirely erased**), the name, the network card,
   DHCP or a static address, the time zone and the root and admin passwords.
3. Choose the allowed networks and the features: client HTTP,
   Windows Update, RPM, Flatpak. SSH is disabled by default; when enabling it,
   choose **Root password** (no key needed) or **Public key only**.
4. Check the summary, install, remove the media and restart.

The first boot finishes loco's configuration, then the console shows
the portal's address and the SHA-256 fingerprint of its HTTPS authority. Compare this
fingerprint before handing out the CA to the devices. The passwords entered are not
shown. The temporary arguments containing the admin password are
deleted after a successful installation.

The wizard is a text interface with windows, usable with the keyboard on the
local console or a VM's console. Day-to-day administration has a
graphical web interface and CLI commands.

In the wizard, **Esc** or **Home** goes back to the start menu without starting
the installation. The root console only opens with **Troubleshooting console**;
typing `exit` there brings you back to the wizard. **Alt+F1** opens an independent
login console, **Alt+F7** goes back to the wizard.

## Automatic installation and CLI

Copy [answers.example](answers.example), fill in the two passwords
and adapt the settings. One value per line, single or double quotes
optional: the file is never run as shell.

- Put the `loco-answers.conf` file at the root of a volume named `LOCO`,
  then boot on the ISO. A ten-second pause allows cancelling.
- Or embed the answers at build time with `--answers`.
- Or, from the live console:

```sh
loco-installer --answers /path/answers.conf --yes
```

`DISK=auto` requires exactly one fixed disk of at least 8 GiB. With several
disks, give the name (for example `DISK=vda`). The boot media's disk
and the answers' disk are excluded. An invalid answer opens the wizard;
with `--yes`, it causes a failure before anything is erased.

Keep the answers in mode 600 and remove their media after installation.
An ISO built with answers contains their passwords: it must
stay private. The public ISO embeds neither answers nor an SSH key.

`SSH_MODE=password` enables `ssh root@server-address` sign-in with
`ROOT_PASSWORD`, without `SSH_KEY`. `SSH_MODE=key` requires a key in `SSH_KEY` and
refuses SSH passwords. `SSH_MODE=off` disables SSH. Without `SSH_MODE`, the
older files keep their behavior: key filled in = SSH by key,
otherwise SSH disabled. After installation, `setup-loco` → **SSH access** changes
the mode; the password stays the root account's.

## Configuration and updates

In the portal, **Administration → Configuration** sets the access, the outbound
proxy, the features, the DNS of the Windows Update cache, the size of the caches,
the uploads and the backup retention. The portal's networks must
include the browser's address to avoid losing access.

**Administration → Updates** shows the available versions, the Alpine
packages, whether a restart is needed and the operations' log. The agent checks
the versions every day. The portal runs as `loco`; a separate OpenRC
service, `loco-agent`, validates the requests again before applying them as root.

The same operations are available on the root console:

```sh
setup-loco                       # wizard for network, name, passwords and SSH
loco-config show
loco-config set 'ALLOW=192.168.43.0/24' WU_CACHE_SIZE=50g BACKUP_KEEP=5
loco-update status
loco-update check
loco-update loco                  # latest stable release, archive checked by SHA-256
loco-update system                # apk upgrade, in the installed Alpine branch
loco-update all
loco-update reboot
loco-backup create
```

Changing the address or the name is done with `setup-loco` on the console: the
network connection may be cut and the devices must use the new
address. The certificate and the links are regenerated, keeping the authority.

If a loco installation or a setting fails, the agent restores the
configuration and the database, then starts the previous sources again. The sources of
both versions are kept under `/usr/local/src/`. This rollback covers
the application; `apk upgrade` follows Alpine's transactions and does not automatically
restore the old system packages. Moving to another Alpine branch
remains an administration operation to prepare with a backup.

Only one privileged operation can run at a time (portal and CLI).
Updates require access to the official Alpine repositories and to GitHub,
directly or through the configured proxy.

Logs: `/var/log/loco/`, `/var/lib/loco-agent/jobs/` and, at first boot,
`/var/log/loco-firstboot.log`. If it fails, it is resumed at the next
restart; its arguments file stays reserved to root.

## Building the ISO

On Alpine 3.24, install the build tools, then use a non-root account
that is a member of the `abuild` group, allowed to install its APK public key:

```sh
# as root:
apk add git xorriso squashfs-tools fakeroot abuild alpine-base mkinitfs \
    syslinux grub-bios grub-efi mtools dosfstools python3
addgroup my-account abuild
# sign in again as my-account, then:
sh linux/iso/build.sh --outdir out
# options: --answers private-file --aports aports-clone --source-archive loco-VERSION.tar.gz
```

The build uses the official `mkimage` scripts of the `3.24-stable` branch
of aports and the official v3.24 repositories. A modloop signing key is
created by `abuild-keygen` if `PACKAGER_PRIVKEY` is not provided; this key
is never embedded in the ISO. Verification of the distributed media relies
on the release's fingerprints downloaded over HTTPS. Secure Boot is not
supported.

The embedded sources only come from the files tracked by Git
(add new files with `git add` before building). Untracked local
files, private answers in particular, are excluded from the source
archive. From an extraction without Git, provide `--source-archive` with
the matching official archive.

The output is `out/loco-v3.24-x86_64.iso` and its `.sha256`; publication
renames the image with loco's version. The complete runtime is listed in
[packages](packages), also used by the profile and the overlay. The APT
cache uses nginx, as on Windows; OpenRC starts supervisord, the portal,
nginx, the periodic tasks and the administration agent.

## Validation and publication

```sh
tests/vm-alpine-server-e2e.sh       # native Alpine server: packages/signatures/cache, agent, rollback, removal
tests/vm-alpine-iso-build.sh        # builds the ISO, installs in BIOS then UEFI
tests/vm-alpine-iso-e2e.sh out/loco-v3.24-x86_64.iso
```

Host tools: QEMU, `genisoimage`, `xorriso`, OVMF, SSH, Python and curl. KVM is
used if available, otherwise QEMU uses TCG. The disks are disposable.
The ISO test ejects the media, boots on the disk, checks the portal with its
CA, the commands, the backups and a second boot.

The **Release** workflow, triggered with an existing tag, requires a successful CI
for that commit. It takes exactly the ISO tested in the `loco-iso` artifact,
creates the source archive prefixed `loco-VERSION/`, computes `SHA256SUMS` and publishes
the three files. The ISO is not rebuilt at publication.

---

[← Back to the overview](../../README.md) · [Linux server guide](../../docs/linux.md)
