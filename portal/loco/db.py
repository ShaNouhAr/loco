"""Base SQLite : connexion par requête, connexion tenue ouverte par le portail en service, migrations versionnées."""

import atexit
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager, suppress

from flask import g

from . import config

INSTANCE = secrets.token_hex(8)                 # identifiant de ce processus, pour l'élection des fils de fond

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
    # 20 : double authentification (TOTP) des comptes locaux.
    """
    ALTER TABLE users ADD COLUMN totp_secret TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN recovery_codes TEXT NOT NULL DEFAULT '';
    """,
    # 21 : supervision des postes (dernier relevé + historique de 7 jours).
    """
    ALTER TABLE devices ADD COLUMN metrics TEXT NOT NULL DEFAULT '';
    CREATE TABLE IF NOT EXISTS device_metrics (
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        at INTEGER NOT NULL,
        cpu REAL, mem REAL, swap REAL, disk REAL, net_rx REAL, net_tx REAL
    );
    CREATE INDEX IF NOT EXISTS device_metrics_device_at ON device_metrics(device_id, at);
    CREATE INDEX IF NOT EXISTS device_metrics_at ON device_metrics(at);
    """,
    # 22 : supervision en direct (relevés toutes les 2 s pendant qu'un administrateur regarde, 10 min gardées).
    """
    ALTER TABLE devices ADD COLUMN live_until REAL NOT NULL DEFAULT 0;
    CREATE TABLE IF NOT EXISTS device_live (
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        at REAL NOT NULL,
        cpu REAL, mem REAL, net_rx REAL, net_tx REAL
    );
    CREATE INDEX IF NOT EXISTS device_live_device_at ON device_live(device_id, at);
    """,
    # 23 : gel des mises à jour Windows par poste (l'agent impose la politique, loco garde la main).
    """
    ALTER TABLE devices ADD COLUMN block_windows_update INTEGER NOT NULL DEFAULT 0;
    """,
    # 24 : scripts Python (tout système) en plus de PowerShell/shell. On élargit la contrainte CHECK de la colonne
    # platform sans reconstruire la table (ids et clé étrangère de script_runs préservés) via writable_schema.
    """
    PRAGMA writable_schema=ON;
    UPDATE sqlite_master SET sql=replace(sql, "CHECK (platform IN ('windows', 'linux'))",
        "CHECK (platform IN ('windows', 'linux', 'python'))") WHERE type='table' AND name='scripts';
    PRAGMA writable_schema=OFF;
    """,
    # 25 : faisceau d'autorités épinglé par chaque agent (empreinte remontée au contact) : bascule d'autorité sans coupure.
    """
    ALTER TABLE devices ADD COLUMN trust_sha256 TEXT NOT NULL DEFAULT '';
    """,
    # 26 : apparence du portail par utilisateur (thème, accent, transparence, fond) — JSON, suit le compte.
    """
    ALTER TABLE users ADD COLUMN appearance TEXT NOT NULL DEFAULT '';
    """,
    # 27 : logiciel Windows — ignorer le certificat HTTPS du site source (intégrité garantie par l'empreinte SHA-256).
    """
    ALTER TABLE win_apps ADD COLUMN allow_insecure INTEGER NOT NULL DEFAULT 0;
    """,
    # 28 : fournisseurs OpenID Connect (Authentik…) parmi les annuaires. Contrainte CHECK élargie sans reconstruire la
    # table, comme la migration 24 : identités liées et clé étrangère préservées.
    """
    PRAGMA writable_schema=ON;
    UPDATE sqlite_master SET sql=replace(sql, "CHECK (kind IN ('ldap', 'radius'))",
        "CHECK (kind IN ('ldap', 'radius', 'oidc'))") WHERE type='table' AND name='auth_directories';
    PRAGMA writable_schema=OFF;
    """,
    # 29 : accès des assistants IA (MCP) : jeton haché, outils accordés un à un, rattaché au compte qui le crée
    # (supprimé avec lui ; inutilisable tant qu'il est désactivé).
    """
    CREATE TABLE mcp_tokens (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        hint TEXT NOT NULL DEFAULT '',
        tools TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL DEFAULT '',
        last_used_at TEXT NOT NULL DEFAULT '',
        last_ip TEXT NOT NULL DEFAULT '',
        last_client TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX mcp_tokens_user ON mcp_tokens(user_id);
    """,
    # 30 : parc accessible d'un profil — tout le parc (par défaut, et toujours pour les profils intégrés) ou les postes
    # de groupes choisis et des postes choisis un à un ; un groupe ou un poste supprimé sort du parc du profil.
    """
    ALTER TABLE access_roles ADD COLUMN whole_fleet INTEGER NOT NULL DEFAULT 1;
    CREATE TABLE access_role_groups (
        role_id TEXT NOT NULL REFERENCES access_roles(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE,
        PRIMARY KEY (role_id, group_id)
    );
    CREATE TABLE access_role_devices (
        role_id TEXT NOT NULL REFERENCES access_roles(id) ON DELETE CASCADE,
        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        PRIMARY KEY (role_id, device_id)
    );
    """,
    # 31 : clé d'inscription propre à un groupe de machines (créée à la première demande) : un poste inscrit avec elle
    # rejoint le groupe. Clé envoyée aux agents sous la forme « g<id>-<secret> » (même place que la clé du serveur).
    """
    ALTER TABLE device_groups ADD COLUMN enroll_key TEXT NOT NULL DEFAULT '';
    """,
    # 32 : envoi syslog, où en est chaque poste qui remonte ses journaux.
    """
    ALTER TABLE devices ADD COLUMN log_sent_at TEXT NOT NULL DEFAULT '';
    ALTER TABLE devices ADD COLUMN log_lines INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE devices ADD COLUMN log_error TEXT NOT NULL DEFAULT '';
    """,
    # 33 : déploiement par vagues. waves : réglages et avancement (JSON, vide = tout d'un coup) ; wave : vague du poste
    # (0 = sans vague) ; pending : la commande à créer quand sa vague commencera.
    """
    ALTER TABLE deployments ADD COLUMN waves TEXT NOT NULL DEFAULT '';
    ALTER TABLE deployment_targets ADD COLUMN wave INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE deployment_targets ADD COLUMN pending TEXT NOT NULL DEFAULT '';
    """,
    # 34 : groupes reçus du fournisseur à la dernière connexion (JSON) : ils proposent un profil à l'approbation, sans rien accorder seuls.
    """
    ALTER TABLE directory_identities ADD COLUMN groups TEXT NOT NULL DEFAULT '[]';
    """,
    # 35 : cartes et réseaux annoncés par l'agent (JSON) : le réveil à distance en dépend.
    """
    ALTER TABLE devices ADD COLUMN net_info TEXT NOT NULL DEFAULT '';
    """,
    # 36 : réveil demandé sur le poste, réappliqué par l'agent : on, off, ou vide (l'agent n'y touche pas).
    """
    ALTER TABLE devices ADD COLUMN wake_policy TEXT NOT NULL DEFAULT '';
    """,
    # 37 : mises à jour bloquées (paquet, KB ou titre), sur tout le parc ou un groupe, et combien chaque poste en a en attente.
    """
    CREATE TABLE update_blocks (
        id INTEGER PRIMARY KEY,
        family TEXT NOT NULL,
        pattern TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        group_id INTEGER REFERENCES device_groups(id),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL
    );
    CREATE UNIQUE INDEX update_blocks_unique ON update_blocks(family, pattern, COALESCE(group_id, 0));
    ALTER TABLE devices ADD COLUMN blocked_updates INTEGER NOT NULL DEFAULT 0;
    """,
    # 38 : accord de la personne devant le poste avant le bureau distant : exigé sur ce poste, et où en est chaque session
    # (vide : pas demandé ; pending ; given ; nobody : personne de connecté à qui demander).
    """
    ALTER TABLE devices ADD COLUMN remote_consent INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE remote_sessions ADD COLUMN consent TEXT NOT NULL DEFAULT '';
    """,
    # 39 : applications Android. Sources : dépôts F-Droid et empreinte confirmée de leur clé ; index_timestamp = dernière
    # entrée signée vérifiée (une plus ancienne est refusée). Catalogue : applications d'une source, ou fiches Google Play.
    # F-Droid et IzzyOnDroid sont proposés d'office (empreintes vérifiées le 2026-11-06, voir android_apps.KNOWN).
    """
    CREATE TABLE android_sources (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        fingerprint TEXT NOT NULL,
        known TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT '',
        index_timestamp INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT '',
        checked_at TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        apps INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE android_apps (
        id INTEGER PRIMARY KEY,
        package TEXT NOT NULL UNIQUE,
        store TEXT NOT NULL,
        source_id INTEGER REFERENCES android_sources(id),
        name TEXT NOT NULL,
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO android_sources (name, url, fingerprint, known, position, added_at) VALUES
        ('F-Droid', 'https://f-droid.org/repo', '43238d512c1e5eb2d6569f4a3afbf5523418b82e0a3ed1552770abb9a9c9ccab', 'fdroid', 0,
         strftime('%Y-%m-%d %H:%M', 'now', 'localtime')),
        ('IzzyOnDroid', 'https://apt.izzysoft.de/fdroid/repo', '3bf0d6abfeae2f401707b6d966be743bf0eee49c2561b9ba39073711f628937a',
         'izzy', 1, strftime('%Y-%m-%d %H:%M', 'now', 'localtime'));
    """,
    # 40 : terminal adb des appareils Android. mode : wireless (appairé par le débogage sans fil, port annoncé par
    # l'application) ou tcp (port classique choisi) ; report : ce que l'application loco voit (débogage, port, adresse).
    """
    CREATE TABLE android_adb (
        device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        host TEXT NOT NULL DEFAULT '',
        port INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT 'wireless',
        paired_at TEXT NOT NULL DEFAULT '',
        paired_by TEXT NOT NULL DEFAULT '',
        connected_at TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        report TEXT NOT NULL DEFAULT ''
    );
    """,

    # 41 : déverrouillage à distance d'un appareil Android (adb). unlock : JSON du calage de la grille de schéma propre
    # à l'appareil quand la géométrie automatique ne tombe pas juste (centre et pas des 9 points, en fractions de l'écran).
    """
    ALTER TABLE android_adb ADD COLUMN unlock TEXT NOT NULL DEFAULT '';
    """,
    # 42 : nom personnalisé d'un appareil (« friendly name »), affiché à la place du nom d'hôte dans Mon parc.
    """
    ALTER TABLE devices ADD COLUMN label TEXT NOT NULL DEFAULT '';
    """,
    # 43 : autorisations posées une fois par adb sur l'application loco d'un appareil Android appairé (écran distant et
    # contrôle sans geste) : JSON {at, agent, done}.
    """
    ALTER TABLE android_adb ADD COLUMN prepared TEXT NOT NULL DEFAULT '';
    """,
    # 44 : clés d'accès (passkeys, WebAuthn) des comptes locaux. passkey_handle : identifiant opaque du compte pour les
    # appareils ; passkeys : clé publique (DER, base64) et compteur de chaque clé ; passkey_challenges : défis à usage
    # unique en cours (éphémères).
    """
    ALTER TABLE users ADD COLUMN passkey_handle TEXT NOT NULL DEFAULT '';
    CREATE TABLE passkeys (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        credential_id TEXT UNIQUE NOT NULL,
        alg INTEGER NOT NULL,
        public_key TEXT NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL DEFAULT '',
        synced INTEGER NOT NULL DEFAULT 0,
        transports TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_used TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX passkeys_user ON passkeys(user_id);
    CREATE TABLE passkey_challenges (
        id TEXT PRIMARY KEY,
        challenge TEXT NOT NULL,
        purpose TEXT NOT NULL,
        user_id INTEGER,
        created REAL NOT NULL
    );
    """,
]


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def lease(name="background", ttl=180):
    """Élection d'un meneur pour les tâches de fond : un seul processus agit à la fois, même avec plusieurs workers.
    Renvoie True si CE processus détient (ou prend) le bail, renouvelé à chaque passage ; False si un autre le tient."""
    key, now = "lease_" + name, time.time()
    try:
        with transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            try:
                held = json.loads(row[0]) if row else {}
            except (TypeError, ValueError):
                held = {}
            if held.get("holder") not in (None, INSTANCE) and held.get("until", 0) > now:
                return False
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps({"holder": INSTANCE, "until": now + ttl})))
            return True
    except sqlite3.Error:
        return False


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
        wrote = conn.total_changes
        conn.close()
        if wrote and _keeper is not None:
            _keeper.dirty.set()                 # écritures peut-être validées sans attendre le disque : synchroniser


# --------------------------------------------------------------------------- base tenue ouverte par le portail en service

SYNC_SECONDS = 1.0              # écritures validées sans attendre le disque : synchronisées au plus tard dans ce délai
SYNC_KEY = "db_sync"            # réglage réécrit en mode sûr pour forcer cette synchronisation (instant de la dernière)
WATCH_SECONDS = 30              # ronde au repos : base remplacée sous le portail ? journal devenu trop gros ?
RETRY_SECONDS = 5               # connexion permanente en défaut : nouvel essai
WAL_LIMIT = 64 * 1024 * 1024    # journal au-delà : recopié en entier et remis à zéro (SQLite ne le raccourcit jamais seul)


def _db_file():
    """La base telle qu'elle est sur le disque à cet instant : change si le fichier est remplacé."""
    st = os.stat(config.DB_PATH)
    return config.DB_PATH, st.st_dev, st.st_ino


class _Keeper:
    """Connexion tenue ouverte tant que le portail sert.

    Sans elle, chaque requête ouvre puis ferme la seule connexion du processus, et à chaque fermeture SQLite recopie son
    journal (WAL) dans la base puis synchronise le disque : c'est l'essentiel du coût d'un contact d'agent. Avec elle, ce
    travail revient au point de contrôle automatique de SQLite, et une validation ne coûte plus qu'une écriture du journal.

    Elle sert aussi de filet aux écritures validées sans attendre le disque (relax, relais des sessions distantes) : une
    seconde au plus après, une ligne validée ici en mode sûr synchronise le journal, donc tout ce qui la précède."""

    def __init__(self):
        self.pid = os.getpid()
        self.healthy = False
        self.held = None                        # fichier tenu (chemin, périphérique, inode)
        self._warned = 0.0
        self.ready, self.dirty, self.halt = threading.Event(), threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name="loco-db", daemon=True)

    def alive(self):
        return self.pid == os.getpid() and self.thread.is_alive()       # un fil ne survit pas à un fork

    def _run(self):
        conn = None
        while not self.halt.is_set():
            try:
                if conn is not None and _db_file() != self.held:        # base remplacée : lâcher l'ancienne, tenir la nouvelle
                    conn.close()
                    conn = None
                if conn is None:
                    self.held = _db_file()
                    conn = sqlite3.connect(config.DB_PATH, timeout=30)
                    conn.execute("PRAGMA synchronous = FULL")
                    conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchall()      # ouvre vraiment la base et son journal
                if not self.dirty.is_set():
                    self.healthy = True                                 # après un défaut : seulement une fois rattrapé
                self.ready.set()
                if self.dirty.wait(WATCH_SECONDS) and not self.halt.is_set():
                    if self.healthy:
                        self.halt.wait(SYNC_SECONDS)                    # regrouper : une synchronisation par seconde au plus
                    self.dirty.clear()
                    self._sync(conn)
                    self.healthy = True
                self._trim(conn)
            except (sqlite3.Error, OSError) as e:
                # Disque plein, base retenue trop longtemps, fichier absent… : relax() refuse tant que cela dure (chaque
                # validation attend de nouveau le disque) et on réessaie.
                if self.healthy or time.monotonic() - self._warned > 600:       # une ligne au début, puis toutes les 10 min
                    self._warned = time.monotonic()
                    logging.getLogger(__name__).warning("base : connexion permanente en défaut (%s) ; les validations "
                                                        "attendent de nouveau le disque, nouvel essai toutes les %s s", e, RETRY_SECONDS)
                self.healthy = False
                self.dirty.set()
                if conn is not None:
                    with suppress(sqlite3.Error):
                        conn.close()
                    conn = None
                self.ready.set()
                self.halt.wait(RETRY_SECONDS)
        self.healthy = False
        if conn is not None:
            with suppress(sqlite3.Error):
                if self.dirty.is_set():
                    self._sync(conn)
            with suppress(sqlite3.Error):
                conn.close()                    # dernière connexion du processus : SQLite recopie son journal et le range

    @staticmethod
    def _sync(conn):
        with conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (SYNC_KEY, str(int(time.time()))))

    @staticmethod
    def _trim(conn):
        """Journal devenu gros (base réécrite d'un bloc par la réplication, lecteurs toujours présents aux points de
        contrôle automatiques) : le recopier en entier dans la base et le remettre à zéro. Les écritures attendent le
        temps de cette recopie ; si la base est occupée, ce sera pour la ronde suivante."""
        try:
            large = os.path.getsize(config.DB_PATH + "-wal") > WAL_LIMIT
        except OSError:
            return
        if large:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()


_keeper = None
_keeper_lock = threading.Lock()
_release_at_exit = False


def keep_open():
    """Tient la base ouverte : à appeler par le processus qui sert le portail (app.py), après les migrations ; jamais par
    un outil en ligne de commande, qui ouvre et referme la base comme avant. Renvoie vrai si la base est tenue."""
    global _keeper, _release_at_exit
    with _keeper_lock:
        if _keeper is None or not _keeper.alive():
            if not _release_at_exit:
                atexit.register(release)        # arrêt du service : journal recopié et rangé, comme à chaque fermeture avant
                _release_at_exit = True
            keeper = _Keeper()
            try:
                keeper.thread.start()
            except RuntimeError as e:           # plus de fil disponible : le portail sert quand même, comme avant
                logging.getLogger(__name__).warning("base : connexion permanente impossible (%s)", e)
                return False
            _keeper = keeper
        keeper = _keeper
    keeper.ready.wait(35)
    return keeper.healthy


def release():
    """Rend la base : dernière synchronisation puis fermeture de la connexion permanente (arrêt du service, tests)."""
    global _keeper
    with _keeper_lock:
        keeper, _keeper = _keeper, None
    if keeper is not None and keeper.alive():
        keeper.halt.set()
        keeper.dirty.set()
        keeper.thread.join(40)


def relax(conn=None):
    """Écritures que l'agent renvoie de lui-même au contact suivant (présence, adresse, mesures) : validées sans attendre
    le disque, puis synchronisées dans la seconde par la connexion permanente. Sans cette connexion (ligne de commande,
    tests), tant qu'elle est en défaut, ou si une transaction est déjà ouverte (SQLite ne change pas de mode en cours de
    route), rien ne change : la validation attend le disque. Renvoie vrai si le relâchement est en place."""
    keeper, conn = _keeper, conn or db()
    if keeper is None or not keeper.healthy or not keeper.alive() or conn.in_transaction:
        return False
    conn.execute("PRAGMA synchronous = NORMAL")
    return True


def durable(conn=None):
    """Fin du relâchement sur cette connexion (hors transaction) : la prochaine validation attend le disque, et met du même
    coup à l'abri tout ce qui a été validé avant elle."""
    (conn or db()).execute("PRAGMA synchronous = FULL")


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
        from .builtin_scripts import seed as seed_scripts
        seed_scripts(conn, time.strftime("%Y-%m-%d %H:%M:%S"))
        # Un téléchargement interrompu par un redémarrage ne reprendra jamais.
        conn.execute("UPDATE win_apps SET status='error', error='Interrompu (redémarrage du service)' "
                     "WHERE status='downloading'")
        return conn.execute("PRAGMA user_version").fetchone()[0]
