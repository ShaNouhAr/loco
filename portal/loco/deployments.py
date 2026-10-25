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

from . import app, config, fleet, groups, winget
from .i18n import L_, _
from .auth import audit, can, lock_access, login_required, require
from .db import db
from .util import back, sha256_of

MAX_APPS = 100
MAX_DEVICES = 500
ACTIVE = ('queued', 'sent', 'running')
STATES = {'queued': L_('En attente du poste'), 'sent': L_('Transmis à l’agent'), 'running': L_('Installation en cours'),
          'success': L_('Réussi'), 'failed': L_('Échec'), 'cancelled': L_('Annulé'), 'skipped': L_('Non déployé')}
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
                               "WHERE m.group_id=? AND d.state!='removed' ORDER BY d.hostname", (gid,)).fetchall()
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
                               "WHERE m.group_id=? AND d.state!='removed' ORDER BY d.hostname", (gid,)).fetchall()
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
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE").fetchall())


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
    audit('deploiement.preparation', f'{title} : {len(packages)} paquet(s), {len(devices)} cible(s)', commit=False)
    conn.commit()
    return back('deployment_detail', deployment_id=deployment_id)


def _confirm_linux(conn, deployment_id, plan):
    """Confirmation d'un déploiement de paquets Linux : une commande install-package par poste compatible."""
    catalog = _package_catalog()
    ready = []
    for snap in plan['apps']:
        if catalog.get(f"{snap['kind']}:{snap['name']}") is None:
            abort(409, _('Le paquet %(name)s n’est plus publié : préparez à nouveau le déploiement.', name=snap['name']))
        ready.append(snap)
    families = {p['family'] for p in ready}
    queued = 0
    for target in plan['devices']:
        if target['reason']:
            conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason) '
                         'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, None, target['uid'], target['hostname'],
                         target['os_family'], None, '[]', '[]', target['reason']))
            continue
        dev = conn.execute('SELECT * FROM devices WHERE id=?', (target['id'],)).fetchone()
        if dev is None or dev['uid'] != target['uid'] or _target_reason_pkg(dev, families):
            abort(409, _('Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.'))
        installed = [p for p in ready if p['family'] == dev['os_family']]
        command_id = None
        if installed:
            params = {'packages': [p['name'] for p in installed], 'deployment_id': deployment_id,
                      'bulk_install': plan['bulk'], 'kind': 'linux'}
            command_id = conn.execute('INSERT INTO device_commands(device_id,action,params,created_at,created_by) '
                                      "VALUES (?,'install-package',?,?,?)", (dev['id'], json.dumps(params, sort_keys=True),
                                      fleet._now(), g.user['username'])).lastrowid
            queued += 1
        conn.execute('INSERT INTO deployment_targets(deployment_id,device_id,device_uid,hostname,os_family,command_id,apps,skipped,reason) '
                     'VALUES (?,?,?,?,?,?,?,?,?)', (deployment_id, dev['id'], target['uid'], target['hostname'],
                     target['os_family'], command_id, json.dumps(installed), '[]', target['reason']))
    return queued, len(ready)


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
                           devices=db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE").fetchall())


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
FILTERS = {'en-cours': (L_('En cours'), ('running', 'cancelling'), 'warn'), 'a-confirmer': (L_('À confirmer'), ('draft',), 'warn'),
           'erreurs': (L_('Avec erreurs'), ('failed',), 'err'), 'termines': (L_('Terminés'), ('success', 'cancelled', 'expired'), 'ok')}


def _progress(targets):
    """Postes réussis, en attente, en échec et non concernés (non déployés ou annulés)."""
    counts = {'success': 0, 'active': 0, 'failed': 0, 'other': 0}
    for t in targets:
        key = 'active' if t['state'] in ACTIVE else t['state'] if t['state'] in ('success', 'failed') else 'other'
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
    rows = db().execute("SELECT * FROM deployments WHERE state!='draft' OR user_id=? ORDER BY created_at DESC,id DESC",
                        (g.user['id'],)).fetchall()
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
        if kind == 'linux':
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
    signature = hashlib.sha256(json.dumps([state, [(a['current']['status'] if a['current'] else '', a['problem']) for a in apps],
                                          [(t['id'], t['state'], t['output'], t['progress']) for t in targets]]).encode()).hexdigest()[:16]
    if request.headers.get('X-Requested-With') == 'fetch':
        return {'signature': signature}
    if kind == 'linux':
        recompose = url_for("deployment_compose_linux", package=[f"{a['kind']}:{a['name']}" for a in plan['apps']],
                            device=[d['id'] for d in plan['devices']])
    else:
        recompose = url_for("deployment_compose", app=[a['slug'] for a in plan['apps']],
                            device=[d['id'] for d in plan['devices']])
    return render_template('deployment_detail.html', deployment=row, plan=plan, apps=apps, targets=targets,
                           state=state, states=STATES, confirmable=confirmable, blocked=blocked, signature=signature,
                           progress=_progress(targets), families=fleet.FAMILIES, kind=kind, recompose=recompose)


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
    if plan.get('kind') == 'linux':
        try:
            queued_count, ready_n = _confirm_linux(conn, deployment_id, plan)
        except (OSError, ValueError) as e:
            conn.rollback()
            flash(_('Déploiement non lancé : %(e)s', e=e), 'error')
            return back('deployment_detail', deployment_id=deployment_id)
        conn.execute("UPDATE deployments SET state='queued',confirmed_at=? WHERE id=?", (fleet._now(), deployment_id))
        audit('deploiement.confirmation', f"{row['title']} : {queued_count} poste(s), {ready_n} paquet(s), {deployment_id}", commit=False)
        conn.commit()
        flash(_('Déploiement confirmé sur %(queued_count)s poste(s). Les agents installent les paquets à leur prochain contact.', queued_count=queued_count), 'ok')
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
        if dev is None or dev['uid'] != target['uid'] or _target_reason(dev):
            abort(409, _('Une cible a été retirée ou modifiée : préparez à nouveau le déploiement.'))
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
        flash(_('Déploiement non lancé : %(e)s', e=e), 'error')
        return back('deployment_detail', deployment_id=deployment_id)
    conn.execute("UPDATE deployments SET state='queued',confirmed_at=? WHERE id=?", (fleet._now(), deployment_id))
    cleanup_artifacts(conn)
    audit('deploiement.confirmation', f"{row['title']} : {queued_count} poste(s), {len(ready)} logiciel(s), {deployment_id}", commit=False)
    conn.commit()
    flash(_('Déploiement confirmé sur %(queued_count)s poste(s). Les agents exécutent les installations à leur prochain contact.', queued_count=queued_count), 'ok')
    return back('deployment_detail', deployment_id=deployment_id)


@app.route('/fleet/deployments/<deployment_id>/cancel', methods=['POST'])
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
    flash(_('Commandes non transmises annulées. Les installations déjà reçues par un agent peuvent se terminer.'), 'ok')
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
