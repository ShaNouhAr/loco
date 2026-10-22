"""Clavier réel de l'ISO : annulations, shell interactif et consoles indépendantes."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid


class ConsoleVM:
    def __init__(self, work, log):
        self.work = Path(work)
        self.log = log
        self.serial_buffer = b''

    def connect(self, name):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            connection = socket.socket(socket.AF_UNIX)
            try:
                connection.connect(str(self.work / name))
                connection.settimeout(1)
                return connection
            except (FileNotFoundError, ConnectionRefusedError):
                connection.close()
                time.sleep(.2)
        raise TimeoutError(name)

    def qmp(self, command, arguments=None):
        self.monitor.sendall(json.dumps(dict(execute=command, arguments=arguments or {})).encode() + b'\n')
        while True:
            reply = json.loads(self.monitor_file.readline())
            if 'error' in reply:
                raise RuntimeError(reply)
            if 'return' in reply:
                return reply['return']

    def read_until(self, marker, timeout=30):
        deadline = time.monotonic() + timeout
        while marker not in self.serial_buffer:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Console série : {marker!r}')
            try:
                chunk = self.serial.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                raise RuntimeError('Console série fermée')
            self.log.write(chunk)
            self.log.flush()
            self.serial_buffer += chunk
        result, self.serial_buffer = self.serial_buffer.split(marker, 1)
        return result

    def command(self, command):
        token = uuid.uuid4().hex.encode()
        # Les marqueurs sur leurs propres lignes évitent de lire l'écho de la commande.
        self.serial.sendall(b"printf '\\n" + token + b"\\n'; " + command.encode()
                            + b"; printf '\\n" + token + b"-end\\n'\n")
        self.read_until(b'\r\n' + token + b'\r\n')
        return self.read_until(b'\r\n' + token + b'-end\r\n').decode().strip()

    def screen(self):
        return base64.b64decode(self.command('base64 /dev/vcs7')).decode('latin1')

    def wait_screen(self, *expected):
        # L'assistant de l'ISO suit la langue du serveur (anglais par défaut sur le banc) : accepter chaque variante.
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            screen = self.screen()
            hit = next((text for text in expected if text in screen), None)
            if hit:
                print(f'Écran : {hit}', flush=True)
                return screen
            time.sleep(.3)
        raise AssertionError(f'Écran attendu : {" / ".join(expected)}\n{screen}')

    def key(self, key):
        self.qmp('human-monitor-command', {'command-line': f'sendkey {key} 50'})
        time.sleep(.15)

    def text(self, text):
        symbols = {' ': 'spc', '\n': 'ret', '-': 'minus', '/': 'slash',
                   '>': 'shift-dot', '.': 'dot'}
        for char in text:
            self.key(symbols.get(char, char))

    def configure_to_features(self):
        self.key('ret')
        self.wait_screen('Clavier', 'Keyboard')
        self.key('ret')
        self.wait_screen('Disque', 'Disk')
        self.key('ret')
        self.wait_screen('Nom', 'Name')
        self.key('ret')
        self.wait_screen('Adresse du serveur', 'Server address', 'Dirección del servidor')
        self.key('down')  # DHCP : pas de formulaire d'adresse fixe.
        self.key('ret')
        self.wait_screen('Fuseau horaire', 'Time zone', 'Zona horaria')
        self.key('ret')
        self.wait_screen('Compte root', 'Root account', 'Cuenta root')
        self.text('console-test-123\n')
        self.text('console-test-123\n')
        self.wait_screen('Portail', 'Portal')
        self.text('console-portal-123\n')
        self.text('console-portal-123\n')
        self.wait_screen('Postes clients', 'Client machines', 'Equipos cliente')
        self.key('tab')
        self.key('ret')
        self.wait_screen('Fonctions', 'Features', 'Funciones')

    def check(self):
        self.monitor = self.connect('monitor')
        self.monitor.settimeout(15)
        self.monitor_file = self.monitor.makefile('r')
        json.loads(self.monitor_file.readline())
        self.qmp('qmp_capabilities')
        self.serial = self.connect('serial')
        self.read_until(b'login: ', timeout=240)
        self.serial.sendall(b'root\n')
        self.read_until(b'# ')
        self.wait_screen('Bienvenue', 'Welcome')
        # Laisser init démarrer ses getty : tty0 ne doit jamais reprendre l'écran actif.
        time.sleep(3)
        assert self.command("grep -c '^tty0:' /etc/inittab") == '0'
        assert self.command('cat /sys/class/tty/tty0/active') == 'tty7'
        original = self.command('dd if=/dev/vda bs=1M count=1 2>/dev/null | sha256sum')

        self.key('esc')
        time.sleep(2)
        self.wait_screen('Bienvenue', 'Welcome')
        self.key('ret')
        self.wait_screen('Clavier', 'Keyboard')
        # Le bouton Accueil (Tab puis Entrée), puis Échap sur la sélection du disque.
        self.key('tab')
        self.key('ret')
        self.wait_screen('Bienvenue', 'Welcome')
        self.key('ret')
        self.wait_screen('Clavier', 'Keyboard')
        for _ in range(4):
            self.key('down')  # QEMU injecte des touches physiques QWERTY : sélectionner US.
        self.key('ret')
        self.wait_screen('Disque', 'Disk')
        self.key('esc')
        self.wait_screen('Bienvenue', 'Welcome')

        self.key('down')
        self.key('ret')
        screen = self.wait_screen('Tapez exit', 'Type exit', 'Escriba exit')
        assert "can't access tty" not in screen and 'job control turned off' not in screen
        # Ctrl+C doit arrêter la commande et laisser un shell capable de recevoir la suivante.
        self.text('sleep 60\n')
        self.key('ctrl-c')
        assert self.command("pgrep -f '^sleep 60$' || true") == ''
        self.text('echo console-ok > /tmp/loco-console-result\n')
        assert self.command('cat /tmp/loco-console-result') == 'console-ok'
        self.text('exit\n')
        self.wait_screen('Bienvenue', 'Welcome')
        self.key('alt-f1')
        assert self.command('cat /sys/class/tty/tty0/active') == 'tty1'
        self.key('alt-f7')
        self.wait_screen('Bienvenue', 'Welcome')

        # Atteindre les fonctions, puis annuler : aucun récapitulatif/effacement ne doit suivre.
        self.configure_to_features()
        self.key('esc')
        self.wait_screen('Bienvenue', 'Welcome')
        self.configure_to_features()
        for _ in range(4):
            self.key('down')
        self.key('spc')  # Active SSH : mot de passe proposé sans saisir de clé.
        self.key('ret')
        self.wait_screen('Connexion SSH', 'SSH access', 'Acceso SSH')
        self.key('ret')
        screen = self.wait_screen('SSH root')
        # Le texte peut être coupé entre deux lignes de la fenêtre dialog.
        assert any(t in ' '.join(screen.replace('\xb3', ' ').split()) for t in ('mot de passe root', 'root password'))
        self.key('esc')
        self.wait_screen('Bienvenue', 'Welcome')
        assert self.command('dd if=/dev/vda bs=1M count=1 2>/dev/null | sha256sum') == original
        assert self.command('ls /sys/block/vda/vda* 2>/dev/null | wc -l') == '0'
        print('ISO interactive : Échap, Accueil, shell/Ctrl+C/exit, Alt+F1/F7, fonctions annulées, SSH par mot de passe sans clé, disque intact : OK')


def main():
    iso = str(Path(sys.argv[1]).resolve())
    logs = Path(os.environ.get('LOCO_VM_ARTIFACTS', 'test-results')) / 'alpine-console'
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='loco-console-') as work, \
            (logs / 'serial.log').open('wb') as serial_log, (logs / 'qemu.log').open('wb') as qemu_log:
        disk = str(Path(work) / 'disk.qcow2')
        subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', disk, '12G'], check=True)
        accel = ['-accel', 'kvm', '-cpu', 'host'] if os.access('/dev/kvm', os.R_OK | os.W_OK) else ['-accel', 'tcg']
        with subprocess.Popen(['qemu-system-x86_64', *accel, '-m', '2048', '-smp', '2', '-display', 'none',
                               '-monitor', 'none', '-qmp', f'unix:{work}/monitor,server=on,wait=off',
                               '-serial', f'unix:{work}/serial,server=on,wait=off',
                               '-drive', f'file={disk},if=virtio,format=qcow2',
                               '-drive', f'file={iso},media=cdrom,readonly=on', '-boot', 'd',
                               '-netdev', 'user,id=n', '-device', 'virtio-net-pci,netdev=n'],
                              stdout=qemu_log, stderr=qemu_log) as process:
            vm = ConsoleVM(work, serial_log)
            try:
                vm.check()
            except Exception:
                if hasattr(vm, 'monitor_file'):
                    vm.qmp('human-monitor-command', {'command-line': f'screendump {logs.resolve()}/failure.ppm'})
                raise
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
