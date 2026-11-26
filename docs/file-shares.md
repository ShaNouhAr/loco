**English** · [Français](fr/file-shares.md) · [Español](es/file-shares.md)

# File shares (Samba)

> **Optional feature**, off by default: an administrator turns it on in *Administration ›
> Features* (*Optional* section). It then appears in *Systems › Files › File shares*.

The loco server shares folders on the network, over **SMB**, like a small NAS: Windows, macOS and
Linux devices open them as `\\server\share` (or `smb://server/share`). Each share lives on a **partition already
in use** on the server, has a **size limit**, and only opens to the chosen Samba users — loco accounts or
Samba-only users — read-only or read-write.

Possible on the appliance (ISO) and on a Debian or Ubuntu server installed with `install.sh`: it is the server's
administration agent (`loco-agent`, root) that installs and configures Samba, the portal itself has no root
right. Not in the Docker image, nor on a Windows server, nor (for now) on a Rocky / RHEL server.

## Installing Samba

The page offers **Install Samba**: loco installs it from the system's repositories (`samba-server` and
`samba-common-tools` on the appliance, `samba` on Debian and Ubuntu), then writes its own configuration — the
original configuration is kept in `/etc/samba/smb.conf.avant-loco`. If Samba was already installed and configured by
hand, loco touches nothing until an administrator has clicked *Let loco manage Samba* (same copy kept).

loco's settings:

- **accounts only**: no guest or anonymous access (you cannot even list the shares without an account);
- **SMB 2 and newer** only, on port **445**; NetBIOS (port 139, `nmbd`) stopped;
- **loco's device networks only** (*Administration › Configuration*, fleet devices);
- no printers, no home folders.

**Firewall**: Samba only needs port **445/TCP** (NetBIOS, ports 137 to 139, is off). loco opens it itself
on the server's active firewall — ufw (Debian, Ubuntu, the appliance) or firewalld —, for the device networks, as long
as the shares are served, and closes it again when they are turned off; its rules carry the `loco-samba` mark, the
administrator's are never touched, and a firewall turned on later gets the rule within ten minutes. The page
says where things stand (*Server firewall*). A firewall **between** the devices and the server must, for its part, let
445/TCP through (*System › Network* lists it).

**Addresses**: the page gives, to copy, the server's address and that of each share for each system —
`\\server\share` for Windows (Explorer, *Map network drive*), `smb://server/share` for Linux
(Files, Dolphin…) and macOS (Finder › *Go* › *Connect to Server*).

## Partitions and charts

loco offers the server's partitions **mounted read-write**, with Unix permissions (ext4, XFS, Btrfs, F2FS…), outside the
system folders; it neither formats nor mounts any. For each one, a bar shows what fills it: system
and other data, shares' files, space reserved for the shares (their size limits not yet used) and
free space. Shares go to `/srv/partages` on the root partition, to `<mount point>/partages` on the
others.

To add a partition (a second disk of the VM, for example), format and mount it yourself on the
server, then reopen the page.

## Creating a share

*Add a share*: a name (`Documents`, `Accounting`…), a partition chosen with a click, a **size limit** —
with the slider, which draws the future share on the partition's bar — and the users who access it. No
path to type: loco creates the folder from the name (`/srv/partages/accounting`, or `accounting-2` if it is already
taken) and never takes over an existing folder containing files that do not come from it.

Windows devices see the share as **a disk the size of the limit**, with the space it has left.
Usage is read every two minutes: **beyond the limit, the share becomes read-only** for
everyone, an alert appears in *To handle* on the overview, and writing comes back by itself as soon as some space
has been freed. Between two readings, a large upload may slightly exceed the limit. Devices already connected become
read-only too (see [Active connections](#active-connections)).

Removing a share removes it from the network; **its files stay on the server**, in its folder.

## Samba users

Everything is set on the shares page, *Users* tab: nothing in *My account*. A Samba user is:

- **a loco account** chosen from the list: it signs in with its loco username, and it is disabled or removed from
  Samba at the same time as its loco account;
- or **a Samba-only user**, for someone without a loco account (a scanning station, a colleague, a
  printer): a username of its own for the shares, without any access to loco. It cannot take the username
  of a loco account.

In both cases, the administrator chooses its **Samba password** and its **rights, share by share** (no
access, read, or read and write) — from the user, or from each share. It can be **disabled**
(it keeps its password and rights to be enabled again) or **removed** from Samba; the linked loco account
is not touched. Disabled or removed, it loses its active connections at once.

The password is handed to Samba through `smbpasswd`'s standard input: it never goes through a command's
arguments, is written neither to loco's database nor to its audit log, and loco can no longer show it — give it to
the person through a safe channel. Samba stores it in a less protected way than a loco password (NT hash,
unsalted): choose one that is used nowhere else.

## Active connections

The *Connections* tab shows, read every fifteen seconds, one row per connection of a device: the user,
the shares it has opened, the device and the connection time.

**Cut** ends the connection: the Samba process that serves it — the one Samba itself designates, loco never
stops another — closes cleanly. A file being saved may remain incomplete. The device
reconnects by itself as long as the user keeps their rights: to prevent it, tick *Also disable* in the
confirmation window (it is enabled again in the *Users* tab). Each cut is recorded in the audit log.

Samba only applies its configuration to connections that open. loco therefore closes itself, at each change, what
is no longer allowed on the connections already open: those of a disabled or removed user, and the share
on the connections whose right has changed (removed, made read-only because the share is full, or given back) or
which is no longer served. The device that is still entitled to it reopens it by itself, with its new rights.

## Opening a share in the browser

*Open*, on a share's card, shows it in loco, with a complete file manager:

- **browse**: breadcrumb, previous and next folders, sorting by name, size or date, hidden files shown
  or not, folder filter and **search in subfolders** (case- and accent-insensitive), TreeSize-style
  disk usage, properties (size of a folder and its contents);
- **upload** files or **whole folders** (*Upload* button, or drag and drop from the
  computer, onto the list or onto a folder): the upload is done in chunks, resumes after a network cut,
  keeps the original modification date; several files go at once, each with its progress;
- **download** a file, or a folder and a whole selection as a **ZIP archive** built on the fly;
- **preview** photos, PDFs, sound and videos, with the folder's neighbors (← → arrows); **editor** for
  text files (Ctrl+S saves) — a file changed in the meantime by someone else is not overwritten;
- **new folder** or **text file**, **rename**, **copy**, **cut** and **paste** (within the share, or
  into another share opened next), move by dragging rows onto a folder (Ctrl: copy),
  **delete** — after confirmation, without a recycle bin;
- **selection** of several items (boxes, Shift or Ctrl + click, Ctrl+A) with a bar at the bottom of the screen
  (download, copy, cut, delete), **right click** and ⋮ button on each row, **keyboard shortcuts**
  (Enter, Del, F2, Ctrl+C / X / V, Backspace, Alt + ← / →, `/` to search; list through the keyboard button).

A name already taken at the destination is raised **before** any upload or paste: *Replace* (*Merge* for an
uploaded folder), *Keep both* ("name (2)"), *Skip*, once and for all if you want. The space used out of
the size limit is shown at the top: an upload that would exceed it is refused in advance, and **a full share can only
shrink** (delete, rename, move) until it is back under its limit.

**Who opens what.** The *Open shares in the browser* right is needed. With *Manage file
shares*, it opens **all** the shares read-write. Otherwise, it opens the shares of the **Samba user
linked to the loco account**, with its network rights: read-only, the file manager offers no upload,
creation, change or deletion; a disabled Samba user opens nothing anymore. An account that only has this
right sees, in *File shares*, the list of its shares (space, network address, *Open*) and nothing of
their management. Uploads, creations, renames, copies, moves and deletions are recorded in the audit log.

**How it is done.** The portal itself never touches the shares' folders. A separate service, started by
the root agent under the account that owns the shares (`loco-share`, without any root right), performs each operation
in the share's folder: a path is followed there one folder after another, **never going through a symbolic
link** (links are shown, never followed). For an upload or a download, it hands the portal the
file already opened. Files created from loco get the same owner and permissions as those placed by Samba
(`0660`, folders `2770`). Names that a Windows device could not open (`\ / : * ? " < > |`, trailing dot or
space, `CON`, `NUL`…) are refused. An HTML page or an SVG image from a share is downloaded, never run
in the browser. Uploads in progress wait in the share's hidden `.loco-envois` folder, which does not appear
in loco; those interrupted by a restart are deleted from it after one day.

## Rights

*View file shares* and *Manage file shares* (install Samba, create and edit the
shares, add Samba users, choose their password and their rights, cut a connection), and *Open
shares in the browser* (see above): to tick in the access profiles. The Administrator profile has all
three.

## Backup, high availability, turning off

- The description of the shares, the users and their rights is in loco's database, therefore in its backup.
  **The shares' files are not** (back them up separately), nor the Samba passwords: after a
  restore on another server, the administrator chooses them again (the page flags them as "not yet set
  in Samba").
- In high availability, shares are not replicated to the second server.
- Turning the feature off stops Samba; the files stay in place and everything starts again when it is turned back on.

## What has been verified

With the real Samba of Alpine 3.24 (the appliance's) and of Debian 13, driven by loco's real code: read and
read-write rights, refusal of wrong passwords and of guest access, switch to read-only once full
then back, users disabled then enabled again, size limit seen by a real Windows 11 device (SMB 3.1.1, signed).
On connections already open (Samba 4.23, `smbclient` clients): cutting only the targeted connection on demand,
and immediate loss of access for a disabled user, a removed right, a switch to read-only
(full share, then back to writing) and a removed share.
Installation through the page, on a Debian 13 loco; not yet on the appliance itself, nor with a macOS device.

The file manager, on a Debian 13 loco (Samba 4.22 installed through the page, portal confined by systemd) in
Chromium: upload of a 20 MB file in chunks then identical download (same fingerprint), requested chunk
(video playback), ZIP archive, preview of a photo and a PDF, new folder, copy, rename, editor,
search in subfolders, deletion; service started under `loco-share`, restarted with the root agent; files
created with the same owner and permissions as by Samba, original date kept; read and added to by a
Samba user with `smbclient`. Rights (manager, reader, writer, no access), the size limit and the
full share, symbolic links and refused names are covered by the automated tests. Not yet on
the appliance, nor with Firefox or Safari.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
