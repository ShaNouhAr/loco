"""Serveur Windows natif : configuration, service (nginx, portail, tâches) et sauvegardes.

Module hors du paquet loco : la configuration JSON doit être appliquée avant de l'importer.
Lancé par windows\\install.ps1 et par le service Windows « loco » (WinSW) :
  python -m locowin configure        dossiers, certificats, clé de signature, comptes, nginx
  python -m locowin service          service : nginx, portail (waitress) et tâches périodiques
  python -m locowin stats            taille des caches
  python -m locowin backup create|list|restore ARCHIVE
  python -m locowin user list|add|passwd|enable|disable …

La configuration est lue dans le fichier JSON désigné par LOCO_CONFIG (écrit par install.ps1).
"""

import datetime
import ipaddress
import json
import os
from pathlib import PurePosixPath
import secrets
import shutil
import signal
import socket
import locosnapshot
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

CONFIG_PATH = os.environ.get("LOCO_CONFIG", r"C:\ProgramData\loco\etc\loco.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("program_dir", "data_dir", "state_dir", "etc_dir", "log_dir", "backup_dir", "nginx_dir", "host"):
        if not cfg.get(key):
            sys.exit(f"Configuration incomplète : {key} manquant dans {path}")
    for key in ("data_dir", "state_dir", "etc_dir", "log_dir", "backup_dir", "nginx_dir"):
        if any(c in cfg[key] for c in ' ";{}'):
            sys.exit(f"{key} : chemin sans espace ni guillemet attendu (nginx) : {cfg[key]}")
    for key in ("allow", "admin_allow"):
        cfg[key] = [str(ipaddress.ip_network(n, strict=False)) for n in cfg.get(key) or []]
    cfg.setdefault("https_port", 8443)
    cfg.setdefault("http_port", 8088)
    cfg.setdefault("app_port", 8089)
    cfg.setdefault("acng_port", 3142)
    cfg.setdefault("resolvers", ["1.1.1.1", "9.9.9.9"])
    cfg.setdefault("dns", [])
    cfg.setdefault("backup_keep", 3)
    return cfg


def apply_environment(cfg):
    """Variables lues par loco.config : à fixer avant d'importer le portail."""
    https = f"https://{cfg['host']}:{cfg['https_port']}"
    http = f"http://{cfg['host']}:{cfg['http_port']}" if cfg.get("http_port") else ""
    tmp = os.path.join(cfg["data_dir"], ".tmp")
    os.environ.update({
        "LOCO_RUNTIME": "windows", "LOCO_DATA": cfg["data_dir"], "LOCO_STATE": cfg["state_dir"], "LOCO_ETC": cfg["etc_dir"],
        "LOCO_HOST": cfg["host"], "LOCO_ADMIN_URL": https, "LOCO_PUBLIC_URL": http, "LOCO_BACKUP_DIR": cfg["backup_dir"],
        "LOCO_ACNG_PORT": str(cfg["acng_port"]), "LOCO_FLATPAK": "1" if cfg.get("flatpak", True) else "0",
        "LOCO_RPM": "1" if cfg.get("rpm", True) else "0",
        "LOCO_WU_LOG": os.path.join(cfg["log_dir"], "loco-wu.log"), "TEMP": tmp, "TMP": tmp,
    })
    if cfg.get("proxy"):
        os.environ.update({"http_proxy": cfg["proxy"], "https_proxy": cfg["proxy"],
                           "no_proxy": f"localhost,127.0.0.1,{cfg['host']}"})


def say(text):
    print(f"==> {text}", flush=True)


def _slash(path):
    return path.replace("\\", "/")


def hostname():
    return socket.gethostname()


# --------------------------------------------------------------------------- certificats

def _new_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path, key):
    from cryptography.hazmat.primitives import serialization
    with open(path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


def _write_cert(path, cert):
    from cryptography.hazmat.primitives import serialization
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def ensure_tls(cfg):
    """Autorité locale (10 ans) et certificat serveur (825 jours), comme install.sh ; refait le certificat
    s'il manque, expire dans moins de 30 jours ou ne couvre plus l'adresse annoncée."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    tls = os.path.join(cfg["etc_dir"], "tls")
    os.makedirs(tls, exist_ok=True)
    if os.path.exists(os.path.join(tls, "custom")):
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key_path, ca_path = os.path.join(tls, "ca.key"), os.path.join(tls, "ca.crt")
    server_path, server_key_path = os.path.join(tls, "server.crt"), os.path.join(tls, "server.key")
    if not os.path.exists(ca_key_path):
        key = _new_key()
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "loco"),
                          x509.NameAttribute(NameOID.COMMON_NAME, f"loco CA ({hostname()})")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        _write_key(ca_key_path, key)
        _write_cert(ca_path, cert)
        if os.path.exists(server_path):
            os.unlink(server_path)
    with open(ca_path, "rb") as f:
        ca = x509.load_pem_x509_certificate(f.read())
    with open(ca_key_path, "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    ip = ipaddress.ip_address(cfg["host"]) if _is_ip(cfg["host"]) else None
    renew = True
    if os.path.exists(server_path):
        with open(server_path, "rb") as f:
            current = x509.load_pem_x509_certificate(f.read())
        try:
            san = current.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covered = (ip in san.get_values_for_type(x509.IPAddress)) if ip else \
                (cfg["host"] in san.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            covered = False
        renew = not covered or current.not_valid_after_utc - now < datetime.timedelta(days=30)
    if renew:
        names = [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName(hostname()), x509.DNSName("localhost")]
        names.insert(0, x509.IPAddress(ip) if ip else x509.DNSName(cfg["host"]))
        fqdn = socket.getfqdn()
        if fqdn and fqdn not in (hostname(), "localhost") and "." in fqdn:
            names.append(x509.DNSName(fqdn))
        key = _new_key()
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "loco"),
                                         x509.NameAttribute(NameOID.COMMON_NAME, hostname())]))
                .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
                .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        _write_key(server_key_path, key)
        _write_cert(server_path, cert)
        say("Certificat HTTPS du serveur émis par l'autorité locale")


def ca_fingerprint(cfg):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    try:
        with open(os.path.join(cfg["etc_dir"], "tls", "ca.crt"), "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
    except OSError:
        return ""
    return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def write_ca_bundle(cfg):
    """Autorités racines de Windows au format PEM : nginx vérifie ainsi les miroirs (Flathub, Rocky…)."""
    pems = []
    seen = set()
    for store in ("ROOT", "CA"):
        for der, encoding, trust in ssl.enum_certificates(store):
            if encoding != "x509_asn" or der in seen:
                continue
            if trust is not True and "1.3.6.1.5.5.7.3.1" not in trust:   # autorités de serveurs TLS
                continue
            seen.add(der)
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    path = os.path.join(cfg["etc_dir"], "tls", "windows-roots.pem")
    with open(path + ".new", "w", encoding="ascii") as f:
        f.write("".join(pems))
    os.replace(path + ".new", path)
    return path


# --------------------------------------------------------------------------- nginx

def nginx_exe(cfg):
    return os.path.join(cfg["program_dir"], "nginx", "nginx.exe")


def _render(text, values):
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


def render_nginx(cfg):
    """Configuration nginx produite à partir des mêmes gabarits que sous Linux (dossier conf du programme)."""
    conf_dir = os.path.join(cfg["nginx_dir"], "conf")
    snippets = os.path.join(conf_dir, "snippets")
    for d in (snippets, os.path.join(cfg["nginx_dir"], "logs"), os.path.join(cfg["nginx_dir"], "temp")):
        os.makedirs(d, exist_ok=True)
    templates = os.path.join(cfg["program_dir"], "conf")
    loopback = ["127.0.0.0/8", "::1"]

    def acl(networks):
        return "".join(f"allow {n};\n" for n in loopback + [n for n in networks if n not in loopback]) + "deny all;\n"

    values = {
        "DATA": _slash(cfg["data_dir"]), "HOST": cfg["host"], "HOSTNAME": hostname(),
        "HTTPS_PORT": str(cfg["https_port"]), "HTTPS_OLD_LISTEN": "", "PORTAL_PORT": str(cfg.get("http_port") or 0),
        "APP_PORT": str(cfg["app_port"]), "ACNG_PORT": str(cfg["acng_port"]),
        "MAX_UPLOAD": cfg.get("max_upload", "8g"), "WU_CACHE_SIZE": cfg.get("wu_cache_size", "50g"),
        "FLATPAK_CACHE_SIZE": cfg.get("flatpak_cache_size", "20g"), "RPM_CACHE_SIZE": cfg.get("rpm_cache_size", "20g"),
        "APT_CACHE_SIZE": cfg.get("apt_cache_size", "50g"), "RESOLVERS": " ".join(cfg["resolvers"]),
        "APT_SELF_HOSTS": "\n    ".join(f"{h} 1;" for h in
                                         dict.fromkeys(n.lower() for n in ("localhost", "127.0.0.1", cfg["host"], hostname()))),
        "DNS": " ".join(cfg["dns"] or cfg["resolvers"]), "LOGDIR": _slash(cfg["log_dir"]),
        "CA_BUNDLE": _slash(write_ca_bundle(cfg)),
    }

    def tpl(name):
        with open(os.path.join(templates, name), encoding="utf-8") as f:
            text = _render(f.read(), values)
        # Chemins Linux des gabarits ramenés aux dossiers de l'installation Windows.
        # Fichiers statiques du portail : chemin du programme (avec une espace), donc entre guillemets.
        static = _slash(os.path.join(cfg["program_dir"], "portal", "static")) + "/"
        text = text.replace("alias /opt/loco/portal/static/;", f'alias "{static}";')
        for linux, windows in (("/etc/loco/", _slash(cfg["etc_dir"]) + "/"),
                               ("/var/lib/loco/", _slash(cfg["state_dir"]) + "/"),
                               ("/var/log/nginx/", _slash(cfg["log_dir"]) + "/"),
                               ("/opt/loco/portal/", _slash(os.path.join(cfg["program_dir"], "portal")) + "/"),
                               ("/etc/nginx/snippets/", "snippets/")):
            text = text.replace(linux, windows)
        # nginx pour Windows est un programme 32 bits : zones de clés de cache plus petites.
        return text.replace("keys_zone=loco_wu:256m", "keys_zone=loco_wu:64m")

    files = {
        "snippets/loco-clients.conf": "# loco : réseaux autorisés pour les postes.\n" + acl(cfg["allow"]),
        "snippets/loco-admin.conf": "# loco : réseaux autorisés pour l'interface.\n" + acl(cfg["admin_allow"]),
        "snippets/loco-proxy.conf": tpl("nginx-proxy-snippet.conf"),
        "snippets/loco-flathub-proxy.conf": tpl("nginx-flathub-proxy-snippet.conf"),
        "snippets/loco-rpm-proxy.conf": tpl("nginx-rpm-proxy-snippet.conf"),
    }
    values["FLATPAK_LOCATIONS"] = tpl("nginx-flatpak-locations.conf") if cfg.get("flatpak", True) else ""
    values["RPM_LOCATIONS"] = tpl("nginx-rpm-locations.conf") if cfg.get("rpm", True) else ""
    portal = ""
    if cfg.get("flatpak", True):
        portal += tpl("nginx-flatpak-cache.conf")
    if cfg.get("rpm", True):
        portal += tpl("nginx-rpm-cache.conf")
    portal += tpl("nginx-portal.conf")
    if cfg.get("http_port"):
        portal += tpl("nginx-portal-http.conf")
    files["loco-portal.conf"] = portal
    files["loco-apt.conf"] = tpl("nginx-apt-proxy.conf")
    main = [
        "# Généré par loco (serveur Windows) : relancer le service pour appliquer une modification.",
        "worker_processes 1;", "error_log " + _slash(os.path.join(cfg["log_dir"], "nginx-error.log")) + " warn;",
        "pid logs/nginx.pid;", "events {", "    worker_connections 1000;", "}",
    ]
    if cfg.get("wu", True):
        files["loco-stream.conf"] = tpl("nginx-stream.conf")
        main.append("include loco-stream.conf;")
    main += [
        "http {", f"    include \"{_slash(os.path.join(cfg['program_dir'], 'nginx', 'conf', 'mime.types'))}\";",
        "    default_type application/octet-stream;", "    server_tokens off;", "    access_log off;",
        "    client_body_temp_path temp/client_body;", "    proxy_temp_path temp/proxy;",
        "    fastcgi_temp_path temp/fastcgi;", "    uwsgi_temp_path temp/uwsgi;", "    scgi_temp_path temp/scgi;",
        "    include loco-portal.conf;", "    include loco-apt.conf;",
    ]
    if cfg.get("wu", True):
        files["loco-wucache.conf"] = tpl("nginx-wucache.conf")
        main.append("    include loco-wucache.conf;")
    main.append("}")
    files["nginx.conf"] = "\n".join(main) + "\n"
    for old in ("loco-stream.conf", "loco-wucache.conf"):
        if old not in files and os.path.exists(os.path.join(conf_dir, old)):
            os.unlink(os.path.join(conf_dir, old))
    for name, text in files.items():
        path = os.path.join(conf_dir, name)
        with open(path + ".new", "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(path + ".new", path)


def nginx(cfg, *args, check=True):
    return subprocess.run([nginx_exe(cfg), "-p", _slash(cfg["nginx_dir"]) + "/", "-c", "conf/nginx.conf", *args],
                          cwd=cfg["nginx_dir"], capture_output=True, text=True, timeout=60,
                          creationflags=NO_WINDOW, check=check)


def nginx_test(cfg):
    out = nginx(cfg, "-t", check=False)
    if out.returncode != 0:
        sys.exit("Configuration nginx refusée :\n" + out.stderr.strip())


# --------------------------------------------------------------------------- configuration

def ensure_secret(cfg):
    path = os.path.join(cfg["etc_dir"], "portal.secret")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not data.get("secret_key"):
        data["secret_key"] = secrets.token_hex(32)
        with open(path, "w") as f:
            json.dump(data, f)


def configure(cfg, password=""):
    """Équivalent d'install.sh pour Windows ; relançable (mise à jour, changement de réglages)."""
    for d in ("apt/pool", "apt/repositories", "win", "rpm", "wucache", "aptcache", "rpmcache", "flatpakcache", ".tmp"):
        os.makedirs(os.path.join(cfg["data_dir"], d), exist_ok=True)
    for d in (os.path.join(cfg["state_dir"], "nginx"),
              os.path.join(cfg["etc_dir"], "tls"), cfg["log_dir"], cfg["backup_dir"]):
        os.makedirs(d, exist_ok=True)
    ensure_secret(cfg)
    from loco import app, auth, cli, config, signing
    from loco.db import transaction
    with app.app_context():
        cli.main(["init"])
        # Clé OpenPGP des dépôts (RSA 4096), gérée en Python : pas de GnuPG sous Windows.
        if signing.ensure_key():
            say("Création de la clé de signature des dépôts")
        with open(os.path.join(config.APT_DIR, "loco.gpg.new"), "wb") as f:
            f.write(signing.public_key())
        os.replace(os.path.join(config.APT_DIR, "loco.gpg.new"), os.path.join(config.APT_DIR, "loco.gpg"))
        say("Dépôts APT et RPM")
        cli.main(["rebuild-apt"])
        cli.main(["write-maps"])
        if cfg.get("rpm", True):
            cli.main(["rpm-init"])
    say("Certificat HTTPS")
    ensure_tls(cfg)
    say("Configuration de nginx")
    render_nginx(cfg)
    nginx_test(cfg)
    stats(cfg)
    # Compte créé en dernier : un mot de passe généré n'est jamais perdu sur une installation interrompue.
    say("Comptes du portail")
    issued = ""
    with app.app_context():
        with transaction() as conn:
            empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            if password and empty:
                auth.create_user(conn, "admin", password, "admin", must_change=False)
            elif password:
                row = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
                if row:
                    auth.set_password(conn, row["id"], password, must_change=False)
            elif empty:
                alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
                issued = "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))
                auth.create_user(conn, "admin", issued, "admin", must_change=True)
            if empty:
                conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                             (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "installation", "console",
                              "comptes.ajout", "admin (installation)"))
    return {"issued": issued, "ca_fingerprint": ca_fingerprint(cfg),
            "admin_url": f"https://{cfg['host']}:{cfg['https_port']}",
            "http_url": f"http://{cfg['host']}:{cfg['http_port']}" if cfg.get("http_port") else ""}


# --------------------------------------------------------------------------- tâches

def dir_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def stats(cfg):
    d = cfg["data_dir"]
    data = {"acng": dir_size(os.path.join(d, "aptcache")), "wucache": dir_size(os.path.join(d, "wucache")),
            "flatpakcache": dir_size(os.path.join(d, "flatpakcache")), "rpmcache": dir_size(os.path.join(d, "rpmcache")),
            "rpm": dir_size(os.path.join(d, "rpm")), "apt": dir_size(os.path.join(d, "apt")),
            "win": dir_size(os.path.join(d, "win")), "flatpak": 0,
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    path = os.path.join(cfg["state_dir"], "stats.json")
    with open(path + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)


def rotate_logs(cfg, keep=14):
    """Rotation quotidienne des journaux nginx (renommés, puis réouverts par nginx)."""
    stamp = datetime.date.today().strftime("%Y%m%d")
    rotated = False
    for name in os.listdir(cfg["log_dir"]):
        path = os.path.join(cfg["log_dir"], name)
        if name.endswith(".log") and os.path.getsize(path) > 0:
            target = f"{path}.{stamp}"
            if not os.path.exists(target):
                os.replace(path, target)
                rotated = True
    if rotated:
        nginx(cfg, "-s", "reopen", check=False)
    old = sorted(n for n in os.listdir(cfg["log_dir"]) if ".log." in n)
    by_base = {}
    for n in old:
        by_base.setdefault(n.split(".log.")[0], []).append(n)
    for names in by_base.values():
        for n in names[:-keep]:
            os.unlink(os.path.join(cfg["log_dir"], n))


# --------------------------------------------------------------------------- sauvegardes

def backup_create(cfg, dest=None):
    dest = dest or cfg["backup_dir"]
    os.makedirs(dest, exist_ok=True)
    name = f"loco-{hostname()}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tempfile.TemporaryDirectory(dir=os.path.join(cfg["data_dir"], ".tmp")) as work:
        db_copy = os.path.join(work, "portal.db")
        locosnapshot.snapshot(os.path.join(cfg["state_dir"], "portal.db"), db_copy)
        archive = os.path.join(work, name)
        skip_state = {"portal.db", "portal.db-wal", "portal.db-shm", "service-status.json"}

        def keep(info):
            base = os.path.basename(info.name)
            if info.name.startswith("state/") and (base in skip_state or base.startswith("S.")):
                return None
            return info
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(cfg["etc_dir"], "etc", filter=keep)
            tar.add(cfg["state_dir"], "state", filter=keep)
            for d in ("apt", "win", "rpm"):
                path = os.path.join(cfg["data_dir"], d)
                if os.path.isdir(path):
                    tar.add(path, f"data/{d}", filter=lambda i: None if "/." in i.name else i)
            tar.add(db_copy, "portal.db")
        shutil.move(archive, os.path.join(dest, name))
    keep_n = int(cfg.get("backup_keep", 3))
    if keep_n > 0 and dest == cfg["backup_dir"]:
        archives = sorted((n for n in os.listdir(dest) if n.startswith("loco-") and n.endswith(".tar.gz")),
                          key=lambda n: os.path.getmtime(os.path.join(dest, n)), reverse=True)
        for n in archives[keep_n:]:
            os.unlink(os.path.join(dest, n))
    path = os.path.join(dest, name)
    print(f"{path} ({os.path.getsize(path) // 1024} Kio)")
    return path


def backup_restore(cfg, archive):
    """À lancer service arrêté (loco.cmd backup restore s'en charge)."""
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        if "portal.db" not in names or not {"etc/loco.json", "etc/local-repo.json"}.intersection(names):
            sys.exit("Ce n'est pas une sauvegarde loco pour Windows.")
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in member.name
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Chemin ou type de fichier invalide dans la sauvegarde.")
        targets = {"etc": cfg["etc_dir"], "state": cfg["state_dir"], "data": cfg["data_dir"]}
        for member in tar.getmembers():
            top, _, rest = member.name.partition("/")
            if member.name == "portal.db":
                dest = os.path.join(cfg["state_dir"], "portal.db")
            elif top in targets and rest:
                if rest in ("loco.json", "local-repo.json") and top == "etc":
                    continue    # la configuration propre à ce serveur (adresse, ports, réseaux) est gardée
                dest = os.path.join(targets[top], *rest.split("/"))
            else:
                continue
            if ".." in member.name.split("/") or not (member.isfile() or member.isdir()):
                continue
            if member.isdir():
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with tar.extractfile(member) as src, open(dest + ".restore", "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(dest + ".restore", dest)
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(os.path.join(cfg["state_dir"], "portal.db" + suffix))
        except OSError:
            pass
    print("Archive restaurée.")


def backup_list(cfg):
    try:
        names = sorted(n for n in os.listdir(cfg["backup_dir"]) if n.endswith(".tar.gz"))
    except OSError:
        names = []
    for n in names:
        path = os.path.join(cfg["backup_dir"], n)
        print(f"{n}  {os.path.getsize(path) // 1024} Kio  "
              f"{datetime.datetime.fromtimestamp(os.path.getmtime(path)):%Y-%m-%d %H:%M}")
    if not names:
        print(f"Aucune sauvegarde dans {cfg['backup_dir']}.")


# --------------------------------------------------------------------------- service

class Service:
    """Processus du service Windows : nginx (processus enfant), portail (waitress) et tâches périodiques."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.stop = threading.Event()
        self.nginx = None
        self.tasks_alive = False
        self.versions = {}

    def write_status(self):
        status = {"updated": time.time(), "versions": self.versions,
                  "services": {"nginx": self.nginx is not None and self.nginx.poll() is None,
                               "loco-tasks": self.tasks_alive}}
        path = os.path.join(self.cfg["state_dir"], "service-status.json")
        with open(path + ".tmp", "w") as f:
            json.dump(status, f)
        os.replace(path + ".tmp", path)

    def start_nginx(self):
        log = open(os.path.join(self.cfg["log_dir"], "nginx-stderr.log"), "ab")
        self.nginx = subprocess.Popen([nginx_exe(self.cfg), "-p", _slash(self.cfg["nginx_dir"]) + "/", "-c", "conf/nginx.conf"],
                                      cwd=self.cfg["nginx_dir"], stdout=log, stderr=log, creationflags=NO_WINDOW)
        log.close()

    def stop_nginx(self):
        if self.nginx and self.nginx.poll() is None:
            nginx(self.cfg, "-s", "quit", check=False)
            try:
                self.nginx.wait(20)
            except subprocess.TimeoutExpired:
                nginx(self.cfg, "-s", "stop", check=False)
                self.nginx.kill()

    def collect_versions(self):
        import platform
        out = nginx(self.cfg, "-v", check=False)
        self.versions["nginx"] = (out.stderr or out.stdout).strip().split("/")[-1]
        from importlib.metadata import version
        self.versions["cryptography"] = version("cryptography")
        self.versions["waitress"] = version("waitress")
        self.versions["windows"] = f"{platform.release()} ({platform.version()})"

    def tasks(self):
        cfg = self.cfg
        state = os.path.join(cfg["state_dir"], "nginx", "wu-http.map")
        last_map = os.path.getmtime(state) if os.path.exists(state) else 0
        next_stats = 0
        next_daily = 0
        day_file = os.path.join(cfg["state_dir"], ".daily-tasks")
        if not os.path.exists(day_file):
            with open(day_file, "w") as f:
                f.write(datetime.date.today().isoformat())
        while not self.stop.is_set():
            self.tasks_alive = True
            try:
                if self.nginx and self.nginx.poll() is not None and not self.stop.is_set():
                    print("nginx s'est arrêté : redémarrage", flush=True)
                    self.start_nginx()
                current = os.path.getmtime(state) if os.path.exists(state) else 0
                if current != last_map:
                    last_map = current
                    if nginx(cfg, "-t", check=False).returncode == 0:
                        nginx(cfg, "-s", "reload", check=False)
                now = time.time()
                if now >= next_stats:
                    stats(cfg)
                    next_stats = now + 900
                today = datetime.date.today().isoformat()
                with open(day_file) as f:
                    done = f.read().strip()
                if datetime.datetime.now().strftime("%H%M") >= "0330" and done != today and now >= next_daily:
                    next_daily = now + 300
                    rotate_logs(cfg)
                    ensure_tls(cfg)
                    write_ca_bundle(cfg)
                    nginx(cfg, "-s", "reload")
                    if int(cfg.get("backup_keep", 3)) > 0:
                        backup_create(cfg)
                    with open(day_file, "w") as f:
                        f.write(today)
                self.write_status()
            except Exception as e:  # noqa: BLE001 - une tâche en échec ne doit pas arrêter le service
                print(f"Tâche périodique en échec : {e}", flush=True)
            self.stop.wait(5)
        self.tasks_alive = False

    def run(self):
        from waitress import create_server
        render_nginx(self.cfg)
        nginx_test(self.cfg)
        ensure_tls(self.cfg)
        self.collect_versions()
        import app as entry      # portal/app.py : migrations et application Flask
        server = create_server(entry.app, host="127.0.0.1", port=int(self.cfg["app_port"]), threads=8,
                               ident="loco", clear_untrusted_proxy_headers=False,
                               max_request_body_size=1 << 40, channel_timeout=300)
        self.start_nginx()
        web = threading.Thread(target=server.run, name="portail", daemon=True)
        web.start()
        tasks = threading.Thread(target=self.tasks, name="taches", daemon=True)
        tasks.start()
        print(f"loco démarré : https://{self.cfg['host']}:{self.cfg['https_port']}", flush=True)

        def stop(*_):
            self.stop.set()
        for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, sig):
                signal.signal(getattr(signal, sig), stop)
        try:
            while not self.stop.wait(1):
                if not web.is_alive() or not tasks.is_alive():
                    raise RuntimeError("Un composant du service s'est arrêté ; redémarrage par Windows.")
        finally:
            self.stop.set()
            print("Arrêt de loco", flush=True)
            self.stop_nginx()
            server.close()
            tasks.join(10)
            self.write_status()


# --------------------------------------------------------------------------- ligne de commande

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    cfg = load_config()
    apply_environment(cfg)
    cmd = argv[0]
    if cmd == "configure":
        result = configure(cfg, password=os.environ.pop("LOCO_PASSWORD", ""))
        print("LOCALREPO-RESULT " + json.dumps(result), flush=True)
    elif cmd == "service":
        Service(cfg).run()
    elif cmd == "stats":
        stats(cfg)
    elif cmd == "backup":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "create":
            backup_create(cfg, argv[2] if len(argv) > 2 else None)
        elif sub == "list":
            backup_list(cfg)
        elif sub == "restore" and len(argv) > 2:
            backup_restore(cfg, argv[2])
        else:
            sys.exit("backup create [DOSSIER] | list | restore ARCHIVE")
    elif cmd == "user":
        from loco import app, cli
        with app.app_context():
            cli.main(["user"] + argv[1:])
    elif cmd == "fingerprint":
        print(ca_fingerprint(cfg))
    elif cmd == "check":
        render_nginx(cfg)
        nginx_test(cfg)
        print("Configuration nginx valide.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
