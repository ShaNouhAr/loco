"""Réveil à distance (Wake-on-LAN) des postes éteints.

Un paquet magique ne traverse pas les routeurs : il doit partir du réseau du poste. Le réveil n'est donc proposé que si le poste a
une carte Ethernet branchée à son dernier contact, et si quelque chose peut émettre sur ce réseau : le serveur tout de suite, ou un
poste allumé à son prochain contact. En Wi-Fi, un poste éteint n'écoute plus rien : le réveil n'est jamais proposé."""
import datetime
import ipaddress
import json
import re
import socket
import struct
import subprocess
import time

from flask import g

from . import app, config
from .db import db
from .i18n import _

PORT = 9
MAX_CARDS, MAX_NETWORKS = 16, 16
MAX_TARGETS = 64                # par commande de relais
RELAYS = 2                      # postes allumés sollicités par réseau : l'un peut s'éteindre entre-temps
RELAY_VERSION = (2, 134)        # agents qui savent relayer
BACK_MINUTES = 10               # ensuite, le poste est considéré comme n'ayant pas répondu
RELAY_MINUTES = 5               # un relais revenu plus tard ne réveille plus personne à l'improviste
MAC_RE = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
NOT_BROADCAST = re.compile(r"lo$|docker|br-|veth|virbr|vboxnet|vmnet|vethernet|cni|flannel|cali|lxc|lxd|podman|tun|tap|wg|tailscale|zt|wsl|vpn|loopback", re.I)
SERVER_TTL = 60

_server = {"until": 0.0, "networks": {}}


def _clean_networks(values):
    """Adresses 192.168.1.5/24 acceptables : IPv4 d'un vrai réseau local, où une diffusion a un sens."""
    found = []
    for value in values if isinstance(values, list) else []:
        try:
            address = ipaddress.ip_interface(str(value)[:45])
        except ValueError:
            continue
        ip = address.ip
        if (address.version == 4 and 8 <= address.network.prefixlen <= 30 and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)
                and str(address) not in found):
            found.append(str(address))
    return found[:MAX_NETWORKS]


def clean_info(data):
    """Cartes et réseaux annoncés par l'agent, validés, pour la base ; None si cet agent ne les annonce pas."""
    if not isinstance(data.get("cards"), list):
        return None
    cards = []
    for raw in data["cards"][:MAX_CARDS]:
        if not isinstance(raw, dict):
            continue
        mac = str(raw.get("mac", "")).lower().replace("-", ":")
        if not MAC_RE.fullmatch(mac) or mac == "00:00:00:00:00:00" or int(mac[:2], 16) & 1 or raw.get("kind") not in ("ethernet", "wifi"):
            continue
        cards.append({"name": str(raw.get("name", ""))[:60], "mac": mac, "kind": raw["kind"], "up": raw.get("up") is True, "usb": raw.get("usb") is True,
                      "wol": raw.get("wol") if raw.get("wol") in ("on", "off", "none") else "", "networks": _clean_networks(raw.get("networks"))})
    return json.dumps({"cards": cards, "networks": _clean_networks(data.get("networks"))}, ensure_ascii=False)


def info(dev):
    try:
        found = json.loads(dev["net_info"] or "{}")
    except (ValueError, TypeError):
        found = {}
    return found if isinstance(found, dict) else {}


def _network(address):
    return str(ipaddress.ip_interface(address).network)


def _list_server_addresses():
    """(nom de l'interface, adresse/préfixe) de ce serveur."""
    if config.WINDOWS:
        script = "Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred | ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress + '/' + $_.PrefixLength }"
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15).stdout
        return [tuple(line.strip().split("|", 1)) for line in out.splitlines() if "|" in line]
    return _linux_addresses()


def _linux_addresses():
    """Par SIOCGIFCONF et SIOCGIFNETMASK : le service du portail n'a pas accès au netlink (RestrictAddressFamilies), donc pas à « ip »."""
    import array
    import fcntl
    siocgifconf, siocgifnetmask = 0x8912, 0x891b
    size = 40 if struct.calcsize("P") == 8 else 32       # struct ifreq
    found = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        buffer = array.array("B", bytes(size * 64))
        answer = fcntl.ioctl(sock.fileno(), siocgifconf, struct.pack("iP", len(buffer), buffer.buffer_info()[0]))
        data = buffer.tobytes()[:struct.unpack("iP", answer)[0]]
        for start in range(0, len(data), size):
            raw = data[start:start + 16].split(b"\0")[0]
            name, address = raw.decode(errors="replace"), socket.inet_ntoa(data[start + 20:start + 24])
            mask = fcntl.ioctl(sock.fileno(), siocgifnetmask, struct.pack("16sH", raw, socket.AF_INET).ljust(size, b"\0"))
            prefix = bin(int.from_bytes(mask[20:24], "big")).count("1")
            found.append((name.split(":")[0], f"{address}/{prefix}"))
    return found


def server_networks():
    """{réseau : adresse du serveur sur ce réseau}, relu au plus une fois par minute."""
    now = time.monotonic()
    if now < _server["until"]:
        return _server["networks"]
    found = {}
    try:
        for name, address in _list_server_addresses():
            if not NOT_BROADCAST.match(name):
                for clean in _clean_networks([address]):
                    found.setdefault(_network(clean), clean.split("/")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    _server.update(until=now + SERVER_TTL, networks=found)
    return found


def context():
    """Qui peut émettre sur quel réseau : le serveur, et les postes allumés du parc de ce compte, les plus récents d'abord."""
    from . import fleet, scope
    found = {network: {"server": address, "relays": []} for network, address in server_networks().items()}
    alive = (datetime.datetime.now() - datetime.timedelta(seconds=fleet.ONLINE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db().execute("SELECT * FROM devices WHERE state='active' AND net_info!='' AND last_seen>=?" + scope.sql() + " ORDER BY last_seen DESC", (alive,))
    for dev in rows.fetchall():
        if fleet.version_key(dev["agent_version"]) >= RELAY_VERSION:
            for address in info(dev).get("networks") or []:
                found.setdefault(_network(address), {"server": "", "relays": []})["relays"].append(dev)
    return found


def offer(dev, senders):
    """Le réveil de ce poste est-il possible, et par qui ? Appelé pour un poste allumé aussi : la fiche l'annonce d'avance."""
    known = info(dev)
    refused = {"ok": False, "note": "", "card": None, "network": "", "server": "", "relays": []}
    cards = known.get("cards")
    if cards is None:
        return dict(refused, reason=_("Cet agent n’indique pas encore ses cartes réseau : il le fera à sa prochaine mise à jour."))
    ethernet = [c for c in cards if c.get("kind") == "ethernet"]
    if not ethernet:
        return dict(refused, reason=_("Aucune carte Ethernet : un poste en Wi-Fi ne peut pas être réveillé."))
    plugged = [c for c in ethernet if c.get("up") and c.get("networks")]
    if not plugged:
        return dict(refused, reason=_("Carte Ethernet %(name)s débranchée à son dernier contact.", name=ethernet[0].get("name", "")))
    usable = [c for c in plugged if c.get("wol") not in ("off", "none")]
    if not usable:
        card = plugged[0]
        return dict(refused, reason=_("La carte %(name)s ne gère pas le réveil.", name=card.get("name", "")) if card.get("wol") == "none"
                    else _("Réveil désactivé sur la carte %(name)s : activez-le dans le BIOS et dans ses propriétés (Gestion de l’alimentation).", name=card.get("name", ""))
                    if dev["os_family"] == "windows"
                    else _("Réveil désactivé sur la carte %(name)s : activez-le dans le BIOS et avec « ethtool -s %(name)s wol g », à rendre permanent dans la configuration réseau.", name=card.get("name", "")))
    missing = []
    for card in usable:
        for address in card["networks"]:
            network = _network(address)
            sender = senders.get(network) or {}
            relays = [r for r in sender.get("relays") or [] if r["id"] != dev["id"]]
            if sender.get("server") or relays:
                return {"ok": True, "reason": "", "card": card, "network": network, "server": sender.get("server") or "", "relays": relays,
                        "note": _("Adaptateur USB : le réveil n’est pas garanti.") if card.get("usb") else ""}
            missing.append(network)
    return dict(refused, reason=_("Ni le serveur ni un poste allumé ne sont sur le réseau %(network)s pour envoyer le réveil.", network=", ".join(dict.fromkeys(missing))))


def magic_packet(mac):
    return b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16


def send_packet(mac, network, source):
    """Diffuse le paquet magique sur ce réseau, depuis l'adresse du serveur qui y est connectée."""
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((source, 0))
        for address in (str(ipaddress.ip_network(network).broadcast_address), "255.255.255.255"):
            sock.sendto(packet, (address, PORT))


def _track(conn, dev, via, user):
    """Ligne d'historique du poste à réveiller : soldée à son retour, ou en échec au bout de quelques minutes."""
    from . import fleet
    running = conn.execute("SELECT id FROM device_commands WHERE device_id=? AND action='wake' AND state='running'", (dev["id"],)).fetchone()
    if running:
        conn.execute("UPDATE device_commands SET sent_at=?, output=? WHERE id=?", (fleet._now(), via, running["id"]))
    else:
        conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, sent_at, created_by, output) VALUES (?,?,?,?,?,?,?)",
                     (dev["id"], "wake", "running", fleet._now(), fleet._now(), user, via))


def wake(devices, user=None):
    """Réveille ces postes : le serveur émet tout de suite sur ses réseaux, ailleurs un poste allumé relaie à son prochain contact.
    Renvoie {direct: [noms], relayed: [(nom, relais)], skipped: [(nom, raison)]}. Le paquet ne vise que les MAC remontées par les agents."""
    from . import fleet
    conn = db()
    user = user if user is not None else g.user["username"]
    senders = context()
    result = {"direct": [], "relayed": [], "skipped": []}
    plan = {}
    for dev in devices:
        found = offer(dev, senders)
        if not found["ok"]:
            result["skipped"].append((dev["hostname"], found["reason"]))
            continue
        if fleet.presence(dev)["online"]:
            result["skipped"].append((dev["hostname"], _("Ce poste est déjà en ligne.")))
            continue
        if found["server"]:
            try:
                send_packet(found["card"]["mac"], found["network"], found["server"])
                _track(conn, dev, _("Paquet de réveil envoyé par le serveur."), user)
                result["direct"].append(dev["hostname"])
                continue
            except OSError as e:
                app.logger.warning("réveil de %s : %s", dev["hostname"], e)
        relays = found["relays"][:RELAYS]
        if not relays:
            result["skipped"].append((dev["hostname"], _("Envoi du réveil impossible depuis le serveur, et aucun poste allumé sur ce réseau.")))
            continue
        item = plan.setdefault(found["network"], {"relays": relays, "targets": []})
        item["targets"].append(found["card"]["mac"])
        _track(conn, dev, _("Réveil confié à %(relay)s.", relay=relays[0]["hostname"]), user)
        result["relayed"].append((dev["hostname"], relays[0]["hostname"]))
    for network, item in plan.items():
        broadcast = str(ipaddress.ip_network(network).broadcast_address)
        macs = sorted(set(item["targets"]))
        for start in range(0, len(macs), MAX_TARGETS):
            for relay in item["relays"]:
                fleet.queue(relay, "wake-relay", {"broadcast": broadcast, "macs": macs[start:start + MAX_TARGETS]}, user=user)
    conn.commit()
    return result


def arrived(conn, dev_id):
    """Le poste a repris contact : son réveil est soldé."""
    from . import fleet
    conn.execute("UPDATE device_commands SET state='success', finished_at=?, output=? WHERE device_id=? AND action='wake' AND state='running'",
                 (fleet._now(), "Poste revenu en ligne.", dev_id))


def expire(conn, dev_id=None):
    """Réveils sans réponse, et relais pas transmis à temps : un poste revenu plus tard ne réveille plus personne à l'improviste."""
    from . import fleet
    now = datetime.datetime.now()
    only = " AND device_id=?" if dev_id else ""
    extra = [dev_id] if dev_id else []
    conn.execute("UPDATE device_commands SET state='failed', finished_at=?, output=? WHERE action='wake' AND state='running' AND sent_at<?" + only,
                 (fleet._now(), f"Pas revenu après {BACK_MINUTES} min : vérifiez que le réveil par le réseau est activé dans le BIOS et que le poste est alimenté.",
                  (now - datetime.timedelta(minutes=BACK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S"), *extra))
    conn.execute("UPDATE device_commands SET state='cancelled', finished_at=?, output=? WHERE action='wake-relay' AND state='queued' AND created_at<?" + only,
                 (fleet._now(), "Relais trop tardif : réveil abandonné.", (now - datetime.timedelta(minutes=RELAY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S"), *extra))
