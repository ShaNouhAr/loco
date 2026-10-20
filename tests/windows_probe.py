"""Vérifie un serveur Windows réel par HTTPS, ses signatures et son cache APT.

À lancer avec le Python embarqué, LOCO_E2E_PASSWORD et un dossier de paquets d'essai.
"""
import gzip
import hashlib
import http.client
import http.cookiejar
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import locowin

cfg = locowin.load_config()
base = f"https://127.0.0.1:{cfg['https_port']}"
fixtures = Path(sys.argv[1]).resolve()
password = os.environ['LOCO_E2E_PASSWORD']
context = ssl.create_default_context(cafile=str(Path(cfg['etc_dir'], 'tls', 'ca.crt')))
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                   urllib.request.HTTPSHandler(context=context))


def get(path):
    with opener.open(base + path, timeout=90) as response:
        return response.read()


# gpgv indépendant de loco (celui de Git for Windows sur les machines de CI) : les signatures produites
# en Python par le serveur sont vérifiées par un outil OpenPGP de référence.
GPGV = next((g for g in (os.environ.get('LOCO_GPGV'), shutil.which('gpgv'),
                          r'C:\Program Files\Git\usr\bin\gpgv.exe') if g and os.path.exists(g)), None)
if GPGV is None and os.environ.get('CI'):
    sys.exit('gpgv introuvable : vérification des signatures impossible.')


def verify(signature, data=None):
    if GPGV is None:
        print('gpgv absent : signature non vérifiée sur ce poste')
        return
    with tempfile.TemporaryDirectory() as work:
        keyring = Path(work, 'loco.gpg')
        keyring.write_bytes(get('/apt/loco.gpg'))
        Path(work, 'sig').write_bytes(signature)
        # Chemins relatifs : le gpgv de Git for Windows prend « C: » pour un préfixe d'URL de trousseau.
        args = [GPGV, '--keyring', './loco.gpg', 'sig']
        if data is not None:
            Path(work, 'data').write_bytes(data)
            args.append('data')
        result = subprocess.run(args, capture_output=True, text=True, cwd=work)
        assert result.returncode == 0, result.stderr


def csrf(page):
    return re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()


def post(path, values):
    with opener.open(base + path, urllib.parse.urlencode(values).encode(), timeout=90) as response:
        return response.geturl(), response.read()


def upload(page, repository, filename):
    token = csrf(get('/' + page))
    boundary = uuid.uuid4().hex
    fields = {'csrf': token, 'repository': repository}
    body = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
                    for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename.name}"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n').encode()
    body += filename.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(base + '/' + page + '/upload', body,
                                    {'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with opener.open(request, timeout=120) as response:
        result = response.read().decode()
    messages = re.findall(r'data-flash="([^"]*)"', result)
    assert any(m.startswith(('Paquet ajouté', 'Ajouté')) for m in messages), messages


url, html = post('/login', {'csrf': csrf(get('/login')), 'username': 'admin', 'password': password})
if url.endswith('/compte'):
    changed = password + '-Changed'
    url, html = post('/compte', {'csrf': csrf(html), 'current': password, 'new': changed, 'confirm': changed})
assert url.rstrip('/') == base, f'Connexion refusée : {url}'
for page in ('/', '/linux', '/rpm', '/flatpak', '/windows-apps', '/windows-update', '/systeme', '/api/status'):
    assert get(page), page
print('Portail HTTPS et pages authentifiées : OK')

# Réseau en direct : compteurs 64 bits de l'interface vers Internet lus par GetIfEntry2 (structure MIB_IF_ROW2 décrite
# en ctypes), comparés aux statistiques de la carte données par PowerShell : un décalage de champ se verrait aussitôt.
first = json.loads(get('/api/network'))
assert first.get('available') and first['iface'] and first['rx'] > 0 and first['tx'] > 0, first
reference = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f"$s = Get-NetAdapterStatistics -Name '{first['iface']}'; \"$($s.ReceivedBytes) $($s.SentBytes)\""],
                           capture_output=True, text=True, timeout=60)
second = json.loads(get('/api/network'))
assert second['t'] > first['t'] and second['rx'] >= first['rx'] and second['tx'] >= first['tx'], (first, second)
if reference.returncode == 0 and len(reference.stdout.split()) == 2:
    received, sent = map(int, reference.stdout.split())
    for name, ours, theirs in (('reçus', first['rx'], received), ('émis', first['tx'], sent)):
        assert abs(ours - theirs) <= max(theirs // 10, 50 * 2**20), f"octets {name} : API {ours}, PowerShell {theirs}"
    print(f"Réseau en direct ({first['iface']}) : compteurs cohérents avec Get-NetAdapterStatistics : OK")
else:
    print(f"Réseau en direct ({first['iface']}) : compteurs croissants (pas de carte PowerShell à comparer) : OK")

for compression in ('xz', 'gzip', 'zstd'):
    # Des paquets identiques, encodés différemment, dans des dépôts séparés.
    suite = 'native-' + compression
    url, html = post('/linux/repositories', {'csrf': csrf(get('/linux')), 'distribution': 'debian',
                                               'suite': suite, 'version': 'test'})
    assert suite.encode() in html, 'Création dépôt APT refusée'
    upload('linux', 'debian/' + suite, fixtures / (compression + '.deb'))
    root = '/apt/repositories/debian/' + suite + '/'
    packages = get(root + 'Packages')
    assert b'Package: lr-native-e2e\n' in packages
    assert gzip.decompress(get(root + 'Packages.gz')) == packages
    path = re.search(rb'^Filename: (.+)$', packages, re.M).group(1).decode()
    assert get(root + path) == (fixtures / (compression + '.deb')).read_bytes()
    verify(get(root + 'InRelease'))
    verify(get(root + 'Release.gpg'), get(root + 'Release'))
print('APT gzip/xz/zstd, paquets et signatures : OK')

upload('rpm', 'el9', fixtures / 'test.rpm')
repomd = get('/rpm/el9/repodata/repomd.xml')
ns = {'r': 'http://linux.duke.edu/metadata/repo'}
for node in ET.fromstring(repomd).findall('r:data', ns):
    payload = get('/rpm/el9/' + node.find('r:location', ns).get('href'))
    assert hashlib.sha256(payload).hexdigest() == node.findtext('r:checksum', namespaces=ns)
verify(get('/rpm/el9/repodata/repomd.xml.asc'), repomd)
print('RPM, index et signature : OK')


class Origin(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = b'loco-cache-test'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


origin = http.server.ThreadingHTTPServer(('127.0.0.2', 0), Origin)
thread = threading.Thread(target=origin.serve_forever, daemon=True)
thread.start()
try:
    target = f'http://127.0.0.2:{origin.server_port}/pool/{uuid.uuid4().hex}.deb'
    for expected in ('MISS', 'HIT'):
        connection = http.client.HTTPConnection('127.0.0.1', cfg['acng_port'], timeout=30)
        connection.request('GET', target)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader('X-Loco-Cache') == expected
        assert response.read() == b'loco-cache-test'
        connection.close()
    assert Origin.hits == 1
finally:
    origin.shutdown()
    origin.server_close()
print('Cache APT natif MISS/HIT : OK')
