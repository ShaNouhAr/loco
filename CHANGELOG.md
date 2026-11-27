**English** · [Français](CHANGELOG.fr.md) · [Español](CHANGELOG.es.md)

# Changelog

## 2.211.0 — 2026-11-28

- **Re-enroll a forgotten Windows device.** If a fresh installer provides an enrollment key and the server rejects the old token, the agent enrolls again with a new token.
- **MCP installers support internal certificate authorities.** When the portal uses an enterprise certificate, the installer offers the verified root for the portal’s name and shows its SHA-256 fingerprint before adding it.
- **Windows software updates retry when Windows Installer is busy.** The agent waits and retries WinGet updates after MSI error 1618. If the conflict persists, it reports the reason and leaves the software eligible for the next update.

## 2.210.0 — 2026-11-27

- **Simpler network access in the ISO installer.** Use the detected server subnet, private IPv4 networks, or a custom list. A custom web list can inherit the client networks, and IP addresses and CIDRs can be separated by spaces, commas or semicolons. The ISO guide explains how to recover from an HTTP 403 from the server console.

## 2.209.0 — 2026-11-27

- **Proxmox and Home Assistant in the machine group editor.** Hosts appear alongside managed computers, with their own icon, address, system and status. Search by address, filter to group members only, and see the total membership in the same table.
- **More detail when choosing Proxmox script targets.** The picker shows each server’s icon, address and version.
- **List Proxmox hosts from MCP.** The read-only `list_proxmox_hosts` tool reports addresses, versions and SSH link status. Enable it on a key whose account can view Proxmox and access the whole fleet.

## 2.208.0 — 2026-11-27

- **Run shell scripts on Proxmox VE.** From *My fleet › Scripts*, open a Linux shell script and choose *Run on Proxmox*. Select individual servers linked over SSH or machine groups. The run history shows each server’s output and exit code.
- **Group Proxmox and Home Assistant hosts.** Assign them to one or more machine groups from their own pages or the group editor and find them by group in *My fleet*.

## 2.207.0 — 2026-11-26

- **The documentation in English, French and Spanish.** The README, the guides in `docs/`, the security policy, the appliance guide and this changelog now exist in English — the default on GitHub — and in French and Spanish. A bar at the top of each page switches language in one click and leads to the same page in the other language. The screenshots, the banner and the animation of the live updates are taken from the portal in each language.
- **What's new, in your language.** *Administration › Updates* shows the changelog of the installed version and the new features of the available one in the account's language (English, French or Spanish), and falls back to English when a translation is missing.
- **Claude and Codex installers named in your language**: `add-loco-to-claude.cmd`, `ajouter-loco-a-claude.cmd`, `anadir-loco-a-claude.cmd`…
- **Fixed, in English and Spanish**: the *Alps* and *Boreal* appearance presets kept their French name; the title of the device and software groups pages mixed in a French word ("machines groups"); the actions of the audit log without a category showed "Autre"; My fleet's filters put a space before the colon ("Status : All"); and, in Spanish, the scripts' run counts read "ejecución(es)".

## 2.206.0 — 2026-11-26

- **Linking Proxmox and Home Assistant is done in My fleet › Enrollment.** Two tabs, *Proxmox VE* and *Home Assistant*, next to Windows, Linux and Android, give the linking steps and the fingerprint confirmation (Proxmox certificate, Home Assistant host key). For Proxmox, a last step lists the servers linked by their token but not yet by SSH (*Link through SSH*), which is what it takes to see them in My fleet. *Backups* and the *Home Assistant* page keep what is already linked and point to the enrollment to link a new one; renewing a certificate or a host key is still confirmed on the spot.
- **My fleet: a Proxmox server that needs a restart looks like a device.** With no update to install, it is *up to date* (green dot), with *restart needed* below, instead of "restart required" in place of the status; the overview counts it the same way.
- **Administration › Configuration, put back in order.** The page is organized by question: *Who can reach loco* (fleet devices, computers that open the portal, unencrypted HTTP), *Access to the Internet* (corporate proxy, DNS to reach Microsoft), *Caches served to your devices* — one row per cache, with its switch, its maximum size and the space it takes —, then *Uploads to the portal*. Each setting says what it is for, in French, English and Spanish (the labels had stayed in French). The number of backups kept, already adjustable in *Backups*, is no longer duplicated there; *See also* leads to the neighboring settings (Backups, Features, System). A single *Apply*, which restarts the services once.

## 2.205.0 — 2026-11-26

- **"MCP server" becomes "MCP and API", with keys.** The Administration › Access page is now called *MCP and API*: the same keys serve an LLM, through its MCP client (Claude Code, Codex, Cursor…), and your scripts or monitoring tools, through the REST API. Everywhere we now say *keys* rather than *tokens*: page, Claude and Codex installers, app authorization, *Create MCP and API keys* right, audit log, Features and documentation.
- **Each account sees its own keys.** The page first shows *Your keys*. Those of the other accounts, which an administrator can revoke without changing their tools, are in a separate table, with the account that holds them. An account that no longer has the right to create keys still sees and revokes its own: before, they kept working without it being able to see them.
- **Fixed: after a system update, the updates page stayed on the old state.** The page reloaded as soon as the installation finished, before loco had read again what was left to update: it still showed the packages just installed and no longer refreshed. The operation now only reports itself finished after that check — last step of the bar: *Checking what is left to update* —, and the page also reloads as soon as a check ends.

## 2.204.0 — 2026-11-26

- **My fleet: reserving a device.** On a device's page, *More › Reserve this device…* reserves it with a password: the other accounts — even with the Administrator profile — must enter it to open its terminal, its remote desktop (console included) and its files, to restart it (restart, update followed by a restart, update to a new version of the system), and on Android for its adb terminal, its screen and its unlocking. Once entered, it opens access for half an hour (*Close* closes it sooner); five failed attempts block for a quarter of an hour. Everything else stays open to them according to their rights: inventory, updates without restart, deployments, scripts, agent removal, forgetting the device. Grouped restarts in My fleet leave out a device reserved by another account, and grouped updates update it without restarting it (unless the password has already been entered); an update in waves or a schedule only restarts it if its author is the owner. The page shows *Reserved by …*, My fleet a padlock next to the name; only the owner changes the password or releases the device. Reserving or changing the password closes the sessions already opened by the others; reserving also cancels the restarts they had requested that had not yet gone to the device. The MCP tools and the API of the other accounts are refused on a reserved device (only a person can enter the password). Password kept hashed, never shown; if it is lost, the device is forgotten, then enrolled again. New *Reserve a device* right; everything is recorded in the audit log.
- **Fixed: the message of an action requested on a device.** In French it made every action feminine ("Redémarrage demandée", "Inventaire demandée"); it now says "Restart pending on …: starts at the next contact".

## 2.203.0 — 2026-11-25

- **File shares: opening a share in loco, with a real file manager.** *Open*, on a share's card, shows it in the browser: browse its folders, upload files or whole folders (button, or drag and drop from the computer), download a file, or a folder and a selection as a ZIP archive, preview photos, PDFs, sound and videos (with the folder's neighbors, ← →), editor for text files, new folder or file, rename, copy, cut and paste — within the share or to another one —, delete, properties (size of a folder and its content). Selection of several items (boxes, Shift and Ctrl + click, bottom bar), sorting by name, size or date, folder filter and search in subfolders (ignoring case and accents), right click, keyboard shortcuts (Del, F2, Ctrl+C/X/V, Ctrl+A, Enter, Backspace…), previous and next folders, TreeSize-style disk space. A name already taken is raised before any upload or paste: replace, keep both, skip, once and for all if you like. The original modification date of uploaded files is kept; a file modified in the meantime by someone else is not overwritten from the editor. The space used against the size limit is shown: an upload that would exceed it is refused in advance, and a full share can only shrink (delete, rename, move).
- **Who opens what: a new right, "Open shares in the browser".** With *Manage file shares*, it opens all shares in read and write; otherwise, those of the Samba user linked to the loco account, with its rights on the network (read, or read and write) — a disabled Samba user no longer opens anything. An account that only has this right sees, in *File shares*, the list of its shares (space, network address, *Open*) and nothing of their management. Uploads, creations, renames, copies, moves and deletions go to the audit log.
- **Safety of the shares' files.** The portal itself never touches the shares' folders: a separate service, started by the root agent under the account that owns the shares (without any root right), performs each operation in the share's folder — a path is followed there one folder at a time, never crossing a symbolic link — and hands the portal the file already opened for an upload or a download. Files created from loco have the same owner and permissions as those placed by Samba. Names that a Windows device could not open (`\ / : * ? " < > |`, trailing dot or space, `CON`, `NUL`…) are refused. An HTML page or an SVG image from a share is downloaded, never run in the browser.
- **A single file manager, for devices too.** A device's *Files* tab gets the same one: multiple selection, sorting, filter, preview of photos, PDFs, sound and video (read from the device), right click, shortcuts, drag and drop of whole folders, moving by dragging onto a folder, name conflicts asked before uploading. The agents do not change.
- **The theme's colors apply to resources.** The processor, memory and disk gauges of *My fleet*, of a device's page (disks), of Home Assistant, Proxmox and network devices, and the processor curve of the monitoring, take the tones of the chosen accent color — softened toward the theme's grey, grey with the neutral accent; a warm accent (orange, amber, pink) is softened more, so that a normal gauge never looks like an alert. Orange (almost full) and red (full) stay those of the alerts; the memory curve keeps another hue.
- **Fixed: a Linux device's disk space.** The file manager's *Disk space: see what takes the most* button never appeared for Linux devices (only Windows).
- **Fixed: the live chart's gradations.** On a device's page, the 100%, 50% and 0% marks of the axis of the *Live* chart stayed at the top left (style refused by the portal's security policy).

## 2.202.1 — 2026-11-25

- **Windows agent: a power cut during its update no longer makes it disappear.** The installer removed the old version before laying down the new one: a PC switched off abruptly between the two was left without an agent — empty folder, service not found — and left the fleet until a manual reinstallation (it happened to a device on 25/11). The new version is now laid down in full before the old one is removed. Checked on Windows 11 by cutting the machine at four moments of the update: the agent comes back each time, with the old or the new version, and the next update goes through. The old order, cut at the same point, did leave the device without an agent.
- **Windows agent: its settings files withstand power cuts.** They are written all the way to disk before replacing the previous ones (the device's identity depends on them), and the update's log is written line by line, readable even after a cut.
- **Windows agent: old installers no longer pile up.** Each update kept a copy, dozens over the versions: only the one of the running version stays.

## 2.202.0 — 2026-11-25

- **My fleet: Home Assistant and Proxmox behave like the other devices.** *Update everything* and the whole-fleet schedule update them too (Home Assistant with a backup first), and the button's number counts them. Their rows follow the filters (*Status*, *System* — with *Home Assistant* and *Proxmox VE* —, *Situation*), the search and the live refresh; they count in the page's numbers and in the overview ring, their tasks show in the *Updates* timeline and in the navigation badge, and Ctrl K finds them. Home Assistant shows its host's resources (disk; memory and processor on Home Assistant OS) and can be ticked with the rights of its own buttons (update, restart) instead of "manage". They belong to no group: *Update this group* does not touch them.
- **Proxmox restarts from My fleet, after the fleet's updates.** With a selection, *Update, then restart if needed* (only if the update installs a kernel to load) or the new *Restart* button on its row when it is required, like a device — except that loco first waits for the updates in progress on the fleet's devices to finish, an hour at most: the hypervisor stops all its machines with it, loco included, and must not cut any installation. The task says what it is waiting for.
- **SSO: the profile photo comes from the provider.** When signing in with OpenID Connect (Authentik…), loco takes the account's photo from the provider (`picture` claim): read again if it changes, at most once a day, removed if the provider no longer gives one (initials avatar). An image chosen in *My account* takes precedence, and *Use my … account photo again* goes back to the provider's. The image is only requested from the provider itself (same trust as the sign-in) or from a public address (Gravatar) — never from an internal address or through a redirect; PNG, JPEG, GIF or WebP, 2 MB at most. Checked with a real Authentik 2026.8.6: image served by Authentik (pinned certificate), Gravatar, back to initials.
- **My account: changing the theme no longer removes the profile photo.** Saving the appearance (theme, color, background, logo) rewrote all its settings: the profile photo disappeared, along with the hidden notices ("don't show again").

## 2.201.1 — 2026-11-25

- **Android: closing the screen session really locks the tablet or phone.** Locking waited five seconds after the end of the session, and a session reopened in the meantime cancelled it: closing the session and reopening it right away to check left the device open. It now happens as soon as the last screen session ends; a session reopened afterwards finds the device locked (*Unlock*, in the session). Never while loco is still driving the screen through adb. The result is noted in Android's log (`loco` tag).

## 2.201.0 — 2026-11-25

- **File shares: the SMB port is opened automatically on the server's firewall.** Samba only needs port 445/TCP (NetBIOS is off): loco opens it itself on the server's active firewall — ufw (Debian, Ubuntu, the appliance) or firewalld — for the devices' networks, as long as the shares are served, and closes it again when they are turned off. Its rules carry its mark (`loco-samba`) and the administrator's are never touched; a firewall turned on afterwards gets the rule within ten minutes. The shares page says how things stand (*Server firewall*).
- **File shares: the address for each system.** The server and each share show, ready to copy, `\\server\share` for Windows and `smb://server/share` for Linux and macOS.
- **File shares: cutting a connection.** The *Connections* tab shows one row per device connection — user, open shares, device, since when — and *Disconnect* ends it: the Samba process serving it, the one Samba itself designates (loco never stops another), closes cleanly. The device reconnects by itself as long as the user keeps their rights; the *Also disable* box prevents it. The disconnection is recorded in the audit log.
- **File shares: what you remove also applies to connections already open.** Samba only applies its configuration to connections that open: a user disabled or removed, or whose right on a share was withdrawn, kept their connection open — and could still write to it — as long as their device stayed connected; a full share stayed writable for devices already connected, a removed share stayed open. Now, at each change, loco immediately closes what is no longer allowed: the connections of a disabled or removed user, and the share on the connections whose right changed (withdrawn, switched to read-only, or given back) — the device that is still entitled to it reopens it by itself, with its new rights. Checked with Alpine's real Samba 4.23.
- **File shares: buttons rather than "⋮" menus.** Edit, disable and remove are done straight from the share's card and the user's row, with no menu to open.
- **"⋮" menus of tables (file manager, groups, directories, Wazuh): never clipped or off screen again.** On the last row of a table, the menu was cut off by the table, which gained a scrollbar; on a phone, it went off the screen to the left. It now opens over the page, below its button or above it when there is no room, always whole, and follows the page as it scrolls.

## 2.200.0 — 2026-11-25

- **Android: always reachable, with the screen off and the device locked.** Android puts the processor to sleep when nothing holds it any more: the app's contacts stopped and loco lost the device after about an hour of sleep. Now, on mains power, the app keeps the processor awake (the screen stays off) and Wi-Fi out of its power saving: contacts, persistent channel and wireless debugging carry on as with the screen on. On battery, Android wakes it every five minutes, even in deep sleep, for a contact and to reopen the channel. The app's *Always reachable* setting keeps it awake on battery too, at the cost of higher consumption.
- **Android: battery optimization is lifted.** The app asks for it by itself, once per version, as long as it is not lifted — otherwise Android also cuts its network during sleep. On a device paired over adb, loco lifts it itself as soon as the app reports it back (sleep whitelist, background activity). The device's page shows *Sleep not lifted* as long as it is not.
- **MCP on Windows: `terminal_job` follows a running task.** The detached launcher of a background task keeps its output (`out.txt`) open for writing: reading, which asked for exclusive access, failed ("the process cannot access the file…") instead of returning a state, during the task and right after. Output, exit code and process number are now read in shared mode, and a file that is momentarily unreadable no longer cuts the follow-up: the task always keeps a state (running with its partial output, finished…). Checked on Windows 11.

## 2.199.0 — 2026-11-25

- **MCP: a command that exceeds its time limit is really stopped.** Until now, `terminal_run` gave control back at the time limit and sent a Ctrl+C, which some programs ignore (`cmd /c … .bat` then asks "Terminate batch job (Y/N)?"): the shell stayed busy and all the following commands timed out. Now, at the time limit, the shell and all the processes the command started are closed (Windows, Linux, Android), and the next call opens a fresh shell. A shell left busy is detected instead of making the next command time out: it is closed, the command goes to a fresh shell and the answer says so. `fresh` asks for a fresh shell; `reset` of `terminal_interact` stays the fallback of the interactive terminal.
- **MCP: background commands.** `terminal_run` with `background` starts a command detached from the terminal — it survives the session closing — and returns a task number; the new `terminal_job` tool gives its state, exit code and output, or stops it along with everything it started. For long builds and installations.
- **MCP: capped output, never an echo of the command.** `terminal_run`, `adb_shell` and `terminal_job` return at most `max_output` characters (24,000 by default, under the MCP clients' limit), keeping the beginning and the end. A long script no longer goes as a single terminal line: on Linux and in the adb shell, the echo and line editing of the shell reserved to the assistant are turned off and the script goes in short lines — an adb command loaded with 15 KB of base64 no longer comes back echoed in an answer of more than 60 KB.
- **MCP: files to and from an Android device, through adb.** New `push_file` and `pull_file` tools, like `adb push` / `adb pull`, with the rights of Android's *shell* user (`/data/local/tmp`…): the content goes in the request body (never in a command line) or goes straight from a fleet device to the device, and back — a `.dex` built on a Windows PC goes to the tablet directly —, then it is checked on the device (size and SHA-256). Where the loco app has no access, `write_file` and `read_file` go through adb by themselves.
- **MCP: `write_file` writes UTF-8 without BOM, everywhere, and says so.** `charset` (utf-8 by default, utf-8-bom, strict ascii, utf-16le-bom) and `newline` (lf, crlf) options; the tools' documentation recommends `write_file` rather than the terminal to drop code (PowerShell 5.1's `Set-Content -Encoding UTF8` adds a BOM that makes javac fail).
- **MCP on Windows: `exit 3` no longer closes the session.** The exit code comes back, the console stays open; a PowerShell error always gives code 1.
- **Existing MCP tokens**: those that had `terminal_run` get `terminal_job`, those that had `adb_shell` get `push_file` and `pull_file` (migration 61).
- **Android: closing the screen session locks the phone or tablet.** A few seconds after the end of the last screen session, the device locks as with the power button (Android 9 and later; not TVs). A session reopened right away finds it as it was.
- **Android: the app's terminal also stops what a command started** (Ctrl+C, time limit exceeded, end of session), not only its `sh`.

## 2.198.1 — 2026-11-24

- **Unlock: the pattern can be drawn diagonally.** On the portal's pad, each dot took up its whole cell: a diagonal stroke caught the neighboring dots on the way. The pad now behaves like Android's grid: a dot is only taken if the pointer passes near its center, a quick gesture skips no dot it crosses, the stroke follows the pointer, and a dot skipped on a straight line (1 → 3, 1 → 9…) is taken on the way, as Android does. Same pad in the screen session and in *Control the screen*.

## 2.198.0 — 2026-11-24

- **Android: "Unlock" moves into the screen session.** A toolbar button opens a pad to draw the pattern (or click its dots in order), or fields for the PIN and the password; the loco app wakes the device, brings up the code entry and enters the code there. The code is neither kept nor logged, and the app never types it anywhere other than the code entry: on a device already unlocked, it is simply discarded. Unlocking through adb leaves the device's page for the toolbar of *Control the screen*.
- **Remembering the unlock code, for yourself only.** A box keeps the pattern (or the PIN, the password) for the next sessions on this device: *Unlock* then unlocks in one click. The code is sealed on the server and tied to the account and the device; nobody else, administrator included, can use it, it is never shown, and the audit log only records that it was saved, used and forgotten (migration 60).
- **OnePlus, Oppo, Realme (OxygenOS, ColorOS): the pattern is recognized.** The grid of these skins has a different name from stock Android's: the app did not find it and the code typed had no effect. It now finds it, like those of the skins that name it in a similar way, and brings up the code entry through the official request to Android (the swipe up went to the lock screen's notifications). Checked on a OnePlus Pad 3 (OxygenOS 16).
- **Unlocking through adb: the grid is located on the screen.** Once the code entry is open, loco reads the exact position of the grid in the description of the screen Android gives, instead of deducing it from the brand: the pattern goes through the first time on a OnePlus tablet, in any orientation. The per-brand computation and the calibration remain for the devices that do not expose it.
- **OxygenOS / ColorOS: no more black screen after unlocking.** These skins hide the apps in the screen sharing granted once and for all by adb pairing; only the system bars stayed visible. The app detects it, stops that sharing and shows the screen through accessibility (about three frames per second; *Control the screen*, through adb, stays smooth).
- **adb: "failed to connect" fixed.** Another program of the loco account that borrowed loco's adb server could start it first, when the server booted, with another key: the paired devices then refused loco. loco detects that server and starts its own again with its key. A device that really no longer recognizes loco is described as such ("pair it again") instead of an adb message.

## 2.197.1 — 2026-11-24

- **Features: the confirmation says what changes.** When turning a feature on, the confirmation window announced that "users lose access to the disabled features", even when nothing was turned off. It now says what is turned on, what is turned off — and what goes with it, through dependencies (turning off *My fleet* also turns off remote access, scripts…) —, only mentions the network services if one of them changes, and no longer appears when nothing changes.

## 2.197.0 — 2026-11-24

- **File shares (optional): the loco server shares folders on the network, over SMB, like a NAS.** loco installs Samba from the system's repositories and configures it itself: accounts only (neither guest nor anonymous), SMB 2 and later on port 445, without NetBIOS or printers, loco's device networks only. Each share lives on a **partition already in service**, chosen in one click — loco neither formats nor mounts any —, in a folder it creates itself from the share's name: no path to type. Each partition is shown as a bar (system and other data, shares' files, reserved space, free space) and the size limit is chosen with a slider, drawn on that bar; each share has its usage ring. On the appliance, Debian and Ubuntu; to turn on in *Features*.
- **Size limit per share**: Windows devices see the share as a disk of that size; beyond it, it goes **read-only** for everyone, the alert appears in *Needs attention*, and writing comes back by itself as soon as some space has been freed.
- **Samba users, all set on the shares page**: a loco account chosen from the list (its identifier; disabled and removed with its account), or a Samba-only user, without access to loco (scanning station, colleague, printer). The administrator chooses its password and its rights share by share (read, or read and write), from the user or from the share; it can be disabled without losing its password. The passwords go to Samba through its standard input, never in the arguments, loco's database or the audit log.
- **Network devices: background readings and scheduled backups done again.** Since 2.193.0, the background loop stopped on a silent error: the devices were only read when their page was opened, and the scheduled configuration backups (every day by default) never happened.
- **Debian or Ubuntu server: purging and analyzing the caches from the portal work.** The portal, confined by systemd, could not drop these requests for the administration agent (the appliance was not affected).

## 2.196.0 — 2026-11-24

- **Faster overview, My fleet and System page.** On a fleet of 300 devices, the overview shows in 25 ms instead of 190 (33 database queries instead of 334), the server state queried by the live pages in 4 ms instead of 130, the *System* page in 15 ms instead of 93, and My fleet with its live refresh makes 18 queries instead of 318. The processor load is measured in the background (each display waited 120 ms to measure it); the portal's certificate, the list of time zones and the versions of the installed services are no longer read again on every page; the last result of each device is read in one go; the overview now only computes the devices' color, without the detail of their reasons. New index for failed updates (migration 58).
- **Style sheets and scripts compressed** by nginx on the appliance: the style sheet goes from 210 KB to 45 KB transferred, the portal's script from 122 to 35 KB — snappier pages over a slow link (VPN, phone).
- **Lighter code**: encryption of secrets, terminal sessions held by the server (Home Assistant, Proxmox, network devices), known SSH host keys, uploading packages to the Linux repositories and reading searches in the catalogs each grouped in a single place; dead code removed, along with 30 style classes and 209 translated texts that were no longer used.

## 2.195.0 — 2026-11-24

- **Enrollment: the DNS name, then the IP.** When loco has a DNS name — the portal address, or failing that the full name of its enterprise certificate —, the installation command (Linux and Windows), the Linux script, the .deb and .rpm packages, the preconfigured MSI and APK try that name first, then the server's IP. A device that does not recognize the name's certificate (enterprise authority missing from the device or from the agents' bundle) enrolls through the IP, with loco's certificate, instead of failing. The agent (Linux, Windows, Android) keeps the other address as a fallback. The *Enrollment* page shows the addresses and their order; the MSI's `SERVER` and the `Server` registry value (GPO) accept several comma-separated addresses.

## 2.194.0 — 2026-11-23

- **New font: Outfit**, instead of Bricolage Grotesque, in the whole portal and in the Android app. Round, geometric shapes, a clean design, and compact: the columns of My fleet and of the tables stay readable. Shipped with loco (no call to an outside site), SIL OFL license; IBM Plex Mono stays the font of addresses, versions and commands.

## 2.193.0 — 2026-11-23

- **Network devices (experimental): Cisco and Extreme switches, Stormshield firewalls.** loco links to your devices without an agent, with an account of the device: Cisco IOS / IOS-XE (Catalyst, routers), Cisco Small Business (CBS, SG), Extreme EXOS / Switch Engine, Extreme VOSS / Fabric Engine — over SSH and through the command line —, and Stormshield SNS through its HTTPS API (SSH for the terminal). The password is encrypted in the database and handed to ssh without going through its arguments; the SSH host key and the firewall's certificate are only accepted once their fingerprint is confirmed. Old devices that only speak the SSH algorithms from before 2015 (ssh-rsa, Diffie-Hellman group 14 with SHA-1, CBC) are reached too. An experimental feature, to turn on in *Features*.
- **State in My fleet and on its page**: model, serial number, version, active ports, processor, memory, temperature, uptime — read every fifteen minutes; unreachable, failed link or newer firmware available can be seen at a glance and in *Needs attention*.
- **Configuration backup**, every day by default (or every six hours, every week, on demand), kept **only when it changes** — timestamps that move by themselves do not count — with the history, the line-by-line **differences** between two versions and the download. Files encrypted in loco's state: they are part of its own backup. The Stormshield firewall gives its full backup (.na). A new *Network* tab in *Backups* gathers them.
- **Firmware**: drop the manufacturer's file in loco's library (version read from its name, SHA-256 fingerprint shown); loco flags the devices of the same platform that have an older version and installs it, with the configuration backed up just before: Cisco IOS (scp copy, MD5 checked by the switch, boot set to it; INSTALL mode of the Catalyst 9000), EXOS (backup partition), VOSS (add, activate, then commit after the restart), Stormshield (upload and activation through the API). Restart and return followed, new version confirmed. A Cisco Small Business only loads firmware over TFTP: loco flags it, the installation is done from its web interface.
- **Terminal** of the device in the browser (logged session, password handed over by loco) and followed **restart**. Separate rights: view, back up and read configurations, install firmware, restart, terminal, link.
- **A single status dot** on the Proxmox and Home Assistant rows of My fleet: the label ("restart required", "link failed"…) added a second one. A task in progress shows in the actions column, as for a device.
- **Proxmox: pinned kernel respected.** A kernel pinned with `proxmox-boot-tool kernel pin` is the one the server restarts on: if it is already running, loco no longer asks for a restart (it asked endlessly as soon as a newer kernel was installed). The server's page flags the pinning.

## 2.192.0 — 2026-11-22

- **The graphics card in the device's specifications** (*Details* tab), on Windows and on Linux: name, video memory and driver version, for each card present. On Windows, the video memory is read from the registry — WMI caps it at 4 GB — and the virtual display adapters (remote desktop, virtual screens) are not counted; on Linux, the cards are those of the PCI bus, named by nvidia-smi, the AMD driver or lspci.
- **Updating the NVIDIA driver from loco (Windows).** loco compares the driver of each NVIDIA card with the latest *Game Ready* driver NVIDIA published for that card and that version of Windows (NVIDIA's public service, read in the background): the page shows it in the *Updates* tab (*Graphics driver* section) and in *Specifications*, with an *Update the driver* button. The agent downloads the official installer from download.nvidia.com, refuses any file that is not signed by NVIDIA, installs it with no window and no restart, then checks the version really in place. Never started by *Update* or *Update everything*: only on demand. On Linux, the NVIDIA driver is a system package, updated with it.

## 2.191.0 — 2026-11-22

- **Hardware details for Windows too.** A Windows device's page now shows the *Specifications* section (*Details* tab), as on Linux: processor, memory, system disk, Windows version, virtualization, boot, and the hardware configuration — machine and serial number, motherboard, BIOS, memory modules (slot, size, type, speed, manufacturer, part number) and physical disks (model, size, SSD or hard drive). Read by the Windows agent (read-only WMI) at the next inventory, once the agent is up to date.
- **Physical cores and logical processors told apart**: "20 cores · 28 threads" for a hyperthreaded processor, instead of counting the logical processors as cores (Linux and Windows).
- **Unknown disk type** (a virtual machine's disk on Windows) shown as "—" instead of "hard drive".
- The MCP server (`get_device`) also gives these specifications and the hardware configuration, for Linux and Windows.

## 2.190.0 — 2026-11-22

- **Proxmox in My fleet, like Home Assistant: updates, restart and terminal.** A Proxmox server linked for its backups can also be linked through SSH, with a key specific to loco (host key confirmed by its fingerprint) — Proxmox's API cannot install updates. It then appears in My fleet with its updates, its resources and an *Update* button, and it has its page: version, kernel (*restart required* when a newer one is installed), packages to update (security first), machines, processor, memory, disk, activity with a live log, root terminal.
- **Safe update of the hypervisor.** `apt-get dist-upgrade` like Proxmox's *Upgrade* button (never `apt-get upgrade`), started on the server itself: the update goes to the end even if the link drops or loco restarts — including when loco runs on that Proxmox, which the page flags. Optional restart afterwards, only if the update succeeded; refusal of an update that would remove `proxmox-ve`. A Proxmox server is never restarted through a selection in My fleet.
- **My fleet shows Home Assistant and Proxmox even with no device enrolled** (they stayed hidden behind "No device yet").

## 2.189.0 — 2026-11-20

- **Long outputs fully readable for assistants (MCP).** When a command, a script or the remote terminal produces an output longer than the limit, the assistant now receives the **beginning** *and* the end of the text (with, in the middle, the count of the characters left out) instead of only the end: the context and the errors, often at the top, are no longer lost. Applies to the terminal (`terminal_run`), to commands and scripts (`get_script_run`, a device's recent actions). The output is still flagged as shortened.

## 2.188.0 — 2026-11-20

- **More hardware details on a Linux device.** The *Specifications* section (*Details* tab) now shows the machine's brand, model and serial number, the motherboard, the BIOS, the processor's sockets and threads, the memory modules (slot, size, type, speed, manufacturer) and the disks (model, size, SSD or hard drive). *(Windows devices will follow.)*
- **Clearer uninstall icon.** The button to uninstall a package or an application now uses a trash can.

## 2.187.0 — 2026-11-19

- **Easy control of an Android TV / Google TV.** In an Android device's *Control the screen*, a directional pad (↑ ← OK → ↓) lets you navigate as with the remote, and the keyboard's arrows (with Enter = OK) do the same — essential on a TV, which is driven with the directional pad and not by touch.
- **Uninstalling a package or an application (Linux).** On a Linux device, the *Installed packages* tab lets you uninstall a package installed on purpose, and the *Flatpak* tab a Flatpak application, after confirmation. (System dependencies are not offered for uninstallation.)

## 2.186.1 — 2026-11-19

- **Fix: Wazuh page.** The *Wazuh* page no longer showed (internal error) as soon as a device had the Wazuh agent installed. This is fixed.

## 2.186.0 — 2026-11-19

- **Disk space analysis (TreeSize style), on demand.** In a device's *Files* tab, a *Disk space* button switches the view to show, in the folder displayed, each item with its **real size (content included)** and a proportional bar, from the largest to the smallest. You click a folder to go down into it, exactly as when browsing. (Windows and Linux devices, once the agent is up to date.)
- **Neater action menus.** The files' "three dots" button becomes a small, discreet, round menu; on the *Wazuh* page, a device's *Reapply* and *Remove* actions are now in that same menu rather than shown as links stuck together.
- **Finishing touches.** The "No schedule" screen now shows a single icon.

## 2.185.0 — 2026-11-19

- **HACS updates: working again with Home Assistant 2026.11.** Since version 2026.11, Home Assistant has replaced HACS with a built-in *Marketplace* that blocks updates until a warning has been accepted — an acceptance recorded separately for each user. As loco acts with its own access, its updates failed ("error 500"). loco now accepts this warning for itself before installing, and the update goes through. For any other problem, Home Assistant's exact message is now shown instead of an opaque "error 500".
- **"Update" button on the Home Assistant row of My fleet.** When a Home Assistant instance has updates available, an *Update* button appears right on its row in *My fleet*, as for the other devices.

## 2.184.0 — 2026-11-19

- **HACS (Home Assistant Community Store) updates.** On a Home Assistant instance's page, loco now lists the installed HACS repositories (community cards, integrations and themes), flags those for which an update is available, and lets you update them — one by one, from the *My fleet* selection, or with *Update everything* — in the same place as the core and the add-ons.

## 2.183.1 — 2026-11-19

- **Fix: page of a device with a graphics card.** The page of a device whose graphics card does not report its temperature (or its usage) no longer opened (internal error). This is fixed.
- **Better placed "Refresh" button.** The button that calls the agent back to redo the inventory right away has moved to the actions bar, at the top of the page (it was badly placed and showed twice on the Inventory tile).

## 2.183.0 — 2026-11-19

- **Clearer file transfers, which can be cancelled.** In a device's *Files* tab, each upload or download shows the size transferred (MB / GB), the percentage and the throughput, and a cross cancels it along the way.

## 2.182.0 — 2026-11-19

- **Graphics card in the monitoring.** Devices with a graphics card (NVIDIA, and AMD on Linux) show its usage, its memory and its temperature in the *Monitoring* tab, next to the processor and the memory.
- **Home Assistant in the My fleet selection.** Home Assistant instances can be ticked like the other devices, and their available updates started, or the instances restarted, from the selection bar.
- **Refreshing a device on demand.** A button on a device's page calls the agent back to redo the inventory right away, without waiting for the next contact.
- **Profile photo.** Each account can add a photo (in *My account › Appearance*), shown in the top bar instead of its initial.
- **"Don't show again" on information notices.** The orange information notices now have a cross so you no longer see them; this choice is kept for your account.
- **Navigation bar: "Systems".** The menu that gathers Linux, Windows, Android and Home Assistant is now called *Systems*.

## 2.181.0 — 2026-11-19

- **Resources of your Home Assistant OS instances.** On a Home Assistant OS instance's page, loco now shows the memory and the processor in addition to the storage, read live.
- **Battery of Android devices.** A phone's or tablet's page shows its battery level (and whether it is charging); nothing for a device without a battery (TV box).
- **Neater "System" and "Administration" menus.** They show in a single column with discreet scrolling when they are long, instead of a block that overflowed.
- **Home Assistant icon in line with the rest.** In *My fleet*, an instance's icon uses the neutral color of the other device icons.
- **Fix: updating an application in the user's session.** When an installer must run in the open session (like iCUE), loco now faithfully reports the real result instead of a misleading message. *(Note: the Corsair iCUE installer sometimes ends in error by its own doing during an upgrade; it is a defect of that installer, not of loco.)*

## 2.180.0 — 2026-11-18

- **Windows: stubborn applications finally update (winget fallback).** Three cases where winget failed on a real fleet are now handled, each with the right method, from the vendors' official sources:
  - **HWiNFO**: winget's public manifest stayed stuck on a version whose installer the vendor had removed (404 error). loco finds the current official version and lays down the vendor's installer after **checking its digital signature**.
  - **Corsair iCUE**: its installer ended abruptly when started in the background. loco starts it again in the signed-in user's session, where it goes through.
  - **Google Play Games**: it updates itself (Google manages its versions) and yet appeared endlessly as "to update". loco applies the update, then stops offering it again.
- None of this adds anything to loco's catalog: these applications are simply tracked.
- **More balanced navigation bar.** The entries are centered (no more feeling of emptiness on the right) and *Overview* shows its name next to the icon again. The drop-down menus open and close with a short delay, nicer to browse.
- **Home Assistant in My fleet, cleaned up.** Home Assistant's real "house" icon (in line with the style of the other icons), only the instance's address, and a row that behaves like the other devices.
- **Small finishing touches.** The overview's storage bars animate on display like the rest of the dashboard; the Home Assistant terminal's welcome text no longer stretches.

## 2.179.0 — 2026-11-18

- **Home Assistant: one page per instance.** Each instance now has its own page (versions, updates, add-ons, backups, log, terminal and actions); the *Home Assistant* page has become a clear list of instances that lead to them.
- **Your Home Assistant instances in My fleet.** Home Assistant instances appear as rows of *My fleet*, next to the Windows, Linux and Android devices — with their state and the available updates; a click opens their page.
- **"System" menu reworked.** Linux, Windows, Android and Home Assistant are grouped in a single *System* menu, wider and balanced (Linux, Windows, Android, Home Assistant sections).
- **A device's page: breathing room.** The *Updates · Up to date* label no longer sticks to the block of cards.

## 2.178.0 — 2026-11-18

- **Windows: applications installed "for everyone" finally update.** Some applications (PdfCraft, PhotoCraft, WordCraft…) are installed for all users; their installer then refused the update started by loco ("already installed for everyone, pass ALLUSERS=1", MSI error 1603 / `0x8A150049`). The agent now tries again passing `ALLUSERS=1` to the installer, which does update the shared copy — with nobody signed in.
- **Navigation bar: a single "System" menu.** Linux, Windows and Android are no longer three separate entries at the top: they are grouped in a **System** menu (Linux, Windows, Android sections), like the Administration menu. The bar is clearer.

## 2.177.0 — 2026-11-18

- **Windows: third-party applications update even when open, and a failure no longer blocks the update.** When winget fails to update an application outside the catalog because it is in use (locked files, MSI installer failing with `0x8A150049`), the agent **closes that application — without confirmation — then tries again**. If it still fails, the device's update no longer fails because of it: Windows Update and the catalog's applications alone decide the result; the application is simply flagged and **stays offered**, tried again at the next update. As the agent runs as a service (SYSTEM account), a scheduled update outside any session updates these applications with nobody signed in.
- **My fleet: the update counters become badges.** "N system", "N software" and "N security" show as badges matching the style (screen, package, shield icon), instead of plain text.

## 2.176.0 — 2026-11-18

- **REST API (HTTP)**: the *MCP server* tokens now open a **REST API** — each tool becomes an ordinary HTTP route, for a script, monitoring (Zabbix, Grafana, a cron job) or another program, without an MCP client. Base `https://<server>:8443/api/v1`, `Authorization: Bearer …` header; same tokens, same rights, same fleet and same traces in the audit log ("· API "token name"") as the MCP server. Reads are `GET`s (devices, groups, installed software, catalog, scripts and their runs, overview, server state, audit log), actions are `POST`s or `DELETE`s (inventory, update, restart, wake, installation, cancellation, run a script), queued as for the MCP server. The **OpenAPI 3.1** description is served without a token at `/api/v1/openapi.json`, and `GET /api/v1` lists the routes the token can call.
- **The machines' resources from the API**: `GET /api/v1/devices/{id}/resources` returns a device's latest reading — processor (percentage, cores, load), memory and swap **in bytes** (total, used, available) and as a percentage, each disk (mount point, bytes, percentage), network throughput and time since boot; `?history=1h|24h|7d` adds the curve. `GET /api/v1/resources?sort=memory` (or `cpu`, `disk`) ranks the whole fleet by usage, to spot the loaded machines at once.

## 2.175.0 — 2026-11-18

- **Windows: updates of applications outside the catalog**: the installed applications that winget can track (7-Zip, Notepad++, VLC, Remote Desktop Manager…) appear in the device's *Updates* tab, next to the Windows updates, with the installed version and the new one — without adding them to loco's catalog. You tick them one by one or *Update* takes them all (device, whole fleet, schedules); the agent updates them through winget, silently, under the SYSTEM account. They count in the status light and in My fleet, and are blocked like a Windows update, by their name or their winget identifier. The listing is done by `winget export` on the device and the comparison against loco's winget index, renewed every day. Without winget on the device, the page says so. Applications installed for a single user are not seen.
- **On a Windows in French, applications published only in English update too** (Remote Desktop Manager, for example): winget judged them "not applicable" for lack of a French installer; the agent then tries again accepting the English installer. What winget cannot update on a device is set aside, with its reason, as long as its version does not change, and the components that Windows or their vendor keep up to date themselves (App Installer, Edge, WebView2, Teams, OneDrive, Terminal, Store libraries) are never offered.
- **Update the selection** no longer queries Windows Update when only applications are ticked.

## 2.174.0 — 2026-11-18

- **Free appearance for administrators**: an account with the *Administrator* profile is no longer held by the company's frame (*Administration › Appearance*) — its *Appearance* window offers all the themes and presets, all the colors and all the backgrounds, even when the company limits or imposes them. The other accounts and the sign-in screen stay within the frame.
- **Their own logo**: in the same window, an administrator chooses the logo they see — the company's, loco's, or an image of their own (PNG, JPEG, GIF, WebP or SVG, 2 MB at most), in the header and the tab's icon. This choice only applies to them. Changing the logo reloads the page, window reopened, without losing the theme just tried.

## 2.173.1 — 2026-11-18

- **Backups in one place**: the *Backups* tab of the *System* page duplicated the new *Backups* page; it joins it. Downloading, restoring or deleting a loco backup, setting how many are kept and restoring from a file are now done in *Administration › Backups*, *loco* tab; the off-site copy is the *Destination* tab there. The "Last backup" tile of *System*, the overview's alerts (backup overdue, off-site copy failed) and the documentation pages lead to the new page. On a Windows server, the *loco* tab shows the backups and the commands, as *System* did.
- **loco backups on a phone**: each row squashed the archive's name (one letter per line) and choosing a file to restore widened the page on a small screen; the name moves above the buttons and the field narrows.

## 2.173.0 — 2026-11-18

- **Backups page** (*Administration › Server › Backups*): what is backed up — loco itself, your Home Assistant instances, your Proxmox machines —, where, and when, in one place. The external destination (mounted NAS, S3, SFTP) is set there too, and you choose what goes to it: loco's backups (*Also copy loco's backups* box, ticked by default), and the Home Assistant instances one by one.
- **Proxmox VE** (optional feature): a Proxmox server is linked through an **API token** created for loco, with rights limited to backup (the `pveum` commands are given). The secret is kept encrypted and never shown again; Proxmox's certificate is never accepted automatically: its SHA-256 fingerprint is shown, to compare with Proxmox's before confirming, and the token only goes to that certificate (a renewed certificate stops everything until a new confirmation). The page lists the virtual machines and the containers with their last backup; you tick, choose the Proxmox storage (disk, NAS declared in Proxmox, Proxmox Backup Server) and the mode (snapshot, suspend, stop), then *Back up now* or *Schedule…* (once, every day, every week, every month; the ticked machines, or all including future ones). Each backup is followed to the end — even after a loco restart, Proxmox carrying on with the task —, with its log, and can be cancelled. A schedule's retention only deletes its own backups, never the others or the protected backups. *Declare a NAS…* adds an NFS or SMB share as Proxmox storage (the password is handed to Proxmox, loco does not keep it). Rights: view, back up, link.
- **Home Assistant: copying the backups to the external destination**: *Copy now* (with, if you want, a full backup just before) or scheduled; the most recent backup kept in Home Assistant is downloaded through the Supervisor API — the token goes through curl's input, never as an argument —, checked down to the size, encrypted with the destination's passphrase if there is one, then dropped off; only the latest copies of each instance are kept (3 by default). A backup already copied does not go again.
- **External copy**: an S3 upload is now streamed (a backup of several gigabytes no longer goes through memory), an SFTP upload is no longer cut off after three minutes, and the Home Assistant copies and loco's never delete each other.

## 2.172.3 — 2026-11-17

- **Phone: the top bar no longer overflows**: with My fleet, Linux, Windows, Home Assistant or Android and Administration, it overflowed the screen on most phones (the page widened and shifted). It now tightens according to the real space — arrows, then labels of the menus not open — and each entry keeps its icon; Administration gets its own back when its label no longer fits.
- **Nothing overflows any more on a small phone (320 to 360 px)**: long names and identifiers without spaces (catalog applications, Android packages, links in a description, a command in a text) are broken rather than widening the page; the Windows Update state, a deployment's "reinstall" box and the alerts' switches wrap.

## 2.172.2 — 2026-11-17

- **Windows enrollment without importing loco's authority**: the command of the *Enrollment* page failed on a new device ("Could not establish trust relationship for the SSL/TLS secure channel"), loco's certificate not being recognized yet. It now recognizes that certificate by its SHA-256 fingerprint, for the script as for the MSI download: it works the first time, and any other certificate is still refused. A certificate the device already recognizes (enterprise authority) goes through as before. Run it in Windows PowerShell (not PowerShell 7).
- **Home Assistant: link that failed on some SSH add-ons** ("sh: /dev/fd/64: line 14: syntax error: unexpected "(" (expecting "}")"): loco ran its commands in a login shell, which first reads the add-on's profiles; a profile written for bash or zsh stopped `sh` before any command. The commands now go through a plain shell, and the supervisor token, which the `ha` command needs, is taken from the add-on's environment (otherwise from its profile, read without being run). It never appears in a command.

## 2.172.1 — 2026-11-17

- **Pages followed live, never again under your fingers**: a device's page now refreshes in place (header, actions in progress, *Updates*, *History* and *Details* tabs) instead of reloading entirely every minute; an open window or menu, selected text, typing, ticked boxes or an expanded log are no longer lost — the refresh waits, and an open log stays open. Same rule for following the updates, a script's run and a deployment's detail, which only reload when nobody is doing anything there.
- **Wazuh live**: the *Wazuh* page follows the installations, removals and states reported by the agents, without reloading.
- **No more error page along the way**: when loco restarts (an update) or a request fails, My fleet and the other followed pages no longer reload onto an error page; they resume at the next reading. A request without an answer no longer stops the follow-up, and coming back to the tab starts a reading right away.
- A menu that opens a window closes.

## 2.172.0 — 2026-11-17

- **Clearer bar for ticked devices** (My fleet): "2 devices selected" instead of "Selection: 2", then *Wake*, *Restart* and *Update*. *Restart* is new: the ticked devices restart one minute after their next contact (restart right; those already restarting, changing their system version, or the Android devices are left out and counted). The separate "Restart if needed" box becomes *Update, then restart if needed* in the *More* menu, with *Turn on remote wake*.
- **Approval before the remote desktop, per group**: *Require approval* and *Stop requiring approval* leave the selection bar; they are now set for all the devices of a group, from the group's menu (*Device groups*), and still device by device on its page.

## 2.171.0 — 2026-11-17

- **Updating to a new version of the system**: when the next major version is published (Debian 13, Ubuntu 26.04 LTS, Fedora, new Windows version offered by Windows Update), loco shows it — "Update to Debian 13 (trixie)" on the device's page and in My fleet, *Situation › Update to a new version* filter — and installs it on demand, restart included. Debian: current version updated, refusal if held packages remain, repositories switched to the new version with a copy of the old files (a third-party repository that does not publish it yet is disabled, not deleted), disk space checked, repositories put back as before if something is missing, then `upgrade` and `full-upgrade` with no questions. Ubuntu: the official `do-release-upgrade` tool, after a restart if the current version requires one, then loco's repository put back for the new version. Fedora: `dnf system-upgrade`, offline installation at the restart, version checked when it is back. On Linux, the upgrade runs outside the agent, with its progress live and its log on the device. Rights: update and restart.
- **Windows: no more new Windows version by surprise**: a new version offered by Windows Update ("Upgrades" category) no longer goes with *Update* or *Update everything*; it is shown separately and only installs through its button.
- **Restart, next to Update**: on a device's page, *Restart* leaves the *More* menu and takes its place next to *Update*, with its own calendar button.
- **Scheduled restarts**: the calendar opens *Schedule a restart* — once, every day, every week or every month, in the server's time. The *Schedules* page also offers *Restart* for groups or the whole fleet. Each device restarts one minute after receiving the command, and a message warns the signed-in person. Rights: restart devices, and for a group or the whole fleet, the "whole fleet" right as well. Android devices are not concerned.

## 2.170.0 — 2026-11-17

- **Enterprise certificate without an IP address**: the certificate request (*System › Certificate*) now only contains the server's DNS names. When the agents reach loco by an IP address, they keep the certificate of loco's authority, and your authority's certificate is presented to whoever comes by name (SNI) — browsers, Claude Code, Codex. Before, the agents' IP was in it: an authority that restricts IP addresses (name constraints, for example an intermediate authority limited to `lab.local` and to IP ranges) then made BoringSSL clients, Claude Code among them, refuse the whole chain ("unsupported name constraint type"), even when OpenSSL validated it.
- **The case is flagged**: if the enterprise certificate in service contains an IP address while an authority of the chain restricts IPs, the System page says so and gives the steps to follow (new request, signature, import). The enterprise certificate served by name is also watched by the expiry alert.
- Nothing changes for a certificate that already covers the agents' address, nor when they reach loco by a name.

## 2.169.0 — 2026-11-17

- **Claude and Codex: an installer instead of the pre-filled message**: the Claude and Codex tiles of the *MCP server* page open a window — the token's name, lifetime and tools, the computer's system — then download an installer (Windows `.cmd`, macOS and Linux to run with `sh`). Opened, it writes the application's MCP configuration: `~/.claude.json` for Claude Code (`claude` command and *Code* tab of the desktop application), `~/.codex/config.toml` for Codex (desktop application and `codex` command), keeping the rest of the file and a copy of the old one. It then deletes itself. No link can add an MCP server to these applications, hence the installer.
- **loco's certificate**: if the device does not recognize it and loco's authority signed it, the installer shows its fingerprint and offers to trust it (user store on Windows, keychain on macOS, Claude Code setting or system store on Linux).
- **Codex by IP address**: with the installer, Codex also connects to a loco reached by an IP address. Connecting through loco (OAuth), still possible from a terminal, still requires a name for Codex.

## 2.168.0 — 2026-11-16

- **Adding loco to Claude or to Codex in one click**: the *MCP server* page offers two buttons, in the colors and with the logos of Claude and Codex. Each opens the computer's desktop application with the request to add already written: sent, it adds loco to the application's **local** configuration, then opens loco in the browser to authorize access. No token to copy. The equivalent commands for a terminal (`claude mcp add … && claude mcp login loco`, `codex mcp add … && codex mcp login loco`) are given below.
- **Connecting through loco (OAuth 2.1)**: an MCP client can now connect without a prepared token. It discovers loco, registers on its own, and the person authorizes in loco, on the *Authorize Claude Code* (or Codex) page: the token's name, lifetime and tools — viewing is ticked by default, never remote access. The token obtained is an ordinary MCP token, within the limits of the account that authorized it, visible in the list ("obtained by …") and revocable. PKCE required, code only returned to an application of the computer (local loopback), token valid only for loco's MCP server. Signing out of Claude Code also removes it from loco.
- **Codex requires a name**: it refuses to connect through loco if loco is reached by a private IP address. The page flags it; declare a name in *System › Network › Portal address*.
- Tokens created by hand and their `Authorization` header work as before. See [docs/mcp.md](docs/mcp.md).

## 2.167.0 — 2026-11-16

- **Wazuh: removing the agent also deletes its entry on the manager**: when the Wazuh agent is uninstalled from a device, loco removes its entry from the manager (`manage_agents -r`), then writes it in the removal report. It needs SSH access to the manager, to set once on the Wazuh page (*Removing manager entries* block): loco gives a line to add to `authorized_keys`, which only allows its key to run that single command, with only the digits received — no shell, no other command; the manager's host key is shown and confirmed before any exchange, and a *Test the access* button checks everything without removing anything. Without this access, nothing changes: the entry stays on the manager, and the report says so.
- **Caution**: loco only removes an entry if the device was linked to the configured manager and reports the agent as really uninstalled; never the `000` entry (the manager itself).
- **The Wazuh manager enrolled as a device is recognized**: it appears as "Wazuh manager" (instead of "Stopped — the Wazuh agent service is not running"), is not counted among the devices to equip, and loco neither installs nor removes an agent there. Comes with the update of the device's loco agent (2.167.0).

## 2.166.1 — 2026-11-16

- **Wazuh: enrollment without a password after having had one**: when the enrollment password is removed from the settings (*stop sending one*), *Reapply* also removes from the device the `authd.pass` file placed before. Without this, the Wazuh agent kept sending the old password, and a manager that does not expect one refused it in a loop ("Invalid request for new agent").

## 2.166.0 — 2026-11-16

- **Wazuh agent on the devices (optional)**: to turn on in *Features › Optional*. A *My fleet › Wazuh* page sets your manager's address, the agent's version (chosen among those published by Wazuh), the agent group and, if one is needed, the enrollment password; then installs the Wazuh agent on the ticked devices, or on a device from its page (*More › Install the Wazuh agent*). Windows, Debian, Ubuntu and the Red Hat family.
- **The official package, verified, without Internet on the devices**: loco downloads Wazuh's package once (deb, rpm or msi), checks it against the SHA-512 fingerprint published by Wazuh, then hands it to the devices through their loco agent, which checks its fingerprint in turn before installing it.
- **The state of each device**: connected, waiting for the manager, disconnected, stopped or not installed, with the agent's version, number and name at Wazuh — on the Wazuh page and on the device's page. When the agent does not connect, the report quotes the error from its log (password refused, name already taken on the manager…). *Reapply*, *Update* and *Remove* complete the set.
- **The enrollment password stays secret**: kept encrypted, never shown again or written to the audit log; on the device, it goes in the file the Wazuh agent reads (`authd.pass`, readable by administrators only), never as a command argument.
- **Three rights** in the access profiles: view Wazuh, install and remove the agent, configure Wazuh. An account limited to part of the fleet only acts on that part.
- The loco agents update themselves in 2.166.0 to report the Wazuh agent's state; an installation requested before waits for their update. See [docs/wazuh.md](docs/wazuh.md).

## 2.165.0 — 2026-11-15

- **Docker without cloning the project**: a ready-to-use image, `ghcr.io/shanouhar/loco`, is published with each version. Just download `linux/docker/docker-compose.yml` into a folder, then run `docker compose up -d`; the settings go in a `.env` file next to it (template: `.env.example`). Update: `docker compose pull && docker compose up -d`. The `LOCO_IMAGE` variable chooses the image, for example a precise version (`ghcr.io/shanouhar/loco:2.165.0`).
- **From the sources, nothing changes**: `sudo ./up.sh` still builds the image on the spot. The build is described in a separate file, `docker-compose.build.yml`, which `up.sh` adds to `docker-compose.yml`. Whoever ran `docker compose up -d --build` directly now uses `./up.sh`, or `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.

## 2.164.0 — 2026-11-15

- **Taking over installations from before 2.0 removed**: loco no longer takes over an installation, a backup or a device's configuration dating from before version 2.0, under the project's former name. The Linux, Windows and Docker installers no longer migrate such an installation; `loco-backup restore` and the *System* page only accept loco backups; the devices' scripts (APT, RPM, Flatpak) no longer remove the files placed by those old versions, and the old address of the Flatpak repository is no longer served. **To come from such an old installation**, first install 2.163.1, which does the takeover, then update.
- **An APT repository already published keeps its identity**, whatever it is (`Origin` and `Label` of its `Release` file): the devices already connected keep updating without confirming anything.
- **Docker**: `up.sh` builds the image, starts loco and waits for it to be healthy, with no other step.
- **Documentation redone**: README in pictures (tour of the portal, animation of the live updates, how-it-works diagram), new screenshots taken on a demo instance, one guide per topic in `docs/` and a new operations guide, [docs/operations.md](docs/operations.md): routine, alerts, metrics, backups, updates, certificates, lost access, incidents.

## 2.163.1 — 2026-11-13

- **More readable sizes**: a size moves to the next unit from a thousand — 999.9 MB at most, then 0.98 GB, 1.00 GB… An APT cache of 1021.9 MB now reads "1.00 GB". Everywhere loco shows a size: Storage table of the overview, caches, repositories, backups, Home Assistant, and what the browser updates live.
- In Spanish, the sizes updated live by the browser are shown in "MB", like the rest of the page (they appeared in French units).

## 2.163.0 — 2026-11-13

- **Remote desktop: choosing the screen on KDE Plasma (Wayland)**: the *Screen* list also appears on a KDE Plasma device with several screens — *All screens*, or a single one. Plasma then shares its "Full workspace" (first tile of its *Screen sharing* window, the consent asked once on the device): loco crops the chosen screen out of it. A screen set to another scale is taken into account; the choice is remembered per device, as elsewhere.
- **KDE Plasma: the mouse lands where you click from one screen to the other**: Plasma holds the pointer back a hundred pixels or so when it moves from one screen to the next; a click made right after changing screens landed beside the target. The pointer now lands in the right place from the first movement.
- **KDE Plasma: a single shared screen among several**: when the consent given on the device covered a single screen while the device has several, the mouse acted elsewhere than where you clicked. loco now recognizes the shared screen and places the pointer on it. In this case, there is no list (only one screen is visible); to share the whole workspace, delete `~/.config/loco/portal-token` on the device: the next session asks for consent again.
- GNOME on Wayland still shows its primary screen. These changes come with the update of the device's agent (2.163.0).

## 2.162.0 — 2026-11-13

- **Remote desktop: choosing the screen of a device that has several**: a *Screen* list appears next to *Resolution* as soon as the device has more than one screen — *All screens* (side by side, as until now) or a single one, designated by its number, its size and the *primary* mention. The picture takes the size of the chosen screen; mouse and keyboard stay accurate. In full screen, the list follows the other settings in the control panel.
- **The choice is remembered**: per device, in your browser. The next session on this device reopens on the same screen; a screen unplugged during the session brings the view back to all screens.
- **Windows**: the numbers are those of Windows' display settings. A screen shown alone is read as on a single-screen device (DXGI duplication, only when the picture changes), instead of the slower capture that several screens imposed. The remote desktop now counts in real pixels on each screen, even when set to different scales.
- **Linux**: on an X11 desktop. GNOME on Wayland keeps showing its primary screen, KDE Plasma on Wayland the one that was granted on the device.
- The list comes with the update of the device's agent (2.162.0); an older agent shows all the screens, as before.

## 2.161.0 — 2026-11-13

- **Optional features**: the *Features* page gains an *Optional* section, after *Experimental*. It gathers what is complete but does not concern everyone; like the experimental ones, it is off by default and any account with the Administrator profile turns it on.
- **Home Assistant** (optional): a *Home Assistant* page follows and maintains your installation. loco gets in through Home Assistant's SSH add-on ("Advanced SSH & Web Terminal" or "Terminal & SSH"), with a key of its own: no Home Assistant token is requested or kept, and the private key does not leave the server. The page gives the three steps of the link and the public key to copy.
- **The host key is confirmed**: before any exchange, loco shows the fingerprint of the key the given address presents; nothing is saved until you have confirmed it. If it changes afterwards (add-on reinstalled), the link stops and asks for a confirmation again.
- **What the page shows**: the versions of Home Assistant, the system and the Supervisor with the one waiting, the add-ons and their state, the storage, the backups, what the Supervisor reports (no recent backup, disk almost full, degraded installation…) and, on demand, the last 200 lines of the log. The reading is redone by itself while the page is displayed.
- **Updates**: one by one, or *Update everything*, which chains them in the advised order — the Supervisor, the add-ons, Home Assistant, then the system. The confirmation window offers to create a backup first (ticked by default). An update that has been started shows as "in progress" and cannot be started again; the activity keeps the last twelve tasks with their output.
- **Backup and restarts**: *Back up* starts a full backup, kept in Home Assistant; *Restart* restarts Home Assistant or the whole machine. When the system restarts, loco waits for it to come back: the task only succeeds if it answers again.
- **Terminal**: the SSH add-on's shell opens in loco's session page (full screen, new tab), logged session. It requires the *Remote access* feature and a loco server on Linux.
- **Rights and overview**: a *Home Assistant* group in the access profiles separates view, update, back up, restart, open a terminal and link instances; only the Administrator profile gets them by default. The overview flags in "Needs attention" a failed link, a degraded installation and the available updates.
- **Server side**: the link uses the server's SSH client — already present on the appliance, added by the installation elsewhere (without ever blocking it). The remote sessions open at the time of the update are closed. See [docs/homeassistant.md](docs/homeassistant.md).

## 2.160.0 — 2026-11-13

- **Android: the sleeping screen turns back on when you take over**: when a screen session opens, the app turns the device's screen back on; if it goes back to sleep during the session, the first click or key press turns it on again, like a finger on a sleeping phone. You thus land on the lock screen instead of a dark screen.
- **Android: the device's code is typed on the keyboard**: with the device locked, type its code on your keyboard, then Enter — PIN or password as is; for a pattern, the numbers of its dots, counted as on a phone keypad (1 top left, 9 bottom right). Typed from the lock screen, the first key brings up the code entry. Nothing is bypassed: you need to know the code, and failed attempts count as on the device.
- Android hides the code entry screen in the picture sent: it shows as black, the typing still goes through. A note recalls it below the session, and the description of the app's accessibility service says what it now does.
- These new features come with the update of the Android app (2.160.0).

## 2.159.0 — 2026-11-12

- **My fleet: filters as drop-down lists**: the two rows of chips give way to a bar of lists — *Status*, *System*, *Situation* (offline, restart needed) and *Group*. Each button says the filter and the value chosen, and fills in when a filter is set; each row of a list gives its number of devices. A *Clear the filters* link appears as soon as there is one. The search and the pages keep the chosen filters.
- **Filtering by distribution**: the *System* list offers the family — Windows, Linux, Android — then, indented, its distributions present in the fleet, without a version number: Windows 10, Windows 11, Windows Server 2025, Debian, Ubuntu, Rocky Linux… Debian 12 and Debian 13 count together. Android devices now have their own row: they are no longer counted with Linux.
- **Appliance and Docker: stopping the service no longer waits for the agents' connections**: since the persistent connections (2.158.0), nginx waited ten seconds for the agents to give theirs back before being stopped by force; stopping the service went from three to twelve seconds, and during an update the service restarted in the meantime failed up to four times out of the five the system tolerates — beyond that, loco would have stayed stopped. nginx now stops right away, and the appliance's update waits for the old service to have really finished before starting the new one. Debian, Ubuntu and Red Hat installations were not affected.

## 2.158.1 — 2026-11-12

- **A requested restart is no longer started again**: once the device had taken the restart, the *Restart* button came back during the minute or more before the shutdown, as if nothing had been requested — a second one could be started. The device now shows "restart in progress", with a spinning indicator, until it is back (five minutes at most if it does not come back); on its page, the *Restart* entry of the More menu is replaced by "Restart in progress" and the Restart tile says so too.
- **An update in progress can be seen, and is not started again**: in My fleet, a device's button gives way to "update pending" as long as the device has not taken it, then to "update in progress" with a spinning indicator; on the page, the *Update* button becomes "Update in progress" and can no longer be clicked, and the package selection boxes disappear during the update.
- **"Update everything" no longer counts the devices already started**: the number in parentheses only covers the remaining devices, and an "Updates in progress (n)" button appears next to it, leading to the timeline. When all the devices are already started, only that one remains.
- A second request is refused by the server with "already in progress", even when made another way (with or without restart, through the device selection, through "Update everything"), and a double click while sending no longer goes twice.
- Waking a device shows "wake in progress" once sent, instead of "wake pending".

## 2.158.0 — 2026-11-12

- **Remote sessions over a persistent connection**: a device's agent and the browser now keep a connection open with loco (WebSocket, on the usual HTTPS port). The server notifies them as soon as a session is requested, a key is pressed or the device answers, instead of waiting for them to come and check. Terminal, remote desktop and the Files tab benefit from it. Measured on a Debian device: from the key to its echo, 62 ms median before, 25 ms now; an idle open session no longer produces any request (about ten per second before); a terminal opens in less than 0.2 s, whether the device's page was open or not.
- **Actions go right away**: an update, a restart or a script requested from the portal immediately wakes the connected agent, which comes to fetch it without waiting for its next contact.
- **Nothing to set, and a permanent fallback**: no port to open on the devices or on the firewall. If the persistent connection is not possible — agent not up to date yet, network equipment that cuts it, browser that refuses it —, the session goes through HTTPS round trips as before, without interruption: it can switch from one to the other during the session. Rights, the user's approval before the remote desktop, session limits and the audit log are unchanged.
- **Server side**: the relay runs in the portal itself, on local port 8090 (next to 8089), and nginx passes it two addresses with the same access rules as the rest — `/agent/relay` for the devices, `/fleet/session/…/ws` for the interface. The update redoes this configuration. If port 8090 is already taken by another program, loco installs anyway and the sessions go through HTTPS.
- **A background tab no longer loses its session**: as long as the browser stays connected, loco knows it is there, even if the browser slows down the tabs you are not looking at.
- Linux, Windows and Android agents: the persistent channel comes with their update (automatic for Linux and Windows). The Windows agent could not be tried on a real Windows before this version; if there is a problem, it falls back by itself to the HTTPS round trips.

## 2.157.0 — 2026-11-12

- **A terminal opens right away**: a requested session waited for the device's agent to come and check, which it only did every five seconds when idle — hence two to five seconds of waiting when opening a terminal, a remote desktop or the Files tab. As soon as a device's page is open, or a session is running or has just closed, its agent now comes to check several times per second: the session opens in about a quarter of a second. Measured on a Debian device: 3.9 s median before, 0.25 s now.
- **Even without going through the page**: a device nobody is looking at comes to check every second in a fleet of 25 devices at most, then less and less often as the fleet grows, down to the five seconds of before — the server never receives more than 25 of these exchanges per second, about 2 ms each. An idle Android device keeps its three-second pace, for its battery.
- **Android: the first text of a session no longer waits**: after a long pause, it arrived more than a second late.
- The live follow-up of the Monitoring tab keeps its reading every two seconds, whatever the pace of the exchanges.
- The Linux and Windows agents update themselves; until then, they keep their five-second pace. The Android app follows with its own update.

## 2.156.0 — 2026-11-12

- **My fleet and the overview keep up to date without reloading**: when the fleet's state changes, only the parts of the page concerned are replaced — no more flickering page, the scroll stays where it is. In My fleet, the row of a device whose state changes lights up for a moment; the *Update everything* window follows the number shown on its button. Nothing is replaced while a menu or a window is open, nor during a selection of devices. If the page must change shape (first device of the fleet, for example), it reloads as before.
- **More visual overview**: the fleet is a ring colored according to the devices' state, with their number in the center. The server's processor and memory have, like the network, a curve of the last two minutes, kept up to date every two seconds; it turns orange then red as it approaches saturation.
- **The color tells the level**: the gauges (server disk, a device's resources) are green as long as all is well, orange then red at their thresholds. The accent color stays your setting.
- **Icons to find your way**: on the panel titles and the tiles of the overview, System, a device's page, Linux packages and Windows Update.
- **More readable empty states**: an icon above the sentence, wherever a list is empty. An empty Linux or Windows Update cache says when it will fill up, instead of a table header above nothing.
- **A bit of motion**: the panels come in smoothly when arriving on a page, the tabs fade, the buttons press down slightly. No motion on a reload, nor when the system asks to reduce animations.

## 2.155.0 — 2026-11-12

- **Android: app updates without Play Protect, through adb**: on a device paired over adb and reachable, loco installs the new version of the app itself, as from a computer — no confirmation window, no scan offered by Play Protect on the device. If the installation through adb fails (device off the network, wireless debugging off), the app takes over and updates itself, as before. Never during an open remote session; each installation is recorded in the audit log.
- **Preparing the app's registration with Google**: Android is gradually requiring that an app installed outside the Play Store come from a registered developer. Registration is done once, by the holder of the app's signing key, and applies to every loco instance. The APK build now embeds the proof token given by the Android Developer Console, if it is placed next to the key; the Android documentation describes the steps.
- The permissions set through adb after pairing are no longer set twice when two connections arrive at the same time.

## 2.154.0 — 2026-11-11

- **Android: a smooth picture at every session, set up from the phone**: without adb pairing, Android limits the remote screen to about three frames per second. The app's new *Smooth picture at every session* panel does the pairing without going through the portal: **Set up** opens *Wireless debugging*; turn it on, tap *Pair device with pairing code*, then type the six-digit code in loco's notification, without closing that window. The app finds the port on its own and sends the code to loco. Then each session opens with a smooth picture, with no "Cast" window (about thirty frames per second measured on a OnePlus 12).
- **Pairing limited to the device itself**: loco only pairs with the address the request comes from, declared by the device, never an address chosen by the caller. The device must therefore be on the same network as loco. The code appears in no command, and the pairing is recorded in the audit log as done "from the device".

## 2.153.0 — 2026-11-11

- **An icon says what type of device it is**: a small screen in front of the computers' names (Debian, Ubuntu, Red Hat, Windows), and for Android devices a phone, a tablet or a TV. It shows in My fleet, in the title of a device's page, in the updates timeline, in the device groups and in the choice of devices for a deployment.
- **Android: phone, tablet or TV, recognized by the app**: the app announces the device's type — TV on Android TV, tablet when the screen is at least 600 dp wide (Android's threshold), phone otherwise. The device's page shows it in *Details › Device type*. A device whose app is older is shown as a phone, and its page says so, until the app updates. An open foldable phone is seen as a tablet.

## 2.152.0 — 2026-11-11

- **Much faster My fleet**: the page queries the server every two seconds to keep up to date, and each query recomputed the state of all the devices. The most costly part was comparing the software installed on each Windows device with the catalog, redone several times per device. What the list draws from a device's inventory is now kept in memory and only recomputed when that inventory or the catalog changes. On a simulated fleet with a catalog of 30 applications: 5.5 ms instead of 528 ms for 100 devices, 34 ms instead of 5 seconds for 1,000 devices. The first query after a restart of the portal stays longer (0.6 seconds for 1,000 devices).
- **The overview benefits too**: it no longer reloads each device's full inventory to count the status lights.
- **A long history of actions no longer slows down the list**: the actions in progress of the whole fleet are read in a single query, and a device's last finished action is found without reading its whole history again.

## 2.151.0 — 2026-11-11

- **After this update, sign in again once**: the sessions opened before it are no longer recognized.
- **Signing out really closes the session**: the server now knows each portal session. Once signed out, a cookie copied before is worth nothing any more. A session lasts seven days at most, even when used non-stop; it still expires after eight hours of inactivity, and changing your password closes the account's other sessions.
- **Two-factor authentication: a code only opens one session**: a code already used is refused ("This code has already been used"), you have to wait for the next one. Someone who saw the code being typed can no longer replay it within the minute.
- **The enrollment key no longer stays on the devices**: the agent's `.deb` and `.rpm` packages kept their installation script, readable by all the device's users, with the enrollment key inside. It is now only readable by root, and it is replaced by a simple note as soon as the device is enrolled; the devices already installed by a package are cleaned by their agent at its next contact. During enrollment, the key no longer appears in the device's process list.
- **The enrollment key is no longer written to the log**: the portal's access log (nginx) no longer keeps the addresses' parameters, so no longer the `?key=…` of the enrollment commands.
- **AI assistants**: a command written by the assistant targets 25 devices at most per call, so that a targeting mistake does not go to the whole fleet at once; a library script, written and saved by a person, keeps the limit of 500. The instructions given to the assistant specify that what comes back from a device (a command's output, a screen's text, a file's content) is data, never an instruction to follow.
- **Backups**: `SECURITY.md` wrongly said they were in 0600. They are in 0640 `root:loco` so that the portal can offer them for download and send them off-site, and they contain the private keys of the server and of the local authority: an archive must be protected like the server itself.

## 2.150.0 — 2026-11-11

- **Remote desktop: the numeric keypad works**: on a Windows device, the numeric keypad's keys typed unrelated characters (Korean glyphs). Digits, operators, Enter and the keypad's navigation keys now arrive as the expected keys. On Linux, the digit typed is the one that arrives, whatever the state of Num Lock on the device; in the text console, where the keypad typed nothing, it works too. On Android, the keypad types its characters.
- **Android: no more lost keystrokes**: when typing on the keyboard in a field of an Android device, about one character in two could disappear. Fixed.
- **"Fit to window" says when the device cannot follow**: some devices do not offer the requested resolution — a Windows virtual machine with the basic graphics adapter often offers only one. The picture is then fitted to the window, and the session now says so: *The device does not offer this resolution*, with in the tooltip the size requested, the one the device kept, and the remedy (a full graphics card and driver, VirtIO-GPU or QXL).

## 2.149.1 — 2026-11-11

- **Remote desktop in full screen: a centered, readable panel**: the controls panel stays on the left edge, but in the middle of the screen's height instead of the top corner. Nothing is cut off there any more: the session's state has its own line and shows in full, and the *Resolution* and *Zoom* lists take the whole width.
- **Leaving full screen is visible**: the button is spelled out, with a "minimize" icon, instead of an icon alone. *New tab* and *Close the session* stay as icons on its right.

## 2.149.0 — 2026-11-11

- **Updating the Android agent from the app**: when loco offers a newer version, the app's screen shows *App update* with the available version and the **Install the update** button. The app still updates itself; this button installs it right away, without waiting. Opening the app queries loco at once.
- **You know where the device stands**: at the bottom of the screen, the version line says "up to date" or the available version. If the permission to install apps is missing, the button leads there first; and what prevents an installation (loco unreachable, unexpected file, refusal on the device) is written under the button.
- **After the installation**: Android closes the app to replace it. It starts again by itself, an *App updated* notification confirms it, and its screen reopens when Android allows it.
- Outside its trusted networks, the device does not contact loco: the button is not offered there.

## 2.148.0 — 2026-11-10

- **Much faster Android remote desktop**: an Android device's screen arrived in jerks, with more than a second between a click and its effect. The app redid a full secure connection at each of its exchanges with loco (about thirty per second): it now keeps the connection open. What moves on the screen goes as JPEG, twice as light, then the picture becomes sharp again as soon as the screen settles; and the app sends the pictures without waiting for the browser to ask for them one by one, adjusting to what it has really displayed. On the test device: about ten frames per second instead of one and a half, and a third of a second between a click and the picture instead of a second and a half.
- **The finger follows the mouse live**: dragging to scroll or move acts during the gesture, instead of being replayed once the button is released; holding the button down makes a long press.
- **For a smooth picture, screen sharing**: remote control (accessibility) alone stays limited by Android to about three frames per second. Turn on the remote screen in the app, or pair the device once through adb so that it takes it back on its own.
- The AI assistant, which does not read JPEG, still receives the screen losslessly, as before.

## 2.147.2 — 2026-11-10

- **Remote desktop: a tidy page**: outside full screen, the display settings, the session's buttons and the controls shared three rows with no order. The session's title and state are now on the left, **Full screen**, **New tab** and **Close the session** on the right on the same line. The device's screen fits in a single frame: above, the bar of the commands sent to the device (Ctrl+Alt+Del, shortcuts, keys to hold, text, clipboard); below, a status bar with the resolution, the zoom and the session note. The page fits in the window, without a scrollbar.
- **The portal's top bar**: between 980 and 1060 pixels wide, it overflowed the window when the Android menu was shown, which made a horizontal scrollbar appear on every page. Fixed.

## 2.147.1 — 2026-11-10

- **Remote desktop in full screen: controls that no longer hide the device**: the side panel took up the screen's whole height on a wide opaque band. It is now a small floating panel, four times less bulky, **translucent** — you can make out the desktop below, and it becomes denser as soon as you use it. The session's state and the actions (leave full screen, new tab, close the session) fit on one line, as icons with their name in a tooltip; folded, only a discreet handle remains on the edge.

## 2.147.0 — 2026-11-10

- **Trusted networks on Android**: in the app, a new panel sets where the device stays in touch with loco — **everywhere** (as before, by default), **no mobile data**, or **only on the Wi-Fi networks you approve by their name**. Elsewhere the agent is paused: no contact with the server, sessions stop, wireless debugging is not turned back on; the notification says why, and it resumes by itself back on an approved network. A Wi-Fi network is approved with a button while the device is connected to it, or by its name.
- **What Android requires for the Wi-Fi name mode**: the location permission set to "Allow all the time", and the device's location turned on — Android only gives a Wi-Fi network's name on that condition. loco never reads the position; the permission is only requested if you choose this mode, and the app guides you step by step. Without it, the agent stays paused rather than being active on a network it cannot name.
- **An Android device's page says where it is active**: "active off mobile data only", "active on 2 approved Wi-Fi networks only"… You thus understand why it appears offline at times. The Wi-Fi names stay on the device, and the setting can only be changed on the device.
- **Updating the agent from a device's page**: when a device's agent is older than the server, its page says so ("agent to update") and the *More* menu offers **Update the agent**. The agent is called right away instead of waiting for its next contact, and the action can be followed like the others: successful when it announces the new version, failed after a quarter of an hour with where to look on the device. It works with the agents already installed (Linux, Windows, Android).

## 2.146.2 — 2026-11-10

- **An action no longer stays "sent to the agent" without ever running**: a restart and an update requested one after the other went in the same answer; the agent restarted the device and dropped the update, which stayed "sent" for six hours before failing. Now, nothing else goes with a restart (nor with an update that restarts, nor with the agent's removal): the rest waits for the device to come back, then runs.
- **Lost actions go again by themselves**: when a device never received or never started an action (lost answer, device off, agent restarted), loco sends it again at the next contact — three times at most — instead of declaring it failed after six hours. An action interrupted along the way (device switched off during the action) is reported right away, with its cause.
- **APT cache of the appliance and the Windows server: going out over HTTPS**: in a network that no longer lets HTTP out, the cache could no longer reach the repositories. It now reaches over HTTPS the repositories known to answer there (Debian, Ubuntu, Launchpad PPAs, Zorin, Raspberry Pi), the others over HTTP, and tries the other way when the first one does not answer; the repository's certificate is checked.
- **No more "Release file … is expired" because of the cache**: when a repository no longer answered, the cache served its old index again indefinitely; once the validity date had passed (seven days for Debian's updates), the device's whole inventory failed and its updates were blocked. An index is never served again stale: the device keeps its lists and updates the rest.
- **A faulty repository no longer blocks the others** (Linux agent): a stale index or an unsigned repository among readable repositories no longer prevents the inventory or the update; it is reported separately. The warning on the device's page now gives the cause for each unusable repository, and says when the error comes from loco's APT cache rather than from the device.
- **Automatic update of the Linux agent**: after updating itself, the agent starts the new version right away. Until now it downloaded itself again at each contact as long as the frequent contacts lasted, still announcing the old version.

## 2.146.1 — 2026-11-10

- **Nothing is reserved to the installation's "admin" account any more**: *Administration › Features* (turning whole parts of loco on or off, experimental part included) and the server's **Root shell** are now open to **any active account with the Administrator profile**, whatever its name or origin (local, directory, SSO). The other profiles still have no access to them.
- **Turning the root shell on**: it asks for the password of the signed-in account, no longer that of the "admin" account. A directory or SSO account, which has no local password, cannot turn it on itself — the page explains it — but can use it once turned on by a local account.

## 2.146.0 — 2026-11-10

- **Signing in with a passkey**: a local account can now sign in **without a password**, with its phone, its computer or a security key. The passkey is added in *My account › Security › Passkeys* (the current password is asked again, ten passkeys at most per account), then the sign-in page offers **Sign in with a passkey**. The device verifies the person at each use (fingerprint, face or code): the passkey counts as password and second factor, and loco only keeps its public part. The password stays usable, nothing changes for whoever does not add a passkey.
- **Two conditions, imposed by the browsers**: loco must be opened by a **name** — the one of *System › Network › Portal address*, never an IP address — and its **certificate** must be recognized by the browser (loco's authority installed on the device, or your authority's certificate). Elsewhere, *My account* explains by which address to open loco and the sign-in page does not offer the button.
- **Lost device**: the person removes their passkey in *My account*; an administrator can remove an account's passkeys from *Accounts* (**Remove passkeys**) or with `loco-user passkeys-reset NAME`, and resetting a password also removes the account's passkeys. Each addition, removal and sign-in with a passkey is noted in the audit log. Directory and SSO accounts keep their provider's rules.

## 2.145.0 — 2026-11-10

- **Android remote screen without going back through the app**: until now, it was enough to restart the loco app, or the device, to have to go back and tap "Turn on the remote screen". Now, **a single permission is enough**: once remote control (accessibility service) is turned on on the device, loco shows the screen at each session — after an app update or a device restart too — without redoing anything. Android limits this reading to about three frames per second; screen sharing, smoother, is still offered. During any screen session, the device shows the "Remote control in progress" notification.
- **Allowing everything by pairing the device once**: when an Android device is paired with loco through adb (*More › adb debugging…*), loco itself gives it, once and for all, what it needs for a smooth remote screen with no gesture: screen sharing with no consent window, control turned on, the block on "restricted settings" lifted, app exempted from sleep. The app also turns wireless debugging back on by itself when Android turns it off (restart, Wi-Fi back). The *Audit log* notes these permissions; *Forget* on adb debugging ends them.
- **"Access denied" when turning control on**: on Android 13 and later, Android blocks the accessibility service of an app installed from a file. The app now explains how to allow it (*Settings › Apps › Agent loco*, ⋮ menu, *Allow restricted settings*).

## 2.144.0 — 2026-11-10

- **Much lighter agent contacts**: the portal now keeps its database open while it serves, instead of putting it away on disk at the end of each request. A simple agent contact costs the server about ten times less, and the remote sessions benefit too. Nothing changes for what matters: enrollments, inventories, commands sent, results, settings and accounts are still written to disk before the answer; only the presence and the measurements of a simple contact — which the agent sends again by itself at the next contact — are put there within the following second.
- **A requested action goes within a few seconds**: when an action is waiting for a device, loco signals it through the channel already open for remote access, and the agent (Linux and Windows) makes contact right away instead of waiting for its two-minute timer. Idle contacts are not multiplied for all that.
- **An alert that does not go out no longer freezes the portal**: an alert's e-mail or webhook is sent without holding the database. A mail server or a webhook slow to answer no longer blocks the agents' contacts or the portal's actions in the meantime. Adding a custom Android app source also reads the repository without holding the database.
- **A device can no longer be taken over with the enrollment key alone**: an agent that enrolls again (reinstallation, change of the server's address) proves it holds the device's token, without presenting it, and keeps its page as before. Without that proof, whoever presents the identifier of an already enrolled device — restored disk, clone, removed device, or an attempt to take its place — is enrolled as a **new device**: the original device's page and token stay intact, and the *Audit log* notes it. An Android device removed from loco and then enrolled again therefore reappears as a new device; after a simple "Leave" in the app, it gets its page back.
- **KDE and GNOME remote desktop on Wayland without a package to install**: on a Debian or Ubuntu device with a KDE Plasma or GNOME session on Wayland, the agent itself installs, once, the components the remote desktop needs. The "Components missing for the remote desktop" message no longer appears when taking over.
- **Linux packages: the fleet's devices are counted again**: the *Linux packages* page always showed 0 Linux devices in the fleet and 0 devices per version. The counters are right, and only count the signed-in account's fleet.
- **Safer rollback of an update**: if the installation of a version fails, the portal is stopped before the previous database is put back, on all systems (it was already the case on the appliance).

## 2.143.0 — 2026-11-09

- **Remote desktop on KDE Plasma on Wayland**: the remote desktop of KDE devices on Wayland now works, without having to open an X11 session. The agent captures the screen through the standard `xdg-desktop-portal` (ScreenCast, PipeWire stream) and injects input through a virtual input device. The first opening asks for a screen-sharing consent on the device (KDE "Screen sharing" window); by ticking "Allow restoring for future sessions", the following openings no longer ask anything. (GNOME and the wlroots compositors such as Sway or Hyprland were already supported; the other Wayland compositors still are not.)

## 2.142.0 — 2026-11-09

- **More responsive agent contacts**: when an action is pending on a device (or one has just run), the Linux agent contacts loco again every ~10 seconds instead of waiting up to two minutes, then goes back to its idle pace (two minutes) once things are calm again. Actions and their sequences (update then restart, successive actions) therefore go almost right away when you are working on a device, without weighing down the idle contacts. The Android agent, which already followed the pace announced by loco, benefits too.
- **A Linux device's inventory shown from enrollment**: a Debian, Ubuntu or RHEL device that enrolls shows **right away** its installed software and its specifications (processor, memory, disk…), without waiting for the end of the first `apt-get update`; the number of updates shows right after. Before, the page stayed "inventory in progress" until the end of the network scan.

## 2.141.0 — 2026-11-09

- **A device's custom name**: in *My fleet*, you can give a device a more meaningful name than its hostname. On the device's page, *More › Custom name…* opens a small window; the name entered shows instead of the hostname in the list, the page and the search, **while keeping the hostname visible** just below. Leaving the field empty goes back to the hostname. New **Name devices** right, granted by default to Administrator and Operator.

## 2.140.0 — 2026-11-09

- **Wayland remote desktop installable offline**: the Linux agent's `.deb` package **now embeds `grim`** (the screen capture of wlroots Wayland desktops — Sway, Hyprland, Wayfire, river). On a device **without access to a repository** that provides "grim", the agent installs anyway and the remote desktop of these compositors works. `grim` is no longer a dependency of the package: the agent uses the system's if it is present, otherwise the embedded binary matching its architecture (amd64 or arm64).
- **Android agent APK always offered at enrollment**: in *My fleet › Enrollment*, *Android* tab, the download button of the preconfigured APK is **always shown**. If the version's APK is not on the server yet — right after an update —, it **downloads by itself in the background** as soon as the page opens, and the waiting message disappears as soon as it is there.

## 2.139.0 — 2026-11-06

- **Android apps** (experimental *Android devices* feature): a catalog of apps to install on your devices from loco, with a new *Android* menu (*Catalog*, *F-Droid repositories*, *Sources*).
  - **Verified F-Droid repositories**: F-Droid and IzzyOnDroid are offered from the start. Nine other vendor repositories can be added in one click: Guardian Project, Bitwarden, Brave, Molly, Session, Threema Libre, KDE, NewPipe and microG. Their key has been checked: the one that signs the index is the same as the one the vendor publishes.
  - **Another F-Droid repository** can be added by hand: loco reads its key and asks to confirm its fingerprint before adding it.
  - **Each index is signed**: loco checks the signature with the source's key and refuses an index older than the last one verified. The indexes are read again every day.
  - **Search** by name, *Discover* selection and categories. An app's page shows the version, the minimum Android, the processors, the size, the license, the signing key, and F-Droid's warnings (ads, tracking…).
  - **Google Play**: Google does not allow any third-party tool to install its apps. A Play app is added to the catalog by its link or its package name; at deployment, the device opens its page in the Play Store and you tap *Install*. Google Play then takes care of the updates.
  - **Deployment** from the catalog, a device's page (*More › Install apps…*) or *My fleet › Deployments* (new *Android apps* type), with device groups and waves as for the other deployments.
  - **Installation**: each device receives the version compatible with its Android and its processor. loco downloads the APK from the source and checks its fingerprint, recorded in the signed index; the device checks it too, with the signing key, before handing it to Android.
  - **Confirmation**: a first installation is confirmed on the device, in Android's *Install* window, opened by the loco app or by its notification. On Android 12 or later, updates of the apps loco installed happen without asking anything.
  - **Follow-up**: a device's page shows, for each catalog app, the installed version, the store that installed it and the available version. The catalog shows the devices to update.
  - **New rights**: *View Android apps* and *Manage Android apps and sources*. Installing stays the *Deploy software* right.
- **APK ready to enroll**, in *My fleet › Enrollment*, *Android* tab: like the Windows MSI, the server's address, the enrollment key and the authority's fingerprint are **engraved in the APK**. Opened on the device, the app enrolls **on its own**, with nothing to enter or confirm. With a device group, the device joins it.
  - The configuration is placed in the APK's *signing block*: **its signature stays valid** (no re-signing), and the app can update as before. If the fingerprint received does not match the engraved one, automatic enrollment is refused and the app falls back to manual entry.
  - The **generic APK** (address and key to enter by hand) is still offered, folded under *Generic APK and manual entry*.
- **Android app 2.139.0**: new *Installs* card, to allow loco to install apps; the app is now called **"Agent loco"** ("loco Agent", "Agente loco"), to set it apart as the agent of the other systems.
  - **New look**, in the image of the portal: same colors, same font (Bricolage Grotesque, and IBM Plex Mono for addresses, fingerprints and versions), same panels, buttons and status badges. The default appearance is *Mist*; in dark mode, the app switches to the "soft dark" theme. The enrollment screen and the list of permissions on the device have been redone.
  - It **updates itself** from loco at the following versions, outside an ongoing remote session; after an update, the remote screen has to be turned on again.
  - It **starts again by itself** after a device restart or an update.
  - **To install once by hand** from the enrollment, over 2.138.0: 2.138.0 could not update itself.
- **Remove the agent** from an Android device's page now removes it from loco; the app stays installed.
- **adb terminal** for Android devices: a real `adb shell` terminal, with the rights of Android's "shell" user (`pm`, `am`, `settings`, `input`, `logcat`, `dumpsys`…), much wider than those of the app's terminal.
  - **Opening**: *adb terminal* button on the device's page, or *More › adb debugging…*.
  - **Connection**: loco connects to the device itself, over the local network, like a development computer.
  - **Wireless debugging** (Android 11 and later): you enter in loco the port and the pairing code shown by the device, only once. The loco app then tells loco the connection port, which changes at each activation.
  - **Classic adb port**: direct connection for a device that already listens (5555).
  - **AI assistants**: new MCP tool `adb_shell`.
  - **Rights and security**: new *Open an adb terminal* right. Sessions, pairings and commands are logged. loco keeps its own adb key and its adb server listens on no network port.
  - **adb on the server**: included in the appliance, installed by loco when the distribution provides it (`adb` package, or `android-tools` on Rocky with EPEL). No adb terminal on a Windows server.
- **Unlocking an Android device remotely** (*Unlock* button on the page of a device paired over adb, and a tool for AI assistants): loco wakes the screen and replays the **pattern**, the **PIN** or the **password** you enter — through adb, without touching the device.
  - **Pattern**: you draw it on a 3×3 pad in the portal. As the lock screen cannot be shown (Android's secure surface), the 9 dots are computed from the resolution; a **manufacturer profile** (Pixel/AOSP, Samsung, Xiaomi, OnePlus…) serves as a starting point, and a per-device **calibration** adjusts it if the pattern does not go through. **PIN** and **password** need no calibration.
  - **AI assistants**: new MCP tool `adb_unlock` (pattern, PIN or password).
  - **Security**: *Open an adb terminal* right required (adb access already allowed injecting these gestures). The secret entered is never logged. Relies on the wireless debugging already allowed on the device.
- **Controlling an Android device's screen through adb** (*Control the screen* button, once the device is unlocked): loco streams the screen and injects the gestures — click to touch, drag to swipe, *Back*, *Home*, *Recents*, *Power/sleep* keys, and text input. **No MediaProjection dialog.** The lock screen, a secure surface, cannot be shown (hence the blind unlocking above).
  - **Low-latency stream**: loco starts scrcpy-server (video only) on the device and relays the **raw H.264**; the browser decodes it with **WebCodecs** (hardware decoding, nothing to transcode on the server). **Automatic fallback** to an MJPEG stream (`screenrecord` + ffmpeg) for browsers without WebCodecs or if scrcpy fails. scrcpy-server (Apache 2.0) is included; ffmpeg is only needed for the MJPEG fallback.
  - **Visible safeguard**: as long as loco drives the screen, the device shows a **"Remote control in progress"** notification, and contacts loco more often to make it appear and disappear quickly.
- **The Linux agent's `.deb` package installs grim**: grim was only recommended, it becomes a dependency. The remote desktop of a Wayland compositor such as Sway or Hyprland therefore works from the installation, even with `--no-install-recommends`. For an agent already in place, install grim by hand if needed.

## 2.138.0 — 2026-11-06

- **Android devices** (experimental, off by default): manage your own Android phones and tablets from loco, **without a QR code** at installation.
  - **Turn on** in *Administration › Features*, *Experimental* section: *Android devices*. Reserved to the `admin` account created at installation.
  - **Install and connect**: *Android* tab of the enrollment, download the app (`loco-agent.apk`), enter the server's address and the enrollment key, then confirm the authority's fingerprint. No Google account.
  - **Screen, terminal, files** as for a computer: see and drive the screen (touch, keyboard, mouse; *Back*, *Home*, *Recent apps*, *Notifications* buttons), a terminal with the app's rights (no root), and the file manager. AI assistants (MCP) have the same actions.
  - **With your consent, on the device**: the app stays visible and each capability (screen, control, files, background) is turned on by hand. Nothing is captured outside a session opened from the portal.
  - **No system updates or software catalog** on Android. Details: [docs/android.md](docs/android.md).

## 2.137.1 — 2026-11-05

- **Appliance: the Windows Update proxy starts again after a restart.** Its folder in `/run` was only created at installation: at each restart, the service stayed failed and port 3128 closed. The DNS cache and the rest of the portal were not affected. The update is enough.
- **Appliance installation**: the full test now checks all the services at the second boot, not only the portal.

## 2.137.0 — 2026-11-04

- **Blocking an update**: new *Blocks* tab in My fleet, or *Block* on an update's row in a device's page. A package (Linux) or a KB or a title (Windows), with "*" as a wildcard, on the whole fleet or a device group, with a reason.
  - **Respected everywhere**: device page, whole fleet, waves, schedules, AI assistants. A block set after a request applies as long as the device has not received it. An agent too old to respect it gets the command after its automatic update.
  - **On the device**: Debian and Ubuntu hold the package (`apt-mark hold`), which also applies to their automatic updates; a hold set by hand is never touched. Windows hides the update, except those it enforces. With DNF, only the updates started by loco are concerned.
  - **Always visible**: a blocked update is no longer counted as pending, but is still flagged on the page, and the page shows the devices where it is waiting. *Block updates* right, reserved by default to administrators and to the profiles that cover the whole fleet.
  - Flatpak and catalog software are not concerned.
- **User approval before the remote desktop**, off by default. The person in front of the device sees an *Accept / Refuse* window before anything is captured.
  - **Required on a device**: device page, *Details* tab, or selection in My fleet. A separate right, *Set approval before the remote desktop*: a technician who opens the desktop cannot remove it. Also applies to AI assistants, which the window names as such.
  - **For a single session**: *More* menu of the page, *Remote desktop, with the user's approval*.
  - **No answer within a minute, or a locked screen, counts as a refusal**, explained to the administrator. If nobody is signed in on the device, the desktop opens without asking, and the audit log says so. The terminal and the files are not concerned.
  - **An agent too old does not open without approval**: the session is refused until its automatic update.
  - **On Linux**, the window uses zenity or kdialog, present on the common desktops. Without either, the desktop is refused with this explanation.

## 2.136.0 — 2026-11-03

- **New default appearance**: *Soft dark* theme, without an accent color, with a photo of ridges in the mist (*Mist* preset). *Midnight* and *Alps* stay available.
  - **On an existing server**, it applies to the sign-in screen and to the accounts without a saved appearance, unless an instance appearance has been chosen. The accounts that saved theirs keep it.
- **The sign-in screen can have its own appearance**: in *Instance appearance*, it follows the default appearance or gets its own. From the *Appearance* window, *Apply to sign-in* gives it the one you are trying, without touching the accounts' default. Reserved to the right to configure the server.
- **New *Forest* preset** with a new *Fir* theme.
- **Fixes**: in the *Appearance* window, the close cross and the *No background* thumbnail were empty, and the presets' labels could lack contrast.

## 2.135.0 — 2026-11-03

- **The agent can enable wake**: *Enable wake* button on a device's page and for a selection in My fleet, on its Ethernet cards.
  - **On Linux**, the agent applies it again at each contact: most cards go back to "disabled" at each restart. The device's network configuration is not changed, and the `ethtool` command is not needed.
  - **On Windows**, the setting persists. The card may restart at the moment of the change: a few seconds of interruption, a single attempt per day in case of failure.
  - **If the driver or the system refuses**, the page shows the error.
  - *Stop enabling it* turns wake off and stops maintaining it. The BIOS still has to be set by hand: the agent has no access to it. Nothing is enabled without a request.

## 2.134.0 — 2026-11-03

- **Remote wake (Wake-on-LAN)**: *Wake* button on a device that is off, in My fleet and on its page, for one device or a selection. Also available through the MCP server (`wake_device`).
  - **Only offered when it has a chance to work**: the device had a connected Ethernet card at its last contact, wake is not disabled on it, and something can broadcast on its network. Never over Wi-Fi, where a device that is off no longer listens.
  - **Sent by the server** if it is on the same network, otherwise by a device that is on in that network at its next contact, since a wake-up packet does not cross routers. A relay that comes back too late no longer wakes anyone up unexpectedly.
  - **Result followed** in the device's history: back online, or not back after 10 minutes, with what to check.
- **Network cards on a device's page**: type (Ethernet, Wi-Fi), connected or not, MAC address, network and wake state. The page also says why wake is not available. An agent 2.134.0 or later is needed.
- **New *Wake devices* right**, granted to the Operator profile.

## 2.133.0 — 2026-11-03

- **Certificate signing request (CSR)**: the page asks for the full name (FQDN), shows the requested SANs and warns that the authority must keep the IP if the agents use it. A short name like `loco` is no longer silently dropped.
- **Importing an enterprise certificate**: the leaf alone is accepted, with the intermediate as a complement. The three errors are distinct and say what to fix: missing intermediate, missing SAN (with the list of what the certificate covers) and wrong authority.
- **Linking an administrator account to its SSO identity**: *My account › SSO sign-in*. The link is made by a real pass through the provider, from an account already signed in. Nothing is matched automatically by name or e-mail.
  - The password and the rights do not change, and the account's two-factor authentication is still required.
  - Deleting or disabling the provider does not cut off the local account.
  - An identity already linked to another active account is refused. An access request still pending for that identity is replaced.
- **Profile suggested from a group**: for each OpenID Connect provider, `group = profile` lines. The profile is preselected when approving a request: nothing is granted without approval.

## 2.132.0 — 2026-11-03

- **Configuring Authentik from loco**: button in *Directories and SSO*.
  - **With a temporary API token**: loco creates or updates the provider and the application, without duplicates: confidential client, `authorization_code` only, strict redirect URL, `openid profile email` scopes, RS256 signature. It then fetches the client ID, the secret and the issuer, and starts the check.
  - **The token** is neither stored, nor logged, nor passed as a command argument. Between two steps, it stays with the browser, encrypted and limited to ten minutes. Delete it in Authentik once done.
  - **Certificate first**: the token is only sent once Authentik's certificate is recognized, or its authority confirmed by its fingerprint. If the server does not send the root, the page asks you to paste it.
  - **Existing application**: the changes are shown before being applied. The client ID and the secret do not change. A redirect URL is only deleted if you tick it. If Authentik changed in the meantime, nothing is applied.
  - **Without a token**: a ready-to-paste blueprint, plus the values to enter. The secret is still generated by Authentik.

## 2.131.0 — 2026-11-03

- **Internal names managed from the portal**: *System › Network › Internal names*. When the server's DNS does not know a name, such as Authentik's address on an internal network, give its IP address, without opening a shell.
  - **Suggested by the diagnosis**: when "Check configuration" fails on DNS, a field lets you add the name on the spot, then the check starts again.
  - **Kept**: the list is in `/etc/loco`, therefore backed up and replicated in high availability. The dedicated block of `/etc/hosts` is redone if the file is rewritten, by an update or by `setup-loco`.
  - **Nothing else changes**: `RESOLVERS` and `resolv.conf`, used by the Windows Update cache, are not touched. Under Docker and on a Windows server, the page explains how to do it: `extra_hosts`, or the `hosts` file.

## 2.130.0 — 2026-11-03

- **"Check configuration" button for OpenID Connect providers**, in *Directories and SSO*. It also runs by itself when saving. Without a user signing in and without creating an account, it checks seven steps from the server and gives for each its state and its fix: name resolution, connection, certificate, discovery, signing keys, client and secret, sign-in request. No more need to read Authentik's logs.
  - **Certificate**: the message names the missing intermediate or root and gives the fingerprint to check before pasting it. A name not covered, an expired certificate or a pinned certificate that changed are flagged separately.
  - **Undeclared redirect URL**: the URL to declare is shown, ready to copy.
  - **"code" flow not allowed**: `invalid_request`, "The request is otherwise malformed", is finally explained.
  - **Wrong secret**: detected with a fake code, without obtaining a token. As Authentik also answers `invalid_client` when the redirect URL is not declared, the secret is not blamed then.
  - **Other providers**: an unknown answer gives "inconclusive", never "failure". No secret is shown or logged.
- **Sign-in refused**: the page tells administrators where to find the cause. The detail stays reserved to those who have the right to manage SSO.

## 2.129.0 — 2026-11-03

- **Portal address separate from the agents' address.** New setting in *System › Network › Portal address*: opening loco by its name (`https://loco.lab.local:8443`) no longer requires running the installer again with `--host`, which also changed the enrollment address. The agents and the enrollment stay on their address. Without the setting, nothing changes.
  - **Editable without reinstalling or restarting**, from the portal or with `loco-config portal-url`, now present on every Linux server. The setting survives updates and the root agent validates it again. Under Docker: `LOCO_PORTAL_URL`. On a Windows server: `install.ps1 -PortalUrl`.
  - **The certificate must cover the address.** Otherwise the change is refused. For a loco certificate, a box adds the name to the certificate. For an enterprise certificate or one provided at installation, the message says what to request.
  - **SSO sign-in**: the return always happens on the portal address. From another address, such as the IP, the browser is redirected there before the cookie is set. The false "sign-in expired" message disappears, and a return on a wrong address says what to fix. No URL is built from the Host header.
  - **Redirect URLs to declare again**: the page lists them for each provider and asks for confirmation before the change.
- **High availability: renewing the certificate keeps both servers.** Renewal from the portal removed the addresses of the cluster's two servers from the certificate, keeping only the virtual IP.

## 2.128.0 — 2026-11-03

- **Deployment in waves.** An update of the fleet or of a group, a deployment of software or packages can go in waves instead of touching all the devices at once. You choose the number of devices per wave, the wait between two waves, the number of failures that pauses everything, and optionally a pilot group to start with.
  - **A wave only goes on proof**: at least one success in the previous one and fewer failures than the threshold. Otherwise everything stops: the following devices receive nothing, the overview flags it, a notification goes out, and it is up to you to decide to start the next wave or to cancel the rest.
  - **A device that is off does not block**: its command waits for it and it will follow when it is back. The online devices go first, as they answer quickly. If no device of a wave has answered, nothing goes on its own.
  - **At the start of each wave**, a device removed from the fleet, or that has nothing left to update, is set aside.

## 2.127.3 — 2026-11-03

- **Commands without an answer**: a command sent to a device that never comes back now fails after 6 h. It stayed shown indefinitely, since only a contact from the device could close it.
- **My fleet: *Offline* and *Restart needed* filters.** They appear when they concern at least one device, and those two lines of the overview lead there, with the same count.
- **Plurals**: no more "device(s)" or "update(s)" in the portal, the audit log, the agents and the console, in French, English and Spanish. The results already saved stay readable.
- **Status light tooltip**: it no longer repeats "up to date: up to date" or "incomplete inventory: incomplete inventory".
- **Overview**: the processor shows an average between two refreshes instead of a very unstable 120 ms measurement, an empty cache no longer appears in *Storage* ("APT cache 2 B"), and the load is written with the same decimal from loading.

## 2.127.2 — 2026-11-03

- **Broken repositories finally detected on Debian and Ubuntu devices.** `apt-get update` answers "success" even when no repository answers, so the agent saw nothing and an update could end in success without installing anything.
  - **No readable repository**: the device goes to *Unknown state* with the cause, and an update stops in failure instead of claiming to have succeeded.
  - **One broken repository among others** (abandoned third-party repository, for example): the status light does not change and the updates go on, but the device's page flags that the updates of that repository are not counted.

## 2.127.1 — 2026-11-03

- **Fixed: after a change of the server's IP, all of a Linux device's repositories went down.** The agent reached the new server, but the APT proxy (`01loco-proxy`) and the HTTP sources kept the old address: the device no longer read any repository, official repositories included. The agents now update the APT proxy, the APT and RPM sources and the Flatpak repositories, over HTTP as over HTTPS, when they adopt a new address. The note of 2.125.0 about following a new IP was therefore incomplete.
- **Still to fix by hand**: the devices that had already changed address before this version, and those configured by `setup.sh` without an agent: run `setup.sh` again once.

## 2.127.0 — 2026-11-03

- **My fleet is paginated**, by 50 devices, the most urgent first. The navigation only appears beyond 50 devices and keeps the filters.
- **The device search covers the whole fleet**, not only the page shown: name, system, any address of the device, group, ignoring case and accents. It survives the refresh and can be shared by its address.
- **The device selection follows the page change.** The bar says how many selected devices are outside the page; "Select all" covers the page shown.
- **Fixed: "Update everything" announced a wrong number when a filter was active.** The counter followed the filter while the action covers the whole group or fleet: with the Windows filter, Linux devices were updated too. The counter now says what will really be done.

## 2.126.1 — 2026-11-03

- **Wrong address shown for a device that has Docker.** My fleet could show the address of the Docker bridge (`172.17.0.1`) instead of the network card's. The portal now first shows the address through which it sees the device, and the Linux and Windows agents put first the card of the default route, the virtual interfaces last (Docker, virtual machines, Hyper-V, WSL) and IPv4 before IPv6. All the addresses are still listed on the device's page.

## 2.126.0 — 2026-11-02

- **Overview redone to answer first "is everything fine, and what do I have to do?".**
  - **Overall state at the top**: "Nothing needs attention" with what was checked, or the list of what requires an action, the most serious first, with a link to the page where to act. Stopped services, certificate, backups, syslog, disk, security updates, failed updates: everything appears there, even if the matching notification is disabled. The list refreshes by itself and follows each account's rights and fleet. A device that is simply off does not appear there.
  - **Fleet just below**: each state opens the filtered list, the states at zero stay discreet, and the updates in progress, restarts needed and offline devices are shown.
  - **Server on a single band**: processor, memory, disk and network. The disk's figure and its bar finally say the same thing, and the network curve survives reloads and going back and forth in the portal.
  - **Storage in a single table**, sorted by size, with a link to each cache. It replaces the cache tiles and "Data disk", which overlapped.
  - **Removed from the page**: the 8 tiles, the services table (the detail stays in *System*, a stopped service comes up in the overall state) and the second Windows Update percentage, which could contradict the chart's.
- **Alerts**: the messages no longer write "update(s)" or "day(s)" for the security updates, the certificate and the failed logins.

## 2.125.0 — 2026-11-02

- **Changing the web interface's port** from *System › Network*. The new port opens next to the old one: neither the administrator nor the devices are cut off. The agents learn the new port at their next contact, the page counts those that know it, and the old port only closes from the new one, which proves it is reachable, firewall included. Unusable values are refused before any change: port already taken, port reserved to loco, port that browsers block. A failure restores the previous configuration. Docker and Windows server: the port stays set at installation, the page explains it.
- **The Linux agents follow the server's address down to their repositories.** When an agent adopts a new address, new IP included, it updates its APT and RPM sources and its Flatpak repositories installed over HTTPS. Until now they stayed on the old address.
- **High availability: the port is no longer assumed to be 8443.** The health check and the exchanges between servers use the configured port, and a standby can join a primary on another port (`address:port`). The port is changed outside high availability.

## 2.124.2 — 2026-11-02

- **Removing an application from the local catalog, straight from the list.** Until now, it was only possible from the *More* menu of its page. A *Remove* button appears on each row and next to *Retry* when the download failed, for example if a firewall blocks the source site. Tick several applications to remove them at once, including those in error, which could not be ticked until now; *Deploy*, *Create a group* and the PowerShell command stay limited to the ready applications. Nothing is uninstalled from the devices.
- **The catalog's selection bar fixed on a photo background**: it stayed partly visible without a selection and stuck to the bottom of the page instead of the screen's, out of sight in a long catalog.

## 2.124.1 — 2026-11-02

- **Overview: the latest additions can be clicked.** A Windows application opens its page; an APT package, which has no page of its own, opens its repository already filtered on that package. The whole row can be clicked, as in My fleet.

## 2.124.0 — 2026-11-02

- **Sending logs to a syslog server or a SIEM** (*Administration › Syslog forwarding*). As you choose: **loco's audit log**, the **devices' logs**, or both. RFC 5424 format, over TLS (recommended, with your certificate authority), TCP or UDP, and a button to send a test message.
  - **Devices**: journald on Linux, event logs on Windows, at the chosen minimum level, with the security events if you wish, on the whole fleet or device groups. The devices go through loco: no network opening to the syslog server. Agents 2.124.0 and later (automatic update).
  - **Syslog server missing**: sending resumes where it had stopped, without storing anything on the loco server. An event may arrive twice after an outage.
  - **Follow-up**: last sending, last failure and each device's state on the page; new *Syslog forwarding failing* alert rule after 5 minutes.

## 2.123.1 — 2026-11-02

- **The *My fleet › Updates* page redone**: a single list in aligned columns (device, action, state, date, author), grouped into *In progress*, *Pending* and *Finished*. On a phone, each row stacks.
- **Failures finally appear on this page**: failed updates never appeared there (wrong state in the query). They now come first among the finished ones, with the packages concerned or the error line, instead of the last line of the log.
- **The list of packages really shows live**: it did not appear if the page had been opened before the first package (device page and timeline). Expanded or collapsed lists stay in that state when the page reloads.
- **"Pending" tells the truth**: a device that is off no longer shows "less than 2 min" but *Device offline, seen on …, will go when it is back*; a command in progress or sent to a silent device shows *No answer from the device*. *Cancel* button right on the row, and the tab's badge no longer counts the offline devices or the upcoming schedules.
- **Durations visible on the page**: a pending action does not expire; the finished ones stay 24 hours (instead of 30 minutes, too short to follow a wave), then in the device's history.

## 2.123.0 — 2026-11-02

- **Progress of updates package by package, live.** During an update, you now see each package (or each Windows update) go by with its outcome: **installed** (✓), **in progress**, or **failed** (✗), plus a summary "X installed · Y failed · Z in progress". Visible on the device's page and on the *My fleet › Updates* timeline, refreshed every 2 s without reloading.
  - **APT**: unpacking → in progress, *Setting up* → installed, dpkg error → failed. **DNF/YUM** and **Windows Update**: one entry per update with its result. **Flatpak**: a global entry.
  - The list can still be viewed afterwards in the device's history (next to the log), to see again what succeeded or failed.

## 2.122.0 — 2026-11-01

- **Global timeline of the fleet's updates.** New *My fleet › Updates* page: following, in one place, all the fleet's updates — **in progress** (live progress, bar per device, refreshed every 2 s without reloading), **pending** the next contact, and **recently finished** (success or failure with the reason). Each row leads to the device's page.
  - **Adapted to rights**: each person only sees the devices of their fleet (same scope as My fleet); a tab with a **badge** shows the number of updates in progress or pending.
  - *Update the whole fleet* leads straight to this timeline to follow the wave started.

## 2.121.1 — 2026-11-01

- **Restarting from My fleet stays on the list.** A device's *Restart* button no longer sends you to its page: a message confirms the restart is requested and the list stays displayed. While the restart is pending, the badge goes from "restart needed" to "restart in progress".

## 2.121.0 — 2026-11-01

- **Built-in high availability.** Two loco servers, a single *virtual IP*: the primary serves the portal, the repositories and the agents; the standby replicates everything continuously and takes the IP automatically if the primary goes down (keepalived / VRRP). *Administration › Server › High availability*.
  - **Guided setup**: on the first server, "Enable" asks for a free virtual IP and shows a single-use **pairing code**; on a second, new server (same version), "Join" with the primary's address and that code copies the whole configuration and the repositories, then arms the failover. loco sets its address to the virtual IP itself and extends its certificate to the virtual IP and the two servers.
  - **Safe replication**: configuration, database and repositories are transferred encrypted (AES-256-GCM) between the servers, authenticated by a cluster secret that the portal never sees (it only keeps its fingerprint). The caches are not replicated (they rebuild themselves). During an update, the health check does not trigger a failover.
  - **Monitoring and failover**: the page shows each server's role, which one holds the virtual IP, how fresh the replication is, keepalived's state and the latest events. *Fail over to the standby* allows a deliberate failover (planned maintenance); the standby is passive (read-only) as long as it does not hold the IP.
  - Unavailable in a Docker container; `keepalived` is included in the appliance and installed on demand on Debian/Ubuntu and Red Hat.

## 2.120.2 — 2026-11-01

- **Restart icon**: circular arrow (the one of "restart needed") instead of the power symbol, which suggested a shutdown — *Restart* button of My fleet, the server's restart button and banner.

## 2.120.1 — 2026-10-31

- **My fleet without a horizontal scrollbar**: the table fits in the page's width, even with long system names. System shown in a short version ("Windows Server 2025 Standard", "Debian 13 (trixie)", full name in a tooltip), groups under the device's name, last contact under the address, *Restart* button as an icon; on a narrow screen, the resource bars tighten and the buttons go one under the other.

## 2.120.0 — 2026-10-31

- **A device's updates one by one, with live progress.** On a device's page, the *Updates* tab offers a box per system update (APT or RPM package, Windows update) and a *Flatpak applications* line; *Update the selection* only applies those, with an optional restart. *Update* still does everything.
  - **Live**: the action's line shows the step (preparation, download, installation), the package or update in progress and a progress bar, read again every 2 s without reloading the page; in the table, the finished items are ticked and the one in progress spins. The page reloads at the end with the inventory up to date, and not while you are ticking.
  - **Linux agent**: apt's output (`APT::Status-Fd` status lines) and dnf's read as it goes; a selection goes through `apt-get install` (new packages of an update included) keeping their "automatically installed" mark, or `dnf upgrade <packages>`.
  - **Windows agent**: each update reports its Windows Update identifier; installation one by one (servicing stack first), with the step reported, and a failed update no longer prevents the following ones.
  - One-by-one choice reserved to agents 2.120.0 and later (an old agent would do everything); selection limited to the updates announced by the inventory.
  - **MCP**: `update_device` accepts `updates` (package names, titles, KB numbers or identifiers) and `flatpak`; `get_device` gives the identifiers and the progress of the actions in progress.

## 2.119.2 — 2026-10-31

- **A device's page**: a pending action (update, inventory, restart…) shows on a discreet line — colored dot, action, "at the next contact", *Cancel* link — instead of a large alert box.

## 2.119.1 — 2026-10-31

- **Plainer terminals**: under the root shell and the devices' terminals, the shortcuts (copy, paste, search, text size…) and the remark about closing the session fit in a "Shortcuts and help" block folded by default.
- **Root shell**: the service listens on its socket as soon as it is created; a connection arriving right at startup is no longer refused.

## 2.119.0 — 2026-10-30

- **Instance appearance** (*Administration → Appearance*, *Configure the server* right): a company adapts loco to its brand without removing the shipped themes. At installation, everything stays offered as before.
  - **Identity**: logo (moved here from the personal Appearance window), **company color** — offered as an accent color, with the text laid over it in black or white depending on its lightness — and **company background**, an image offered among the backgrounds and also served on the sign-in screen.
  - **Default appearance**: theme, color, background and opacity of the new accounts, of the sign-in screen and of *Reset* (until now set to "Alps").
  - **Choices left to each person**: themes offered (for example a light one and a dark one), accent color and background **free or imposed**. Each account's Appearance window only shows what is allowed; a setting outside the frame is replaced on display by the default appearance, without being erased. *Original settings* restores all the choices. Audit log: Server category.

## 2.118.0 — 2026-10-30

- **Enrolling a device straight into a group.** At the top of **Enrollment**, *Enroll devices into the group* switches the MSI, the .deb/.rpm packages, the commands and the mass-deployment command (GPO, Intune) to the **group key**: the device enrolled with them joins that group (a device already enrolled joins it too, without leaving its own). No agent to update — the group key (`g<id>-…`) takes the place of the server's.
  - By default, no group; *Enroll devices into this group* in the ⋯ menu of each group opens the page on it.
  - An account limited to part of the fleet only enrolls into the groups assigned to its profile, never without a group (the device would leave its fleet): its first group is preselected, and a limited profile without a group no longer receives the server key.
  - *Change this group's key* (*Configure the fleet* right) voids its files and commands already handed out; deleting the group does too. Audit log: enrollment with its group, "New enrollment key for a group".

## 2.117.0 — 2026-10-30

- **Profiles limited to part of the fleet.** In **Access profiles**, a profile covers **the whole fleet** (by default, and always for the built-in profiles) or **chosen device groups and devices** — for example "Building B technicians". Devices added later to these groups come in by themselves; the list of profiles shows each one's fleet.
  - The profile's accounts only see and target these devices: My fleet, device page (not found otherwise), search, overview, groups reduced to their devices, grouped updates, deployments, scripts and history, schedules, remote sessions — and the **MCP tokens**, which follow the creating account's fleet.
  - A schedule applies its author's fleet at each occurrence; a limited profile cannot target "Whole fleet".
  - Managing device groups and configuring the fleet stay reserved to the profiles that cover the whole fleet (the groups define the others' fleet); a limited manager only creates or assigns profiles included in their own fleet.
  - Changing a profile's fleet signs its accounts in again; a remote session whose device leaves the fleet closes. Audit log: "profils.parc".

## 2.116.0 — 2026-10-30

- **MCP server: page redone and precise vocabulary.** "AI assistants" becomes **MCP server** (tab, feature, **Create MCP tokens** right, audit log), and an "access" becomes a **token**: the page says what it is — an LLM drives loco over MCP from its client (Claude Code, Cursor, VS Code…), with a token that only opens the enabled tools.
  - **Tokens** first: number of tools and actions, breakdown by area ("Fleet 5 · Remote access 3"), last call with the client and the address, expiry with the year.
  - **Connecting an MCP client**: address, authentication header and Claude Code command, with a `<token>` placeholder — or the real token right after its creation; the JSON file (Cursor, VS Code) is folded.
  - **Token window** on two columns, half as tall: each tool shows its MCP name (`list_devices`, `terminal_run`…), the **action** or **sensitive** marker, and the reason if it is greyed out.

## 2.115.0 — 2026-10-30

- **AI assistants: faster and more complete remote access.**
  - **Sessions open faster**: a device targeted by the assistant (page, action…) is warmed up for a minute and then opens its session in less than 2 s instead of 5; a new PowerShell session is ready in ~2 s (probe sent at a short interval); the assistant's sessions stay open 5 minutes between two calls (2 for the desktop), so the following calls go at once.
  - **Light screenshots**: the picture is kept in memory and, after the first capture, only the changed areas travel — an action that opens a menu only sends back that menu (Zorin GNOME: 0.2 to 0.5 s per following capture). Windows devices now send their screen losslessly compressed (instead of raw pixels) and answer at once when nothing has moved, instead of letting the server wait nearly a second, once their agent is up to date.
  - **Faithful typing on Linux**: the agent types each character with the key and the level of the device's real layout (read from the GNOME session, the X server or the system): `<`, `>`, `@`, `€`, capitals and letters accented with a dead key (`ê`) go through in AZERTY on GNOME Wayland, X11 and wlroots. On Windows, text input goes in one block.
  - **Interactive terminal** (new permission): the assistant types, presses keys (`{enter}`, `{ctrl+c}`, `{esc}`, arrows…) and reads the terminal's screen (120 × 40) — vim, top, yes/no prompts, PowerShell; the session keeps its state from one call to the next.
  - **Longer scripts** in the terminal: up to 64 KB, time limit up to 5 minutes.
  - **A person comes before the assistant**: when a device's session limit is reached (two per type, four for a Windows device), opening a session from the interface first closes the assistant's idle sessions. If its interactive terminal was closed between two calls, its next input is not typed in the new shell: it is told so.
- **Text console of Linux devices without a display**: opening vim no longer closes it (a private terminal sequence, `ESC [ ? 4 m`, crashed the screen emulator).
- **Agents**: a remote session that stays silent spaces out its readings (25 ms → 0.1 s → 0.25 s), instead of querying the server dozens of times per second; the Windows agent reserves JPEG to browsers (noVNC) and sends lossless Tight to the other clients.

## 2.114.0 — 2026-10-30

- **AI assistants: the devices' terminal, files and desktop, live.** Eleven new permissions in **AI assistants**, "Remote access" group, each to tick separately:
  - **Commands in the terminal**: the assistant runs a command in the device's root bash (Linux) or SYSTEM PowerShell (Windows) and reads its output and exit code within a few seconds, without waiting for the agent's next contact; beyond the chosen time limit, the command is interrupted.
  - **Files**: browse, read (text or base64), write (atomic replacement), organize (folder, copy, move, rename, permissions) and delete, with a separate permission for reading, writing, organizing and deleting.
  - **Desktop**: screenshot returned as an image to the assistant, which sees the screen (Windows desktop, GNOME on Wayland, lock screen, text console of a device without a display, zoom on an area), then click, typing, keys and shortcuts (Ctrl+Alt+Del included) and wheel, each action returning a new capture.
- Same sessions as the interface (agent relay, "Open a remote terminal", "Manage device files", "Open the noVNC desktop" rights of the creating account): one session per access and per device, reused from one call to the next and closed after a minute without a call, or as soon as the access is revoked or changed. Commands, writes, deletions and desktop actions go into the audit log (without the text typed).
- Validated in the lab with Claude Code: capture and description of Zorin OS's GNOME Wayland desktop, PowerShell on Windows Server 2025 and Windows 11, files on Windows and Linux, Ctrl+Alt+Del on the Windows lock screen.

## 2.113.0 — 2026-10-30

- **AI assistants (MCP): connect Claude Code, Cursor, VS Code… to loco.** New **Administration → AI assistants** page: create an **access** whose permissions are ticked **one by one** — list and view devices, look for installed software, request an inventory, update one or several devices, restart, install a catalog application, cancel a pending action, read and run the library's scripts, have commands written by the assistant run, server state, audit log. The assistant then drives loco in natural language ("which devices have security updates?", "run uptime on this server").
- **Never more than its creator.** An access is tied to the account that creates it: at each call, it can only do what is ticked **and** still allowed by that account's profile. Account disabled, deleted or to finalize, access expired or revoked: immediate refusal. The actions follow the same path as the interface and go into the audit log with the access's name; a command written by the assistant is kept as is in the scripts' history.
- **Simple to plug in.** The token is only shown once (loco only keeps a fingerprint of it), with the `claude mcp add` command and the JSON configuration ready to copy; expiry of your choice; last use visible (date, address, client). MCP server at `https://<server>:8443/mcp` (HTTP "Streamable HTTP"), compatible with both generations of the protocol: `initialize` handshake (2025-03-26 to 2025-11-25) and 2026-07-28. Validated with the official Python SDKs 1.x and 2.x and with Claude Code on the demo.
- New **Connect AI assistants** right (administrators by default); feature that can be turned off in **Features**.

## 2.112.0 — 2026-10-30

- **Signing in with Authentik (and any OpenID Connect provider).** In **Directories and SSO**, two new types: **Authentik** and **Other OpenID Connect** (Keycloak, Zitadel, Authelia, Microsoft Entra ID…). The sign-in page then shows a **"Sign in with Authentik"** button: password, two-factor authentication and access rules stay managed by Authentik. As for LDAP and RADIUS, a person's first sign-in creates an **access request** to approve in **Accounts**; no local account is matched by its name or e-mail, and disabling the provider immediately closes the sessions of its accounts.
- **Guided setup.** The form shows the **redirect URL** to declare in Authentik (*Copy* button) and accepts the issuer as well as the full configuration address. **Authentik's self-signed certificate** is pasted as is: loco **pins** it (its "*" name does not allow checking the address); an authority of your network is pasted the same way. **Test** makes a real sign-in at the provider and shows the identity received (suggested identifier, name, e-mail, groups) without creating anything, including before activation; in case of refusal, the detail returned by the provider and the settings to check are shown.
- **Security.** "code" flow with **PKCE** (S256), `state` and `nonce` tied to the browser by an encrypted cookie; identity token verified (RSA, ECDSA or EdDSA signature by the published keys, or **HS256 with the client secret** when the Authentik provider has no signing key; issuer, audience, expiry); check of the issuer on return (RFC 9207); no token kept; client secret encrypted like the other directory secrets. Validated end to end with **Authentik 2026.8** (RS256 and HS256 key, pinned self-signed certificate).
- Overview: the disk's "System and other" share is now translated into English and Spanish.

## 2.111.1 — 2026-10-30

- **GNOME Wayland remote desktop on a locked device.** GNOME refuses the remote desktop while the session is locked ("Session creation inhibited") — yet that is the usual case when nobody is in front of the device. The agent **unlocks the session for the time of the takeover, then locks it again automatically at the end** (tab closed, session closed): a locked device is never left open, and an unlocked device is not locked. During the session, the local screen shows the desktop with GNOME's sharing indicator. If the screen locks along the way (inactivity, local locking), the remote session closes with a clear message; just reopen it.

## 2.111.0 — 2026-10-30

- **GNOME remote desktop on Wayland, for good (Zorin OS, Ubuntu…).** Capture through GNOME Shell's "screenshot" API (2.110) is refused by GNOME 46 ("Screenshot is not allowed"). The agent now goes through **the API of GNOME's built-in remote desktop** (Mutter's RemoteDesktop + ScreenCast sessions): live picture through PipeWire (~25 fps), keyboard and mouse injected through the same session — **no configuration, no consent window, no restart**. Validated on a **freshly installed Zorin OS 18.1** (GNOME 46, Wayland): everything needed is there out of the box. During the session, GNOME shows its screen-sharing indicator; the person in front of the device can stop it with one click, and the remote session then closes with a message. For this session, the agent leaves root and acts as the signed-in user. Primary screen only.
- **Faithful keyboard on Wayland desktops (AZERTY, dead keys, AltGr).** The remote desktop server handles noVNC's "physical keys" extension, like the Proxmox console: the device's layout applies as is — `<` `>`, `ê` through the dead key, `@` `€` through AltGr… X11 sessions keep their behavior.
- *wlroots* compositors (Sway, Hyprland…): capture by grim unchanged, now with the physical keys. The "GNOME Shell capture" path of 2.110 is removed.

## 2.110.0 — 2026-10-30

- **Remote desktop on GNOME Wayland (Zorin OS, Ubuntu…).** A GNOME desktop in a Wayland session does not expose the free screen capture (*wlr-screencopy*) that grim uses: the remote desktop failed with "the compositor does not support wlr-screencopy". The agent now switches to **GNOME Shell's screenshot API** (over D-Bus, on behalf of the session's user) — the remote desktop therefore works on GNOME Wayland **without changing sessions or restarting the device**. Keyboard and mouse already went through a virtual input device independent of the compositor. It is slower than an X11 session or a *wlroots* compositor (a few frames per second) and depends on a GNOME version still exposing this API; otherwise, the message invites you to open an X11 (Xorg) session.

## 2.109.0 — 2026-10-30

- **Ignoring an HTTPS certificate refused on an application's source site.** When downloading an installer fails with "Invalid HTTPS certificate on the source site" (certificate expired or misconfigured on the vendor's side), the application's page now offers to **ignore it and try again**, and the *Installer* panel keeps a "Source site certificate: checked / ignored" setting that can be restored at any time. The option only appears when loco knows the file's **SHA-256 fingerprint** (WinGet catalog): integrity stays checked — any altered file is refused —, only the validation of the transport certificate is lifted.

## 2.108.0 — 2026-10-30

- **Sturdier inventory of Debian/Ubuntu devices.** When another APT tool was using the device at inventory time — automatic updates, `apt-daily` task, or the very end of the agent's installation —, `apt-get update` failed with "E: Unable to lock directory /var/lib/apt/lists/" and the device stayed shown as **"incomplete inventory"** for hours. The agent **now waits for the lock to be released** (and redoes the inventory at the next contact if needed) instead of reporting a failure.
- **Distributions derived from Ubuntu/Debian recognized automatically.** The loco repository's configuration now detects the derivatives (Zorin OS, Linux Mint, Pop!_OS, elementary…) through their base (`ID_LIKE`) and keeps the original version name (e.g. Zorin OS 18 → Ubuntu *noble*), instead of showing "Distribution not recognized" and falling back to the APT cache alone.

## 2.107.0 — 2026-10-30

- **Remote desktop: Wayland support.** Remote control of a Linux device only worked on an **X11** session ("Wayland desktop not supported; an X11 session is required." message). It now also handles **Wayland** on *wlroots*-type compositors (Sway, Hyprland, Wayfire, river…): the screen is captured with **grim** and keyboard and mouse are replayed by a **virtual input device** (`/dev/uinput`), independent of the compositor. On Debian/Ubuntu, `grim` is offered automatically when installing the agent (recommended dependency).
- **Clear message on GNOME/KDE.** These Wayland desktops do not allow screen capture without a consent portal: the agent now says so explicitly and invites you to open an X11 session, instead of a generic refusal.

## 2.106.0 — 2026-10-29

- **Neater software center.** The application cards are redone in the style of the rest of the interface: bigger icon, prominent name, readable version, soft shadow and hover effect — no more technical WinGet identifier shown bare on the card (it stays on the application's page).
- **Faster, smarter icons.** The browser cache of the icons goes to a week (the following visits and page changes are instant), and when a page of the center opens, loco **preloads in the background** the visible icons so that they are ready instead of being downloaded on demand.
- **Fix:** the "HTTPS certificate" card of the System page does open the **Certificate** tab (it opened the Network tab).

## 2.105.0 — 2026-10-29

- **Windows Update: sturdier inventory (agent).** When a device refuses the driver search through Windows Update — error `0x80240032` (invalid criteria), seen on some Windows 11 builds or when drivers are disabled by policy — the agent **falls back automatically to software updates only** instead of reporting "incomplete inventory". Software is inventoried and installed normally; drivers stay detected when the device allows it.

## 2.104.0 — 2026-10-29

- **Two style fixes.** The system selector of the *Linux packages* page (Debian/Ubuntu ↔ Rocky/RHEL) no longer spreads across the full width half empty: it becomes a compact two-segment button again (a rule of the *Directories* page crushed it by mistake; the directories' four-choice selector is preserved). And the arrow of the drop-down lists (`select`) becomes a clean **chevron** instead of a small triangle.

## 2.103.0 — 2026-10-29

- **Installable web application (PWA).** loco can now be **installed** as an application (on the desktop or the mobile home screen): app manifest, icons (including a "maskable" icon), opening in a standalone window. A **service worker** caches the interface's shell (faster loading) and shows a neat **"offline" page** if the server cannot be reached. Installation requires trusted HTTPS access (loco certificate accepted).

## 2.102.0 — 2026-10-29

- **Wallpaper: the content sits on a "sheet".** With a wallpaper, the page's content (titles, descriptions, statistics, cards) no longer floats bare on the photo: it rests on a surface, and the wallpaper becomes a clean **frame** around it and behind the navigation bar. Much sharper and more readable. Themes **without a background** (light, plain dark…) keep the content directly on the plain background, clean. The sign-in page keeps its central card.

## 2.101.0 — 2026-10-29

- **Cleaner cards and surfaces (a more "pro" style).** Inspired by a polished dashboard design: softer shadows, slightly more rounded corners, more air in and between the cards, sharper hierarchy. The shadows are **harmonized across all the themes** so that changing theme stays pleasant everywhere (light, OLED, soft dark, midnight, sepia). The wallpaper stays the default; the interface is just calmer and better laid out.

## 2.100.0 — 2026-10-29

- **Interface more "settled" on the wallpaper.** The panels are made a bit more opaque and the background photo is faded more: the interface clearly stands out instead of giving an impression of "floating glass", while keeping the background's mood. The default transparency goes from 80 to 90% (still adjustable in *Appearance*).

## 2.99.0 — 2026-10-29

- **Sign-in screen consistent with the default appearance.** The sign-in page (and the other screens outside a session) now use the instance's default **theme, color, transparency and background** — **frosted glass** card included. Before, an old setting remembered in the browser could show them in a completely different theme (e.g. black + green accent, opaque card) over the background. The small theme selector outside a session, now useless (the appearance is set in the account), is removed.

## 2.98.0 — 2026-10-29

- **"Floating" header.** The navigation bar no longer spreads across the whole width at the top: its items are gathered in a **rounded** block, detached from the edges, laid over the content (and the wallpaper). Lighter, more modern.
- **Readability on photo backgrounds.** The text laid directly on the background (page headers, statistics, sign-in footer) gets a slight halo in the theme's color to stay sharp on a photo — in all the themes, light as dark. The sign-in page also shows the wallpaper, with its opaque central card.
- **Fix:** choosing "No background" in *Appearance* does remove the wallpaper (it came back by mistake to the default background since 2.97.0).

## 2.97.0 — 2026-10-29

- **Friendlier default appearance.** At installation, loco adopts the **"Alps"** preset (night-blue theme, blue accent, mountain photo as background, frosted-glass panels) instead of plain black. Each user stays free to change theme, color, transparency and background, or to go back to a sober theme, from *Appearance*.
- The repository's screenshots redone in this new style.

## 2.96.0 — 2026-10-29

- **Customizable logo (global).** In *Appearance*, a **Logo** section reserved to administrators lets you replace loco's logo with your organization's — **for everyone**: header, sign-in page and favicon. Image (PNG, JPEG, GIF, WebP) or **SVG**, 2 MB maximum; back to loco's logo in one click. When a logo is set, the word "loco" fades away to leave room for the chosen brand.
- Unlike the theme, the color and the background (specific to each account), the logo is **global** to the instance. The SVG is served as an image (no script can run) and uploading it is reserved to the "Configure the server" right.

## 2.95.0 — 2026-10-29

- **4K photo wallpapers (nature).** The *Appearance* window offers eight real high-definition photo backgrounds — **Mountain, Forest, Desert, Dunes, Ocean, Aurora, Starry night, Winter** — in addition to the plain colors and gradients, now in a separate section. Three photo presets (Alps, Boreal, Desert) highlight them.
- **Readability guaranteed in all the themes.** A veil matched to the theme's color and frosted-glass panels make any combination stay readable: **light** theme on a mountain, dark theme on a starry sky, sepia on a winter landscape — the text always stands out. Choosing a photo automatically lowers the transparency if the panels were opaque, so that you see the background right away.
- Photos under the Unsplash license (free use) — credits in `static/wallpapers/CREDITS.txt`.

## 2.94.0 — 2026-10-29

- **Customizable appearance.** New *Appearance* window (account menu → "Appearance…") to compose a portal to your liking, **saved on the account** (you find your setting again in any browser):
  - **5 themes**: OLED, Soft dark, Light, and two new ones — **Midnight** (night blue) and **Sepia** (warm paper).
  - **8 accent colors** (**teal** and **amber** added).
  - **Adjustable panel transparency**: the cards become translucent and let the background show through.
  - **Wallpapers**: six gradients provided (Aurora, Ocean, Forest, Dusk, Pink, Slate) or **your own image** to import (PNG, JPEG, GIF, WebP, 8 MB max).
  - **6 ready-to-use presets** (OLED, Light, Ocean, Forest, Dusk, Pink) that set theme, accent, transparency and background in one click.
  - Everything applies **live** and **without flickering** at load (the theme is rendered on the server side). Outside a session, the choice stays remembered in the browser.

## 2.93.0 — 2026-10-29

- **Windows Update: the "Directing the devices" section decluttered.** The two methods (DNS, proxy through the agent) fit in two short, balanced columns; the detail of the conditions and the manual PowerShell command move into a "Manual setting and proxy troubleshooting" fold-out — no more block of text and overflowing command.

## 2.92.0 — 2026-10-29

- **Fleet settings and Configuration, more polished.** *Fleet settings*: the server's current address moves into a box with its state right next to it, and the new address field is clearly labeled. *Configuration* (appliance): the "enabled / disabled" menus become **switches** (HTTP access, Windows Update cache, Red Hat, Flatpak), each with a short description; and each cache limit shows its **real usage** below.

## 2.91.0 — 2026-10-29

- **Plainer enrollment composer.** The composer's boxes become **switches** (like the Features page): a framed list, a label and its description on the left, the switch on the right, the row greyed out when it is off. The "first installation" option is set apart. The generated command is unchanged.

## 2.90.0 — 2026-10-29

- **Enrollment composer.** The *Enrollment* page becomes a selector: you **tick what the device receives** — **Management (agent)**, **loco repository**, **Updates cache** (and, on the Windows side, **Redirect Windows Update**), with an **All** box — and the **command to run is generated live**, per system (Windows, Debian/Ubuntu, Red Hat). Downloading the package for an offline device and mass deployment (GPO, Intune) stay in "Other methods".
- **Default Debian/Ubuntu repositories.** loco now creates by default the **Debian 12 and 13, Ubuntu 22.04 and 24.04** repositories (like el8/el9/el10 on the Red Hat side): no more need to create them by hand before connecting a Debian device. The *Your repositories* section is reworked — the "Add packages" button moves to the header of the chosen repository.
- **Cache: more meaningful names right after the update, handier folding.** The labels "Windows update", "Repository metadata"… now appear right after a loco update (the agent rebuilds the cache's index at startup), without waiting for a manual analysis. And when the full list is expanded, it stays at a limited height: no more need to scroll down to the bottom to fold it.

## 2.89.0 — 2026-10-28

- **More readable cache list.** In the **Windows Update** and **Linux packages** pages, the list of cached files now only shows the **5 most recent** — the rest expands (and folds) in one click — and each row says **what the file is** ("Windows update", "Microsoft Defender definitions", "Microsoft Edge", "Repository metadata"…), with the file name just below. The .deb and .rpm packages keep their name as is, already meaningful.

## 2.88.0 — 2026-10-28

- **Seeing and emptying the caches.** The **Windows Update** and **Linux packages** pages now list what is really cached — each file (Windows update, Debian or Ubuntu package, Red Hat or EPEL mirror) with its origin, its size and its caching date — and let you **delete an item** or **empty the whole cache** in one click. The pieces of the same file (Windows Update) are grouped into a single object; a deleted file simply comes again from its origin server at a device's next request, nothing is lost.
  - The list can be filtered, refreshes live and stays limited to the most recent objects on a big cache. The analysis and the purge go through the agent (the caches' content is only readable by it); on the APT side, the appliance's nginx cache as well as a Debian server's apt-cacher-ng are supported.
- **Pages reworked.** Plainer presentation: a factual summary (cache size, share served locally, last 14 days) replaces the large tiles, and the cache's content takes the central place.

## 2.87.0 — 2026-10-28

- **My fleet: restarting from the list.** When a device is waiting for a restart, a **Restart** button appears in the list, in the very place of *Update*. If the device also has pending updates, the two buttons sit side by side. The restart is confirmed before sending and warns the signed-in person.

## 2.86.0 — 2026-10-28

- **Sign-in screen redesigned.** A light bar (loco, the server, the language and the theme) and a centered, airier card: title and welcome sentence, more comfortable fields, and a clear **SSO** mention when enterprise directories are active. Errors (wrong password, too many attempts) show in the card, only once. Same presentation for two-factor authentication (with a link to change account) and for choosing the password at the first sign-in.

## 2.85.0 — 2026-10-28

- **Directories and SSO, simpler.** The page (renamed *Directories and SSO*) lists the directories in a table: type, server, number of accounts, **Active / Inactive switch** (disabling closes the sessions of its accounts), Test and Edit, trial order in the menu. The pending access requests are flagged with a link to Accounts.
  - Guided addition by type: **Active Directory** (the domain is enough; controller and service account optional), **FreeIPA** (domain and server; accounts under `cn=users,cn=accounts`, FreeIPA authority indicated), other LDAP directory or RADIUS. The technical settings move into "Advanced options". The server accepts a port (`dc1:3269`, global catalog over SSL).
  - The sign-in screen talks about SSO sign-in with the company account.

## 2.84.0 — 2026-10-28

- **Certificate authority of your choice.** New *System › Certificate* tab with a **loco authority / Enterprise authority** switch: the portal's certificate can be signed by your company's authority (AD CS, internal PKI…), which the domain's devices already trust, and go back to the loco authority at any time.
  - Guided setup in four steps: **import the authority** (.cer, .crt, .pem or .p7b chain); **obtain the server's certificate**, either through a **request (CSR) generated by loco** — the private key never leaves the server, `certreq` command for AD CS provided — or with a certificate and its key (PEM or PFX); **spread the trust** to the devices, with the number of devices ready; **switch**.
  - **No interruption for the fleet**: the agents (2.84 and later) receive a "loco authority + enterprise authority" bundle through their already secured channel and confirm it; the switch warns if some devices do not have it yet. The loco authority stays recognized: going back is always safe.
  - Each certificate is checked before being served (matching key, agents' address covered, chain signed by your authority); nginx is reloaded, or the old certificate is restored. A loco update keeps the enterprise certificate as long as it is valid.
- **Updates: shorter lists.** The changelog and the list of operations now only show the **5 most recent**; the rest expands (and folds) in one click. Only the most recent version is open; an older operation that is chosen expands its list by itself.

## 2.82.0 — 2026-10-28

- **Updates page redesigned.** Everything that matters is visible at a glance, at the top of the page:
  - A **loco** card (installed version → available, publication date, versions behind, "Coming up" with the new features, **Install** button) and a **System** card (packages, security fixes, kernel, **Fix first** box sorted by severity, **Install the fixes** and **Update everything** buttons).
  - A compact **restart banner** with its button, when a new kernel or services require it.
  - The **new features of the available version** can be read before installing: at each check, loco fetches the changelog published with it (same GitHub or GitLab API, no new outbound flow).
  - **Changelog as a timeline**: new features and installed version marked, one summary line per version, the old history folded.
  - **Operations**: the list and the log of the chosen operation side by side, without stray color codes.
  - The **updates source**, a rare setting, moves to the bottom of the page, folded.

## 2.81.0 — 2026-10-28

- **Ready-to-use basic scripts.** Two new Windows scripts arrive in the library, next to the NVIDIA driver: **Repair Windows (DISM + SFC)** (repairs the image with `DISM /RestoreHealth`, then checks the system files with `SFC /scannow`, cleaned-up output) and **Clean up temporary files** (Temp folders, Windows Update cache and recycle bin, with the space freed). Editable and deletable like any script.

## 2.80.0 — 2026-10-28

- **Changelog in the portal.** The *Administration → Updates* page now shows a **Changelog** panel: what changes with each loco version, offline, without going through GitHub. The installed version and the available one are marked.

## 2.79.0 — 2026-10-28

- **NVIDIA driver in one click.** A ready-to-use script arrives in the *Scripts* library: it detects the device's NVIDIA GPU, fetches the latest **Game Ready (DCH)** driver straight from NVIDIA, compares it with the installed version and installs it silently. To run on Windows devices, now or on a schedule. Editable and deletable like any script.

## 2.78.0 — 2026-10-28

- **Driver detection in Windows Update.** The inventory only looked at software updates: the drivers (NVIDIA/AMD graphics card, chipset…) distributed by Windows Update never appeared. They are now detected, shown with a **driver** label on the device's page, and installed along with the other updates.

## 2.77.0 — 2026-10-28

- **Python scripts.** In addition to PowerShell (Windows) and shell (Linux), the library accepts **Python scripts compatible with any system**: the agent runs them with the device's Python interpreter (Windows as Linux), as SYSTEM or root. New filter chip and dedicated label.

## 2.76.0 — 2026-10-28

- **Freezing Windows Update on a whole group.** A device group's "⋯" menu offers to **block** or **give back** the Windows updates for all its Windows members at once.

## 2.75.0 — 2026-10-28

- **Freezing Windows updates per device.** On a Windows device's page, a switch (off by default) prevents Windows from installing its updates by itself: you are the one who starts them from loco. The agent applies the instruction at the next contact and removes it if you untick it.

## 2.74.1 — 2026-10-27

- **My account, more readable.** Header as compact labels (profile, account type, last sign-in, creation) with a single date format. Security tab: fields of reasonable width and the password rules shown alongside, as a checklist; a more discreet "Good to know" box; two-factor authentication with its explanation and its button on the same line.

## 2.74.0 — 2026-10-27

- **Account creation redesigned.** The Accounts page shows the list at full width and a **New account** button at the top (the duplicate "Access profiles" disappears, the tab remains). Creation happens in a window in three parts: the identifier with its rule shown; the **access profile as cards**, each with what it allows (only the profiles you can assign are offered); the password (typed, generated with the die or left empty) and the choice to have it changed at the first sign-in. If the creation is refused (identifier taken, password too weak), the window reopens with the input.

## 2.73.3 — 2026-10-27

- **My fleet: "restart needed" in its place.** The information is no longer a badge that wrapped depending on the space available, shifted under the count: it is now a discreet status line (orange icon and text), always under the number of updates, aligned with it. The count and the "security" badge no longer get cut.

## 2.73.2 — 2026-10-27

- **My fleet: lighter header.** The "Restart if needed" box no longer floats between the buttons: "Update everything" (or "Update this group") opens a window that sums up the operation and offers the **Restart the machines if needed** switch, only for those where an update requires it.

## 2.73.1 — 2026-10-27

- **Device and software groups: renaming and deleting from the list.** Each row has a "⋯" menu with *Edit the members*, *Rename…* (only the name changes: members, description and deployments kept; a name already taken is refused) and *Delete…* (with confirmation; the devices, applications and deployments remain). Reserved to the accounts that can manage the groups.

## 2.73.0 — 2026-10-27

- **New font: Bricolage Grotesque** instead of IBM Plex Sans. More character in the titles and large figures, while staying sober and readable at small sizes thanks to its variable optical size. Fixed-width digits for the tables, and full coverage of French and Spanish (including "—" and "’", which used to show in another font). IBM Plex Mono stays for addresses and code.
- **Progress bar of the server's operations** (*Administration → Updates*): during an operation, a card at the top of the page shows the current step, the percentage and the elapsed time.
  - **loco update**: download, fingerprint check, then each of the installer's steps (ports, packages, copy of the portal, certificate, nginx, services…).
  - **System updates**: "package n of N" from apk, dnf or APT.
  - Other operations: animated bar.
  - When the portal restarts at the end of the installation, the card says so and the page reconnects by itself.

## 2.72.0 — 2026-10-27

- **Nicer shells, like a real terminal** (a device's shell and the server's root shell):
  - **Clipboard**: selecting copies; right click copies the selection or pastes if there is none; **Ctrl+Shift+C / Ctrl+Shift+V** and Shift+Insert; **Ctrl+C with a selection copies** (without interrupting the command), without a selection it goes to the shell as usual.
  - **Clickable links**: Ctrl+click (⌘+click) opens the address in a new tab.
  - **Search in the history**: Ctrl+Shift+F, Enter / Shift+Enter to navigate, results highlighted, Esc to close.
  - **Text size from the keyboard**: Ctrl + / Ctrl − / Ctrl 0, in addition to the A− / A+ buttons.
  - **Full screen**: the keyboard is locked (Chromium browsers) so that Ctrl+W, Ctrl+T… go to the shell instead of closing the tab.
  - **Rendering**: WebGL display (sharp text, instant scrolling, continuous frames and tables), embedded **JetBrains Mono** font (arrows, dashes, symbols and frames in the same font, loaded before opening for accurate columns), Unicode 11 width (emojis and wide symbols aligned), minimum contrast guaranteed, history of 10,000 lines. Automatic fallback to the old rendering if the browser has no WebGL.
  - A reminder of the shortcuts shows under the terminal.

## 2.71.1 — 2026-10-27

- **Automatic update of the Windows agents restored**: since 2.39, the server announced to the devices the fingerprint of the original MSI while it served them the "generic" MSI (enrollment slots emptied). The Windows agent therefore refused each update ("wrong MSI fingerprint" in its log, every 2 minutes) and stayed on its installation version. The fingerprint announced is now that of the file really served: the Windows devices update themselves and receive the monitoring of 2.71.

## 2.71.0 — 2026-10-27

*Versions 2.70.0 and 2.70.1 were not published separately (a test failed on Rocky 8): their content, described below, is delivered with 2.71.0.*

- **Device monitoring**: each agent (Linux and Windows) attaches to its contact, every 2 minutes, a reading: processor and network throughput averaged since the previous contact, memory, swap or page file, each local disk with its mount point, load (Linux) and time since boot. loco keeps the last reading and 7 days of history.
  - **Device page → Monitoring tab**: current values (thresholds flagged), *Processor and memory* and *Network* curves over 24 h or 7 days (interrupted when the device was off, tooltip on hover), table of the disks with their usage. A device out of contact keeps its last reading, greyed out.
  - **My fleet → Resources column**: mini-gauges for processor, memory and the fullest disk, in a status color beyond the thresholds.
  - **Alerts**: three new adjustable rules for the devices: disk almost full, memory saturated and processor saturated (average over 15 minutes, never on a simple spike).
  - **Live**: as long as the Monitoring tab is open and visible, the agent sends a reading **every 2 seconds** (processor, memory, network) through the channel it already keeps open with the server. A curve of the last 3 minutes scrolls, the values update and a "live" indicator lights up. As soon as the tab is left or the window hidden, nothing more is requested: no extra load the rest of the time.
  - The agents update automatically; the monitoring appears from their first contact in 2.71.

## 2.70.1 — 2026-10-27

- **NTP: an entered server that does not answer, clearly flagged**: when none of the entered servers is used, *System → Time and hardware* shows it ("The entered server does not answer: 192.168.100.23") and says where the time comes from (the distribution's fallback servers, with their address), instead of a simple "synchronized". The badge becomes "synchronized by the fallback servers". A hint gives the lead on the NTP server's side: accept the network's clients (chrony: `allow`) and allow incoming UDP 123. The table of sources also shows the name entered (for example pool.ntp.org) under the address.
- **"Use only these servers"**: new option so that the entered servers are the only sources. With chrony, loco itself comments out the distribution's sources in chrony.conf (pool, server, sourcedir), reversibly: unticking restores them exactly. With timesyncd, the built-in fallback servers are disabled. Without an entered server, the option is ignored, so as never to leave the server without a source.

## 2.70.0 — 2026-10-27

- **HTTPS certificate: renewing and importing from the portal** (*System → Network and certificates*).
  - **Renew**: new certificate signed by the loco authority (825 days, the server's current address and names). nginx is tested before being reloaded, and the old certificate is restored in case of failure. The devices that trust the authority see no difference.
  - **Import a certificate for a DNS name** (enterprise authority, Let's Encrypt…): PEM chain and key, checked (matching key, validity, DNS names). It is only served to the browsers that open the portal by one of its names (SNI); the fleet's agents, which pin the loco authority, keep the loco certificate on the server's address. A certificate that would cover that address is refused, since the agents would lose the connection. The imported certificate can be replaced or removed, and its nginx block is regenerated at each update.

## 2.69.3 — 2026-10-27

- **Account creation: password of your choice**: the *New account* form offers a **Password** field. You can type one, click the **die** to generate a random one (readable, without ambiguous characters, shown in clear and selected; each click offers another one), or leave it empty so that it is generated at creation. A help line checks the rule live (12 characters minimum, without the account's name). A **"To change at the first sign-in"** switch (on by default) leaves the choice of imposing the change or not. After creation, the **Credentials to hand over** box shows the account and its password with a Copy button. A scripted call that does not send the switch keeps the change mandatory.

## 2.69.2 — 2026-10-27

- **Downloads refused with "Invalid HTTPS certificate" (GIMP, SumatraPDF…)**: the real cause was the server's clock running late. A certificate issued after the server's date looks "not yet valid": with a clock on October 3, 7-Zip goes through but SumatraPDF and GIMP, whose certificates are more recent, fail. The message now points to the clock, with the server's date, and leads to *System → Time and hardware*.
- **NTP on the Alpine 3.24 appliance**: its chrony.conf reads the `conf.d` folder at the top, then disables the command port (`cmdport 0`) further down; loco's setting dropped in `conf.d` was therefore overridden and the time state stayed unreadable. loco now includes itself at the end of chrony.conf (and removes the old file), which restores reading the state and the clock offset in the portal.

## 2.69.1 — 2026-10-27

- **Device and software groups**: after saving a group (creation or change), you go back to the list of groups, positioned on the saved group and highlighted, instead of staying in the editor.

## 2.69.0 — 2026-10-27

- **Outbound flows to allow on the firewall**: *System → Network and certificates* now lists, under the incoming ports, the Internet destinations the server must reach **outbound**, with ports and protocols: DNS resolvers, configured NTP servers, repositories of the server's system, source of loco's updates (GitHub or GitLab), Linux mirrors actually seen in the apt-cacher-ng cache, RPM repositories and Flathub, Windows Update domains, winget and hosts of the Windows catalog's installers, and, if configured, SMTP server, alerts webhook and off-site backup (S3 or SFTP). Everything is drawn from the configuration and the active features; a button copies the list in "one destination per line" format for the network team.

## 2.68.0 — 2026-10-27

- **Server time, NTP overhaul**: the time state shown was often wrong. It is now measured by the root agent (the unprivileged portal cannot query chrony) and the *System → Time and hardware* block has been redesigned: clear status badge (synchronized / in progress / disabled / managed by the host), summary (local time, time zone, **source in use, stratum, offset**) and table per server (in use / reachable / unreachable…). NTP synchronization accepts **several servers** (IPv4/v6 or names, separated by spaces), taking precedence over the distribution's, and the network setting comes before the time zone. On the appliance (chrony with `cmdport 0`), the local command port is re-enabled automatically so that the state becomes readable again; timesyncd (Debian/Ubuntu) and chrony (Red Hat, Alpine) are supported, with "include" for the distributions without a *confdir* (Rocky, AlmaLinux) and the choice persisting across restarts on the appliance (OpenRC).

## 2.67.0 — 2026-10-27

- **Root shell: the terminal straight away**: once the shell is turned on (and the server restarted), *Administration → Root shell*, the bar's icon and Ctrl+K open the root terminal directly, with no intermediate page. A clearly visible **"Turn off the root shell"** button on the terminal closes all the shells and cuts off access (confirmation asked); you then go back to the setup. The steps page is only used as long as the shell is not active. The shell only starts at the terminal's first exchange (protected against CSRF), never on simply displaying the page.

## 2.66.0 — 2026-10-26

This version also publishes the content of 2.64.0 and 2.65.0 (dedicated Root shell page, ergonomics and design pass), not published separately.

- **Faster, leaner continuous integration, simple to take over**: each push now only runs the quick checks (lint, unit tests, PowerShell syntax — ~3 min, without a virtual machine). The full integration tests (Linux VMs, Docker, ISO, Windows servers) run when a push touches the installers, the agents or the ISO, and always before a release: pushing a `vX.Y.Z` tag checks, tests and publishes in a single run (the ISO and the Windows agent published = those tested). No more throttling of parallelism, QEMU installed without graphical packages (43 packages instead of 298), no more automatic weekly run. A fork works without a secret or a server: see *Continuous integration and release* in the README.

## 2.65.0 — 2026-10-26

An ergonomics and design pass over the whole portal, to find your way faster.

- **Decluttered top bar**: five entries (Overview, My fleet, Linux, Windows, Administration). The language, the appearance, the account and sign-out are gathered in an **account menu** (avatar). Nothing overflows any more, even at 1280 px.
- **Administration organized by sections**: the menu shows *Access* (accounts, profiles, directories), *Server* (system, updates, configuration, features, root shell) and *Monitoring* (audit log, alerts). The tabs now only show those of the current section.
- **Enrollment joins My fleet**, as a tab next to the devices, the groups and the deployments.
- **System in tabs**: *State* (services, locations, versions), *Backups*, *Network and certificates*, *Time and hardware*. The indicators at the top lead to the matching tab, and the actions bring you back to the right tab. The QEMU panel only appears if it applies.
- **Overview**: the caches' indicators and the server's form two distinct rows.
- **Readable audit log and operations**: labels for two-factor authentication, schedules, backups, the root shell and the alerts, instead of the technical codes; readable summary of the parameters of the server's operations.
- **Fixes**: the *My fleet* selection bar stayed shown without a selection; spacing in *My account*; missing spaces in *Enrollment* and *Windows Update*; lighter "Restart if needed" option.

## 2.64.0 — 2026-10-26

- **Better integrated root shell**: it now has its own page in the *Administration* menu (and therefore in the Ctrl+K search), with its state as a subtitle (disabled, restart required, active). The page guides the setup in three steps — turn on, restart, open — and the terminal opens in the same page. When the shell is active, a terminal icon in the top bar gives access to it in one click. The panel has left the *System* page.

## 2.63.0 — 2026-10-26

- **The server's root shell in the browser** (*System → Server root shell*): a root terminal on the loco server itself (Linux installation or appliance), reserved to the installation's "admin" account. **Off by default**; turning it on asks for the admin account's password and **only takes effect after a restart of the server** (restart button offered). Turning it off is immediate and closes the open shells. Each activation, opening and closing is logged. The terminal is served by the root agent through a local socket reserved to the portal.

## 2.62.0 — 2026-10-26

- **`ufw` present on existing appliances too**: in 2.59 the `ufw` package was only added to the ISO image (new installations). It is now also in the list of packages applied by `install.sh`, so an appliance already installed gets `ufw` at its next loco update. Still installed without being enabled or configured.
- **Appliance welcome message (MOTD)**: signing in to the appliance (console and SSH) now shows a neat loco banner — logo, version, system, portal address and a reminder of the console commands — regenerated at each boot.

## 2.61.0 — 2026-10-26

- **Correct NTP state on the Alpine appliance**: *System → server time* read the synchronization state only through `timedatectl`, missing on the Alpine appliance — the banner therefore always showed "NTP synchronization disabled", even when chrony was active and synchronized. The state (enabled / synchronized / in progress) is now read from chrony when `timedatectl` does not exist. To be used with the fix of 2.60 (chrony reloaded by `rc-service`).

## 2.60.0 — 2026-10-26

- **NTP synchronization fixed on the Alpine appliance**: setting the NTP server from *System → server time* reloaded the service through `systemctl`, missing on the Alpine appliance (OpenRC) — the new server was therefore only taken into account at the restart. The service is now reloaded with `rc-service` when `systemctl` does not exist. Validated end to end against a containerized NTP server. Covered by tests (Debian → `systemctl`, Alpine → `rc-service`, container → skipped).

## 2.59.0 — 2026-10-26

- **`ufw` present in the appliance (ISO)**: the `ufw` firewall is now installed in the Alpine image, but **neither enabled nor configured** — it applies no rules and filters nothing by default. It is simply available if you want to set rules yourself later (`ufw enable`, `ufw allow …`).

## 2.58.0 — 2026-10-26

- **Network ports in System**: a new *Administration → System* section sums up the ports to open **inbound** on the firewall for the application to work — interface and agents over HTTPS (8443), HTTP bootstrap of the devices, Linux packages cache (3142), Windows Update cache (80/443) and Windows Update proxy (3128). The list adapts to the configuration and the enabled features, says who is concerned (administration, Linux devices, Windows devices) and recalls that the application's internal port stays local.

## 2.57.0 — 2026-10-26

- **Two-factor authentication QR code reliable everywhere**: the enrollment QR code now shows even without the `python3-qrcode` system package (appliance, minimal installations) thanks to an embedded QR generator with no dependency. The enrollment page also lists the **compatible apps** (Microsoft Authenticator, Google Authenticator, Authy, 1Password, Bitwarden — any TOTP app works).

## 2.56.0 — 2026-10-25

- **Deploying Linux packages**: the *Deployments* section now offers, next to the Windows software, a store of the `.deb` and `.rpm` packages published in loco. You choose packages, target Linux devices or groups, preview, then confirm; each device only installs the packages compatible with its system (`.deb` → Debian/Ubuntu, `.rpm` → Red Hat), from the loco repository already configured. Progress is followed device by device as for Windows. New `install-package` agent action (*Deploy software* right).

## 2.55.0 — 2026-10-25

- **"Restart needed" badge** in *My fleet*: the old warning text becomes an amber badge with an icon, neatly aligned with the update counters and the security badge.

## 2.54.0 — 2026-10-25

- **Two-factor authentication (TOTP)** for local accounts: from *My account → Security*, you turn it on by scanning a QR code (or entering the key) with an authenticator app, then a 6-digit code is asked at each sign-in. Single-use **backup codes** let you sign in if the phone is lost. An administrator can remove an account's 2FA (*Reset passwords* right), and `loco-user mfa-reset NAME` removes it from the console as a last resort. The secret is encrypted at rest and the backup codes are hashed.

## 2.53.0 — 2026-10-24

- **Sturdier background tasks**: the periodic evaluations (alerts, off-site copy) now only act in a single process at a time thanks to a lease in the database, to avoid any duplicate if the server runs with several workers. Automatic takeover if the leading process stops.
- **Faster CI**: the portal's unit tests now run outside a virtual machine, in a few minutes, and gate the longer VM tests.

## 2.52.0 — 2026-10-24

- **Device selection and grouped update** (*My fleet*): ticking devices in the list ("select all" box in the header) brings up a discreet bar at the top of the table with the number selected and an **Update selected** button (*Restart if needed* option). The automatic refresh pauses during the selection. Reserved to the *Update a device* right.

## 2.51.0 — 2026-10-24

- **Off-site backup** (*System → Backups*): automatic copy of the latest daily backup to external storage — **S3-compatible** (AWS/Backblaze/Wasabi/OVH/Scaleway/R2 cloud or on-prem MinIO), **SFTP** (SSH key), or **folder / mount point** (second disk, external media, mounted SMB/NFS share). Optional AES-256 encryption before sending, **Test** button, secrets encrypted at rest. Retention follows the "Backups kept" setting. An "Off-site copy failed" alert warns in case of a problem.
- **Units on the alert thresholds** (*Alerts → Rules*): each threshold shows its unit (days, %, failures / 24 h, updates, devices).
- **Backups panel reworked**: more regular list (actions always aligned on the right) and off-site copy built into the same panel.

## 2.50.0 — 2026-10-23

- **Alerts and notifications** (*Administration → Alerts*): be warned by **e-mail (SMTP)** or **webhook** (Slack, Teams, Discord or a home-made relay, with an HMAC signature) when the state of the server or the fleet calls for it. Rules that can be turned on, with thresholds — service stopped, HTTPS certificate expiring, backup overdue, disk full, failed logins, pending security updates, devices out of contact — and a minimum severity per channel. **Test** button, secrets encrypted at rest, evaluation on the server every minute without repetition, with re-sending in case of failure. New *Configure alerts* right and a dedicated feature switch.

## 2.49.0 — 2026-10-23

- **Backups redesigned** (*System* page): more compact list (date and size on one line, file name below), grouped actions with a delete icon, and a clear settings area for the retention and the restore from a file.
- **Windows Update message fixed**: the "no device has gone through loco yet" warning no longer shows as soon as there has been no recent request; it only appears when the cache has really served nothing (no traffic over 14 days and an empty cache).
- **README reworked**: header, badges, table of contents and foldable detailed sections, for a nicer read.

## 2.48.0 — 2026-10-23

- **Deleting a backup** (*System › Backups* page): a *Delete* button on each backup, with a confirmation before final deletion. Done by the root agent. Reserved to the *Configure the server* right.
- **Up-to-date Windows Update message**: when no device has gone through the cache yet, the message now explains that the enrolled Windows devices are directed automatically to the cache (through the proxy, on by default) and points to the "Directing the devices to the cache" section for unmanaged devices, instead of only talking about DNS redirection.

## 2.47.0 — 2026-10-23

- **Restoring from an uploaded file** (*System › Backups* page): you can upload a `.tar.gz` archive (a backup kept elsewhere, or coming from another server) to restore it. The file is **checked before anything is written** — it really is a loco/local-repo backup (presence of the database and the configuration, safe paths, no `..` or symbolic link, readable `.tar.gz`) — on the portal's side, then a second time by the root agent before applying.
- **Configurable backup retention**: a *Backups kept* field (1 to 60) writes `BACKUP_KEEP` and immediately deletes the oldest beyond the limit. Reserved to the *Configure the server* right.

## 2.46.0 — 2026-10-23

- **Downloading and restoring the backups** (*System* page): each backup has a **Download** button (to keep it elsewhere) and a one-click **Restore** button. The restore goes through the root agent — portal stopped, database, accounts, keys and repositories put back, services regenerated, then restart; the server's own configuration (address, ports, networks) is kept, and an automatic fallback restores the previous state in case of failure. The command stays available to restore on another server. Reserved to the *Configure the server* right.
- **Software logos in "Latest additions"** (Overview): each recently added application shows its logo (real icon for the WinGet apps, otherwise a badge with the initial), as in the Software center.
- **Dismissible audit log warning**: the "N failed login(s) in the last 24 hours" banner has a cross to close it; it stays hidden as long as no new failure occurs.

## 2.45.0 — 2026-10-23

- **Finding your machines in the search (Ctrl+K)**: the navigation bar's search palette now lists the **fleet's devices** in addition to the pages. You type a machine name, an IP address or a system, and open its page with *Enter*. Reserved to the profiles that have access to the fleet (*View the fleet* right).

## 2.44.0 — 2026-10-23

- **Connecting a Proxmox VE host to the cache**: new section on the *Packages* page, next to "Connect a Linux device". A single command on the Proxmox host connects its repositories to loco, either **no-subscription** (free, cached) — recommended — or **enterprise** (subscription required, direct HTTPS). Debian and the no-subscription repository go through the cache; `--trust-ca` for the first connection, `--remove` to go back.
- **The Proxmox repositories are replaced, never duplicated**: the script finds the Proxmox VE repository wherever it is — `.sources` files (deb822, Proxmox 9) as well as old `.list` files in `sources.list.d` and `sources.list` — removes it and writes a single `loco-proxmox.sources` with the chosen variant. The Debian and Ceph repositories are not touched.

## 2.43.0 — 2026-10-23

- **"QEMU guest agent" card visible on the appliance**: the appliance runs on Alpine (without systemd), where VM detection failed, so the installation button stayed hidden. loco now recognizes a QEMU/KVM VM by its DMI manufacturer. The card (*System* page) is now always present, with a clear message depending on the server: install on a QEMU VM, "clock/virtualization managed by the host" in a container, "not applicable" on a physical server.

## 2.42.0 — 2026-10-23

- **Server time, NTP in containers**: on a containerized server (LXC), the *System › Server time* card now says that the clock comes from the host (NTP synchronization is set on the host) instead of a switch with no effect; on a real VM or a physical server, setting the upstream NTP server works normally.
- **QEMU guest agent preinstalled on the appliance**: the ISO now installs and enables `qemu-guest-agent` by default (the appliance always runs as a Proxmox/QEMU VM) — clean shutdown, IP addresses in Proxmox and consistent backups work from the installation. On the other servers, the *System › QEMU guest agent* card still lets you install it in one click.

## 2.41.0 — 2026-10-23

- **Server time** (*System* page): choose the server's **time zone** and, if you wish, **synchronize the clock with an upstream NTP server** (the server becomes an NTP client, it does not host one). Applied by the agent — `timedatectl` for the time zone, `systemd-timesyncd` (Debian/Ubuntu) or `chrony` (Red Hat) for NTP.
- **QEMU guest agent** (*System* page): when loco runs in a Proxmox/QEMU VM, a card shows the state of `qemu-guest-agent` and lets you **install it in one click** (clean shutdown, IP addresses in Proxmox, file freezing for consistent backups). Hidden on containers and physical hardware.

## 2.40.0 — 2026-10-22

- **Clearer *Fleet settings* page**: the server address card shows the current address with a confirmation badge ("3/3 agents have confirmed it") and folds the instructions into a three-step "How to change the address without losing the devices?"; an announced address waiting for the switch has its own box. The badge threshold gains a color legend (up to date · 1 to N · N or more), and the enrollment key shows a masked preview.

## 2.39.0 — 2026-10-22

- **Ready-to-use Windows MSI**: "Download the MSI" from the *Enrollment* page now gives a ready-made installer — **a double-click installs the service AND enrolls the device**, without any command, like the `.deb` package on Linux. The portal engraves loco's address, the enrollment key and the authority's fingerprint into the MSI at download. The PowerShell command and mass deployment (GPO) stay available under "Other methods" (generic MSI, without enrollment).
- **Updates from GitLab**: loco can pull its versions from a **GitLab project** (including an internal GitLab), not only from GitHub. In *Updates › Updates source*, give `owner/repo` (GitHub) or the URL of a GitLab project. Section reworked, more readable.
- **Security updates counter** of the fleet: instead of a red "including N security", a discreet **shield badge**, on the fleet list and each device's page.

## 2.38.0 — 2026-10-22

- **More readable overview**: the tiles (caches, Processor, Memory, Disk) wrap instead of being squeezed on a single line, so no more cut-off values. The **Memory** tile now shows a usage **percentage** (like the Processor) with "used of total" below, instead of a truncated number of bytes.
- **Linux devices' terminal like a real terminal**: the session opens a **colored prompt** by default (`user@device: path`) when the device has none, for a real terminal look from the start. The device's `.bashrc` takes precedence: if it already defines a colored prompt (or a `PROMPT_COMMAND`), its own is shown.

## 2.37.0 — 2026-10-22

- **The server's processor and memory in the overview**: two tiles, next to "Disk", show the load of the server loco runs on — **Processor** (usage percentage, 1-min load and number of cores) and **Memory** (used of total), with a gauge (orange then red as it fills up). The values refresh live like the rest of the page. Read on the server (`/proc`); hidden if unavailable.

## 2.36.0 — 2026-10-22

- **Devices' terminal redesigned** (Linux shell as well as Windows PowerShell, same display): a real, readable 16-color ANSI palette, matched to the chosen theme (OLED, dark, light — white background and adapted colors in the light theme); the cursor and the selection take your accent color and update live when you change theme or accent. Modern monospace font, airier line spacing and smooth scrolling for long outputs, background matched to the surface (no more border around the margin) and a thin, discreet scrollbar. The **A− / A+** buttons set the text size, remembered in the browser.

## 2.35.0 — 2026-10-22

- **Updates page fully translated**: the administration agent (`locoadmin`) now returns a structured state (codes) that the portal puts into words in each person's language — summary, restart reasons, errors. The operation logs (check, update, restart) follow the server's language (French, Spanish or English according to its locale), English by default.

- **Page addresses (URLs) in English**, identical whatever the language chosen, for uniform navigation: `/fleet`, `/accounts`, `/log`, `/system/updates`, `/enrollment`… (before `/parc`, `/comptes`, `/journal`, `/systeme/mises-a-jour`, `/enrolement`). The internal links follow automatically; the addresses served to the devices (repositories, agents, installation scripts) do not change.

- **Simpler loco update**, from the *Updates* page: the installation button for the published version now works for **any Linux server** (systemd), not only the Alpine appliance — the archive is checked (SHA-256) then installed, with an automatic return to the previous version in case of failure. A **GitHub repository** section lets you point the updates to another "owner/repo" repository (for example your fork) and check again; the choice is kept in `loco.conf` (and kept from one update to the next). Also settable at installation with `--github-repo owner/repo`.

- **Dedicated language selector**: a globe icon, at the top right and on the sign-in screen, opens a *Français / English / Español* menu reachable from any page. The language choice leaves the *Appearance* menu (palette) to become a one-click action.

- **Upgrading from an old version**: the restart reasons and errors already cached stay readable on the Updates page and in the console, until the agent's next check.


## 2.34.0 — 2026-10-21

- **Multilingual portal (French, English, Spanish)**: each person chooses **Français**, **English** or **Español** in the *Appearance* menu (palette icon, at the top right, also on the sign-in screen); the choice is kept in their account. Without a choice, the portal follows the browser's language. Everything is translated: pages, windows, confirmations, messages after an action and errors, labels (rights, audit log, states, recurrences, built-in profiles), file manager, remote console, Ctrl+K search. Dates, sizes (Go/GB, decimal comma or point) and numbers follow the language; the schedules' recurrences are written in each language's order ("Every Tuesday and Thursday at 02:00", "Cada martes y jueves a las 02:00").
- **Multilingual device scripts**: the commands served to the devices (APT configuration `setup.sh`, RPM `rpm-setup.sh`, Flatpak, Windows software installation `install.ps1`, DNS zones, Linux and Windows agents) write their messages in the device's language — French, Spanish or English according to the system (`LANG` on Linux, display language of Windows). The server puts the three versions in the script; the command shown in the portal does not change.
- **Multilingual agents**: the Linux agent (`loco-client`) and the Windows agent (service, tooltip and window of the desktop companion, remote console and files) speak the device's language (French, Spanish or English): displayed state, log, messages sent back to the portal (action results), restart messages.
- **Server in three languages**: the installer (`install.sh`, `remove.sh`), the appliance's console (`setup-loco`, the ISO's installer, sign-in banner) and the administration tools (`loco-user`, `loco-fleet`, `loco-backup`…) follow the server's language (`LANG`).
- French stays the reference language: the code's texts serve as keys, English is in `portal/i18n/en.json` and Spanish in `portal/i18n/es.json`. A test refuses any displayed text without its English **and** Spanish translation, with the same values to insert.

## 2.33.0 — 2026-10-21

- **Clearer Linux packages**: the page is in two parts. **Connect a Linux device**, in three steps valid for all the families: the device's system (Debian, Ubuntu or Rocky, AlmaLinux, RHEL), what loco provides it (cache and your repositories, your repositories only, cache only, or remove loco), with the EPEL and "the device does not have loco's authority yet" options as boxes to tick; the command to copy builds itself. Then **Your repositories**: the bar of versions, adding packages to the repository shown and the list. At the top, the sizes of the two caches, your packages and the fleet's Linux devices. Special cases (derived distributions, `wget`, keys, mirrors) grouped together.
- **Scripts**: counters (library, pending devices, runs with failures); filterable library (search, Windows / Linux); history of the last 100 runs with filters (in progress, with failures, successful) and each run's state. **Empty the queue** cancels all the sendings not yet received by the devices (scheduled ones included), **Empty everything** deletes the history of the finished runs (those in progress are kept), and each finished run can be removed from the history (also from its page, which also offers "Run again…"). The audit log keeps track of these cleanups.

## 2.32.0 — 2026-10-21

- **Adding / editing a Windows application**: window reorganized in steps (installer, then name and version), the type is **detected** from the link or the file, and the settings rarely useful (type, silent arguments, name in Windows) are folded into "Installation options", with their summary. For an `.exe` without silent arguments, the options open by themselves with a warning (otherwise the installation waits for a click on each device).
- **All the windows**: title and buttons stay visible, only the content scrolls; closing with a cross; a single focus ring on the fields (no more double outline).
- **Linux packages: APT and RPM on a single page** (*Linux › Packages*, which replaces "APT packages" and "RPM packages"): a bar gathers all the repositories, grouped by family (Debian, Ubuntu · Red Hat), with their number of packages; a click changes version instead of a list and a "Show" button. The rest follows the chosen family: cache (APT or Red Hat mirrors), connection command (`apt` or `dnf`, EPEL option), adding `.deb` or `.rpm` files. "+ New repository" creates a Debian, Ubuntu or Red Hat repository from the same window. The `/linux` and `/rpm` addresses, the devices' scripts and the separate APT / RPM rights do not change.
- List of packages at full width, with each package's **description**, its date of addition and actions as icons (download, copy to another repository, delete); number of **fleet devices** on the version shown; explanatory empty state; connection options and mirrors grouped together.

## 2.31.0 — 2026-10-21

A more modern interface, faster to browse, in the three themes:

- **Depth**: the page has a slightly tinted background and the content sits on white (or charcoal) cards with more rounded corners, with a soft shadow; the indicators become separate tiles. Airier titles, margins and tables.
- **Navigation**: the header's active item is a pill (its icon takes the accent color); the submenus (My fleet, Administration, Software) form a bar where the current page stands out as a card; pill tabs on the pages; filters as rounded chips, the active filter filled in.
- **Go to a page (Ctrl+K)**: magnifier button in the header; a search window lists all the accessible pages (including Groups, Deployments, Scripts, Schedules, Software center…) and opens by typing a few letters, arrows and Enter. **"/"** puts the cursor in the page's search (fleet, catalog, audit log…).
- **Clickable rows**: in the lists of the fleet, scripts, deployments and groups, a click anywhere on the row opens the item (Ctrl+click: new tab); the row's buttons, boxes and links keep their action.
- Buttons, fields and menus a bit bigger and more rounded, dialog windows with a blurred background, sign-in screen on a discreet gradient.

## 2.30.0 — 2026-10-21

- **Updates of the server's system from the portal**, on all the Linux installations (Debian, Ubuntu, Rocky Linux, and the Alpine appliance, which already had it): *Administration → Updates* lists the pending packages (kernel, OpenSSL, Squid, nginx, Python, glibc…), with the **security fixes first** (Debian/Ubuntu `-security` repository, DNF security advisories with their severity on Rocky) and the important packages marked ("kernel", "loco service", "base"). Filters and search in the list.
- **Install the security fixes** alone or **update everything**; the configuration files placed by loco are kept, and loco's services still using old libraries (portal, nginx, Squid, apt-cacher-ng) are restarted automatically. The log of each operation shows live.
- **Restart**: the page says why it is needed (new kernel installed, a package's request, replaced libraries still loaded by the system) and the services that use old libraries; *Restart the server* button. In an LXC container, the kernel is flagged as managed by the host.
- Automatic check every day and at restart. On the console: `loco-update status|check|security|system|reboot`. The operations go through a separate root agent (`loco-agent`, a systemd service now installed everywhere) that validates each request again: the portal stays unprivileged.

## 2.29.2 — 2026-10-21

A security and reliability pass:

- **Windows Update proxy never sent without Squid**: on a `--no-wu` installation or a loco server on Windows (no proxy-cache), the "Windows Update proxy" feature, on by default since 2.26, still pushed the proxy to the Windows devices, whose updates failed. The portal now knows whether Squid is installed; otherwise the feature is turned off, the *Windows Update* page says so and the manual command is no longer offered.
- **Windows agent: the proxy is really removed when loco no longer answers** (announced in 2.25 but missing): after three missed contacts in a row (about 6 min), or if loco refuses the device, the WinHTTP proxy set by loco is removed and Windows Update goes back to direct; it is set again at the first successful contact.
- **Hardened Squid**: the proxy refuses to relay to the server itself (127.0.0.0/8, ::1) and to the link-local range (169.254.0.0/16, fe80::/10), so that a device cannot reach loco's internal services by bouncing.
- **File manager**: the fields returned by the agent (type, permissions, dates) are validated and escaped before display, so that a compromised device cannot inject anything into the page (already blocked by the content security policy, now also at the source).
- **Schedules**: the person who edits or resumes a schedule becomes responsible for it (their rights are checked again when sending to the devices, their name appears in the audit log), instead of the original author.

## 2.29.1 — 2026-10-21

- File manager: the breadcrumb no longer doubles the root's "/" ("/ root" instead of "/ / root"), and a folder that only contains hidden files is no longer announced as "empty": the number of hidden items is shown, with a link to display them.

## 2.29.0 — 2026-10-21

- **Schedules redesigned, and recurring**: new *My fleet → Schedules* tab (list with action, targets, recurrence in plain language, next and last run, state; edit, pause, resume, delete). Updates and scripts are scheduled **once, every day, every week** (days of your choice) **or every month** (fixed day or last day of the month), on the whole fleet, groups (members resolved at each run) or devices. A common "When" component (segmented choice, days as chips, plain-language summary with the next run) replaces the date/time fields. In context: calendar button next to *Update* on a device's page and *Schedule…* next to *Update everything* (scheduling window), "Now / Schedule" choice when running a script; a device's page shows the schedules that concern it.
- A schedule that is due becomes an action at the agents' next contact; missed occurrences do not pile up. A script modified since it was scheduled pauses the schedule (resumed with the current version, on demand). The rights of the scheduled action are required at creation and checked again when sending. The one-off schedules of 2.24 (date/time field) are replaced by this system.

## 2.28.0 — 2026-10-20

- **Eye / crossed-out eye icons** to show or hide: on all the password fields (sign-in, first password, account, directories), and instead of the file manager's "Hidden files" box.
- **Windows Update, latest requests**: only the Windows devices are named (a Debian device sharing the address no longer appears). Behind a NAT, when several Windows devices have the same address as seen by loco, all are named ("pc-a or pc-b") with the mention "shared address (NAT)", instead of picking one at random.
- File manager: the toolbar no longer touches the panel's edges.

## 2.27.0 — 2026-10-20

- **New application logo** (building, hand and mouse): vector (SVG), transparent background, it takes the theme's color (light, soft dark, OLED) and follows the chosen accent color. It replaces the square in the navigation bar and appears large on the sign-in and first password screens. The browser tab's icon uses the same logo and adapts to the browser's light or dark mode.

## 2.26.0 — 2026-10-20

- **Windows Update proxy on by default**, and adjustable in *Administration › Features* ("Windows Update proxy (without DNS)"). The managed Windows devices are directed to the cache through their WinHTTP proxy from installation, without any DNS redirection; turning the feature off makes the agents remove the proxy. The *Windows Update* page shows the state instead of a dedicated switch.
- **File manager**: fix of the empty button ("Paste") that appeared in the bar, and start at the **root of the system disk (`C:\`)** on Windows (still `/root` on Linux, the agent acting as root/SYSTEM with all rights).

## 2.25.0 — 2026-10-20

- **Windows Update cache through a proxy, without DNS**: loco embeds a proxy-cache (Squid) reached through the Windows devices' **WinHTTP proxy** setting. A new alternative to DNS redirection: on the *Windows Update* page, a "Direct the managed devices" switch has the loco agent set each Windows device's WinHTTP proxy to loco (per device, reversible, the agent removes it if it can no longer reach loco or when the agent is removed); no DNS zone to edit. The proxy caches the same immutable files (HTTP) and relays HTTPS without decrypting it — same coverage as the DNS path. The manual command (`netsh winhttp set proxy …`) is also offered on the *Windows Update* and *Enrollment* pages. The service is installed with the Windows Update cache (`--wu-proxy-port` option, 3128 by default) on Debian/Ubuntu, Rocky/AlmaLinux, Alpine, the ISO and the container, and appears on the *System* page. Validated for real: a device's Windows Update goes through the proxy (search and download), WU file cached (MISS then HIT), and a full loco installation bringing an active Squid.

## 2.24.0 — 2026-10-20

- **One-off scheduling of updates and scripts**: choose a date and a time (server time) instead of "at the next contact". The command stays pending and is only sent to the agent from the chosen moment. Available for *Update* (a device from its page, through "Schedule an update"), *Update everything* (fleet or group) and running a script (run page). A pending scheduled action can be **cancelled** before being sent (*Cancel* button on the device's page, *Cancel the pending devices* for a script). The device's page and the run follow-up show the scheduled moment. Field left empty = current behavior (from the next contact).

## 2.23.0 — 2026-10-20

- **Device file manager** (*Files* tab of a device's page): browse the tree, upload (button or drag and drop), download, create a folder, rename, copy, move, delete, edit a text file in the built-in editor, and adjust the permissions (octal) on Linux. Breadcrumb, shortcuts to the root and the home folder, optional display of hidden files and progress per transfer. Like the terminals and the desktop, everything goes through the HTTPS relay — the agent runs each operation as root (SYSTEM on Windows) and the server never connects to the device; no device address or token reaches the browser. New *Manage device files* right (reserved to the administrator by default), agent 2.23 minimum; the page's automatic refresh is suspended while the tab is open, so as not to interrupt a transfer or an edit.

## 2.22.0 — 2026-10-20

- **"Agent and repositories" package** (Enrollment page, Debian/Ubuntu and Rocky/AlmaLinux/RHEL): the same `loco-client` package entrusts loco's authority to the system, connects the device to the cache and to its version's local repository — the cache alone if that repository does not exist yet — then installs the agent. It is now the recommended choice; "agent only" is still offered. Its version is one notch above (`+depots`, `1.depots`): moving from the agent only to the full package is an update that keeps the device's enrollment, and uninstalling the full package also removes the repository configuration (the system's repositories restored on Red Hat). The repository scripts gain the `--from-package` option (no call to APT or DNF, busy with the installation). Validated by real installations, updates and uninstallations on Debian 13 and Rocky Linux 9.
- **The agent's packages also served over HTTP** (bootstrap port), like the script: the "authority not imported yet" commands of the Enrollment page work.
- **Red Hat agent**: the inventory (`dnf check-update`, `updateinfo`) accepts importing the key of a signed repository added since; without a terminal, dnf refused and wrongly reported a "bad signature" until someone ran dnf by hand.
- **Accounts: origin of the last sign-in** — under the date, the address from which the account last signed in (as seen by loco), and a link to the history of its sign-ins in the audit log. The address is recorded at each sign-in; for existing accounts, it is taken from the audit log (`loco-user list` shows it too). The list of accounts wraps instead of cutting off the actions.
- **System page redesigned**: summary at the top (active services, days of validity of the certificate, disk, last backup with an alert if the daily backup is missing, version), a stopped service and a missing certificate flagged at the top of the page; paired panels (services and backups, certificate and authority, locations and versions); authority download button and import commands per system, Rocky / AlmaLinux / RHEL included.

## 2.21.0 — 2026-10-20

- **Enrollment page** (navigation bar): everything that attaches a device to loco, one tab per system (Windows; Debian, Ubuntu; Rocky, AlmaLinux, RHEL), in numbered steps — install the agent, configure the repositories and caches (APT, DNF, Flatpak), Windows Update and catalog software on Windows. It replaces the overview's "Connect a device" block and My fleet's "Add a device" window (whose button now leads to the page). Each section follows the account's rights and features; the enrollment key stays reserved to the enrollment right.
- **The agent's .deb and .rpm packages**, produced by loco for this server (address, pinned authority and enrollment key included): `sudo apt install ./loco-client_<version>_all.deb` or `sudo dnf install ./loco-client-<version>-1.noarch.rpm` enrolls the device at once, and uninstalling the package removes the agent. The package only contains the installation script (the agent then updates itself). Validated with dpkg-deb, rpm 4.14 and 4.16 (header and payload digests), then by a real installation and uninstallation on Debian 13 and Rocky Linux 9.

## 2.20.1 — 2026-10-20

- **Availability and updates told apart in My fleet**: near the device's name, a green dot shows a contact from the agent in the last six minutes (three usual cycles) and a red one the absence of a recent contact. The updates volume light is now in the *Updates* column. The page repeats the availability near the name and the update summary near the counters. Going offline is taken into account by the automatic refresh, even without new activity from the device.
- The remote relay's exchanges and the action results also keep the device online during a long operation. The timestamp is recorded at most once per contact cycle to avoid a write at each noVNC frame.

## 2.20.0 — 2026-10-20

- **Live network on the overview**: the server's incoming and outgoing throughput, read every 2 seconds on the default route's interface (failing that, the physical interfaces), with the peak and the curve of the last 2 minutes (common scale for both directions, instant value on hover). The throughput is computed in the browser between two readings of the counters: nothing is measured or kept when the page is not displayed. Linux (`/proc/net/dev`) and native Windows server (64-bit counters of the `GetIfEntry2` API, checked in CI against `Get-NetAdapterStatistics`).
- **Back icons in the navigation bar**.
- README screenshots updated.

## 2.19.0 — 2026-10-20

- **Interface redesigned, sober and consistent**: a real design system replaces the successive additions — tighter type scale (12 to 24 px), spacing on a 4 px grid, 6 px (controls) and 8 px (panels) radii, a single shadow for floating elements. Monospace font reserved to code (commands, paths, addresses, versions, fingerprints): device names, dates, accounts, counters and indicator values move to the normal font. Indicator labels in lower case instead of spaced capitals, links not underlined outside running text, main navigation without icons on a large screen, translucent header.
- **Overview**: fleet state as a segmented bar with the list of states (instead of the ring), disk usage as rows with single-hue bars (instead of the rainbow bar), Windows Update chart in two tones of the same blue (palette checked for contrast and color blindness). "Recent activity", which in fact listed the additions to the catalog, becomes "Latest additions".
- **States named the same everywhere**: Up to date, Updates pending, Needs attention, Unknown state — on the overview, My fleet, a device's page and the groups.
- **Ergonomics**: fleet list as cards on mobile (no more horizontal scrolling); "Restart if needed" option presented as a button; package filters and search on a single bar; file drop zones in French with a "Browse…" button instead of the native field; discreet chevrons instead of the native triangles; confirmation as the primary button, red only for destructive actions (delete, remove, restart…); selected filters marked without an accent fill; tinted status badges; numeric column headers aligned.

## 2.18.0 — 2026-10-20

- **Installed packages of Linux devices** (*My fleet › device page*): the Linux agent now reports the installed packages (name, version, architecture, size), the Flatpak applications and the device's specifications (processor, memory, disk, kernel, architecture, virtualization, boot). An *Installed packages* tab lists them, with a search and filters (all, installed on purpose, to update — the candidate version is then shown opposite); a *Flatpak* tab and a *Specifications* block in *Details* complete the page. The detailed inventory is stored in a separate table, so that the fleet list stays light. Debian/Ubuntu (`dpkg-query`/`apt-mark`) and Red Hat (`rpm`/`dnf repoquery`); read at the inventory's usual pace and redone once after the agent's update.
- **Deployments live in My fleet**: a deployment is an action on the devices (targets, progress device by device), like scripts. The *Deployments* tab of My fleet now stays in My fleet (the fleet's submenu, "My fleet" entry active) instead of switching to Windows. *Windows › Software* keeps the local catalog, the software center and the software groups; their *Deploy* buttons open the preparation in My fleet. New `/parc/deploiements…` addresses; the old ones (`/windows-apps/deploiements…`, `/windows-apps/deployer`) redirect, parameters included.
- The "My fleet" entry is also active on the Scripts pages.

## 2.17.1 — 2026-10-20

- **The device's resolution always restored when the console closes** (Windows agent): in 2.17.0, when the browser closed the session while a frame request was waiting for a screen change, the desktop companion did not see the channel closing; it was stopped by force after 5 seconds, without restoring the original resolution (the device stayed at 848 × 480, for example). The closed channel is now noticed at once: the companion ends cleanly and gives back the initial resolution. Checked over a series of sessions with resolution changes, full screen and zoom.

## 2.17.0 — 2026-10-20

- **More responsive noVNC console** (Windows agent 2.17): the screen is read through **DXGI duplication** — only when it changes, with the changed areas — instead of a full GDI capture at each frame (≈ 180 ms on a virtual machine). The companion holds the frame request as long as nothing moves and answers it at the first change, without delaying input; no more exchanges or computing on a still desktop (≈ 1% of a core against ≈ 35%). GDI takes over on the secure desktop, with several screens or a driver without duplication, and duplication is reopened right after a mode change. Reused buffers, at most ~30 frames per second. Measured on the demo VM: input → changed frame from 257 to ~135 ms (worst case 641 → ~250 ms), full frame from 310 to ~85 ms.
- **Shorter relay**: the agent goes again as soon as a frame is ready (in a single exchange) and, with a session open, polls every 12 ms (pure round trip of the relay 48 → 33 ms); the relay's transactions, ephemeral, no longer force a disk write at each request (cost per request 5.4 → 2.1 ms), the rest of loco keeping its full durability; faster base64 decoding in the browser, polling spaced out after a second of calm.
- **No more scrollbars in fitted resolution** (including when leaving full screen): the picture is always fitted to the window, and the zoom enlarges the device's interface there without scrolling. In the device's resolution, the zoom enlarges the picture, with thin scrollbars. Display settings remembered per browser; "Fit" by default.
- **A single, sharp cursor**: on the machines whose driver has no hardware cursor (VMs, basic adapter), Windows draws the cursor in the picture; the agent recognizes it and the browser now only shows a marker dot instead of a second, offset arrow. The cursor follows the scale of the desktop shown (zoom, fitting).
- **Console redesigned**: status badge with the latency to the server (repeated in the rail in full screen), connection overlay then end-of-session overlay with "New session" in one click, icon buttons, held keys shown like keyboard keys, labels reduced to icons in a narrow window. In full screen, a click on the desktop folds the controls rail.
- **Clipboard**: option to send the local clipboard automatically to the device at each click on the desktop (browser permission asked once).
- An abnormal stop of the desktop companion is traced in the agent's log; a failed capture falls back to GDI instead of closing the session.

## 2.16.0 — 2026-10-19

- **noVNC console — full screen redesigned (desktop)**: the device's picture takes up **the whole screen**, without a header. The controls (resolution, zoom, Ctrl+Alt+Del, shortcuts, held keys, sending text, clipboard, new tab, closing) move to a **retractable left side rail**; a handle at the edge of the screen unfolds or folds it.
- **The device's cursor fixed in full screen**: noVNC's fallback software cursor (used in particular on touch devices) was rendered outside the full-screen element — therefore **invisible in full screen** and **offset when coming back**. It is now moved into the console for the duration of the full screen, then put back: cursor visible and aligned in both cases.
- Full screen is driven by a class set when the state changes (independent of the engine); the terminal's full screen is unchanged.

## 2.15.0 — 2026-10-19

- **noVNC console — display redesigned**: two independent menus, RDP style. *Resolution* (Fit to window, which changes the Windows resolution to follow the window in physical pixels, or Device resolution, which keeps and restores the original one) and *Zoom* (Fit, or 50/75/100/125/150/200% with scrolling; the pointer stays accurate at any zoom since noVNC's own scale is driven). The resolution and the percentage obtained stay shown. The desktop centers itself, scrolls in both directions and fills the window cleanly without useless black bands.
- **Adaptive JPEG compression of the desktop** (Windows agent 2.15): complex areas (photos, gradients, wallpapers) are sent as JPEG (standard Tight sub-encoding, decoded by noVNC), while text, flat colors and icons stay **lossless** (Tight zlib). Much less bandwidth and a smoother desktop, with no visible loss on the interface. Automatic fallback to lossless zlib if the encoder is not available.
- **Shared text clipboard** (Windows agent 2.15): the console's *Clipboard* panel. The text copied on the device shows in the browser (and, if the browser allows it, is copied into the local clipboard); *Send to the device* puts a text into the device's clipboard to paste it there (Ctrl+V). Accents kept (Latin-1), line endings normalized, bounded size. Only on the user desktop, never on the secure desktop; loco keeps nothing.

## 2.14.0 — 2026-10-19

- **LDAP and RADIUS directories instead of OpenID Connect** (*Administration › Directories*): sign-in from loco's usual form with an **LDAP** account (Active Directory, OpenLDAP, 389 DS, FreeIPA; LDAPS or StartTLS mandatory, optional PEM authority, search through a service account or direct bind, filters escaped according to RFC 4515, empty password refused) or a **RADIUS** account (PAP, shared secret, Message-Authenticator attribute sent and required in the answers against BlastRADIUS). Ordered directories, **Test** button without creating an account, secrets encrypted and never shown again. Each new identity waits for a **manual approval** in *Accounts*; no matching with a local account. Editing or disabling a directory closes the sessions of its accounts, deleting it disables them. Feature that can be turned off. Local accounts always stay available.
- **OpenID Connect removed**: the existing providers are disabled at the update and their transactions erased; the accounts concerned are kept without access and flagged in *Accounts*, where **Local password** converts them into local accounts. The embedded PyJWT library is removed. Rights renamed: "Manage directories", "Approve directory accounts".
- **Software center**: the "WinGet Store" is renamed "Software center" throughout the interface (addresses and rights unchanged).
- **"Operator" profile**: the built-in "Fleet technician" profile is renamed, including on existing installations (unless a custom profile already has that name).
- **Appearance**: menu in the header and tab of *My account*; new **Soft dark** theme (charcoal) next to OLED and Light, and an **accent color** of your choice (neutral, blue, green, purple, orange, pink) for the primary buttons, selections, tabs, focus and active navigation. Secondary texts with more contrast (WCAG AA) in light and OLED.
- **Scripts** (*My fleet › Scripts*): library of PowerShell (Windows) and shell (Linux) scripts with description, versions (a new one at each change of content) and author; run on devices or device groups (only the compatible and active devices are kept), content frozen and checked by its SHA-256 fingerprint on the device, run as SYSTEM or root with a 30-minute time limit; follow-up per run (successful, pending, failures, standard output and errors, exit code) and cancellation of the pending devices. Separate rights: view, manage, run (sensitive, reserved by default to administrators and checked again before sending to the agent). The reviewed version must be the one that runs. Feature that can be turned off. Windows and Linux agents 2.14.0 required.
- **Live progress of deployments**: the Windows agent reports the current step of each installation (download of the installer with a percentage, fingerprint check, installation, application n/total; Windows updates for the Update action), at most once per second. The deployment's page shows for each device a progress bar and the step ("Downloading 7-Zip · 45% · application 1/3"), refreshed every 3 seconds; the device's page repeats the step in its pending actions. Invalid steps are ignored; the old agents keep the display by states.
- **Features that can be turned on** (*Administration › Features*, reserved to the installation's local "admin" account, even for the other administrators): APT packages, RPM packages, Flatpak, Windows Update cache, My fleet and agents, Windows software, Remote access, Scripts, Directories. A disabled feature disappears from the navigation, the buttons and the overview; its pages and API are refused with an explicit message, its rights stop having any effect, the agents receive "try again later" (503) when the fleet is turned off and the installations wait for the Windows software to be turned back on. Dependencies are applied (remote access follows the fleet). On the appliance, the Windows Update, RPM and Flatpak caches are also stopped or started again; elsewhere, the network services stay started (indicated on the page). Each change is logged and the data is kept.
- **The noVNC console's controls panel**: Ctrl+Alt+Del, shortcuts (Start menu, Run, Explorer, Show desktop, Lock, Task manager, Alt+Tab, Alt+F4, Esc, Print screen, context menu), Ctrl/Alt/Shift/Win keys held until the next click, and **Send text** typed character by character whatever the device's keyboard layout (passwords, commands; nothing is kept).
- **Real Ctrl+Alt+Del on Windows**: Windows only accepts the secure attention sequence from a service. The desktop companion signals it to the loco-client service through an event reserved to SYSTEM; the service sends it (SendSAS) only if a remote desktop session is open, allowing the SoftwareSASGeneration policy for the time of the call and then restoring its value. The "Windows Security" screen shows and can be driven in the console. Print screen, Menu and Pause keys added to the Windows agent.
- **My account redesigned**: header with initial, profile, account type and dates; Security (password rules checked live, Show button), Appearance (preview of the themes), My rights (grouped by area with their number) and Recent activity (the account's last ten actions, link to its audit log) tabs. A direct link to a tab (`/compte#apparence`) also works without reloading the page.

## 2.13.4 — 2026-10-19

- Direct link to the agent's MSI: if the server cannot provide its version's MSI yet (GitHub momentarily unreachable…), it answers 503 with the reason and "Try again in a moment", and records it in its log, instead of an unexplained 404.
- The MSI fetched from the release is written under a unique temporary name before being put in place: two simultaneous downloads can no longer get in each other's way.

## 2.13.3 — 2026-10-19

- **Windows agent's MSI as a direct download** in the *Add a device* window, right after the commands: "Download the MSI" button (version and SHA-256 fingerprint shown), direct links to open from the device's browser (HTTPS and, if enabled, HTTP without a certificate warning), then the `msiexec` command ready to copy, which installs and enrolls the device with loco's address, the enrollment key and the authority's fingerprint. A simple double-click on the MSI installs the service without enrolling it: this is stated.

## 2.13.2 — 2026-10-19

- **The remote desktop fills the window**: in automatic mode, the Windows agent chooses among the driver's resolutions the one that best fills the display area (aspect ratio kept), then the closest to 100%. A very wide window gets for example 2560 × 1080 (21:9) instead of 1280 × 720 with large black bands.
- The requested size is computed in screen pixels and not in CSS pixels: with a scaled display (150% on a laptop, for example), the desktop is no longer reduced accordingly, and a desktop pixel matches a displayed pixel. The percentage shown next to the resolution follows the same rule (100% = sharp).

## 2.13.1 — 2026-10-19

- **Windows dynamic resolution fixed**: the automatic mode really changes the Windows desktop's resolution as the window is enlarged or reduced, including in full screen. The agent uses the resolutions accepted by the graphics driver; the initial resolution is restored at closing. Requests during UAC are applied when coming back to the normal desktop. Multi-monitor devices and concurrent sessions keep a display fitting without reconfiguring the screens.
- The requested size follows the whole available surface, without staying limited to the starting resolution or aspect ratio. If the driver imposes a different size, the browser fits the whole desktop to the window, keeping its aspect ratio.
- **Virtual machines**: Windows' basic display driver (standard VGA adapter of a VM without a graphics driver, including Proxmox by default) only offers a single resolution and refuses any change; the picture is then fitted to the window by the browser. For real resizing, use a VirtIO-GPU adapter with its driver (virtio-win) or the physical GPU's driver.
- The service leaves up to five seconds to the desktop companion to restore the original resolution when the session closes.

## 2.13.0 — 2026-10-19

- **Adaptive noVNC console**: the stream's resolution fitted to the window, full screen and the dedicated tab, keeping the aspect ratio and limited at the source for the Windows desktop, and back to the native stream. Bounded RFB ExtendedDesktopSize protocol; fallback to local fitting with the old agents and X11 desktops. The device's physical screen is not reconfigured.
- Consecutive frame requests are grouped to avoid a queue of refreshes during resizes and UAC transitions; a full refresh request stays a priority.
- **Linux text console**: real resizing of the PTY, of the number of columns/rows and of the rendering, including for dimensions that are not multiples of the glyph size.
- **Windows UAC**: SYSTEM graphical companion in the interactive session and following of the input desktop, including the secure desktop. Capture transitions and momentarily blocked input no longer drop the connection. Local channel restricted to SYSTEM and PID check kept; the tray stays unprivileged.
- **Access profiles editor**: categories as navigation, search ignoring accents, filters of granted rights/sensitive actions, counters per area, selection of the visible rights with partial state, indication of the adjusted dependencies and a persistent save bar. Protected profiles and delegation limits kept.

## 2.12.1 — 2026-10-19

- **Windows agents updated after the server**: the server only offered the new version of the Windows agent if its MSI had already been downloaded from the portal (installation button or deployment link). Without that action, the Windows devices stayed on their old version. Now, as soon as a Windows agent older than the server shows up, the server fetches its version's MSI from GitHub (checked by SHA256SUMS) in the background, at most one attempt every ten minutes, and offers it at the next contact.
- Audit log test made independent of the second at which it runs.

## 2.12.0 — 2026-10-19

- **Audit log redesigned**: events grouped by day (today, yesterday, then the date), time only, actions in plain language with their category instead of the technical codes (the code stays in a tooltip and in the export). Failures flagged by a red dot and summed up at the top of the page over the last 24 hours.
- **Server-side filters** over the whole audit log (and no longer only the last 500 events): period (today, 7 days, 30 days, all or dates of your choice), category with its number of events, account, failures only, search; pages of 100 events ("Older"). A click on an account filters its events. On mobile, the detail goes under the action.
- **Export and retention in windows** opened from the header: the export takes the filtered view (or a period, or everything) and adds each action's label and the applied filters to the JSON file.
- **Windows Update redesigned**: indicators over 14 days (served by the cache and share of the traffic, fetched from the Internet), cache size and service state, last request and number of devices; daily chart of the last 14 days; alert if no device has gone through loco yet or if the cache is stopped.
- Redirected domains in a compact list, editable in a window; explanation of how it works folded.
- Latest requests: name of the fleet device instead of its address (*View devices* right), "cache" or "Internet" source.
- **Local catalog redesigned**: badges of the points to handle with their number (updates to deploy on the fleet, missing silent arguments, errors, downloads in progress); columns reduced to the essentials (application, version, equipped devices and those behind with a "deploy" link, size, state). Type and origin go under the name; "Retry" on a failed download and "Fill in" on an installer without arguments, right in the list.
- **An application's page**: header with Edit, Deploy and a More menu (store page, download again, delete); indicators (installer state, equipped devices, devices to update, groups); list of the devices with their version and "Deploy the update". After an edit from the page, you come back to it.
- **WinGet Store redesigned**: compact cards (four per row on a large screen) entirely clickable to the page; **Add** button right on each card, which stays on the current search or category to add more; "in loco", "downloading" or "error" state for the applications already added. Search and categories as chips grouped at the top; duplicate links to the local catalog removed.
- **The store's page**: publisher, version and identifier under the name; actions grouped in the header (Add to loco or See in the local catalog, Deploy); description and installation details (installer, arguments, fingerprint) side by side; warning when the store offers a newer version than the local catalog's.
- **Software and device groups redesigned**: compact list with the description, a preview of the members (applications' icons, devices' names) and their summarized state (applications ready or in error; devices up to date, pending, many updates); "Deploy" and, for machines, "See in My fleet" on each row.
- **Group editor**: members shown first with a live counter, "members only" filter and search; icon, version and state of each application, system and status light of each device; Save bar always visible at the bottom; deleting the group in the More menu. Read-only, only the members are listed.
- The side-by-side fields (groups, profiles, appliance configuration) are aligned again.
- **Deployments redesigned**: list with badges per state (in progress, to confirm, with errors, finished), named applications and a progress bar per deployment (successful, pending, failures). Following a deployment shows four indicators, the progress bar and the devices first, with a failure's log expanded by default; the fingerprints move to a tooltip. Confirm, Abandon and Cancel are in the header, and a deployment that cannot be confirmed says why (another connection, installer not ready, missing right).
- **Preparing a deployment**: selections shown first (with the applications' icons), live counters, compatible devices before the others, and a "Check before deploying" bar always visible with the name and the reinstallation option.
- The lists refreshed live also escape the quotes in the file names requested by the devices.

## 2.11.0 — 2026-10-19

- **My fleet redesigned**: devices sorted from the most urgent to the most up to date; the device's real address (and "via" the address seen by loco when a NAT separates them); compact updates (system, software, including security); restart flagged; groups as clickable labels; last contact in plain language; pending action shown in place of the button.
- **Immediate filters**: state chips with their number (all, many updates, pending, unknown state, up to date), Windows / Linux, group applied as soon as chosen, search; "Update everything" and "Deploy" follow the group shown.
- **Add a device** in a window (Windows and Linux commands, GPO deployment) instead of a panel at the bottom of the page.
- **Settings tab** of the fleet: server address announced to the agents, badge threshold and enrollment key.

## 2.10.1 — 2026-10-19

- **A device's page redesigned**: header with the name, a short status badge (up to date, updates pending, many updates, no recent check-in…), the detailed explanation, the system, the device's address, the last contact in plain language ("2 min ago") and the agent's version; groups as labels.
- Actions grouped on the right: terminal and remote desktop, **Update** as the primary button, and a **More** menu (update with restart, inventory, deployment, restart, then the agent's removal, separate and in red).
- Four indicators (system updates including security, software or Flatpak, restart, inventory) and **tabs** Updates, Installed software (with a filter), History and Details; the chosen tab is kept during the automatic refresh.
- Separate technical details: the device's addresses, the address seen by loco (NAT flagged), the server addresses known to the agent, identifier and groups.

## 2.10.0 — 2026-10-18

- **Charts in the overview**, drawn in SVG by the server (no library or script, OLED and light themes, tooltips on hover):
  - **Windows Update over 14 days**: volume served by the cache and volume downloaded from the Internet, day by day, with the share saved. History rebuilt from the rotating nginx logs (`.1`, `.2.gz`, `.YYYYMMDD`), current log read incrementally;
  - **Fleet state**: green, orange, red, grey ring (*View devices* right);
  - **Data disk**: share of each cache (APT, Windows Update, RPM, Flathub), of the local repositories and software, of the rest and of the free space.

## 2.9.0 — 2026-10-18

- **Changing the server's address without losing the agents**: the future address (IP or DNS name) is announced in *My fleet › Server address for agents* or with `loco-fleet announce https://host:port`. Each agent receives it at its next contact; the page and `loco-fleet servers` show how many devices have been told.
- After the change, an agent that no longer reaches the old address tries those it knows and adopts the first that answers with a certificate of the pinned loco authority: an announced address cannot divert the devices to another server. The server's HTTP answers (token refused…) never trigger a switch.
- `setup-loco` (appliance) announces the new static address and waits for the devices to be told before applying the network; with DHCP, it warns that the agents will not be able to follow.
- Windows and Linux agents 2.9.0: list of addresses remembered and declared to the server, shown by `loco-client status`.

## 2.8.0 — 2026-10-18

- **Unified software management**: local catalog, WinGet store, software groups and deployment follow-up in the same tabs. Deployment from a store page, from the catalog or from a device; shared WinGet import and verified fingerprint kept across new attempts.
- **Software and device groups**: multiple memberships, creation and editing of the members; fleet filter and update limited to the group shown. Deleting a group without deleting its applications, devices or deployments.
- **Prepared and confirmed deployments**: combination of applications, batches, devices and groups, deduplication, explicit Windows/Linux exclusions, frozen selection and versions. Confirmed installers kept even after the catalog is edited or deleted; backup compatible with these versions.
- **Follow-up per device**: pending commands for offline agents, logs and results, cancellation of only the commands not yet sent. Versions already installed and identical installations already pending skipped; explicit reinstallation possible.
- **Separate rights**: managing both types of groups, viewing the deployments and deploying to several devices. Collective deployment requires a right reserved to administrators by default, checked again before sending to the agents.

## 2.7.0 — 2026-10-18

- **Navigation**: My fleet comes right after Overview.
- **Linux without a desktop**: the noVNC console renders a real PTY terminal and its ANSI colors in the agent's RFB stream, without X11 or a connection to Proxmox. The fallback also requires the Remote terminal right; a Wayland desktop does not trigger an implicit shell. Dedicated console, real size and full screen stay available.
- **SSO sign-ins**: several providers, templates for Microsoft Entra ID, Google, GitHub, GitLab, Keycloak, Authentik, Okta, Auth0, Authelia, ZITADEL, Dex, Cognito, AD FS and generic OIDC. GitHub uses OAuth 2; the others use OpenID Connect.
- **Mandatory approval**: a new SSO account has no right or session before administrative validation and explicit assignment of a profile. Identities are never matched automatically by e-mail. Secrets are encrypted; editing, disabling or deleting a provider revokes its sessions. Local sign-in is kept.

## 2.6.1 — 2026-10-18

- **Real cursor in the remote desktop**: the Windows and Linux agents send the shape of the device's cursor (arrow, text, hand, wait, resize…) with its transparency and its hotspot; the browser shows it like a native cursor, without offset or latency. Light or inverting cursors outlined to stay readable on all backgrounds; the dot is now only a fallback.
- **Audit log**: JSON export of a period or of the whole audit log, the export itself being logged; configurable retention (7 days to 2 years or without a limit), **30 days by default**, with automatic rotation. New *Export the audit log* (Auditor profile) and *Configure audit log retention* rights; the built-in profiles follow the new rights.
- A device's page: remote access buttons aligned and "new tab" icon centered; same fix for the other rows of buttons.

## 2.6.0 — 2026-10-17

- **Console in a dedicated tab**: terminal and noVNC without the portal's navigation, compact toolbar and the available surface for the device; full screen, back to its page and explicit closing.
- **Native, sharp desktop**: Windows capture without a forced reduction to 1920 × 1080, lossless Tight compression on Windows and X11, fitted display without artificial enlargement or real 1:1 size with scrolling and resolution indication.
- **Smoothness**: changed rectangles instead of full pictures, grouping of the packets, immediate sending of the input and of the pending streams, persistent HTTPS connection on Linux, removal of useless TCP waits on Windows. Bounded queues and acknowledgments kept, resumption without replay when the receiver slows down.

## 2.5.0 — 2026-10-16

- **Granular access profiles**: 46 per-action permissions, reusable custom profiles and Administrator, Read-only, Fleet technician, Repository manager and Auditor templates. Duplicating the templates, assignment per account and viewing one's own rights.
- Separate rights for inventory, individual or global updates, installation, restart, agent removal, terminal and noVNC. Viewing and managing the repositories, the store, the server, the accounts, the profiles and the audit log separated.
- Server-side check of each private route and adapted navigation. Delegation limited to the rights held, built-in profiles and one's own access protected, last active administrator preserved during concurrent changes.
- Revocation of the connections and remote sessions after a change of rights; cancellation of the commands still pending and check of the rights before they are sent to the agent. Detailed audit of the permissions granted and withdrawn.
- Migration preserving the existing accounts and passwords. CLI `loco-user roles` and `loco-user set-role`. Sign-in form immediately usable after a session is revoked; fix of sending the fleet's actions from the browser.

## 2.4.0 — 2026-10-16

- **Remote access through the agent**: interactive Linux PTY terminal and native Windows PowerShell ConPTY, with resizing, stopping at closing and an outbound HTTPS relay. No access to the Proxmox consoles.
- **noVNC desktop**: keyboard and mouse on an open Windows session or a Linux X11 desktop, without a VNC server to install or an inbound port to open. Windows companion included in the MSI; browser libraries served locally.
- **Session windows**: full screen with a toolbar and an explicit exit, and opening in a new tab from the device's page or an ongoing session.
- Access reserved to administrators, sessions tied to their connection, revocation and expiry delays, acknowledged streams without replay, temporary queues excluded from the backups. Audit of openings and closings without the sessions' content.

## 2.3.0 — 2026-10-16

- Windows software store fed by Microsoft's WinGet community catalog: categories, paginated search, pages, verified and cached logos, import of verified installers into loco; the last valid index can be viewed offline.
- Windows agent: notification icon per session, green / orange / red dot and read-only details. The user cannot start any update from the icon; the tokens stay private and the service only publishes the device's state.
- Same count in My fleet and on Windows: green no updates, orange from 1 to 9, red from 10; configurable red threshold. An unavailable state stays grey. Installation, session start and removal of the companion handled by the MSI.

## 2.2.0 — 2026-10-16

- **My fleet**: `loco-client` agent for Windows (.NET Framework service, MSI installer deployable by GPO) and Linux
  (Debian, Ubuntu, Rocky Linux, AlmaLinux, RHEL; systemd). Green / orange / red light per device, IP address,
  pending updates (including security), installed software and history of the actions.
- Updating one device or the whole fleet in one click (Windows Update, APT, DNF, Flatpak, catalog software),
  optional restart, installation of a catalog application, inventory, restart and agent removal.
- Device identity independent of its IP address: a change of address does not detach it; only the removal
  pushed from loco uninstalls the agent. Pinned HTTPS authority, hashed tokens, closed list of actions.
- Windows software: detection of the versions installed on the fleet; search and import from the winget catalog
  (official installer checked by SHA-256, silent options and detection name), and new winget versions.
- Automatic update of the agents; MSI built on Linux (Mono, msitools) by the CI and published with the version.

## 2.1.3 — 2026-10-16

- ISO installation and `setup-loco`: choice between SSH with the root password, SSH by public key, or SSH disabled. The password mode asks for no public key.
- Automatic answers: `SSH_MODE=password`, `key` or `off`; the old files only using `SSH_KEY` stay compatible.
- SSH configuration shared by the installation and the console; empty passwords refused, key mode without password authentication, tests of real SSH connections.

## 2.1.2 — 2026-10-16

- Portal: an HTTP request received on the HTTPS port redirects automatically to HTTPS instead of showing "400 Bad Request"; the host, port, path and parameters are kept.
- Common configuration for Linux, Alpine/ISO, Docker and Windows; the redirect checked by real GET, HEAD and POST requests in the installation tests.

## 2.1.1 — 2026-10-16

- ISO: login console fixed on tty1 to prevent the getty from stealing the wizard's keyboard and screen on tty7.
- Esc and Home go back to the menu without opening a shell; the troubleshooting console is explicit and `exit` brings you back to the wizard.
- Cancelling the feature selection respected; test of the keyboard, the cancellations and the console in a VM.

## 2.1.0 — 2026-10-15

- Alpine 3.24 / BusyBox ISO appliance: visual wizard, automatic installation by answers, BIOS and UEFI, embedded packages and configured first boot.
- Graphical and CLI administration: configuration of the access, the features and the caches, updates of loco and the system, live logs; `setup-loco`, `loco-config` and `loco-update`.
- OpenRC agent separate from the unprivileged portal; requests validated again, operations serialized and rollback of loco / configuration / database in case of failure.
- Alpine becomes a platform of the common Linux installer, with an nginx APT cache shared with Windows, Python signatures and package readers, backups and uninstallation.
- Native Alpine CI and ISO: verified archives, provoked rollback, BIOS / UEFI installations and restarts without media; publication of the sources, validated ISO and SHA256SUMS.

## 2.0.0 — 2026-10-15

- Complete renaming to **loco**: GitHub repository, portal, Python modules, services, system account, commands, paths and device configurations.
- Migration of the existing Linux and Windows servers when the installer runs again, with a prior backup and keeping the accounts, Linux UIDs, certificates, keys, repositories and caches.
- The existing APT repositories keep their signed Origin/Label so that the old devices carry on with their updates without a manual confirmation; the new repositories carry loco.
- Update of the APT/RPM devices at the next setup, migration of the Flatpak origins; old Linux and Windows backups can be restored.
- `linux/docker/up.sh` takes over the LR_ variables and copies the historical volumes to the loco project; the original volumes are kept and the old container restarts if the migration fails.
- CI: migrations from local-repo 1.9.0 on Debian, Ubuntu, Rocky, Docker and Windows Server 2022/2025.

## 1.9.0 — 2026-10-15

- **The project organized by system**, in a single branch: `linux/` (installer, administration,
  Docker, Proxmox LXC container), `windows/` (installer and Windows service), common `portal/` and `conf/`,
  `docs/linux.md` and `docs/windows.md`. `sudo ./install.sh` stays valid (shortcut to `linux/install.sh`),
  as do the updates of the existing installations; the installed tree does not change.
- Docker: `cd linux/docker && docker compose up -d --build`; fixed project name, volumes kept.
- LXC container: `linux/lxc/proxmox-lxc.sh`.
- README: choice of the installation, then a Linux server part and a Windows server part.

## 1.8.0 — 2026-10-15

- Native Windows 11 Pro / Enterprise and Windows Server 2022 / 2025 server: Windows service, embedded Python, nginx and Waitress, without Linux or Hyper-V. OpenPGP signatures of the repositories
  (RSA 4096 key) produced in Python: no GnuPG to install, no change to the `PATH`.
- Rerunnable PowerShell installer, components checked by SHA-256, virtual service account, ACLs and firewall rules limited to the allowed networks.
- APT and RPM repositories indexed and signed on Windows with Python readers, compared with the native Linux tools; reading the metadata without loading the whole package into memory.
- nginx APT cache, RPM/Flathub/Windows Update caches and Windows catalog; the local Flatpak repository stays reserved to Linux.
- Administration, SQLite backups, restore with the service stopped, HTTPS renewal and uninstallation keeping the data or with an explicit purge.
- Automatic tests of the Windows Server 2022 and 2025 servers, gzip/xz/zstd package imports, signatures, cache and maintenance cycle.

## 1.7.0 — 2026-10-14

- Native installation of the server on Rocky Linux 8, with the same `install.sh` and the same features as Rocky 9/10.
- Isolated Python 3.11, PowerTools repository and APT 2.6.1 indexer built from the Debian sources with a SHA-256 check; no replacement of the system Python.
- Selection of the nginx 1.24 AppStream stream on Rocky 8 if the nginx present does not provide `stream_ssl_preread`, needed by the Windows Update relay.
- SQLite backups run with the application's runtime on Rocky 8, compatible with the online copy of the database.
- Generation of the HTTPS authority compatible with OpenSSL 1.1, without duplicate extensions, and verification of the server certificate by this authority.
- Uninstallation of the private indexer and restoration of the PowerTools repository if the installer had enabled it.
- Full Rocky 8 test added to the CI, with SELinux and firewalld active.

## 1.6.0 — 2026-10-14

- Native installation of the full server on Rocky Linux 9 and 10 with the same `install.sh`: DNF, EPEL/CRB, nginx, APT cache, APT/RPM repositories, Flatpak and Windows Update.
- SELinux kept active, dedicated firewalld service if the firewall is running, APT cache filtered by nginx with a loopback backend.
- Rocky 10: APT cache built from the Debian 3.7.5-1.3 sources with pinned patches and fingerprints.
- Uninstallation adapted to DNF, restoration of the SELinux, firewall and nginx settings added by the application.
- Server tests in Rocky 9/10 VMs, added to the CI; installation compatible with a restrictive umask.

## 1.5.1 — 2026-10-14

- Overview, *Connect a device*: one tab per type of device (Debian / Ubuntu; Rocky Linux,
  AlmaLinux, RHEL; Flatpak; Windows), each with its command, the first connection and the link to
  its options. Windows specifies that Windows Update goes through DNS, without installing anything on the device.
- Tabs on a single line, scrolling on a small screen.

## 1.5.0 — 2026-10-14

- Temporary password (first sign-in, reset): dedicated page, without the portal's
  navigation, with the possibility to sign out; no other page is reachable before the change.
- Theme: OLED by default, switch to the light theme with a discreet icon (moon) in the
  navigation bar and on the sign-in screens; choice remembered by the browser.

## 1.4.0 — 2026-10-14

- **Proxmox VE LXC container**: `lxc/proxmox-lxc.sh`, to run on any Proxmox VE 7, 8 or 9 host,
  creates an unprivileged Debian container (storage, bridge and template chosen according to the host, static IP or DHCP,
  VLAN) and does the native installation in it; `install.sh` options passed after `--`; update with
  `--update`, version pinned with `--version`, local copy with `--source`.

## 1.3.0 — 2026-10-14

- **Docker deployment** (`docker compose up -d --build`), in addition to the native installation, which stays
  recommended: Debian 13 image, configuration by variables (`.env.example`) applied at each start
  by `install.sh --container`, services started by `supervisord`, periodic tasks (statistics,
  nginx reload, log rotation, daily backup) by `local-repo-cron`.
- Container without superfluous capabilities, `no-new-privileges`, service accounts with fixed identifiers,
  health check, volumes for the data, the configuration and the backups.
- The portal recognizes the container mode: services' state, help commands and restore adapted.
- Validation in a disposable Debian 13 VM (`tests/vm-docker-e2e.sh`).

## 1.2.0 — 2026-10-13

- **Red Hat family**: cache of the Rocky Linux, AlmaLinux and EPEL mirrors (packages and uniquely named
  metadata kept 6 months, mirrors' certificates checked); local RPM repositories per major version
  (`el8`, `el9`, `el10`, adding a future version from the portal), signed metadata, refusal of
  packages of another version or of a different content for the same name/version.
- `rpm/setup.sh` script: detection of the device, local repository, official repositories and EPEL through the cache with their
  GPG keys, `--epel`, `--trust-ca`, `--no-cache`, `--no-local`, `--remove` (exact restoration of the repositories).
- RHEL: local repository and EPEL through this server; Red Hat repositories (subscription) in direct access.
- `--no-rpm` and `--rpm-cache-size` options; backup, statistics, `/metrics` and uninstallation
  cover the RPM repositories.
- Real tests (`rpmbuild`, `createrepo_c`, signatures) and validation of Rocky Linux / AlmaLinux 8, 9
  and 10 devices in VMs, added to the CI.

## 1.1.0 — 2026-10-13

- APT repositories isolated by distribution and version name, created from the portal or `local-repo-apt`.
- Automatic detection of Debian / Ubuntu devices, deb822 sources; `--cache-only`,
  `--distribution`, `--suite`, `--legacy` options. A missing version does not replace the device's configuration.
- Legacy repository kept, explicit copy of packages between repositories; publishing without replacing
  a package/version with a different content; signed metadata prepared before publishing.
- Backup and restore of all the repositories, migration of the versions' registry.
- Compatibility fixes: APT HTTPS in direct access, the cache's outbound connections kept,
  cleaning of the devices' cache when the repository changes; support for the control channel
  of the new Gunicorn versions and bounded delays to check the startup.
- Isolation and signature tests, migration test from 1.0.0; VM matrix of Debian 12/13 and
  Ubuntu 22.04/24.04/26.04, verified official images, weekly checks active in the CI.
- Linux / Windows navigation with icons and menus on hover, keyboard and touch; account icons.

## 1.0.0 — 2026-10-13

First version intended for enterprise use.

### Added
- **Flatpak**: Flathub cache (immutable objects kept 6 months, Flathub signatures checked by the devices)
  and signed local Flatpak repository, fed by uploading `.flatpak` packages; `flatpak/setup.sh` script.
- **Named accounts** (administrator, read-only), temporary password to change, lockout after failures,
  session expiry, audit log, command-line management (`local-repo-user`).
- **HTTPS everywhere**: client devices over HTTPS by default, scripts following the protocol used,
  `setup.sh --trust-ca` for bootstrapping, `--no-http` to close the HTTP port.
- **Allowed networks** (`--allow`, `--admin-allow`) applied by nginx, apt-cacher-ng and systemd.
- **Corporate proxy** (`--proxy`) for apt-cacher-ng and the portal's downloads.
- Daily **backups** with rotation, restore on the same server or a new one
  (`local-repo-backup`).
- **Monitoring**: `/metrics` (Prometheus) and `/healthz`.
- **Administration** page (accounts, audit log, system, backups).
- Unit tests, end-to-end test (`tests/e2e.sh`) and GitHub continuous integration.
- Operations guide (`docs/EXPLOITATION.md`) and security policy (`SECURITY.md`).

### Changed
- Strict content security policy (CSP): no more inline scripts or styles.
- The portal's systemd unit confined; apt-cacher-ng passwords for its maintenance.
- Portal reorganized as a Python package; SQLite database in WAL with versioned migrations.
- Interface: grouped navigation, theme selector in *My account*.

### Security
- The single account of the previous versions becomes the `admin` account (same password);
  the `/etc/local-repo/admin-password` file is deleted.

## 0.3 — 2026-10-13
- Interface over HTTPS (local authority), sober visual redesign (IBM Plex, light and OLED themes).

## 0.2 — 2026-10-13
- Windows Update cache limited to the domains of the list, only immutable files cached,
  fixes from the tests (Debian 12/13, Ubuntu 24.04, Windows 11).

## 0.1 — 2026-10-12
- First version: apt-cacher-ng and local APT repository, Windows Update cache, Windows software catalog,
  web portal, `install.sh` and `remove.sh`.
