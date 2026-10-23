"""Alertes et notifications : e-mail (SMTP) et webhook, sur l'état du serveur et du parc.

Simple à configurer (une page, des interrupteurs, un bouton « Tester ») et sans spam : chaque condition a une
clé et un délai de répétition. L'évaluation tourne côté serveur, dans un fil de fond lancé au démarrage du portail.
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate

from flask import abort, flash, render_template, request

from . import app, config, features
from .i18n import _
from .auth import audit, can, login_required, require
from .db import db, transaction

SETTING = "alerts_config"
SEVERITY = {"info": 0, "warning": 1, "critical": 2}
CHANNELS = ("email", "webhook")
SECRET_FIELDS = {"email": "password", "webhook": "secret"}
MASK = "••••••••"                               # affiché à la place d'un secret déjà enregistré

# clé -> libellé, description, groupe, gravité par défaut, champ de seuil (ou None), délai de répétition (s), parc ?
RULES = {
    "service_down":     (lambda: _("Service arrêté"),            lambda: _("Un service de loco (nginx, cache, portail, agent) n'est plus actif."),
                         "server", "critical", None, 1800, False),
    "cert_expiring":    (lambda: _("Certificat HTTPS qui expire"), lambda: _("Le certificat du serveur arrive à expiration."),
                         "server", "warning", ("days", 21, 1, 365, lambda: _("jours")), 86400, False),
    "backup_overdue":   (lambda: _("Sauvegarde en retard"),      lambda: _("Aucune sauvegarde récente (la sauvegarde quotidienne a manqué)."),
                         "server", "warning", None, 86400, False),
    "offsite_failed":   (lambda: _("Copie hors-site en échec"),  lambda: _("La dernière copie de sauvegarde vers le stockage externe a échoué."),
                         "server", "warning", None, 21600, False),
    "disk_high":        (lambda: _("Disque presque plein"),      lambda: _("L'espace de stockage des données dépasse le seuil."),
                         "server", "warning", ("percent", 90, 50, 99, lambda: "%"), 21600, False),
    "login_failures":   (lambda: _("Échecs de connexion"),       lambda: _("Beaucoup d'échecs de connexion au portail en 24 h."),
                         "security", "warning", ("count", 10, 1, 1000, lambda: _("échecs / 24 h")), 10800, False),
    "security_updates": (lambda: _("Mises à jour de sécurité en attente"), lambda: _("Des postes du parc ont des mises à jour de sécurité en attente."),
                         "fleet", "warning", ("count", 1, 1, 1000, lambda: _("mises à jour")), 86400, True),
    "devices_offline":  (lambda: _("Postes hors contact"),       lambda: _("Des postes du parc n'ont pas contacté loco récemment."),
                         "fleet", "warning", ("count", 1, 1, 1000, lambda: _("postes")), 21600, True),
}
GROUPS = (("server", lambda: _("Serveur")), ("fleet", lambda: _("Parc")), ("security", lambda: _("Sécurité")))


# --------------------------------------------------------------------------- configuration

def _cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-alerts-v1", hashlib.sha256).digest()))


def _seal(value):
    return _cipher().encrypt(value.encode()).decode() if value else ""


def _open(value):
    try:
        return _cipher().decrypt(value.encode()).decode() if value else ""
    except Exception:                           # clé changée ou valeur altérée : secret à ressaisir
        return ""


def defaults():
    rules = {}
    for key, spec in RULES.items():
        rules[key] = {"on": True}
        if spec[4]:
            rules[key][spec[4][0]] = spec[4][1]
    return {
        "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "",
                  "password": "", "sender": "", "recipients": "", "min_severity": "warning"},
        "webhook": {"enabled": False, "url": "", "secret": "", "min_severity": "info"},
        "rules": rules,
    }


def load(conn=None):
    """Configuration effective (secrets déchiffrés), fusionnée avec les valeurs par défaut."""
    cfg = defaults()
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    if row:
        try:
            stored = json.loads(row[0])
        except (TypeError, ValueError):
            stored = {}
        for channel in CHANNELS:
            cfg[channel].update({k: v for k, v in (stored.get(channel) or {}).items() if k in cfg[channel]})
            cfg[channel][SECRET_FIELDS[channel]] = _open(cfg[channel][SECRET_FIELDS[channel]])
        for key, rule in (stored.get("rules") or {}).items():
            if key in cfg["rules"] and isinstance(rule, dict):
                cfg["rules"][key].update({k: v for k, v in rule.items() if k in cfg["rules"][key]})
    return cfg


def save(cfg, conn=None):
    stored = json.loads(json.dumps(cfg))         # copie, pour sceller sans toucher l'appelant
    for channel in CHANNELS:
        stored[channel][SECRET_FIELDS[channel]] = _seal(cfg[channel].get(SECRET_FIELDS[channel], ""))
    (conn or db()).execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (SETTING, json.dumps(stored)))


def configured(cfg):
    return any(cfg[c]["enabled"] for c in CHANNELS)


# --------------------------------------------------------------------------- évaluation des règles

def _rule_on(cfg, key):
    return bool(cfg["rules"].get(key, {}).get("on"))


def _threshold(cfg, key):
    field = RULES[key][4][0]
    return cfg["rules"].get(key, {}).get(field, RULES[key][4][1])


def _expected_services():
    from .system import UNITS
    units = [u for u, _label in UNITS]
    if not (config.CONTAINER or config.ALPINE or config.WINDOWS) and os.path.exists("/etc/squid/squid.conf"):
        units.append("squid")
    return units


def evaluate(conn, cfg=None):
    """Conditions actuellement en alerte : liste de {key, severity, subject, body}. N'envoie rien."""
    from .util import service_active, disk_usage
    from .system import cert_info, list_backups, _age
    cfg = cfg or load(conn)
    fleet_on = "fleet" not in features.disabled(conn)
    fired = []

    def add(key, body, severity=None):
        fired.append({"key": key, "severity": severity or RULES[key][3],
                      "subject": RULES[key][0](), "body": body})

    if _rule_on(cfg, "service_down"):
        down = [u for u in _expected_services() if not service_active(u)]
        if down:
            add("service_down", _("Services arrêtés : %(list)s.", list=", ".join(down)))

    if _rule_on(cfg, "cert_expiring"):
        cert = cert_info(os.path.join(config.TLS_DIR, "server.crt"))
        days = (cert or {}).get("end", {}).get("days")
        if days is not None and days <= _threshold(cfg, "cert_expiring"):
            add("cert_expiring", _("Le certificat HTTPS expire dans %(n)s jour(s).", n=days),
                "critical" if days <= 7 else "warning")

    if _rule_on(cfg, "backup_overdue"):
        backups = list_backups()
        if not backups:
            add("backup_overdue", _("Aucune sauvegarde n'a encore été faite."))
        elif _age(backups[0]["mtime"])["late"]:
            add("backup_overdue", _("La dernière sauvegarde remonte à %(age)s.", age=_age(backups[0]["mtime"])["text"]))

    if _rule_on(cfg, "offsite_failed"):
        from . import offsite
        st = offsite.state(conn)
        if offsite.configured(offsite.load(conn)) and st.get("last_error") and st.get("last_failed", "") > st.get("last_ok", ""):
            add("offsite_failed", _("La dernière copie hors-site a échoué : %(error)s", error=st["last_error"][:200]))

    if _rule_on(cfg, "disk_high"):
        pct = disk_usage()["pct"]
        if pct >= _threshold(cfg, "disk_high"):
            add("disk_high", _("Le stockage des données est à %(n)s %%.", n=pct), "critical" if pct >= 97 else "warning")

    if _rule_on(cfg, "login_failures"):
        since = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        n = conn.execute("SELECT COUNT(*) FROM audit WHERE at >= ? AND (action LIKE '%echec' OR action LIKE '%bloquee')",
                         (since,)).fetchone()[0]
        if n >= _threshold(cfg, "login_failures"):
            add("login_failures", _("%(n)s échec(s) de connexion au portail en 24 h.", n=n))

    if fleet_on and _rule_on(cfg, "security_updates"):
        n = conn.execute("SELECT COALESCE(SUM(security_updates),0) FROM devices WHERE state != 'removed'").fetchone()[0]
        if n >= _threshold(cfg, "security_updates"):
            add("security_updates", _("%(n)s mise(s) à jour de sécurité en attente sur le parc.", n=n))

    if fleet_on and _rule_on(cfg, "devices_offline"):
        from .fleet import ONLINE_SECONDS
        cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=ONLINE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
        n = conn.execute("SELECT COUNT(*) FROM devices WHERE state != 'removed' AND last_seen != '' AND last_seen < ?",
                         (cutoff,)).fetchone()[0]
        if n >= _threshold(cfg, "devices_offline"):
            add("devices_offline", _("%(n)s poste(s) sans contact depuis plus de %(m)s min.",
                                     n=n, m=ONLINE_SECONDS // 60))

    return fired


# --------------------------------------------------------------------------- envoi

def _targets(cfg, severity):
    return [c for c in CHANNELS if cfg[c]["enabled"] and SEVERITY[severity] >= SEVERITY[cfg[c]["min_severity"]]]


def send_email(cfg, subject, body):
    channel = cfg["email"]
    recipients = [r.strip() for r in channel["recipients"].replace(";", ",").split(",") if r.strip()]
    if not channel["host"] or not recipients:
        raise ValueError("serveur SMTP ou destinataires manquants")
    message = EmailMessage()
    message["From"] = channel["sender"] or channel["username"] or "loco@localhost"
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message.set_content(body)
    context = ssl.create_default_context()
    port = int(channel["port"] or (465 if channel["security"] == "tls" else 587))
    if channel["security"] == "tls":
        server = smtplib.SMTP_SSL(channel["host"], port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(channel["host"], port, timeout=20)
    try:
        if channel["security"] == "starttls":
            server.starttls(context=context)
        if channel["username"]:
            server.login(channel["username"], channel["password"])
        server.send_message(message)
    finally:
        server.quit()


def send_webhook(cfg, subject, body, severity):
    channel = cfg["webhook"]
    if not channel["url"]:
        raise ValueError("URL de webhook manquante")
    payload = json.dumps({"source": "loco", "host": config.HOST, "severity": severity,
                          "subject": subject, "text": body,
                          "sent_at": datetime.datetime.now().isoformat(timespec="seconds")}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "loco"}
    if channel["secret"]:
        headers["X-Loco-Signature"] = "sha256=" + hmac.new(channel["secret"].encode(), payload, hashlib.sha256).hexdigest()
    req = urllib.request.Request(channel["url"], data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise ValueError(f"réponse HTTP {resp.status}")


def _deliver(cfg, channel, subject, body, severity):
    if channel == "email":
        send_email(cfg, subject, body)
    else:
        send_webhook(cfg, subject, body, severity)


# --------------------------------------------------------------------------- aiguilleur (fil de fond)

def dispatch(conn):
    """Évalue les règles, met en file les alertes dues (hors délai de répétition), puis vide la file d'envoi."""
    cfg = load(conn)
    if not configured(cfg) or "alerts" in features.disabled(conn):
        return
    now = time.time()
    for alert in evaluate(conn, cfg):
        targets = _targets(cfg, alert["severity"])
        if not targets:
            continue
        row = conn.execute("SELECT last_fired FROM alert_state WHERE key=?", (alert["key"],)).fetchone()
        interval = RULES[alert["key"]][5]
        if row and row[0] and now - float(row[0]) < interval:
            continue
        conn.execute("INSERT INTO alert_state (key, last_fired) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET last_fired=excluded.last_fired", (alert["key"], str(now)))
        conn.execute("INSERT INTO alert_outbox (created, severity, subject, body, channels, next_try) "
                     "VALUES (?, ?, ?, ?, ?, '0')",
                     (datetime.datetime.now().isoformat(timespec="seconds"), alert["severity"],
                      "[loco] " + alert["subject"], alert["body"], json.dumps(targets)))
    _drain(conn, cfg)


def _drain(conn, cfg):
    now = time.time()
    for row in conn.execute("SELECT * FROM alert_outbox ORDER BY id").fetchall():
        if row["next_try"] and float(row["next_try"]) > now:
            continue
        remaining, error = [], ""
        for channel in json.loads(row["channels"]):
            try:
                _deliver(cfg, channel, row["subject"], row["body"], row["severity"])
            except Exception as e:              # réseau, SMTP, DNS… : on retentera
                remaining.append(channel)
                error = f"{channel}: {e}"[:300]
        if not remaining:
            conn.execute("DELETE FROM alert_outbox WHERE id=?", (row["id"],))
        elif row["attempts"] + 1 >= 12:
            conn.execute("DELETE FROM alert_outbox WHERE id=?", (row["id"],))
        else:
            backoff = min(60 * 2 ** row["attempts"], 3600)
            conn.execute("UPDATE alert_outbox SET channels=?, attempts=attempts+1, next_try=?, last_error=? WHERE id=?",
                         (json.dumps(remaining), str(now + backoff), error, row["id"]))


_started = False


def start_dispatcher():
    """Lance l'aiguilleur dans un fil de fond (une seule fois par processus ; loco tourne en un seul worker)."""
    global _started
    if _started or config.WINDOWS and os.environ.get("LOCO_NO_ALERTS"):
        return
    _started = True

    def loop():
        time.sleep(15)                          # laisser le serveur finir de démarrer
        while True:
            try:
                with transaction() as conn:
                    dispatch(conn)
            except Exception:                    # un fil de fond ne doit jamais faire tomber le serveur
                pass
            time.sleep(60)

    threading.Thread(target=loop, name="loco-alerts", daemon=True).start()


# --------------------------------------------------------------------------- page et routes

def _public(cfg):
    """Configuration pour l'affichage : secrets masqués, jamais renvoyés au navigateur."""
    out = json.loads(json.dumps(cfg))
    for channel in CHANNELS:
        out[channel][SECRET_FIELDS[channel]] = MASK if cfg[channel].get(SECRET_FIELDS[channel]) else ""
    return out


def _rules_view(cfg):
    view = []
    for key, spec in RULES.items():
        rule = {"key": key, "label": spec[0](), "description": spec[1](), "group": spec[2],
                "severity": spec[3], "on": _rule_on(cfg, key), "threshold": None}
        if spec[4]:
            rule["threshold"] = {"field": spec[4][0], "value": _threshold(cfg, key),
                                 "min": spec[4][2], "max": spec[4][3], "unit": spec[4][4]()}
        view.append(rule)
    return view


@app.route("/alerts")
@login_required
def alerts():
    cfg = load()
    groups = [(key, label()) for key, label in GROUPS]
    pending = db().execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0]
    last_error = db().execute("SELECT last_error FROM alert_outbox WHERE last_error != '' ORDER BY id DESC LIMIT 1").fetchone()
    return render_template("alerts.html", cfg=_public(cfg), rules=_rules_view(cfg), groups=groups,
                           severities=list(SEVERITY), channels=CHANNELS, configured=configured(cfg),
                           pending=pending, last_error=last_error[0] if last_error else "",
                           can_edit=can("alerts.configure"))


def _form_config():
    """Lit le formulaire et renvoie une configuration validée (secrets inchangés si laissés masqués)."""
    cfg = load()
    for channel in CHANNELS:
        section = cfg[channel]
        section["enabled"] = request.form.get(f"{channel}_enabled") == "1"
        sev = request.form.get(f"{channel}_min_severity", "")
        if sev in SEVERITY:
            section["min_severity"] = sev
    email = cfg["email"]
    email["host"] = request.form.get("email_host", "").strip()
    email["port"] = max(1, min(65535, int(request.form.get("email_port") or 587) if request.form.get("email_port", "").isdigit() else 587))
    if request.form.get("email_security") in ("none", "starttls", "tls"):
        email["security"] = request.form["email_security"]
    email["username"] = request.form.get("email_username", "").strip()
    email["sender"] = request.form.get("email_sender", "").strip()
    email["recipients"] = request.form.get("email_recipients", "").strip()
    cfg["webhook"]["url"] = request.form.get("webhook_url", "").strip()
    for channel in CHANNELS:                     # secret : conservé si laissé masqué, effacé si vidé
        field, value = SECRET_FIELDS[channel], request.form.get(f"{channel}_{SECRET_FIELDS[channel]}", "")
        if value != MASK:
            cfg[channel][field] = value
    for key, spec in RULES.items():
        rule = cfg["rules"][key]
        rule["on"] = request.form.get(f"rule_{key}") == "1"
        if spec[4]:
            field, low, high = spec[4][0], spec[4][2], spec[4][3]
            raw = request.form.get(f"rule_{key}_{field}", "")
            if raw.isdigit():
                rule[field] = max(low, min(high, int(raw)))
    return cfg


@app.route("/alerts", methods=["POST"])
@login_required
def alerts_save():
    require("alerts.configure")
    save(_form_config())
    audit("alertes.configuration", _("Alertes et notifications"))
    flash(_("Réglages des alertes enregistrés."), "ok")
    from .util import back
    return back("alerts")


@app.route("/alerts/test", methods=["POST"])
@login_required
def alerts_test():
    require("alerts.configure")
    channel = request.form.get("channel")
    if channel not in CHANNELS:
        abort(400)
    cfg = _form_config()                         # teste les valeurs à l'écran, sans les enregistrer
    subject = _("Test d'alerte loco")
    body = _("Ceci est une notification de test envoyée depuis loco (%(host)s).", host=config.HOST)
    try:
        _deliver(cfg, channel, "[loco] " + subject, body, "info")
        return {"ok": True, "message": _("Message de test envoyé.")}
    except Exception as e:
        return {"ok": False, "message": _("Échec : %(error)s", error=str(e)[:200])}
