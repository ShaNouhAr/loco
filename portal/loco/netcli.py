"""Équipements réseau : la liaison SSH par mot de passe, et la conversation avec leur ligne de commande.

Un switch ou un pare-feu s'ouvre d'ordinaire avec un compte et un mot de passe, pas avec une clé : loco garde le mot de
passe chiffré dans sa base et le remet à ssh par SSH_ASKPASS (un petit script qui le lit dans l'environnement du seul
processus ssh) — jamais dans les arguments d'une commande, jamais sur le disque en clair. La clé d'hôte n'est jamais
acceptée d'office : un administrateur en confirme l'empreinte, et ssh refuse ensuite tout autre hôte.

La plupart de ces systèmes n'exécutent pas une commande passée à ssh comme le ferait Linux : loco ouvre donc la ligne de
commande comme le ferait une personne (pseudo-terminal), apprend l'invite, coupe la pagination, puis envoie chaque
commande et lit sa sortie jusqu'au retour de l'invite. Les « --More-- » qui resteraient sont passés d'eux-mêmes.
Les vieux équipements ne parlent que des algorithmes SSH d'avant 2015 (groupe Diffie-Hellman 14 en SHA-1, clé d'hôte
ssh-rsa, chiffrement CBC) : ils sont ajoutés, en dernier choix, à ceux du client quand celui-ci les connaît encore.
"""
import os
import re
import select
import signal
import subprocess
import tempfile
import time

from . import config, homeassistant
from .i18n import _

KEY_DIR = os.path.join(config.STATE, "network")
ASKPASS = os.path.join(KEY_DIR, "askpass")
ASKPASS_BODY = '#!/bin/sh\nprintf "%s\\n" "$LOCO_NET_PASS"\n'
LinkError = homeassistant.LinkError
WANTED = {"kex": ("diffie-hellman-group14-sha1", "diffie-hellman-group-exchange-sha1", "diffie-hellman-group1-sha1"),
          "key": ("ssh-rsa",), "cipher": ("aes128-cbc", "aes256-cbc", "3des-cbc")}
_LEGACY = None
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHM]")
# Pagination restée active malgré la commande qui la coupe (ou avant qu'elle soit passée) : un espace la fait suivre.
PAGER = re.compile(r"(?:-- ?More ?--|--More--|More: <space>,\s*Quit: q[^\n]*|Press <SPACE> to continue or <Q> to quit:|"
                   r"-+ ?More \(q=Quit, space/return=Continue\) ?-+|<--- More --->|Press any key to continue[^\n]*)\s*$", re.I)
PAGER_ANY = re.compile(PAGER.pattern[:-4], re.I)      # le même, n'importe où dans le texte
LOGIN_USER = re.compile(r"(?:User ?Name|Username|login)\s*:\s*$", re.I)
LOGIN_PASS = re.compile(r"Password\s*:\s*$", re.I)
CONTINUE = re.compile(r"(?:Press (?:RETURN|ENTER) to (?:get started|continue)|Hit any key to continue)[^\n]*$", re.I)
DENIED = re.compile(r"permission denied|authentication failed|access denied|login invalid|bad passwords?|% login|"
                    r"incorrect (?:user ?name|password)", re.I)
MAX_OUTPUT = 16 * 1024 * 1024
QUIET = 0.3                     # silence qui suit l'invite : la sortie est finie


class CliError(LinkError):
    """La liaison est établie mais l'équipement refuse ou ne répond pas comme prévu."""


# --------------------------------------------------------------------------- ssh et ses réglages

def legacy_options():
    """Algorithmes d'avant 2015 que ce client ssh connaît encore, ajoutés en dernier choix (« + »)."""
    global _LEGACY
    if _LEGACY is None:
        binary, options = homeassistant.ssh_binary(), []
        for kind, option in (("kex", "KexAlgorithms"), ("key", "HostKeyAlgorithms"), ("cipher", "Ciphers")):
            try:
                known = set(subprocess.run([binary, "-Q", kind], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           timeout=10).stdout.decode().split()) if binary else set()
            except (OSError, subprocess.TimeoutExpired):
                known = set()
            names = [n for n in WANTED[kind] if n in known]
            if names:
                options += ["-o", f"{option}=+{','.join(names)}"]
                if kind == "key":
                    options += ["-o", "PubkeyAcceptedAlgorithms=+ssh-rsa"]
        _LEGACY = options
    return list(_LEGACY)


def askpass():
    """Script remis à ssh pour le mot de passe : il le lit dans l'environnement du processus ssh, rien d'autre."""
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    try:
        with open(ASKPASS, encoding="ascii") as f:
            same = f.read() == ASKPASS_BODY
    except OSError:
        same = False
    if not same:
        fd = os.open(ASKPASS + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(ASKPASS_BODY)
        os.replace(ASKPASS + ".new", ASKPASS)
    os.chmod(ASKPASS, 0o700)
    return ASKPASS


def environment(secret, term="vt100"):
    """Environnement de ssh : le mot de passe pour le script askpass, aucun réglage hérité du compte."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": KEY_DIR, "LANG": "C", "LC_ALL": "C", "TERM": term,
            "SSH_ASKPASS": askpass(), "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": ":0", "LOCO_NET_PASS": secret or ""}


def base_argv(host, port, username, known, program="ssh"):
    """Arguments communs à ssh et scp : mot de passe seulement (ni clé ni agent du compte), la clé d'hôte du fichier
    donné et elle seule, une seule tentative (un mot de passe faux ne doit pas verrouiller le compte)."""
    binary = homeassistant.ssh_binary() or "ssh"
    if program != "ssh":
        binary = os.path.join(os.path.dirname(binary), program)
    return ([binary, "-F", os.devnull, "-o", "BatchMode=no", "-o", "StrictHostKeyChecking=yes",
             "-o", "UserKnownHostsFile=" + known, "-o", "GlobalKnownHostsFile=" + os.devnull,
             "-o", "PreferredAuthentications=keyboard-interactive,password", "-o", "PubkeyAuthentication=no",
             "-o", "IdentityAgent=none", "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "-o", "LogLevel=ERROR"]
            + legacy_options() + (["-p", str(int(port)), "-l", username] if program == "ssh" else ["-P", str(int(port))]))


class Known:
    """Fichier des clés d'hôte, écrit depuis la base (c'est elle qui fait foi) le temps d'une liaison."""
    def __init__(self, host, port, host_key, keep=False):
        self.line, self.keep = f"{homeassistant._pattern(host, port)} {host_key}\n", keep

    def __enter__(self):
        os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
        fd, self.path = tempfile.mkstemp(prefix="known-", dir=KEY_DIR)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(self.line)
        return self.path

    def __exit__(self, *_exc):
        if not self.keep:
            remove(self.path)


def remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def probe_host_key(host, port, username="loco"):
    """Clé d'hôte que présente l'équipement, relevée sans rien authentifier ni accepter : (type, clé en base64). Le
    compte de l'équipement est annoncé (sans mot de passe) : son journal ne montre pas d'échec pour un compte inconnu."""
    if not homeassistant.ssh_binary():
        raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
    with tempfile.TemporaryDirectory(prefix="loco-net-") as work:
        known = os.path.join(work, "known")
        argv = ([homeassistant.ssh_binary(), "-F", os.devnull, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "UserKnownHostsFile=" + known, "-o", "GlobalKnownHostsFile=" + os.devnull,
                 "-o", "PreferredAuthentications=none", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
                + legacy_options() + ["-p", str(int(port)), "-l", username or "loco", "--", host, "exit"])
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LinkError(_("L'équipement ne répond pas en SSH à cette adresse : %(e)s", e=str(e)[:160])) from e
        try:
            with open(known, encoding="ascii", errors="replace") as f:
                fields = [line.split() for line in f if line.strip() and not line.startswith("#")]
        except OSError:
            fields = []
        for entry in fields:
            if len(entry) >= 3 and re.fullmatch(r"[A-Za-z0-9+/=]{40,}", entry[2]):
                return entry[1], entry[2]
        reason = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise LinkError(_("L'équipement ne répond pas en SSH à cette adresse : %(e)s",
                          e=(reason[-1] if reason else _("connexion fermée par l'équipement avant l'échange des clés"))[:160]))


def explain(output):
    """Refus de ssh dit clairement : mot de passe refusé, clé d'hôte changée, équipement injoignable."""
    text = clean(output).strip()
    low = text.lower()
    if "host key verification failed" in low or "remote host identification has changed" in low:
        return _("La clé d'hôte de l'équipement n'est plus celle qui a été confirmée : vérifiez qu'il s'agit bien de lui, puis confirmez la nouvelle.")
    if "permission denied" in low or "too many authentication failures" in low:
        return _("L'équipement refuse le compte ou le mot de passe enregistré.")
    if "no matching" in low or "unable to negotiate" in low:
        return _("L'équipement et loco n'ont aucun algorithme SSH en commun : %(e)s", e=text.splitlines()[-1][:200])
    if "connection refused" in low:
        return _("L'équipement refuse la connexion SSH (port fermé ou SSH désactivé).")
    if "timed out" in low or "no route to host" in low or "network is unreachable" in low:
        return _("L'équipement ne répond pas (injoignable depuis loco).")
    return (text.splitlines()[-1] if text else _("liaison SSH interrompue"))[:240]


def clean(text):
    """Texte lisible : séquences d'échappement du terminal ôtées, fins de ligne unifiées, retours arrière appliqués."""
    text = ANSI.sub("", text).replace("\r\n", "\n")
    out = []
    for line in text.split("\n"):
        line = line.rsplit("\r", 1)[-1] if "\r" in line.rstrip("\r") else line.rstrip("\r")
        while "\b" in line:
            i = line.index("\b")
            line = line[:max(i - 1, 0)] + line[i + 1:]
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- conversation avec la ligne de commande

class Session:
    """Une ligne de commande ouverte : `with Session(...) as cli: cli.run("show version")`.

    family : objet de netfamilies (invite, pagination, élévation des droits). secret : mot de passe en clair, gardé le
    temps de la session. enable : mot de passe de l'élévation (Cisco « enable »), le même que celui du compte s'il est vide.
    """

    def __init__(self, host, port, username, secret, host_key, family, enable="", timeout=30):
        self.host, self.port, self.username, self.secret, self.host_key = host, int(port), username, secret, host_key
        self.family, self.enable_secret, self.timeout = family, enable, timeout
        self.proc = self.known = None
        self.buffer, self.prompt, self.base, self.privileged = "", None, "", False
        self.transcript = []

    # -- ouverture et fermeture

    def __enter__(self):
        if not homeassistant.ssh_binary():
            raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
        if not self.host_key:
            raise LinkError(_("La clé d'hôte de l'équipement n'a pas encore été confirmée."))
        self._known = Known(self.host, self.port, self.host_key)
        self.known = self._known.__enter__()
        argv = base_argv(self.host, self.port, self.username, self.known) + ["-tt", "--", self.host]
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=environment(self.secret), start_new_session=True, close_fds=True)
        except OSError as e:
            self._known.__exit__(None, None, None)
            raise LinkError(str(e)[:200]) from e
        try:
            self._login()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                for word in self.family.logout:
                    self._send(word + "\r")
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
        if self.proc is not None:
            for stream in (self.proc.stdout, self.proc.stdin):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.known:
            self._known.__exit__(None, None, None)
            self.known = None

    # -- échanges bruts

    def _send(self, text):
        self.proc.stdin.write(text.encode("utf-8", "replace"))
        self.proc.stdin.flush()

    def _read(self, wait):
        """Lit ce qui arrive pendant au plus `wait` secondes ; chaîne vide si rien. EOFError si ssh s'est arrêté."""
        fd = self.proc.stdout.fileno()
        ready = select.select([fd], [], [], wait)[0]
        if not ready:
            if self.proc.poll() is not None:
                raise EOFError
            return ""
        chunk = os.read(fd, 65536)
        if not chunk:
            raise EOFError
        return chunk.decode("utf-8", "replace")

    def _ended(self):
        """ssh s'est arrêté : ce qu'il a dit en dernier, expliqué."""
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        return explain(self.buffer)

    # -- ouverture de session

    def _login(self):
        """Jusqu'à l'invite : identifiants demandés par l'équipement lui-même (Cisco Small Business), « appuyez sur
        Entrée », puis invite apprise et pagination coupée, droits élevés si la famille le demande."""
        deadline, sent_user, sent_pass, last = time.monotonic() + self.timeout, 0, 0, time.monotonic()
        while True:
            if time.monotonic() > deadline:
                raise CliError(_("L'équipement n'a pas présenté d'invite de commande dans le délai (%(n)s s).", n=self.timeout))
            try:
                chunk = self._read(QUIET)
            except EOFError:
                raise LinkError(self._ended()) from None
            if chunk:
                self.buffer += chunk
                last = time.monotonic()
                if len(self.buffer) > 200000:
                    self.buffer = self.buffer[-100000:]
            tail = clean(self.buffer)[-300:]
            if DENIED.search(tail[-160:]) and sent_pass:
                raise LinkError(_("L'équipement refuse le compte ou le mot de passe enregistré."))
            if LOGIN_USER.search(tail):
                if sent_user >= 2:
                    raise LinkError(_("L'équipement refuse le compte ou le mot de passe enregistré."))
                sent_user += 1
                self.buffer = ""
                self._send(self.username + "\r")
                continue
            if LOGIN_PASS.search(tail):
                if sent_pass >= 2:
                    raise LinkError(_("L'équipement refuse le compte ou le mot de passe enregistré."))
                sent_pass += 1
                self.buffer = ""
                self._send(self.secret + "\r")
                continue
            if CONTINUE.search(tail) or PAGER.search(tail):
                self.buffer = ""
                self._send("\r" if CONTINUE.search(tail) else " ")
                continue
            m = self.family.learn.search(tail)
            if m and time.monotonic() - last > QUIET:
                break
            if not chunk and time.monotonic() - last > 4:
                self._send("\r")            # bannière muette, ou invite qui n'arrive qu'après une touche
                last = time.monotonic()
        self._learn()
        if self.family.elevate:
            self._elevate()
        for command in self.family.prepare:
            self.run(command, check=False, timeout=15)

    def _learn(self):
        """Invite confirmée : un retour à la ligne la fait réapparaître, identique (une bannière qui finirait par « # »
        ne trompe pas loco)."""
        for _attempt in range(3):
            self.buffer = ""
            self._send("\r")
            text = self._until(self.family.learn, 10, learning=True)
            lines = [line for line in clean(text).split("\n") if line.strip()]
            m = self.family.learn.search("\n" + lines[-1]) if lines else None
            if m:
                self.base = m.group("base")
                self.prompt = self.family.prompt(self.base)
                self.privileged = m.group("mark").strip().endswith("#")
                return
        raise CliError(_("Invite de commande de l'équipement non reconnue."))

    def _elevate(self):
        """Mode privilégié (Cisco IOS : « enable ») quand l'invite se termine par « > »."""
        if self.privileged:
            return
        self.buffer = ""
        self._send(self.family.elevate + "\r")
        deadline = time.monotonic() + 15
        sent = False
        while time.monotonic() < deadline:
            try:
                chunk = self._read(QUIET)
            except EOFError:
                raise LinkError(self._ended()) from None
            self.buffer += chunk
            tail = clean(self.buffer)[-200:]
            if LOGIN_PASS.search(tail):
                if sent:
                    break
                sent = True
                self.buffer = ""
                self._send((self.enable_secret or self.secret) + "\r")
                continue
            m = self.prompt.search(tail)
            if m:
                self.privileged = m.group("mark").strip().endswith("#")
                if not self.privileged:
                    break
                return
        raise CliError(_("Mode privilégié refusé (enable) : vérifiez le mot de passe enable enregistré pour cet équipement."))

    # -- commandes

    def _until(self, pattern, timeout, learning=False):
        """Lit jusqu'à ce que `pattern` corresponde à la fin du texte reçu, suivie d'un court silence (l'invite doit
        être la toute fin), en faisant suivre la pagination. Le délai repart à chaque donnée reçue, jusqu'à dix fois le
        délai au plus. learning : l'invite n'est pas encore connue, un retour à la ligne la redemande au besoin."""
        start = time.monotonic()
        deadline, hard, last = start + timeout, start + timeout * 10, start
        while True:
            now = time.monotonic()
            if now > deadline or now > hard:
                raise CliError(_("L'équipement n'a pas rendu la main dans le délai (%(n)s s).", n=int(timeout)))
            try:
                chunk = self._read(QUIET)
            except EOFError:
                raise LinkError(self._ended()) from None
            if chunk:
                self.buffer += chunk
                last = deadline = time.monotonic()
                deadline += timeout
                if len(self.buffer) > MAX_OUTPUT:
                    raise CliError(_("Sortie trop longue (plus de 16 Mo) : commande interrompue."))
                continue
            tail = clean(self.buffer[-600:])
            if PAGER.search(tail):
                self.buffer = PAGER_ANY.sub("", self.buffer)
                self._send(" ")
                continue
            if pattern.search(tail):
                return self.buffer
            if learning and now - last > 2:
                self._send("\r")
                last = now

    def run(self, command, timeout=60, check=True):
        """Envoie une commande et rend sa sortie, sans l'écho de la commande ni l'invite qui suit. Sortie d'erreur de
        l'équipement (« % Invalid input », « Error: ») : CliError si check, sinon rendue telle quelle."""
        self.buffer = ""
        self._send(command + "\r")
        raw = self._until(self.prompt, timeout)
        text = clean(PAGER_ANY.sub("", raw))
        lines = text.split("\n")
        # L'écho de la commande (parfois découpé, ou précédé de l'invite) puis l'invite finale sont retirés.
        if lines and command[:20].strip() and command[:20].strip() in lines[0]:
            lines = lines[1:]
        while lines and (not lines[-1].strip() or self.prompt.search("\n" + lines[-1])):
            lines.pop()
        out = "\n".join(lines).strip("\n")
        self.transcript.append((command, out))
        if check and self.family.error.search(out[:400]):
            raise CliError(_("L'équipement refuse la commande « %(c)s » : %(e)s", c=command, e=out.strip().splitlines()[0][:200]))
        return out

    def run_interactive(self, command, answers, timeout=120):
        """Commande qui pose des questions (« [confirm] », « (y/N) ») : chaque (motif, réponse) est donné à son tour,
        jusqu'au retour de l'invite — ou jusqu'à la fin de la liaison (redémarrage). Rend le texte reçu.

        Une réponse d'une seule touche (« y ») suffit à certains systèmes, d'autres attendent Entrée : la touche part
        seule, puis Entrée si la question reste affichée sans suite — une Entrée de trop répondrait « non » à la
        question suivante sur les premiers."""
        self.buffer = ""
        self._send(command + "\r")
        deadline = time.monotonic() + timeout
        pending, waiting = list(answers), None
        while time.monotonic() < deadline:
            try:
                chunk = self._read(QUIET)
            except EOFError:
                return clean(self.buffer)
            now = time.monotonic()
            if chunk:
                self.buffer += chunk
                deadline = max(deadline, now + timeout / 4)
                if waiting:
                    since = clean(self.buffer[waiting[2]:]).strip()
                    waiting = waiting if since.lower() in ("", waiting[0].lower()) else None    # rien de plus que l'écho
                    if waiting:
                        waiting = (waiting[0], now, waiting[2])
                continue
            tail = clean(self.buffer[-400:])
            if waiting:
                if now - waiting[1] > 1.5:
                    self._send("\r")
                    waiting = None
                continue
            for i, (pattern, answer) in enumerate(pending):
                if re.search(pattern, tail, re.I):
                    self._send(answer)
                    self.buffer += "\n"
                    pending.pop(i)
                    if not answer.endswith("\r"):
                        waiting = (answer, now, len(self.buffer))
                    break
            else:
                if self.prompt.search(tail):
                    return clean(self.buffer)
        raise CliError(_("L'équipement n'a pas rendu la main dans le délai (%(n)s s).", n=int(timeout)))


def push_file(host, port, username, secret, host_key, local, remote, legacy=True, timeout=7200):
    """Copie un fichier vers l'équipement par scp (protocole SCP d'origine quand `legacy`, que gardent les switchs ;
    sinon SFTP). Rend (réussi, sortie)."""
    with Known(host, port, host_key) as known:
        argv = base_argv(host, port, username, known, program="scp") + (["-O"] if legacy else []) + ["-q", "--", local,
                                                                                                      f"{username}@{_bracket(host)}:{remote}"]
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=environment(secret), timeout=timeout, start_new_session=True)
        except subprocess.TimeoutExpired:
            return False, _("Copie interrompue : plus de deux heures.")
        except OSError as e:
            return False, str(e)[:200]
    out = done.stdout.decode("utf-8", "replace")
    return done.returncode == 0, out


def _bracket(host):
    return f"[{host}]" if ":" in host else host
