"""Clés d'accès (passkeys, WebAuthn) : connexion d'un compte local sans mot de passe.

Une clé d'accès est une paire de clés gardée par l'appareil de la personne (téléphone, ordinateur, clé de sécurité) et
déverrouillée par son empreinte, son visage ou son code : loco n'en reçoit que la clé publique. La vérification de la
personne par l'appareil est exigée ; une connexion par clé d'accès vaut donc mot de passe et second facteur.

Le navigateur n'accepte une clé d'accès que sur une adresse à nom (jamais une adresse IP), en HTTPS, avec un certificat
qu'il reconnaît : c'est l'adresse du portail (config.portal_url) qui sert d'identité, jamais l'en-tête Host. Aucune
attestation du fabricant n'est demandée : loco ne restreint pas les modèles d'appareils.

Sans dépendance : décodage CBOR et clés COSE lus ici, signatures vérifiées par « cryptography ».
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import struct
import time
import urllib.parse

from flask import abort, flash, g, request, session, url_for
from werkzeug.security import check_password_hash

from . import app, config
from .i18n import _
from .auth import _blocked, _complete_login, _get_user, _record_failure, audit, can_manage_user, lock_access, login_required
from .db import db
from .util import back, safe_next

CHALLENGE_SECONDS = 300
MAX_CHALLENGES = 2000                   # défis en attente gardés ; au-delà, les plus anciens sont oubliés
MAX_KEYS = 10
NAME_MAX = 60
ALGORITHMS = (-7, -8, -257)             # ES256, EdDSA (Ed25519), RS256
UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


class Refused(ValueError):
    """Réponse du navigateur refusée ; le message, technique, va au journal, jamais à l'écran."""


# --------------------------------------------------------------------------- base64url et CBOR (sous-ensemble de WebAuthn)

def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    if not isinstance(text, str) or len(text) > 16384:
        raise Refused("valeur base64url attendue")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as e:
        raise Refused("base64url illisible") from e


def cbor(data, pos=0, depth=0):
    """(valeur, position suivante) de l'élément CBOR qui commence à pos : entiers, octets, texte, tableaux, tables,
    booléens et null, de longueur définie. Tout le reste (étiquettes, flottants, longueurs indéfinies) est refusé."""
    if depth > 8 or pos >= len(data):
        raise Refused("CBOR tronqué ou trop imbriqué")
    major, info = data[pos] >> 5, data[pos] & 0x1F
    pos += 1
    if info < 24:
        value = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise Refused("CBOR tronqué")
        value = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    else:
        raise Refused("CBOR non pris en charge")
    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major in (2, 3):
        if pos + value > len(data):
            raise Refused("CBOR tronqué")
        raw = data[pos:pos + value]
        try:
            return (bytes(raw) if major == 2 else raw.decode("utf-8")), pos + value
        except UnicodeDecodeError as e:
            raise Refused("texte CBOR illisible") from e
    if major == 4:
        if value > 64:
            raise Refused("tableau CBOR trop long")
        items = []
        for _i in range(value):
            item, pos = cbor(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        if value > 64:
            raise Refused("table CBOR trop longue")
        table = {}
        for _i in range(value):
            key, pos = cbor(data, pos, depth + 1)
            if not isinstance(key, (int, str)) or key in table:
                raise Refused("clé CBOR invalide")
            table[key], pos = cbor(data, pos, depth + 1)
        return table, pos
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], pos
    raise Refused("CBOR non pris en charge")


# --------------------------------------------------------------------------- clés publiques (COSE) et signatures

def public_key(cose):
    """(algorithme COSE, clé publique au format DER) d'une clé COSE ; seuls ES256 (P-256), Ed25519 et RS256."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    if not isinstance(cose, dict):
        raise Refused("clé COSE attendue")
    kty, alg = cose.get(1), cose.get(3)
    try:
        if kty == 2 and alg == -7 and cose.get(-1) == 1:
            x, y = cose[-2], cose[-3]
            if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == len(y) == 32):
                raise Refused("point P-256 invalide")
            key = ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
        elif kty == 1 and alg == -8 and cose.get(-1) == 6:
            key = ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])
        elif kty == 3 and alg == -257:
            n, e = cose[-1], cose[-2]
            if not (isinstance(n, bytes) and isinstance(e, bytes) and 256 <= len(n) <= 1024 and len(e) <= 8):
                raise Refused("clé RSA invalide")
            key = rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
        else:
            raise Refused("algorithme de clé non pris en charge")
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, Refused):
            raise
        raise Refused("clé publique invalide") from e
    return alg, key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def signed(alg, der, signature, data):
    """Vrai si la signature de data est bien celle de cette clé publique."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    try:
        key = serialization.load_der_public_key(der)
        if alg == -7:
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        elif alg == -8:
            key.verify(signature, data)
        elif alg == -257:
            key.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- identité du portail

def origin():
    """Origine exacte que le navigateur doit annoncer : celle de l'adresse du portail (schéma, nom, port non standard)."""
    return config.portal_url().rstrip("/")


def relying_party():
    """Nom du portail, identité des clés d'accès ; "" si elles ne sont pas possibles (adresse IP, pas de HTTPS)."""
    parts = urllib.parse.urlsplit(origin())
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
        return ""                                   # WebAuthn refuse une adresse IP comme identité
    except ValueError:
        pass
    if parts.scheme != "https" and host != "localhost":
        return ""
    return host


def here():
    """Le navigateur est-il sur l'adresse du portail ? Ailleurs (adresse IP, autre nom), il refuserait la clé d'accès.
    Host et Origin ne servent qu'à cette comparaison, jamais à bâtir une adresse."""
    try:
        seen = urllib.parse.urlsplit(request.headers.get("Origin") or f"{request.scheme}://{request.host}")
        wanted = urllib.parse.urlsplit(origin())
        default = 443 if wanted.scheme == "https" else 80
        return bool(relying_party()) and (seen.hostname, seen.port or default) == (wanted.hostname, wanted.port or default)
    except ValueError:
        return False


# --------------------------------------------------------------------------- défis à usage unique (gardés par le serveur)

def _issue(purpose, user_id=None):
    conn = db()
    conn.execute("DELETE FROM passkey_challenges WHERE created < ?", (time.time() - CHALLENGE_SECONDS,))
    token, challenge = secrets.token_urlsafe(24), b64(secrets.token_bytes(32))
    conn.execute("INSERT INTO passkey_challenges (id, challenge, purpose, user_id, created) VALUES (?,?,?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), challenge, purpose, user_id, time.time()))
    # La page de connexion en demande sans être connecté : la table reste bornée quoi qu'il arrive.
    conn.execute("DELETE FROM passkey_challenges WHERE id NOT IN (SELECT id FROM passkey_challenges ORDER BY created DESC, rowid DESC LIMIT ?)",
                 (MAX_CHALLENGES,))
    conn.commit()
    session["passkey"] = token
    return challenge


def _take(purpose, user_id=None):
    """Défi en cours pour ce navigateur, retiré dès sa lecture : une réponse ne peut servir qu'une fois."""
    token = session.pop("passkey", None)
    if not token:
        raise Refused("aucun défi en cours")
    conn = db()
    key = hashlib.sha256(str(token).encode()).hexdigest()
    row = conn.execute("SELECT * FROM passkey_challenges WHERE id=?", (key,)).fetchone()
    conn.execute("DELETE FROM passkey_challenges WHERE id=?", (key,))
    conn.commit()
    if row is None or row["purpose"] != purpose or row["user_id"] != user_id or time.time() - row["created"] > CHALLENGE_SECONDS:
        raise Refused("défi inconnu ou expiré")
    return row["challenge"]


# --------------------------------------------------------------------------- vérification des réponses du navigateur

def _client_data(raw, kind, challenge):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Refused("clientDataJSON illisible") from e
    if not isinstance(data, dict) or data.get("type") != kind:
        raise Refused("type de réponse inattendu")
    if not isinstance(data.get("challenge"), str) or not hmac.compare_digest(data["challenge"].rstrip("="), challenge):
        raise Refused("défi différent")
    if data.get("origin") != origin():
        raise Refused(f"origine {str(data.get('origin'))[:80]!r} au lieu de {origin()!r}")
    if data.get("crossOrigin") is True:
        raise Refused("réponse venue d'un cadre d'un autre site")


def _authenticator_data(raw, attested):
    """Données de l'authentificateur : (drapeaux, compteur, identifiant et clé COSE si attested)."""
    if len(raw) < 37:
        raise Refused("données de l'authentificateur tronquées")
    if not hmac.compare_digest(raw[:32], hashlib.sha256(relying_party().encode()).digest()):
        raise Refused("clé d'accès émise pour un autre site")
    flags, count = raw[32], struct.unpack(">I", raw[33:37])[0]
    if not flags & UP or not flags & UV:
        raise Refused("la personne n'a pas été vérifiée par l'appareil")
    if not attested:
        return flags, count, None, None
    if not flags & AT or len(raw) < 55:
        raise Refused("clé publique absente")
    size = struct.unpack(">H", raw[53:55])[0]
    if not 1 <= size <= 1023 or len(raw) < 55 + size:
        raise Refused("identifiant de clé invalide")
    credential = raw[55:55 + size]
    cose, _end = cbor(raw, 55 + size)
    return flags, count, credential, cose


def register(response, challenge):
    """Nouvelle clé d'accès : (identifiant, algorithme, clé publique DER, compteur, synchronisée ?)."""
    if not isinstance(response, dict):
        raise Refused("réponse attendue")
    _client_data(unb64(response.get("clientDataJSON")), "webauthn.create", challenge)
    attestation, _end = cbor(unb64(response.get("attestationObject")))
    if not isinstance(attestation, dict) or not isinstance(attestation.get("authData"), bytes):
        raise Refused("attestation illisible")
    flags, count, credential, cose = _authenticator_data(attestation["authData"], attested=True)
    alg, der = public_key(cose)
    return b64(credential), alg, der, count, bool(flags & BE)


def assertion(response, challenge, row):
    """Connexion avec la clé d'accès row : renvoie le nouveau compteur, ou lève Refused."""
    client = unb64(response.get("clientDataJSON"))
    _client_data(client, "webauthn.get", challenge)
    data = unb64(response.get("authenticatorData"))
    _flags, count, _credential, _cose = _authenticator_data(data, attested=False)
    if not signed(row["alg"], base64.b64decode(row["public_key"]), unb64(response.get("signature")), data + hashlib.sha256(client).digest()):
        raise Refused("signature invalide")
    if (count or row["sign_count"]) and count <= row["sign_count"]:
        raise Refused(f"compteur {count} déjà dépassé ({row['sign_count']}) : clé peut-être copiée")
    return count


# --------------------------------------------------------------------------- comptes

def _handle(user):
    """Identifiant opaque du compte pour les appareils (jamais son nom), créé à la première clé."""
    if user["passkey_handle"]:
        return user["passkey_handle"]
    value = b64(secrets.token_bytes(32))
    db().execute("UPDATE users SET passkey_handle=? WHERE id=? AND passkey_handle=''", (value, user["id"]))
    db().commit()
    return db().execute("SELECT passkey_handle FROM users WHERE id=?", (user["id"],)).fetchone()[0]


def keys_of(user_id, conn=None):
    return (conn or db()).execute("SELECT * FROM passkeys WHERE user_id=? ORDER BY id", (user_id,)).fetchall()


def counts(conn=None):
    """Nombre de clés d'accès par compte (page Comptes)."""
    return {r[0]: r[1] for r in (conn or db()).execute("SELECT user_id, COUNT(*) FROM passkeys GROUP BY user_id")}


def remove_all(conn, user_id):
    """Retire toutes les clés d'accès d'un compte (réinitialisation par un administrateur) ; renvoie leur nombre."""
    return conn.execute("DELETE FROM passkeys WHERE user_id=?", (user_id,)).rowcount


def _answer():
    """Réponse du navigateur (champ « response », JSON) ; 400 si elle n'en est pas une."""
    try:
        value = json.loads(request.form.get("response", ""))
    except ValueError:
        abort(400)
    if not isinstance(value, dict) or not isinstance(value.get("response"), dict):
        abort(400)
    return value


def _fail(message, status=400):
    return {"error": message}, status, {"Cache-Control": "no-store"}


@app.context_processor
def _passkey_pages():
    if request.endpoint == "login":
        # Bouton proposé seulement là où le navigateur acceptera une clé, et si un compte en a une.
        return {"passkey_login": here() and db().execute("SELECT 1 FROM passkeys LIMIT 1").fetchone() is not None}
    if request.endpoint == "account" and g.get("user") is not None:
        return {"passkeys": keys_of(g.user["id"]), "passkey_state": "ok" if here() else "name" if relying_party() else "address",
                "passkey_origin": origin() if relying_party() else "", "passkey_max": MAX_KEYS}
    return {}


# --------------------------------------------------------------------------- connexion

@app.route("/login/passkey/options", methods=["POST"])
def login_passkey_options():
    if g.user is not None or not here():
        abort(404)
    return {"challenge": _issue("login"), "rpId": relying_party(), "timeout": CHALLENGE_SECONDS * 1000,
            "userVerification": "required", "allowCredentials": []}, 200, {"Cache-Control": "no-store"}


@app.route("/login/passkey", methods=["POST"])
def login_passkey():
    if g.user is not None or not here():
        abort(404)
    ip = request.remote_addr or ""
    answer = _answer()
    key = db().execute("SELECT * FROM passkeys WHERE credential_id=?", (str(answer.get("id", ""))[:1400],)).fetchone()
    user = db().execute("SELECT * FROM users WHERE id=?", (key["user_id"],)).fetchone() if key else None
    name = user["username"] if user else f"clé d'accès inconnue ({ip})"
    if _blocked(ip, name):
        audit("connexion.bloquee", f"compte « {name} » (clé d'accès)", username="")
        return _fail(_('Trop de tentatives. Réessayez dans quelques minutes.'), 429)
    try:
        challenge = _take("login")
        if key is None or user is None:
            raise Refused("clé d'accès inconnue de loco")
        if user["auth_source"] != "local" or user["disabled"] or user["pending"]:
            raise Refused("compte désactivé ou géré par un annuaire")
        handle = answer["response"].get("userHandle")
        if handle and (not isinstance(handle, str) or handle.rstrip("=") != user["passkey_handle"]):
            raise Refused("clé d'accès d'un autre compte")
        count = assertion(answer["response"], challenge, key)
    except Refused as e:
        _record_failure(ip, name)
        audit("connexion.echec", f"compte « {name} » (clé d'accès : {e})", username="")
        time.sleep(0.5)
        return _fail(_("Cette clé d'accès n'a pas été acceptée."), 401)
    db().execute("UPDATE passkeys SET sign_count=?, last_used=? WHERE id=?", (count, time.strftime("%Y-%m-%d %H:%M"), key["id"]))
    _complete_login(user, ip, passkey=key["name"] or "clé d'accès")
    target = url_for("account") if user["must_change"] else safe_next(request.form.get("next", "/"))
    return {"redirect": target}, 200, {"Cache-Control": "no-store"}


# --------------------------------------------------------------------------- mon compte

def _own():
    if g.user["auth_source"] != "local" or g.user["must_change"]:
        abort(403)
    if not here():
        abort(409)


@app.route("/account/passkeys/options", methods=["POST"])
@login_required
def account_passkey_options():
    """Avant d'ajouter une clé : le mot de passe est redemandé (une session laissée ouverte ne suffit pas)."""
    _own()
    if not check_password_hash(g.user["password_hash"], request.form.get("password", "")):
        time.sleep(0.5)
        return _fail(_('Mot de passe actuel incorrect.'), 403)
    existing = keys_of(g.user["id"])
    if len(existing) >= MAX_KEYS:
        return _fail(_("Ce compte a déjà %(n)s clés d'accès : retirez-en une d'abord.", n=MAX_KEYS), 409)
    return {"challenge": _issue("register", g.user["id"]), "rp": {"id": relying_party(), "name": "loco"},
            "user": {"id": _handle(g.user), "name": g.user["username"], "displayName": g.user["username"]},
            "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in ALGORITHMS],
            "timeout": CHALLENGE_SECONDS * 1000, "attestation": "none",
            "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True, "userVerification": "required"},
            "excludeCredentials": [{"type": "public-key", "id": k["credential_id"]} for k in existing]}, 200, {"Cache-Control": "no-store"}


@app.route("/account/passkeys/add", methods=["POST"])
@login_required
def account_passkey_add():
    _own()
    answer = _answer()
    name = " ".join(request.form.get("name", "").split())[:NAME_MAX] or _("Clé d'accès")
    try:
        credential, alg, der, count, synced = register(answer["response"], _take("register", g.user["id"]))
    except Refused as e:
        audit("compte.cle.echec", str(e)[:200])
        return _fail(_("Cette clé d'accès n'a pas pu être enregistrée."))
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM passkeys WHERE credential_id=?", (credential,)).fetchone() or len(keys_of(g.user["id"], conn)) >= MAX_KEYS:
        conn.rollback()
        return _fail(_("Cette clé d'accès est déjà enregistrée."), 409)
    transports = answer["response"].get("transports")
    transports = [t for t in transports if isinstance(t, str) and t.isalnum() and len(t) <= 20][:6] if isinstance(transports, list) else []
    conn.execute("INSERT INTO passkeys (user_id, credential_id, alg, public_key, sign_count, name, synced, transports, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (g.user["id"], credential, alg, base64.b64encode(der).decode(), count, name, int(synced),
                                              ",".join(transports), time.strftime("%Y-%m-%d %H:%M")))
    audit("compte.cle.ajout", name, commit=False)
    conn.commit()
    flash(_("Clé d'accès « %(name)s » ajoutée : elle permet de se connecter sans mot de passe.", name=name), "ok")
    return {"redirect": url_for("account") + "#securite"}, 200, {"Cache-Control": "no-store"}


def _mine(key_id):
    row = db().execute("SELECT * FROM passkeys WHERE id=? AND user_id=?", (key_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/account/passkeys/<int:key_id>/rename", methods=["POST"])
@login_required
def account_passkey_rename(key_id):
    row = _mine(key_id)
    name = " ".join(request.form.get("name", "").split())[:NAME_MAX]
    if name and name != row["name"]:
        db().execute("UPDATE passkeys SET name=? WHERE id=?", (name, row["id"]))
        audit("compte.cle.renommage", f"{row['name']} → {name}")
    return back("account", _anchor="securite")


@app.route("/account/passkeys/<int:key_id>/delete", methods=["POST"])
@login_required
def account_passkey_delete(key_id):
    row = _mine(key_id)
    db().execute("DELETE FROM passkeys WHERE id=?", (row["id"],))
    audit("compte.cle.retrait", row["name"])
    flash(_("Clé d'accès « %(name)s » retirée. Pensez à la supprimer aussi de l'appareil.", name=row["name"]), "ok")
    return back("account", _anchor="securite")


# --------------------------------------------------------------------------- comptes (administration)

@app.route("/accounts/<int:user_id>/passkeys-reset", methods=["POST"])
@login_required
def users_passkeys_reset(user_id):
    conn = lock_access("users.reset")
    row = _get_user(user_id)
    if not can_manage_user(row):
        abort(403)
    removed = remove_all(conn, row["id"])
    audit("comptes.cles.reinitialisation", f"{row['username']} : {removed}", commit=False)
    conn.commit()
    flash(_("Clés d'accès retirées pour %(user)s.", user=row["username"]), "ok")
    return back("users")
