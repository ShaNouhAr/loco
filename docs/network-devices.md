**English** · [Français](fr/network-devices.md) · [Español](es/network-devices.md)

# Network devices: switches and firewalls

> **Experimental feature**, off by default: an administrator turns it on in *Administration ›
> Features* (*Experimental* section). It then appears in *Systems › Network › Network devices*.

loco manages your **Cisco and Extreme switches** and your **Stormshield firewalls** without an agent, with an account
on the device, as an administrator would:

| Family | Link | Reading and configuration | Firmware |
|---|---|---|---|
| Cisco IOS / IOS-XE (Catalyst, routers) | SSH | command line | scp copy, MD5 checked, `boot system` — or `install add … activate commit` in INSTALL mode |
| Cisco Small Business (CBS, SG) | SSH | command line | flagged; installation from the web interface (TFTP only on the command line) |
| Extreme EXOS / Switch Engine | SSH | command line | scp copy, `install image` on the backup partition |
| Extreme VOSS / Fabric Engine | SSH | command line | SFTP copy into `/intflash`, `software add` / `activate`, `software commit` after the restart |
| Stormshield SNS | HTTPS API (SSH for the terminal) | API (`SYSTEM PROPERTY`, `CONFIG BACKUP`) | `SYSTEM UPDATE UPLOAD` then `ACTIVATE` |

## What the device needs

- **Cisco IOS / IOS-XE**: SSH enabled (`ip ssh version 2`) and a level 15 account — or a level 1 account with
  the *enable* password saved in loco. For firmware: `ip scp server enable`.
- **Cisco Small Business**: SSH enabled (`ip ssh server`) and a level 15 account. Whether the switch asks for the account
  over SSH or itself once connected (`ip ssh password-auth` absent), loco answers.
- **Extreme EXOS**: SSH enabled (`enable ssh2`) and an administration account.
- **Extreme VOSS**: SSH enabled (`boot config flags sshd`) and an `rwa` account; SFTP for firmware.
- **Stormshield SNS**: an administration account **without a second factor** (TOTP), and access to the administration
  interface from loco. SSH is optional: it is only used for the terminal.

Old devices that only speak pre-2015 SSH algorithms (`ssh-rsa` host key, Diffie-Hellman
group 14 with SHA-1, CBC encryption) are reached: loco adds them as a last choice, when its SSH client still knows
them.

## Linking a device

*Add a device*: name, family, address, SSH port (and HTTPS port for Stormshield), account and password.
The password is **encrypted** in loco's database; it is never shown again, and it is handed to `ssh` through
`SSH_ASKPASS` — never in a command's arguments nor in clear on disk. loco then shows the SSH **host key
fingerprint** (and the firewall's **certificate** fingerprint): compare it, then confirm. Nothing else is
exchanged before, and loco then refuses any device that would present another key. From a computer that has
already connected to it, `ssh-keygen -lF ADDRESS` shows the fingerprint it knows.

## What My fleet and the device page show

Model, serial number, version (and boot image), active ports out of the total, processor, memory,
temperature and uptime — read every fifteen minutes, and when the page opens. A command the
device does not know simply leaves its cell empty. Unreachable, failed link, failed backup or a
newer firmware available show in My fleet and in *To handle* on the overview.

## Configuration backup

Every day by default (every six hours, every week, or on demand only), loco reads the configuration
(`show running-config`, `show configuration` for EXOS, the full `.na` backup for Stormshield) and keeps it
**only if it has changed**: lines that move on their own ("Last configuration change at…", VOSS
timestamp) do not count. The page shows the history, the number of lines added and removed, the **differences**
between two versions, and lets you download each one. Retention (10 to 365 versions) is set per device.

Configurations often contain the device's secrets: they are **encrypted** in loco's state
(`/var/lib/loco/network/configs`), and therefore included in loco's own backup, and each viewing or
download is recorded in the audit log. The *Back up and read configurations* right is separate from the one for
viewing the devices.

## Firmware

1. **Upload** the file downloaded from the manufacturer into the *Firmware library* (Network devices
   page): loco reads its version from the name (you can correct it) and shows its SHA-256 fingerprint — compare it with the one
   published by the manufacturer. *Models concerned* restricts the file to some models (`C9300-*, X440G2*`).
2. loco **flags** the devices that run an older version. For Cisco, only those of the **same
   platform** as the image (a Catalyst 9300 image is never offered to a 2960X); EXOS, VOSS and Stormshield
   refuse an image of another platform by themselves.
3. *Install the firmware*: the configuration is backed up just before, then the image is sent and activated
   (see the table at the top). With *Restart afterwards*, loco follows the device's restart and return and
   checks the new version. Catalysts in INSTALL mode and Stormshield firewalls restart by themselves on
   activation. On VOSS, loco validates the version (`software commit`) as soon as it has reached the switch again: without this, the switch
   would roll back to the old one on its own.

First check in the manufacturer's release notes that the version suits the model and that the update
is possible from the one in service: loco knows nothing about it.

## Terminal and restart

*Terminal* opens the device's command line in the browser (session held by the loco server and
recorded in the audit log), with the saved account; privileged mode (`enable`) is typed in the terminal. *Restart*
restarts the device and follows its return: the traffic going through it is cut during the restart, and
unsaved configuration changes are lost — loco saves nothing on your behalf.

## Rights

*View network devices*, *Back up and read configurations*, *Install a firmware*, *Restart
a network device*, *Open a terminal on a device*, *Link network devices* (add, remove,
account and password, fingerprints): to tick in the access profiles.

## What has been verified

Each family was tried with loco's real SSH client against **simulators** faithful to the documented outputs
of these systems (prompt, paging, `enable`, credentials asked by the device, confirmation
questions, restart), including an old IOS limited to `ssh-rsa` and Diffie-Hellman group 14 with SHA-1, and
against a Stormshield API simulated from the official client. **Not yet on real devices**: the readings
(ports, processor, temperature) depend on the model and the version, and some cells may stay empty.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
