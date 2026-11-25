"""Partages de fichiers (Samba) servis par le serveur loco lui-même : fonctionnalité facultative.

Le portail décrit les partages dans sa base (partition, dossier, taille limite) et les utilisateurs Samba — liés à un
compte loco, ou seuls — avec leurs droits ; l'agent root les applique et publie l'état (portal/locosamba.py). Tout se
règle sur la page des partages, mots de passe compris : ils vont à Samba, loco ne les garde pas.
"""
import hashlib
import json
import re
import threading
import time
import unicodedata
import urllib.parse

from flask import abort, flash, g, render_template, request, url_for

import locosamba
from . import app, config
from .auth import audit, can, login_required, password_problem, require
from .db import connect, db, lease
from .features import samba_possible
from .i18n import _
from .util import back

GIB = 1 << 30
STALE = 60                      # état de l'agent plus ancien : l'agent ne tourne plus
NEAR_FULL = 90                  # jauge orange à partir de 90 % de la taille limite
CUT_WAIT = 8.0                  # attente de la coupure d'une connexion par l'agent (secondes)
RESERVED_LOGINS = {"root", "nobody", "guest", "loco", "loco-share"}


def unix_name(account_id):
    return f"lsmb{int(account_id)}"


def agent_state():
    """État publié par l'agent root ; « stale » si l'agent ne le rafraîchit plus."""
    st = locosamba.status()
    st["stale"] = time.time() - float(st.get("at") or 0) > STALE
    return st


# --------------------------------------------------------------------------- base

def accounts(conn=None):
    """Utilisateurs Samba : liés à un compte loco (son identifiant ; désactivé ou supprimé avec lui) ou seuls."""
    rows = [dict(r) for r in (conn or db()).execute(
        "SELECT a.*, u.username AS loco, u.disabled AS loco_disabled, u.pending AS loco_pending FROM samba_accounts a "
        "LEFT JOIN users u ON u.id=a.user_id ORDER BY a.login COLLATE NOCASE")]
    for row in rows:
        row["unix"], row["linked"] = unix_name(row["id"]), row["user_id"] is not None
        row["off"] = bool(row["disabled"] or (row["linked"] and (row["loco_disabled"] or row["loco_pending"])))
    return rows


def free_loco_accounts(conn=None):
    """Comptes loco approuvés pas encore utilisateurs Samba : ceux qu'on peut lier."""
    return [dict(r) for r in (conn or db()).execute(
        "SELECT id, username FROM users WHERE pending=0 AND disabled=0 AND id NOT IN "
        "(SELECT user_id FROM samba_accounts WHERE user_id IS NOT NULL) ORDER BY username")]


def share_rows(conn=None):
    conn = conn or db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM samba_shares ORDER BY name COLLATE NOCASE")]
    access = {}
    for r in conn.execute("SELECT x.share_id, x.account_id, x.can_write, a.login, a.user_id FROM samba_access x "
                          "JOIN samba_accounts a ON a.id=x.account_id ORDER BY a.login COLLATE NOCASE"):
        access.setdefault(r["share_id"], []).append(dict(r))
    for row in rows:
        row["access"] = access.get(row["id"], [])
        row["path"] = locosamba.base_dir(row["mount"]) + "/" + row["folder"]
    return rows


def spec(conn=None):
    """Description complète remise à l'agent : utilisateurs (compte Unix ↔ identifiant, désactivé ou non) et partages."""
    conn = conn or db()
    users = [{"unix": a["unix"], "login": a["login"], "disabled": a["off"]} for a in accounts(conn)]
    return {"users": users,
            "shares": [{"name": r["name"], "mount": r["mount"], "folder": r["folder"], "limit": r["size_limit"],
                        "comment": r["comment"],
                        "read": [unix_name(a["account_id"]) for a in r["access"] if not a["can_write"]],
                        "write": [unix_name(a["account_id"]) for a in r["access"] if a["can_write"]]}
                       for r in share_rows(conn)]}


def push(conn=None):
    """Remet la description à l'agent root. Faux s'il ne peut pas la recevoir : elle reste dans la base et repart
    d'elle-même (page ouverte, boucle de fond)."""
    try:
        locosamba.request("apply", spec=spec(conn), enabled=True)
        return True
    except ValueError:
        return False


def sync(st):
    """Description de la base pas encore appliquée (agent relancé, demande perdue, fonctionnalité réactivée) : renvoyée."""
    if not st.get("stale") and st.get("installed") and st.get("managed") \
            and (not st.get("enabled") or st.get("applied") != locosamba.digest(spec())):
        push()


def background():
    """Un passage de la boucle de fond : la description de la base est renvoyée à l'agent s'il ne l'a pas (agent
    relancé, demande perdue) — un compte supprimé ou désactivé perd ainsi son accès sans que personne n'ouvre la page."""
    from . import features, ha
    if ha.skip_background() or not lease("samba"):
        return False
    conn = connect()
    try:
        if "samba" in features.disabled(conn):
            return False
        st = agent_state()
        if st.get("stale") or not st.get("installed") or not st.get("managed"):
            return False
        current = spec(conn)
        if st.get("enabled") and st.get("applied") == locosamba.digest(current):
            return False
        locosamba.request("apply", spec=current, enabled=True)
        return True
    except ValueError:
        return False
    finally:
        conn.close()


_started = False


def start():
    global _started
    if _started or not samba_possible():
        return
    _started = True

    def loop():
        time.sleep(30)
        while True:
            try:
                background()
            except Exception:                       # un fil de fond ne doit jamais faire tomber le serveur
                pass
            time.sleep(60)
    threading.Thread(target=loop, name="loco-partages", daemon=True).start()


def switched(on):
    """Fonctionnalité activée (description remise, Samba relancé) ou coupée (Samba arrêté, fichiers gardés)."""
    try:
        if on:
            locosamba.request("apply", spec=spec(), enabled=True)
        else:
            locosamba.request("stop")
    except ValueError:
        pass


# --------------------------------------------------------------------------- vues et graphiques

def _used(st, name):
    return int(((st.get("shares") or {}).get(name) or {}).get("used") or 0)


def _level(pct):
    return "err" if pct is not None and pct >= 100 else "warn" if pct is not None and pct >= NEAR_FULL else "ok"


def partition_views(st, rows):
    """Partitions et les segments de leur graphique : système et autres données, fichiers des partages, place réservée
    aux partages (taille limite pas encore occupée), place libre."""
    views = []
    for p in st.get("partitions") or []:
        on = [r for r in rows if r["mount"] == p["mount"]]
        used = sum(_used(st, r["name"]) for r in on)
        reserved = sum(max(0, r["size_limit"] - _used(st, r["name"])) for r in on)
        size, free = max(int(p.get("size") or 0), 1), max(int(p.get("free") or 0), 0)
        held = min(reserved, free)
        views.append(dict(p, shares=on, shares_used=used, reserved=reserved, over=reserved > free,
                          available=max(0, free - reserved), pct=round(100 * (size - free) / size),
                          segments=[("other", max(0, size - free - used)), ("used", used), ("reserved", held),
                                    ("free", free - held)]))
    return views


def share_views(st, rows):
    """Partages avec leur occupation (anneau), leur état et les connexions en cours."""
    published, sessions = st.get("shares") or {}, st.get("sessions") or []
    views = []
    for r in rows:
        s = published.get(r["name"]) or {}
        used = s.get("used")
        pct = min(100, round(100 * used / r["size_limit"])) if used is not None and r["size_limit"] else None
        error = s.get("error") or ""
        views.append(dict(r, used=used, pct=pct, full=bool(s.get("full")), measured=s.get("measured"),
                          rights={a["account_id"]: "write" if a["can_write"] else "read" for a in r["access"]},
                          error=_(error, path=r["path"], mount=r["mount"]) if error else "",
                          color="err" if s.get("full") else _level(pct),
                          sessions=[x for x in sessions if x.get("share") == r["name"]], applied=r["name"] in published))
    return views


def account_views(st, rows, users):
    """Utilisateurs avec leurs partages (droit par partage), l'état de leur mot de passe dans Samba et leur connexion."""
    passwords = set(st.get("passwords") or [])
    connected = {x.get("user") for x in st.get("sessions") or []}
    for a in users:
        mine = [(r, x) for r in rows for x in r["access"] if x["account_id"] == a["id"]]
        a["shares"] = [(r["name"], "write" if x["can_write"] else "read") for r, x in mine]
        a["rights"] = {r["id"]: "write" if x["can_write"] else "read" for r, x in mine}
        a["in_samba"] = a["unix"] in passwords
        a["connected"] = a["login"] in connected
    return users


def connection_views(st, users):
    """Connexions en cours, une par connexion de poste (le processus smbd qui la sert) : qui, d'où, quels partages
    ouverts, depuis quand ; et l'utilisateur Samba qu'on peut désactiver en la coupant, s'il est seul dessus."""
    by_login, found = {a["login"]: a for a in users}, {}
    for x in st.get("sessions") or []:
        pid = str(x.get("pid") or "")
        key = pid if pid.isdigit() else f"{x.get('user')}@{x.get('machine')}"
        c = found.setdefault(key, {"pid": pid if pid.isdigit() else "", "uid": str(x.get("uid") or ""), "users": [],
                                   "machine": str(x.get("machine") or ""), "shares": [], "since": ""})
        for field, value in (("users", x.get("user")), ("shares", x.get("share"))):
            if value and value not in c[field]:
                c[field].append(value)
        since = str(x.get("since") or "")
        if since and (not c["since"] or since < c["since"]):
            c["since"] = since
    for c in found.values():
        account = by_login.get(c["users"][0]) if len(c["users"]) == 1 else None
        c["account"] = account if account and not account["off"] else None
    return sorted(found.values(), key=lambda c: ([u.casefold() for u in c["users"]], c["machine"], c["pid"]))


def hosts():
    """Noms sous lesquels les postes joignent le serveur (adresse du portail, puis celle des agents) : jamais l'en-tête
    Host de la requête."""
    names = []
    for url in (config.portal_url(), config.HTTPS_URL):
        host = urllib.parse.urlsplit(url).hostname or ""
        if host and host not in ("localhost", "127.0.0.1", "::1") and host not in names:
            names.append(host)
    return names


def full_shares(st=None):
    """Partages pleins (passés en lecture seule) : pour la vue d'ensemble et les alertes."""
    st = st if st is not None else agent_state()
    return sorted(name for name, s in (st.get("shares") or {}).items() if s.get("full"))


def attention():
    """Pour « À traiter » de la vue d'ensemble : partage plein (en lecture seule), Samba arrêté, agent muet."""
    st, rows = agent_state(), []
    if not st.get("installed") and not st.get("stale"):
        return rows
    url = url_for("shares")
    if st.get("stale"):
        rows.append({"tone": "warn", "url": url, "text": _("Partages de fichiers : l'agent d'administration (loco-agent) ne répond pas.")})
        return rows
    has_shares = db().execute("SELECT 1 FROM samba_shares LIMIT 1").fetchone()
    if has_shares and st.get("enabled") and st.get("managed") and not st.get("running"):
        rows.append({"tone": "err", "url": url, "text": _("Partages de fichiers : Samba est arrêté, les partages ne sont plus servis.")})
    for name in full_shares(st):
        rows.append({"tone": "err", "url": url, "text": _("Partage « %(name)s » plein : il est passé en lecture seule.", name=name)})
    for name, s in sorted((st.get("shares") or {}).items()):
        if s.get("error"):
            rows.append({"tone": "warn", "url": url, "text": _("Partage « %(name)s » en erreur.", name=name)})
    return rows


def _signature(st, rows, users):
    shares = st.get("shares") or {}
    return hashlib.sha256(json.dumps([
        [st.get(k) for k in ("installed", "running", "managed", "enabled", "busy", "error", "version", "stale")],
        [[r["id"], r["name"], r["size_limit"], [(a["account_id"], a["can_write"]) for a in r["access"]],
          (shares.get(r["name"]) or {}).get("used"), (shares.get(r["name"]) or {}).get("full")] for r in rows],
        [(a["id"], a["login"], a["off"], a["password_at"]) for a in users], sorted(st.get("passwords") or []),
        [(s.get("user"), s.get("share"), s.get("machine"), s.get("pid")) for s in st.get("sessions") or []],
        [(p.get("mount"), p.get("free")) for p in st.get("partitions") or []]], default=str).encode()).hexdigest()[:16]


def folder_for(name):
    """Dossier proposé d'après le nom du partage (le même que la page affiche) : sans accent, en minuscules."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:63]


def free_folder(mount, base):
    """Dossier du partage sur sa partition, d'après son nom : « documents », ou « documents-2 » s'il est déjà pris."""
    taken = {r[0] for r in db().execute("SELECT folder FROM samba_shares WHERE mount=?", (mount,))}
    folder, n = base, 2
    while folder in taken:
        folder, n = f"{base[:60]}-{n}", n + 1
    return folder


def _fail(message, anchor=None):
    flash(message, "error")
    return back("shares", **({"_anchor": anchor} if anchor else {}))


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- page

@app.route("/shares")
@login_required
def shares():
    from . import sharefiles
    st = agent_state()
    opens = sharefiles.openable(st)
    if not can("samba.view"):
        # Seulement le droit d'ouvrir des partages dans le navigateur : ceux du compte, sans rien de leur gestion.
        mine = [s for s in share_views(st, share_rows()) if s["id"] in opens]
        return render_template("share_list.html", shares=mine, opens=opens, hosts=hosts())
    if can("samba.manage"):
        sync(st)
    rows, users = share_rows(), accounts()
    sig = _signature(st, rows, users)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": sig}
    users = account_views(st, rows, users)
    return render_template("samba.html", st=st, partitions=partition_views(st, rows), shares=share_views(st, rows),
                           users=users, connections=connection_views(st, users), free_loco=free_loco_accounts(),
                           hosts=hosts(), signature=sig, gib=GIB, available=samba_possible(),
                           min_password=config.MIN_PASSWORD, opens=opens)


# --------------------------------------------------------------------------- partages

def _share_form(row=None):
    """Champs du formulaire d'un partage, vérifiés ; (valeurs, None) ou (None, message d'erreur)."""
    st = agent_state()
    name = request.form.get("name", "").strip()
    if not locosamba.NAME_RE.fullmatch(name) or name.casefold() in locosamba.RESERVED:
        return None, _("Nom de partage invalide : lettres, chiffres, point, tiret ou trait bas, 32 caractères au plus.")
    clash = db().execute("SELECT id FROM samba_shares WHERE name=? COLLATE NOCASE AND id!=?",
                         (name, row["id"] if row else 0)).fetchone()
    if clash:
        return None, _("Un partage porte déjà ce nom.")
    mount = row["mount"] if row else request.form.get("mount", "")
    part = next((p for p in st.get("partitions") or [] if p["mount"] == mount), None)
    if not row and not part:
        return None, _("Choisissez une partition proposée par le serveur.")
    folder = row["folder"] if row else free_folder(mount, folder_for(name) or "partage")
    try:
        size = float(request.form.get("size", "").replace(",", "."))
    except ValueError:
        size = 0
    limit = int(size * GIB)
    if limit < locosamba.MIN_LIMIT:
        return None, _("La taille limite doit être d'au moins 1 Go.")
    if part and limit > int(part.get("size") or 0):
        return None, _("La taille limite dépasse la taille de la partition.")
    rights = {}
    for account in accounts():
        choice = request.form.get(f"access-{account['id']}", "")
        if choice in ("read", "write"):
            rights[account["id"]] = choice == "write"
    return {"name": name, "mount": mount, "folder": folder, "limit": limit,
            "comment": request.form.get("comment", "").strip()[:120], "rights": rights}, None


def _save_share_rights(conn, share_id, rights):
    conn.execute("DELETE FROM samba_access WHERE share_id=?", (share_id,))
    conn.executemany("INSERT INTO samba_access (share_id, account_id, can_write) VALUES (?, ?, ?)",
                     [(share_id, aid, int(write)) for aid, write in rights.items()])


@app.route("/shares/add", methods=["POST"])
@login_required
def shares_add():
    require("samba.manage")
    values, error = _share_form()
    if error:
        return _fail(error)
    conn = db()
    cur = conn.execute("INSERT INTO samba_shares (name, mount, folder, size_limit, comment, created_at, created_by) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (values["name"], values["mount"], values["folder"], values["limit"],
                                                        values["comment"], _stamp(), g.user["username"]))
    _save_share_rights(conn, cur.lastrowid, values["rights"])
    audit("partages.ajout", f"{values['name']} ({locosamba.base_dir(values['mount'])}/{values['folder']}, "
                            f"{values['limit'] // GIB} Go, {len(values['rights'])} utilisateur(s))", commit=False)
    conn.commit()
    flash(_("Partage %(name)s créé.", name=values["name"]) if push(conn)
          else _("Partage %(name)s enregistré : l'agent d'administration (loco-agent) l'appliquera à son retour.",
                 name=values["name"]), "ok")
    return back("shares")


@app.route("/shares/<int:share_id>", methods=["POST"])
@login_required
def shares_edit(share_id):
    require("samba.manage")
    row = db().execute("SELECT * FROM samba_shares WHERE id=?", (share_id,)).fetchone() or abort(404)
    values, error = _share_form(row)
    if error:
        return _fail(error)
    conn = db()
    conn.execute("UPDATE samba_shares SET name=?, size_limit=?, comment=? WHERE id=?",
                 (values["name"], values["limit"], values["comment"], share_id))
    _save_share_rights(conn, share_id, values["rights"])
    audit("partages.modification", f"{values['name']} ({values['limit'] // GIB} Go, {len(values['rights'])} utilisateur(s))",
          commit=False)
    conn.commit()
    push(conn)
    flash(_("Partage %(name)s enregistré.", name=values["name"]), "ok")
    return back("shares")


@app.route("/shares/<int:share_id>/delete", methods=["POST"])
@login_required
def shares_delete(share_id):
    require("samba.manage")
    row = db().execute("SELECT * FROM samba_shares WHERE id=?", (share_id,)).fetchone() or abort(404)
    conn = db()
    conn.execute("DELETE FROM samba_shares WHERE id=?", (share_id,))
    path = locosamba.base_dir(row["mount"]) + "/" + row["folder"]
    audit("partages.retrait", f"{row['name']} ({path})", commit=False)
    conn.commit()
    push(conn)
    flash(_("Partage %(name)s retiré : ses fichiers restent sur le serveur, dans %(path)s.", name=row["name"], path=path), "ok")
    return back("shares")


@app.route("/shares/install", methods=["POST"])
@login_required
def shares_install():
    require("samba.manage")
    if not samba_possible():
        abort(404)
    try:
        locosamba.request("install")
    except ValueError:
        return _fail(_("L'agent d'administration (service loco-agent) ne répond pas."))
    audit("partages.installation", "Samba")
    flash(_("Installation de Samba demandée : elle prend une à deux minutes."), "ok")
    return back("shares")


@app.route("/shares/takeover", methods=["POST"])
@login_required
def shares_takeover():
    require("samba.manage")
    try:
        locosamba.request("apply", spec=spec(), enabled=True)
        locosamba.request("takeover")
    except ValueError:
        return _fail(_("L'agent d'administration (service loco-agent) ne répond pas."))
    audit("partages.reprise", "configuration Samba existante gardée dans /etc/samba/smb.conf.avant-loco")
    flash(_("loco gère désormais Samba : l'ancienne configuration est gardée dans /etc/samba/smb.conf.avant-loco."), "ok")
    return back("shares")


# --------------------------------------------------------------------------- utilisateurs Samba

def _password_error(new, confirm, login):
    """Règles d'un mot de passe Samba (celles de loco, plus ce que Samba accepte) ; message, ou "" s'il convient."""
    if new != confirm:
        return _("Les deux saisies du nouveau mot de passe diffèrent.")
    problem = password_problem(new, login)
    if problem:
        return problem
    if any(c in new for c in "\r\n\x00") or len(new) > 256:
        return _("Ce mot de passe contient un caractère que Samba n'accepte pas.")
    return ""


def _user_rights():
    """Droits choisis dans le dialogue d'un utilisateur : {partage: écriture ?}."""
    rights = {}
    for row in db().execute("SELECT id FROM samba_shares"):
        choice = request.form.get(f"share-{row['id']}", "")
        if choice in ("read", "write"):
            rights[row["id"]] = choice == "write"
    return rights


def _save_user_rights(conn, account_id, rights):
    conn.execute("DELETE FROM samba_access WHERE account_id=?", (account_id,))
    conn.executemany("INSERT INTO samba_access (share_id, account_id, can_write) VALUES (?, ?, ?)",
                     [(sid, account_id, int(write)) for sid, write in rights.items()])


def _send_password(conn, account_id, password):
    """Description à jour (le compte Unix existe), puis le mot de passe : l'agent traite les demandes dans l'ordre."""
    locosamba.request("apply", spec=spec(conn), enabled=True)
    locosamba.request("password", unix=unix_name(account_id), password=password)


def _account(account_id):
    return db().execute("SELECT * FROM samba_accounts WHERE id=?", (account_id,)).fetchone() or abort(404)


@app.route("/shares/users/add", methods=["POST"])
@login_required
def shares_user_add():
    """Nouvel utilisateur Samba : lié à un compte loco (même identifiant, désactivé et supprimé avec lui) ou seul."""
    require("samba.manage")
    linked, user = request.form.get("loco", ""), None
    if linked:
        user = db().execute("SELECT id, username FROM users WHERE id=? AND pending=0", (linked,)).fetchone() if linked.isdigit() else None
        if not user:
            return _fail(_("Choisissez un compte loco de la liste."), "utilisateurs")
        if db().execute("SELECT 1 FROM samba_accounts WHERE user_id=?", (user["id"],)).fetchone():
            return _fail(_("Ce compte loco est déjà un utilisateur Samba."), "utilisateurs")
        login = user["username"]
    else:
        login = request.form.get("login", "").strip().lower()
        if not config.USERNAME_RE.match(login) or login in RESERVED_LOGINS:
            return _fail(_("Identifiant invalide : minuscules, chiffres, point, tiret ou trait bas, de 2 à 32 caractères."), "utilisateurs")
        if db().execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (login,)).fetchone():
            return _fail(_("Un compte loco porte déjà cet identifiant : choisissez-le dans « Compte loco »."), "utilisateurs")
    if db().execute("SELECT 1 FROM samba_accounts WHERE login=? COLLATE NOCASE", (login,)).fetchone():
        return _fail(_("Un utilisateur Samba porte déjà cet identifiant."), "utilisateurs")
    new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
    error = _password_error(new, confirm, login)
    if error:
        return _fail(error, "utilisateurs")
    conn = db()
    cur = conn.execute("INSERT INTO samba_accounts (user_id, login, label, password_at, created_at, created_by) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (user["id"] if user else None, login,
                                                     request.form.get("label", "").strip()[:80], _stamp(), _stamp(),
                                                     g.user["username"]))
    rights = _user_rights()
    _save_user_rights(conn, cur.lastrowid, rights)
    audit("partages.utilisateur_ajout", f"{login}{' (compte loco)' if user else ''}, {len(rights)} partage(s)", commit=False)
    conn.commit()
    try:
        _send_password(conn, cur.lastrowid, new)
    except ValueError:
        return _fail(_("L'agent d'administration (service loco-agent) ne répond pas : réessayez dans un instant."), "utilisateurs")
    flash(_("Utilisateur Samba %(login)s créé.", login=login), "ok")
    return back("shares", _anchor="utilisateurs")


@app.route("/shares/users/<int:account_id>", methods=["POST"])
@login_required
def shares_user_edit(account_id):
    """Description, droits sur les partages, et nouveau mot de passe s'il est saisi."""
    require("samba.manage")
    row = _account(account_id)
    new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
    if new or confirm:
        error = _password_error(new, confirm, row["login"])
        if error:
            return _fail(error, "utilisateurs")
    conn = db()
    conn.execute("UPDATE samba_accounts SET label=? WHERE id=?", (request.form.get("label", "").strip()[:80], account_id))
    rights = _user_rights()
    _save_user_rights(conn, account_id, rights)
    if new:
        conn.execute("UPDATE samba_accounts SET password_at=? WHERE id=?", (_stamp(), account_id))
    audit("partages.utilisateur_modification",
          f"{row['login']} ({len(rights)} partage(s){', mot de passe changé' if new else ''})", commit=False)
    conn.commit()
    try:
        if new:
            _send_password(conn, account_id, new)
        else:
            locosamba.request("apply", spec=spec(conn), enabled=True)
    except ValueError:
        return _fail(_("L'agent d'administration (service loco-agent) ne répond pas : réessayez dans un instant."), "utilisateurs")
    flash(_("Utilisateur Samba %(login)s enregistré.", login=row["login"]), "ok")
    return back("shares", _anchor="utilisateurs")


@app.route("/shares/users/<int:account_id>/toggle", methods=["POST"])
@login_required
def shares_user_toggle(account_id):
    """Désactivé : il ne se connecte plus à rien, mais garde ses droits et son mot de passe pour être réactivé."""
    require("samba.manage")
    row = _account(account_id)
    conn = db()
    conn.execute("UPDATE samba_accounts SET disabled=? WHERE id=?", (0 if row["disabled"] else 1, account_id))
    audit("partages.utilisateur_" + ("reactive" if row["disabled"] else "desactive"), row["login"], commit=False)
    conn.commit()
    push(conn)
    flash(_("Utilisateur Samba %(login)s réactivé.", login=row["login"]) if row["disabled"]
          else _("Utilisateur Samba %(login)s désactivé : il ne peut plus se connecter.", login=row["login"]), "ok")
    return back("shares", _anchor="utilisateurs")


@app.route("/shares/users/<int:account_id>/delete", methods=["POST"])
@login_required
def shares_user_delete(account_id):
    """Retiré de Samba ; le compte loco lié, s'il y en a un, reste intact."""
    require("samba.manage")
    row = _account(account_id)
    conn = db()
    conn.execute("DELETE FROM samba_accounts WHERE id=?", (account_id,))
    audit("partages.utilisateur_retrait", row["login"], commit=False)
    conn.commit()
    push(conn)
    flash(_("Utilisateur Samba %(login)s retiré.", login=row["login"]), "ok")
    return back("shares", _anchor="utilisateurs")


# --------------------------------------------------------------------------- connexions des postes

def _cut_done(pid, seconds):
    """La connexion n'est plus dans l'état publié par l'agent (il l'a coupée) ; faux au bout du délai."""
    deadline = time.time() + seconds
    while True:
        if not any(str(x.get("pid") or "") == pid for x in locosamba.status().get("sessions") or []):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


@app.route("/shares/sessions/cut", methods=["POST"])
@login_required
def shares_session_cut():
    """Coupe la connexion d'un poste (le processus smbd qui la sert, désigné par l'agent lui-même) ; au choix,
    l'utilisateur est désactivé du même coup, pour qu'il ne se reconnecte pas."""
    require("samba.manage")
    pid, uid = request.form.get("pid", ""), request.form.get("uid", "")
    rows = [x for x in agent_state().get("sessions") or []
            if pid.isdigit() and str(x.get("pid") or "") == pid and str(x.get("uid") or "") == uid]
    if not rows:
        flash(_("Cette connexion est déjà terminée."), "ok")
        return back("shares", _anchor="connexions")
    who = ", ".join(sorted({str(x.get("user") or "") for x in rows}))
    machine = str(rows[0].get("machine") or "")
    conn, off = db(), None
    target = request.form.get("disable", "")
    if target.isdigit():
        row = conn.execute("SELECT * FROM samba_accounts WHERE id=?", (int(target),)).fetchone()
        if row and not row["disabled"] and row["login"] in {x.get("user") for x in rows}:
            conn.execute("UPDATE samba_accounts SET disabled=1 WHERE id=?", (row["id"],))
            off = row
    audit("partages.connexion_coupee", f"{who} depuis {machine}" + (f", {off['login']} désactivé" if off else ""),
          commit=False)
    if off:
        audit("partages.utilisateur_desactive", off["login"], commit=False)
    conn.commit()
    try:
        if off:
            locosamba.request("apply", spec=spec(conn), enabled=True)    # désactivé d'abord : il ne revient pas
        locosamba.request("disconnect", pid=pid, uid=uid)
    except ValueError:
        return _fail(_("L'agent d'administration (service loco-agent) ne répond pas : réessayez dans un instant."),
                     "connexions")
    if not _cut_done(pid, CUT_WAIT):
        flash(_("Coupure demandée : la liste des connexions se met à jour dans un instant."), "ok")
    elif off:
        flash(_("Connexion de %(user)s coupée ; l'utilisateur est désactivé et ne peut plus se reconnecter.",
                user=off["login"]), "ok")
    else:
        flash(_("Connexion de %(user)s depuis %(machine)s coupée.", user=who, machine=machine), "ok")
    return back("shares", _anchor="connexions")
