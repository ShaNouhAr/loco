"""Pilotes NVIDIA des postes Windows : dernière version publiée par NVIDIA pour la carte d'un poste (Game Ready, WHQL,
DCH), comparée à celle installée, et mise à jour depuis loco — l'agent télécharge l'installeur officiel, vérifie qu'il
est signé par NVIDIA (Authenticode) et l'installe en silence. Sous Linux, le pilote est un paquet du système : il suit
les mises à jour du système.

Deux services publics de NVIDIA, ceux de sa page de téléchargement : la liste des produits (série et identifiant de
chaque carte), puis la recherche du dernier pilote d'un produit pour une version de Windows. Les réponses sont gardées
en cache (fichier d'état) et relues en arrière-plan : une page ne fait jamais attendre un appel à NVIDIA. NVIDIA ne
publie pas d'empreinte : seul un installeur servi en HTTPS par download.nvidia.com est proposé, et le poste refuse un
fichier qui ne serait pas signé par NVIDIA Corporation.
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from . import config

PRODUCTS = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3"
LOOKUP = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php?func=DriverManualLookup"
          "&psid={psid}&pfid={pfid}&osID={os}&languageCode=1033&beta=0&isWHQL=1&dltype=-1&dch=1&upCRD=0&qnf=0&sort1=0&numberOfResults=1")
CACHE = os.path.join(config.STATE, "nvidia.json")
PRODUCTS_TTL, DRIVER_TTL, RETRY = 7 * 86400, 6 * 3600, 900
URL_RE = re.compile(r"^https://(?:[a-z]{2}|international)\.download\.nvidia\.com/Windows/\d{3}\.\d{2}/[A-Za-z0-9._-]+\.exe$")
VERSION_RE = re.compile(r"^\d{3}\.\d{2}$")
SIGNER = "NVIDIA Corporation"
OS_IDS = (("Windows 11", 135), ("Windows 10", 57))     # identifiants de NVIDIA (Windows 64 bits)
_LOCK, _BUSY = threading.Lock(), set()


def normalize(name):
    """« NVIDIA GeForce RTX 4080 » et « GeForce RTX 4080 » désignent la même carte."""
    name = re.sub(r"\((?:tm|r)\)", "", (name or "").lower())
    name = re.sub(r"^nvidia\s+", "", name.strip())
    return re.sub(r"\s+", " ", name).strip()


def is_nvidia(gpu):
    return isinstance(gpu, dict) and (str(gpu.get("pci", "")).lower().startswith("10de:") or "nvidia" in str(gpu.get("name", "")).lower())


def windows_version(wmi):
    """Version NVIDIA d'après celle de Windows : 32.0.16.2124 → 621.24 (les cinq derniers chiffres)."""
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", wmi or ""):
        return ""
    digits = wmi.replace(".", "")
    return f"{int(digits[-5:-2])}.{digits[-2:]}" if len(digits) >= 5 else ""


def vkey(version):
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


def os_id(dev):
    name = (dev["os_name"] or "") if "os_name" in dev.keys() else ""
    return next((oid for label, oid in OS_IDS if label in name), None)


# --------------------------------------------------------------------------- cache et services de NVIDIA

def _load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(8 * 1024 * 1024)


def parse_products(xml):
    """{nom normalisé: [série, produit]} d'après la liste des produits de NVIDIA."""
    found = {}
    for parent, name, value in re.findall(r'<LookupValue ParentID="(\d+)"[^>]*>\s*<Name>([^<]{1,120})</Name>\s*<Value>(\d+)</Value>', xml):
        found.setdefault(normalize(name), [int(parent), int(value)])
    return found


def parse_driver(text):
    """{version, url, size, date} du dernier pilote, ou None (réponse vide ou non conforme)."""
    try:
        data = json.loads(text)
        info = data["IDS"][0]["downloadInfo"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    version = urllib.parse.unquote(str(info.get("Version", "")))
    url = urllib.parse.unquote(str(info.get("DownloadURL", "")))
    if not VERSION_RE.match(version) or not URL_RE.match(url) or f"/{version}/" not in url:
        return None
    date = urllib.parse.unquote(str(info.get("ReleaseDateTime", "")))[:40]
    try:
        date = datetime.datetime.strptime(date, "%a %b %d, %Y").strftime("%Y-%m-%d")     # « Thu Nov 12, 2026 » → 2026-11-12
    except ValueError:
        pass
    return {"version": version, "url": url, "size": urllib.parse.unquote(str(info.get("DownloadURLFileSize", "")))[:20],
            "date": date,
            "name": urllib.parse.unquote(str(info.get("Name", "")))[:80]}


def refresh(name, oid):
    """Relit (au besoin) la liste des produits, puis le dernier pilote de cette carte pour cette version de Windows."""
    data = _load()
    now = time.time()
    if now - data.get("products_at", 0) > PRODUCTS_TTL or not data.get("products"):
        try:
            data["products"] = parse_products(_get(PRODUCTS).decode("utf-8", "replace"))
            data["products_at"] = now
        except Exception:
            data["products_at"] = now - PRODUCTS_TTL + RETRY                 # NVIDIA injoignable : réessai plus tard
    product = (data.get("products") or {}).get(normalize(name))
    key = f"{normalize(name)}|{oid}"
    drivers = data.setdefault("drivers", {})
    if product:
        try:
            found = parse_driver(_get(LOOKUP.format(psid=product[0], pfid=product[1], os=oid)).decode("utf-8", "replace"))
            drivers[key] = dict(found, at=now) if found else {"at": now, "none": True}
        except Exception:
            drivers[key] = dict(drivers.get(key) or {}, at=now - DRIVER_TTL + RETRY)
    else:
        drivers[key] = {"at": now, "none": True}                         # carte inconnue de NVIDIA (ou non GeForce)
    _save(data)


def refresh_soon(name, oid):
    key = f"{normalize(name)}|{oid}"
    with _LOCK:
        if key in _BUSY:
            return
        _BUSY.add(key)

    def go():
        try:
            refresh(name, oid)
        except Exception:
            pass
        finally:
            with _LOCK:
                _BUSY.discard(key)
    threading.Thread(target=go, name="loco-nvidia", daemon=True).start()


def latest(name, oid):
    """Dernier pilote connu pour cette carte (cache), relu en arrière-plan s'il date. None tant qu'on ne sait pas."""
    entry = (_load().get("drivers") or {}).get(f"{normalize(name)}|{oid}")
    if entry is None or time.time() - entry.get("at", 0) > DRIVER_TTL:
        refresh_soon(name, oid)
    return entry if entry and not entry.get("none") and entry.get("version") else None


def offers(dev, hardware):
    """Pour chaque carte NVIDIA d'un poste Windows : pilote installé, dernier publié, et s'il faut mettre à jour."""
    if dev["os_family"] != "windows":
        return []
    oid = os_id(dev)
    out = []
    for gpu in (hardware or {}).get("gpus") or []:
        if not is_nvidia(gpu):
            continue
        installed = windows_version(gpu.get("driver", ""))
        last = latest(gpu["name"], oid) if oid else None
        out.append({"name": gpu["name"], "installed": installed, "latest": last,
                    "outdated": bool(last and installed and vkey(last["version"]) > vkey(installed))})
    return out
