"""Fonctionnalités générales activables : masquées et bloquées (pages, API, agents) quand le compte « admin » les coupe."""
import json

from flask import abort, flash, g, render_template, request, session

from . import app, config
from .i18n import L_, _
from .auth import audit, login_required
from .db import db
from .util import back

SETTING = "features_disabled"

# clé -> (libellé, description, dépend de, interrupteur de service de l'appliance)
FEATURES = {
    "apt": (L_("Paquets APT"), L_("Cache des paquets Debian/Ubuntu, dépôt local et script de configuration des postes."), (), ""),
    "rpm": (L_("Paquets RPM"), L_("Cache et dépôts locaux RHEL, Rocky et AlmaLinux."), (), "RPM_ENABLED"),
    "flatpak": (L_("Flatpak"), L_("Cache Flathub et applications Flatpak internes."), (), "FLATPAK_ENABLED"),
    "wu": (L_("Cache Windows Update"), L_("Mises à jour Windows téléchargées une fois puis servies en local."), (), "WU_ENABLED"),
    "wu_proxy": (L_("Proxy Windows Update (sans DNS)"), L_("Dirige automatiquement les postes Windows gérés vers le cache par leur proxy WinHTTP (Squid), sans redirection DNS. L'agent pose et retire le réglage."), ("wu", "fleet"), ""),
    "fleet": (L_("Mon parc et agents"), L_("Postes équipés de l'agent : inventaire, mises à jour, actions, groupes de machines."), (), ""),
    "windows_apps": (L_("Logiciels Windows"), L_("Catalogue local, centre de logiciels, groupes de logiciels et déploiements."), (), ""),
    "remote": (L_("Accès distant"), L_("Terminal et bureau à distance (console noVNC) par l'agent."), ("fleet",), ""),
    "scripts": (L_("Scripts"), L_("Bibliothèque de scripts PowerShell et shell exécutés sur les postes par l'agent."), ("fleet",), ""),
    "directories": (L_("Annuaires LDAP et RADIUS"), L_("Connexion avec un compte d'annuaire ; comptes locaux toujours disponibles."), (), ""),
}

# Module de la page -> fonctionnalités nécessaires ; quelques pages partagées sont précisées par leur nom.
MODULES = {"loco.linux": ("apt",), "loco.rpmrepo": ("rpm",), "loco.flatpak": ("flatpak",), "loco.wupdate": ("wu",),
           "loco.winapps": ("windows_apps",), "loco.winget": ("windows_apps",), "loco.deployments": ("windows_apps", "fleet"),
           "loco.fleet": ("fleet",), "loco.remote": ("fleet", "remote"), "loco.scripts": ("fleet", "scripts"), "loco.schedules": ("fleet",),
           "loco.directories": ("directories",)}
ENDPOINTS = {"linux_setup": ("apt",), "linux_proxmox": ("apt",), "linux_repository_source": ("apt",), "rpm_setup": ("rpm",),
             "flatpak_setup": ("flatpak",), "flatpak_repo_file": ("flatpak",), "windows_install_script": ("windows_apps",),
             "fleet_groups": ("fleet",), "fleet_group_editor": ("fleet",), "fleet_group_save": ("fleet",),
             "fleet_group_delete": ("fleet",), "fleet_group_membership": ("fleet",),
             "software_groups": ("windows_apps",), "software_group_editor": ("windows_apps",),
             "software_group_save": ("windows_apps",), "software_group_delete": ("windows_apps",),
             "software_group_attach": ("windows_apps",), "store_group_attach": ("windows_apps",)}
# Droits rattachés à une fonctionnalité : coupée, elle rend ces droits inopérants (navigation, boutons, pages).
PERMISSIONS = {"linux": ("apt",), "rpm": ("rpm",), "flatpak": ("flatpak",), "wu": ("wu",), "windows": ("windows_apps",),
               "store": ("windows_apps",), "fleet": ("fleet",), "deployments": ("fleet", "windows_apps"),
               "remote": ("fleet", "remote"), "scripts": ("fleet", "scripts"), "sso": ("directories",)}
# Échanges des agents : refusés avec 503 (l'agent réessaie plus tard sans rien perdre).
AGENT_ENDPOINTS = {"agent_enroll", "agent_checkin", "agent_result", "agent_linux_installer", "agent_windows_installer",
                   "agent_file", "agent_deb", "agent_rpm", "remote_agent"}


def disabled(conn=None):
    """Fonctionnalités coupées : par le compte « admin », ou dès l'installation (RPM, Flatpak)."""
    if conn is None and "features_off" in g:
        return g.features_off
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    try:
        off = {k for k in json.loads(row[0]) if k in FEATURES} if row else set()
    except (TypeError, ValueError):
        off = set()
    if not config.RPM_ENABLED:
        off.add("rpm")
    if not config.FLATPAK_ENABLED:
        off.add("flatpak")
    if not config.WU_PROXY:
        off.add("wu_proxy")              # Squid absent : ne jamais pousser un proxy inexistant aux postes
    # Une fonctionnalité dont un prérequis est coupé l'est aussi.
    off |= {k for k, f in FEATURES.items() if set(f[2]) & off}
    if conn is None:
        g.features_off = off
    return off


def enabled(key):
    return key not in disabled()


def allows(permission):
    """Faux si la fonctionnalité dont relève ce droit est coupée."""
    needed = PERMISSIONS.get(permission.split(".", 1)[0], ())
    return not needed or not (set(needed) & disabled())


def required(endpoint):
    if endpoint in ENDPOINTS:
        return ENDPOINTS[endpoint]
    view = app.view_functions.get(endpoint)
    return MODULES.get(getattr(view, "__module__", ""), ())


def is_super_admin(user=None):
    """Compte « admin » local créé à l'installation : seul à pouvoir couper ou rétablir une fonctionnalité."""
    user = user if user is not None else g.get("user")
    return bool(user and user["username"].lower() == "admin" and user["auth_source"] == "local"
                and user["role"] == "admin" and not user["disabled"])


@app.before_request
def block_disabled_features():
    endpoint = request.endpoint
    if endpoint is None or endpoint == "static":
        return
    off = [key for key in required(endpoint) if key in disabled()]
    if not off:
        return
    label = FEATURES[off[0]][0]
    if endpoint in AGENT_ENDPOINTS:
        return ({"error": f"Fonctionnalité désactivée sur ce serveur : {label}."}, 503, {"Retry-After": "600"})
    if not session.get("uid") and endpoint not in ENDPOINTS:
        return              # visiteur non connecté : redirigé vers la connexion comme ailleurs, sans rien révéler
    abort(403, _("Fonctionnalité désactivée par l'administrateur : %(label)s.", label=label))


app.before_request_funcs[None].remove(block_disabled_features)
app.before_request_funcs[None].insert(0, block_disabled_features)
app.jinja_env.globals.update(feature=enabled, is_super_admin=is_super_admin)


@app.route("/administration/features")
@login_required
def features():
    if not is_super_admin():
        abort(403, _("Réservé au compte « admin » de l'installation."))
    off = disabled()
    manual = set()
    row = db().execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    if row:
        try:
            manual = set(json.loads(row[0]))
        except (TypeError, ValueError):
            pass
    items = []
    for key, (label, description, depends, switch) in FEATURES.items():
        install_off = (key == "rpm" and not config.RPM_ENABLED) or (key == "flatpak" and not config.FLATPAK_ENABLED)
        items.append({"key": key, "label": label, "description": description, "on": key not in off,
                      "chosen": key not in manual, "install_off": install_off,
                      "depends": [FEATURES[d][0] for d in depends], "blocked_by": [FEATURES[d][0] for d in depends if d in off],
                      "service": bool(switch) and config.APPLIANCE})
    return render_template("features.html", items=items, appliance=config.APPLIANCE)


@app.route("/administration/features", methods=["POST"])
@login_required
def features_save():
    if not is_super_admin():
        abort(403, _("Réservé au compte « admin » de l'installation."))
    wanted_off = sorted(k for k in FEATURES if request.form.get(k) != "1")
    conn = db()
    previous = disabled(conn)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SETTING, json.dumps(wanted_off)))
    now = disabled(conn)
    changes = [f"{FEATURES[k][0]} {'désactivé' if k in now else 'activé'}" for k in FEATURES if (k in now) != (k in previous)]
    if changes:
        audit("systeme.fonctionnalites", ", ".join(changes), commit=False)
    conn.commit()
    g.pop("features_off", None)
    # Appliance : arrêter ou relancer aussi les services réseau correspondants (caches Windows Update, RPM, Flatpak).
    if config.APPLIANCE and changes:
        from . import appliance
        switches = {FEATURES[k][3]: "0" if k in now else "1" for k in FEATURES
                    if FEATURES[k][3] and (k in now) != (k in previous)}
        if switches:
            try:
                clean = appliance.locoadmin.validate(switches)
            except ValueError as e:
                clean = {}
                flash(_('Services réseau inchangés : %(e)s.', e=e), "error")
            if clean:
                appliance._submit("configure", clean)
    flash((_('Fonctionnalités enregistrées.') if changes else _('Aucun changement.')), "ok")
    return back("features")
