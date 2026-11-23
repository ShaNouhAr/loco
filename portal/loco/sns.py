"""Stormshield SNS : l'API HTTPS du pare-feu (celle de son interface d'administration et de son client officiel).

Connexion avec un compte administrateur (identifiant et mot de passe en base64, comme le client officiel), puis une
session serverd : chaque commande (« SYSTEM PROPERTY », « CONFIG BACKUP list=all »…) part en GET, sa réponse arrive en
XML. Un fichier se télécharge ou s'envoie dans la foulée de la commande qui l'annonce, avec sa taille et son CRC
vérifiés. Le certificat du pare-feu (autorité propre à chaque équipement depuis SNS 5) n'est pas vérifié par une
autorité : son empreinte, confirmée par un administrateur, l'est avant tout envoi.
"""
import base64
import hashlib
import hmac
import http.client
import os
import re
import ssl
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
import zlib

from . import config
from .homeassistant import LinkError
from .i18n import _
from .proxmox import _certificate, _context

APP = "sslclient"
LIMIT = 8 * 1024 * 1024
FILE_LIMIT = 512 * 1024 * 1024
WAIT_DOWNLOAD, WAIT_UPLOAD = "00a01c00", "00a00300"
CHANGED = "Le certificat du pare-feu n'est plus celui qui a été confirmé : vérifiez qu'il s'agit bien de lui, puis confirmez le nouveau."


class SnsError(LinkError):
    pass


def probe_certificate(host, port):
    """(empreinte SHA-256 en hexadécimal majuscule, détails) du certificat présenté ; seule la poignée de main a lieu."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with _context().wrap_socket(raw, server_hostname=host if not re.fullmatch(r"[0-9.:]+", host) else None) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        raise SnsError(_("Le pare-feu ne répond pas en HTTPS à %(host)s:%(port)s (%(e)s).", host=host, port=port,
                         e=(getattr(e, "strerror", None) or str(e))[:120])) from e
    if not der:
        raise SnsError(_("Aucun certificat présenté par %(host)s:%(port)s.", host=host, port=port))
    return hashlib.sha256(der).hexdigest().upper(), _certificate(der)


def crc(data):
    """CRC du pare-feu : celui de zlib sans l'inversion finale."""
    return "%X" % (zlib.crc32(data) ^ 0xFFFFFFFF)


def _reason(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()[:200]


class Response:
    def __init__(self, ret, code, msg, data):
        self.ret, self.code, self.msg, self.data = ret, code, msg, data

    @property
    def ok(self):
        return 100 <= self.ret < 200

    def section(self, title="Result"):
        """Clés d'une section (format « section ») : {nom en minuscules: valeur}."""
        out = {}
        if self.data is None:
            return out
        for sec in self.data.iter("section"):
            if (sec.get("title") or "").lower() == title.lower():
                for key in sec.iter("key"):
                    out[(key.get("name") or "").lower()] = key.get("value") or ""
        return out

    def lines(self, title="Result"):
        """Lignes d'une section (format « section_line ») : [{nom: valeur}]."""
        out = []
        if self.data is None:
            return out
        for sec in self.data.iter("section"):
            if (sec.get("title") or "").lower() == title.lower():
                for line in sec.iter("line"):
                    out.append({(k.get("name") or "").lower(): k.get("value") or "" for k in line.iter("key")})
        return out

    def raw(self):
        return (self.data.text or "") if self.data is not None and self.data.get("format") == "raw" else ""


class Client:
    """`with Client(host, port, empreinte, compte, mot_de_passe) as fw: fw.command("SYSTEM PROPERTY")`."""

    def __init__(self, host, port, fingerprint, user, password, timeout=30):
        self.host, self.port, self.fingerprint = host, int(port), fingerprint
        self.user, self.password, self.timeout = user, password, timeout
        self.cookies, self.session = {}, ""
        self.size = self.crc = None

    # -- HTTPS

    def _request(self, method, path, body=None, headers=None, limit=LIMIT, timeout=None):
        if not self.fingerprint:
            raise SnsError(_("Le certificat du pare-feu n'a pas encore été confirmé."))
        conn = http.client.HTTPSConnection(self.host, self.port, context=_context(), timeout=timeout or self.timeout)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if not der or not hmac.compare_digest(hashlib.sha256(der).hexdigest().upper(), self.fingerprint):
                raise SnsError(_(CHANGED))
            sent = {"User-Agent": config.USER_AGENT, "Accept": "*/*"}
            if self.cookies:
                sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            sent.update(headers or {})
            conn.request(method, path, body=body, headers=sent)
            response = conn.getresponse()
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    pair = value.split(";", 1)[0]
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        self.cookies[k.strip()] = v.strip()
            payload = response.read(limit + 1)
            status = response.status
        except (OSError, ssl.SSLError, http.client.HTTPException) as e:
            raise SnsError(_("Le pare-feu ne répond pas (%(e)s).", e=_reason(getattr(e, "strerror", None) or e)[:120] or type(e).__name__)) from e
        finally:
            conn.close()
        if len(payload) > limit:
            raise SnsError(_("Réponse du pare-feu trop grande."))
        if status != 200:
            raise SnsError(_("Le pare-feu a répondu HTTP %(code)s.", code=status))
        return payload

    @staticmethod
    def _xml(payload):
        try:
            return ET.fromstring(payload)
        except ET.ParseError as e:
            raise SnsError(_("Réponse illisible : ce n'est pas l'API d'un pare-feu Stormshield.")) from e

    # -- session

    def __enter__(self):
        form = urllib.parse.urlencode({"uid": base64.b64encode(self.user.encode()).decode(),
                                       "pswd": base64.b64encode(self.password.encode()).decode(), "app": APP})
        node = self._xml(self._request("POST", "/auth/admin.html", form.encode(), {"Content-Type": "application/x-www-form-urlencoded"}))
        msg = node.get("msg", "")
        if msg == "ERR_BRUTEFORCE":
            raise SnsError(_("Le pare-feu bloque les connexions après trop d'échecs : réessayez dans %(n)s secondes.", n=node.get("delay", "?")))
        if msg == "NEED_TOTP_AUTH":
            raise SnsError(_("Ce compte demande un code à usage unique (TOTP) : utilisez un compte d'administration sans second facteur pour loco."))
        if msg != "AUTH_SUCCESS":
            raise SnsError(_("Le pare-feu refuse le compte ou le mot de passe enregistré."))
        node = self._xml(self._request("POST", "/api/auth/login", urllib.parse.urlencode({"app": APP, "id": 0}).encode(),
                                       {"Content-Type": "application/x-www-form-urlencoded"}))
        if node.get("code") != "100" or node.find("sessionid") is None:
            raise SnsError(_("Session d'administration refusée par le pare-feu (%(m)s).", m=_reason(node.get("msg"))))
        self.session = node.find("sessionid").text or ""
        self.level = (node.findtext("sessionlevel") or "")[:400]
        return self

    def __exit__(self, *_exc):
        if self.session:
            try:
                self._request("GET", "/api/auth/logout?sessionid=" + urllib.parse.quote(self.session), timeout=10)
            except LinkError:
                pass
            self.session = ""

    # -- commandes

    def command(self, cmd, check=True, timeout=None):
        """Lance une commande serverd ; Response. check : SnsError si le pare-feu la refuse."""
        payload = self._request("GET", "/api/command?sessionid=" + urllib.parse.quote(self.session) + "&cmd=" + urllib.parse.quote(cmd),
                                timeout=timeout)
        node = self._xml(payload)
        if node.get("code") != "100":
            raise SnsError(_("Le pare-feu refuse la session (%(m)s).", m=_reason(node.get("msg"))))
        parts = node.findall("serverd")
        if not parts:
            raise SnsError(_("Réponse inattendue du pare-feu."))
        first, last = parts[0], parts[-1]
        try:
            ret = int(last.get("ret", "0"))
        except ValueError:
            ret = 0
        res = Response(ret, last.get("code", ""), last.get("msg", ""), first.find("data"))
        res.first_code = first.get("code", "")
        if res.first_code == WAIT_DOWNLOAD:
            data = first.find("data")
            fmt = ""
            if data is not None and data.get("format") == "section":
                key = data.find("section/key")
                fmt = key.get("value", "") if key is not None else ""
                m = re.search(r"crc=([0-9A-Fa-f]+),size=(\d+)", fmt)
                self.crc, self.size = (m.group(1).upper(), int(m.group(2))) if m else (None, None)
            elif data is not None:
                self.crc = (data.findtext("crc") or "").strip().upper() or None
                size = (data.findtext("size") or "").strip()
                self.size = int(size) if size.isdigit() else None
        if check and not res.ok:
            raise SnsError(_("Le pare-feu refuse la commande « %(c)s » : %(m)s", c=cmd.split(" ", 2)[0] + " " + (cmd.split(" ")[1] if " " in cmd else ""),
                             m=_reason(res.msg) or res.code))
        return res

    def download(self, cmd, timeout=300):
        """Commande qui rend un fichier (« CONFIG BACKUP list=all ») : son contenu, taille et CRC vérifiés."""
        res = self.command(cmd, timeout=timeout)
        if res.first_code != WAIT_DOWNLOAD:
            raise SnsError(_("Le pare-feu n'a pas proposé de fichier à télécharger."))
        data = self._request("GET", "/api/download/tmp.file?sessionid=" + urllib.parse.quote(self.session), limit=FILE_LIMIT, timeout=timeout)
        if self.size is not None and len(data) != self.size:
            raise SnsError(_("Téléchargement incomplet : %(got)s octets reçus sur %(want)s.", got=len(data), want=self.size))
        if self.crc and crc(data) != self.crc:
            raise SnsError(_("Téléchargement altéré : le CRC ne correspond pas."))
        return data

    def upload(self, cmd, path, timeout=1800):
        """Commande qui attend un fichier (« SYSTEM UPDATE UPLOAD ») : le fichier est envoyé, Response de l'envoi."""
        res = self.command(cmd, timeout=timeout)
        if res.first_code != WAIT_UPLOAD:
            raise SnsError(_("Le pare-feu n'attend pas de fichier après « %(c)s ».", c=cmd))
        boundary = "loco" + uuid.uuid4().hex
        name = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"upload\"; filename=\"{name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        payload = self._request("POST", "/api/upload?sessionid=" + urllib.parse.quote(self.session), body,
                                {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=timeout)
        node = self._xml(payload)
        part = node.find("serverd")
        if node.get("code") != "100" or part is None:
            raise SnsError(_("Envoi refusé par le pare-feu (%(m)s).", m=_reason(node.get("msg"))))
        try:
            ret = int(part.get("ret", "0"))
        except ValueError:
            ret = 0
        out = Response(ret, part.get("code", ""), part.get("msg", ""), part.find("data"))
        if not out.ok:
            raise SnsError(_("Envoi refusé par le pare-feu : %(m)s", m=_reason(out.msg) or out.code))
        return out

    def modify(self):
        """Droit d'écriture de la session (« MODIFY ON ») : un autre administrateur qui l'a déjà le garde."""
        res = self.command("MODIFY ON", check=False)
        if not res.ok:
            raise SnsError(_("Un autre administrateur a la main en écriture sur le pare-feu : réessayez quand il aura terminé (%(m)s).",
                             m=_reason(res.msg) or res.code))


# --------------------------------------------------------------------------- relevé

def _pick(values, *names):
    for name in names:
        if values.get(name):
            return values[name]
    return ""


def _number(value):
    m = re.search(r"-?\d+(?:\.\d+)?", value or "")
    return float(m.group(0)) if m else None


def collect(fw):
    """Relevé d'un pare-feu : modèle, numéro de série, version, nom, et ce que dit son état (au mieux)."""
    from .netfamilies import uptime_seconds
    prop = fw.command("SYSTEM PROPERTY").section()
    info = {"os": "SNS", "model": _pick(prop, "model"), "serial": _pick(prop, "serialnumber", "serial"),
            "version": _pick(prop, "version"), "hostname": _pick(prop, "systemname", "name", "hostname", "machinename"),
            "type": _pick(prop, "type"), "hw": _pick(prop, "hwversion"), "cluster": _pick(prop, "clusterstate", "cluster")}
    try:
        res = fw.command("MONITOR SYSTEM", check=False)
        state = res.section() if res.ok else {}
    except SnsError:
        state = {}
    for key, value in state.items():
        if "uptime" in key and "uptime" not in info:
            info["uptime"] = int(value) if value.isdigit() else uptime_seconds(value)
        elif key.startswith("cpu") and "cpu" not in info and _number(value) is not None:
            info["cpu"] = round(_number(value))
        elif key.startswith("mem") and "mem" not in info and _number(value) is not None and _number(value) <= 100:
            info["mem"] = round(_number(value), 1)
        elif "temp" in key and "temperature" not in info and _number(value) is not None:
            info["temperature"] = round(_number(value))
    try:
        res = fw.command("SYSTEM UPDATE STATUS", check=False)
        staged = res.section() if res.ok else {}
    except SnsError:
        staged = {}
    info["staged"] = _pick(staged, "version", "updateversion")
    return {k: (re.sub(r"[\x00-\x1f\x7f]+", " ", v).strip()[:160] if isinstance(v, str) else v) for k, v in info.items()}

