"""Applications Android : dépôts F-Droid vérifiés, catalogue de loco et fiches Google Play.

Un dépôt F-Droid publie une entrée signée (entry.jar). loco vérifie sa signature JAR avec la clé du dépôt, dont
l'empreinte a été confirmée à l'ajout, refuse une entrée plus ancienne que la dernière vérifiée (rejeu), puis contrôle
l'index complet (index-v2.json) avec l'empreinte SHA-256 inscrite dans cette entrée. Chaque APK a son empreinte dans
l'index : loco la vérifie au téléchargement, l'appareil aussi avant d'installer.

Google Play n'offre aucune interface pour qu'un outil tiers installe une application : loco ouvre sa fiche sur
l'appareil et la personne touche « Installer ».
"""
import base64
import binascii
import gzip
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

from flask import abort, flash, g, has_request_context, render_template, request, send_file

from . import app, config, features
from .i18n import L_, _, locale, n_
from .auth import audit, lock_access, login_required
from .db import db, lease, transaction
from .util import back, now

ROOT = os.path.join(config.DATA, "android")      # cache régénérable : hors des sauvegardes (STATE)
INDEX_DB = os.path.join(ROOT, "index.db")
APK_DIR = os.path.join(ROOT, "apk")
ICON_DIR = os.path.join(ROOT, "icons")
MAX_ENTRY = 1024 * 1024
MAX_INDEX = 400 * 1024 * 1024
MAX_ICON = 1024 * 1024
MAX_APK = 2 * 1024 * 1024 * 1024
SYNC_SECONDS = 86400                 # index relu chaque jour
RETRY_SECONDS = 3600                 # après un échec
APK_KEEP_SECONDS = 86400             # APK sans installation en attente : gardé un jour
AGENT_VERSION = (2, 139)             # applications loco capables d'installer
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FILE_RE = re.compile(r"^/(?!.*\.\.)[A-Za-z0-9._/+=~-]{1,300}$")
PLAY_URL_RE = re.compile(r"^https://play\.google\.com/store/apps/details\?(?:.*&)?id=([A-Za-z0-9._]+)")
SIGNED_DATA = "1.2.840.113549.1.7.2"
MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
MANIFEST_DIGESTS = {"SHA-256": "sha256", "SHA-384": "sha384", "SHA-512": "sha512"}

# Dépôts vérifiés le 2026-11-06 : empreinte de la clé calculée sur l'entrée signée, identique à celle publiée par l'éditeur.
KNOWN = {
    "fdroid": ("F-Droid", "https://f-droid.org/repo", "43238d512c1e5eb2d6569f4a3afbf5523418b82e0a3ed1552770abb9a9c9ccab",
               L_("Le dépôt principal : des milliers d'applications libres, compilées depuis leur code source par F-Droid.")),
    "izzy": ("IzzyOnDroid", "https://apt.izzysoft.de/fdroid/repo", "3bf0d6abfeae2f401707b6d966be743bf0eee49c2561b9ba39073711f628937a",
             L_("Applications libres publiées par leurs développeurs (APK officiels), analysées avant publication.")),
    "guardian": ("Guardian Project", "https://guardianproject.info/fdroid/repo", "b7c2eefd8dac7806af67dfcd92eb18126bc08312a7f2d6f3862e46013c7a6135",
                 L_("Orbot et les outils Tor du Guardian Project.")),
    "bitwarden": ("Bitwarden", "https://mobileapp.bitwarden.com/fdroid/repo", "bc54ea6fd1cd5175bcccc47c561c5726e1c3ed7e686b6db4b18bac843a3efe6c",
                  L_("Le gestionnaire de mots de passe Bitwarden, publié par son éditeur.")),
    "brave": ("Brave", "https://brave-browser-apk-release.s3.brave.com/fdroid/repo", "3c60de135aa19ec949e998469c908f7171885c1e2805f39eb403ddb0f37b4bd2",
              L_("Le navigateur Brave, publié par son éditeur.")),
    "molly": ("Molly", "https://molly.im/fdroid/foss/fdroid/repo", "5198daef37fc23c14d5ee32305b2af45787bd7df2034de33ad302bdb3446df74",
              L_("Molly, version renforcée de Signal (édition libre).")),
    "session": ("Session", "https://fdroid.getsession.org/fdroid/repo", "db0e5297eb65cc22d6bd93c869943bdcfcb6a07dc69a48a0dd8c7ba698ec04e6",
                L_("La messagerie Session, publiée par son éditeur.")),
    "threema": ("Threema Libre", "https://releases.threema.ch/fdroid/repo", "5734e753899b25775d90fe85362a49866e05ac4f83c05bef5a92880d2910639e",
                L_("La messagerie Threema, édition libre, publiée par son éditeur.")),
    "kde": ("KDE", "https://cdn.kde.org/android/stable-releases/fdroid/repo", "13784ba6c80ff4e2181e55c56f961eed5844cea16870d3b38d58780b85e1158f",
            L_("Applications KDE : NeoChat, Itinerary, Tokodon…")),
    "newpipe": ("NewPipe", "https://archive.newpipe.net/fdroid/repo", "e2402c78f9b97c6c89e97db914a2751fda1d02fe2039cc0897a462bdb57e7501",
                L_("Le lecteur NewPipe, publié par ses développeurs.")),
    "microg": ("microG", "https://microg.org/fdroid/repo", "9bd06727e62796c0130eb6dab39b73157451582cbd138e86c468acc395d14165",
               L_("Les services microG, alternative libre aux services Google.")),
}

# Sélections : les applications viennent toujours des dépôts activés ; une catégorie regroupe celles de F-Droid.
SELECTION = ("io.homeassistant.companion.android.minimal", "com.nextcloud.client", "org.jellyfin.mobile", "app.alextran.immich",
             "org.videolan.vlc", "org.mozilla.fennec_fdroid", "com.tailscale.ipn", "ch.protonvpn.android",
             "proton.android.pass.fdroid", "com.kunzisoft.keepass.libre", "com.beemdevelopment.aegis", "com.x8bit.bitwarden",
             "net.thunderbird.android", "app.organicmaps", "org.localsend.localsend_app", "org.kde.kdeconnect_tf",
             "com.duckduckgo.mobile.android", "com.brave.browser", "org.torproject.android", "im.molly.app", "org.schabi.newpipe",
             "com.termux", "org.connectbot", "com.wireguard.android")
CATEGORIES = {
    "selection": (L_("À découvrir"), ()),
    "internet": (L_("Internet"), ("Internet", "Browser", "Email", "Messaging", "Social Network", "Voice & Video Chat", "Forum")),
    "multimedia": (L_("Multimédia"), ("Multimedia", "Local Media Player", "Online Media Player", "Gallery", "Camera", "Podcast", "Radio")),
    "security": (L_("Sécurité"), ("Security", "Password & 2FA", "VPN & Proxy", "Firewall", "File Encryption & Vault")),
    "connectivity": (L_("Réseau et synchronisation"), ("Connectivity", "Cloud Storage & File Sync", "File Transfer", "Remote Access",
                                                     "Network Analyzer", "DNS & Hosts", "Remote Controller")),
    "office": (L_("Bureautique"), ("Writing", "Note", "Calendar & Agenda", "Task", "Reading", "Ebook Reader", "Text Editor")),
    "system": (L_("Système"), ("System", "File Manager", "App Store & Updater", "Launcher", "Keyboard & IME", "Battery")),
    "navigation": (L_("Cartes et déplacements"), ("Navigation", "Public Transport", "Weather")),
    "development": (L_("Développement"), ("Development", "Code & Forge")),
    "all": (L_("Tout le catalogue"), ()),
}
# Fonctions signalées par F-Droid (index-v2, « antiFeatures »).
ANTI_FEATURES = {"Ads": L_("Publicité"), "Tracking": L_("Pistage"), "NonFreeNet": L_("Service en ligne non libre"),
                 "NonFreeAdd": L_("Promeut des extensions non libres"), "NonFreeDep": L_("Dépend d'un logiciel non libre"),
                 "NonFreeAssets": L_("Contenus non libres"), "UpstreamNonFree": L_("Code d'origine non libre"),
                 "KnownVuln": L_("Faille de sécurité connue"), "NoSourceSince": L_("Code source plus publié"),
                 "TetheredNet": L_("Lié au service de l'éditeur"), "NSFW": L_("Contenu pour adultes")}
# Application qui a installé un paquet (inventaire de l'agent).
INSTALLERS = {"org.loco.agent": "loco", "com.android.vending": "Google Play", "org.fdroid.fdroid": "F-Droid",
              "org.fdroid.basic": "F-Droid", "com.aurora.store": "Aurora Store", "com.machiav3lli.fdroid": "Neo Store",
              "com.looker.droidify": "Droid-ify", "dev.imranr.obtainium": "Obtainium", "dev.imranr.obtainium.fdroid": "Obtainium",
              "com.sec.android.app.samsungapps": "Galaxy Store", "com.huawei.appmarket": "AppGallery",
              "com.google.android.packageinstaller": L_("installée à la main"), "com.android.packageinstaller": L_("installée à la main"),
              "com.android.shell": "adb"}
LOCO_PACKAGE = "org.loco.agent"


def installer_label(name):
    return INSTALLERS.get(name, name or "")


# --------------------------------------------------------------------------- signature des entrées (JAR, PKCS#7)

def _der(data, pos):
    """(étiquette, début du contenu, fin du contenu) de l'élément DER qui commence à pos."""
    if pos + 2 > len(data):
        raise ValueError("DER tronqué")
    tag, length, pos = data[pos], data[pos + 1], pos + 2
    if length & 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4 or pos + count > len(data):
            raise ValueError("longueur DER invalide")
        length, pos = int.from_bytes(data[pos:pos + count], "big"), pos + count
    if pos + length > len(data):
        raise ValueError("DER tronqué")
    return tag, pos, pos + length


def _items(data, start, end):
    """Éléments contenus entre start et end : (étiquette, début du contenu, fin, début de l'élément)."""
    out, pos = [], start
    while pos < end:
        tag, s, e = _der(data, pos)
        if e > end:
            raise ValueError("DER débordant")
        out.append((tag, s, e, pos))
        pos = e
    return out


def _oid(data, s, e):
    raw = data[s:e]
    if not raw:
        raise ValueError("OID vide")
    parts, value = [raw[0] // 40, raw[0] % 40], 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(p) for p in parts)


def _hash_for(oid):
    from cryptography.hazmat.primitives import hashes
    return {"2.16.840.1.101.3.4.2.1": ("sha256", hashes.SHA256), "2.16.840.1.101.3.4.2.2": ("sha384", hashes.SHA384),
            "2.16.840.1.101.3.4.2.3": ("sha512", hashes.SHA512)}.get(oid)


def _verifies(cert_der, signature, data, hash_class):
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    try:
        key = x509.load_der_x509_certificate(cert_der).public_key()
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, data, padding.PKCS1v15(), hash_class())
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, data, ec.ECDSA(hash_class()))
        else:
            return False
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def signers(block, signed):
    """Empreintes SHA-256 des certificats dont la clé a réellement signé « signed » (bloc PKCS#7 d'un JAR)."""
    try:
        tag, s, e = _der(block, 0)
        top = _items(block, s, e)
        if tag != 0x30 or len(top) < 2 or _oid(block, top[0][1], top[0][2]) != SIGNED_DATA or top[1][0] != 0xA0:
            raise ValueError("pas une signature PKCS#7")
        _tag, s, e = _der(block, top[1][1])
        certs, infos = [], []
        for t, cs, ce, _start in _items(block, s, e):
            if t == 0xA0:
                certs = [block[st:en] for _t, _s, en, st in _items(block, cs, ce)]
            elif t == 0x31:
                infos = _items(block, cs, ce)
        found = set()
        for _t, cs, ce, _start in infos:
            fields = _items(block, cs, ce)
            digest = _hash_for(_oid(block, *_items(block, fields[2][1], fields[2][2])[0][1:3]))
            i, attrs = 3, None
            if fields[i][0] == 0xA0:
                attrs, i = fields[i], i + 1
            signature = block[fields[i + 1][1]:fields[i + 1][2]]
            if digest is None:
                continue                            # SHA-1 et algorithmes inconnus : refusés
            data = signed
            if attrs:
                # Attributs signés : la signature porte sur eux (réencodés en SET), et l'un d'eux sur le contenu.
                data = b"\x31" + block[attrs[3] + 1:attrs[2]]
                expected = None
                for _a, a_s, a_e, _st in _items(block, attrs[1], attrs[2]):
                    parts = _items(block, a_s, a_e)
                    if _oid(block, parts[0][1], parts[0][2]) == MESSAGE_DIGEST:
                        value = _items(block, parts[1][1], parts[1][2])[0]
                        expected = block[value[1]:value[2]]
                if expected != hashlib.new(digest[0], signed).digest():
                    continue
            for cert in certs:
                if _verifies(cert, signature, data, digest[1]):
                    found.add(hashlib.sha256(cert).hexdigest())
        return found
    except (IndexError, ValueError) as e:
        raise ValueError(_("signature du dépôt illisible")) from e


def _manifest(text):
    """Sections d'un manifeste JAR (lignes de 72 octets continuées par une espace)."""
    sections, current, last = [], {}, None
    for line in text.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        if not line:
            if current:
                sections.append(current)
            current, last = {}, None
        elif line.startswith(b" ") and last:
            current[last] += line[1:]
        elif b": " in line:
            key, value = line.split(b": ", 1)
            last = key.decode("ascii", "replace")
            current[last] = value
    if current:
        sections.append(current)
    return sections


def _digest_matches(attributes, suffix, content):
    """Vrai si un condensé SHA-256/384/512 de la section vaut celui du contenu (SHA-1 refusé)."""
    ok = False
    for name, algorithm in MANIFEST_DIGESTS.items():
        value = attributes.get(name + suffix)
        if value is not None:
            if base64.b64decode(value) != hashlib.new(algorithm, content).digest():
                return False
            ok = True
    return ok


def read_entry(jar, fingerprint):
    """Contenu de entry.json, seulement si l'entrée est intacte et signée par la clé dont l'empreinte est confirmée."""
    try:
        z = zipfile.ZipFile(io.BytesIO(jar))
        names = z.namelist()
        if len(names) > 32 or len(names) != len(set(names)):
            raise ValueError("archive suspecte")
        entry, manifest = z.read("entry.json"), z.read("META-INF/MANIFEST.MF")
        sections = _manifest(manifest)
        named = [s for s in sections[1:] if s.get("Name") == b"entry.json"]
        if len(named) != 1 or not _digest_matches(named[0], "-Digest", entry):
            raise ValueError(_("entrée du dépôt altérée"))
        for name in names:
            if not re.fullmatch(r"META-INF/[^/]+\.SF", name):
                continue
            sf = z.read(name)
            main = _manifest(sf)
            if not _digest_matches(main[0] if main else {}, "-Digest-Manifest", manifest):
                continue
            for ext in ("RSA", "EC"):
                block = name[:-3] + "." + ext
                if block in names and fingerprint.lower() in signers(z.read(block), sf):
                    data = json.loads(entry.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError(_("entrée du dépôt illisible"))
                    return data
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as e:
        raise ValueError(_("entrée du dépôt illisible")) from e
    raise ValueError(_("signature invalide, ou clé différente de l'empreinte confirmée"))


def jar_signers(jar):
    """Empreintes des clés qui signent une entrée intacte : proposées à la confirmation pour une source nouvelle."""
    z = zipfile.ZipFile(io.BytesIO(jar))
    out = set()
    for name in z.namelist():
        if re.fullmatch(r"META-INF/[^/]+\.SF", name):
            for ext in ("RSA", "EC"):
                if name[:-3] + "." + ext in z.namelist():
                    out |= signers(z.read(name[:-3] + "." + ext), z.read(name))
    for fingerprint in sorted(out):
        try:
            read_entry(jar, fingerprint)
            return fingerprint
        except ValueError:
            continue
    raise ValueError(_("entrée non signée ou altérée"))


# --------------------------------------------------------------------------- lecture de l'index

_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}\[\]]')


def iter_packages(path):
    """(paquet, données) de chaque application de l'index, sans le charger en entier en mémoire (70 Mo pour F-Droid) :
    chaque application est découpée dans le texte puis lue seule."""
    with open(path, "rb") as f:
        data = f.read()
    depth, start, key, inside, last = 0, None, None, False, None
    for m in _TOKEN.finditer(data):
        token = m.group()
        if token[:1] == b'"':
            if depth == 1:
                last = token
            elif inside and depth == 2 and start is None:
                key = token
            continue
        if token in (b"{", b"["):
            depth += 1
            if depth == 2 and token == b"{" and last == b'"packages"':
                inside = True
            elif inside and depth == 3 and start is None:
                start = m.start()
        else:
            if inside and depth == 3 and start is not None:
                try:
                    name, value = json.loads(key), json.loads(data[start:m.end()])
                    if isinstance(name, str) and isinstance(value, dict):
                        yield name, value
                except (TypeError, ValueError):
                    pass
                start = key = None
            if depth == 2:
                inside = False
            depth -= 1


_LANGS = {"fr": ("fr", "fr-FR", "fr-CA", "fr-BE"), "en": ("en-US", "en", "en-GB"), "es": ("es", "es-ES", "es-419", "es-MX", "es-US")}


def _plain(text):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _localized(value, limit, plain=False):
    """Textes en français, anglais et espagnol (repli sur l'anglais) ; seuls ceux qui diffèrent de l'anglais sont gardés."""
    if isinstance(value, str):
        value = {"en-US": value}
    if not isinstance(value, dict):
        return {}
    texts = {k: v for k, v in value.items() if isinstance(v, str) and v.strip()}
    if not texts:
        return {}
    out = {}
    for lang in ("en", "fr", "es"):
        text = next((texts[k] for k in _LANGS[lang] if k in texts), None)
        if text is None:
            text = next((texts[k] for k in _LANGS["en"] if k in texts), None) or next(iter(texts.values()))
        text = (_plain(text) if plain else text.strip())[:limit]
        if lang == "en" or text != out["en"]:
            out[lang] = text
    return out


def text_for(value, lang=None):
    data = json.loads(value) if isinstance(value, str) else (value or {})
    return data.get(lang or locale()) or data.get("en") or ""


def _url(value):
    return value[:300] if isinstance(value, str) and value.startswith(("https://", "http://")) else ""


def _int(value, default=0):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def project(package, data):
    """Ce que loco garde d'une application de l'index : textes, logo, et les versions stables récentes."""
    if not PACKAGE_RE.fullmatch(package) or len(package) > 200:
        return None
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    candidates = []
    for version in (data.get("versions") or {}).values():
        if not isinstance(version, dict) or version.get("releaseChannels"):
            continue                                    # bêtas exclues
        f = version.get("file") if isinstance(version.get("file"), dict) else {}
        m = version.get("manifest") if isinstance(version.get("manifest"), dict) else {}
        sha = str(f.get("sha256") or "").lower()
        if not (isinstance(f.get("name"), str) and FILE_RE.fullmatch(f["name"]) and SHA_RE.fullmatch(sha)
                and 0 < _int(f.get("size")) <= MAX_APK):
            continue
        sdk = m.get("usesSdk") if isinstance(m.get("usesSdk"), dict) else {}
        signer = (m.get("signer") or {}).get("sha256") if isinstance(m.get("signer"), dict) else None
        signer = str(signer[0]).lower() if isinstance(signer, list) and signer else ""
        candidates.append({"code": _int(m.get("versionCode")), "name": str(m.get("versionName") or "")[:80],
                           "file": f["name"], "sha256": sha, "size": f["size"],
                           "min_sdk": _int(sdk.get("minSdkVersion"), 1), "target_sdk": _int(sdk.get("targetSdkVersion")),
                           "abis": [str(a)[:20] for a in (m.get("nativecode") or []) if isinstance(a, str)][:8],
                           "signer": signer if SHA_RE.fullmatch(signer) else "", "added": _int(version.get("added")),
                           "anti": sorted(str(k)[:30] for k in (version.get("antiFeatures") or {}))[:12]})
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["code"], reverse=True)
    latest = candidates[0]["name"]
    keep = [c for c in candidates if c["name"] == latest][:8] + [c for c in candidates if c["name"] != latest][:4]
    names = _localized(meta.get("name"), 120) or {"en": package}
    summaries = _localized(meta.get("summary"), 300)
    icon = {}
    icons = meta.get("icon") if isinstance(meta.get("icon"), dict) else {}
    for key in ("en-US", "en", *icons.keys()):
        choice = icons.get(key)
        if (isinstance(choice, dict) and isinstance(choice.get("name"), str) and FILE_RE.fullmatch(choice["name"])
                and SHA_RE.fullmatch(str(choice.get("sha256") or "").lower()) and 0 < _int(choice.get("size")) <= MAX_ICON):
            icon = {"name": choice["name"], "sha256": choice["sha256"].lower(), "size": choice["size"]}
            break
    categories = [str(c)[:40] for c in (meta.get("categories") or []) if isinstance(c, str)][:20]
    search = " ".join([package, *names.values(), *summaries.values()]).lower()
    return {"package": package, "names": json.dumps(names), "summaries": json.dumps(summaries),
            "descriptions": json.dumps(_localized(meta.get("description"), 4000, plain=True)),
            "search": search, "categories": "|" + "|".join(categories) + "|", "icon": json.dumps(icon) if icon else "",
            "license": str(meta.get("license") or "")[:80], "author": str(meta.get("authorName") or "")[:120],
            "website": _url(meta.get("webSite")), "source_code": _url(meta.get("sourceCode")),
            "anti_features": json.dumps(candidates[0]["anti"]), "version": latest, "version_code": candidates[0]["code"],
            "updated": _int(meta.get("lastUpdated")) or candidates[0]["added"], "versions": json.dumps(keep)}


# --------------------------------------------------------------------------- index local (cache régénérable)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (source_id INTEGER PRIMARY KEY, url TEXT NOT NULL, fingerprint TEXT NOT NULL,
    timestamp INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS apps (source_id INTEGER NOT NULL, package TEXT NOT NULL, names TEXT NOT NULL,
    summaries TEXT NOT NULL, descriptions TEXT NOT NULL, search TEXT NOT NULL, categories TEXT NOT NULL,
    icon TEXT NOT NULL, license TEXT NOT NULL, author TEXT NOT NULL, website TEXT NOT NULL, source_code TEXT NOT NULL,
    anti_features TEXT NOT NULL, version TEXT NOT NULL, version_code INTEGER NOT NULL, updated INTEGER NOT NULL,
    versions TEXT NOT NULL, PRIMARY KEY (source_id, package));
"""
COLUMNS = ("package", "names", "summaries", "descriptions", "search", "categories", "icon", "license", "author", "website",
           "source_code", "anti_features", "version", "version_code", "updated", "versions")


def _index():
    os.makedirs(ROOT, exist_ok=True)
    conn = sqlite3.connect(INDEX_DB, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def sources(enabled_only=False, conn=None):
    return (conn or db()).execute("SELECT * FROM android_sources" + (" WHERE enabled=1" if enabled_only else "") +
                                  " ORDER BY position, id").fetchall()


def _usable(conn=None):
    """Sources activées dont l'index local correspond bien à l'adresse et à la clé enregistrées (relu une fois par requête)."""
    if conn is None and has_request_context() and "android_usable" in g:
        return g.android_usable
    wanted = {s["id"]: s for s in sources(True, conn)}
    if not wanted or not os.path.isfile(INDEX_DB):
        return []
    with _index() as idx:
        rows = {r["source_id"]: r for r in idx.execute("SELECT * FROM sources")}
    usable = [s for sid, s in wanted.items()
              if sid in rows and rows[sid]["url"] == s["url"] and rows[sid]["fingerprint"] == s["fingerprint"]]
    if conn is None and has_request_context():
        g.android_usable = usable
    return usable


def _fetch(url, limit, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(_('réponse trop volumineuse'))
    return data


def _download(url, size, sha256, directory, gzip_ok=False):
    """Fichier téléchargé dans directory (nom temporaire), seulement s'il a exactement la taille et l'empreinte attendues."""
    os.makedirs(directory, exist_ok=True)
    headers = {"User-Agent": config.USER_AGENT}
    if gzip_ok:
        headers["Accept-Encoding"] = "gzip"
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".dl-")
    try:
        digest, done = hashlib.sha256(), 0
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r, os.fdopen(fd, "wb") as out:
            stream = gzip.GzipFile(fileobj=r) if r.headers.get("Content-Encoding", "").lower() == "gzip" else r
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    raise ValueError(_('fichier plus gros qu’annoncé par le dépôt'))
                digest.update(chunk)
                out.write(chunk)
        if done != size or digest.hexdigest() != sha256:
            raise ValueError(_('empreinte SHA-256 incorrecte : fichier refusé'))
        return tmp
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def sync(source):
    """Télécharge et vérifie l'index d'une source. Renvoie (nombre d'applications, horodatage de l'entrée signée)."""
    base = source["url"].rstrip("/")
    signed = read_entry(_fetch(base + "/entry.jar", MAX_ENTRY), source["fingerprint"])
    stamp = _int(signed.get("timestamp"))
    if stamp < source["index_timestamp"]:
        raise ValueError(_("le dépôt présente un index plus ancien que celui déjà vérifié : refusé (rejeu possible)"))
    with _index() as idx:
        cached = idx.execute("SELECT * FROM sources WHERE source_id=?", (source["id"],)).fetchone()
        if (cached and cached["timestamp"] == stamp and cached["url"] == source["url"]
                and cached["fingerprint"] == source["fingerprint"]):
            count = idx.execute("SELECT COUNT(*) FROM apps WHERE source_id=?", (source["id"],)).fetchone()[0]
            return count, stamp
    info = signed.get("index") if isinstance(signed.get("index"), dict) else {}
    name, sha, size = info.get("name"), str(info.get("sha256") or "").lower(), _int(info.get("size"))
    if not (isinstance(name, str) and FILE_RE.fullmatch(name) and name.endswith(".json") and SHA_RE.fullmatch(sha)
            and 0 < size <= MAX_INDEX):
        raise ValueError(_("format d'index non pris en charge (index-v2 attendu)"))
    tmp = _download(base + name, size, sha, ROOT, gzip_ok=True)
    try:
        rows = [r for r in (project(p, d) for p, d in iter_packages(tmp)) if r]
    finally:
        os.unlink(tmp)
    with _index() as idx:
        idx.execute("DELETE FROM apps WHERE source_id=?", (source["id"],))
        idx.executemany("INSERT OR REPLACE INTO apps (source_id, " + ", ".join(COLUMNS) + ") VALUES (?" + ", ?" * len(COLUMNS) + ")",
                        [(source["id"], *(r[c] for c in COLUMNS)) for r in rows])
        idx.execute("INSERT OR REPLACE INTO sources (source_id, url, fingerprint, timestamp) VALUES (?, ?, ?, ?)",
                    (source["id"], source["url"], source["fingerprint"], stamp))
    return len(rows), stamp


_RUNNING = set()
_RUN_LOCK = threading.Lock()


def due(source):
    """Index à relire : jamais lu, lu il y a plus d'un jour, ou en échec depuis plus d'une heure."""
    last = time.mktime(time.strptime(source["checked_at"], "%Y-%m-%d %H:%M")) if source["checked_at"] else 0
    return time.time() - last >= (RETRY_SECONDS if source["error"] else SYNC_SECONDS)


def refresh(source_id=None, due_only=False):
    """Actualise les sources activées (ou une seule) et note le résultat de chacune. Une seule fois à la fois par source."""
    with transaction() as conn:
        rows = [s for s in sources(True, conn) if source_id is None or s["id"] == source_id]
    for source in rows:
        if due_only and not due(source):
            continue
        with _RUN_LOCK:
            if source["id"] in _RUNNING:
                continue
            _RUNNING.add(source["id"])
        try:
            count, stamp = sync(source)
            with transaction() as conn:
                conn.execute("UPDATE android_sources SET apps=?, index_timestamp=MAX(index_timestamp, ?), synced_at=?, "
                             "checked_at=?, error='' WHERE id=?", (count, stamp, now(), now(), source["id"]))
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, EOFError) as e:
            with transaction() as conn:
                conn.execute("UPDATE android_sources SET checked_at=?, error=? WHERE id=?",
                             (now(), str(e)[:300] or e.__class__.__name__, source["id"]))
        finally:
            with _RUN_LOCK:
                _RUNNING.discard(source["id"])


def refresh_in_background(source_id=None, due_only=False):
    threading.Thread(target=refresh, args=(source_id, due_only), name="loco-android-index", daemon=True).start()


def running():
    with _RUN_LOCK:
        return set(_RUNNING)


_started = False


def start():
    """Tâche de fond : index relus chaque jour, APK inutiles retirés. Un seul processus agit, jamais le serveur de secours."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(45)
        from . import ha
        while True:
            try:
                with transaction() as conn:
                    on = "android" not in features.disabled(conn)
                if on and not ha.skip_background() and lease("android"):
                    refresh(due_only=True)
                    cleanup_apks()
            except Exception:
                pass
            time.sleep(600)

    threading.Thread(target=loop, name="loco-android", daemon=True).start()


# --------------------------------------------------------------------------- consultation de l'index

def _source_names(rows):
    return {s["id"]: s["name"] for s in rows}


def browse(query="", category="selection", page=1, size=24):
    """Applications des sources activées, une fois chacune (la première source dans l'ordre l'emporte)."""
    usable = _usable()
    category = category if category in CATEGORIES else "all"
    if not usable:
        return [], 0, 1, 1
    order = {s["id"]: i for i, s in enumerate(usable)}
    names = _source_names(usable)
    conditions, args = ["source_id IN (" + ",".join("?" * len(usable)) + ")"], [s["id"] for s in usable]
    query = query.strip().lower()
    if query:
        conditions.append("search LIKE ? ESCAPE '\\'")
        args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    elif category == "selection":
        conditions.append("package IN (" + ",".join("?" * len(SELECTION)) + ")")
        args.extend(SELECTION)
    elif CATEGORIES[category][1]:
        conditions.append("(" + " OR ".join("categories LIKE ?" for _c in CATEGORIES[category][1]) + ")")
        args.extend(f"%|{c}|%" for c in CATEGORIES[category][1])
    with _index() as idx:
        rows = idx.execute("SELECT source_id, package, names, summaries, icon, version, anti_features FROM apps WHERE "
                           + " AND ".join(conditions), args).fetchall()
    best = {}
    for r in rows:
        if r["package"] not in best or order[r["source_id"]] < order[best[r["package"]]["source_id"]]:
            best[r["package"]] = r
    lang = locale()
    items = [{"source_id": r["source_id"], "source": names[r["source_id"]], "package": r["package"],
              "name": text_for(r["names"], lang) or r["package"], "summary": text_for(r["summaries"], lang),
              "version": r["version"], "icon": bool(r["icon"]), "icon_info": json.loads(r["icon"]) if r["icon"] else None,
              "anti": json.loads(r["anti_features"])} for r in best.values()]
    if query:
        items.sort(key=lambda i: (i["name"].lower() != query and i["package"].lower() != query,
                                  not i["name"].lower().startswith(query), i["name"].lower()))
    elif category == "selection":
        items.sort(key=lambda i: SELECTION.index(i["package"]))
    else:
        items.sort(key=lambda i: i["name"].lower())
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = min(max(1, page), pages)
    return items[(page - 1) * size:page * size], total, page, pages


def entry(source_id, package):
    """Fiche d'une application d'une source activée (dict) ou None ; gardée le temps de la requête."""
    memo = g.setdefault("android_entries", {}) if has_request_context() else {}
    if (source_id, package) in memo:
        return memo[(source_id, package)]
    row = None
    if any(s["id"] == source_id for s in _usable()):
        with _index() as idx:
            row = idx.execute("SELECT * FROM apps WHERE source_id=? AND package=?", (source_id, package)).fetchone()
    if row is None:
        memo[(source_id, package)] = None
        return None
    lang = locale()
    out = dict(row)
    out.update(name=text_for(row["names"], lang) or package, summary=text_for(row["summaries"], lang),
               description=text_for(row["descriptions"], lang), versions=json.loads(row["versions"]),
               anti=json.loads(row["anti_features"]), icon=json.loads(row["icon"]) if row["icon"] else {},
               categories=[c for c in row["categories"].split("|") if c])
    memo[(source_id, package)] = out
    return out


def other_sources(package, source_id):
    usable = _usable()
    with _index() as idx:
        found = {r[0] for r in idx.execute("SELECT source_id FROM apps WHERE package=? AND source_id!=?", (package, source_id))}
    return [s for s in usable if s["id"] in found]


def compatible(versions, sdk=0, abis=()):
    """Version la plus récente installable sur l'appareil (Android et processeurs connus), ou None."""
    for v in sorted(versions, key=lambda c: c["code"], reverse=True):
        if sdk and v["min_sdk"] > sdk:
            continue
        if v["abis"] and abis and not set(v["abis"]) & set(abis):
            continue
        if v["abis"] and not abis and len(versions) > 1 and any(not c["abis"] for c in versions):
            continue                                    # processeur inconnu : préférer une version universelle
        return v
    return None


# --------------------------------------------------------------------------- appareils

def device_system(dev):
    """(niveau d'API Android, processeurs pris en charge, installation autorisée) remontés par l'agent."""
    from .fleet import software
    system = software(dev).get("system") or {}
    abis = [a for a in system.get("abis") or [] if isinstance(a, str)]
    return int(system.get("sdk") or 0), abis, system.get("can_install")


def installed(dev):
    """Applications de l'appareil : paquet -> {version, code, installer}."""
    from .fleet import inventory
    out = {}
    for a in inventory(dev).get("apps") or []:
        if a.get("publisher") and PACKAGE_RE.fullmatch(a["publisher"]):
            out[a["publisher"]] = {"version": a.get("version", ""), "code": a.get("code") or 0, "installer": a.get("installer", "")}
    return out


def ready_agent(dev):
    from .fleet import version_key
    return version_key(dev["agent_version"]) >= AGENT_VERSION


def catalog_rows():
    return db().execute("SELECT a.*, s.name AS source_name, s.enabled AS source_enabled FROM android_apps a "
                        "LEFT JOIN android_sources s ON s.id=a.source_id ORDER BY a.name COLLATE NOCASE").fetchall()


def snapshot(app_row):
    """Ce que fige une préparation de déploiement : versions vérifiables par leur empreinte et leur adresse d'origine."""
    base = {"id": app_row["id"], "package": app_row["package"], "name": app_row["name"], "store": app_row["store"]}
    if app_row["store"] == "play":
        return base, ""
    source = db().execute("SELECT * FROM android_sources WHERE id=?", (app_row["source_id"],)).fetchone()
    if source is None or not source["enabled"]:
        return base, _("source retirée ou désactivée")
    info = entry(source["id"], app_row["package"])
    if info is None:
        return base, _("absente de l'index de %(source)s", source=source["name"])
    url = source["url"].rstrip("/")
    base.update(source=source["name"], version=info["version"],
                candidates=[{**{k: v[k] for k in ("code", "name", "sha256", "size", "min_sdk", "abis", "signer")},
                             "url": url + urllib.parse.quote(v["file"])} for v in info["versions"]])
    return base, ""


def item_for(dev, snap, force=False):
    """Élément de commande pour cet appareil, ou (None, raison) : version compatible, pas déjà installée."""
    if snap["store"] == "play":
        if not force and snap["package"] in installed(dev):
            return None, _("Déjà installée")
        return {"store": "play", "package": snap["package"], "name": snap["name"]}, ""
    sdk, abis, _allowed = device_system(dev)
    choice = compatible(snap.get("candidates") or [], sdk, abis)
    if choice is None:
        return None, _("Aucune version compatible avec cet appareil")
    have = installed(dev).get(snap["package"])
    if have and not force and have["code"] and have["code"] >= choice["code"]:
        return None, _("Version déjà installée")
    return {"store": "fdroid", "package": snap["package"], "name": snap["name"], "version": choice["name"],
            "code": choice["code"], "sha256": choice["sha256"], "size": choice["size"], "signer": choice["signer"],
            "source_url": choice["url"]}, ""


def updates_for(dev, catalog=None):
    """Applications du catalogue installées par loco sur cet appareil et dont une version plus récente est compatible."""
    out = []
    have = installed(dev)
    sdk, abis, _allowed = device_system(dev)
    for row in catalog if catalog is not None else catalog_rows():
        current = have.get(row["package"])
        if row["store"] != "fdroid" or not current or current["installer"] != LOCO_PACKAGE or not row["source_enabled"]:
            continue
        info = entry(row["source_id"], row["package"])
        choice = compatible(info["versions"], sdk, abis) if info else None
        if choice and current["code"] and choice["code"] > current["code"]:
            out.append((row, current, choice))
    return out


def device_view(dev):
    """Fiche d'un appareil : chaque application du catalogue, sa version installée et ce que loco peut y faire."""
    have = installed(dev)
    sdk, abis, allowed = device_system(dev)
    rows = []
    for row in catalog_rows():
        current = have.get(row["package"])
        info = entry(row["source_id"], row["package"]) if row["store"] == "fdroid" and row["source_enabled"] else None
        choice = compatible(info["versions"], sdk, abis) if info else None
        if row["store"] == "play":
            state = "installed" if current else "absent"
        elif not current:
            state = "absent" if choice else "unavailable"
        elif current["installer"] != LOCO_PACKAGE:
            state = "other"                 # installée par une autre boutique : c'est elle qui la met à jour
        elif choice and current["code"] and choice["code"] > current["code"]:
            state = "update"
        else:
            state = "ok"
        rows.append({"row": row, "current": current, "choice": choice, "state": state})
    return {"catalog": rows, "can_install": allowed, "ready": ready_agent(dev)}


# --------------------------------------------------------------------------- APK conservés par loco

_APK_STATE = {}                 # empreinte -> ("running" | message d'erreur, instant)
_APK_GUARD = threading.Lock()


def apk_path(sha256):
    return os.path.join(APK_DIR, sha256 + ".apk")


def _fetch_apk(item):
    try:
        tmp = _download(item["source_url"], item["size"], item["sha256"], APK_DIR)
        os.chmod(tmp, 0o644)
        os.replace(tmp, apk_path(item["sha256"]))
        with _APK_GUARD:
            _APK_STATE.pop(item["sha256"], None)
    except (OSError, ValueError, EOFError) as e:
        with _APK_GUARD:
            _APK_STATE[item["sha256"]] = (str(e)[:200] or e.__class__.__name__, time.time())


def apk_state(item):
    """« ready », « pending » (téléchargement lancé ou en cours) ou le message d'une erreur récente."""
    sha = item.get("sha256", "")
    if not SHA_RE.fullmatch(sha) or not str(item.get("source_url", "")).startswith(("https://", "http://")):
        return _("APK sans empreinte ni adresse valides")
    path = apk_path(sha)
    if os.path.isfile(path) and os.path.getsize(path) == item.get("size"):
        os.utime(path)
        return "ready"
    with _APK_GUARD:
        state = _APK_STATE.get(sha)
        if state and state[0] == "running":
            return "pending"
        if state and time.time() - state[1] < 600:
            return _("téléchargement de l'APK impossible : %(e)s", e=state[0])
        _APK_STATE[sha] = ("running", time.time())
    threading.Thread(target=_fetch_apk, args=(dict(item),), name="loco-android-apk", daemon=True).start()
    return "pending"


def cleanup_apks():
    """APK sans installation en attente, inutilisés depuis un jour : retirés (ils se retéléchargent au besoin)."""
    if not os.path.isdir(APK_DIR):
        return
    needed = set()
    with transaction() as conn:
        for row in conn.execute("SELECT params FROM device_commands WHERE action='install-apk' AND state IN ('queued','sent','running')"):
            needed.update(a.get("sha256") for a in json.loads(row[0]).get("apps", []))
        for row in conn.execute("SELECT pending FROM deployment_targets WHERE pending!=''"):
            needed.update(a.get("sha256") for a in json.loads(row[0]).get("params", {}).get("apps", []))
    for name in os.listdir(APK_DIR):
        path = os.path.join(APK_DIR, name)
        stale = name.startswith(".dl-") and time.time() - os.path.getmtime(path) > 6 * 3600
        if stale or (name.endswith(".apk") and name[:-4] not in needed and time.time() - os.path.getmtime(path) > APK_KEEP_SECONDS):
            try:
                os.unlink(path)
            except OSError:
                pass


@app.route("/agent/android/apk/<sha256>")
def agent_android_apk(sha256):
    """APK vérifié, téléchargé par l'application loco d'un appareil inscrit (jeton de l'appareil exigé)."""
    from .fleet import _device
    dev = _device()
    if dev["os_family"] != "android" or not SHA_RE.fullmatch(sha256) or not os.path.isfile(apk_path(sha256)):
        abort(404)
    response = send_file(apk_path(sha256), mimetype="application/vnd.android.package-archive", conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- logos

_ICON_SEM = threading.Semaphore(8)
ICON_TYPES = (("png", b"\x89PNG\r\n\x1a\n", "image/png"), ("jpeg", b"\xff\xd8\xff", "image/jpeg"))


def _icon_type(data):
    for kind, magic, mime in ICON_TYPES:
        if data.startswith(magic):
            return kind, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _cached_icon(icon):
    for kind, _magic, mime in (*ICON_TYPES, ("webp", b"", "image/webp")):
        path = os.path.join(ICON_DIR, f"{icon['sha256']}.{kind}")
        if os.path.isfile(path):
            return path, mime
    return None


def _store_icon(base_url, icon):
    """Télécharge le logo depuis le dépôt et le garde seulement s'il a l'empreinte inscrite dans l'index signé."""
    cached = _cached_icon(icon)
    if cached:
        return cached
    sha = icon["sha256"]
    with _ICON_SEM:
        try:
            data = _fetch(base_url.rstrip("/") + urllib.parse.quote(icon["name"]), MAX_ICON, timeout=15)
        except (OSError, ValueError):
            return None
    found = _icon_type(data)
    if found is None or hashlib.sha256(data).hexdigest() != sha:
        return None
    os.makedirs(ICON_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ICON_DIR, prefix=".icon-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    path = os.path.join(ICON_DIR, f"{sha}.{found[0]}")
    os.replace(tmp, path)
    return path, found[1]


def icon_file(source_id, package):
    """(chemin, type) du logo vérifié, mis en cache ; aucun lien externe dans le navigateur."""
    info = entry(source_id, package)
    if not info or not info["icon"]:
        return None
    source = db().execute("SELECT url FROM android_sources WHERE id=?", (source_id,)).fetchone()
    return _store_icon(source["url"], info["icon"]) if source else None


def warm_icons(items, urls):
    """Préchauffage en arrière-plan des logos d'une page, avant que le navigateur ne les demande."""
    for item in items:
        if item.get("icon_info") and item["source_id"] in urls and not _cached_icon(item["icon_info"]):
            threading.Thread(target=_store_icon, args=(urls[item["source_id"]], item["icon_info"]),
                             name="loco-android-icon", daemon=True).start()


@app.route("/android/icons/<int:source_id>/<package>")
@login_required
def android_icon(source_id, package):
    found = icon_file(source_id, package) if PACKAGE_RE.fullmatch(package) else None
    if found is None:
        return "", 204, {"Cache-Control": "private, max-age=3600"}
    response = send_file(found[0], mimetype=found[1], conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=604800, stale-while-revalidate=86400"
    return response


# --------------------------------------------------------------------------- pages

def _known_rows():
    return {s["known"]: s for s in sources() if s["known"]}


def _missing(added):
    """Sources vérifiées par loco qui ne sont pas encore ajoutées."""
    urls = {s["url"] for s in sources()}
    return [(key, k) for key, k in KNOWN.items() if key not in added and k[1] not in urls]


def _catalog_view():
    """Catalogue avec, pour chaque application, sa version, les appareils qui l'ont et ceux à mettre à jour."""
    rows = catalog_rows()
    from . import scope
    devices = db().execute("SELECT * FROM devices WHERE os_family='android' AND state='active'" + scope.sql()).fetchall()
    have = {d["id"]: installed(d) for d in devices}
    pending = {}
    for d in devices:
        for row, _current, _choice in updates_for(d, rows):
            pending.setdefault(row["id"], []).append(d["id"])
    view = []
    for row in rows:
        info = entry(row["source_id"], row["package"]) if row["store"] == "fdroid" and row["source_enabled"] else None
        problem = ""
        if row["store"] == "fdroid" and info is None:
            problem = _("Source retirée ou désactivée") if not row["source_enabled"] else _("Absente de l'index de la source")
        view.append({"row": row, "info": info, "problem": problem,
                     "installed": [d for d in devices if row["package"] in have[d["id"]]],
                     "updates": pending.get(row["id"], [])})
    return view, devices


@app.route("/android/apps")
@login_required
def android_apps():
    view, devices = _catalog_view()
    return render_template("android_apps.html", apps=view, devices=devices, sources=sources(),
                           ready=[d for d in devices if ready_agent(d)])


@app.route("/android/store")
@login_required
def android_store():
    query = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "all" if query else "selection")
    if category not in CATEGORIES:
        category = "all"
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    results, total, page, pages = browse(query, category, page)
    all_sources = sources()
    busy = running()
    waiting = {s["id"] for s in all_sources if s["enabled"] and due(s)}
    if not _usable() and waiting and not busy:
        refresh_in_background(due_only=True)        # premier passage : les index arrivent en arrière-plan
        busy = waiting
    known = {r["package"]: r for r in db().execute("SELECT * FROM android_apps")}
    warm_icons(results, {s["id"]: s["url"] for s in all_sources})
    return render_template("android_store.html", query=query, category=category, categories=CATEGORIES, results=results,
                           total=total, page=page, pages=pages, known=known, sources=all_sources, busy=busy)


@app.route("/android/store/<int:source_id>/<package>")
@login_required
def android_store_detail(source_id, package):
    if not PACKAGE_RE.fullmatch(package):
        abort(404)
    info = entry(source_id, package)
    if info is None:
        abort(404)
    source = db().execute("SELECT * FROM android_sources WHERE id=?", (source_id,)).fetchone()
    known = db().execute("SELECT a.*, s.name AS source_name FROM android_apps a LEFT JOIN android_sources s ON s.id=a.source_id "
                         "WHERE a.package=?", (package,)).fetchone()
    latest = [v for v in info["versions"] if v["name"] == info["version"]]
    known_from = ("Google Play" if known["store"] == "play" else known["source_name"] or "") if known else ""
    return render_template("android_store_detail.html", info=info, source=source, known=known, latest=latest,
                           others=other_sources(package, source_id), known_from=known_from)


@app.route("/android/sources")
@login_required
def android_sources():
    rows = sources()
    used = {r[0]: r[1] for r in db().execute("SELECT source_id, COUNT(*) FROM android_apps WHERE source_id IS NOT NULL GROUP BY source_id")}
    known = _known_rows()
    busy = running()
    signature = hashlib.sha256(json.dumps([[s["id"], s["synced_at"], s["checked_at"], s["error"]] for s in rows]
                                          + sorted(busy)).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    return render_template("android_sources.html", sources=rows, used=used, known=KNOWN, missing=_missing(known), busy=busy,
                           signature=signature, check=None)


def _insert_source(conn, name, url, fingerprint, known=""):
    position = (conn.execute("SELECT MAX(position) FROM android_sources").fetchone()[0] or 0) + 1
    if known in KNOWN:
        position = list(KNOWN).index(known)
    return conn.execute("INSERT INTO android_sources (name, url, fingerprint, known, enabled, position, added_at, added_by) "
                        "VALUES (?, ?, ?, ?, 1, ?, ?, ?)", (name, url, fingerprint, known, position, now(),
                                                            g.user["username"])).lastrowid


@app.route("/android/sources/known", methods=["POST"])
@login_required
def android_source_add_known():
    key = request.form.get("known", "")
    if key not in KNOWN:
        abort(400)
    conn = lock_access("android.manage")
    name, url, fingerprint, _desc = KNOWN[key]
    if conn.execute("SELECT 1 FROM android_sources WHERE url=? OR known=?", (url, key)).fetchone():
        conn.rollback()
        flash(_("Cette source est déjà dans loco."), "error")
        return back("android_sources")
    source_id = _insert_source(conn, name, url, fingerprint, key)
    audit("android.source", f"{name} ajoutée ({url}, empreinte {fingerprint})", commit=False)
    conn.commit()
    refresh_in_background(source_id)
    flash(_("%(name)s ajoutée : son index se télécharge et se vérifie en arrière-plan.", name=name), "ok")
    return back("android_sources")


def _clean_url(value):
    url = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or len(url) > 300 or "@" in parsed.netloc:
        raise ValueError(_("Adresse HTTPS d'un dépôt F-Droid attendue (par exemple https://exemple.org/fdroid/repo)."))
    return url


def _clean_fingerprint(value):
    fingerprint = re.sub(r"[\s:]", "", value).lower()
    if not SHA_RE.fullmatch(fingerprint):
        raise ValueError(_("Empreinte SHA-256 attendue : 64 caractères hexadécimaux."))
    return fingerprint


@app.route("/android/sources/check", methods=["POST"])
@login_required
def android_source_check():
    """Première étape d'une source personnalisée : lire sa clé, la montrer, la faire confirmer avant tout ajout."""
    conn = lock_access("android.manage")
    conn.rollback()
    check = {"url": request.form.get("url", "").strip()[:300], "name": request.form.get("name", "").strip()[:60]}
    try:
        check["url"] = _clean_url(check["url"])
        if conn.execute("SELECT 1 FROM android_sources WHERE url=?", (check["url"],)).fetchone():
            raise ValueError(_("Cette source est déjà dans loco."))
        check["fingerprint"] = jar_signers(_fetch(check["url"] + "/entry.jar", MAX_ENTRY))
        check["name"] = check["name"] or urllib.parse.urlparse(check["url"]).netloc
        check["known"] = next((k for k, v in KNOWN.items() if v[2] == check["fingerprint"]), "")
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        flash(_("Source non ajoutée : %(e)s", e=e), "error")
        return back("android_sources")
    rows = sources()
    return render_template("android_sources.html", sources=rows, used={}, known=KNOWN, missing=_missing(_known_rows()),
                           busy=running(), signature="", check=check)


@app.route("/android/sources/add", methods=["POST"])
@login_required
def android_source_add():
    conn = lock_access("android.manage")
    try:
        url = _clean_url(request.form.get("url", ""))
        fingerprint = _clean_fingerprint(request.form.get("fingerprint", ""))
        name = request.form.get("name", "").strip()[:60] or urllib.parse.urlparse(url).netloc
        if request.form.get("confirmed") != "1":
            raise ValueError(_("Confirmez que l'empreinte correspond à celle publiée par l'éditeur du dépôt."))
        if conn.execute("SELECT 1 FROM android_sources WHERE url=?", (url,)).fetchone():
            raise ValueError(_("Cette source est déjà dans loco."))
        read_entry(_fetch(url + "/entry.jar", MAX_ENTRY), fingerprint)        # la clé n'a pas changé depuis la lecture
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        conn.rollback()
        flash(_("Source non ajoutée : %(e)s", e=e), "error")
        return back("android_sources")
    source_id = _insert_source(conn, name, url, fingerprint)
    audit("android.source", f"{name} ajoutée ({url}, empreinte {fingerprint} confirmée)", commit=False)
    conn.commit()
    refresh_in_background(source_id)
    flash(_("%(name)s ajoutée : son index se télécharge et se vérifie en arrière-plan.", name=name), "ok")
    return back("android_sources")


def _source(conn, source_id):
    row = conn.execute("SELECT * FROM android_sources WHERE id=?", (source_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/android/sources/<int:source_id>/toggle", methods=["POST"])
@login_required
def android_source_toggle(source_id):
    conn = lock_access("android.manage")
    row = _source(conn, source_id)
    conn.execute("UPDATE android_sources SET enabled=? WHERE id=?", (0 if row["enabled"] else 1, source_id))
    audit("android.source", f"{row['name']} {'désactivée' if row['enabled'] else 'activée'}", commit=False)
    conn.commit()
    if not row["enabled"]:
        refresh_in_background(source_id)
    flash(_("%(name)s désactivée.", name=row["name"]) if row["enabled"] else _("%(name)s activée.", name=row["name"]), "ok")
    return back("android_sources")


@app.route("/android/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def android_source_delete(source_id):
    conn = lock_access("android.manage")
    row = _source(conn, source_id)
    used = conn.execute("SELECT COUNT(*) FROM android_apps WHERE source_id=?", (source_id,)).fetchone()[0]
    if used:
        conn.rollback()
        flash(n_("%(name)s fournit %(num)s application du catalogue : retirez-la d'abord, ou désactivez la source.",
                 "%(name)s fournit %(num)s applications du catalogue : retirez-les d'abord, ou désactivez la source.",
                 used, name=row["name"]), "error")
        return back("android_sources")
    conn.execute("DELETE FROM android_sources WHERE id=?", (source_id,))
    audit("android.source", f"{row['name']} retirée ({row['url']})", commit=False)
    conn.commit()
    try:
        with _index() as idx:
            idx.execute("DELETE FROM apps WHERE source_id=?", (source_id,))
            idx.execute("DELETE FROM sources WHERE source_id=?", (source_id,))
    except sqlite3.Error:
        pass
    flash(_("%(name)s retirée.", name=row["name"]), "ok")
    return back("android_sources")


@app.route("/android/sources/refresh", methods=["POST"])
@login_required
def android_sources_refresh():
    conn = lock_access("android.manage")
    conn.rollback()
    source_id = request.form.get("source", "")
    refresh_in_background(int(source_id) if source_id.isdigit() else None)
    flash(_("Actualisation lancée : les index se téléchargent et se vérifient en arrière-plan."), "ok")
    return back("android_sources")


def play_package(value):
    value = value.strip()
    m = PLAY_URL_RE.match(value)
    package = m.group(1) if m else value
    if not PACKAGE_RE.fullmatch(package) or len(package) > 200:
        raise ValueError(_("Nom de paquet (com.exemple.app) ou lien Google Play attendu."))
    return package


@app.route("/android/apps/add", methods=["POST"])
@login_required
def android_app_add():
    conn = lock_access("android.manage")
    store = request.form.get("store", "")
    try:
        if store == "play":
            package = play_package(request.form.get("package", ""))
            name = request.form.get("name", "").strip()[:100] or package
            source_id = None
        elif store == "fdroid":
            package = request.form.get("package", "")
            source_id = int(request.form.get("source", "0") or 0)
            info = entry(source_id, package) if PACKAGE_RE.fullmatch(package) else None
            if info is None:
                raise ValueError(_("Application absente des sources activées."))
            name = info["name"][:100]
        else:
            abort(400)
        current = conn.execute("SELECT * FROM android_apps WHERE package=?", (package,)).fetchone()
        if current:
            raise ValueError(_("%(name)s est déjà dans le catalogue.", name=current["name"]))
    except ValueError as e:
        conn.rollback()
        flash(str(e), "error")
        return back(request.form.get("retour") if request.form.get("retour") in ("android_store", "android_apps") else "android_apps")
    conn.execute("INSERT INTO android_apps (package, store, source_id, name, added_at, added_by) VALUES (?, ?, ?, ?, ?, ?)",
                 (package, store, source_id, name, now(), g.user["username"]))
    audit("android.catalogue", f"{name} ({package}) ajoutée depuis " + ("Google Play" if store == "play" else f"la source {source_id}"),
          commit=False)
    conn.commit()
    flash(_("%(name)s ajoutée au catalogue.", name=name), "ok")
    if request.form.get("retour") == "android_store":
        values = {k: request.form[k] for k in ("q", "category", "page") if request.form.get(k)}
        return back("android_store", **values)
    if request.form.get("retour") == "android_store_detail" and source_id:
        return back("android_store_detail", source_id=source_id, package=package)
    return back("android_apps")


@app.route("/android/apps/<int:app_id>/delete", methods=["POST"])
@login_required
def android_app_delete(app_id):
    conn = lock_access("android.manage")
    row = conn.execute("SELECT * FROM android_apps WHERE id=?", (app_id,)).fetchone()
    if row is None:
        abort(404)
    conn.execute("DELETE FROM android_apps WHERE id=?", (app_id,))
    audit("android.catalogue", f"{row['name']} ({row['package']}) retirée du catalogue", commit=False)
    conn.commit()
    flash(_("%(name)s retirée du catalogue. Rien n'est désinstallé des appareils.", name=row["name"]), "ok")
    return back("android_apps")


# Niveau d'API -> version d'Android affichée.
RELEASES = {14: "4.0", 15: "4.0.3", 16: "4.1", 17: "4.2", 18: "4.3", 19: "4.4", 20: "4.4W", 21: "5.0", 22: "5.1", 23: "6", 24: "7.0", 25: "7.1", 26: "8.0", 27: "8.1", 28: "9", 29: "10", 30: "11", 31: "12",
            32: "12L", 33: "13", 34: "14", 35: "15", 36: "16", 37: "17"}


def android_release(sdk):
    sdk = sdk or 0
    if sdk in RELEASES:
        return "Android " + RELEASES[sdk]
    return "Android " + RELEASES[max(RELEASES)] + "+" if sdk > max(RELEASES) else _("Android (API %(sdk)s)", sdk=sdk)


app.jinja_env.globals.update(android_installer=installer_label, android_text=text_for, android_release=android_release)
app.jinja_env.filters["android_anti"] = lambda key: str(ANTI_FEATURES.get(key, key))
