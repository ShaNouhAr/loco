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

from flask import abort, flash, g, redirect, render_template, request, session, url_for

from . import android_apps, app, config, fleet, groups, scope, winget
from .i18n import L_, _, n_
from .auth import audit, can, lock_access, login_required, require
from .db import db
from .util import back, sha256_of, counted

MAX_APPS = 100
MAX_DEVICES = 500
MAX_WAVE_SIZE = 5000
ACTIVE = ('queued', 'sent', 'running')
WAITING = ACTIVE + ('planned',)     # planned : poste d'une vague pas encore commencée
STATES = {'planned': L_('Prévu dans une vague suivante'), 'queued': L_('En attente du poste'), 'sent': L_('Transmis à l’agent'), 'running': L_('Installation en cours'),
          'success': L_('Réussi'), 'failed': L_('Échec'), 'cancelled': L_('Annulé'), 'skipped': L_('Non déployé')}
FIELDS = ('id', 'slug', 'name', 'version', 'kind', 'args', 'detect', 'source_url', 'expected_sha256')


def _owner():
    return hashlib.sha256(session['csrf'].encode()).hexdigest()


def _in_scope(row):
    """Déploiement dont toutes les cibles sont dans le parc du compte courant (sinon il ne le voit pas)."""
    return scope.whole() or all(scope.allows(t['id']) for t in json.loads(row['plan'])['devices'] if t.get('id') is not None)


def _get(deployment_id):
    row = db().execute('SELECT * FROM deployments WHERE id=?', (deployment_id,)).fetchone()
    if row is None or row['state'] == 'draft' and row['user_id'] != g.user['id'] or not _in_scope(row):
        abort(404)
    return row


def _selection(values):
    apps = list(dict.fromkeys(values.getlist('app')))
    if len(apps) > MAX_APPS or any(not config.SLUG_RE.fullmatch(slug) for slug in apps):
        abort(400, _('Sélection de logiciels invalide.'))
    return {'apps': apps, 'software_groups': groups.identifiers(values.getlist('software_group')),
            'devices': groups.identifiers(values.getlist('device')),
            'device_groups': groups.identifiers(values.getlist('device_group'))}


def _resolve(selection):
    selected_apps, selected_devices = {}, {}
    origins = {'software': [], 'devices': []}
    for slug in selection['apps']:
        row = db().execute('SELECT * FROM win_apps WHERE slug=?', (slug,)).fetchone()
        if row is None:
            abort(400, _('Un logiciel sélectionné a été supprimé.'))
        selected_apps[row['id']] = row
    for gid in selection['software_groups']:
        group = groups._group('software', gid)
        origins['software'].append(group['name'])
        members = db().execute('SELECT a.* FROM win_apps a JOIN software_group_members m ON m.app_id=a.id '
                               'WHERE m.group_id=? ORDER BY m.position,a.name', (gid,)).fetchall()
        if not members:
            abort(400, _('Le groupe de logiciels « %(group)s » est vide.', group=group['name']))
        selected_apps.update((row['id'], row) for row in members)
    for dev_id in selection['devices']:
        row = fleet._get(dev_id)
        selected_devices[row['id']] = row
    for gid in selection['device_groups']:
        group = groups._group('device', gid)
        origins['devices'].append(group['name'])
        members = db().execute("SELECT d.* FROM devices d JOIN device_group_members m ON m.device_id=d.id "
                               "WHERE m.group_id=? AND d.state!='removed'" + scope.sql("d.id") + " ORDER BY d.hostname",
                               (gid,)).fetchall()
        if not members:
            abort(400, _('Le groupe de machines « %(group)s » est vide.', group=group['name']))
        selected_devices.update((row['id'], row) for row in members)
    if len(selected_apps) > MAX_APPS or len(selected_devices) > MAX_DEVICES:
        abort(400, _('Ce déploiement est trop volumineux : répartissez-le en plusieurs groupes.'))
    return list(selected_apps.values()), list(selected_devices.values()), origins


def _target_reason(device):
    if device['os_family'] != 'windows':
        return _('Logiciels Windows : système non compatible')
    if device['state'] != 'active':
        return _('Agent en cours de retrait')
    return ''


def _require_bulk(selection, devices):
    bulk = bool(selection['device_groups']) or len(devices) > 1
    if bulk:
        require('fleet.bulk_install')
    return bulk


# --------------------------------------------------------------------------- paquets Linux (store .deb/.rpm publiés)

PKG_ID_RE = re.compile(r'(deb|rpm):[A-Za-z0-9][A-Za-z0-9.+_-]{0,200}')
PKG_FAMILY = {'deb': 'debian', 'rpm': 'rhel'}


def _package_catalog():
    """Paquets publiés dans les dépôts loco (APT/RPM), dédupliqués par (type, nom) ; la version la plus récente est affichée."""
    from . import features, linux, rpmrepo
    off = features.disabled()
    items = {}

    def add(kind, name, version, arch, desc, repo):
        if not name or not PKG_ID_RE.fullmatch(f'{kind}:{name}'):
            return
        key = f'{kind}:{name}'
        it = items.get(key)
        if it is None:
            it = items[key] = {'id': key, 'kind': kind, 'family': PKG_FAMILY[kind], 'name': name,
                               'version': version, 'arch': arch, 'desc': desc, 'repositories': set(), 'archs': set()}
        elif fleet.version_key(version) > fleet.version_key(it['version']):
            it.update(version=version, arch=arch, desc=desc or it['desc'])
        it['repositories'].add(repo)
        it['archs'].add(arch)

    if 'apt' not in off:
        for d in linux.list_debs():
            add('deb', d['package'], d['version'], d['arch'], d.get('description', ''), d['repository'])
    if 'rpm' not in off:
        for p in rpmrepo.list_rpms():
            add('rpm', p['name'], f"{p['version']}-{p['release']}", p['arch'], p.get('summary', ''), p['repository'])
    return items


def _selection_pkg(values):
    packages = list(dict.fromkeys(values.getlist('package')))
    if len(packages) > MAX_APPS or any(not PKG_ID_RE.fullmatch(p) for p in packages):
        abort(400, _('Sélection de paquets invalide.'))
    return {'packages': packages, 'devices': groups.identifiers(values.getlist('device')),
            'device_groups': groups.identifiers(values.getlist('device_group'))}


def _resolve_pkg(selection):
    catalog = _package_catalog()
    chosen = []
    for pid in selection['packages']:
        it = catalog.get(pid)
        if it is None:
            abort(400, _('Un paquet sélectionné n’est plus publié.'))
        chosen.append(it)
    devices, origins = {}, {'software': [], 'devices': []}
    for dev_id in selection['devices']:
        row = fleet._get(dev_id)
        devices[row['id']] = row
    for gid in selection['device_groups']:
        group = groups._group('device', gid)
        origins['devices'].append(group['name'])
        members = db().execute("SELECT d.* FROM devices d JOIN device_group_members m ON m.device_id=d.id "
                               "WHERE m.group_id=? AND d.state!='removed'" + scope.sql("d.id") + " ORDER BY d.hostname",
                               (gid,)).fetchall()
        if not members:
            abort(400, _('Le groupe de machines « %(group)s » est vide.', group=group['name']))
        devices.update((row['id'], row) for row in members)
    if len(chosen) > MAX_APPS or len(devices) > MAX_DEVICES:
        abort(400, _('Ce déploiement est trop volumineux : répartissez-le en plusieurs groupes.'))
    return chosen, list(devices.values()), origins


def _target_reason_pkg(device, families):
    if device['os_family'] not in ('debian', 'rhel'):
        return _('Paquets Linux : système non compatible')
    if device['state'] != 'active':
        return _('Agent en cours de retrait')
    if device['os_family'] not in families:
        return _('Aucun paquet compatible avec ce système')
    return ''


@app.route('/fleet/deployments/new-linux')
@login_required
def deployment_compose_linux():
    selected = _selection_pkg(request.args)
    catalog = sorted(_package_catalog().values(), key=lambda i: (i['name'].lower(), i['kind']))
    for item in catalog:
        item['repositories'] = sorted(item['repositories'])
        item['archs'] = sorted(item['archs'])
    return render_template('deployment_compose_linux.html', selected=selected, packages=catalog,
                           device_groups=groups.listing('device'), families=fleet.FAMILIES,
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed'" + scope.sql()
                                                + " ORDER BY hostname COLLATE NOCASE").fetchall())


@app.route('/fleet/deployments/prepare-linux', methods=['POST'])
@login_required
def deployment_preview_linux():
    selected = _selection_pkg(request.form)
    packages, devices, origins = _resolve_pkg(selected)
    if not packages:
        abort(400, _('Sélectionnez au moins un paquet.'))
    families = {p['family'] for p in packages}
    if not devices or not any(not _target_reason_pkg(d, families) for d in devices):
        abort(400, _('Sélectionnez au moins un poste Linux compatible.'))
    bulk = bool(selected['device_groups']) or len(devices) > 1
    if bulk:
        require('fleet.bulk_install')
    conn = lock_access('fleet.install')
    if bulk:
        require('fleet.bulk_install')
    snapshots = [{'name': p['name'], 'kind': p['kind'], 'family': p['family'], 'version': p['version'],
                  'arch': p['arch'], 'repositories': sorted(p['repositories'])} for p in packages]
    targets = [{'id': d['id'], 'uid': d['uid'], 'hostname': d['hostname'], 'os_family': d['os_family'],
                'reason': _target_reason_pkg(d, families)} for d in devices]
    title = request.form.get('title', '').strip()[:100] or ('Paquets Linux · ' + ', '.join(p['name'] for p in packages))[:100]
    plan = {'kind': 'linux', 'apps': snapshots, 'devices': targets, 'groups': origins, 'bulk': bulk, 'force': False}
    deployment_id = secrets.token_hex(16)
    conn.execute('INSERT INTO deployments(id,title,user_id,created_by,pw_version,owner,created_at,expires,plan) '
                 'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, title, g.user['id'], g.user['username'], g.user['pw_version'],
                 _owner(), fleet._now(), time.time() + 3600, json.dumps(plan)))
    audit('deploiement.preparation', f"{title} : {counted(len(packages), 'paquet')}, {counted(len(devices), 'cible')}", commit=False)
    conn.commit()
    return back('deployment_detail', deployment_id=deployment_id)


def _confirm_linux(conn, deployment_id, plan, rollout):
    """Confirmation d'un déploiement de paquets Linux : une commande install-package par poste compatible."""
    catalog = _package_catalog()
    ready = []
    for snap in plan['apps']:
        if catalog.get(f"{snap['kind']}:{snap['name']}") is None:
            abort(409, _('Le paquet %(name)s n’est plus publié : préparez à nouveau le déploiement.', name=snap['name']))
        ready.append(snap)
    families = {p['family'] for p in ready}
    queued, entries = 0, []
    for target in plan['devices']:
        if target['reason']:
            conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason) '
                         'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, None, target['uid'], target['hostname'],
                         target['os_family'], None, '[]', '[]', target['reason']))
            continue
        dev = conn.execute('SELECT * FROM devices WHERE id=?', (target['id'],)).fetchone()
        if dev is None or dev['uid'] != target['uid'] or _target_reason_pkg(dev, families) or not scope.allows(dev['id']):
            abort(409, _('Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.'))
        installed = [p for p in ready if p['family'] == dev['os_family']]
        params = None
        if installed:
            params = {'packages': [p['name'] for p in installed], 'deployment_id': deployment_id,
                      'bulk_install': plan['bulk'], 'kind': 'linux'}
            queued += 1
        entries.append({'target': target, 'dev': dev, 'action': 'install-package', 'params': params, 'apps': installed, 'skipped': []})
    _add_targets(conn, deployment_id, entries, rollout, g.user['username'])
    return queued, len(ready)


# --------------------------------------------------------------------------- applications Android (catalogue loco)

def _selection_android(values):
    apps = list(dict.fromkeys(values.getlist('app')))
    if len(apps) > MAX_APPS or any(not a.isdigit() for a in apps):
        abort(400, _('Sélection d’applications invalide.'))
    return {'apps': [int(a) for a in apps], 'devices': groups.identifiers(values.getlist('device')),
            'device_groups': groups.identifiers(values.getlist('device_group'))}


def _devices_of(selection):
    devices, origins = {}, {'software': [], 'devices': []}
    for dev_id in selection['devices']:
        row = fleet._get(dev_id)
        devices[row['id']] = row
    for gid in selection['device_groups']:
        group = groups._group('device', gid)
        origins['devices'].append(group['name'])
        members = db().execute("SELECT d.* FROM devices d JOIN device_group_members m ON m.device_id=d.id "
                               "WHERE m.group_id=? AND d.state!='removed'" + scope.sql("d.id") + " ORDER BY d.hostname",
                               (gid,)).fetchall()
        if not members:
            abort(400, _('Le groupe de machines « %(group)s » est vide.', group=group['name']))
        devices.update((row['id'], row) for row in members)
    if len(devices) > MAX_DEVICES:
        abort(400, _('Ce déploiement est trop volumineux : répartissez-le en plusieurs groupes.'))
    return list(devices.values()), origins


def _target_reason_android(device):
    if device['os_family'] != 'android':
        return _('Applications Android : système non compatible')
    if device['state'] != 'active':
        return _('Agent en cours de retrait')
    if not android_apps.ready_agent(device):
        return _('Application loco à mettre à jour sur l’appareil (%(version)s ou plus récente)', version='2.139.0')
    return ''


@app.route('/fleet/deployments/new-android')
@login_required
def deployment_compose_android():
    selected = _selection_android(request.args)
    view = []
    for row in android_apps.catalog_rows():
        problem = ''
        if row['store'] == 'fdroid':
            problem = android_apps.snapshot(row)[1]
        view.append({'row': row, 'problem': problem})
    return render_template('deployment_compose_android.html', selected=selected, apps=view,
                           device_groups=groups.listing('device'), families=fleet.FAMILIES,
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed'" + scope.sql()
                                                + " ORDER BY hostname COLLATE NOCASE").fetchall(),
                           ready=android_apps.ready_agent)


@app.route('/fleet/deployments/prepare-android', methods=['POST'])
@login_required
def deployment_preview_android():
    selected = _selection_android(request.form)
    snapshots = []
    for app_id in selected['apps']:
        row = db().execute('SELECT * FROM android_apps WHERE id=?', (app_id,)).fetchone()
        if row is None:
            abort(400, _('Une application sélectionnée a été retirée du catalogue.'))
        snap, problem = android_apps.snapshot(row)
        if problem:
            abort(400, _('%(name)s : %(problem)s', name=row['name'], problem=problem))
        snapshots.append(snap)
    if not snapshots:
        abort(400, _('Sélectionnez au moins une application.'))
    devices, origins = _devices_of(selected)
    if not devices or not any(not _target_reason_android(d) for d in devices):
        abort(400, _('Sélectionnez au moins un appareil Android compatible.'))
    bulk = bool(selected['device_groups']) or len(devices) > 1
    if bulk:
        require('fleet.bulk_install')
    conn = lock_access('fleet.install')
    if bulk:
        require('fleet.bulk_install')
    targets = []
    for d in devices:
        reason = _target_reason_android(d)
        if not reason and not any(android_apps.item_for(d, s, True)[0] for s in snapshots):
            reason = _('Aucune version compatible avec cet appareil')
        targets.append({'id': d['id'], 'uid': d['uid'], 'hostname': d['hostname'], 'os_family': d['os_family'], 'reason': reason})
    title = request.form.get('title', '').strip()[:100] or ('Applications Android · ' + ', '.join(s['name'] for s in snapshots))[:100]
    plan = {'kind': 'android', 'apps': snapshots, 'devices': targets, 'groups': origins, 'bulk': bulk,
            'force': request.form.get('force') == '1'}
    deployment_id = secrets.token_hex(16)
    conn.execute('INSERT INTO deployments(id,title,user_id,created_by,pw_version,owner,created_at,expires,plan) '
                 'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, title, g.user['id'], g.user['username'], g.user['pw_version'],
                 _owner(), fleet._now(), time.time() + 3600, json.dumps(plan)))
    audit('deploiement.preparation', f"{title} : {counted(len(snapshots), 'application')}, {counted(len(devices), 'cible')}", commit=False)
    conn.commit()
    return back('deployment_detail', deployment_id=deployment_id)


def _confirm_android(conn, deployment_id, plan, rollout):
    """Une commande install-apk par appareil : la version compatible de chaque application, déjà installée = ignorée."""
    queued, entries, fetch = 0, [], {}
    for target in plan['devices']:
        if target['reason']:
            conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason) '
                         'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, None, target['uid'], target['hostname'],
                         target['os_family'], None, '[]', '[]', target['reason']))
            continue
        dev = conn.execute('SELECT * FROM devices WHERE id=?', (target['id'],)).fetchone()
        if dev is None or dev['uid'] != target['uid'] or _target_reason_android(dev) or not scope.allows(dev['id']):
            abort(409, _('Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.'))
        waiting = {a.get('package') for c in fleet.pending(dev['id']) if c['action'] == 'install-apk'
                   for a in json.loads(c['params']).get('apps', [])}
        items, skipped = [], []
        for snap in plan['apps']:
            item, why = android_apps.item_for(dev, snap, plan['force'])
            if item is None:
                skipped.append({'name': snap['name'], 'reason': why})
            elif item['package'] in waiting:
                skipped.append({'name': snap['name'], 'reason': 'Déjà en attente sur cet appareil'})
            else:
                items.append(item)
                if item['store'] == 'fdroid':
                    fetch[item['sha256']] = item
        params = None
        if items:
            params = {'apps': items, 'deployment_id': deployment_id, 'bulk_install': plan['bulk'], 'kind': 'android'}
            queued += 1
        entries.append({'target': target, 'dev': dev, 'action': 'install-apk', 'params': params, 'apps': items, 'skipped': skipped})
    _add_targets(conn, deployment_id, entries, rollout, g.user['username'])
    for item in fetch.values():
        android_apps.apk_state(item)            # loco télécharge et vérifie les APK sans attendre le premier appareil
    return queued, len(plan['apps'])


@app.route('/fleet/deployments/new')
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
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed'" + scope.sql()
                                                + " ORDER BY hostname COLLATE NOCASE").fetchall())


@app.route('/fleet/deployments/prepare', methods=['POST'])
@login_required
def deployment_preview():
    selected = _selection(request.form)
    apps, devices, origins = _resolve(selected)
    if not devices or not any(not _target_reason(device) for device in devices):
        abort(400, _('Sélectionnez au moins un appareil Windows actif.'))
    bulk = _require_bulk(selected, devices)
    package_id = request.form.get('package_id', '')
    if package_id:
        if not winget.ID_RE.fullmatch(package_id):
            abort(400)
        require('store.import')
        try:
            imported = winget.import_package(package_id)
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
            flash(_('Import impossible : %(e)s', e=e), 'error')
            return back('deployment_compose', package_id=package_id)
        apps = list({a['id']: a for a in [*apps, imported]}.values())
    if not apps or len(apps) > MAX_APPS:
        abort(400, _('Sélectionnez entre un et cent logiciels.'))
    conn = lock_access('fleet.install')
    if bulk:
        require('fleet.bulk_install')
    # Relire sous verrou après l'éventuel téléchargement du manifeste.
    snapshots = []
    for selected_app in apps:
        row = conn.execute('SELECT * FROM win_apps WHERE id=?', (selected_app['id'],)).fetchone()
        if row is None:
            abort(409, _('Un logiciel a changé : préparez à nouveau le déploiement.'))
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
    audit('deploiement.preparation', f"{title} : {counted(len(apps), 'logiciel')}, {counted(len(devices), 'cible')}", commit=False)
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
    if any(state in WAITING for state in states):
        if row['cancel_requested']:
            return 'cancelling'
        return 'paused' if _rollout(row).get('paused') else 'running'
    if 'failed' in states:
        return 'failed'
    if 'cancelled' in states or row['cancel_requested']:
        return 'cancelled'
    return 'success'


def _targets(deployment_id):
    rows = db().execute('SELECT t.*,c.state AS command_state,COALESCE(c.output,t.last_output) AS output,c.finished_at,c.sent_at,c.progress,d.state AS device_state '
                        'FROM deployment_targets t LEFT JOIN device_commands c ON c.id=t.command_id '
                        'LEFT JOIN devices d ON d.id=t.device_id WHERE t.deployment_id=? ORDER BY t.hostname,t.id', (deployment_id,)).fetchall()
    out = []
    for row in rows:
        state = row['command_state'] or row['last_state'] or ('cancelled' if row['apps'] != '[]' else 'skipped')
        out.append({**dict(row), 'state': state, 'apps': json.loads(row['apps']), 'skipped': json.loads(row['skipped']),
                    'live': fleet.progress_view(row['progress']) if state == 'running' else None})
    return out


# Filtres de la liste : clé -> (libellé, états de déploiement retenus, classe de la pastille).
FILTERS = {'en-cours': (L_('En cours'), ('running', 'cancelling', 'paused'), 'warn'), 'a-confirmer': (L_('À confirmer'), ('draft',), 'warn'),
           'erreurs': (L_('Avec erreurs'), ('failed',), 'err'), 'termines': (L_('Terminés'), ('success', 'cancelled', 'expired'), 'ok')}


def _progress(targets):
    """Postes réussis, en attente, en échec et non concernés (non déployés ou annulés)."""
    counts = {'success': 0, 'active': 0, 'failed': 0, 'other': 0}
    for t in targets:
        key = 'active' if t['state'] in WAITING else t['state'] if t['state'] in ('success', 'failed') else 'other'
        counts[key] += 1
    counts['total'] = len(targets)
    return counts


@app.route('/fleet/deployments')
@login_required
def deployments():
    db().execute("BEGIN IMMEDIATE")
    cleanup_artifacts(db())
    db().commit()
    wanted = request.args.get('etat', '')
    if wanted not in FILTERS:
        wanted = ''
    rows = [r for r in db().execute("SELECT * FROM deployments WHERE state!='draft' OR user_id=? ORDER BY created_at DESC,id DESC",
                                    (g.user['id'],)).fetchall() if _in_scope(r)]
    campaigns, counts = [], dict.fromkeys(FILTERS, 0)
    for row in rows:
        targets = _targets(row['id'])
        plan = json.loads(row['plan'])
        state = _state(row, targets)
        for key, (_label, states, _cls) in FILTERS.items():
            counts[key] += state in states
        if wanted and state not in FILTERS[wanted][1]:
            continue
        campaigns.append({'row': row, 'state': state, 'devices': len(plan['devices']), 'apps': len(plan['apps']),
                          'names': [a['name'] for a in plan['apps']], 'progress': _progress(targets),
                          'kind': plan.get('kind', 'windows')})
    total = len(campaigns)
    pages = max(1, (total + 49) // 50)
    try:
        page = min(pages, max(1, int(request.args.get('page', '1'))))
    except ValueError:
        page = 1
    return render_template('deployments.html', campaigns=campaigns[(page - 1) * 50:page * 50], total=total, page=page,
                           pages=pages, all_count=len(rows), counts=counts, filters=FILTERS, wanted=wanted)


@app.route('/fleet/deployments/<deployment_id>')
@login_required
def deployment_detail(deployment_id):
    row = _get(deployment_id)
    plan = json.loads(row['plan'])
    kind = plan.get('kind', 'windows')
    apps = []
    for snapshot in plan['apps']:
        if kind in ('linux', 'android'):
            current, problem = None, ''
        else:
            current, problem = _current(snapshot) if row['state'] == 'draft' else (None, '')
        apps.append({'snapshot': snapshot, 'current': current, 'problem': problem})
    targets = _targets(deployment_id)
    state = _state(row, targets)
    blocked = ''
    if state == 'draft':
        if row['user_id'] != g.user['id'] or row['owner'] != _owner() or row['pw_version'] != g.user['pw_version']:
            blocked = 'Cette préparation a été faite depuis une autre connexion : préparez à nouveau le déploiement.'
        elif any(a['problem'] for a in apps):
            blocked = 'Un installeur n’est pas prêt (voir les logiciels ci-dessous) : la confirmation sera possible ensuite.'
        elif plan['bulk'] and not can('fleet.bulk_install'):
            blocked = 'Le droit de déploiement sur plusieurs postes est requis pour confirmer.'
    confirmable = state == 'draft' and not blocked
    signature = hashlib.sha256(json.dumps([state, row['waves'], [(a['current']['status'] if a['current'] else '', a['problem']) for a in apps],
                                          [(t['id'], t['state'], t['output'], t['progress']) for t in targets]]).encode()).hexdigest()[:16]
    if request.headers.get('X-Requested-With') == 'fetch':
        return {'signature': signature}
    if kind == 'update':
        recompose = url_for('fleet')
    elif kind == 'android':
        recompose = url_for("deployment_compose_android", app=[a['id'] for a in plan['apps']],
                            device=[d['id'] for d in plan['devices']])
    elif kind == 'linux':
        recompose = url_for("deployment_compose_linux", package=[f"{a['kind']}:{a['name']}" for a in plan['apps']],
                            device=[d['id'] for d in plan['devices']])
    else:
        recompose = url_for("deployment_compose", app=[a['slug'] for a in plan['apps']],
                            device=[d['id'] for d in plan['devices']])
    return render_template('deployment_detail.html', deployment=row, plan=plan, apps=apps, targets=targets,
                           state=state, states=STATES, confirmable=confirmable, blocked=blocked, signature=signature,
                           progress=_progress(targets), families=fleet.FAMILIES, kind=kind, recompose=recompose,
                           rollout=_rollout_view(row, targets), device_groups=groups.listing('device'), default_size=_default_size(plan))


def _pin(row):
    """Version adressée par son contenu : une mise à jour/suppression du catalogue ne remplace pas l'installeur confirmé."""
    digest = row['sha256']
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError(_('Installeur sans empreinte SHA-256 valide.'))
    source = Path(config.WIN_DIR, row['slug'], row['filename'])
    if not source.is_file() or sha256_of(str(source)) != digest:
        raise ValueError(_('Installeur de %(row)s absent ou altéré.', row=row['name']))
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
        raise ValueError(_('Version conservée altérée : déploiement refusé.'))
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
    for row in conn.execute("SELECT pending FROM deployment_targets WHERE pending!=''"):      # vagues à venir
        needed.update(a['sha256'] for a in json.loads(row[0])['params'].get('app_snapshots', []))
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


@app.route('/fleet/deployments/<deployment_id>/confirm', methods=['POST'])
@login_required
def deployment_confirm(deployment_id):
    conn = lock_access('fleet.install')
    row = _get(deployment_id)
    if row['user_id'] != g.user['id'] or row['owner'] != _owner() or row['pw_version'] != g.user['pw_version']:
        abort(403)
    if row['state'] != 'draft':
        return back('deployment_detail', deployment_id=deployment_id)
    if row['expires'] < time.time():
        abort(409, _('Prévisualisation expirée : préparez à nouveau le déploiement.'))
    plan = json.loads(row['plan'])
    if plan['bulk']:
        require('fleet.bulk_install')
    try:
        rollout = _rollout_options(request.form)
    except ValueError as e:
        flash(str(e), 'error')
        return back('deployment_detail', deployment_id=deployment_id)
    if plan.get('kind') == 'android':
        try:
            queued_count, ready_n = _confirm_android(conn, deployment_id, plan, rollout)
        except (OSError, ValueError) as e:
            conn.rollback()
            flash(_('Déploiement non lancé : %(e)s', e=e), 'error')
            return back('deployment_detail', deployment_id=deployment_id)
        conn.execute("UPDATE deployments SET state='queued',confirmed_at=?,waves=? WHERE id=?", (fleet._now(), json.dumps(rollout) if rollout else '', deployment_id))
        audit('deploiement.confirmation', f"{row['title']} : {counted(queued_count, 'appareil')}, {counted(ready_n, 'application')}, {deployment_id}", commit=False)
        conn.commit()
        flash(n_("Déploiement confirmé sur %(num)s appareil. L'application loco l'installe à son prochain contact ; Android demande une confirmation sur l'appareil pour une première installation.",
                 "Déploiement confirmé sur %(num)s appareils. L'application loco les installe à son prochain contact ; Android demande une confirmation sur l'appareil pour une première installation.", queued_count), 'ok')
        return back('deployment_detail', deployment_id=deployment_id)
    if plan.get('kind') == 'linux':
        try:
            queued_count, ready_n = _confirm_linux(conn, deployment_id, plan, rollout)
        except (OSError, ValueError) as e:
            conn.rollback()
            flash(_('Déploiement non lancé : %(e)s', e=e), 'error')
            return back('deployment_detail', deployment_id=deployment_id)
        conn.execute("UPDATE deployments SET state='queued',confirmed_at=?,waves=? WHERE id=?", (fleet._now(), json.dumps(rollout) if rollout else '', deployment_id))
        audit('deploiement.confirmation', f"{row['title']} : {counted(queued_count, 'poste')}, {counted(ready_n, 'paquet')}, {deployment_id}", commit=False)
        conn.commit()
        flash(n_("Déploiement confirmé sur %(num)s poste. L'agent installe les paquets à son prochain contact.", 'Déploiement confirmé sur %(num)s postes. Les agents installent les paquets à leur prochain contact.', queued_count), 'ok')
        return back('deployment_detail', deployment_id=deployment_id)
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
        if dev is None or dev['uid'] != target['uid'] or _target_reason(dev) or not scope.allows(dev['id']):
            abort(409, _('Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.'))
        devices.append((target, dev))
    pinned = {}
    queued_count = 0
    entries = []
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
            params = None
            if required:
                params = {'app_snapshots': required, 'deployment_id': deployment_id, 'bulk_install': plan['bulk'], 'force': plan['force']}
                queued_count += 1
            entries.append({'target': target, 'dev': dev, 'action': 'install-app', 'params': params, 'apps': required, 'skipped': skipped})
        _add_targets(conn, deployment_id, entries, rollout, g.user['username'])
    except (OSError, ValueError) as e:
        conn.rollback()
        flash(_('Déploiement non lancé : %(e)s', e=e), 'error')
        return back('deployment_detail', deployment_id=deployment_id)
    conn.execute("UPDATE deployments SET state='queued',confirmed_at=?,waves=? WHERE id=?", (fleet._now(), json.dumps(rollout) if rollout else '', deployment_id))
    cleanup_artifacts(conn)
    audit('deploiement.confirmation', f"{row['title']} : {counted(queued_count, 'poste')}, {counted(len(ready), 'logiciel')}, {deployment_id}", commit=False)
    conn.commit()
    flash(n_("Déploiement confirmé sur %(num)s poste. L'agent exécute les installations à son prochain contact.", 'Déploiement confirmé sur %(num)s postes. Les agents exécutent les installations à leur prochain contact.', queued_count), 'ok')
    return back('deployment_detail', deployment_id=deployment_id)


@app.route('/fleet/deployments/<deployment_id>/cancel', methods=['POST'])
@login_required
def deployment_cancel(deployment_id):
    row = _get(deployment_id)
    plan = json.loads(row['plan'])
    conn = lock_access(_manage_permission(plan))
    if plan['bulk'] and plan.get('kind') != 'update':
        require('fleet.bulk_install')
    if row['state'] == 'draft':
        conn.execute("UPDATE deployments SET state='cancelled',cancel_requested=1 WHERE id=?", (deployment_id,))
    else:
        conn.execute("UPDATE device_commands SET state='cancelled',finished_at=?,output='Annulé avant transmission à l’agent.' "
                     "WHERE state='queued' AND id IN (SELECT command_id FROM deployment_targets WHERE deployment_id=?)", (fleet._now(), deployment_id))
        conn.execute("UPDATE deployment_targets SET last_state='cancelled',pending='',last_output='Vague annulée avant son départ.' "
                     "WHERE deployment_id=? AND pending!=''", (deployment_id,))
        conn.execute('UPDATE deployments SET cancel_requested=1 WHERE id=?', (deployment_id,))
    cleanup_artifacts(conn)
    audit('deploiement.annulation', f"{row['title']} : {deployment_id}", commit=False)
    conn.commit()
    flash(_('Commandes non transmises annulées. Les installations déjà reçues par un agent peuvent se terminer.'), 'ok')
    return back('deployment_detail', deployment_id=deployment_id)


# --------------------------------------------------------------------------- vagues

def _manage_permission(plan):
    return 'fleet.bulk_update' if plan.get('kind') == 'update' else 'fleet.install'


def _rollout(row):
    return json.loads(row['waves']) if row['waves'] else {}


def _default_size(plan):
    """Première proposition : un dixième des postes, au moins un."""
    return max(1, len(plan['devices']) // 10)


def _bounded(form, name, low, high, label):
    value = form.get(name, '').strip()
    if not value.isdigit() or not low <= int(value) <= high:
        raise ValueError(_('%(label)s : nombre entre %(low)s et %(high)s attendu.', label=label, low=low, high=high))
    return int(value)


def _rollout_options(form):
    """Réglages choisis par la personne, ou None pour tout déployer d'un coup."""
    if form.get('mode') != 'waves':
        return None
    options = {'size': _bounded(form, 'wave_size', 1, MAX_WAVE_SIZE, _('Postes par vague')),
               'delay': _bounded(form, 'wave_delay', 0, 1440, _('Attente entre deux vagues')),
               'stop_after': _bounded(form, 'wave_stop', 1, 100, _('Échecs avant la pause')), 'current': 1}
    pilot = form.get('wave_pilot', '').strip()
    if pilot:
        if not pilot.isdigit() or db().execute('SELECT 1 FROM device_groups WHERE id=?', (int(pilot),)).fetchone() is None:
            raise ValueError(_('Groupe pilote inconnu.'))
        options['pilot'] = int(pilot)
    return options


def _assign_waves(conn, entries, rollout):
    """Répartit les postes qui ont quelque chose à faire. Le groupe pilote forme la première vague ; ensuite, ceux qui sont
    en ligne d'abord : ils donnent une réponse rapide, alors qu'un poste éteint ne dit rien de la mise à jour."""
    work = [e for e in entries if e['params']]
    pilot = set()
    if rollout.get('pilot'):
        pilot = {r[0] for r in conn.execute('SELECT device_id FROM device_group_members WHERE group_id=?', (rollout['pilot'],))}
    first = [e for e in work if e['dev']['id'] in pilot]
    rest = sorted((e for e in work if e['dev']['id'] not in pilot),
                  key=lambda e: (not fleet.presence(e['dev'])['online'], (e['dev']['hostname'] or '').lower()))
    start = 2 if first else 1
    for e in first:
        e['wave'] = 1
    for i, e in enumerate(rest):
        e['wave'] = start + i // rollout['size']
    return (start - 1) + ((len(rest) + rollout['size'] - 1) // rollout['size'])


def _new_command(conn, dev, action, params, user):
    return conn.execute('INSERT INTO device_commands(device_id,action,params,created_at,created_by) VALUES (?,?,?,?,?)',
                        (dev['id'], action, json.dumps(params, sort_keys=True), fleet._now(), user)).lastrowid


def _add_targets(conn, deployment_id, entries, rollout, user):
    """Enregistre les cibles : commande créée tout de suite, ou gardée pour le départ de sa vague."""
    if rollout:
        rollout['total'] = _assign_waves(conn, entries, rollout)
    for e in entries:
        wave, command_id, pending = e.get('wave', 0), None, ''
        if e['params']:
            if wave > 1:
                pending = json.dumps({'action': e['action'], 'params': e['params']}, sort_keys=True)
            else:
                command_id = _new_command(conn, e['dev'], e['action'], e['params'], user)
        target = e['target']
        conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason,wave,pending,last_state) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (deployment_id, target['id'] if e['dev'] else None, target['uid'], target['hostname'], target['os_family'],
                     command_id, json.dumps(e['apps']), json.dumps(e['skipped']), target['reason'], wave, pending, 'planned' if pending else ''))


def start_update_rollout(conn, devices, form, reboot, group_name):
    """Mise à jour du parc par vagues, lancée depuis Mon parc : un déploiement de type « update », confirmé d'emblée."""
    rollout = _rollout_options(form)
    deployment_id = secrets.token_hex(16)
    title = (_('Mises à jour · groupe %(name)s', name=group_name) if group_name else _('Mises à jour du parc'))[:100]
    targets = [{'id': d['id'], 'uid': d['uid'], 'hostname': d['hostname'], 'os_family': d['os_family'], 'reason': ''} for d in devices]
    plan = {'kind': 'update', 'apps': [], 'devices': targets, 'groups': {'software': [], 'devices': [group_name] if group_name else []},
            'bulk': True, 'force': False, 'reboot': reboot}
    params = {'reboot': reboot, 'deployment_id': deployment_id}
    entries = [{'target': t, 'dev': d, 'action': 'update', 'params': params, 'apps': [], 'skipped': []} for t, d in zip(targets, devices)]
    conn.execute('INSERT INTO deployments(id,title,user_id,created_by,pw_version,owner,created_at,expires,plan,state,confirmed_at,waves) '
                 "VALUES (?,?,?,?,?,?,?,?,?,'queued',?,'')", (deployment_id, title, g.user['id'], g.user['username'], g.user['pw_version'], _owner(),
                 fleet._now(), time.time() + 3600, json.dumps(plan), fleet._now()))
    _add_targets(conn, deployment_id, entries, rollout, g.user['username'])
    conn.execute('UPDATE deployments SET waves=? WHERE id=?', (json.dumps(rollout), deployment_id))
    audit('deploiement.confirmation', f"{title} : {counted(len(devices), 'poste')}, {counted(rollout['total'], 'vague')}, {deployment_id}", commit=False)
    return deployment_id


def _start_wave(conn, row, rollout, number):
    """Crée les commandes d'une vague. Un poste retiré du parc entre-temps n'est plus concerné."""
    for target in conn.execute('SELECT * FROM deployment_targets WHERE deployment_id=? AND wave=? AND pending!=\'\'', (row['id'], number)).fetchall():
        dev = conn.execute("SELECT * FROM devices WHERE id=? AND state='active'", (target['device_id'],)).fetchone()
        if dev is None:
            conn.execute("UPDATE deployment_targets SET pending='',last_state='skipped',reason='Poste retiré du parc avant le départ de sa vague' WHERE id=?", (target['id'],))
            continue
        pending = json.loads(target['pending'])
        if pending['action'] == 'update' and not fleet.needs_update(dev):      # mis à jour entre-temps, ou déjà en attente
            conn.execute("UPDATE deployment_targets SET pending='',last_state='skipped',reason='Rien à mettre à jour au départ de sa vague' WHERE id=?", (target['id'],))
            continue
        command_id = _new_command(conn, dev, pending['action'], pending['params'], row['created_by'])
        conn.execute("UPDATE deployment_targets SET command_id=?,pending='',last_state='' WHERE id=?", (command_id, target['id']))
    rollout['current'] = number
    for key in ('next_at', 'paused', 'silent'):
        rollout.pop(key, None)


def _advance(conn, row):
    """Avance un déploiement par vagues d'un cran. Rien ne part sans preuve : au moins une réussite dans la vague en cours,
    et moins d'échecs que le seuil. Un poste hors ligne ne bloque pas : sa commande l'attend, il suivra à son retour."""
    rollout, before = _rollout(row), row['waves']
    if rollout.get('finished') or rollout.get('paused'):
        return
    now = time.time()
    current = rollout['current']
    success = failed = busy = 0
    for t in conn.execute('SELECT c.state AS state,d.state AS device_state,d.last_seen FROM deployment_targets t LEFT JOIN device_commands c ON c.id=t.command_id '
                          'LEFT JOIN devices d ON d.id=t.device_id WHERE t.deployment_id=? AND t.wave=?', (row['id'], current)):
        success += t['state'] == 'success'
        failed += t['state'] == 'failed'
        if t['state'] in ('sent', 'running'):
            busy += 1                                       # sans réponse, il finit en échec au bout de 6 h
        elif t['state'] == 'queued' and fleet.presence({'state': t['device_state'] or 'removed', 'last_seen': t['last_seen'] or ''})['online']:
            busy += 1
    if failed >= rollout['stop_after']:
        if current < rollout['total']:
            rollout['paused'] = {'wave': current, 'failed': failed}
        else:
            rollout['finished'] = True                      # plus rien à décider : le résultat se lit sur le déploiement
    elif not busy:
        rollout['silent'] = not success                     # personne n'a répondu : rien ne dit qu'on peut continuer
        if current >= rollout['total']:
            rollout['finished'] = True
        elif success:
            rollout.setdefault('next_at', now + rollout['delay'] * 60)
            if now >= rollout['next_at']:
                _start_wave(conn, row, rollout, current + 1)
    updated = json.dumps(rollout)
    if updated != before:
        conn.execute('UPDATE deployments SET waves=? WHERE id=?', (updated, row['id']))


def advance_rollouts(conn):
    """Fil de fond : fait avancer les déploiements par vagues."""
    with app.app_context():                                 # fleet.needs_update lit la base par db()
        for row in conn.execute("SELECT * FROM deployments WHERE waves!='' AND state='queued' AND cancel_requested=0").fetchall():
            _advance(conn, row)


def paused_rollouts():
    """Déploiements en pause visibles du compte courant (vue d'ensemble)."""
    rows = db().execute("SELECT * FROM deployments WHERE waves LIKE '%\"paused\"%' AND state='queued' AND cancel_requested=0").fetchall()
    return [r for r in rows if _in_scope(r)]


def _rollout_view(row, targets):
    """Avancement par vague pour la fiche du déploiement ; None pour un déploiement sans vagues."""
    rollout = _rollout(row)
    if not rollout:
        return None
    waves = []
    for number in range(1, rollout['total'] + 1):
        members = [t for t in targets if t['wave'] == number]
        progress = _progress(members)
        started = number <= rollout['current']
        state = 'planned' if not started else 'done' if not progress['active'] else 'running'
        waves.append({'number': number, 'progress': progress, 'state': state})
    wait = max(0, round((rollout['next_at'] - time.time()) / 60)) if rollout.get('next_at') else None
    can_continue = not row['cancel_requested'] and not rollout.get('finished') and rollout['current'] < rollout['total'] \
        and bool(rollout.get('paused') or rollout.get('silent') or rollout.get('next_at'))
    return {**rollout, 'waves': waves, 'wait': wait, 'can_continue': can_continue}


@app.route('/fleet/deployments/<deployment_id>/next-wave', methods=['POST'])
@login_required
def deployment_next_wave(deployment_id):
    """Décision humaine : passer à la vague suivante malgré une pause, un silence ou une attente."""
    row = _get(deployment_id)
    conn = lock_access(_manage_permission(json.loads(row['plan'])))
    row = conn.execute('SELECT * FROM deployments WHERE id=?', (deployment_id,)).fetchone()
    rollout = _rollout(row)
    if rollout and not row['cancel_requested'] and not rollout.get('finished') and rollout['current'] < rollout['total']:
        _start_wave(conn, row, rollout, rollout['current'] + 1)
        conn.execute('UPDATE deployments SET waves=? WHERE id=?', (json.dumps(rollout), deployment_id))
        audit('deploiement.vague', f"{row['title']} : vague {rollout['current']}, {deployment_id}", commit=False)
    conn.commit()
    return back('deployment_detail', deployment_id=deployment_id)


# Les déploiements vivent dans Mon parc (actions sur les appareils, comme les scripts) ; anciennes adresses redirigées.
@app.route('/windows-apps/deployments', defaults={'deployment_id': ''})
@app.route('/windows-apps/deployments/<deployment_id>')
@app.route('/windows-apps/deployer', defaults={'deployment_id': 'nouveau'})
@login_required
def deployments_legacy(deployment_id):
    target = (url_for('deployment_compose') if deployment_id == 'nouveau' else
              url_for('deployment_detail', deployment_id=deployment_id) if deployment_id else url_for('deployments'))
    query = request.query_string.decode()
    return redirect(target + ('?' + query if query else ''), 308)
