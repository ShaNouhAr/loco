"""Assistants IA (MCP) : un client MCP (Claude Code, Cursor, VS Code…) consulte et pilote loco par des outils.

Chaque accès est un jeton (haché en base, montré une seule fois) rattaché au compte qui l'a créé. Il n'ouvre que les
outils cochés un à un, et jamais plus que les droits actuels de ce compte : droits effectifs = besoins des outils
accordés ∩ droits de son profil, recalculés à chaque requête. Les actions passent par les mêmes fonctions que
l'interface (mêmes contrôles, même journal ; l'agent du poste revérifie les droits du compte avant d'exécuter).

Transport « Streamable HTTP » sans état : POST /mcp, réponses JSON (ni flux SSE, ni session). Deux générations du
protocole sur la même adresse :
- 2025-03-26 à 2025-11-25 : poignée de main `initialize`, puis en-tête MCP-Protocol-Version ;
- 2026-07-28 : version dans le `_meta` de chaque requête, `server/discover`, en-têtes Mcp-Method et Mcp-Name,
  `resultType` dans chaque résultat."""
import base64
import binascii
import datetime
import hashlib
import json
import re
import secrets

from flask import abort, current_app, flash, g, render_template, request
from werkzeug.exceptions import HTTPException

from . import app, config, features, fleet, groups, journal, permissions, scripts
from .auth import audit, can, can_manage_user, login_required, require
from .db import db
from .i18n import L_, _
from .util import back

LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
MODERN = ("2026-07-28",)
STRUCTURED = ("2025-11-25", "2025-06-18")               # structuredContent : absent de 2025-03-26
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO = {"name": "loco", "title": "loco", "version": config.VERSION}
PREFIX = "loco_mcp_"
TOKEN_RE = re.compile(r"^loco_mcp_[A-Za-z0-9_-]{43}$")
MAX_BODY = 1024 * 1024
LIST_TTL_MS = 60_000
TOUCH = datetime.timedelta(minutes=1)
EXPIRIES = {"30": 30, "90": 90, "365": 365, "0": 0}     # jours ; 0 = sans expiration
NAME_MAX = 80
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

INSTRUCTIONS = (
    "loco serves local package caches and repositories, and manages a fleet of Windows and Linux computers through "
    "its agent. Device actions (inventory, updates, reboots, software installs, scripts) are queued: the device agent "
    "runs them at its next contact, usually within 2 minutes. Follow them with get_device or get_script_run instead "
    "of assuming the result. Wherever a device is expected, pass its numeric id or its exact hostname. Only the tools "
    "granted to this access are available, within the rights of the loco account that created it.")


class ToolError(Exception):
    """Refus métier : renvoyé à l'assistant dans le résultat de l'outil (isError), pas en erreur de protocole."""


class ProtocolError(Exception):
    def __init__(self, code, message, status=200, data=None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data


# --------------------------------------------------------------------------- catalogue des outils

GROUPS = (("fleet", L_("Parc")), ("software", L_("Logiciels Windows")), ("scripts", L_("Scripts")),
          ("server", L_("Serveur et journal")))
TOOLS = {}


def tool(name, group, title, label, hint, needs, description, properties=None, required=(), read_only=True,
         destructive=False, idempotent=True):
    """Déclare un outil : nom et description pour l'assistant (anglais), libellé et aide pour l'interface."""
    def register(handler):
        TOOLS[name] = {"name": name, "group": group, "title": title, "label": label, "hint": hint, "needs": tuple(needs),
                       "description": description, "read_only": read_only, "destructive": destructive,
                       "idempotent": idempotent, "handler": handler,
                       "schema": {"type": "object", "properties": properties or {},
                                  **({"required": list(required)} if required else {}), "additionalProperties": False}}
        return handler
    return register


def available(t, granted):
    """L'outil ne demande que des droits accordés par ce profil, dans des fonctionnalités actives."""
    return all(p in granted and features.allows(p) for p in t["needs"])


def definition(t):
    return {"name": t["name"], "title": t["title"], "description": t["description"], "inputSchema": t["schema"],
            "annotations": {"title": t["title"], "readOnlyHint": t["read_only"], "destructiveHint": t["destructive"],
                            "idempotentHint": t["idempotent"], "openWorldHint": False}}


DEVICE = {"type": "string", "maxLength": 255, "description": "Device id (number) or exact hostname."}
DEVICES = {"type": "array", "maxItems": 500, "items": {"type": "string", "maxLength": 255},
           "description": "Device ids or exact hostnames."}
DEVICE_GROUPS = {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 255},
                 "description": "Device group ids or exact names: all their current members are targeted."}
STATUS = {"green": "up_to_date", "orange": "updates_pending", "red": "needs_attention", "grey": "unknown"}
VERBATIM = {"content"}                                     # contenu d'un script : transmis tel quel, sans rognage


def _coerce(key, spec, value):
    kind = spec.get("type")
    if kind == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(int(value)) if float(value).is_integer() else str(value)
        if not isinstance(value, str):
            raise ToolError(f"Argument {key} must be a string.")
        if key not in VERBATIM:
            value = value.strip()
        if len(value) > spec.get("maxLength", 10_000) or len(value) < spec.get("minLength", 0):
            raise ToolError(f"Argument {key} must be {spec.get('minLength', 0)} to {spec.get('maxLength')} characters long.")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"Argument {key} must be one of: {', '.join(spec['enum'])}.")
        return value
    if kind == "integer":
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolError(f"Argument {key} must be an integer.")
        if value < spec.get("minimum", value) or value > spec.get("maximum", value):
            raise ToolError(f"Argument {key} must be between {spec.get('minimum')} and {spec.get('maximum')}.")
        return value
    if kind == "boolean":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise ToolError(f"Argument {key} must be true or false.")
        return value
    if kind == "array":
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            value = [value]                                       # un seul élément passé sans liste
        if not isinstance(value, list) or len(value) > spec.get("maxItems", 500):
            raise ToolError(f"Argument {key} must be a list of at most {spec.get('maxItems', 500)} items.")
        return [_coerce(key, spec["items"], item) for item in value]
    raise ToolError(f"Unsupported argument {key}.")


def arguments(t, values):
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ToolError("Arguments must be a JSON object.")
    properties = t["schema"]["properties"]
    unknown = sorted(set(values) - set(properties))
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown[:10])}.")
    clean = {key: _coerce(key, properties[key], value) for key, value in values.items() if value is not None}
    missing = [key for key in t["schema"].get("required", ()) if clean.get(key) in (None, "", [])]
    if missing:
        raise ToolError(f"Missing argument(s): {', '.join(missing)}.")
    return clean


# --------------------------------------------------------------------------- aides communes aux outils

def _via():
    return f" · MCP « {g.mcp['token']['name']} »"


def _device(value):
    conn = db()
    row = None
    if str(value).isdigit():
        row = conn.execute("SELECT * FROM devices WHERE id=? AND state!='removed'", (int(value),)).fetchone()
    if row is None:
        rows = conn.execute("SELECT * FROM devices WHERE state!='removed' AND hostname=? COLLATE NOCASE",
                            (str(value),)).fetchall()
        if len(rows) > 1:
            raise ToolError(f"Several devices are named {value!r} (ids {', '.join(str(r['id']) for r in rows)}): "
                            "pass the id instead.")
        row = rows[0] if rows else None
    if row is None:
        raise ToolError(f"No device {value!r}: use list_devices to find its id or hostname.")
    return row


def _active(dev):
    if dev["state"] != "active":
        raise ToolError(f"The agent of {dev['hostname']} is being removed: no new action is possible.")


def _group(value):
    conn = db()
    row = None
    if str(value).isdigit():
        row = conn.execute("SELECT * FROM device_groups WHERE id=?", (int(value),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM device_groups WHERE name=? COLLATE NOCASE", (str(value),)).fetchone()
    if row is None:
        raise ToolError(f"No device group {value!r}: use list_device_groups.")
    return row


def _targets(args):
    """Postes visés : listés et membres actuels des groupes, sans doublon, triés par nom."""
    found = {}
    for value in args.get("devices", []):
        dev = _device(value)
        found[dev["id"]] = dev
    for value in args.get("groups", []):
        group = _group(value)
        for dev in db().execute("SELECT d.* FROM devices d JOIN device_group_members m ON m.device_id=d.id "
                                "WHERE m.group_id=? AND d.state!='removed'", (group["id"],)):
            found[dev["id"]] = dev
    if not found:
        raise ToolError("Give at least one device or one device group.")
    return sorted(found.values(), key=lambda d: (d["hostname"] or "").lower())


def _command(c, output=1500):
    text = c["output"] or ""
    return {"id": c["id"], "action": c["action"], "label": str(fleet.action_label(c)), "state": c["state"],
            "created_at": c["created_at"], "created_by": c["created_by"], "finished_at": c["finished_at"] or None,
            "exit_code": c["exit_code"], "output": ("…" + text[-output:]) if len(text) > output else text}


def _queued(dev, action, added, detail=""):
    if not added:
        return {"device_id": dev["id"], "hostname": dev["hostname"], "queued": False,
                "message": "The same action is already waiting for this device."}
    db().commit()
    audit(f"parc.{action}", f"{dev['hostname']}{detail}{_via()}")
    return {"device_id": dev["id"], "hostname": dev["hostname"], "queued": True,
            "message": f"Queued: the agent runs it at its next contact (within about {fleet.CHECKIN // 60} min)."}


# --------------------------------------------------------------------------- outils : parc

@tool("list_devices", "fleet", "List devices", L_("Lister les postes"),
      L_("Postes équipés de l'agent : état, mises à jour en attente, présence et groupes."), ("fleet.view",),
      "List the managed devices (computers running the loco agent) with their status, pending system, security and "
      "software updates, reboot need, online presence and groups. Filter by status, operating system, group, online "
      "state or text.",
      {"status": {"type": "string", "enum": list(STATUS.values()),
                  "description": "up_to_date, updates_pending, needs_attention (many updates, failure, missing "
                                 "inventory…) or unknown (no recent contact, inventory in progress)."},
       "os": {"type": "string", "enum": ["windows", "linux"]},
       "group": {"type": "string", "maxLength": 255, "description": "Device group id or exact name."},
       "search": {"type": "string", "maxLength": 100,
                  "description": "Text found in the hostname, operating system or address (case-insensitive)."},
       "online": {"type": "boolean", "description": "true: only devices whose agent answered recently; false: only the others."},
       "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of devices (default 200)."}})
def _list_devices(args):
    rows = fleet._rows()
    if "group" in args:
        group = _group(args["group"])
        members = {r[0] for r in db().execute("SELECT device_id FROM device_group_members WHERE group_id=?", (group["id"],))}
        rows = [r for r in rows if r["d"]["id"] in members]
    rows = fleet._enrich(rows)
    if "status" in args:
        rows = [r for r in rows if STATUS[r["color"]] == args["status"]]
    if "os" in args:
        rows = [r for r in rows if (r["d"]["os_family"] == "windows") == (args["os"] == "windows")]
    if "online" in args:
        rows = [r for r in rows if r["presence"]["online"] == args["online"]]
    if args.get("search"):
        needle = args["search"].casefold()
        rows = [r for r in rows if needle in " ".join((r["d"]["hostname"], r["d"]["os_name"], r["d"]["addresses"],
                                                       r["d"]["ip"])).casefold()]
    limit = args.get("limit", 200)
    return {"total": len(rows), "devices": [{
        "id": r["d"]["id"], "hostname": r["d"]["hostname"], "os": r["d"]["os_name"], "family": r["d"]["os_family"],
        "agent_version": r["d"]["agent_version"], "address": r["address"], "online": r["presence"]["online"],
        "last_seen": r["d"]["last_seen"] or None, "status": STATUS[r["color"]], "status_detail": r["reason"],
        "system_updates": r["d"]["updates"], "security_updates": r["d"]["security_updates"],
        "software_updates": r["apps"], "reboot_required": bool(r["d"]["reboot_required"]),
        "pending_actions": len(r["pending"]), "groups": [x["name"] for x in r["groups"]]} for r in rows[:limit]]}


@tool("get_device", "fleet", "Get device details", L_("Consulter un poste"),
      L_("Fiche d'un poste : système, mises à jour en attente, logiciels du catalogue, supervision, actions récentes."),
      ("fleet.view",),
      "Get one device in detail: identity and addresses, status, pending updates (with security flag), catalog software "
      "versions (Windows), package counts (Linux), latest CPU / memory / disk measures, pending actions and the most "
      "recent actions with their result and output.",
      {"device": DEVICE}, ("device",))
def _get_device(args):
    from . import monitoring
    dev = _device(args["device"])
    inv = fleet.inventory(dev)
    color, reason = fleet.status(dev)
    updates = inv.get("updates") or []
    out = {"id": dev["id"], "hostname": dev["hostname"], "os": dev["os_name"], "family": dev["os_family"],
           "agent_version": dev["agent_version"], "addresses": (dev["addresses"] or "").split(),
           "contact_address": dev["ip"], "online": fleet.presence(dev)["online"], "last_seen": dev["last_seen"] or None,
           "last_inventory": dev["last_scan"] or None, "enrolled_at": dev["enrolled_at"], "status": STATUS[color],
           "status_detail": reason, "agent_state": dev["state"], "reboot_required": bool(dev["reboot_required"]),
           "inventory_error": inv.get("error") or None, "pending_updates_total": len(updates),
           "pending_updates": [{"name": u.get("name"), "version": u.get("version"), "security": bool(u.get("security"))}
                               for u in updates[:200] if isinstance(u, dict)],
           "flatpak_updates": int(inv.get("flatpak") or 0),
           "groups": [x["name"] for x in groups.memberships("device", dev["id"])],
           "pending_actions": [_command(c) for c in fleet.pending(dev["id"])],
           "recent_actions": [_command(c) for c in db().execute(
               "SELECT * FROM device_commands WHERE device_id=? AND state NOT IN ('queued', 'sent', 'running') "
               "ORDER BY id DESC LIMIT 10", (dev["id"],))]}
    if dev["os_family"] == "windows":
        out["windows_update_blocked"] = bool(dev["block_windows_update"])
        out["installed_programs"] = len(inv.get("apps") or [])
        out["catalog_software"] = [{"slug": a["slug"], "name": a["name"], "catalog_version": a["version"],
                                    "installed_version": installed or None, "update_available": outdated}
                                   for a, installed, outdated in fleet.catalog_state(inv)]
    else:
        soft = fleet.software(dev)
        packages = soft.get("packages") or []
        out["packages"] = {"total": len(packages), "manually_installed": sum(1 for p in packages if isinstance(p, dict) and p.get("manual"))}
        if soft.get("system"):
            out["system"] = soft["system"]
    now = monitoring.latest(dev)
    if now:
        out["measures"] = {"age_seconds": now["age"], "cpu_percent": now["cpu"], "memory_percent": now["mem"],
                           "swap_percent": now["swap"], "uptime_seconds": now["raw"].get("uptime"),
                           "disks": [{"mount": d.get("mount"), "used_percent": d.get("pct"), "total_bytes": d.get("total")}
                                     for d in now["disks"]]}
    return out


@tool("find_installed_software", "fleet", "Find installed software", L_("Rechercher un logiciel installé"),
      L_("Postes où un programme Windows ou un paquet Linux est installé, avec sa version."), ("fleet.view",),
      "Search the software inventory of every device: Windows programs and Linux packages whose name contains the "
      "given text. Returns, for each device, the matching names and installed versions.",
      {"name": {"type": "string", "minLength": 2, "maxLength": 100, "description": "Text found in the program or package name."},
       "os": {"type": "string", "enum": ["windows", "linux"]},
       "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of devices (default 100)."}},
      ("name",))
def _find_software(args):
    needle, out = args["name"].casefold(), []
    for dev in db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE"):
        windows = dev["os_family"] == "windows"
        if "os" in args and windows != (args["os"] == "windows"):
            continue
        items = (fleet.inventory(dev).get("apps") if windows else fleet.software(dev).get("packages")) or []
        found = [{"name": i.get("name"), "version": i.get("version")} for i in items
                 if isinstance(i, dict) and needle in str(i.get("name") or "").casefold()]
        if found:
            out.append({"device_id": dev["id"], "hostname": dev["hostname"], "matches": found[:50]})
    return {"total_devices": len(out), "devices": out[:args.get("limit", 100)]}


@tool("list_device_groups", "fleet", "List device groups", L_("Lister les groupes de machines"),
      L_("Groupes de machines et leurs membres."), ("fleet.view",),
      "List the device groups with their member devices (id and hostname).")
def _list_groups(args):
    out = []
    for row in groups.listing("device"):
        members = db().execute("SELECT d.id, d.hostname FROM device_group_members m JOIN devices d ON d.id=m.device_id "
                               "WHERE m.group_id=? AND d.state!='removed' ORDER BY d.hostname COLLATE NOCASE LIMIT 500",
                               (row["id"],)).fetchall()
        out.append({"id": row["id"], "name": row["name"], "devices": [{"id": m["id"], "hostname": m["hostname"]} for m in members]})
    return {"groups": out}


@tool("scan_device", "fleet", "Refresh device inventory", L_("Demander un inventaire"),
      L_("Actualiser les mises à jour et logiciels remontés par un poste."), ("fleet.scan",),
      "Ask a device agent to refresh its inventory (pending updates, installed software) at its next contact.",
      {"device": DEVICE}, ("device",), read_only=False)
def _scan(args):
    dev = _device(args["device"])
    _active(dev)
    return _queued(dev, "scan", fleet.queue(dev, "scan"))


@tool("update_device", "fleet", "Update a device", L_("Mettre à jour un poste"),
      L_("Système, Flatpak et logiciels du catalogue déjà installés, sur un poste."), ("fleet.update",),
      "Install the pending updates of one device: operating system, Flatpak and, on Windows, the catalog software "
      "already installed. reboot=true also restarts it afterwards (only if this access may reboot devices).",
      {"device": DEVICE, "reboot": {"type": "boolean", "description": "Restart the device after updating (default false)."}},
      ("device",), read_only=False)
def _update(args):
    dev = _device(args["device"])
    _active(dev)
    reboot = args.get("reboot", False)
    if reboot and not can("fleet.reboot"):
        raise ToolError("This access may not reboot devices: call again without reboot.")
    result = _queued(dev, "update", fleet.queue(dev, "update", {"reboot": reboot}), " (avec redémarrage)" if reboot else "")
    result["had_pending_updates"] = fleet.needs_update(dev)
    return result


@tool("update_devices", "fleet", "Update several devices", L_("Mettre à jour plusieurs postes"),
      L_("Mêmes mises à jour sur des postes listés ou les membres de groupes."), ("fleet.update",),
      "Install the pending updates on several devices at once, given by id / hostname and / or device groups. By "
      "default only devices that have pending updates are queued.",
      {"devices": DEVICES, "groups": DEVICE_GROUPS,
       "reboot": {"type": "boolean", "description": "Restart each device after updating (default false)."},
       "only_if_needed": {"type": "boolean", "description": "Skip devices with nothing to update (default true)."}},
      read_only=False)
def _update_many(args):
    reboot = args.get("reboot", False)
    if reboot and not can("fleet.reboot"):
        raise ToolError("This access may not reboot devices: call again without reboot.")
    queued, skipped = [], []
    for dev in _targets(args):
        if dev["state"] != "active":
            skipped.append({"hostname": dev["hostname"], "reason": "agent being removed"})
        elif args.get("only_if_needed", True) and not fleet.needs_update(dev):
            skipped.append({"hostname": dev["hostname"], "reason": "nothing to update"})
        elif fleet.queue(dev, "update", {"reboot": reboot}):
            queued.append(dev["hostname"])
        else:
            skipped.append({"hostname": dev["hostname"], "reason": "an identical update is already waiting"})
    db().commit()
    if queued:
        audit("parc.mise-a-jour-selection", f"{len(queued)} poste(s){', avec redémarrage' if reboot else ''}{_via()}")
    return {"queued": queued, "skipped": skipped}


@tool("reboot_device", "fleet", "Reboot a device", L_("Redémarrer un poste"),
      L_("Redémarrage d'un poste au prochain contact de son agent."), ("fleet.reboot",),
      "Restart a device at the next contact of its agent. Users of that computer lose unsaved work.",
      {"device": DEVICE}, ("device",), read_only=False, destructive=True)
def _reboot(args):
    dev = _device(args["device"])
    _active(dev)
    return _queued(dev, "reboot", fleet.queue(dev, "reboot"))


@tool("cancel_device_action", "fleet", "Cancel a queued action", L_("Annuler une action en attente"),
      L_("Annuler une action pas encore transmise à l'agent (seulement celles que cet accès peut lancer)."),
      ("fleet.view",),
      "Cancel an action that is still queued (not yet sent to the agent). The action id comes from get_device "
      "(pending_actions). Only kinds of actions this access may start can be cancelled.",
      {"device": DEVICE, "action_id": {"type": "integer", "minimum": 1}}, ("device", "action_id"), read_only=False)
def _cancel(args):
    dev = _device(args["device"])
    cmd = db().execute("SELECT * FROM device_commands WHERE id=? AND device_id=?", (args["action_id"], dev["id"])).fetchone()
    if cmd is None:
        raise ToolError(f"No action {args['action_id']} on {dev['hostname']}.")
    if cmd["state"] != "queued":
        raise ToolError(f"Action {cmd['id']} is {cmd['state']}: already sent to the agent or finished, it can no longer be cancelled.")
    if not can(permissions.ACTION_PERMISSIONS.get(cmd["action"], "fleet.view")):
        raise ToolError("This access may not start this kind of action, so it may not cancel it either.")
    db().execute("UPDATE device_commands SET state='cancelled', finished_at=?, output=? WHERE id=?",
                 (fleet._now(), "Annulée avant transmission à l'agent.", cmd["id"]))
    if cmd["action"] == "uninstall":
        db().execute("UPDATE devices SET state='active' WHERE id=? AND state='removing'", (dev["id"],))
    db().commit()
    audit(f"parc.{cmd['action']}.annulation", f"{dev['hostname']}{_via()}")
    return {"device_id": dev["id"], "hostname": dev["hostname"], "action_id": cmd["id"], "cancelled": True}


# --------------------------------------------------------------------------- outils : logiciels Windows

def _catalog_app(value):
    row = db().execute("SELECT * FROM win_apps WHERE slug=? OR name=? COLLATE NOCASE ORDER BY slug=? DESC LIMIT 1",
                       (value, value, value)).fetchone()
    if row is None:
        raise ToolError(f"No software {value!r} in the local catalog: use list_software_catalog.")
    return row


@tool("list_software_catalog", "software", "List the software catalog", L_("Consulter le catalogue de logiciels"),
      L_("Logiciels Windows du catalogue local, leur version et leur présence sur le parc."), ("windows.view",),
      "List the local Windows software catalog (installers served by loco): slug, name, version, state and, when this "
      "access may see the fleet, on how many devices each one is installed or outdated.")
def _catalog(args):
    apps = db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE").fetchall()
    ready = [a for a in apps if a["status"] == "ready"]
    usage = fleet.fleet_usage(ready) if can("fleet.view") else {}
    return {"software": [{"slug": a["slug"], "name": a["name"], "version": a["version"], "installer": a["kind"],
                          "state": a["status"], "winget_id": a["winget_id"] or None,
                          **({"installed_on": usage[a["slug"]][0], "outdated_on": usage[a["slug"]][1]} if a["slug"] in usage else {})}
                         for a in apps]}


@tool("install_software", "software", "Install catalog software", L_("Installer un logiciel du catalogue"),
      L_("Installer ou mettre à jour un logiciel du catalogue sur un poste Windows."), ("fleet.install",),
      "Install (or update to the catalog version) one software of the local catalog on a Windows device. The software "
      "is given by its slug or exact name from list_software_catalog.",
      {"device": DEVICE, "software": {"type": "string", "maxLength": 200, "description": "Catalog slug or exact name."}},
      ("device", "software"), read_only=False)
def _install(args):
    dev = _device(args["device"])
    _active(dev)
    if dev["os_family"] != "windows":
        raise ToolError("Catalog software installs only on Windows devices.")
    app_row = _catalog_app(args["software"])
    if app_row["status"] != "ready":
        raise ToolError(f"{app_row['name']} is not ready in the catalog (state: {app_row['status']}).")
    return _queued(dev, "install-app", fleet.queue(dev, "install-app", {"slug": app_row["slug"]}), f" : {app_row['slug']}")


# --------------------------------------------------------------------------- outils : scripts

def _script(value):
    conn = db()
    row = conn.execute("SELECT * FROM scripts WHERE id=?", (int(value),)).fetchone() if str(value).isdigit() else None
    if row is None:
        row = conn.execute("SELECT * FROM scripts WHERE name=? COLLATE NOCASE", (str(value),)).fetchone()
    if row is None:
        raise ToolError(f"No script {value!r} in the library: use list_scripts.")
    return row


@tool("list_scripts", "scripts", "List library scripts", L_("Consulter la bibliothèque de scripts"),
      L_("Scripts PowerShell, shell et Python de la bibliothèque."), ("scripts.view",),
      "List the scripts of the loco library (id, name, description, platform, version). Use get_script for the content.",
      {"search": {"type": "string", "maxLength": 100, "description": "Text found in the name or description."}})
def _list_scripts(args):
    rows = db().execute("SELECT id, name, description, platform, version, updated_at, updated_by FROM scripts "
                        "ORDER BY name COLLATE NOCASE").fetchall()
    needle = args.get("search", "").casefold()
    return {"scripts": [dict(r) for r in rows if needle in (r["name"] + " " + r["description"]).casefold()]}


@tool("get_script", "scripts", "Read a library script", L_("Lire un script"),
      L_("Contenu d'un script de la bibliothèque."), ("scripts.view",),
      "Read a library script, including its content. The script is given by its id or exact name.",
      {"script": {"type": "string", "maxLength": 200, "description": "Script id or exact name."}}, ("script",))
def _get_script(args):
    row = _script(args["script"])
    return {k: row[k] for k in ("id", "name", "description", "platform", "version", "content", "updated_at", "updated_by")}


def _run_view(run, targets, output):
    return {"run_id": run["id"], "name": run["name"], "version": run["version"], "platform": run["platform"],
            "created_by": run["created_by"], "created_at": run["created_at"], "summary": scripts._summary(targets),
            **({"targets": [{"device_id": t["device_id"], "hostname": t["hostname"], "state": t["state"],
                             "exit_code": t["exit_code"], "finished_at": t["finished_at"] or None,
                             "output": ("…" + (t["output"] or "")[-output:]) if len(t["output"] or "") > output else (t["output"] or "")}
                            for t in targets]} if output else {})}


@tool("list_script_runs", "scripts", "List recent script runs", L_("Consulter les exécutions de scripts"),
      L_("Dernières exécutions de scripts et leur bilan par poste."), ("scripts.view",),
      "List the most recent script runs with their per-device summary (success, failed, in progress).",
      {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Number of runs (default 20)."}})
def _list_runs(args):
    runs = db().execute("SELECT * FROM script_runs ORDER BY id DESC LIMIT ?", (args.get("limit", 20),)).fetchall()
    return {"runs": [_run_view(r, scripts._targets(r["id"]), 0) for r in runs]}


@tool("get_script_run", "scripts", "Get a script run", L_("Consulter une exécution de script"),
      L_("Résultat d'une exécution : état, code de sortie et sortie de chaque poste."), ("scripts.view",),
      "Get one script run: for each target device its state, exit code and output (end of the output when long).",
      {"run_id": {"type": "integer", "minimum": 1}}, ("run_id",))
def _get_run(args):
    run = db().execute("SELECT * FROM script_runs WHERE id=?", (args["run_id"],)).fetchone()
    if run is None:
        raise ToolError(f"No script run {args['run_id']}.")
    return _run_view(run, scripts._targets(run["id"]), 4000)


def _launch(script, args):
    """Exécution figée (contenu et empreinte) sur les postes compatibles ; même chemin que l'interface."""
    targets = _targets(args)
    compatible = [d for d in targets if scripts._compatible(d, script["platform"])]
    if not compatible:
        raise ToolError(f"No active device among the targets can run a {script['platform']} script.")
    if len(compatible) > scripts.MAX_TARGETS:
        raise ToolError(f"At most {scripts.MAX_TARGETS} devices per run.")
    conn = db()
    run_id = scripts.start_run(conn, script, compatible, g.user["username"])
    audit("scripts.execution", f"{script['name']} v{script['version']} : {len(compatible)} poste(s){_via()}", commit=False)
    conn.commit()
    chosen = {d["id"] for d in compatible}
    return {"run_id": run_id, "targets": [d["hostname"] for d in compatible],
            "skipped": [d["hostname"] for d in targets if d["id"] not in chosen],
            "message": "Sent to the agents at their next contact: follow it with get_script_run."}


@tool("run_script", "scripts", "Run a library script", L_("Exécuter un script de la bibliothèque"),
      L_("Lancer un script existant en SYSTEM (Windows) ou root (Linux) sur des postes ou des groupes."), ("scripts.run",),
      "Run an existing library script on devices and / or device groups, as SYSTEM (Windows) or root (Linux). Pass the "
      "version read with get_script to make sure the content did not change in between.",
      {"script": {"type": "string", "maxLength": 200, "description": "Script id or exact name."},
       "version": {"type": "integer", "minimum": 1, "description": "Expected script version (recommended)."},
       "devices": DEVICES, "groups": DEVICE_GROUPS}, ("script",), read_only=False, destructive=True, idempotent=False)
def _run_script(args):
    script = _script(args["script"])
    if "version" in args and args["version"] != script["version"]:
        raise ToolError(f"The script changed (now version {script['version']}): read it again with get_script.")
    return _launch(script, args)


@tool("run_command", "scripts", "Run a command written by the assistant", L_("Exécuter des commandes écrites par l'assistant"),
      L_("L'assistant écrit lui-même un script et le lance en SYSTEM ou root : le plus puissant, à réserver aux accès de confiance."),
      ("scripts.run", "scripts.edit"),
      "Run a script you write (PowerShell on Windows, shell on Linux, or Python on both) on devices and / or device "
      "groups, as SYSTEM (Windows) or root (Linux). It is recorded with its exact content in the script runs history. "
      "Prefer read-only commands unless the user explicitly asked for a change.",
      {"platform": {"type": "string", "enum": sorted(scripts.PLATFORMS),
                    "description": "windows (PowerShell), linux (sh) or python (any system)."},
       "content": {"type": "string", "minLength": 1, "maxLength": scripts.MAX_CONTENT, "description": "The script itself."},
       "title": {"type": "string", "maxLength": 80, "description": "Short title shown in the history."},
       "devices": DEVICES, "groups": DEVICE_GROUPS},
      ("platform", "content"), read_only=False, destructive=True, idempotent=False)
def _run_command(args):
    content = args["content"].replace("\r\n", "\n")
    if "\x00" in content or len(content.encode()) > scripts.MAX_CONTENT:
        raise ToolError(f"The script must be text of at most {scripts.MAX_CONTENT // 1024} KiB.")
    title = CONTROL_RE.sub(" ", args.get("title") or "commande").strip()[:80]
    script = {"id": None, "name": _("Assistant IA : %(title)s", title=title), "version": 1,
              "platform": args["platform"], "content": content}
    return _launch(script, args)


# --------------------------------------------------------------------------- outils : serveur et journal

@tool("get_server_status", "server", "Get server status", L_("Consulter l'état du serveur"),
      L_("Version, services, disque, processeur, mémoire et caches du serveur loco."), ("dashboard.view",),
      "Get the loco server status: version, services, disk, CPU and memory usage, cache sizes, Windows Update cache "
      "efficiency and, when this access may see the fleet, a device summary.")
def _server_status(args):
    from . import dashboard
    d = dashboard.dashboard_data()
    out = {"loco_version": config.VERSION, "admin_url": config.HTTPS_URL,
           "services_running": {"apt_cache": bool(d["services"]["acng"]), "web_server": bool(d["services"]["nginx"])},
           "disk_bytes": d["disk"], "cpu": d["cpu"], "memory_bytes": d["mem"],
           "cache_bytes": {"apt": d["acng_size"], "windows_update": d["wucache_size"], "flatpak": d["flatpak_cache"]},
           "windows_update_cache": d["wu"], "local_deb_packages": d["debs"], "catalog_software": d["apps"]}
    if can("fleet.view"):
        rows = fleet._rows()
        out["fleet"] = {"devices": len(rows), "online": sum(1 for r in rows if fleet.presence(r["d"])["online"]),
                        **{STATUS[c]: sum(1 for r in rows if r["color"] == c) for c in STATUS}}
    return out


@tool("search_journal", "server", "Search the audit journal", L_("Consulter le journal"),
      L_("Connexions et actions d'administration, filtrées par texte, compte ou date."), ("audit.view",),
      "Search the loco audit journal (sign-ins and administration actions), newest first.",
      {"text": {"type": "string", "maxLength": 100, "description": "Text found in the action, detail or account."},
       "username": {"type": "string", "maxLength": 100, "description": "Exact account name."},
       "since": {"type": "string", "maxLength": 10, "description": "First day, YYYY-MM-DD."},
       "until": {"type": "string", "maxLength": 10, "description": "Last day, YYYY-MM-DD."},
       "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Number of events (default 50)."}})
def _journal(args):
    where, values = [], []
    for key, op, shift in (("since", ">=", 0), ("until", "<", 1)):
        if key in args:
            try:
                day = datetime.date.fromisoformat(args[key]) + datetime.timedelta(days=shift)
            except ValueError:
                raise ToolError(f"{key} must be a date YYYY-MM-DD.")
            where.append(f"at {op} ?")
            values.append(day.isoformat())
    if args.get("username"):
        where.append("username = ?")
        values.append(args["username"])
    if args.get("text"):
        like = "%" + args["text"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(action LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\')")
        values += [like, like, like]
    rows = db().execute("SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") +
                        " ORDER BY id DESC LIMIT ?", (*values, args.get("limit", 50))).fetchall()
    return {"events": [{"at": r["at"], "username": r["username"], "ip": r["ip"], "action": r["action"],
                        "label": str(journal.describe(r["action"])["label"]), "detail": r["detail"]} for r in rows]}


# --------------------------------------------------------------------------- jetons

def _now():
    return fleet._now()


def _parse_tools(raw):
    try:
        names = json.loads(raw or "[]")
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str) and n in TOOLS] if isinstance(names, list) else []


def authenticate():
    """(jeton, compte) si l'en-tête Authorization porte un jeton valide, non expiré, d'un compte utilisable."""
    scheme, _sep, value = request.headers.get("Authorization", "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not TOKEN_RE.match(value):
        return None
    conn = db()
    token = conn.execute("SELECT * FROM mcp_tokens WHERE token_hash=?", (hashlib.sha256(value.encode()).hexdigest(),)).fetchone()
    if token is None or (token["expires_at"] and token["expires_at"] <= _now()):
        return None
    user = conn.execute("SELECT * FROM users WHERE id=?", (token["user_id"],)).fetchone()
    # Mêmes conditions qu'une session : compte actif, finalisé, et pour un compte d'annuaire, annuaire actif.
    if user is None or user["disabled"] or user["pending"] or user["must_change"] or user["auth_source"] == "sso":
        return None
    if user["auth_source"] == "directory" and ("directories" in features.disabled() or conn.execute(
            "SELECT 1 FROM directory_identities i JOIN auth_directories d ON d.id=i.directory_id "
            "WHERE i.user_id=? AND d.enabled=1", (user["id"],)).fetchone() is None):
        return None
    return token, user


def grants(token, user):
    """(outils utilisables, droits effectifs) : accordés au jeton ET permis par le profil actuel du compte."""
    own = permissions.role_permissions(db(), user["role"])
    usable = [TOOLS[n] for n in _parse_tools(token["tools"]) if available(TOOLS[n], own)]
    return usable, frozenset(p for t in usable for p in t["needs"])


def _touch(client=None):
    """Dernière utilisation (au plus une écriture par minute, sauf changement d'adresse ou de client)."""
    token = g.mcp["token"]
    label = ""
    if isinstance(client, dict) and isinstance(client.get("name"), str):
        label = CONTROL_RE.sub(" ", f"{client['name'][:60]} {str(client.get('version') or '')[:20]}").strip()
    ip = request.remote_addr or ""
    age = fleet._ago(token["last_used_at"])
    if (age is not None and datetime.timedelta(0) <= age < TOUCH and token["last_ip"] == ip
            and label in ("", token["last_client"])):
        return
    db().execute("UPDATE mcp_tokens SET last_used_at=?, last_ip=?, last_client=CASE WHEN ?='' THEN last_client ELSE ? END "
                 "WHERE id=?", (_now(), ip, label, label, token["id"]))
    db().commit()


# --------------------------------------------------------------------------- protocole

def _respond(payload, status=200, headers=None):
    body = "" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    response = current_app.response_class(body, status=status, mimetype="application/json")
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _error(msg_id, code, message, status=200, data=None, headers=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _respond({"jsonrpc": "2.0", "id": msg_id, "error": error}, status, headers)


def _origin_ok():
    """Protection contre le « DNS rebinding » : une page web d'une autre origine ne parle pas au serveur MCP."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    own = {request.host_url.rstrip("/").lower(), config.HTTPS_URL.rstrip("/").lower()}
    return origin.rstrip("/").lower() in own


def _header(name):
    """En-tête MCP, décodé s'il a été transmis en « =?base64?…?= » (valeur non sûre pour un en-tête HTTP)."""
    value = request.headers.get(name)
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def _call(params, structured):
    name = params.get("name")
    if not isinstance(name, str) or name not in TOOLS:
        raise ProtocolError(-32602, f"Unknown tool: {str(name)[:100]}")
    t = TOOLS[name]

    def failure(message):
        return {"content": [{"type": "text", "text": message}], "isError": True}
    if t not in g.mcp["tools"]:
        return failure(f"The tool {name} is not allowed for this access (or no longer allowed by the rights of its "
                       "loco account). In loco: Administration > AI assistants (MCP).")
    try:
        data = t["handler"](arguments(t, params.get("arguments")))
    except ToolError as e:
        db().rollback()
        return failure(str(e))
    except HTTPException as e:                              # abort() d'une fonction partagée avec l'interface
        db().rollback()
        return failure(str(e.description or e.name))
    except Exception:
        db().rollback()
        current_app.logger.exception("mcp: échec de l'outil %s", name)
        return failure("Internal error in loco while running this tool (see the server log).")
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1, default=str)}], "isError": False}
    if structured:
        result["structuredContent"] = data
    return result


def _modern(msg_id, method, params, meta, notification):
    version = meta.get(VERSION_KEY)
    if request.headers.get("MCP-Protocol-Version") != version:
        return _error(msg_id, -32020, "Header mismatch: MCP-Protocol-Version does not match the protocol version of _meta.", 400)
    if version not in MODERN:
        return _error(msg_id, -32022, "Unsupported protocol version", 400,
                      {"supported": list(MODERN + LEGACY), "requested": str(version)[:40]})
    if notification:
        return _respond(None, 202)
    if _header("Mcp-Method") != method:
        return _error(msg_id, -32020, "Header mismatch: Mcp-Method does not match the method of the body.", 400)
    if method == "tools/call" and _header("Mcp-Name") != params.get("name"):
        return _error(msg_id, -32020, "Header mismatch: Mcp-Name does not match the tool name of the body.", 400)
    _touch(meta.get(CLIENT_KEY))
    extra = {"resultType": "complete", "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO}}
    if method == "server/discover":
        result = {"supportedVersions": list(MODERN), "capabilities": {"tools": {"listChanged": False}},
                  "instructions": INSTRUCTIONS, "ttlMs": LIST_TTL_MS, "cacheScope": "private"}
    elif method == "tools/list":
        result = {"tools": [definition(t) for t in g.mcp["tools"]], "ttlMs": LIST_TTL_MS, "cacheScope": "private"}
    elif method == "tools/call":
        result = _call(params, True)
    else:
        return _error(msg_id, -32601, f"Method not found: {method[:100]}", 404)
    return _respond({"jsonrpc": "2.0", "id": msg_id, "result": {**result, **extra}})


def _legacy(msg_id, method, params, notification):
    if method == "initialize":
        requested = params.get("protocolVersion")
        _touch(params.get("clientInfo"))
        return _respond({"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": requested if requested in LEGACY else LEGACY[0],
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}})
    version = request.headers.get("MCP-Protocol-Version") or "2025-03-26"     # en-tête absent : valeur du protocole
    if version not in LEGACY:
        return _error(msg_id, -32600, f"Unsupported MCP-Protocol-Version: {version[:40]}. Supported: "
                      + ", ".join(MODERN + LEGACY), 400)
    if notification:
        return _respond(None, 202)
    _touch()
    if method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [definition(t) for t in g.mcp["tools"]]}
    elif method == "tools/call":
        result = _call(params, version in STRUCTURED)
    else:
        return _error(msg_id, -32601, f"Method not found: {method[:100]}")
    return _respond({"jsonrpc": "2.0", "id": msg_id, "result": result})


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    if not _origin_ok():
        return _error(None, -32000, "Origin not allowed.", 403)
    if not features.enabled("mcp"):
        return _error(None, -32000, "The MCP server is disabled on this loco (Administration > Features).", 403)
    if request.method != "POST":
        return _respond({"error": "Use POST: this server offers neither an SSE stream nor sessions."}, 405, {"Allow": "POST"})
    found = authenticate()
    if found is None:
        return _error(None, -32001, "Missing, invalid, expired or revoked access token.", 401,
                      headers={"WWW-Authenticate": 'Bearer realm="loco", error="invalid_token"'})
    token, user = found
    if (request.content_length or 0) > MAX_BODY:
        return _error(None, -32600, "Request too large.", 413)
    body = request.get_data(cache=False)
    if len(body) > MAX_BODY:
        return _error(None, -32600, "Request too large.", 413)
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _error(None, -32700, "Parse error: the body must be a JSON-RPC message.", 400)
    if isinstance(message, list):
        return _error(None, -32600, "JSON-RPC batches are not supported: send one message per request.", 400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC message.", 400)
    if "method" not in message:                             # réponse du client : rien n'en attend une ici
        return _respond(None, 202)
    method, params, notification = message.get("method"), message.get("params", {}), "id" not in message
    msg_id = message.get("id")
    if (not isinstance(method, str) or not isinstance(params, dict)
            or not notification and (isinstance(msg_id, bool) or not isinstance(msg_id, (str, int)))):
        return _error(None if notification else msg_id, -32600, "Invalid JSON-RPC request.", 400)
    usable, effective = grants(token, user)
    # Le compte du jeton devient l'utilisateur de la requête, limité aux droits des outils accordés.
    g.user, g.permissions = user, effective
    g.mcp = {"token": token, "tools": usable}
    meta = params.get("_meta")
    try:
        if isinstance(meta, dict) and VERSION_KEY in meta:
            return _modern(msg_id, method, params, meta, notification)
        return _legacy(msg_id, method, params, notification)
    except ProtocolError as e:
        return _error(msg_id, e.code, e.message, e.status, e.data)


# --------------------------------------------------------------------------- page d'administration

def _endpoint_url():
    return config.HTTPS_URL.rstrip("/") + "/mcp"


def _catalog_view(own):
    """Outils par groupe pour les formulaires, avec ce qui manque au compte pour les autres."""
    out = []
    for key, label in GROUPS:
        items = []
        for t in TOOLS.values():
            if t["group"] != key:
                continue
            missing = [str(permissions.PERMISSIONS[p]["label"]) for p in t["needs"] if p not in own]
            off = [str(features.FEATURES[f][0]) for p in t["needs"] for f in features.PERMISSIONS.get(p.split(".")[0], ())
                   if f in features.disabled()]
            items.append({"name": t["name"], "label": t["label"], "hint": t["hint"], "read_only": t["read_only"],
                          "destructive": t["destructive"], "available": not missing and not off,
                          "missing": missing, "off": sorted(set(off))})
        out.append({"key": key, "label": label, "tools": items})
    return out


def _visible():
    rows = db().execute("SELECT t.*, u.username, u.role, u.disabled AS owner_disabled FROM mcp_tokens t "
                        "JOIN users u ON u.id=t.user_id ORDER BY t.id DESC").fetchall()
    return [r for r in rows if r["user_id"] == g.user["id"] or can_manage_user(r)]


def _token_view(row):
    names = _parse_tools(row["tools"])
    now = _now()
    return {"id": row["id"], "name": row["name"], "hint": row["hint"], "owner": row["username"],
            "mine": row["user_id"] == g.user["id"], "tools": names,
            "labels": [str(TOOLS[n]["label"]) for n in names], "actions": sum(1 for n in names if not TOOLS[n]["read_only"]),
            "created": fleet.ago_text(row["created_at"]),
            "expires": row["expires_at"][:10] if row["expires_at"] else "", "expired": bool(row["expires_at"]) and row["expires_at"] <= now,
            "owner_disabled": bool(row["owner_disabled"]), "used": fleet.ago_text(row["last_used_at"]) if row["last_used_at"] else "",
            "ip": row["last_ip"], "client": row["last_client"]}


def _page(created=None, status=200):
    own = permissions.role_permissions(db(), g.user["role"])
    return render_template("mcp.html", tools=_catalog_view(own), tokens=[_token_view(r) for r in _visible()],
                           endpoint=_endpoint_url(), created=created, expiries=EXPIRIES), status


def _form_values():
    """(nom, outils) du formulaire ; ValueError avec un message pour une saisie à corriger."""
    chosen = list(dict.fromkeys(request.form.getlist("tools")))
    own = permissions.role_permissions(db(), g.user["role"])
    if any(n not in TOOLS or not available(TOOLS[n], own) for n in chosen):
        abort(400)                                          # cases désactivées dans le formulaire : requête forgée
    name = CONTROL_RE.sub(" ", request.form.get("name", "")).strip()
    if not name or len(name) > NAME_MAX:
        raise ValueError(_("Donnez un nom à l’accès (%(n)s caractères au plus).", n=NAME_MAX))
    if not chosen:
        raise ValueError(_("Cochez au moins une autorisation."))
    return name, [n for n in TOOLS if n in chosen]


@app.route("/accounts/mcp")
@login_required
def mcp_settings():
    return _page()


@app.route("/accounts/mcp/add", methods=["POST"])
@login_required
def mcp_token_add():
    require("mcp.manage")
    days = EXPIRIES.get(request.form.get("expiry", "90"))
    if days is None:
        abort(400)
    try:
        name, chosen = _form_values()
    except ValueError as e:
        flash(str(e), "error")
        return back("mcp_settings")
    secret = PREFIX + secrets.token_urlsafe(32)
    expires = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else ""
    conn = db()
    conn.execute("INSERT INTO mcp_tokens (user_id, name, token_hash, hint, tools, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                 (g.user["id"], name, hashlib.sha256(secret.encode()).hexdigest(), secret[:len(PREFIX) + 4],
                  json.dumps(chosen), _now(), expires))
    audit("mcp.acces.ajout", f"{name} : {len(chosen)} autorisation(s)" + (f", expire le {expires[:10]}" if expires else ""), commit=False)
    conn.commit()
    # Affiché une seule fois, dans la réponse même (jamais dans un cookie ni une redirection).
    return _page(created={"name": name, "token": secret})


@app.route("/accounts/mcp/<int:token_id>/edit", methods=["POST"])
@login_required
def mcp_token_update(token_id):
    require("mcp.manage")
    conn = db()
    row = conn.execute("SELECT * FROM mcp_tokens WHERE id=? AND user_id=?", (token_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    try:
        name, chosen = _form_values()
    except ValueError as e:
        flash(str(e), "error")
        return back("mcp_settings")
    conn.execute("UPDATE mcp_tokens SET name=?, tools=? WHERE id=?", (name, json.dumps(chosen), token_id))
    audit("mcp.acces.modification", f"{name} : {len(chosen)} autorisation(s)", commit=False)
    conn.commit()
    flash(_("Autorisations de « %(name)s » enregistrées : elles s’appliquent dès le prochain appel de l’assistant.", name=name), "ok")
    return back("mcp_settings")


@app.route("/accounts/mcp/<int:token_id>/revoke", methods=["POST"])
@login_required
def mcp_token_revoke(token_id):
    require("mcp.manage")
    row = next((r for r in _visible() if r["id"] == token_id), None)
    if row is None:
        abort(404)
    conn = db()
    conn.execute("DELETE FROM mcp_tokens WHERE id=?", (token_id,))
    audit("mcp.acces.revocation", f"{row['name']} ({row['username']})", commit=False)
    conn.commit()
    flash(_("Accès « %(name)s » révoqué : l’assistant qui l’utilisait est refusé dès maintenant.", name=row["name"]), "ok")
    return back("mcp_settings")
