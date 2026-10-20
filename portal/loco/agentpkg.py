"""Paquets d'installation de l'agent Linux (.deb et .rpm), produits à la demande pour ce serveur.

Le paquet n'embarque que le script d'installation rendu par le portail (agent, autorité épinglée, adresse et clé
d'inscription) ; son script de post-installation l'exécute, exactement comme `curl … | sudo sh`. L'agent reste maître
de ses fichiers (mise à jour automatique, `loco-client remove`) : dpkg et rpm ne possèdent que ce script.
Désinstaller le paquet retire l'agent du poste ; le poste se retire de la liste du parc depuis le portail."""

import gzip
import hashlib
import io
import struct
import tarfile
import textwrap
import time
import urllib.parse

DIRECTORY = "/usr/share/loco-client"
SCRIPT = DIRECTORY + "/install.sh"
AGENT = "/usr/local/lib/loco-client/loco-client.py"
REMOVE = f"if [ -x {AGENT} ]; then {AGENT} remove || true; fi"
SUMMARY = "agent du parc loco"
DESCRIPTION = ("Inscrit ce poste dans « Mon parc » de loco ({server}) : inventaire, mises à jour, actions et accès "
               "distant. L'agent se met à jour seul ; désinstaller ce paquet le retire du poste.")


def _host(server):
    return urllib.parse.urlsplit(server).hostname or "localhost"


def deb_name(version):
    return f"loco-client_{version}_all.deb"


def rpm_name(version):
    return f"loco-client-{version}-1.noarch.rpm"


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


def build_deb(script, version, server, mtime=None):
    """Paquet Debian/Ubuntu (Architecture: all) qui exécute le script d'installation à la configuration."""
    mtime = int(time.time() if mtime is None else mtime)
    script = script.encode() if isinstance(script, str) else script
    control = (f"Package: loco-client\nVersion: {version}\nArchitecture: all\n"
               f"Maintainer: loco <loco@{_host(server)}>\nInstalled-Size: {max(1, -(-len(script) // 1024))}\n"
               "Depends: python3, systemd\nSection: admin\nPriority: optional\n"
               "Homepage: https://github.com/ShaNouhAr/loco\n"
               f"Description: {SUMMARY} ({server})\n"
               + "".join(f" {line}\n" for line in textwrap.wrap(DESCRIPTION.format(server=server), 76)))
    postinst = f'#!/bin/sh\nset -e\ncase "$1" in\n    configure) sh {SCRIPT} ;;\nesac\nexit 0\n'
    prerm = f'#!/bin/sh\nset -e\ncase "$1" in\n    remove) {REMOVE} ;;\nesac\nexit 0\n'
    md5sums = f"{hashlib.md5(script).hexdigest()}  {SCRIPT.lstrip('/')}\n"
    control_tar = _tar_gz([("./control", control.encode(), 0o644), ("./md5sums", md5sums.encode(), 0o644),
                           ("./postinst", postinst.encode(), 0o755), ("./prerm", prerm.encode(), 0o755)], mtime)
    data_tar = _tar_gz([("./", None, 0o755), ("./usr/", None, 0o755), ("./usr/share/", None, 0o755),
                        (f".{DIRECTORY}/", None, 0o755), (f".{SCRIPT}", script, 0o644)], mtime)
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

    for ino, (path, mode, data) in enumerate(files, 1):
        entry("." + path, mode, b"" if data is None else data, ino, 2 if data is None else 1)
    entry("TRAILER!!!", 0, b"", 0, 1)
    return bytes(out)


def build_rpm(script, version, server, mtime=None):
    """Paquet Rocky Linux / AlmaLinux / RHEL (noarch) qui exécute le script d'installation après l'installation."""
    mtime = int(time.time() if mtime is None else mtime)
    script = script.encode() if isinstance(script, str) else script
    release, name = "1", "loco-client"
    files = [(DIRECTORY, 0o40755, None), (SCRIPT, 0o100644, script)]
    raw = _cpio(files, mtime)
    payload = gzip.compress(raw, compresslevel=9, mtime=0)
    sizes = [4096 if data is None else len(data) for _, _, data in files]
    requires = [("python3", 0, ""), ("systemd", 0, ""), ("/bin/sh", 0, ""),
                ("rpmlib(CompressedFileNames)", RPMLIB, "3.0.4-1"), ("rpmlib(FileDigests)", RPMLIB, "4.6.0-1"),
                ("rpmlib(PayloadFilesHavePrefix)", RPMLIB, "4.0-1")]
    header = _rpm_header([
        (100, STRING_ARRAY, ["C"]),
        (1000, STRING, name), (1001, STRING, version), (1002, STRING, release),
        (1004, I18NSTRING, [f"{SUMMARY} ({server})"]), (1005, I18NSTRING, [textwrap.fill(DESCRIPTION.format(server=server), 76)]),
        (1006, INT32, [mtime]), (1007, STRING, _host(server)), (1009, INT32, [sum(sizes)]),
        (1014, STRING, "MIT"), (1016, I18NSTRING, ["System Environment/Daemons"]),
        (1020, STRING, "https://github.com/ShaNouhAr/loco"), (1021, STRING, "linux"), (1022, STRING, "noarch"),
        (1024, STRING, f"sh {SCRIPT}\n"), (1025, STRING, f'if [ "$1" -eq 0 ]; then {REMOVE}; fi\n'),
        (1028, INT32, sizes), (1030, INT16, [mode for _, mode, _ in files]), (1033, INT16, [0, 0]),
        (1034, INT32, [mtime, mtime]),
        (1035, STRING_ARRAY, ["" if data is None else hashlib.sha256(data).hexdigest() for _, _, data in files]),
        (1036, STRING_ARRAY, ["", ""]), (1037, INT32, [0, 0]), (1039, STRING_ARRAY, ["root", "root"]),
        (1040, STRING_ARRAY, ["root", "root"]), (1044, STRING, f"{name}-{version}-{release}.src.rpm"),
        (1045, INT32, [0xFFFFFFFF, 0xFFFFFFFF]),
        (1047, STRING_ARRAY, [name]), (1048, INT32, [flags for _, flags, _ in requires]),
        (1049, STRING_ARRAY, [dep for dep, _, _ in requires]), (1050, STRING_ARRAY, [v for _, _, v in requires]),
        (1064, STRING, "4.16.1"), (1086, STRING, "/bin/sh"), (1087, STRING, "/bin/sh"),
        (1095, INT32, [1, 1]), (1096, INT32, [1, 2]), (1097, STRING_ARRAY, ["", ""]),
        (1112, INT32, [8]), (1113, STRING_ARRAY, [f"{version}-{release}"]),
        (1116, INT32, [0, 1]), (1117, STRING_ARRAY, ["loco-client", "install.sh"]),
        (1118, STRING_ARRAY, ["/usr/share/", DIRECTORY + "/"]),
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
