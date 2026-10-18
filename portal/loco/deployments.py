"""Déploiements préparés puis confirmés : cibles figées, installeurs épinglés et résultats des agents."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile

from flask import abort, flash, g, render_template, request, session, url_for

from . import app, config, fleet, groups, winget
from .auth import audit, lock_access, login_required, require
from .db import db
from .util import back, sha256_of

MAX_APPS = 100
MAX_DEVICES = 500
ACTIVE = ('queued', 'sent', 'running')
STATES = {'queued': 'En attente du poste', 'sent': 'Transmis à l’agent', 'running': 'Installation en cours',
          'success': 'Réussi', 'failed': 'Échec', 'cancelled': 'Annulé', 'skipped': 'Non déployé'}
FIELDS = ('id', 'slug', 'name', 'version', 'kind', 'args', 'detect', 'source_url', 'expected_sha256')


def _owner():
    return hashlib.sha256(session['csrf'].encode()).hexdigest()


def _get(deployment_id):
    row = db().execute('SELECT * FROM deployments WHERE id=?', (deployment_id,)).fetchone()
    if row is None or row['state'] == 'draft' and row['user_id'] != g.user['id']:
        abort(404)
    return row


def _selection(values):
    apps = list(dict.fromkeys(values.getlist('app')))
    if len(apps) > MAX_APPS or any(not config.SLUG_RE.fullmatch(slug) for slug in apps):
        abort(400, 'Sélection de logiciels invalide.')
    return {'apps': apps, 'software_groups': groups.identifiers(values.getlist('software_group')),
            'devices': groups.identifiers(values.getlist('device')),
            'device_groups': groups.identifiers(values.getlist('device_group'))}


def _resolve(selection):
    selected_apps, selected_devices = {}, {}
    origins = {'software': [], 'devices': []}
    for slug in selection['apps']:
        row = db().execute('SELECT * FROM win_apps WHERE slug=?', (slug,)).fetchone()
        if row is None:
            abort(400, 'Un logiciel sélectionné a été supprimé.')
        selected_apps[row['id']] = row
    for gid in selection['software_groups']:
        group = groups._group('software', gid)
        origins['software'].append(group['name'])
        members = db().execute('SELECT a.* FROM win_apps a JOIN software_group_members m ON m.app_id=a.id '
                               'WHERE m.group_id=? ORDER BY m.position,a.name', (gid,)).fetchall()
        if not members:
            abort(400, f"Le groupe de logiciels « {group['name']} » est vide.")
        selected_apps.update((row['id'], row) for row in members)
    for dev_id in selection['devices']:
        row = fleet._get(dev_id)
        selected_devices[row['id']] = row
    for gid in selection['device_groups']:
        group = groups._group('device', gid)
        origins['devices'].append(group['name'])
        members = db().execute("SELECT d.* FROM devices d JOIN device_group_members m ON m.device_id=d.id "
                               "WHERE m.group_id=? AND d.state!='removed' ORDER BY d.hostname", (gid,)).fetchall()
        if not members:
            abort(400, f"Le groupe de machines « {group['name']} » est vide.")
        selected_devices.update((row['id'], row) for row in members)
    if len(selected_apps) > MAX_APPS or len(selected_devices) > MAX_DEVICES:
        abort(400, 'Ce déploiement est trop volumineux : répartissez-le en plusieurs groupes.')
    return list(selected_apps.values()), list(selected_devices.values()), origins


def _target_reason(device):
    if device['os_family'] != 'windows':
        return 'Logiciels Windows : système non compatible'
    if device['state'] != 'active':
        return 'Agent en cours de retrait'
    return ''


def _require_bulk(selection, devices):
    bulk = bool(selection['device_groups']) or len(devices) > 1
    if bulk:
        require('fleet.bulk_install')
    return bulk


@app.route('/windows-apps/deployer')
@login_required
def deployment_compose():
    selected = _selection(request.args)
    store_id = request.args.get('package_id', '')
    store_info, error = None, ''
    if store_id:
        if not winget.ID_RE.fullmatch(store_id):
            abort(400)
        known = db().execute('SELECT slug FROM win_apps WHERE winget_id=? ORDER BY id LIMIT 1', (store_id,)).fetchone()
        if known:
            selected['apps'] = list(dict.fromkeys([*selected['apps'], known['slug']]))
            store_id = ''
        else:
            require('store.import')
            try:
                store_info = winget.describe(store_id)
            except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
                error = f'Ce logiciel ne peut pas être importé : {e}'
    return render_template('deployment_compose.html', selected=selected, store_id=store_id, store_info=store_info,
                           error=error, apps=db().execute('SELECT * FROM win_apps ORDER BY name COLLATE NOCASE').fetchall(),
                           software_groups=groups.listing('software'), device_groups=groups.listing('device'),
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE").fetchall())


@app.route('/windows-apps/deploiements/preparer', methods=['POST'])
@login_required
def deployment_preview():
    selected = _selection(request.form)
    apps, devices, origins = _resolve(selected)
    if not devices or not any(not _target_reason(device) for device in devices):
        abort(400, 'Sélectionnez au moins un appareil Windows actif.')
    bulk = _require_bulk(selected, devices)
    package_id = request.form.get('package_id', '')
    if package_id:
        if not winget.ID_RE.fullmatch(package_id):
            abort(400)
        require('store.import')
        try:
            imported = winget.import_package(package_id)
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
            flash(f'Import impossible : {e}', 'error')
            return back('deployment_compose', package_id=package_id)
        apps = list({a['id']: a for a in [*apps, imported]}.values())
    if not apps or len(apps) > MAX_APPS:
        abort(400, 'Sélectionnez entre un et cent logiciels.')
    conn = lock_access('fleet.install')
    if bulk:
        require('fleet.bulk_install')
    # Relire sous verrou après l'éventuel téléchargement du manifeste.
    snapshots = []
    for selected_app in apps:
        row = conn.execute('SELECT * FROM win_apps WHERE id=?', (selected_app['id'],)).fetchone()
        if row is None:
            abort(409, 'Un logiciel a changé : préparez à nouveau le déploiement.')
        snapshot = {key: row[key] for key in FIELDS}
        snapshot.update(sha256=row['sha256'] or row['expected_sha256'],
                        filename=row['filename'] or row['source_filename'])
        snapshots.append(snapshot)
    targets = [{'id': d['id'], 'uid': d['uid'], 'hostname': d['hostname'], 'os_family': d['os_family'],
                'reason': _target_reason(d)} for d in devices]
    title = request.form.get('title', '').strip()[:100] or ('Déploiement · ' + ', '.join(a['name'] for a in apps))[:100]
    plan = {'apps': snapshots, 'devices': targets, 'groups': origins, 'bulk': bulk,
            'force': request.form.get('force') == '1'}
    deployment_id = secrets.token_hex(16)
    conn.execute('INSERT INTO deployments(id,title,user_id,created_by,pw_version,owner,created_at,expires,plan) '
                 'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, title, g.user['id'], g.user['username'], g.user['pw_version'],
                 _owner(), fleet._now(), time.time() + 3600, json.dumps(plan)))
    audit('deploiement.preparation', f'{title} : {len(apps)} logiciel(s), {len(devices)} cible(s)', commit=False)
    conn.commit()
    return back('deployment_detail', deployment_id=deployment_id)


def _current(snapshot):
    row = db().execute('SELECT * FROM win_apps WHERE id=?', (snapshot['id'],)).fetchone()
    if row is None:
        return None, 'Logiciel supprimé du catalogue'
    if any(row[key] != snapshot[key] for key in FIELDS):
        return row, 'Fiche modifiée depuis la prévisualisation : préparez à nouveau le déploiement'
    if row['status'] != 'ready':
        return row, row['error'] if row['status'] == 'error' else 'Téléchargement de l’installeur en cours'
    if not snapshot['sha256'] or row['sha256'] != snapshot['sha256'] or row['filename'] != snapshot['filename']:
        return row, 'Installeur modifié : préparez à nouveau le déploiement'
    return row, ''


def _state(row, targets):
    if row['state'] == 'draft':
        return 'expired' if row['expires'] < time.time() else 'draft'
    states = [t['state'] for t in targets]
    if any(state in ACTIVE for state in states):
        return 'cancelling' if row['cancel_requested'] else 'running'
    if 'failed' in states:
        return 'failed'
    if 'cancelled' in states or row['cancel_requested']:
        return 'cancelled'
    return 'success'


def _targets(deployment_id):
    rows = db().execute('SELECT t.*,c.state AS command_state,COALESCE(c.output,t.last_output) AS output,c.finished_at,c.sent_at,d.state AS device_state '
                        'FROM deployment_targets t LEFT JOIN device_commands c ON c.id=t.command_id '
                        'LEFT JOIN devices d ON d.id=t.device_id WHERE t.deployment_id=? ORDER BY t.hostname,t.id', (deployment_id,)).fetchall()
    return [{**dict(row), 'state': row['command_state'] or row['last_state'] or ('cancelled' if row['apps'] != '[]' else 'skipped'),
             'apps': json.loads(row['apps']), 'skipped': json.loads(row['skipped'])} for row in rows]


@app.route('/windows-apps/deploiements')
@login_required
def deployments():
    db().execute("BEGIN IMMEDIATE")
    cleanup_artifacts(db())
    db().commit()
    total = db().execute("SELECT COUNT(*) FROM deployments WHERE state!='draft' OR user_id=?", (g.user['id'],)).fetchone()[0]
    pages = max(1, (total + 49) // 50)
    try:
        page = min(pages, max(1, int(request.args.get('page', '1'))))
    except ValueError:
        page = 1
    rows = db().execute("SELECT * FROM deployments WHERE state!='draft' OR user_id=? ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET ?",
                        (g.user['id'], (page - 1) * 50)).fetchall()
    campaigns = []
    for row in rows:
        targets = _targets(row['id'])
        plan = json.loads(row['plan'])
        campaigns.append({'row': row, 'state': _state(row, targets), 'devices': len(plan['devices']),
                          'apps': len(plan['apps']), 'finished': sum(t['state'] not in ACTIVE for t in targets)})
    return render_template('deployments.html', campaigns=campaigns, total=total, page=page, pages=pages)


@app.route('/windows-apps/deploiements/<deployment_id>')
@login_required
def deployment_detail(deployment_id):
    row = _get(deployment_id)
    plan = json.loads(row['plan'])
    apps = []
    for snapshot in plan['apps']:
        current, problem = _current(snapshot) if row['state'] == 'draft' else (None, '')
        apps.append({'snapshot': snapshot, 'current': current, 'problem': problem})
    targets = _targets(deployment_id)
    state = _state(row, targets)
    confirmable = (state == 'draft' and all(not a['problem'] for a in apps) and row['user_id'] == g.user['id']
                   and row['owner'] == _owner() and row['pw_version'] == g.user['pw_version'])
    signature = hashlib.sha256(json.dumps([state, [(a['current']['status'] if a['current'] else '', a['problem']) for a in apps],
                                          [(t['id'], t['state'], t['output']) for t in targets]]).encode()).hexdigest()[:16]
    if request.headers.get('X-Requested-With') == 'fetch':
        return {'signature': signature}
    return render_template('deployment_detail.html', deployment=row, plan=plan, apps=apps, targets=targets,
                           state=state, states=STATES, confirmable=confirmable, signature=signature,
                           recompose=url_for("deployment_compose", app=[a['slug'] for a in plan['apps']],
                                             device=[d['id'] for d in plan['devices']]))


def _pin(row):
    """Version adressée par son contenu : une mise à jour/suppression du catalogue ne remplace pas l'installeur confirmé."""
    digest = row['sha256']
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Installeur sans empreinte SHA-256 valide.')
    source = Path(config.WIN_DIR, row['slug'], row['filename'])
    if not source.is_file() or sha256_of(str(source)) != digest:
        raise ValueError(f"Installeur de {row['name']} absent ou altéré.")
    directory = Path(config.WIN_DIR, '_deployments', digest)
    directory.mkdir(parents=True, exist_ok=True)
    name = 'installer' + (Path(row['filename']).suffix.lower() or '.bin')
    if not re.fullmatch(r'installer\.[a-z0-9]{1,8}', name):
        name = 'installer.bin'
    destination = directory / name
    # tarfile du serveur Windows conserve autrement des liens que son restaurateur refuse.
    # Sous Linux, loco-backup matérialise les liens physiques pendant l'archivage.
    if not destination.exists() and not config.WINDOWS:
        try:
            os.link(source, destination)
        except OSError:
            pass
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(prefix='.copy-', dir=directory)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if sha256_of(str(destination)) != digest:
        raise ValueError('Version conservée altérée : déploiement refusé.')
    payload = fleet.app_payload(row)
    payload.update(url=f'/win/files/_deployments/{digest}/{name}', detect=row['detect'])
    return payload


def cleanup_artifacts(conn):
    """Cache des versions : garder les fichiers des commandes actives, purger les autres après une heure."""
    now = time.time()
    last = conn.execute("SELECT value FROM settings WHERE key='deployment_gc'").fetchone()
    try:
        if last and float(last[0]) > now - 3600:
            return
    except ValueError:
        pass
    needed = set()
    for row in conn.execute("SELECT params FROM device_commands WHERE state IN ('queued','sent','running') AND action='install-app'"):
        params = json.loads(row[0])
        needed.update(a['sha256'] for a in params.get('app_snapshots', []))
    root = Path(config.WIN_DIR, '_deployments')
    if root.is_dir():
        for directory in root.iterdir():
            if not re.fullmatch('[0-9a-f]{64}', directory.name) or directory.name in needed or directory.is_symlink():
                continue
            try:
                if directory.stat().st_mtime < now - 3600:
                    shutil.rmtree(directory)
            except OSError:
                pass  # Windows peut encore avoir un fichier ouvert pour un téléchargement terminé.
    conn.execute("INSERT INTO settings(key,value) VALUES ('deployment_gc',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),))


def _already_installed(dev, row):
    age = fleet._ago(dev['last_scan'])
    if age is None or age > datetime.timedelta(days=1) or fleet.inventory(dev).get('error'):
        return False
    _, installed, outdated = fleet.catalog_state(fleet.inventory(dev), [row])[0]
    return bool(installed and installed != '?' and row['version'] and not outdated)


def _pending_apps(dev):
    result = set()
    for command in fleet.pending(dev['id']):
        if command['action'] != 'install-app':
            continue
        params = json.loads(command['params'])
        if params.get('slug'):
            row = db().execute('SELECT sha256 FROM win_apps WHERE slug=?', (params['slug'],)).fetchone()
            if row:
                result.add((params['slug'], row['sha256']))
        result.update((a['slug'], a['sha256']) for a in params.get('app_snapshots', []))
    return result


@app.route('/windows-apps/deploiements/<deployment_id>/confirmer', methods=['POST'])
@login_required
def deployment_confirm(deployment_id):
    conn = lock_access('fleet.install')
    row = _get(deployment_id)
    if row['user_id'] != g.user['id'] or row['owner'] != _owner() or row['pw_version'] != g.user['pw_version']:
        abort(403)
    if row['state'] != 'draft':
        return back('deployment_detail', deployment_id=deployment_id)
    if row['expires'] < time.time():
        abort(409, 'Prévisualisation expirée : préparez à nouveau le déploiement.')
    plan = json.loads(row['plan'])
    if plan['bulk']:
        require('fleet.bulk_install')
    ready = []
    for snapshot in plan['apps']:
        current, problem = _current(snapshot)
        if problem:
            abort(409, problem)
        ready.append(current)
    devices = []
    for target in plan['devices']:
        if target['reason']:
            devices.append((target, None))
            continue
        dev = conn.execute('SELECT * FROM devices WHERE id=?', (target['id'],)).fetchone()
        if dev is None or dev['uid'] != target['uid'] or _target_reason(dev):
            abort(409, 'Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.')
        devices.append((target, dev))
    pinned = {}
    queued_count = 0
    try:
        for target, dev in devices:
            required, skipped = [], []
            if dev:
                pending_apps = _pending_apps(dev)
                for software in ready:
                    if (software['slug'], software['sha256']) in pending_apps:
                        skipped.append({'name': software['name'], 'reason': 'Déjà en attente sur ce poste'})
                    elif not plan['force'] and _already_installed(dev, software):
                        skipped.append({'name': software['name'], 'reason': 'Version déjà installée'})
                    else:
                        if software['id'] not in pinned:
                            pinned[software['id']] = _pin(software)
                        required.append(pinned[software['id']])
            command_id = None
            if required:
                params = {'app_snapshots': required, 'deployment_id': deployment_id, 'bulk_install': plan['bulk'], 'force': plan['force']}
                command_id = conn.execute('INSERT INTO device_commands(device_id,action,params,created_at,created_by) '
                                          "VALUES (?,'install-app',?,?,?)", (dev['id'], json.dumps(params, sort_keys=True), fleet._now(), g.user['username'])).lastrowid
                queued_count += 1
            conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason) '
                         'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, target['id'] if dev else None, target['uid'], target['hostname'],
                         target['os_family'], command_id, json.dumps(required), json.dumps(skipped), target['reason']))
    except (OSError, ValueError) as e:
        conn.rollback()
        flash(f'Déploiement non lancé : {e}', 'error')
        return back('deployment_detail', deployment_id=deployment_id)
    conn.execute("UPDATE deployments SET state='queued',confirmed_at=? WHERE id=?", (fleet._now(), deployment_id))
    cleanup_artifacts(conn)
    audit('deploiement.confirmation', f"{row['title']} : {queued_count} poste(s), {len(ready)} logiciel(s), {deployment_id}", commit=False)
    conn.commit()
    flash(f'Déploiement confirmé sur {queued_count} poste(s). Les agents exécutent les installations à leur prochain contact.', 'ok')
    return back('deployment_detail', deployment_id=deployment_id)


@app.route('/windows-apps/deploiements/<deployment_id>/annuler', methods=['POST'])
@login_required
def deployment_cancel(deployment_id):
    conn = lock_access('fleet.install')
    row = _get(deployment_id)
    plan = json.loads(row['plan'])
    if plan['bulk']:
        require('fleet.bulk_install')
    if row['state'] == 'draft':
        conn.execute("UPDATE deployments SET state='cancelled',cancel_requested=1 WHERE id=?", (deployment_id,))
    else:
        conn.execute("UPDATE device_commands SET state='cancelled',finished_at=?,output='Annulé avant transmission à l’agent.' "
                     "WHERE state='queued' AND id IN (SELECT command_id FROM deployment_targets WHERE deployment_id=?)", (fleet._now(), deployment_id))
        conn.execute('UPDATE deployments SET cancel_requested=1 WHERE id=?', (deployment_id,))
    cleanup_artifacts(conn)
    audit('deploiement.annulation', f"{row['title']} : {deployment_id}", commit=False)
    conn.commit()
    flash('Commandes non transmises annulées. Les installations déjà reçues par un agent peuvent se terminer.', 'ok')
    return back('deployment_detail', deployment_id=deployment_id)
