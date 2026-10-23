"""Base SQLite : connexion par requête et migrations versionnées."""

import os
import sqlite3
from contextlib import contextmanager

from flask import g

from . import config

MIGRATIONS = [
    # 1 : catalogue Windows (schéma historique)
    """
    CREATE TABLE IF NOT EXISTS win_apps (
        id INTEGER PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'exe',
        args TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    );
    """,
    # 2 : comptes, journal d'audit, échecs de connexion
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change INTEGER NOT NULL DEFAULT 0,
        pw_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '',
        last_login TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX audit_at ON audit(at);
    CREATE TABLE login_failures (
        at REAL NOT NULL,
        ip TEXT NOT NULL,
        username TEXT NOT NULL
    );
    CREATE INDEX login_failures_at ON login_failures(at);
    """,
    # 3 : dépôts APT isolés par distribution et nom de version ; le dépôt historique reste intact.
    """
    CREATE TABLE apt_repositories (
        distribution TEXT NOT NULL,
        suite TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (distribution, suite)
    );
    """,
    # 4 : dépôts RPM locaux par version majeure de la famille Red Hat (el8, el9, el10…).
    """
    CREATE TABLE rpm_repositories (
        name TEXT PRIMARY KEY,
        created_at TEXT NOT NULL
    );
    """,
    # 5 : parc de postes (agent loco-client) : identité propre au poste, indépendante de son adresse IP ;
    # actions à exécuter par l'agent ; détection des logiciels du catalogue Windows installés sur les postes.
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY,
        uid TEXT UNIQUE NOT NULL,
        token_hash TEXT NOT NULL,
        hostname TEXT NOT NULL DEFAULT '',
        os_family TEXT NOT NULL DEFAULT '',
        os_name TEXT NOT NULL DEFAULT '',
        agent_version TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        addresses TEXT NOT NULL DEFAULT '',
        enrolled_at TEXT NOT NULL,
        last_seen TEXT NOT NULL DEFAULT '',
        last_scan TEXT NOT NULL DEFAULT '',
        updates INTEGER NOT NULL DEFAULT 0,
        security_updates INTEGER NOT NULL DEFAULT 0,
        reboot_required INTEGER NOT NULL DEFAULT 0,
        inventory TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'active'
    );
    CREATE TABLE device_commands (
        id INTEGER PRIMARY KEY,
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        action TEXT NOT NULL,
        params TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        sent_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT NOT NULL DEFAULT '',
        exit_code INTEGER,
        output TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX device_commands_device ON device_commands(device_id, state);
    ALTER TABLE win_apps ADD COLUMN detect TEXT NOT NULL DEFAULT '';
    ALTER TABLE win_apps ADD COLUMN winget_id TEXT NOT NULL DEFAULT '';
    """,
    # 6 : sessions distantes, files transitoires acquittées (jamais de transcription conservée).
    """
    CREATE TABLE remote_sessions (
        id TEXT PRIMARY KEY,
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        owner TEXT NOT NULL,
        pw_version INTEGER NOT NULL,
        kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'waiting',
        error TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        browser_seen REAL NOT NULL,
        browser_attached INTEGER NOT NULL DEFAULT 0,
        agent_seen REAL NOT NULL DEFAULT 0,
        expires REAL NOT NULL,
        cols INTEGER NOT NULL DEFAULT 100,
        rows INTEGER NOT NULL DEFAULT 30,
        input_seq INTEGER NOT NULL DEFAULT 0,
        output_seq INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX remote_sessions_device ON remote_sessions(device_id, state);
    CREATE TABLE remote_packets (
        session_id TEXT NOT NULL REFERENCES remote_sessions(id) ON DELETE CASCADE,
        direction TEXT NOT NULL,
        seq INTEGER NOT NULL,
        data BLOB NOT NULL,
        PRIMARY KEY (session_id, direction, seq)
    );
    """,
    # 7 : profils d'accès et permissions granulaires ; les rôles historiques sont conservés.
    """
    CREATE TABLE access_roles (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        label_key TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        permissions TEXT NOT NULL DEFAULT '[]',
        builtin INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 8 : fédération d'identités, demandes d'accès et transactions de connexion à usage unique.
    """
    ALTER TABLE remote_sessions ADD COLUMN terminal_fallback INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local';
    ALTER TABLE users ADD COLUMN pending INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE sso_providers (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        preset TEXT NOT NULL,
        issuer TEXT NOT NULL,
        client_id TEXT NOT NULL,
        client_secret TEXT NOT NULL,
        auth_method TEXT NOT NULL DEFAULT 'client_secret_basic',
        enabled INTEGER NOT NULL DEFAULT 1,
        revision INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE sso_identities (
        provider_id TEXT NOT NULL REFERENCES sso_providers(id) ON DELETE CASCADE,
        subject TEXT NOT NULL,
        user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        display_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (provider_id, subject)
    );
    CREATE TABLE sso_flows (
        state_hash TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL REFERENCES sso_providers(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL,
        browser_hash TEXT NOT NULL,
        payload TEXT NOT NULL,
        expires REAL NOT NULL
    );
    """,
    # 9 : groupes de logiciels/postes et déploiements confirmés avec sélection et versions figées.
    """
    ALTER TABLE win_apps ADD COLUMN expected_sha256 TEXT NOT NULL DEFAULT '';
    ALTER TABLE win_apps ADD COLUMN source_filename TEXT NOT NULL DEFAULT '';
    UPDATE win_apps SET expected_sha256=sha256,source_filename=filename WHERE winget_id!='' AND status='ready';
    CREATE TABLE device_groups (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE device_group_members (
        group_id INTEGER NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE,
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        PRIMARY KEY (group_id,device_id)
    );
    CREATE TABLE software_groups (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE software_group_members (
        group_id INTEGER NOT NULL REFERENCES software_groups(id) ON DELETE CASCADE,
        app_id INTEGER NOT NULL REFERENCES win_apps(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id,app_id)
    );
    CREATE TABLE deployments (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by TEXT NOT NULL,
        pw_version INTEGER NOT NULL,
        owner TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires REAL NOT NULL,
        state TEXT NOT NULL DEFAULT 'draft',
        plan TEXT NOT NULL,
        confirmed_at TEXT NOT NULL DEFAULT '',
        cancel_requested INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE deployment_targets (
        id INTEGER PRIMARY KEY,
        deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
        device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
        device_uid TEXT NOT NULL,
        hostname TEXT NOT NULL,
        os_family TEXT NOT NULL,
        command_id INTEGER UNIQUE REFERENCES device_commands(id) ON DELETE SET NULL,
        apps TEXT NOT NULL DEFAULT '[]',
        skipped TEXT NOT NULL DEFAULT '[]',
        reason TEXT NOT NULL DEFAULT '',
        last_state TEXT NOT NULL DEFAULT '',
        last_output TEXT NOT NULL DEFAULT '',
        UNIQUE (deployment_id,device_uid)
    );
    CREATE TRIGGER deployment_command_snapshot BEFORE DELETE ON device_commands
    BEGIN
        UPDATE deployment_targets
        SET last_state=CASE WHEN OLD.state IN ('queued','sent','running') THEN 'cancelled' ELSE OLD.state END,
            last_output=CASE WHEN OLD.state IN ('queued','sent','running') THEN 'Commande supprimée avant son terme.' ELSE OLD.output END
        WHERE command_id=OLD.id;
    END;
    CREATE INDEX deployment_targets_campaign ON deployment_targets(deployment_id);
    CREATE INDEX deployments_created ON deployments(created_at);
    CREATE INDEX device_group_members_device ON device_group_members(device_id);
    CREATE INDEX software_group_members_app ON software_group_members(app_id);
    """,

    # 10 : adresses du serveur connues de chaque agent (annonce d'une nouvelle adresse avant un changement).
    """
    ALTER TABLE devices ADD COLUMN servers TEXT NOT NULL DEFAULT '';
    """,
    # 11 : étape en cours remontée par l'agent pendant une installation (téléchargement, vérification, installation).
    """
    ALTER TABLE device_commands ADD COLUMN progress TEXT NOT NULL DEFAULT '';
    """,
    # 12 : bibliothèque de scripts (PowerShell, shell) et exécutions sur les postes, contenu figé à l'exécution.
    """
    CREATE TABLE scripts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        platform TEXT NOT NULL CHECK (platform IN ('windows', 'linux')),
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE script_runs (
        id INTEGER PRIMARY KEY,
        script_id INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        version INTEGER NOT NULL,
        platform TEXT NOT NULL,
        content TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE script_run_targets (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES script_runs(id) ON DELETE CASCADE,
        device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
        hostname TEXT NOT NULL,
        command_id INTEGER UNIQUE REFERENCES device_commands(id) ON DELETE SET NULL,
        last_state TEXT NOT NULL DEFAULT '',
        last_output TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX script_runs_script ON script_runs(script_id);
    CREATE INDEX script_run_targets_run ON script_run_targets(run_id);
    CREATE TRIGGER script_command_snapshot BEFORE DELETE ON device_commands
    BEGIN
        UPDATE script_run_targets SET last_state=OLD.state, last_output=OLD.output WHERE command_id=OLD.id;
    END;
    """,
    # 13 : annuaires LDAP et RADIUS à la place d'OpenID Connect. Les anciens fournisseurs sont désactivés ; leurs comptes
    # restent (journal, profils) et ne se connectent plus tant qu'un administrateur ne leur donne pas un mot de passe local.
    """
    CREATE TABLE auth_directories (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('ldap', 'radius')),
        label TEXT NOT NULL,
        config TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE directory_identities (
        directory_id TEXT NOT NULL REFERENCES auth_directories(id) ON DELETE CASCADE,
        subject TEXT NOT NULL,
        user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        display_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (directory_id, subject)
    );
    UPDATE sso_providers SET enabled=0;
    DELETE FROM sso_flows;
    """,
    # 14 : paquets installés, applications Flatpak et caractéristiques système remontés par l'agent Linux. Table à part :
    # la liste du parc relit l'inventaire de chaque appareil et n'a pas à porter des milliers de paquets.
    """
    CREATE TABLE device_software (
        device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        data TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT ''
    );
    """,
    # 15 : adresse de la dernière connexion de chaque compte (page Comptes), reprise du journal pour l'existant ;
    # le journal ne garde ses événements que quelques semaines.
    """
    ALTER TABLE users ADD COLUMN last_ip TEXT NOT NULL DEFAULT '';
    UPDATE users SET last_ip = COALESCE((SELECT ip FROM audit WHERE audit.username = users.username
                                         AND action IN ('connexion', 'sso.connexion') ORDER BY id DESC LIMIT 1), '');
    """,
    # 16 : planification ponctuelle d'une commande. Vide = tout de suite ; sinon horodatage local « après lequel »
    # la commande est transmise à l'agent (mises à jour et exécutions de scripts planifiées).
    """
    ALTER TABLE device_commands ADD COLUMN run_after TEXT NOT NULL DEFAULT '';
    """,
    # 17 : planifications (mises à jour et scripts), une fois ou récurrentes. Transformées en commandes d'agent quand elles
    # sont dues ; next_run vide = terminée. Heures locales du serveur, comme le reste du portail.
    """
    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL CHECK (action IN ('update', 'run-script')),
        params TEXT NOT NULL DEFAULT '{}',
        targets TEXT NOT NULL DEFAULT '{}',
        recurrence TEXT NOT NULL CHECK (recurrence IN ('once', 'daily', 'weekly', 'monthly')),
        at_time TEXT NOT NULL,
        start_date TEXT NOT NULL,
        weekdays TEXT NOT NULL DEFAULT '',
        monthday INTEGER NOT NULL DEFAULT 0,
        next_run TEXT NOT NULL DEFAULT '',
        last_run TEXT NOT NULL DEFAULT '',
        last_result TEXT NOT NULL DEFAULT '',
        last_ref INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX schedules_due ON schedules(enabled, next_run);
    """,
    # 18 : langue de l'interface choisie par chaque compte (vide = celle du navigateur).
    """
    ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT '';
    """,
    # 19 : alertes et notifications — délai de répétition par condition et file d'envoi (e-mail, webhook).
    """
    CREATE TABLE alert_state (
        key TEXT PRIMARY KEY,
        last_fired TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE alert_outbox (
        id INTEGER PRIMARY KEY,
        created TEXT NOT NULL,
        severity TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        channels TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_try TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT ''
    );
    """,
]


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction():
    """Connexion hors requête : validée à la sortie (annulée en cas d'erreur), puis fermée."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def migrate():
    """Crée ou met à jour le schéma ; renvoie la version atteinte."""
    os.makedirs(config.STATE, exist_ok=True)
    with transaction() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS, start=1):
            if number > version:
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {number}")
        from .permissions import seed_roles
        seed_roles(conn)
        # Un téléchargement interrompu par un redémarrage ne reprendra jamais.
        conn.execute("UPDATE win_apps SET status='error', error='Interrompu (redémarrage du service)' "
                     "WHERE status='downloading'")
        return conn.execute("PRAGMA user_version").fetchone()[0]
