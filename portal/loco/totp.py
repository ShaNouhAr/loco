"""Double authentification TOTP (RFC 6238) et codes de secours, sans dépendance obligatoire.

Le secret est chiffré au repos (comme les autres secrets de loco). Le QR d'enrôlement est rendu en SVG côté serveur
via la bibliothèque « qrcode » si elle est présente ; sinon la clé à saisir à la main reste proposée.
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
import urllib.parse

from werkzeug.security import check_password_hash, generate_password_hash

from . import app

DIGITS = 6
PERIOD = 30
RECOVERY_COUNT = 10


def _cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-mfa-v1", hashlib.sha256).digest()))


def seal(secret):
    return _cipher().encrypt(secret.encode()).decode() if secret else ""


def unseal(value):
    try:
        return _cipher().decrypt(value.encode()).decode() if value else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- TOTP

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret, code, window=1, now=None):
    """Vrai si le code correspond au secret, en tolérant une fenêtre avant/après (décalage d'horloge)."""
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit() or len(code) != DIGITS:
        return False
    counter = int((now if now is not None else time.time()) // PERIOD)
    return any(hmac.compare_digest(_code(secret, counter + delta), code) for delta in range(-window, window + 1))


def uri(secret, username, issuer="loco"):
    label = urllib.parse.quote(f"{issuer}:{username}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                    "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{query}"


def qr_svg(data, scale=5):
    """QR en SVG (chaîne). Utilise python3-qrcode si présent, sinon le cœur vendorisé (toujours disponible)."""
    try:
        import qrcode
    except ImportError:
        from ._vendor import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    rects = []
    for y, line in enumerate(matrix):
        for x, dark in enumerate(line):
            if dark:
                rects.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    size = n * scale
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {n} {n}" '
            f'shape-rendering="crispEdges" role="img"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<g fill="#000">{"".join(rects)}</g></svg>')


# --------------------------------------------------------------------------- codes de secours

def _normalize(code):
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def new_recovery_codes(count=RECOVERY_COUNT):
    """Codes à usage unique, lisibles ; renvoie (liste en clair à montrer, liste de hachés à stocker)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"       # sans caractères ambigus
    plain = ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2)) for _ in range(count)]
    hashed = [generate_password_hash(_normalize(code)) for code in plain]
    return plain, json.dumps(hashed)


def use_recovery_code(stored, code):
    """Si le code correspond à un haché non utilisé, le consomme ; renvoie (ok, nouveau JSON de hachés restants)."""
    try:
        hashes = json.loads(stored) if stored else []
    except (TypeError, ValueError):
        hashes = []
    target = _normalize(code)
    if not target:
        return False, stored
    for i, h in enumerate(hashes):
        if check_password_hash(h, target):
            del hashes[i]
            return True, json.dumps(hashes)
    return False, stored


def recovery_left(stored):
    try:
        return len(json.loads(stored)) if stored else 0
    except (TypeError, ValueError):
        return 0
