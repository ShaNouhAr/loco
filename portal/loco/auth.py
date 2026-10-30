"""Comptes, sessions, limitation des tentatives de connexion et journal d'audit."""

import datetime
import json
import secrets
import time
from functools import wraps

from flask import Response, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from . import app, config, permissions, scope, PUBLIC_ENDPOINTS
from .i18n import _, gettext, n_
from . import journal as journal_view
from .db import db
from .util import back, safe_next

# Hachage factice : la vérification prend le même temps, que le compte existe ou non.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


# --------------------------------------------------------------------------- audit

def audit(action, detail="", username=None, commit=True):
    """Trace une action dans la base (page Journal) et dans journald."""
    user = username if username is not None else (g.user["username"] if g.get("user") else "")
    ip = request.remote_addr or ""
    db().execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user, ip, action, detail[:500]))
    if time.time() - _PURGE["last"] > 3600:
        purge_audit(db())
    if commit:
        db().commit()
    current_app.logger.info("audit user=%s ip=%s action=%s %s", user or "-", ip, action, detail)


# --------------------------------------------------------------------------- comptes

def generate_password():
    """Mot de passe temporaire lisible (sans caractères ambigus)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def password_problem(password, username=""):
    if len(password) < config.MIN_PASSWORD:
        return _("Le mot de passe doit faire au moins %(n)s caractères.", n=config.MIN_PASSWORD)
    if username and username.lower() in password.lower():
        return _("Le mot de passe ne doit pas contenir le nom du compte.")
    if len(set(password)) < 5:
        return _("Le mot de passe est trop répétitif.")
    return None


def create_user(conn, username, password=None, role="admin", must_change=True, password_hash=None):
    if role not in permissions.role_labels(conn):
        raise ValueError(_('Profil inconnu.'))
    conn.execute(
        "INSERT INTO users (username, password_hash, role, must_change, created_at) VALUES (?,?,?,?,?)",
        (username.lower(), password_hash or generate_password_hash(password), role, int(must_change),
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))


def set_password(conn, user_id, password, must_change=False):
    conn.execute("UPDATE users SET password_hash=?, must_change=? WHERE id=?",
                 (generate_password_hash(password), int(must_change), user_id))
    permissions.revoke(conn, user_id)


def active_admins(conn):
    return conn.execute("SELECT COUNT(*) FROM users u WHERE role='admin' AND disabled=0 AND pending=0 AND "
                        "(auth_source='local' OR EXISTS (SELECT 1 FROM directory_identities i JOIN auth_directories d "
                        "ON d.id=i.directory_id WHERE i.user_id=u.id AND d.enabled=1))").fetchone()[0]


def last_local_admin(conn, row):
    return (row["auth_source"] == "local" and row["role"] == "admin" and not row["disabled"]
            and conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND auth_source='local'").fetchone()[0] <= 1)


# --------------------------------------------------------------------------- session

@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    # Compte supprimé, désactivé ou mot de passe changé ailleurs : la session n'est plus valable.
    if row is None or row["disabled"] or row["pending"] or row["pw_version"] != session.get("pwv"):
        session.clear()
        # csrf_protect passe avant ce hook : garder un formulaire de connexion valide dès cette réponse.
        session["csrf"] = secrets.token_hex(16)
        return
    if row["auth_source"] == "sso":                     # ancienne connexion OpenID Connect : retirée
        session.clear()
        return
    if row["auth_source"] == "directory":
        directory = db().execute("SELECT d.enabled FROM directory_identities i JOIN auth_directories d ON d.id=i.directory_id "
                                 "WHERE i.user_id=? AND d.id=?", (uid, session.get("directory", ""))).fetchone()
        from . import features
        if directory is None or not directory["enabled"] or "directories" in features.disabled():
            session.clear()
            return
    g.user = row


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            if request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        if g.user["must_change"] and request.endpoint not in ("account", "logout"):
            if request.headers.get("X-Requested-With") == "fetch":
                abort(403)
            return redirect(url_for("account"))
        return view(*args, **kwargs)
    return wrapper


def can(permission):
    if g.get("user") is None:
        return False
    if "permissions" not in g:
        g.permissions = permissions.role_permissions(db(), g.user["role"])
    if permission not in g.permissions:
        return False
    if permission in scope.WHOLE_FLEET and not scope.whole():
        return False
    from . import features            # import tardif : features dépend de ce module
    return features.allows(permission)


def can_any(*values):
    return any(can(value) for value in values)


def require(permission):
    if not can(permission):
        abort(403)


@app.before_request
def enforce_access():
    endpoint = request.endpoint
    if endpoint is None or endpoint in PUBLIC_ENDPOINTS or endpoint in permissions.ACCOUNT_ENDPOINTS:
        return

    @login_required
    def check():
        if endpoint == "index" and not can("dashboard.view"):
            # Un profil limité arrive sur sa première page autorisée, jamais sur une boucle de 403.
            for page in ("fleet", "linux", "rpm", "flatpak", "windows_apps", "software_store",
                         "windows_update", "users", "roles", "journal", "system"):
                if can(permissions.ENDPOINTS[page]):
                    if (page == "rpm" and not config.RPM_ENABLED) or (page == "flatpak" and not config.FLATPAK_ENABLED):
                        continue
                    return redirect(url_for(page))
            return redirect(url_for("account"))
        allowed = permissions.ENDPOINTS.get(endpoint, ())
        if isinstance(allowed, str):
            allowed = (allowed,)
        if not can_any(*allowed):
            abort(403)
    return check()


def lock_access(permission):
    """Sérialise les mutations de comptes/profils et revérifie les droits sous le même verrou."""
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    if row is None or row["disabled"] or row["must_change"] or row["pw_version"] != session.get("pwv"):
        abort(403)
    g.user = row
    g.pop("permissions", None)
    g.pop("assignable_roles", None)
    g.pop("fleet_scope", None)
    require(permission)
    return conn


def can_assign(role):
    """Profil que le compte courant peut attribuer (et donc gérer ses comptes) : tous ses droits et tout son parc."""
    if "assignable_roles" not in g:
        own = permissions.role_permissions(db(), g.user["role"])
        g.assignable_roles = {key for key in permissions.role_labels(db())
                              if (key != "admin" or g.user["role"] == "admin")
                              and permissions.role_permissions(db(), key) <= own and scope.within(db(), key)}
    return role in g.assignable_roles


def can_manage_user(row):
    return can_assign(row["role"])


# --------------------------------------------------------------------------- tentatives de connexion

def _blocked(ip, username):
    since = time.time() - config.LOGIN_WINDOW
    conn = db()
    conn.execute("DELETE FROM login_failures WHERE at < ?", (since,))
    by_ip = conn.execute("SELECT COUNT(*) FROM login_failures WHERE ip=? AND at>=?", (ip, since)).fetchone()[0]
    by_user = conn.execute("SELECT COUNT(*) FROM login_failures WHERE username=? AND at>=?",
                           (username, since)).fetchone()[0]
    return by_ip >= config.LOGIN_MAX_PER_IP or by_user >= config.LOGIN_MAX_PER_USER


def _record_failure(ip, username):
    db().execute("INSERT INTO login_failures (at, ip, username) VALUES (?,?,?)", (time.time(), ip, username))
    db().commit()


def _complete_login(row, ip, directory=None):
    """Ouvre la session après validation complète (mot de passe, et 2FA le cas échéant)."""
    session.clear()
    session.permanent = True
    session.update(uid=row["id"], pwv=row["pw_version"], csrf=secrets.token_hex(16))
    if directory:
        session["directory"] = directory
    db().execute("DELETE FROM login_failures WHERE username=?", (row["username"],))
    db().execute("UPDATE users SET last_login=?, last_ip=? WHERE id=?",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ip, row["id"]))
    db().commit()
    g.user = row
    audit("sso.connexion" if directory else "connexion")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()[:64]
        password = request.form.get("password", "")
        ip = request.remote_addr or ""
        if _blocked(ip, username):
            audit("connexion.bloquee", f"compte « {username} »", username="")
            flash(_('Trop de tentatives. Réessayez dans quelques minutes.'), "error")
            return render_template("login.html", username=username), 429
        row = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        valid = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
        directory, notice = None, None
        if not (row and row["auth_source"] == "local"):
            # Annuaire LDAP/RADIUS : compte déjà lié, ou identifiant inconnu essayé dans l'ordre des annuaires actifs.
            from .directories import directory_login
            found, result = directory_login(username, password)
            if found is not None:
                row, valid, directory = found, True, result
            else:
                notice = result
        if notice:
            flash(notice, "ok")
            return render_template("login.html", username=username), 202
        if row and valid and not row["disabled"] and not row["pending"] and row["auth_source"] in ("local", "directory"):
            next_url = safe_next(request.args.get("next", "/"))
            # Comptes locaux avec 2FA : mot de passe validé, on demande le code avant d'ouvrir la session.
            if directory is None and row["auth_source"] == "local" and row["mfa_enabled"]:
                session.clear()
                session.permanent = True
                session["mfa_pending"] = {"uid": row["id"], "pwv": row["pw_version"], "ip": ip,
                                          "at": time.time(), "next": next_url}
                return redirect(url_for("login_mfa"))
            _complete_login(row, ip, directory)
            return redirect(url_for("account") if row["must_change"] else next_url)
        _record_failure(ip, username)
        audit("connexion.echec", f"compte « {username} »", username="")
        time.sleep(0.5)
        flash(_('Identifiant ou mot de passe incorrect.'), "error")
        return render_template("login.html", username=username), 401
    return render_template("login.html", username="")


@app.route("/login/mfa", methods=["GET", "POST"])
def login_mfa():
    """Deuxième étape : code TOTP (ou code de secours) après un mot de passe validé pour un compte local avec 2FA."""
    from . import totp
    if g.user is not None:
        return redirect(url_for("index"))
    pending = session.get("mfa_pending")
    if not pending or time.time() - pending.get("at", 0) > 300:
        session.pop("mfa_pending", None)
        return redirect(url_for("login"))
    row = db().execute("SELECT * FROM users WHERE id=?", (pending["uid"],)).fetchone()
    if row is None or row["disabled"] or row["pending"] or not row["mfa_enabled"] or row["pw_version"] != pending["pwv"]:
        session.pop("mfa_pending", None)
        return redirect(url_for("login"))
    if request.method == "POST":
        ip = request.remote_addr or ""
        if _blocked(ip, row["username"]):
            audit("connexion.bloquee", f"compte « {row['username']} » (2FA)", username="")
            flash(_('Trop de tentatives. Réessayez dans quelques minutes.'), "error")
            return render_template("login_mfa.html"), 429
        code = request.form.get("code", "")
        ok = totp.verify(totp.unseal(row["totp_secret"]), code)
        if not ok:
            used, remaining = totp.use_recovery_code(row["recovery_codes"], code)
            if used:
                db().execute("UPDATE users SET recovery_codes=? WHERE id=?", (remaining, row["id"]))
                db().commit()
                ok = True
        if ok:
            next_url, must_change = pending.get("next", "/"), row["must_change"]
            session.pop("mfa_pending", None)
            _complete_login(row, ip)
            return redirect(url_for("account") if must_change else safe_next(next_url))
        _record_failure(ip, row["username"])
        audit("connexion.echec", f"compte « {row['username']} » (2FA)", username="")
        time.sleep(0.5)
        flash(_('Code incorrect.'), "error")
        return render_template("login_mfa.html"), 401
    return render_template("login_mfa.html")


@app.route("/logout", methods=["POST"])
def logout():
    if g.user is not None:
        # Une console distante ne doit pas survivre à la déconnexion du navigateur.
        from . import remote
        owner = remote._owner()
        for row in db().execute("SELECT id FROM remote_sessions WHERE user_id=? AND owner=? AND state!='closed'",
                                (g.user["id"], owner)).fetchall():
            remote._close(row["id"], "Administrateur déconnecté.", g.user["username"])
        audit("deconnexion")
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- mon compte

@app.route("/language", methods=["POST"])
def set_language():
    """Langue de l'interface : gardée dans le compte (et un cookie, pour l'écran de connexion)."""
    from . import i18n
    lang = i18n.valid(request.form.get("lang", ""))
    if not lang:
        abort(400)
    if g.get("user") is not None:
        db().execute("UPDATE users SET lang=? WHERE id=?", (lang, g.user["id"]))
        db().commit()
    target = safe_next(request.form.get("next", "")) or "/"
    resp = {"redirect": target} if request.headers.get("X-Requested-With") == "fetch" else redirect(target)
    resp = app.make_response(resp)
    resp.set_cookie(i18n.COOKIE, lang, max_age=3 * 365 * 86400, samesite="Strict", httponly=True,
                    secure=app.config["SESSION_COOKIE_SECURE"])
    return resp


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        if g.user["auth_source"] != "local":
            abort(403)
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(g.user["password_hash"], current):
            flash(_('Mot de passe actuel incorrect.'), "error")
        elif new != request.form.get("confirm", ""):
            flash(_('Les deux saisies du nouveau mot de passe diffèrent.'), "error")
        elif new == current:
            flash(_("Le nouveau mot de passe doit être différent de l'actuel."), "error")
        elif (problem := password_problem(new, g.user["username"])):
            flash(problem, "error")
        else:
            set_password(db(), g.user["id"], new)
            db().commit()
            session["pwv"] = g.user["pw_version"] + 1
            audit("compte.mot_de_passe")
            if g.user["must_change"]:
                flash(_('Mot de passe enregistré. Bienvenue sur loco.'), "ok")
            else:
                flash(_('Mot de passe changé. Les autres sessions de ce compte sont fermées.'), "ok")
            return redirect(url_for("index"))
        return redirect(url_for("account"))
    # Mot de passe temporaire : page dédiée, sans la navigation, qui ne mène nulle part avant le changement.
    template = "password_setup.html" if g.user["must_change"] else "account.html"
    granted = permissions.role_permissions(db(), g.user["role"])
    activity = [{"r": r, **journal_view.describe(r["action"])} for r in db().execute(
        "SELECT * FROM audit WHERE username=? ORDER BY id DESC LIMIT 10", (g.user["username"],))]
    from . import totp
    return render_template(template, min_password=config.MIN_PASSWORD, activity=activity,
                           mfa_recovery_left=totp.recovery_left(g.user["recovery_codes"]) if g.user["mfa_enabled"] else 0,
                           granted_count=len(granted), total_permissions=len(permissions.PERMISSIONS),
                           granted_groups=[(label, [permissions.PERMISSIONS[item[0]] for item in items if item[0] in granted])
                                           for label, items in permissions.GROUPS])


# --------------------------------------------------------------------------- double authentification (mon compte)

@app.route("/account/mfa/setup", methods=["GET", "POST"])
@login_required
def account_mfa_setup():
    from . import totp
    if g.user["auth_source"] != "local" or g.user["mfa_enabled"]:
        return redirect(url_for("account"))
    secret = session.get("mfa_setup_secret") or totp.new_secret()
    session["mfa_setup_secret"] = secret
    if request.method == "POST":
        if totp.verify(secret, request.form.get("code", "")):
            plain, hashed = totp.new_recovery_codes()
            db().execute("UPDATE users SET totp_secret=?, mfa_enabled=1, recovery_codes=? WHERE id=?",
                         (totp.seal(secret), hashed, g.user["id"]))
            db().commit()
            session.pop("mfa_setup_secret", None)
            session["mfa_recovery_shown"] = plain
            audit("compte.2fa.activee")
            flash(_("Double authentification activée."), "ok")
            return redirect(url_for("account_mfa_codes"))
        flash(_("Code incorrect, réessayez."), "error")
    account_uri = totp.uri(secret, g.user["username"])
    return render_template("mfa_setup.html", secret=secret, otpauth=account_uri, qr=totp.qr_svg(account_uri))


@app.route("/account/mfa/codes")
@login_required
def account_mfa_codes():
    codes = session.pop("mfa_recovery_shown", None)
    if not codes:
        return redirect(url_for("account"))
    return render_template("mfa_codes.html", codes=codes)


@app.route("/account/mfa/disable", methods=["POST"])
@login_required
def account_mfa_disable():
    from . import totp
    if g.user["auth_source"] != "local" or not g.user["mfa_enabled"]:
        abort(403)
    ok = (totp.verify(totp.unseal(g.user["totp_secret"]), request.form.get("code", ""))
          or (request.form.get("password") and check_password_hash(g.user["password_hash"], request.form["password"])))
    if not ok:
        flash(_("Code ou mot de passe incorrect."), "error")
        return redirect(url_for("account"))
    db().execute("UPDATE users SET totp_secret='', mfa_enabled=0, recovery_codes='' WHERE id=?", (g.user["id"],))
    db().commit()
    audit("compte.2fa.desactivee")
    flash(_("Double authentification désactivée."), "ok")
    return redirect(url_for("account"))


@app.route("/account/mfa/recovery", methods=["POST"])
@login_required
def account_mfa_recovery():
    from . import totp
    if g.user["auth_source"] != "local" or not g.user["mfa_enabled"]:
        abort(403)
    if not totp.verify(totp.unseal(g.user["totp_secret"]), request.form.get("code", "")):
        flash(_("Code incorrect."), "error")
        return redirect(url_for("account"))
    plain, hashed = totp.new_recovery_codes()
    db().execute("UPDATE users SET recovery_codes=? WHERE id=?", (hashed, g.user["id"]))
    db().commit()
    session["mfa_recovery_shown"] = plain
    audit("compte.2fa.codes")
    return redirect(url_for("account_mfa_codes"))


# --------------------------------------------------------------------------- gestion des comptes

@app.route("/accounts/<int:user_id>/mfa-reset", methods=["POST"])
@login_required
def users_mfa_reset(user_id):
    conn = lock_access("users.reset")
    row = _get_user(user_id)
    if not can_manage_user(row) or not row["mfa_enabled"]:
        abort(403)
    conn.execute("UPDATE users SET totp_secret='', mfa_enabled=0, recovery_codes='' WHERE id=?", (row["id"],))
    audit("comptes.2fa.reinitialisation", row["username"], commit=False)
    conn.commit()
    flash(_("Double authentification retirée pour %(user)s.", user=row["username"]), "ok")
    return back("users")


@app.route("/accounts")
@login_required
def users():
    rows = db().execute("SELECT u.*, COALESCE(di.display_name, i.display_name) AS display_name, COALESCE(di.email, i.email) AS email, "
                        "COALESCE(di.subject, i.subject) AS subject, COALESCE(d.label, p.label) AS provider_label, d.kind AS directory_kind "
                        "FROM users u LEFT JOIN sso_identities i ON i.user_id=u.id LEFT JOIN sso_providers p ON p.id=i.provider_id "
                        "LEFT JOIN directory_identities di ON di.user_id=u.id LEFT JOIN auth_directories d ON d.id=di.directory_id "
                        "ORDER BY u.pending DESC,u.username").fetchall()
    issued = session.pop("issued_password", None)
    return render_template("users.html", users=rows, issued=issued, can_manage_user=can_manage_user, min_password=config.MIN_PASSWORD,
                           assignable_roles={k: v for k, v in permissions.role_labels(db()).items() if can_assign(k)},
                           role_choices=[r for r in permissions.role_details(db()) if can_assign(r["id"])],
                           draft=session.pop("new_account_draft", None))


def _get_user(user_id):
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/accounts/add", methods=["POST"])
@login_required
def users_add():
    conn = lock_access("users.create")
    username = request.form.get("username", "").strip().lower()
    role = request.form.get("role", "viewer")
    if role not in permissions.role_labels(conn):
        abort(400)
    if not can_assign(role):
        abort(403)
    draft = {"username": username, "role": role}             # rouvre la fenêtre avec la saisie si c'est refusé
    if not config.USERNAME_RE.match(username):
        flash(_('Identifiant invalide : 2 à 32 caractères, minuscules, chiffres, point, tiret.'), "error")
        session["new_account_draft"] = draft
    elif conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash(_('Le compte « %(username)s » existe déjà.', username=username), "error")
        session["new_account_draft"] = draft
    else:
        # Mot de passe choisi (saisi ou généré dans le formulaire) ou, champ vide, généré ici et affiché une fois.
        chosen = request.form.get("password", "")
        # Champ caché « 0 » + case « 1 » : sans le champ (appel scripté), le changement reste exigé par défaut.
        flags = request.form.getlist("must_change")
        must_change = "1" in flags if flags else True
        problem = password_problem(chosen, username) if chosen else None
        if problem:
            conn.rollback()
            flash(problem, "error")
            session["new_account_draft"] = draft
            return back("users")
        password = chosen or generate_password()
        create_user(conn, username, password, role, must_change=must_change)
        session["issued_password"] = {"username": username, "password": password, "must_change": must_change}
        audit("comptes.ajout", f"{username} ({permissions.role_labels(conn)[role]})", commit=False)
    conn.commit()
    return back("users")


@app.route("/accounts/<int:user_id>/reset", methods=["POST"])
@login_required
def users_reset(user_id):
    conn = lock_access("users.reset")
    row = _get_user(user_id)
    if row["id"] == g.user["id"] or not can_manage_user(row) or row["auth_source"] not in ("local", "sso"):
        abort(403)
    password = generate_password()
    set_password(conn, row["id"], password, must_change=True)
    if row["auth_source"] == "sso":
        conn.execute("UPDATE users SET auth_source='local' WHERE id=?", (row["id"],))
    session["issued_password"] = {"username": row["username"], "password": password, "must_change": True}
    audit("comptes.reinitialisation", row["username"], commit=False)
    conn.commit()
    return back("users")


@app.route("/accounts/<int:user_id>/edit", methods=["POST"])
@login_required
def users_update(user_id):
    conn = lock_access("users.edit")
    row = _get_user(user_id)
    if row["pending"]:
        abort(400, _('Ce compte doit être approuvé avant son activation.'))
    role = request.form.get("role", row["role"])
    disabled = request.form.get("disabled") == "1"
    if role not in permissions.role_labels(conn):
        abort(400)
    if not can_manage_user(row) or not can_assign(role):
        abort(403)
    if row["id"] == g.user["id"] and (role != row["role"] or disabled):
        flash(_('Vous ne pouvez pas modifier le profil ni désactiver votre propre compte.'), "error")
        return back("users")
    losing_admin = row["role"] == "admin" and not row["disabled"] and (role != "admin" or disabled)
    if losing_admin and (active_admins(conn) <= 1 or last_local_admin(conn, row)):
        flash(_('Il doit rester au moins un administrateur local actif.'), "error")
        return back("users")
    changes = []
    if role != row["role"]:
        changes.append(f"profil {permissions.role_labels(conn)[role]}")
    if disabled != bool(row["disabled"]):
        changes.append("désactivé" if disabled else "réactivé")
    if changes:
        conn.execute("UPDATE users SET role=?, disabled=? WHERE id=?", (role, int(disabled), row["id"]))
        permissions.revoke(conn, row["id"])
        audit("comptes.modification", f"{row['username']} : {', '.join(changes)}", commit=False)
        flash(_('Compte %(row)s : %(v)s. Ses connexions ont été fermées.', row=row['username'], v=', '.join(changes)), "ok")
    conn.commit()
    return back("users")


@app.route("/accounts/<int:user_id>/delete", methods=["POST"])
@login_required
def users_delete(user_id):
    conn = lock_access("users.delete")
    row = _get_user(user_id)
    if not can_manage_user(row):
        abort(403)
    if row["id"] == g.user["id"]:
        flash(_('Vous ne pouvez pas supprimer votre propre compte.'), "error")
    elif row["role"] == "admin" and not row["disabled"] and (active_admins(conn) <= 1 or last_local_admin(conn, row)):
        flash(_('Il doit rester au moins un administrateur local actif.'), "error")
    else:
        permissions.revoke(conn, row["id"])
        conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
        audit("comptes.suppression", row["username"], commit=False)
        flash(_('Compte %(row)s supprimé.', row=row['username']), "ok")
    conn.commit()
    return back("users")


# --------------------------------------------------------------------------- profils d'accès


def _role(role_id):
    row = db().execute("SELECT * FROM access_roles WHERE id=?", (role_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _editable_role(row):
    return not row["builtin"] and row["id"] != g.user["role"] and can_assign(row["id"])


@app.route("/accounts/profiles")
@login_required
def roles():
    rows = db().execute("SELECT r.*,COUNT(u.id) AS accounts FROM access_roles r LEFT JOIN users u ON u.role=r.id "
                        "GROUP BY r.id ORDER BY r.builtin DESC,r.label COLLATE NOCASE").fetchall()
    return render_template("roles.html", profiles=rows, role_permissions=lambda key: permissions.role_permissions(db(), key),
                           editable_role=_editable_role,
                           role_fleet=lambda key: scope_summary(db(), *scope.declared(db(), key)))


@app.route("/accounts/profiles/<role_id>")
@login_required
def roles_editor(role_id):
    row = None if role_id == "new" else _role(role_id)
    model = None
    if row is None:
        require("roles.manage")
        if request.args.get("copy"):
            model = _role(request.args["copy"])
            if not can_assign(model["id"]):
                abort(403)
    grants = permissions.role_permissions(db(), row["id"] if row else model["id"]) if row or model else frozenset()
    source = row or model
    whole, scope_groups, scope_devices = scope.declared(db(), source["id"]) if source else (scope.whole(), frozenset(), frozenset())
    editable = bool(can("roles.manage") and (row is None or _editable_role(row)))
    duplicable = bool(row and can("roles.manage") and can_assign(row["id"]))
    return render_template("role_editor.html", profile=row, selected=grants, editable=editable, duplicable=duplicable,
                           initial_label=_("Copie de %(label)s", label=gettext(model["label"]) if model["builtin"] else model["label"])
                           if model else "",
                           initial_description=(gettext(model["description"]) if model["builtin"] else model["description"])
                           if model else "",
                           permission_groups=permissions.GROUPS, permission_catalog=permissions.PERMISSIONS,
                           fleet=_scope_choices(whole, scope_groups, scope_devices))


def _scope_choices(whole, groups, devices):
    """Groupes et postes proposés dans « Parc accessible » : ceux du parc du compte qui édite (plus ce que le profil
    contient déjà, affiché pour un profil en lecture seule)."""
    conn, mine = db(), scope.visible()
    _whole, mine_groups, _devices = scope.declared(conn, g.user["role"])
    group_rows = [dict(r) for r in conn.execute(
        "SELECT g.id, g.name, (SELECT COUNT(*) FROM device_group_members m JOIN devices d ON d.id=m.device_id "
        "WHERE m.group_id=g.id AND d.state!='removed') AS members FROM device_groups g ORDER BY g.name COLLATE NOCASE")]
    device_rows = [dict(r) for r in conn.execute(
        "SELECT id, hostname, uid, os_name, os_family FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE")]
    return {"whole": whole, "groups": groups, "devices": devices, "can_whole": mine is None,
            "group_rows": [r for r in group_rows if mine is None or r["id"] in mine_groups or r["id"] in groups],
            "device_rows": sorted((r for r in device_rows if mine is None or r["id"] in mine or r["id"] in devices),
                                  key=lambda r: r["id"] not in devices),          # postes déjà choisis en tête
            "summary": scope_summary(conn, whole, groups, devices)}


def scope_summary(conn, whole, groups, devices):
    """« Tout le parc » ou « 2 groupes · 3 postes (17 postes au total) » pour les listes de profils."""
    if whole:
        return _("Tout le parc")
    parts = []
    if groups:
        parts.append(n_("%(num)s groupe", "%(num)s groupes", len(groups)))
    if devices:
        parts.append(n_("%(num)s poste", "%(num)s postes", len(devices)))
    return " · ".join(parts) if parts else _("Aucun poste")


def _scope_values(conn):
    """(tout le parc, groupes, postes) du formulaire, dans la limite du parc du compte qui enregistre."""
    whole = request.form.get("fleet_scope", "all") != "some"
    try:
        groups = frozenset(int(v) for v in request.form.getlist("scope_group")) if not whole else frozenset()
        devices = frozenset(int(v) for v in request.form.getlist("scope_device")) if not whole else frozenset()
    except ValueError:
        abort(400)
    if len(groups) > 1000 or len(devices) > 20000:
        abort(400)
    known_groups = {r[0] for r in conn.execute("SELECT id FROM device_groups")}
    known_devices = {r[0] for r in conn.execute("SELECT id FROM devices WHERE state!='removed'")}
    groups, devices = groups & known_groups, devices & known_devices
    mine_whole, mine_groups, _devices = scope.declared(conn, g.user["role"])
    if not mine_whole and (whole or not groups <= mine_groups or not devices <= (scope.visible() or frozenset())):
        abort(403)
    return whole, groups, devices


def _save_scope(conn, role_id, values):
    """Enregistre le parc d'un profil ; renvoie True s'il a changé (journalisé)."""
    before = scope.declared(conn, role_id)
    whole, groups, devices = values
    if before == (whole, groups, devices):
        return False
    conn.execute("UPDATE access_roles SET whole_fleet=? WHERE id=?", (1 if whole else 0, role_id))
    conn.execute("DELETE FROM access_role_groups WHERE role_id=?", (role_id,))
    conn.execute("DELETE FROM access_role_devices WHERE role_id=?", (role_id,))
    conn.executemany("INSERT INTO access_role_groups (role_id, group_id) VALUES (?,?)", [(role_id, i) for i in sorted(groups)])
    conn.executemany("INSERT INTO access_role_devices (role_id, device_id) VALUES (?,?)", [(role_id, i) for i in sorted(devices)])
    if whole:
        detail = "tout le parc"
    else:
        names = [r[0] for r in conn.execute(f"SELECT name FROM device_groups WHERE id IN ({','.join(map(str, sorted(groups))) or 'NULL'}) "
                                            "ORDER BY name COLLATE NOCASE")]
        hosts = [r[0] for r in conn.execute(f"SELECT hostname FROM devices WHERE id IN ({','.join(map(str, sorted(devices))) or 'NULL'}) "
                                            "ORDER BY hostname COLLATE NOCASE")]
        detail = "; ".join(p for p in (("groupes " + ", ".join(names)) if names else "",
                                       ("postes " + ", ".join(hosts)) if hosts else "") if p) or "aucun poste"
    audit("profils.parc", f"{role_id} : {detail}"[:400], commit=False)
    return True


def _role_values():
    label = request.form.get("label", "").strip()
    description = request.form.get("description", "").strip()
    if not 2 <= len(label) <= 64 or len(description) > 300 or any(ord(c) < 32 for c in label + description):
        abort(400, _('Nom de profil (2 à 64 caractères) ou description invalide.'))
    try:
        grants = permissions.expand(request.form.getlist("permissions"))
    except ValueError as exc:
        abort(400, str(exc))
    if not grants <= permissions.role_permissions(db(), g.user["role"]):
        abort(403)
    return label, description, grants


def _audit_grants(role_id, before, after):
    for kind, values in (("accordés", after - before), ("retirés", before - after)):
        chunk = []
        for value in sorted(values):
            if len(", ".join(chunk + [value])) > 340:
                audit("profils.droits", f"{role_id} : {kind} {', '.join(chunk)}", commit=False)
                chunk = []
            chunk.append(value)
        if chunk:
            audit("profils.droits", f"{role_id} : {kind} {', '.join(chunk)}", commit=False)


@app.route("/accounts/profiles/add", methods=["POST"])
@login_required
def roles_add():
    conn = lock_access("roles.manage")
    label, description, grants = _role_values()
    if conn.execute("SELECT 1 FROM access_roles WHERE label_key=?", (permissions.role_key(label),)).fetchone():
        flash(_('Un profil porte déjà ce nom.'), "error")
        return back("roles")
    fleet = _scope_values(conn)
    role_id = "r_" + secrets.token_hex(8)
    conn.execute("INSERT INTO access_roles (id,label,label_key,description,permissions) VALUES (?,?,?,?,?)",
                 (role_id, label, permissions.role_key(label), description, json.dumps(sorted(grants))))
    audit("profils.ajout", f"{role_id} : {label}", commit=False)
    _audit_grants(role_id, frozenset(), grants)
    _save_scope(conn, role_id, fleet)
    conn.commit()
    flash(_('Profil « %(label)s » créé.', label=label), "ok")
    return redirect(url_for("roles_editor", role_id=role_id))


@app.route("/accounts/profiles/<role_id>/edit", methods=["POST"])
@login_required
def roles_update(role_id):
    conn = lock_access("roles.manage")
    row = _role(role_id)
    if not _editable_role(row):
        abort(403)
    label, description, grants = _role_values()
    fleet = _scope_values(conn)
    before = permissions.role_permissions(conn, role_id)
    if conn.execute("SELECT 1 FROM access_roles WHERE label_key=? AND id!=?", (permissions.role_key(label), role_id)).fetchone():
        flash(_('Un profil porte déjà ce nom.'), "error")
        return back("roles_editor", role_id=role_id)
    conn.execute("UPDATE access_roles SET label=?,label_key=?,description=?,permissions=? WHERE id=?",
                 (label, permissions.role_key(label), description, json.dumps(sorted(grants)), role_id))
    if _save_scope(conn, role_id, fleet) or before != grants:
        for user in conn.execute("SELECT id FROM users WHERE role=?", (role_id,)).fetchall():
            permissions.revoke(conn, user["id"])
    audit("profils.modification", f"{role_id} : {row['label']} → {label}", commit=False)
    _audit_grants(role_id, before, grants)
    conn.commit()
    flash(_('Profil enregistré. Les comptes concernés doivent se reconnecter si leurs droits ou leur parc ont changé.'), "ok")
    return back("roles_editor", role_id=role_id)


@app.route("/accounts/profiles/<role_id>/delete", methods=["POST"])
@login_required
def roles_delete(role_id):
    conn = lock_access("roles.manage")
    row = _role(role_id)
    if not _editable_role(row):
        abort(403)
    if conn.execute("SELECT 1 FROM users WHERE role=?", (role_id,)).fetchone():
        flash(_('Réaffectez les comptes à un autre profil avant de supprimer celui-ci.'), "error")
        return back("roles")
    conn.execute("DELETE FROM access_roles WHERE id=?", (role_id,))
    audit("profils.suppression", f"{role_id} : {row['label']}", commit=False)
    conn.commit()
    flash(_('Profil supprimé.'), "ok")
    return back("roles")


# --------------------------------------------------------------------------- journal

AUDIT_RETENTION_DEFAULT = 30          # jours ; 0 : conservation illimitée
AUDIT_RETENTION_CHOICES = (7, 14, 30, 60, 90, 180, 365, 730, 0)
_PURGE = {"last": 0.0}


def audit_retention(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='audit_retention_days'").fetchone()
    try:
        days = int(row["value"]) if row else AUDIT_RETENTION_DEFAULT
    except (TypeError, ValueError):
        days = AUDIT_RETENTION_DEFAULT
    return days if 0 <= days <= 3650 else AUDIT_RETENTION_DEFAULT


def purge_audit(conn):
    """Rotation du journal : supprime les événements plus anciens que la durée de conservation."""
    _PURGE["last"] = time.time()
    days = audit_retention(conn)
    if not days:
        return 0
    limit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute("DELETE FROM audit WHERE at < ?", (limit,)).rowcount


FILTERS = ("periode", "debut", "fin", "categorie", "compte", "echecs", "q")


def _filters():
    """Filtres de la page reconnus dans la requête (repris par les liens, la pagination et l'export)."""
    return {k: request.args[k] for k in FILTERS if request.args.get(k)}


@app.route("/log")
@login_required
def journal():
    args = _filters()
    try:
        sql, values = journal_view.where(args)
    except ValueError:
        flash(_('Période invalide : dates attendues au format AAAA-MM-JJ, la première avant la seconde.'), "err")
        return redirect(url_for("journal"))
    before = request.args.get("avant", type=int)
    page_sql = sql + (" AND" if sql else " WHERE") + " id < ?" if before else sql
    rows = db().execute("SELECT * FROM audit" + page_sql + " ORDER BY id DESC LIMIT ?",
                        values + ([before] if before else []) + [journal_view.PAGE + 1]).fetchall()
    more = len(rows) > journal_view.PAGE
    days = []
    for r in rows[:journal_view.PAGE]:
        label = journal_view.day_label(r["at"])
        if not days or days[-1]["label"] != label:
            days.append({"label": label, "events": []})
        days[-1]["events"].append({"r": r, **journal_view.describe(r["action"])})
    stats = db().execute("SELECT COUNT(*) AS n, MIN(at) AS first FROM audit").fetchone()
    matching = db().execute("SELECT COUNT(*) FROM audit" + sql, values).fetchone()[0] if args else stats["n"]
    failures = db().execute("SELECT COUNT(*) FROM audit WHERE at >= ? AND (action LIKE '%echec' OR action LIKE "
                            "'%bloquee')", ((datetime.datetime.now() - datetime.timedelta(days=1))
                                            .strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    accounts = [r[0] for r in db().execute("SELECT DISTINCT username FROM audit WHERE username != '' "
                                           "ORDER BY username COLLATE NOCASE")]
    def link(**changes):
        return url_for("journal", **{k: v for k, v in {**args, **changes}.items() if v})
    return render_template("journal.html", days=days, args=args, link=link, before=before, more=more,
                           last_id=rows[journal_view.PAGE - 1]["id"] if more else None,
                           stats=stats, matching=matching, failures=failures, accounts=accounts,
                           counts=journal_view.category_counts(db(), args), categories=journal_view.CATEGORIES,
                           periods=journal_view.PERIODS, retention=audit_retention(db()),
                           choices=AUDIT_RETENTION_CHOICES, today=datetime.date.today().isoformat())


@app.route("/log/export")
@login_required
def journal_export():
    """Journal en JSON : en entier, sur une période (dates incluses) ou avec les filtres de la page."""
    args = _filters()
    try:
        start, end = journal_view.bounds(args)
        sql, values = journal_view.where(args)
    except ValueError:
        abort(400, _('Dates attendues au format AAAA-MM-JJ, la date de début avant la date de fin.'))
    rows = db().execute("SELECT id, at, username, ip, action, detail FROM audit" + sql + " ORDER BY id",
                        values).fetchall()
    period = f"{(start or 'début')[:10]}_{(end or 'fin')[:10]}"
    others = {k: v for k, v in args.items() if k not in ("periode", "debut", "fin")}
    audit("journal.export", f"{len(rows)} événement(s), période {period.replace('_', ' → ')}"
          + "".join(f", {k}={v}" for k, v in others.items()))
    document = {
        "server": config.HOST, "loco_version": config.VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"), "exported_by": g.user["username"],
        "period": {"from": start, "to": end}, "filters": others, "retention_days": audit_retention(db()),
        "count": len(rows),
        "events": [{"id": r["id"], "at": r["at"], "username": r["username"], "ip": r["ip"],
                    "action": r["action"], "label": journal_view.describe(r["action"])["label"],
                    "detail": r["detail"]} for r in rows],
    }
    name = f"loco-journal-{config.HOST}-{period}.json".replace(":", "-")
    return Response(json.dumps(document, ensure_ascii=False, indent=2) + "\n", mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{secure_filename(name) or "loco-journal.json"}"'})


@app.route("/log/retention", methods=["POST"])
@login_required
def journal_retention():
    try:
        days = int(request.form.get("days", ""))
    except ValueError:
        days = -1
    if days not in AUDIT_RETENTION_CHOICES:
        abort(400)
    db().execute("INSERT INTO settings (key, value) VALUES ('audit_retention_days', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(days),))
    removed = purge_audit(db())
    audit("journal.conservation", f"{days} jour(s)" if days else "illimitée", commit=False)
    db().commit()
    flash((_('Journal conservé %(days)s jours', days=days) if days else _('Journal conservé sans limite')) + (_(' ; %(removed)s événement(s) plus ancien(s) supprimé(s).', removed=removed) if removed else "."), "ok")
    return back("journal")


def bootstrap_admin(conn, secrets_data):
    """Reprise d'une installation à mot de passe unique : crée le compte « admin » avec ce mot de passe."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 and secrets_data.get("admin_hash"):
        create_user(conn, "admin", role="admin", must_change=False, password_hash=secrets_data["admin_hash"])
