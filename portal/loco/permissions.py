"""Catalogue fermé de droits, profils persistants et politique de chaque route du portail.

Les profils sont partagés entre comptes. Aucun droit inconnu n'accorde d'accès ; les prérequis sont
explicites. Le profil admin reste protégé et reçoit les futurs droits d'administration.
"""
import json
import unicodedata
from .i18n import L_, N_, _, gettext

# clé, libellé, description, prérequis, droit sensible
GROUPS = [
    (L_("Vue d'ensemble"), [
        ("dashboard.view", L_("Consulter la vue d'ensemble"), L_("Indicateurs globaux des caches, dépôts et services."), (), False),
    ]),
    (L_("Parc et accès distant"), [
        ("fleet.view", L_("Consulter les appareils"), L_("Inventaires, logiciels remontés et historique des actions."), (), False),
        ("fleet.enroll", L_("Inscrire des appareils"), L_("Afficher la clé et les commandes d'installation de l'agent."), ("fleet.view",), True),
        ("fleet.scan", L_("Demander un inventaire"), L_("Actualiser les informations d'un appareil."), ("fleet.view",), False),
        ("fleet.update", L_("Mettre à jour un appareil"), L_("Système, Flatpak et logiciels du catalogue déjà installés."), ("fleet.view",), True),
        ("fleet.bulk_update", L_("Mettre à jour tout le parc"), L_("Lancer les mises à jour sur tous les appareils concernés."), ("fleet.update",), True),
        ("fleet.install", L_("Déployer un logiciel"), L_("Installer ou mettre à jour un logiciel du catalogue sur un appareil."), ("fleet.view", "windows.view"), True),
        ("fleet.bulk_install", L_("Déployer sur des groupes ou plusieurs postes"), L_("Déploiements de logiciels sur plusieurs appareils ou sur un groupe."), ("fleet.install",), True),
        ("fleet.groups", L_("Gérer les groupes de machines"), L_("Créer les groupes et organiser leurs membres, sans déployer de logiciel."), ("fleet.view",), False),
        ("deployments.view", L_("Consulter les déploiements"), L_("Versions, cibles confirmées, avancement et résultats par poste."), ("fleet.view", "windows.view"), False),
        ("fleet.reboot", L_("Redémarrer les appareils"), L_("Redémarrage direct ou après une mise à jour."), ("fleet.view",), True),
        ("fleet.remove", L_("Retirer les agents et appareils"), L_("Désinstaller l'agent ou oublier un appareil."), ("fleet.view",), True),
        ("fleet.settings", L_("Configurer le parc"), L_("Seuil des pastilles, clé d'inscription et adresses du serveur annoncées aux agents."), ("fleet.view",), True),
        ("remote.terminal", L_("Ouvrir un terminal distant"), L_("Shell root ou PowerShell SYSTEM : contrôle privilégié du poste."), ("fleet.view",), True),
        ("remote.desktop", L_("Ouvrir le bureau noVNC"), L_("Contrôler le bureau, y compris l’UAC et l’écran verrouillé sous Windows (SYSTEM)."), ("fleet.view",), True),
        ("remote.files", L_("Gérer les fichiers du poste"), L_("Parcourir, téléverser, modifier et supprimer les fichiers du poste (accès complet, en root ou SYSTEM)."), ("fleet.view",), True),
    ]),
    (L_("Paquets APT"), [
        ("linux.view", L_("Consulter les dépôts APT"), L_("Paquets et commandes de branchement."), (), False),
        ("linux.create", L_("Créer un dépôt APT"), L_("Ajouter une distribution ou une version."), ("linux.view",), False),
        ("linux.upload", L_("Importer des paquets APT"), L_("Ajouter des fichiers .deb."), ("linux.view",), True),
        ("linux.copy", L_("Copier des paquets APT"), L_("Copier entre les dépôts locaux."), ("linux.view",), True),
        ("linux.delete", L_("Supprimer des paquets APT"), L_("Retirer des paquets du dépôt."), ("linux.view",), True),
    ]),
    (L_("Paquets RPM"), [
        ("rpm.view", L_("Consulter les dépôts RPM"), L_("Paquets et commandes de branchement."), (), False),
        ("rpm.create", L_("Créer un dépôt RPM"), L_("Ajouter une version majeure."), ("rpm.view",), False),
        ("rpm.upload", L_("Importer des paquets RPM"), L_("Ajouter des fichiers .rpm."), ("rpm.view",), True),
        ("rpm.delete", L_("Supprimer des paquets RPM"), L_("Retirer des paquets du dépôt."), ("rpm.view",), True),
    ]),
    (L_("Flatpak"), [
        ("flatpak.view", L_("Consulter Flatpak"), L_("Cache, dépôt local et commandes de branchement."), (), False),
        ("flatpak.upload", L_("Importer une application Flatpak"), L_("Ajouter un bundle au dépôt local."), ("flatpak.view",), True),
        ("flatpak.delete", L_("Supprimer une application Flatpak"), L_("Retirer une référence du dépôt local."), ("flatpak.view",), True),
    ]),
    (L_("Logiciels Windows et centre de logiciels"), [
        ("windows.view", L_("Consulter le catalogue local"), L_("Logiciels Windows et installeurs disponibles."), (), False),
        ("windows.groups", L_("Gérer les groupes de logiciels"), L_("Regrouper les logiciels du catalogue pour les déployer ensemble."), ("windows.view",), False),
        ("windows.add", L_("Ajouter un logiciel au catalogue"), L_("Téléverser un installeur ou le télécharger depuis une URL."), ("windows.view",), True),
        ("windows.edit", L_("Modifier un logiciel"), L_("Version, détection et arguments d'installation silencieuse."), ("windows.view",), True),
        ("windows.refresh", L_("Actualiser les installeurs"), L_("Retélécharger et rechercher les nouvelles versions WinGet."), ("windows.view",), True),
        ("windows.delete", L_("Supprimer un logiciel"), L_("Retirer son installeur du catalogue local."), ("windows.view",), True),
        ("store.view", L_("Parcourir le centre de logiciels"), L_("Rechercher et consulter les fiches publiques WinGet."), (), False),
        ("store.refresh", L_("Actualiser l'index du centre de logiciels"), L_("Télécharger à nouveau l'index WinGet."), ("store.view",), False),
        ("store.import", L_("Importer depuis le centre de logiciels"), L_("Ajouter un installeur vérifié au catalogue local."), ("store.view", "windows.add"), True),
    ]),
    (L_("Cache Windows Update"), [
        ("wu.view", L_("Consulter le cache Windows Update"), L_("Statistiques, domaines et commandes DNS."), (), False),
        ("wu.configure", L_("Modifier les domaines du cache"), L_("Changer les destinations relayées par le serveur."), ("wu.view",), True),
    ]),
    (L_("Serveur loco"), [
        ("system.view", L_("Consulter le système"), L_("Services, certificats, versions et état des sauvegardes."), (), False),
        ("system.configure", L_("Configurer le serveur"), L_("Réseaux autorisés, fonctions, proxy et stockage de l'appliance."), ("system.view",), True),
        ("system.update", L_("Mettre à jour le serveur"), L_("Paquets du système (noyau, correctifs de sécurité) et, sur l'appliance, loco."), ("system.view",), True),
        ("system.reboot", L_("Redémarrer le serveur"), L_("Demander un redémarrage du serveur loco."), ("system.view",), True),
        ("alerts.view", L_("Consulter les alertes"), L_("Voir la configuration des notifications et leur état."), (), False),
        ("alerts.configure", L_("Configurer les alertes"), L_("Canaux (e-mail, webhook), règles et seuils des notifications."), ("alerts.view",), True),
    ]),
    (L_("Scripts"), [
        ("scripts.view", L_("Consulter les scripts"), L_("Bibliothèque de scripts et résultats de leurs exécutions."), (), False),
        ("scripts.edit", L_("Gérer les scripts"), L_("Créer, modifier et supprimer des scripts PowerShell ou shell."), ("scripts.view",), True),
        ("scripts.run", L_("Exécuter un script sur des postes"), L_("Lancer un script en SYSTEM (Windows) ou root (Linux) sur des appareils ou groupes."),
         ("scripts.view", "fleet.view"), True),
    ]),
    (L_("Utilisateurs, profils et audit"), [
        ("users.view", L_("Consulter les comptes"), L_("Identifiants, profils, état et dernière connexion."), (), False),
        ("users.approve", L_("Approuver les comptes d'annuaire"), L_("Attribuer un profil à une nouvelle identité LDAP ou RADIUS et l'autoriser à accéder à loco."), ("users.view",), True),
        ("sso.manage", L_("Gérer les annuaires"), L_("Ajouter, tester et configurer les annuaires LDAP et RADIUS ; révoquer leurs connexions."), ("users.view",), True),
        ("users.create", L_("Créer des comptes"), L_("Attribuer uniquement des profils dont vous possédez tous les droits."), ("users.view",), True),
        ("users.edit", L_("Modifier les comptes"), L_("Changer le profil, activer ou désactiver les comptes que vous pouvez gérer."), ("users.view",), True),
        ("users.reset", L_("Réinitialiser les mots de passe"), L_("Comptes que vous pouvez gérer ; leurs sessions sont fermées."), ("users.view",), True),
        ("users.delete", L_("Supprimer des comptes"), L_("Comptes que vous pouvez gérer, sauf votre propre compte."), ("users.view",), True),
        ("roles.view", L_("Consulter les profils d'accès"), L_("Voir les droits accordés par chaque profil."), (), False),
        ("roles.manage", L_("Gérer les profils d'accès"), L_("Créer, modifier ou supprimer des profils dans la limite de vos droits."), ("roles.view", "users.view"), True),
        ("audit.view", L_("Consulter le journal"), L_("Historique des connexions et actions d'administration."), (), False),
        ("audit.export", L_("Exporter le journal"), L_("Télécharger le journal en JSON, sur une période ou en entier."), ("audit.view",), True),
        ("audit.configure", L_("Configurer la conservation du journal"), L_("Durée de conservation des événements."), ("audit.view",), True),
    ]),
]
PERMISSIONS = {item[0]: {"key": item[0], "label": item[1], "description": item[2],
                        "requires": item[3], "sensitive": item[4]}
               for _, items in GROUPS for item in items}
VIEWER = {"dashboard.view", "fleet.view", "linux.view", "rpm.view", "flatpak.view", "windows.view", "wu.view", "deployments.view"}
DEFAULTS = {
    "pending": (N_("Sans accès"), N_("Aucun droit. Les nouvelles identités d'annuaire attendent une approbation administrative."), set()),
    "admin": (N_("Administrateur"), N_("Accès complet, y compris aux futurs droits. Profil protégé."), set(PERMISSIONS)),
    "viewer": (N_("Lecture seule"), N_("Consultation des appareils, caches et catalogues locaux."), VIEWER),
    "operator": (N_("Opérateur"), N_("Inventaire, mises à jour individuelles et déploiement. Sans terminal, bureau ni redémarrage."),
                 VIEWER | {"fleet.scan", "fleet.update", "fleet.install"}),
    "repository_manager": (N_("Gestionnaire des dépôts"), N_("Gestion des paquets et installeurs. Aucun déploiement sur les postes."),
                           {k for k in PERMISSIONS if k.split('.')[0] in {"linux", "rpm", "flatpak", "windows", "store"}} | {"dashboard.view"}),
    "auditor": (N_("Auditeur"), N_("Consultation, journal et son export, comptes et profils. Aucune modification."),
                VIEWER | {"store.view", "system.view", "users.view", "roles.view", "audit.view", "audit.export", "alerts.view"}),
}

# Chaque route privée est déclarée. Une nouvelle route oubliée est refusée par défaut.
# Plusieurs droits représentent des alternatives pour une même page de consultation.
ENDPOINTS = {
    "index": "dashboard.view", "api_status": "dashboard.view", "api_network": "dashboard.view",
    "enrollment": ("fleet.enroll", "linux.view", "rpm.view", "flatpak.view", "windows.view", "wu.view"),
    "agent_enroll_msi": "fleet.enroll",
    "linux": "linux.view", "linux_repository_create": "linux.create", "linux_upload": "linux.upload",
    "linux_copy": "linux.copy", "linux_delete": "linux.delete",
    "rpm": "rpm.view", "rpm_repository_create": "rpm.create", "rpm_upload": "rpm.upload", "rpm_delete": "rpm.delete",
    "flatpak": "flatpak.view", "flatpak_upload": "flatpak.upload", "flatpak_delete": "flatpak.delete",
    "windows_apps": "windows.view", "api_apps": "windows.view", "windows_apps_add": "windows.add",
    "windows_apps_edit": "windows.edit", "windows_apps_refresh": "windows.refresh", "windows_apps_delete": "windows.delete",
    "software_store": "store.view", "software_store_detail": "store.view", "software_store_refresh": "store.refresh",
    "winget_search": "store.view", "winget_add": "store.import", "winget_update": "windows.refresh",
    "software_detail": "windows.view",
    "software_groups": "windows.view", "software_group_editor": "windows.view",
    "software_group_save": "windows.groups", "software_group_delete": "windows.groups", "software_group_rename": "windows.groups",
    "software_group_attach": "windows.groups", "store_group_attach": "windows.groups",
    "fleet_groups": "fleet.view", "fleet_group_editor": "fleet.view",
    "fleet_group_save": "fleet.groups", "fleet_group_delete": "fleet.groups", "fleet_group_rename": "fleet.groups", "fleet_group_membership": "fleet.groups",
    "deployments": ("deployments.view", "fleet.install"), "deployment_detail": ("deployments.view", "fleet.install"),
    "deployment_compose": "fleet.install", "deployment_preview": "fleet.install",
    "deployment_compose_linux": "fleet.install", "deployment_preview_linux": "fleet.install",
    "deployment_confirm": "fleet.install", "deployment_cancel": "fleet.install",
    "deployments_legacy": ("deployments.view", "fleet.install"),
    "software_icon": ("windows.view", "store.view", "fleet.view"),
    "windows_update": "wu.view", "api_wu": "wu.view", "windows_update_dns": "wu.view",
    "windows_update_domains": "wu.configure",
    "cache_state": ("wu.view", "linux.view", "rpm.view", "flatpak.view"),
    "cache_purge": ("wu.configure", "linux.delete", "rpm.delete", "flatpak.delete"),
    "fleet": "fleet.view", "fleet_device": "fleet.view", "fleet_device_live": "fleet.view", "fleet_action": "fleet.view",
    "fleet_cancel_command": "fleet.view", "fleet_search": "fleet.view", "fleet_windows_update": "fleet.update",
    "fleet_group_windows_update": "fleet.update",
    "schedules": "fleet.view", "schedule_editor": ("fleet.update", "scripts.run"),
    "schedule_save": ("fleet.update", "scripts.run"), "schedule_toggle": ("fleet.update", "scripts.run"),
    "schedule_delete": ("fleet.update", "scripts.run"),
    "fleet_update_all": "fleet.bulk_update", "fleet_update_selected": "fleet.update",
    "fleet_rotate_key": "fleet.settings", "fleet_threshold": "fleet.settings",
    "fleet_server_address": "fleet.settings", "fleet_settings": "fleet.settings",
    "remote_open": ("remote.terminal", "remote.desktop", "remote.files"),
    "remote_session": ("remote.terminal", "remote.desktop", "remote.files"),
    "remote_io": ("remote.terminal", "remote.desktop", "remote.files"),
    "system": "system.view", "system_time": "system.configure", "system_qemu_agent": "system.configure",
    "system_backup_download": "system.configure", "system_restore": "system.configure",
    "system_restore_upload": "system.configure", "system_backup_retention": "system.configure",
    "system_cert_renew": "system.configure", "system_cert_import": "system.configure", "system_cert_remove": "system.configure",
    "system_ca_import": "system.configure", "system_ca_remove": "system.configure", "system_csr": "system.configure",
    "system_csr_download": "system.configure", "system_enterprise_import": "system.configure",
    "system_tls_mode": "system.configure",
    "system_backup_delete": "system.configure",
    "system_offsite": "system.configure", "system_offsite_test": "system.configure",
    "system_shell_enable": "system.configure", "system_shell_disable": "system.configure", "system_shell_open": "system.configure",
    "system_shell_io": "system.configure", "system_shell_home": "system.configure",
    "alerts": "alerts.view", "alerts_save": "alerts.configure", "alerts_test": "alerts.configure",
    "appliance_updates": "system.view", "appliance_job": "system.view",
    "appliance_operations_state": "system.view", "appliance_updates_post": "system.view",
    "appliance_settings": "system.configure", "appliance_settings_post": "system.configure",
    "users": "users.view", "users_add": "users.create", "users_update": "users.edit",
    "users_approve": "users.approve",
    "sso_settings": "sso.manage", "directory_add": "sso.manage", "directory_update": "sso.manage", "directory_toggle": "sso.manage",
    "directory_delete": "sso.manage", "directory_move": "sso.manage", "directory_move_down": "sso.manage", "directory_test": "sso.manage",
    "users_reset": "users.reset", "users_mfa_reset": "users.reset", "users_delete": "users.delete", "journal": "audit.view",
    "journal_export": "audit.export", "journal_retention": "audit.configure",
    "scripts": "scripts.view", "script_editor": "scripts.view", "script_save": "scripts.edit", "script_delete": "scripts.edit",
    "script_compose": "scripts.run", "script_run_start": "scripts.run", "script_run": "scripts.view",
    "script_run_cancel": "scripts.run", "script_runs_cancel_all": "scripts.run", "script_history_clear": "scripts.edit",
    "script_run_delete": "scripts.edit",
    # Réservé en plus au compte « admin » de l'installation (vérifié par la page).
    "features": "system.view", "features_save": "system.view",
    "roles": "roles.view", "roles_editor": "roles.view", "roles_add": "roles.manage",
    "roles_update": "roles.manage", "roles_delete": "roles.manage",
}
ACCOUNT_ENDPOINTS = {"login", "login_mfa", "account", "logout", "set_language",
                     "account_mfa_setup", "account_mfa_codes", "account_mfa_disable", "account_mfa_recovery",
                     "account_appearance", "account_appearance_background", "account_appearance_background_delete",
                     "appearance_background"}
ACTION_PERMISSIONS = {"scan": "fleet.scan", "update": "fleet.update", "install-app": "fleet.install", "install-package": "fleet.install",
                      "run-script": "scripts.run", "reboot": "fleet.reboot", "uninstall": "fleet.remove", "forget": "fleet.remove"}


def expand(values):
    selected = set(values)
    if not selected <= PERMISSIONS.keys():
        raise ValueError(_('Droit inconnu.'))
    while True:
        complete = selected | {r for key in selected for r in PERMISSIONS[key]["requires"]}
        if complete == selected:
            return frozenset(selected)
        selected = complete


def role_key(label):
    return unicodedata.normalize("NFKC", label).casefold()


def seed_roles(conn):
    for key, (label, description, grants) in DEFAULTS.items():
        grants = json.dumps(sorted(expand(grants)))
        conn.execute("INSERT OR IGNORE INTO access_roles (id,label,label_key,description,permissions,builtin) VALUES (?,?,?,?,?,1)",
                     (key, label, role_key(label), description, grants))
        # Modèles intégrés (non modifiables) : alignés sur cette version, y compris ses nouveaux droits.
        conn.execute("UPDATE access_roles SET description=?, permissions=? WHERE id=? AND builtin=1", (description, grants, key))
        # Nom renommé entre deux versions : appliqué sauf si un profil personnalisé porte déjà ce nom.
        conn.execute("UPDATE access_roles SET label=?, label_key=? WHERE id=? AND builtin=1 AND label!=? AND NOT EXISTS "
                     "(SELECT 1 FROM access_roles WHERE label_key=? AND id!=?)", (label, role_key(label), key, label, role_key(label), key))


def role_permissions(conn, role):
    row = conn.execute("SELECT permissions FROM access_roles WHERE id=?", (role,)).fetchone()
    if row is None:
        return frozenset()
    if role == "admin":
        return frozenset(PERMISSIONS)
    try:
        values = json.loads(row[0])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            return frozenset()
        selected = set(values) & PERMISSIONS.keys()
        # Ne jamais accorder implicitement un prérequis manquant dans une définition corrompue.
        while True:
            valid = {k for k in selected if set(PERMISSIONS[k]["requires"]) <= selected}
            if valid == selected:
                return frozenset(valid)
            selected = valid
    except (ValueError, TypeError):
        return frozenset()


def role_labels(conn):
    # Profils intégrés : libellé enregistré en français, traduit à l'affichage ; les profils créés restent tels quels.
    return {r["id"]: gettext(r["label"]) if r["builtin"] else r["label"]
            for r in conn.execute("SELECT id,label,builtin FROM access_roles ORDER BY builtin DESC,label COLLATE NOCASE")}


def role_details(conn):
    """Profils avec leur description (traduites pour les profils intégrés), dans l'ordre de role_labels."""
    return [{"id": r["id"], "label": gettext(r["label"]) if r["builtin"] else r["label"],
             "description": (gettext(r["description"]) if r["builtin"] else r["description"]) if r["description"] else ""}
            for r in conn.execute("SELECT id,label,description,builtin FROM access_roles ORDER BY builtin DESC,label COLLATE NOCASE")]


def revoke(conn, user_id):
    """Révocation atomique des connexions, sessions distantes et frappes en attente."""
    row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        conn.execute("UPDATE devices SET state='active' WHERE state='removing' AND id IN "
                     "(SELECT device_id FROM device_commands WHERE created_by=? AND action='uninstall' AND state='queued')", (row[0],))
        conn.execute("UPDATE device_commands SET state='cancelled',finished_at=datetime('now','localtime'),"
                     "output='Accès révoqué avant transmission à l’agent.' WHERE created_by=? AND state='queued'", (row[0],))
    conn.execute("UPDATE users SET pw_version=pw_version+1 WHERE id=?", (user_id,))
    conn.execute("DELETE FROM remote_packets WHERE session_id IN (SELECT id FROM remote_sessions WHERE user_id=?)", (user_id,))
    conn.execute("UPDATE remote_sessions SET state='closed',error='Droits modifiés : reconnectez-vous.' WHERE user_id=? AND state!='closed'", (user_id,))
