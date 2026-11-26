**English** · [Français](fr/backups.md) · [Español](es/backups.md)

# Backups

The **Administration › Server › Backups** page brings together what is backed up — loco itself, your Home
Assistant instances, your Proxmox machines —, where, and when. It has one tab per source, and one for the **external
destination**: the NAS, the S3 storage or the SFTP server where loco drops the copies.

Home Assistant and Proxmox VE are **optional** features, off by default: turn them on in
**Administration › Features**, *Optional* section.

## External destination

A storage outside the loco server, the only copy that survives if the server is lost:

- **Folder / mount point** — a 2nd disk, or an SMB/NFS share of your NAS mounted by the system; the
  `loco` account must be able to write there;
- **S3 / Cloud** — any S3-compatible storage (AWS, MinIO, Backblaze B2, Wasabi…);
- **SFTP** — with an SSH key.

With a **passphrase**, everything that goes out is encrypted with AES-256 (`openssl enc -aes-256-cbc -pbkdf2`);
keep it, it is needed to restore. *Test* drops then deletes a small file.

You choose what goes there: loco's backups (*Also copy loco's backups*, ticked by default),
and each Home Assistant separately (see below). Proxmox machines do not go there: Proxmox writes
its backups itself to one of its storages — for them to go to the same NAS, declare it in
Proxmox (*Declare a NAS…*).

On the destination, names never mix: `loco-<server>-<date>.tar.gz[.enc]` for loco,
`homeassistant-<no.>-<date>-<backup>.tar[.enc]` for Home Assistant. The retention of one never touches
the files of the other.

## loco

The server's daily backups (configuration, accounts, keys, repositories and catalog, excluding caches): the
latest one, the state of their external copy, and for each one *Download*, *Restore* (the server restarts in
the state of the backup) and deletion. You also set how many are kept — it also applies to the destination —
and restore from a file. This tab replaces the former Backups tab of the System page.

## Home Assistant

For each linked instance (see [Home Assistant](homeassistant.md)):

- **Copy now** copies its most recent backup — those kept in Home Assistant itself, not on
  a Home Assistant network storage. Tick *create a full backup first* to make one just
  before;
- **Schedule…** does the same once, every day, every week or every month, at the loco server's
  time;
- **Copies kept** (3 by default): beyond that, the oldest ones of this instance are deleted from the
  destination at the next copy.

The backup is downloaded over the SSH link, from the Supervisor API (the add-on's token goes through
`curl`'s input, never as a command argument; without `curl`, the archive is found in `/backup`).
It is checked down to the size, encrypted if a passphrase is given, dropped, then deleted from loco's
disk. A backup already copied does not go out again. A backup encrypted by Home Assistant (emergency key)
is copied as is: its key remains necessary to restore it.

Right: *Back up Home Assistant*.

## Proxmox VE

### Linking a server

In **My fleet › Enrollment**, **Proxmox VE** tab: loco goes through the Proxmox API with a **token** created for
it. In a node's shell:

```sh
pveum role add LocoBackup --privs "VM.Audit VM.Backup Datastore.Audit Datastore.AllocateSpace Sys.Audit"
pveum user add loco@pve --comment loco
pveum acl modify / --users loco@pve --roles LocoBackup
pveum user token add loco@pve loco --privsep 0
```

The last command shows the token's secret, only once. In loco: address (`192.168.1.10`, with
`:port` if it is not 8006), identifier `loco@pve!loco`, secret. The secret is kept encrypted and is never
shown again.

loco then reads the Proxmox **certificate** and shows its SHA-256 fingerprint: compare it with the one of
Proxmox (the node › System › Certificates, or the suggested `openssl` command) before confirming. Nothing is
sent before, and the token then only goes to that certificate: if it changes (renewed), everything stops
until **More › Read the certificate again…** and a new confirmation.

The token is missing a right? The page says so.

### Backing up

The page lists the virtual machines and the containers, with their last backup. Tick, then choose:

- the **Proxmox storage** — those that accept backups (*VZDump backup file*): local disk, NAS
  declared in Proxmox, Proxmox Backup Server;
- the **mode** — *Snapshot, no stop* (default), *Suspend* or *Stop then restart*.

Then **Back up now**, or **Schedule…**: once, every day, every week or every month; the
ticked machines, or *all, including future ones* (excluding templates); and the number of **backups kept per
machine**.

Each machine is a Proxmox task (`vzdump`, zstd compression), started one after the other and followed until
it ends, with its log; *Cancel* stops it on Proxmox — an unfinished file may then remain on the storage
(to delete in Proxmox), and the machine may stay locked — and off in *Stop* mode (`pct unlock` or
`qm unlock`, then restart it). If loco restarts
meanwhile, the task goes on on Proxmox and loco picks up its tracking.

A schedule's **retention** only deletes its own backups, recognized by their note
(`loco plan <no.> <machine>`): never those made otherwise (by hand, by a Proxmox task) nor the
protected backups. loco applies it itself, without asking Proxmox for the broader right that
`prune-backups` requires.

### Declaring a NAS

**Declare a NAS…** adds an **NFS** share (server, exported folder) or an **SMB** share (server, share,
user, password, domain) as a Proxmox storage reserved for backups. The password is handed
to Proxmox, which keeps it; loco does not keep it. The token also needs the `Datastore.Allocate` right on
`/storage`.

### Rights

*View Proxmox*, *Back up Proxmox machines* (start, schedule, cancel), *Link Proxmox
servers* (token, certificate, NAS).

### Updates, restart and terminal

Also linked over SSH, the server appears in **My fleet**: see [Proxmox VE: system, updates,
terminal](proxmox.md).

## Schedules

At the loco server's time. A missed run (server off) is not caught up: the next one happens
normally. A schedule can be paused, resumed or deleted; backups already made stay where
they are. On a two-server installation, only the primary server starts them.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
