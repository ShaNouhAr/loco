**English** · [Français](fr/proxmox.md) · [Español](es/proxmox.md)

# Proxmox VE: system, updates, terminal

A Proxmox server linked to loco for its [backups](backups.md#proxmox-ve) can also be linked for its
**system**: it then appears in **My fleet**, like a Home Assistant, with its updates, its resources
and an *Update* button, and it has its own page (the server's name in My fleet, or *System and updates*
on its card in the Backups page).

## Why an SSH link on top of the token

The Proxmox API can tell which updates are waiting, but **cannot install them**: the *Upgrade* button of its
interface opens a shell reserved for a signed-in `root@pam`, refused to any API token. loco therefore comes in over **SSH**,
with a key of its own — the same approach as for Home Assistant. The token remains the one used for backups;
it also provides the server's resources (processor, memory, system disk) shown in My fleet.

## Linking the system

On the server's page, *Also manage the system of this Proxmox* (the **Proxmox VE** tab of **My fleet › Enrollment**
leads there, *Link over SSH*, for each server linked by its token but not yet over SSH):

1. **Authorize loco's key**: open the node's shell (Proxmox interface › the node › *Shell*) and run the
   suggested command, which adds loco's public key to `/root/.ssh/authorized_keys`. The private key never leaves
   the loco server; it is separate from the one used for Home Assistant.
2. **Read the host key**: usually the same address as the API, port 22 and `root`. loco shows the fingerprint
   of the Proxmox host key; compare it with the one given, in the node's shell, by
   `for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done`, then confirm. Nothing is exchanged before.

*Remove the SSH link* (*More* menu) stops the system management without touching the backups; also remove
loco's key from `authorized_keys`.

## What the page shows

- the Proxmox VE version, the node, the running **kernel** — and *reboot required* when a newer kernel
  is already installed;
- the available **updates**, security ones first, the new packages and those an update
  would remove, according to a simulation of `apt-get dist-upgrade` (nothing is changed to read it);
- the machines, the processor, the memory, the system disk and the uptime;
- the **activity**: each task with its log, live during an update.

If loco itself runs on this Proxmox, the page says so (it recognizes itself by its MAC addresses in the configuration
of the machines).

## Updating

*Update* reads the repositories again then installs everything with `apt-get dist-upgrade` — what the *Upgrade* button
of Proxmox does; never `apt-get upgrade`, which Proxmox advises against. Configuration files modified on the
server are kept. The update runs **on the server itself** (a `systemd-run` unit, log in
`/var/lib/loco-upgrade/`): it goes to the end even if the link drops or loco restarts, and loco picks up its
tracking again. Virtual machines keep running; the Proxmox web interface may reload.

Tick *Restart the server afterwards* to chain a restart — only if the update succeeded.
loco refuses to start an update that would remove the `proxmox-ve` package (badly set repositories).

*Check for updates* only reads the repositories again (`apt-get update`); Proxmox also does it every day.

In My fleet, the row's button, the selection, *Update all* and the whole-fleet schedule start
the same update. A Proxmox **also restarts from My fleet** — selection, *Update then restart if
needed* (only if the update installs a kernel to load), *Restart* button on its row when a
restart is required —, like a device, with one difference: loco first waits for the updates in
progress on the fleet's devices to finish, one hour at most, so as not to cut any installation inside a virtual machine. The
task says what it is waiting for ("Waiting for the fleet's updates to finish: 2 devices in progress."). The
*Restart* button on its page, however, restarts at once.

## Restarting

*Restart* stops all the server's virtual machines and containers for the duration of the restart; those
set to start with it come back afterwards. loco waits for the server to answer again, restarted (another
boot identifier). If loco runs on this Proxmox, it also stops for a few minutes and resumes tracking when
it comes back.

## Terminal

*Terminal* opens a **root** shell on the node, held by loco like the Home Assistant one (session recorded in the audit log).

## Rights

*View Proxmox*, *Update Proxmox* (check and install), *Restart Proxmox*, *Open a Proxmox
terminal*, *Link Proxmox servers* (also the SSH link). No shipped profile grants them, except
Administrator.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
