"""Copie des sauvegardes hors du serveur : S3-compatible (cloud ou MinIO), SFTP, ou dossier / point de montage.

Tourne dans un fil de fond du portail (compte loco, qui sait lire les sauvegardes). Après chaque sauvegarde
quotidienne, la plus récente est copiée vers la destination, chiffrée au préalable si une phrase de passe est donnée,
et la destination est limitée au nombre d'archives à conserver. Un échec est signalé (alerte « copie hors-site »).
"""
import base64
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from flask import abort, flash, request

from . import app, config
from .i18n import _
from .auth import audit, can, login_required, require
from .db import db, transaction

CONFIG = "offsite_config"
STATE = "offsite_state"
TYPES = ("none", "s3", "sftp", "folder")
# champ secret par section
SECRETS = {"s3": ("secret_key",), "sftp": ("private_key",), "crypt": ("passphrase",)}
NAME_RE = re.compile(r"(?:loco|local-repo)-[A-Za-z0-9._-]+-\d{8}-\d{6}\.tar\.gz(?:\.enc)?")


def _cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-offsite-v1", hashlib.sha256).digest()))


def _seal(v):
    return _cipher().encrypt(v.encode()).decode() if v else ""


def _open(v):
    try:
        return _cipher().decrypt(v.encode()).decode() if v else ""
    except Exception:
        return ""


def defaults():
    return {
        "type": "none",
        "s3": {"endpoint": "", "region": "us-east-1", "bucket": "", "prefix": "loco", "access_key": "",
               "secret_key": "", "use_ssl": True},
        "sftp": {"host": "", "port": 22, "username": "", "private_key": "", "base_dir": "loco-backups"},
        "folder": {"path": ""},
        "crypt": {"passphrase": ""},
    }


def _merge(base, stored):
    for k, v in (stored or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k].update({kk: vv for kk, vv in v.items() if kk in base[k]})
        elif k in base and not isinstance(base[k], dict):
            base[k] = v
    return base


def load(conn=None):
    cfg = defaults()
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (CONFIG,)).fetchone()
    if row:
        try:
            _merge(cfg, json.loads(row[0]))
        except (TypeError, ValueError):
            pass
    for section, fields in SECRETS.items():
        for field in fields:
            cfg[section][field] = _open(cfg[section][field])
    return cfg


def save(cfg, conn=None):
    stored = json.loads(json.dumps(cfg))
    for section, fields in SECRETS.items():
        for field in fields:
            stored[section][field] = _seal(cfg[section].get(field, ""))
    (conn or db()).execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CONFIG, json.dumps(stored)))


MASK = "••••••••"


def public(cfg):
    out = json.loads(json.dumps(cfg))
    for section, fields in SECRETS.items():
        for field in fields:
            out[section][field] = MASK if cfg[section].get(field) else ""
    return out


def state(conn=None):
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (STATE,)).fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        return {}


def _set_state(conn, **changes):
    st = state(conn)
    st.update(changes)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (STATE, json.dumps(st)))


# --------------------------------------------------------------------------- S3 (SigV4, sans dépendance)

def _sigv4(cfg, method, path, query, payload, now=None):
    s3 = cfg["s3"]
    host = s3["endpoint"].split("://")[-1].rstrip("/")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amzdate, datestamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amzdate}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = f"{method}\n{path}\n{query}\n{canonical_headers}\n{signed}\n{payload_hash}"
    scope = f"{datestamp}/{s3['region']}/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    k = hmac.new(("AWS4" + s3["secret_key"]).encode(), datestamp.encode(), hashlib.sha256).digest()
    for part in (s3["region"], "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Host": host, "x-amz-date": amzdate, "x-amz-content-sha256": payload_hash,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={s3['access_key']}/{scope}, "
                             f"SignedHeaders={signed}, Signature={sig}"}


def _s3_url(cfg, path, query=""):
    s3 = cfg["s3"]
    host = s3["endpoint"].split("://")[-1].rstrip("/")
    scheme = "https" if s3.get("use_ssl", True) else "http"
    return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")


def _s3_key(cfg, name):
    prefix = cfg["s3"]["prefix"].strip("/")
    return f"{prefix}/{name}" if prefix else name


def _s3_put(cfg, name, data):
    path = "/" + cfg["s3"]["bucket"] + "/" + urllib.parse.quote(_s3_key(cfg, name), safe="/")
    headers = _sigv4(cfg, "PUT", path, "", data)
    req = urllib.request.Request(_s3_url(cfg, path), data=data, headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status >= 300:
            raise ValueError(f"S3 HTTP {r.status}")


def _s3_list(cfg):
    prefix = cfg["s3"]["prefix"].strip("/")
    query = "list-type=2&prefix=" + urllib.parse.quote(prefix + "/" if prefix else "", safe="")
    path = "/" + cfg["s3"]["bucket"]
    headers = _sigv4(cfg, "GET", path, query, b"")
    req = urllib.request.Request(_s3_url(cfg, path, query), headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    names = []
    for el in ET.fromstring(body):
        if el.tag.endswith("Contents"):
            key = next((c.text for c in el if c.tag.endswith("Key")), "")
            base = key.rsplit("/", 1)[-1]
            if NAME_RE.fullmatch(base):
                names.append(base)
    return names


def _s3_delete(cfg, name):
    path = "/" + cfg["s3"]["bucket"] + "/" + urllib.parse.quote(_s3_key(cfg, name), safe="/")
    headers = _sigv4(cfg, "DELETE", path, "", b"")
    req = urllib.request.Request(_s3_url(cfg, path), headers=headers, method="DELETE")
    with urllib.request.urlopen(req, timeout=30):
        pass


# --------------------------------------------------------------------------- SFTP (clé, via openssh)

def _sftp_options(key_file, port):
    known = os.path.join(config.STATE, "offsite_known_hosts")
    return ["sftp", "-i", key_file, "-P", str(port or 22), "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={known}"]


def _sftp_run(cfg, commands):
    s = cfg["sftp"]
    if not s["private_key"]:
        raise ValueError(_("clé privée SSH manquante (SFTP par clé)"))
    with tempfile.NamedTemporaryFile("w", delete=False) as key:
        key.write(s["private_key"].rstrip("\n") + "\n")
        key_path = key.name
    os.chmod(key_path, 0o600)
    batch = tempfile.NamedTemporaryFile("w", delete=False)
    batch.write("\n".join(commands) + "\n")
    batch.close()
    try:
        cmd = _sftp_options(key_path, s["port"]) + ["-b", batch.name, f"{s['username']}@{s['host']}"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            raise ValueError((out.stderr or out.stdout or "SFTP").strip().splitlines()[-1][:200])
        return out.stdout
    finally:
        os.unlink(key_path)
        os.unlink(batch.name)


def _sftp_put(cfg, path, name):
    base = cfg["sftp"]["base_dir"].strip("/")
    _sftp_run(cfg, ([f"-mkdir {base}"] if base else []) + [f"put {path} {(base + '/' if base else '') + name}"])


def _sftp_list(cfg):
    base = cfg["sftp"]["base_dir"].strip("/")
    out = _sftp_run(cfg, [f"ls -1 {base or '.'}"])
    names = []
    for line in out.splitlines():
        base_name = line.strip().rsplit("/", 1)[-1]
        if NAME_RE.fullmatch(base_name):
            names.append(base_name)
    return names


def _sftp_delete(cfg, name):
    base = cfg["sftp"]["base_dir"].strip("/")
    _sftp_run(cfg, [f"rm {(base + '/' if base else '') + name}"])


# --------------------------------------------------------------------------- dossier / point de montage

def _folder_put(cfg, path, name):
    dest = cfg["folder"]["path"]
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, name + ".part")
    shutil.copyfile(path, tmp)
    os.replace(tmp, os.path.join(dest, name))


def _folder_list(cfg):
    try:
        return [n for n in os.listdir(cfg["folder"]["path"]) if NAME_RE.fullmatch(n)]
    except OSError:
        return []


def _folder_delete(cfg, name):
    with contextlib.suppress(OSError):
        os.unlink(os.path.join(cfg["folder"]["path"], name))


# --------------------------------------------------------------------------- transport commun

def _put(cfg, path, name):
    {"s3": lambda: _s3_put(cfg, name, open(path, "rb").read()),
     "sftp": lambda: _sftp_put(cfg, path, name),
     "folder": lambda: _folder_put(cfg, path, name)}[cfg["type"]]()


def _list(cfg):
    return {"s3": _s3_list, "sftp": _sftp_list, "folder": _folder_list}[cfg["type"]](cfg)


def _delete(cfg, name):
    {"s3": _s3_delete, "sftp": _sftp_delete, "folder": _folder_delete}[cfg["type"]](cfg, name)


def _encrypt(path, passphrase):
    """Chiffre l'archive avec openssl (AES-256) ; renvoie le chemin chiffré (fichier temporaire) et le suffixe."""
    enc = path + ".enc"
    rc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:LOCO_OFFSITE_PASS",
                         "-in", path, "-out", enc], env={**os.environ, "LOCO_OFFSITE_PASS": passphrase},
                        capture_output=True, text=True, timeout=300)
    if rc.returncode != 0:
        raise ValueError((rc.stderr or "chiffrement").strip()[:200])
    return enc


def configured(cfg):
    return cfg["type"] in ("s3", "sftp", "folder")


def sync(conn):
    """Copie la sauvegarde la plus récente si elle n'est pas déjà hors-site, puis applique la rétention distante."""
    from .system import list_backups
    cfg = load(conn)
    if not configured(cfg):
        return
    backups = list_backups()
    if not backups:
        return
    latest = backups[0]["name"]
    if state(conn).get("last_name") == latest:
        return
    source = os.path.join(config.BACKUP_DIR, latest)
    passphrase = cfg["crypt"]["passphrase"]
    remote_name, cleanup = latest, None
    try:
        if passphrase:
            cleanup = _encrypt(source, passphrase)
            source, remote_name = cleanup, latest + ".enc"
        _put(cfg, source, remote_name)
        from .system import backup_keep        # même rétention que les sauvegardes locales
        keep = max(1, backup_keep())
        for old in sorted(_list(cfg), reverse=True)[keep:]:
            _delete(cfg, old)
        _set_state(conn, last_name=latest, last_ok=datetime.datetime.now().isoformat(timespec="seconds"), last_error="")
    except Exception as e:
        _set_state(conn, last_error=f"{type(e).__name__}: {e}"[:300],
                   last_failed=datetime.datetime.now().isoformat(timespec="seconds"))
    finally:
        if cleanup:
            with contextlib.suppress(OSError):
                os.unlink(cleanup)


def test_destination(cfg):
    """Dépose puis supprime un petit objet de test ; renvoie (ok, message)."""
    name = f"loco-selftest-{datetime.datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(b"loco offsite test")
            tmp = f.name
        try:
            _put(cfg, tmp, name)
            _delete(cfg, name)
        finally:
            os.unlink(tmp)
        return True, _("Destination accessible (écriture et suppression réussies).")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]


# --------------------------------------------------------------------------- fil de fond

_started = False


def start():
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(30)
        while True:
            try:
                with transaction() as conn:
                    sync(conn)
            except Exception:
                pass
            time.sleep(300)

    threading.Thread(target=loop, name="loco-offsite", daemon=True).start()


# --------------------------------------------------------------------------- page et routes

def _form(cfg):
    """Met à jour la configuration depuis le formulaire. Les sections masquées (data-show-for) ne sont pas
    envoyées (champs désactivés) : on ne touche qu'aux champs réellement présents, pour ne pas écraser le reste."""
    f = request.form
    if f.get("offsite_type") in TYPES:
        cfg["type"] = f["offsite_type"]
    text = {"s3": ("endpoint", "region", "bucket", "prefix", "access_key"),
            "sftp": ("host", "username", "base_dir"), "folder": ("path",)}
    for section, fields in text.items():
        for field in fields:
            if f"{section}_{field}" in f:
                cfg[section][field] = f[f"{section}_{field}"].strip()
    if "s3_region" in f and not cfg["s3"]["region"]:
        cfg["s3"]["region"] = "us-east-1"
    if "s3_endpoint" in f:                        # case à cocher : présente seulement quand la section S3 est visible
        cfg["s3"]["use_ssl"] = f.get("s3_use_ssl") == "1"
    if "sftp_port" in f:
        cfg["sftp"]["port"] = int(f["sftp_port"]) if f["sftp_port"].isdigit() else 22
    for section, fields in SECRETS.items():
        for field in fields:
            key = f"{section}_{field}"
            if key in f and f[key] != MASK:
                cfg[section][field] = f[key]
    return cfg


@app.route("/system/offsite", methods=["POST"])
@login_required
def system_offsite():
    require("system.configure")
    if config.WINDOWS:
        abort(404)
    save(_form(load()))
    audit("systeme.sauvegarde.hors-site", _("Copie hors-site"))
    flash(_("Destination hors-site enregistrée."), "ok")
    from .util import back
    return back("system")


@app.route("/system/offsite/test", methods=["POST"])
@login_required
def system_offsite_test():
    require("system.configure")
    cfg = _form(load())
    if not configured(cfg):
        return {"ok": False, "message": _("Choisissez d'abord une destination.")}
    ok, message = test_destination(cfg)
    return {"ok": ok, "message": message}


def panel():
    """Données pour le panneau « Copie hors-site » intégré à la page Système."""
    if config.WINDOWS:
        return None
    cfg = load()
    return {"cfg": public(cfg), "state": state(), "configured": configured(cfg), "can_edit": can("system.configure")}
