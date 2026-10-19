"""Clients LDAP v3 (liaison simple sur TLS) et RADIUS (PAP, RFC 2865/3579), sans dépendance externe.

Seul ce dont loco a besoin pour vérifier un identifiant et un mot de passe est implémenté : liaison, recherche
d'un compte (filtre RFC 4515 : &, |, !, égalité, présence), StartTLS ; demande d'accès RADIUS signée.
"""
import hashlib
import hmac
import os
import re
import socket
import ssl
import struct
import urllib.parse

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
TIMEOUT = 10


class DirectoryError(Exception):
    """Échec technique (serveur injoignable, réponse invalide…), distinct d'un mot de passe refusé."""


# --------------------------------------------------------------------------- BER (sous-ensemble LDAP)

def _length(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag, content):
    return bytes([tag]) + _length(len(content)) + content


def integer(value, tag=0x02):
    body = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True)
    return tlv(tag, body)


def octets(value, tag=0x04):
    return tlv(tag, value.encode() if isinstance(value, str) else value)


def sequence(*items, tag=0x30):
    return tlv(tag, b"".join(items))


def parse(data):
    """[(tag, contenu)] des éléments BER consécutifs de data."""
    out, i = [], 0
    while i < len(data):
        if i + 2 > len(data):
            raise DirectoryError("réponse LDAP tronquée")
        tag, first = data[i], data[i + 1]
        i += 2
        if first & 0x80:
            count = first & 0x7f
            if count == 0 or count > 4 or i + count > len(data):
                raise DirectoryError("longueur LDAP invalide")
            size = int.from_bytes(data[i:i + count], "big")
            i += count
        else:
            size = first
        if i + size > len(data):
            raise DirectoryError("réponse LDAP tronquée")
        out.append((tag, data[i:i + size]))
        i += size
    return out


def _read_message(sock):
    """Un LDAPMessage complet depuis la connexion."""
    def exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise DirectoryError("connexion LDAP fermée par le serveur")
            buf += chunk
        return buf
    head = exact(2)
    if head[1] & 0x80:
        count = head[1] & 0x7f
        if count == 0 or count > 4:
            raise DirectoryError("longueur LDAP invalide")
        extra = exact(count)
        size = int.from_bytes(extra, "big")
        head += extra
    else:
        size = head[1]
    if size > 4 * 1024 * 1024:
        raise DirectoryError("réponse LDAP trop volumineuse")
    return parse(head + exact(size))[0]


# --------------------------------------------------------------------------- filtres RFC 4515

def escape_filter(value):
    return "".join("\\%02x" % ord(c) if c in "*()\\\x00" else c for c in value)


def _unescape(value):
    return re.sub(rb"\\([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), value.encode())


def encode_filter(text):
    """Filtre texte -> BER. Gère (&…), (|…), (!…), (attr=valeur) et (attr=*)."""
    def node(i):
        if i >= len(text) or text[i] != "(":
            raise ValueError("filtre LDAP : « ( » attendue")
        op = text[i + 1:i + 2]
        if op in ("&", "|"):
            children, j = [], i + 2
            while j < len(text) and text[j] == "(":
                child, j = node(j)
                children.append(child)
            if j >= len(text) or text[j] != ")" or not children:
                raise ValueError("filtre LDAP mal formé")
            return sequence(*children, tag=0xa0 if op == "&" else 0xa1), j + 1
        if op == "!":
            child, j = node(i + 2)
            if j >= len(text) or text[j] != ")":
                raise ValueError("filtre LDAP mal formé")
            return tlv(0xa2, child), j + 1
        end = text.find(")", i)
        if end < 0:
            raise ValueError("filtre LDAP : « ) » manquante")
        item = text[i + 1:end]
        attr, sep, value = item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", attr) or attr.endswith(("~", ">", "<", ":")):
            raise ValueError("filtre LDAP : seules l'égalité et la présence sont prises en charge")
        if value == "*":
            return octets(attr, tag=0x87), end + 1
        if "*" in value:
            raise ValueError("filtre LDAP : jokers non pris en charge")
        return sequence(octets(attr), octets(_unescape(value)), tag=0xa3), end + 1
    encoded, end = node(0)
    if end != len(text):
        raise ValueError("filtre LDAP : texte après la fin du filtre")
    return encoded


# --------------------------------------------------------------------------- LDAP

class LdapConnection:
    def __init__(self, url, starttls=False, ca_pem="", timeout=TIMEOUT):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("ldap", "ldaps") or not parts.hostname:
            raise ValueError("adresse ldaps://hôte ou ldap://hôte attendue")
        if parts.scheme == "ldap" and not starttls:
            raise ValueError("le mot de passe ne circule jamais en clair : ldaps:// ou StartTLS requis")
        self.host, self.msgid = parts.hostname, 0
        context = ssl.create_default_context(cadata=ca_pem or None)
        try:
            raw = socket.create_connection((parts.hostname, parts.port or (636 if parts.scheme == "ldaps" else 389)), timeout=timeout)
        except OSError as e:
            raise DirectoryError(f"annuaire injoignable : {e}") from None
        self.sock = raw
        try:
            if parts.scheme == "ldap":
                op, result = self.request(sequence(octets("1.3.6.1.4.1.1466.20037", tag=0x80), tag=0x77))[0]
                if self._code(result) != 0:
                    raise DirectoryError("StartTLS refusé par l'annuaire")
            self.sock = context.wrap_socket(raw, server_hostname=parts.hostname)
        except (OSError, ssl.SSLError) as e:
            raw.close()
            raise DirectoryError(f"connexion TLS à l'annuaire impossible : {e}") from None

    def close(self):
        try:
            self.msgid += 1
            self.sock.sendall(sequence(integer(self.msgid), tlv(0x42, b"")))
        except OSError:
            pass
        self.sock.close()

    def request(self, op, until=None):
        """Envoie une opération ; renvoie [(tag, contenu)] des réponses jusqu'à la réponse finale."""
        self.msgid += 1
        try:
            self.sock.sendall(sequence(integer(self.msgid), op))
            answers = []
            while True:
                tag, content = _read_message(self.sock)
                parts = parse(content)
                if len(parts) < 2 or parts[0][0] != 0x02 or int.from_bytes(parts[0][1], "big") != self.msgid:
                    continue                                            # notification sans rapport
                answers.append(parts[1])
                if until is None or parts[1][0] == until:
                    return answers
        except socket.timeout:
            raise DirectoryError("l'annuaire ne répond pas") from None
        except OSError as e:
            raise DirectoryError(f"échange avec l'annuaire interrompu : {e}") from None

    @staticmethod
    def _code(content):
        fields = parse(content)
        if not fields or fields[0][0] != 0x0a:
            raise DirectoryError("réponse LDAP invalide")
        return int.from_bytes(fields[0][1], "big")

    def bind(self, dn, password):
        """Vrai si l'annuaire accepte ce DN et ce mot de passe (jamais de liaison anonyme)."""
        if not password:
            return False
        op = sequence(integer(3), octets(dn), octets(password, tag=0x80), tag=0x60)
        tag, content = self.request(op)[0]
        if tag != 0x61:
            raise DirectoryError("réponse de liaison LDAP inattendue")
        code = self._code(content)
        if code == 0:
            return True
        if code in (49, 50, 53):                                       # identifiants, droits, compte refusé
            return False
        raise DirectoryError(f"liaison LDAP refusée (code {code})")

    def search(self, base, filter_text, attributes):
        op = sequence(octets(base), integer(2, tag=0x0a), integer(0, tag=0x0a), integer(2), integer(10),
                      tlv(0x01, b"\x00"), encode_filter(filter_text), sequence(*(octets(a) for a in attributes)), tag=0x63)
        entries = []
        for tag, content in self.request(op, until=0x65):
            if tag == 0x64:
                fields = parse(content)
                values = {}
                for _t, attr in parse(fields[1][1]) if len(fields) > 1 else []:
                    name, vals = parse(attr)
                    values[name[1].decode(errors="replace").lower()] = [v.decode(errors="replace") for _t2, v in parse(vals[1])]
                entries.append((fields[0][1].decode(errors="replace"), values))
            elif tag == 0x65 and self._code(content) not in (0, 4):     # 4 : limite de taille atteinte
                raise DirectoryError(f"recherche LDAP refusée (code {self._code(content)})")
        return entries


def ldap_authenticate(cfg, username, password):
    """(accepté, {display_name, email}) d'après la configuration d'un annuaire LDAP."""
    if not USERNAME_RE.match(username) or not password:
        return False, {}
    conn = LdapConnection(cfg["url"], cfg.get("starttls", False), cfg.get("ca_pem", ""))
    try:
        attrs = [cfg.get("name_attr") or "displayName", cfg.get("mail_attr") or "mail"]
        query = (cfg.get("user_filter") or "(uid={username})").replace("{username}", escape_filter(username))
        if cfg.get("bind_dn"):
            if not conn.bind(cfg["bind_dn"], cfg.get("bind_password", "")):
                raise DirectoryError("le compte de service de l'annuaire est refusé")
            entries = conn.search(cfg["base_dn"], query, attrs)
            if len(entries) != 1:
                return False, {}                                       # inconnu ou ambigu : jamais deviné
            dn, values = entries[0]
            if not conn.bind(dn, password):
                return False, {}
        else:
            dn = (cfg.get("user_template") or "{username}").replace("{username}", username)
            if not conn.bind(dn, password):
                return False, {}
            entries = conn.search(cfg["base_dn"], query, attrs) if cfg.get("base_dn") else []
            values = entries[0][1] if len(entries) == 1 else {}
        def first(key):
            return (values.get(key.lower()) or [""])[0][:200]
        return True, {"display_name": first(attrs[0]), "email": first(attrs[1])}
    finally:
        conn.close()


# --------------------------------------------------------------------------- RADIUS

ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE = 1, 2, 3, 11
MESSAGE_AUTHENTICATOR = 80


def _attribute(kind, value):
    if len(value) > 253:
        raise ValueError("attribut RADIUS trop long")
    return bytes([kind, len(value) + 2]) + value


def _hide_password(password, secret, authenticator):
    data = password.encode()
    data += b"\x00" * (-len(data) % 16 or (16 if not data else 0))
    out, previous = b"", authenticator
    for i in range(0, len(data), 16):
        key = hashlib.md5(secret + previous).digest()
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], key))
        out += block
        previous = block
    return out


def _attributes(data):
    out, i = [], 0
    while i + 2 <= len(data):
        kind, size = data[i], data[i + 1]
        if size < 2 or i + size > len(data):
            raise DirectoryError("réponse RADIUS mal formée")
        out.append((kind, data[i + 2:i + size], i))
        i += size
    return out


def radius_packet(username, password, secret, nas="loco", ident=None, authenticator=None):
    """(paquet Access-Request signé, identifiant, authentificateur)."""
    ident = os.urandom(1)[0] if ident is None else ident
    authenticator = authenticator or os.urandom(16)
    attrs = (_attribute(1, username.encode()) + _attribute(2, _hide_password(password, secret, authenticator))
             + _attribute(32, nas.encode()[:64]) + _attribute(MESSAGE_AUTHENTICATOR, b"\x00" * 16))
    header = struct.pack("!BBH", ACCESS_REQUEST, ident, 20 + len(attrs)) + authenticator
    packet = header + attrs
    mac = hmac.new(secret, packet, hashlib.md5).digest()
    return packet[:-16] + mac, ident, authenticator


def radius_check_reply(reply, secret, ident, authenticator, require_mac=True):
    """Code de la réponse (Accept, Reject, Challenge) une fois son authenticité vérifiée."""
    if len(reply) < 20:
        raise DirectoryError("réponse RADIUS trop courte")
    code, rid, length = struct.unpack("!BBH", reply[:4])
    if rid != ident or length != len(reply) or code not in (ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE):
        raise DirectoryError("réponse RADIUS inattendue")
    attrs = reply[20:length]
    expected = hashlib.md5(reply[:4] + authenticator + attrs + secret).digest()
    if not hmac.compare_digest(expected, reply[4:20]):
        raise DirectoryError("réponse RADIUS non authentique (secret partagé différent ?)")
    macs = [(value, offset) for kind, value, offset in _attributes(attrs) if kind == MESSAGE_AUTHENTICATOR]
    if macs:
        value, offset = macs[0]
        start = 20 + offset + 2
        blank = reply[:4] + authenticator + reply[20:start] + b"\x00" * 16 + reply[start + 16:]
        if len(value) != 16 or not hmac.compare_digest(hmac.new(secret, blank, hashlib.md5).digest(), value):
            raise DirectoryError("Message-Authenticator RADIUS invalide")
    elif require_mac and code != ACCESS_REJECT:
        raise DirectoryError("réponse RADIUS sans Message-Authenticator (exigé contre BlastRADIUS)")
    return code


def radius_authenticate(cfg, username, password):
    """(accepté, {}) d'après la configuration d'un serveur RADIUS ; une seule demande par essai réseau."""
    if not USERNAME_RE.match(username) or not password or len(password) > 128:
        return False, {}
    secret = cfg["secret"].encode()
    packet, ident, authenticator = radius_packet(username, password, secret, cfg.get("nas_identifier") or "loco")
    host, port = cfg["host"], int(cfg.get("port") or 1812)
    try:
        address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
    except OSError as e:
        raise DirectoryError(f"serveur RADIUS introuvable : {e}") from None
    with socket.socket(address[0], socket.SOCK_DGRAM) as sock:
        sock.settimeout(int(cfg.get("timeout") or 5))
        for _attempt in range(1 + int(cfg.get("retries") or 1)):
            try:
                sock.sendto(packet, address[4])
                while True:
                    reply, origin = sock.recvfrom(4096)
                    if origin[0] == address[4][0] and len(reply) >= 2 and reply[1] == ident:
                        break
            except socket.timeout:
                continue
            except OSError as e:
                raise DirectoryError(f"serveur RADIUS injoignable : {e}") from None
            code = radius_check_reply(reply, secret, ident, authenticator, cfg.get("require_message_authenticator", True))
            return code == ACCESS_ACCEPT, {}
    raise DirectoryError("le serveur RADIUS ne répond pas (adresse, port, pare-feu ou secret partagé à vérifier)")
