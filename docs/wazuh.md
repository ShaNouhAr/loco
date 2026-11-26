**English** · [Français](fr/wazuh.md) · [Español](es/wazuh.md)

# Wazuh agent

**Optional** — to turn on in *Administration › Features › Optional*.

You have a [Wazuh](https://wazuh.com) manager and a fleet already enrolled in loco: loco installs the Wazuh agent on the
devices you choose, set up for your manager, and tells you where it stands. When you remove the agent from a device,
loco can also delete its entry on the manager. It does not replace Wazuh and does not configure your manager.

**Contents** —
[Settings](#settings) ·
[Install](#installing-on-devices) ·
[Follow](#following-the-state) ·
[Remove](#removing) ·
[Delete the manager's entry](#deleting-the-managers-entry) ·
[What happens on the device](#what-happens-on-the-device) ·
[Rights](#rights) ·
[If it does not connect](#if-it-does-not-connect)

---

## Settings

*My fleet › Wazuh*, **Settings** block:

| Setting | What it is |
|---|---|
| **Manager address** | the name or IP address of your Wazuh manager, without `https://` or port |
| **Agent version** | to choose from the list of versions published by Wazuh. **It must not be newer than the manager's**: a 4.14.9 manager refuses a 4.14.10 agent |
| **Agent group** *(optional)* | the Wazuh group to put the devices in (`servers`, `docker`…). It must already exist on the manager; several groups are separated by commas |
| **Enrollment password** *(optional)* | the manager's, if it requires one (`<use_password>yes</use_password>`, `authd.pass` file) |

The password is kept encrypted. loco never shows it again and does not write it to the audit log: only the fact that it
changed appears there.

Without a password in loco (*stop sending one* box), a reinstallation removes from the device the `authd.pass` file
placed earlier: a manager that does not expect a password refuses an agent that sends one.

## Installing on devices

Two ways:

- **One device** — on its page, *More › Install the Wazuh agent*; or the *Install* button on its row, Wazuh page.
- **Several** — Wazuh page: tick the devices, adjust the agent group if needed, then
  *Install on the ticked devices*.

The installation goes out at the device's next check-in — in a few seconds if it is on, when it comes back otherwise.
The first time, loco first downloads the package: the command waits until it is ready.

Supported systems: **Windows**, **Debian and Ubuntu**, **RHEL, Rocky Linux and AlmaLinux** (x86-64; 64-bit ARM
on Linux). Android is not concerned.

> Devices do not need the Internet. loco downloads the official package (`deb`, `rpm` or `msi`) once from
> `packages.wazuh.com`, checks it against the SHA-512 fingerprint Wazuh publishes, then hands it to the devices through their loco
> agent, which checks its fingerprint in turn.

*Apply again* (device already equipped) reinstalls the agent with the current settings: useful after a change of manager
or password. *Update* appears when the device's version is older than the one set.

## Following the state

The Wazuh page and each device's page (*Details* tab) show what the device reports at each check-in:

| State | What it means |
|---|---|
| **Connected** | the agent runs and talks with the manager |
| **Waiting for the manager** | installed and started, not yet accepted — see [below](#if-it-does-not-connect) |
| **Disconnected** | the manager no longer answers this device |
| **Stopped** | the Wazuh agent service is not running |
| **Not installed** | no Wazuh agent on this device |
| **Wazuh manager** | this device is the manager itself: loco neither installs nor removes an agent there, and does not count it among the devices to equip |
| **Unknown** | the device's loco agent is older than 2.166.0: it updates itself, the state follows |

They come with the installed version, the agent's number and name at Wazuh, and a note when the device is
linked to another manager than the one set.

## Removing

Device page, *More › Remove the Wazuh agent…*, or *Remove* on its row. The agent is uninstalled from the device.

If loco has access to the manager (below), it then deletes the device's entry on the manager, and the removal
report, in the device's history, says so: "Entry 003 removed from the Wazuh manager." Otherwise, the entry stays on the
manager: delete it there if the device should no longer appear.

## Deleting the manager's entry

*My fleet › Wazuh*, **Removing manager entries** block — optional, to set up once.

loco connects to the manager over SSH, with a key of its own, and runs only one command there:
`manage_agents -r <number>`. Three steps:

1. **Authorize loco's key** — loco shows a line to add to the `~/.ssh/authorized_keys` file of the chosen
   account on the manager (`root` in general, since `manage_agents` requires administration rights):

   ```
   restrict,command="/var/ossec/bin/manage_agents -r $(printf %s \"$SSH_ORIGINAL_COMMAND\" | tr -cd 0-9)" ssh-ed25519 AAAA… loco@…
   ```

   With it, the key can only run `manage_agents -r`, with only the **digits** of what it sends: no
   shell, no redirection, no other command (`restrict` also cuts the terminal and tunnels). The private key stays on the
   loco server.
2. **Read the host key** — give the account and the SSH port; loco reads the key the manager presents and
   shows its fingerprint. Compare it with the manager's (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), then
   confirm: loco will only talk with that key.
3. **Try** — *Try the access* asks the manager to remove a number that no agent has: nothing is removed,
   but loco knows the access works. It warns if the key opens a full shell (line without `command=`).

Afterwards, at each successful removal, loco deletes the device's entry. To be safe, it only does it if:

- the device was linked to the manager that is set (not another one);
- the device reports the agent as properly uninstalled;
- the agent's number is known — never `000`, the manager itself.

Changing the manager's address in the settings forgets the host key: it is confirmed again. *Remove the access*
cuts everything at once; also remove the line from `authorized_keys` on the manager.

## What happens on the device

The loco agent, which runs as root (SYSTEM on Windows):

1. receives the package from loco and checks its SHA-256 fingerprint;
2. installs it with the system's package manager (`apt-get`, `dnf` or `msiexec`), giving the package the address of the
   manager and the group — the deployment variables provided by Wazuh;
3. writes the enrollment password, if there is one, to the file the Wazuh agent reads:
   `/var/ossec/etc/authd.pass` (`root:wazuh`, mode 640) on Linux, `authd.pass` in the agent's folder on
   Windows (SYSTEM and administrators only);
4. enables and starts the service (`wazuh-agent`, `WazuhSvc` on Windows);
5. waits about thirty seconds for the first exchange with the manager, then reports.

The password is never passed as a command argument nor written to an output: it travels in the command
handed to the loco agent, through its HTTPS channel, and loco does not keep it with the command.

On a device already equipped, the package update keeps `ossec.conf`: loco only replaces the manager's address there
if it has changed.

## Rights

Three rights, in the access profiles:

| Right | Allows |
|---|---|
| **View Wazuh** | seeing the page and the devices' state |
| **Install and remove the Wazuh agent** | installing the agent, updating it, removing it — on the devices of their profile's fleet |
| **Configure Wazuh** | manager address, enrollment password, group and version, loco's access to the manager |

The Administrator profile has all of them; other profiles must receive them. Rights are checked again when
the command goes out to the device.

## If it does not connect

The installation report (device history) says so: "installed and started; not connected yet".

| To check | How |
|---|---|
| Does the device reach the manager? | ports **1514** (exchanges) and **1515** (enrollment) open from the device to the manager |
| The version | the agent must not be newer than the manager |
| `Invalid password` | the password does not match the manager's `/var/ossec/etc/authd.pass` (extra space or line break included) |
| `Invalid request for new agent` | the device sends a password while the manager does not expect one (`<use_password>no</use_password>`): tick *stop sending one*, then *Apply again* |
| The group | it must exist on the manager (`/var/ossec/bin/agent_groups -l`) |
| An old agent with the same name | an entry with the same name on the manager can block enrollment: remove it, then *Apply again* |

On the device, the Wazuh agent's log says why: `/var/ossec/logs/ossec.log` (Linux),
`C:\Program Files (x86)\ossec-agent\ossec.log` (Windows).

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
