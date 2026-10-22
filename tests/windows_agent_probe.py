"""Agent du parc sur un serveur Windows réel : installation par la commande du portail (MSI), inscription,
inventaire, action à distance, puis retrait poussé depuis loco (service et MSI désinstallés).

À lancer avec le Python embarqué de loco, en administrateur, avec LOCO_E2E_PASSWORD (machine jetable).
"""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import locowin

cfg = locowin.load_config()
base = f"https://127.0.0.1:{cfg['https_port']}"
password = os.environ['LOCO_E2E_PASSWORD']
context = ssl.create_default_context(cafile=str(Path(cfg['etc_dir'], 'tls', 'ca.crt')))
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                     urllib.request.HTTPSHandler(context=context))
hostname = socket.gethostname().lower()


def get(path):
    with opener.open(base + path, timeout=90) as response:
        return response.read().decode()


def post(path, values):
    with opener.open(base + path, urllib.parse.urlencode(values).encode(), timeout=90) as response:
        return response.geturl(), response.read().decode()


def csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def powershell(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                            capture_output=True, text=True, errors='replace')
    print(result.stdout.strip())
    return result


def wait(what, check, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(5)
    sys.exit(f'Délai dépassé : {what}')


url, html = post('/login', {'csrf': csrf(get('/login')), 'username': 'admin', 'password': password})
if url.endswith('/account'):
    changed = password + '-Changed'
    url, html = post('/account', {'csrf': csrf(html), 'current': password, 'new': changed, 'confirm': changed})
assert url.rstrip('/') == base, f'Connexion refusée : {url}'

# Commande d'installation affichée dans « Mon parc » : amorçage en HTTP (autorité pas encore importée).
fleet = get('/enrollment')                                  # clé d'inscription : page Enrôlement
key = re.search(r"/agent/windows\.ps1\?key=([A-Za-z0-9_-]+)", fleet).group(1)
http_base = f"http://127.0.0.1:{cfg['http_port']}" if cfg.get('http_port') else base
result = powershell(f"irm '{http_base}/agent/windows.ps1?key={key}' | iex")
# Le script d'agent suit la langue du poste (anglais par défaut sur le banc) : accepter chaque variante.
assert result.returncode == 0 and any(m in result.stdout for m in (
    'Agent loco installe', 'loco agent installed', 'Agente loco instalado')), result.stderr
service = powershell('(Get-Service loco-client).Status').stdout.strip()
assert service == 'Running', service
print('Agent Windows : MSI vérifié et installé, service loco-client démarré')


def device_id():
    m = re.search(r'href="/fleet/(\d+)" class="[^"]*\bhost\b[^"]*">' + re.escape(hostname) + '<', get('/fleet'))
    return m.group(1) if m else None


dev = wait('inscription du poste', device_id, 300)
page = wait('premier inventaire', lambda: (lambda p: p if 'Inventaire en cours' not in p else None)(get(f'/fleet/{dev}')), 900)
assert 'loco-client' in page and 'Windows' in page, page[:2000]
print(f'Poste {hostname} inscrit (n° {dev}) et inventorié')
probe = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(Path(__file__).with_name('windows-tray-probe.ps1'))], timeout=240)
assert probe.returncode == 0, 'Vérification du systray en échec'

# Terminal natif via le relais HTTPS : vrai ConPTY SYSTEM, puis suppression de l'arbre de processus.
session_url, _ = post(f'/fleet/{dev}/session', {'csrf': csrf(get(f'/fleet/{dev}')), 'kind': 'terminal'})
sid = session_url.rsplit('/', 1)[1]
io_path = f'/fleet/session/{sid}/io'
token = csrf(get(f'/fleet/{dev}'))
command = ("Write-Output ([string]::Concat('LOCO_','PTY_OK')); whoami /user; "
           "$p=Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep 300' -PassThru; "
           "Write-Output ('CHILD_PID='+$p.Id)\r")
pending = base64.b64encode(command.encode()).decode()
ack, output, child = 0, b'', None
try:
    until = time.monotonic() + 40
    while time.monotonic() < until:
        values = {'csrf': token, 'ack': ack}
        if pending:
            values.update(seq=1, data=pending)
        _, text = post(io_path, values)
        response = json.loads(text)
        if response['ack'] >= 1:
            pending = ''
        for packet in response['packets']:
            if packet['seq'] > ack:
                output += base64.b64decode(packet['data'])
                ack = packet['seq']
        match = re.search(rb'CHILD_PID=(\d+)', output)
        if b'LOCO_PTY_OK' in output and b'S-1-5-18' in output and match:
            child = int(match.group(1))
            break
        assert response['state'] != 'closed', response.get('error')
        time.sleep(0.2)
    assert child, 'Terminal ConPTY indisponible : ' + repr(output[-1500:])
finally:
    post(io_path, {'csrf': token, 'close': 1})
wait('arrêt des enfants du terminal fermé', lambda: powershell(
    f'if (Get-Process -Id {child} -ErrorAction SilentlyContinue) {{ 1 }} else {{ 0 }}').stdout.strip() == '0', 30)
print('Terminal natif : flux HTTPS interactif, compte SYSTEM, fermeture et arrêt des enfants validés')


class FilesRelay:
    """Client minimal du gestionnaire de fichiers : trames relayées par /io, comme le navigateur."""
    def __init__(self, io_path, token):
        self.io, self.token, self.inseq, self.outack, self.buf, self.nid = io_path, token, 0, 0, b'', 0

    def request(self, op, extra=None, data=b''):
        self.nid += 1
        header = dict(extra or {}, id=self.nid, op=op)
        hb = json.dumps(header).encode()
        frame = struct.pack('>II', len(hb), len(data)) + hb + data
        self.inseq += 1
        pending = base64.b64encode(frame).decode()
        end = time.monotonic() + 40
        while time.monotonic() < end:
            values = {'csrf': self.token, 'ack': self.outack}
            if pending:
                values.update(seq=self.inseq, data=pending)
            _, text = post(self.io, values)
            response = json.loads(text)
            if response['ack'] >= self.inseq:
                pending = ''
            for packet in response['packets']:
                if packet['seq'] > self.outack:
                    self.buf += base64.b64decode(packet['data'])
                    self.outack = packet['seq']
            while len(self.buf) >= 8:
                hlen, dlen = struct.unpack('>II', self.buf[:8])
                if len(self.buf) < 8 + hlen + dlen:
                    break
                reply = json.loads(self.buf[8:8 + hlen].decode())
                body = self.buf[8 + hlen:8 + hlen + dlen]
                self.buf = self.buf[8 + hlen + dlen:]
                if reply.get('id') == self.nid:
                    return reply, body
            assert response['state'] != 'closed', response.get('error')
            time.sleep(0.15)
        sys.exit(f'Gestionnaire de fichiers : pas de réponse à {op}')


# Gestionnaire de fichiers via le relais : vrai FileRelay C# du poste (agent 2.23+), hello/list/write/read/delete.
_, body = post(f'/fleet/{dev}/session', {'csrf': csrf(get(f'/fleet/{dev}')), 'kind': 'files'})
files_sid = json.loads(body)['sid']
files_io = f'/fleet/session/{files_sid}/io'
files = FilesRelay(files_io, csrf(get(f'/fleet/{dev}')))
files_dir = r'C:\Windows\Temp\loco-fmprobe'
files_path = files_dir + r'\probe.txt'
payload = b'FileRelay sur ' + hostname.encode() + b' fonctionne'
try:
    hello, _ = files.request('hello')
    assert hello['ok'] and hello['os'] == 'windows', hello
    files.request('delete', {'path': files_dir})                         # efface un reste d'exécution précédente
    assert files.request('mkdir', {'path': files_dir})[0]['ok'], 'mkdir refusé'
    assert files.request('write', {'path': files_path, 'offset': 0, 'final': True}, payload)[0]['ok'], 'write refusé'
    read = files.request('read', {'path': files_path, 'offset': 0, 'length': 65536})
    assert read[1] == payload, repr(read)
    names = [e['name'] for e in files.request('list', {'path': files_dir})[0]['entries']]
    assert 'probe.txt' in names, names
    assert files.request('delete', {'path': files_dir})[0]['ok'], 'delete refusé'
finally:
    post(files_io, {'csrf': files.token, 'close': 1})
print('Gestionnaire de fichiers : FileRelay C# — hello, écriture, lecture, liste et suppression via le relais HTTPS')
desktop_exe = Path(os.environ['ProgramFiles'], 'loco-client', 'loco-desktop.exe')
assert desktop_exe.is_file(), 'Compagnon de bureau absent du MSI'
assert subprocess.run([str(desktop_exe), '--update'], timeout=10).returncode == 2, 'Arguments du compagnon trop permissifs'

# Les clés d'inscription ne restent pas dans le registre, lisible par les utilisateurs du poste.
leftover = powershell("(Get-ItemProperty 'HKLM:\\SOFTWARE\\loco\\Client' -ErrorAction SilentlyContinue).Key").stdout.strip()
assert not leftover, 'clé d\'inscription restée dans le registre'

url, html = post(f'/fleet/{dev}/action', {'csrf': csrf(get(f'/fleet/{dev}')), 'action': 'scan'})
wait('résultat de l\'inventaire demandé',
     lambda: re.search(r'<td>Inventaire</td>\s*<td[^>]*>[^<]*</td>\s*<td><span class="st (ok|err)">', get(f'/fleet/{dev}')), 900)
print('Action à distance : inventaire exécuté et rapporté')

# Autre MSI du même numéro de version : remplace le produit sans perdre le service ni l'identité.
# Deux compilations ont des ProductCode distincts ; réutiliser le même MSI ne teste que la maintenance.
client_root = Path(os.environ['ProgramData'], 'loco-client')
identity = (client_root / 'client.json').read_bytes()
replacement = Path(cfg['program_dir'], 'portal', 'agents', 'reinstall', 'loco-client.msi')
assert replacement.is_file(), 'MSI de réinstallation absent'
result = subprocess.run(['msiexec.exe', '/i', str(replacement), '/qn', '/norestart'], timeout=180)
assert result.returncode in (0, 3010), f'Réinstallation MSI : {result.returncode}'
assert powershell('(Get-Service loco-client).Status').stdout.strip() == 'Running', 'service perdu à la réinstallation'
assert (client_root / 'client.json').read_bytes() == identity, 'identité perdue à la réinstallation'
assert device_id() == dev, 'fiche du poste modifiée à la réinstallation'
installed = powershell("Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | "
                       "Where-Object DisplayName -eq 'loco-client' | Measure-Object | Select-Object -ExpandProperty Count")
assert installed.stdout.strip() == '1', 'plusieurs MSI loco-client après réinstallation'
print('Réinstallation de la même version : service actif, identité conservée, un seul MSI')

url, html = post(f'/fleet/{dev}/action', {'csrf': csrf(get(f'/fleet/{dev}')), 'action': 'uninstall'})
wait('retrait de l\'agent', lambda: powershell('if (Get-Service loco-client -ErrorAction SilentlyContinue) { 1 } else { 0 }').stdout.strip() == '0', 600)
assert hostname not in re.findall(r'class="[^"]*\bhost\b[^"]*">([^<]+)<', get('/fleet')), 'poste encore dans le parc'
installed = powershell("Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | "
                       "Where-Object DisplayName -eq 'loco-client' | Measure-Object | Select-Object -ExpandProperty Count")
assert installed.stdout.strip() == '0', 'MSI loco-client encore installé'
print('Retrait poussé depuis loco : service et MSI désinstallés, poste retiré du parc')
