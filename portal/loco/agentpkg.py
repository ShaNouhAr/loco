"""Paquets d'installation de l'agent Linux (.deb et .rpm), produits à la demande pour ce serveur.

Le paquet embarque le script d'installation rendu par le portail (agent, autorité épinglée, adresse et clé
d'inscription) ; son script de post-installation l'exécute, exactement comme `curl … | sudo sh`. L'agent reste maître
de ses fichiers (mise à jour automatique, `loco-client remove`) : dpkg et rpm ne possèdent que les scripts.
Désinstaller le paquet retire l'agent du poste ; le poste se retire de la liste du parc depuis le portail.

Variante « agent et dépôts » : le même paquet configure aussi les dépôts et le cache de loco (scripts setup.sh ou
rpm-setup.sh, lancés avec --from-package) après avoir confié l'autorité de loco au système. Elle porte une version un
cran au-dessus (« +depots », « 1.depots ») : passer de l'agent seul au paquet complet est une simple mise à jour, qui
garde l'agent et son identité ; désinstaller le paquet complet retire aussi la configuration des dépôts."""

import gzip
import hashlib
import io
import os
import struct
import tarfile
import textwrap
import time
import urllib.parse

DIRECTORY = "/usr/share/loco-client"
SCRIPT = DIRECTORY + "/install.sh"
REPOS = DIRECTORY + "/repos.sh"
CA_FILE = DIRECTORY + "/loco-ca.crt"
AGENT = "/usr/local/lib/loco-client/loco-client.py"
REMOVE = f"if [ -x {AGENT} ]; then {AGENT} remove || true; fi"
FLAVOR = "depots"
SUMMARY = "agent du parc loco"
DESCRIPTION = ("Inscrit ce poste dans « Mon parc » de loco ({server}) : inventaire, mises à jour, actions et accès "
               "distant. L'agent se met à jour seul ; désinstaller ce paquet le retire du poste.")
DESCRIPTION_REPOS = (" Branche aussi le poste sur les dépôts et le cache de loco (autorité de loco confiée au système), "
                     "configuration retirée avec le paquet.")
# Autorité et repli par système : sans dépôt local pour cette version, le cache seul reste utile.
SYSTEMS = {
    "deb": ("/usr/local/share/ca-certificates/loco.crt", "update-ca-certificates", "--cache-only", "cache APT"),
    "rpm": ("/etc/pki/ca-trust/source/anchors/loco.crt", "update-ca-trust extract", "--no-local", "cache DNF"),
}


def _host(server):
    return urllib.parse.urlsplit(server).hostname or "localhost"


def deb_name(version, repos=False):
    return f"loco-client_{version}{'+' + FLAVOR if repos else ''}_all.deb"


def rpm_name(version, repos=False):
    return f"loco-client-{version}-1{'.' + FLAVOR if repos else ''}.noarch.rpm"


def _bytes(text):
    return text.encode() if isinstance(text, str) else text


def _indent(lines, depth):
    return "".join(" " * depth + line + "\n" for line in lines.splitlines())


def _contents(script, repos, ca, kind):
    """Fichiers du paquet (chemin, contenu ou None pour un dossier, droits), lignes shell d'installation et de
    retrait, description."""
    files = [(DIRECTORY, None, 0o755), (SCRIPT, _bytes(script), 0o644)]
    install, remove = f"sh {SCRIPT}\n", REMOVE + "\n"
    description = DESCRIPTION
    if repos:
        system_ca, refresh, fallback, cache = SYSTEMS[kind]
        files.append((REPOS, _bytes(repos), 0o644))
        if ca:
            files.append((CA_FILE, _bytes(ca), 0o644))
        install = (f"if [ -f {CA_FILE} ]; then\n    install -d -m 755 {os.path.dirname(system_ca)}\n"
                   f"    install -m 644 {CA_FILE} {system_ca}\n    {refresh} >/dev/null\nfi\n"
                   f"if ! sh {REPOS} --from-package; then\n    echo \"→ {cache} seul.\" >&2\n"
                   f"    sh {REPOS} --from-package {fallback} || echo \"Dépôts loco non configurés : relancez la "
                   "commande de la page Enrôlement.\" >&2\nfi\n" + install)
        remove = f"sh {REPOS} --remove --from-package || true\n" + remove
        description += DESCRIPTION_REPOS
    return files, install, remove, description


# ---------------------------------------------------------------------------------------------------------- .deb

def _tar_gz(entries, mtime):
    """Archive tar compressée ; entrées (chemin, contenu ou None pour un dossier, mode), propriétaire root."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for path, data, mode in entries:
            info = tarfile.TarInfo(path)
            info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = mtime, mode, 0, 0, "root", "root"
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def _ar(members, mtime):
    """Archive ar au format de dpkg-deb : nom sur 16 caractères, données alignées sur deux octets."""
    out = [b"!<arch>\n"]
    for name, data in members:
        out.append(f"{name:<16}{mtime:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode())
        out.append(data + (b"\n" if len(data) % 2 else b""))
    return b"".join(out)


def build_deb(script, version, server, mtime=None, repos=None, ca=""):
    """Paquet Debian/Ubuntu (Architecture: all) qui exécute le script d'installation à la configuration ;
    avec `repos` (setup.sh rendu pour ce serveur), configure aussi les dépôts et le cache APT."""
    mtime = int(time.time() if mtime is None else mtime)
    files, install, remove, description = _contents(script, repos, ca, "deb")
    size = sum(len(data) for _, data, _ in files if data)
    depends = "python3, systemd" + (", ca-certificates, curl | wget" if repos else "")
    control = (f"Package: loco-client\nVersion: {version}{'+' + FLAVOR if repos else ''}\nArchitecture: all\n"
               f"Maintainer: loco <loco@{_host(server)}>\nInstalled-Size: {max(1, -(-size // 1024))}\n"
               f"Depends: {depends}\nSection: admin\nPriority: optional\n"
               "Homepage: https://github.com/ShaNouhAr/loco\n"
               f"Description: {SUMMARY} ({server})\n"
               + "".join(f" {line}\n" for line in textwrap.wrap(description.format(server=server), 76)))
    postinst = f'#!/bin/sh\nset -e\ncase "$1" in\n    configure)\n{_indent(install, 8)}        ;;\nesac\nexit 0\n'
    prerm = f'#!/bin/sh\nset -e\ncase "$1" in\n    remove)\n{_indent(remove, 8)}        ;;\nesac\nexit 0\n'
    md5sums = "".join(f"{hashlib.md5(data).hexdigest()}  {path.lstrip('/')}\n" for path, data, _ in files if data)
    control_tar = _tar_gz([("./control", control.encode(), 0o644), ("./md5sums", md5sums.encode(), 0o644),
                           ("./postinst", postinst.encode(), 0o755), ("./prerm", prerm.encode(), 0o755)], mtime)
    data_tar = _tar_gz([("./", None, 0o755), ("./usr/", None, 0o755), ("./usr/share/", None, 0o755)]
                       + [(f".{path}{'/' if data is None else ''}", data, mode) for path, data, mode in files], mtime)
    return _ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control_tar), ("data.tar.gz", data_tar)], mtime)


# ---------------------------------------------------------------------------------------------------------- .rpm

INT16, INT32, STRING, BIN, STRING_ARRAY, I18NSTRING = 3, 4, 6, 7, 8, 9
SIGNATURES, IMMUTABLE = 62, 63                       # étiquettes de région (en-tête de signature, en-tête principal)
RPMLIB = (1 << 24) | 2 | 8                           # RPMSENSE_RPMLIB | LESS | EQUAL


def _rpm_header(entries, region):
    """En-tête RPM : index trié par étiquette, données dans le même ordre et alignées, région englobant le tout."""
    index, store = [], bytearray()
    for tag, kind, value in sorted(entries):
        width = {INT16: 2, INT32: 4}.get(kind, 1)
        store += b"\0" * (-len(store) % width)
        offset = len(store)
        if kind == INT16:
            store += struct.pack(f">{len(value)}H", *value)
        elif kind == INT32:
            store += struct.pack(f">{len(value)}I", *value)
        elif kind == STRING:
            store += value.encode() + b"\0"
        elif kind == BIN:
            store += value
        else:
            store += b"".join(v.encode() + b"\0" for v in value)
        index.append(struct.pack(">IIII", tag, kind, offset, 1 if kind == STRING else len(value)))
    count = len(index) + 1
    trailer = len(store)
    store += struct.pack(">IIiI", region, BIN, -count * 16, 16)
    return (b"\x8e\xad\xe8\x01\0\0\0\0" + struct.pack(">II", count, len(store))
            + struct.pack(">IIII", region, BIN, trailer, 16) + b"".join(index) + bytes(store))


def _cpio(files, mtime):
    """Charge utile cpio « newc » avec les chemins préfixés par « ./ » (PayloadFilesHavePrefix)."""
    out = bytearray()

    def entry(name, mode, data, ino, nlink):
        name = name.encode() + b"\0"
        out.extend(b"070701" + "".join(f"{v:08X}" for v in (ino, mode, 0, 0, nlink, mtime, len(data), 0, 0, 0, 0,
                                                                   len(name), 0)).encode() + name)
        out.extend(b"\0" * (-len(out) % 4) + data)
        out.extend(b"\0" * (-len(out) % 4))

    for ino, (path, data, mode) in enumerate(files, 1):
        entry("." + path, mode, b"" if data is None else data, ino, 2 if data is None else 1)
    entry("TRAILER!!!", 0, b"", 0, 1)
    return bytes(out)


def build_rpm(script, version, server, mtime=None, repos=None, ca=""):
    """Paquet Rocky Linux / AlmaLinux / RHEL (noarch) qui exécute le script d'installation après l'installation ;
    avec `repos` (rpm-setup.sh rendu pour ce serveur), configure aussi les dépôts et le cache DNF."""
    mtime = int(time.time() if mtime is None else mtime)
    files, install, remove, description = _contents(script, repos, ca, "rpm")
    files = [(path, data, (0o40000 if data is None else 0o100000) | mode) for path, data, mode in files]
    release, name = "1" + ("." + FLAVOR if repos else ""), "loco-client"
    raw = _cpio(files, mtime)
    payload = gzip.compress(raw, compresslevel=9, mtime=0)
    sizes = [4096 if data is None else len(data) for _, data, _ in files]
    count = len(files)
    dirs = sorted({os.path.dirname(path) + "/" for path, _, _ in files})
    requires = [("python3", 0, ""), ("systemd", 0, ""), ("/bin/sh", 0, "")]
    requires += [("ca-certificates", 0, ""), ("curl", 0, "")] if repos else []
    requires += [("rpmlib(CompressedFileNames)", RPMLIB, "3.0.4-1"), ("rpmlib(FileDigests)", RPMLIB, "4.6.0-1"),
                 ("rpmlib(PayloadFilesHavePrefix)", RPMLIB, "4.0-1")]
    header = _rpm_header([
        (100, STRING_ARRAY, ["C"]),
        (1000, STRING, name), (1001, STRING, version), (1002, STRING, release),
        (1004, I18NSTRING, [f"{SUMMARY} ({server})"]),
        (1005, I18NSTRING, [textwrap.fill(description.format(server=server), 76)]),
        (1006, INT32, [mtime]), (1007, STRING, _host(server)), (1009, INT32, [sum(sizes)]),
        (1014, STRING, "MIT"), (1016, I18NSTRING, ["System Environment/Daemons"]),
        (1020, STRING, "https://github.com/ShaNouhAr/loco"), (1021, STRING, "linux"), (1022, STRING, "noarch"),
        (1024, STRING, install), (1025, STRING, f'if [ "$1" -eq 0 ]; then\n{_indent(remove, 4)}fi\n'),
        (1028, INT32, sizes), (1030, INT16, [mode for _, _, mode in files]), (1033, INT16, [0] * count),
        (1034, INT32, [mtime] * count),
        (1035, STRING_ARRAY, ["" if data is None else hashlib.sha256(data).hexdigest() for _, data, _ in files]),
        (1036, STRING_ARRAY, [""] * count), (1037, INT32, [0] * count), (1039, STRING_ARRAY, ["root"] * count),
        (1040, STRING_ARRAY, ["root"] * count), (1044, STRING, f"{name}-{version}-{release}.src.rpm"),
        (1045, INT32, [0xFFFFFFFF] * count),
        (1047, STRING_ARRAY, [name]), (1048, INT32, [flags for _, flags, _ in requires]),
        (1049, STRING_ARRAY, [dep for dep, _, _ in requires]), (1050, STRING_ARRAY, [v for _, _, v in requires]),
        (1064, STRING, "4.16.1"), (1086, STRING, "/bin/sh"), (1087, STRING, "/bin/sh"),
        (1095, INT32, [1] * count), (1096, INT32, list(range(1, count + 1))), (1097, STRING_ARRAY, [""] * count),
        (1112, INT32, [8]), (1113, STRING_ARRAY, [f"{version}-{release}"]),
        (1116, INT32, [dirs.index(os.path.dirname(path) + "/") for path, _, _ in files]),
        (1117, STRING_ARRAY, [os.path.basename(path) for path, _, _ in files]), (1118, STRING_ARRAY, dirs),
        (1124, STRING, "cpio"), (1125, STRING, "gzip"), (1126, STRING, "9"),
        (5011, INT32, [8]), (5092, STRING_ARRAY, [hashlib.sha256(payload).hexdigest()]), (5093, INT32, [8]),
    ], IMMUTABLE)
    signature = _rpm_header([
        (269, STRING, hashlib.sha1(header).hexdigest()), (273, STRING, hashlib.sha256(header).hexdigest()),
        (1000, INT32, [len(header) + len(payload)]), (1004, BIN, hashlib.md5(header + payload).digest()),
        (1007, INT32, [len(raw)]),
    ], SIGNATURES)
    lead = struct.pack(">4sBBhh66shh16s", b"\xed\xab\xee\xdb", 3, 0, 0, 1, f"{name}-{version}-{release}".encode(),
                       1, 5, b"")
    return lead + signature + b"\0" * (-len(signature) % 8) + header + payload
