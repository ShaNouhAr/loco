"""Paquets .rpm et métadonnées de dépôt (repodata) en Python pur : remplace rpm et createrepo_c là où ils
n'existent pas (serveur Windows). Les serveurs Linux gardent createrepo_c."""

import gzip
import hashlib
import os
import re
import struct
import time
from xml.sax.saxutils import escape, quoteattr
from .i18n import _

_LEAD = 96
_HEADER_MAGIC = b"\x8e\xad\xe8\x01"

# Étiquettes de l'en-tête principal (rpmtag.h).
NAME, VERSION, RELEASE, EPOCH, SUMMARY, DESCRIPTION, BUILDTIME, BUILDHOST = 1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007
SIZE, VENDOR, LICENSE, PACKAGER, GROUP, URL, ARCH = 1009, 1011, 1014, 1015, 1016, 1020, 1022
FILEMODES, FILEFLAGS, SOURCERPM = 1030, 1037, 1044
PROVIDENAME, REQUIREFLAGS, REQUIRENAME, REQUIREVERSION = 1047, 1048, 1049, 1050
CONFLICTFLAGS, CONFLICTNAME, CONFLICTVERSION = 1053, 1054, 1055
CHANGELOGTIME, CHANGELOGNAME, CHANGELOGTEXT = 1080, 1081, 1082
OBSOLETENAME, PROVIDEFLAGS, PROVIDEVERSION, OBSOLETEFLAGS, OBSOLETEVERSION = 1090, 1112, 1113, 1114, 1115
DIRINDEXES, BASENAMES, DIRNAMES = 1116, 1117, 1118
LONGSIZE = 5009
# Dépendances faibles : (nom, drapeaux, version) ; dans rpmtag.h la version précède les drapeaux.
WEAK = {"recommends": (5046, 5048, 5047), "suggests": (5049, 5051, 5050),
        "supplements": (5052, 5054, 5053), "enhances": (5055, 5057, 5056)}
# Signature : taille du contenu décompressé.
SIG_PAYLOADSIZE, SIG_LONGARCHIVESIZE = 1007, 271

_PRE = (1 << 6) | (1 << 9) | (1 << 10)          # RPMSENSE_PREREQ, SCRIPT_PRE, SCRIPT_POST
_GHOST = 1 << 6
_PRIMARY_FILE = re.compile(r"^(.*/bin/.*|/etc/.*|/usr/lib/sendmail)$")
CHANGELOG_LIMIT = 10


def _header(data, offset):
    """En-tête RPM à partir de offset : (dictionnaire étiquette → valeur, offset de fin)."""
    if len(data) < offset + 16 or data[offset:offset + 4] != _HEADER_MAGIC:
        raise ValueError(_("ce fichier n'est pas un paquet .rpm valide"))
    count, size = struct.unpack(">II", data[offset + 8:offset + 16])
    index = offset + 16
    store = index + count * 16
    end = store + size
    if count > 100000 or end > len(data):
        raise ValueError(_("ce fichier n'est pas un paquet .rpm valide"))
    tags = {}
    for i in range(count):
        tag, kind, off, n = struct.unpack(">IIII", data[index + i * 16:index + i * 16 + 16])
        pos = store + off
        if off > size:
            raise ValueError(_("offset d'en-tête RPM invalide"))
        if kind in (6, 8, 9):                                # chaîne(s)
            if n > size:
                raise ValueError(_("tableau d'en-tête RPM invalide"))
            values = []
            for _item in range(n if kind != 6 else 1):
                stop = data.find(b"\0", pos, end)
                if stop < 0:
                    raise ValueError(_("chaîne d'en-tête RPM tronquée"))
                values.append(data[pos:stop].decode("utf-8", "replace"))
                pos = stop + 1
            tags[tag] = values[0] if kind == 6 else values
        elif kind in (2, 3, 4, 5):                           # entiers
            width, fmt = {2: (1, "B"), 3: (2, "H"), 4: (4, "I"), 5: (8, "Q")}[kind]
            if pos + width * n > end:
                raise ValueError(_("tableau d'en-tête RPM tronqué"))
            tags[tag] = list(struct.unpack(f">{n}{fmt}", data[pos:pos + width * n]))
        elif kind == 7:
            if pos + n > end:
                raise ValueError(_("donnée d'en-tête RPM tronquée"))
            tags[tag] = data[pos:pos + n]
    return tags, end


def read_rpm(path):
    """En-têtes d'un paquet : (en-tête principal, signature, début et fin de l'en-tête principal)."""
    with open(path, "rb") as f:
        if f.read(4) != b"\xed\xab\xee\xdb":
            raise ValueError(_("ce fichier n'est pas un paquet .rpm valide"))

        def read_header(offset):
            f.seek(offset)
            prefix = f.read(16)
            if len(prefix) != 16 or prefix[:4] != _HEADER_MAGIC:
                raise ValueError(_('en-tête RPM tronqué ou invalide'))
            count, size = struct.unpack(">II", prefix[8:16])
            length = count * 16 + size
            if count > 100000 or length > 64 << 20:
                raise ValueError(_('en-tête RPM trop volumineux'))
            tags, end = _header(prefix + f.read(length), 0)
            return tags, offset + end

        signature, sig_end = read_header(_LEAD)
        start = sig_end + (-sig_end % 8)
        header, end = read_header(start)
    return header, signature, start, end


def _one(tags, tag, default=""):
    value = tags.get(tag, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def rpm_fields(path):
    header = read_rpm(path)[0]
    name, version, release = (_one(header, t) for t in (NAME, VERSION, RELEASE))
    arch = _one(header, ARCH)
    source = _one(header, SOURCERPM, None)
    if not name or not version:
        raise ValueError(_("ce fichier n'est pas un paquet .rpm valide"))
    if not source or arch in ("src", "nosrc"):
        raise ValueError(_('les paquets source (.src.rpm) ne sont pas publiés'))
    return {"name": name, "version": version, "release": release, "arch": arch}


def _flags(value):
    value &= 0x0E
    return {8: "EQ", 2: "LT", 4: "GT", 10: "LE", 12: "GE"}.get(value)


def _evr(text):
    epoch, _, rest = text.rpartition(":") if ":" in text else ("", "", text)
    ver, _, rel = rest.partition("-")
    return epoch or "0", ver, rel


def _deps(header, names, flags, versions, requires=False, provided=()):
    out, seen = [], set()
    names_ = header.get(names, [])
    flags_ = header.get(flags, [0] * len(names_))
    versions_ = header.get(versions, [""] * len(names_))
    for name, flag, version in zip(names_, flags_, versions_):
        if requires and (name.startswith("rpmlib(") or (name, flag & 0x0E, version) in provided):
            continue    # comme createrepo_c : ni rpmlib(), ni ce que le paquet fournit lui-même
        entry = {"name": name}
        kind = _flags(flag)
        if kind and version:
            entry["flags"] = kind
            entry["epoch"], entry["ver"], rel = _evr(version)
            if rel:
                entry["rel"] = rel
        if requires and flag & _PRE:
            entry["pre"] = "1"
        key = tuple(sorted(entry.items()))
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def _files(header):
    dirs, bases, indexes = header.get(DIRNAMES, []), header.get(BASENAMES, []), header.get(DIRINDEXES, [])
    modes, flags = header.get(FILEMODES, [0] * len(bases)), header.get(FILEFLAGS, [0] * len(bases))
    out = []
    for i, base in enumerate(bases):
        path = dirs[indexes[i]] + base
        kind = "ghost" if flags[i] & _GHOST else "dir" if modes[i] & 0o170000 == 0o040000 else ""
        out.append((path, kind))
    return out


def package_info(path, location):
    header, signature, start, end = read_rpm(path)
    st = os.stat(path)
    epoch = header.get(EPOCH)
    archive = _one(signature, SIG_LONGARCHIVESIZE, None) or _one(signature, SIG_PAYLOADSIZE, 0)
    with open(path, "rb") as f:
        digest = hashlib.sha256()
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
        checksum = digest.hexdigest()
    info = {
        "name": _one(header, NAME), "arch": _one(header, ARCH), "epoch": str(epoch[0]) if epoch else "0",
        "ver": _one(header, VERSION), "rel": _one(header, RELEASE), "checksum": checksum,
        "summary": _one(header, SUMMARY), "description": _one(header, DESCRIPTION),
        "packager": _one(header, PACKAGER), "url": _one(header, URL), "file_time": int(st.st_mtime),
        "build_time": _one(header, BUILDTIME, 0), "package_size": st.st_size,
        "installed_size": _one(header, LONGSIZE, None) or _one(header, SIZE, 0), "archive_size": archive,
        "location": location, "license": _one(header, LICENSE), "vendor": _one(header, VENDOR),
        "group": _one(header, GROUP), "buildhost": _one(header, BUILDHOST), "sourcerpm": _one(header, SOURCERPM),
        "header_start": start, "header_end": end, "files": _files(header),
        "provides": _deps(header, PROVIDENAME, PROVIDEFLAGS, PROVIDEVERSION),
        "requires": _deps(header, REQUIRENAME, REQUIREFLAGS, REQUIREVERSION, requires=True,
                          provided={(n, f & 0x0E, v) for n, f, v in zip(header.get(PROVIDENAME, []),
                                                                         header.get(PROVIDEFLAGS, []),
                                                                         header.get(PROVIDEVERSION, []))}),
        "conflicts": _deps(header, CONFLICTNAME, CONFLICTFLAGS, CONFLICTVERSION),
        "obsoletes": _deps(header, OBSOLETENAME, OBSOLETEFLAGS, OBSOLETEVERSION),
        "changelog": list(zip(header.get(CHANGELOGNAME, []), header.get(CHANGELOGTIME, []),
                              header.get(CHANGELOGTEXT, [])))[:CHANGELOG_LIMIT],
    }
    for kind, (names, flags, versions) in WEAK.items():
        info[kind] = _deps(header, names, flags, versions)
    return info


def _version(p):
    return f'<version epoch="{p["epoch"]}" ver={quoteattr(p["ver"])} rel={quoteattr(p["rel"])}/>'


def _entries(kind, entries):
    if not entries:
        return f"    <rpm:{kind}/>\n" if kind in ("provides", "requires") else ""
    lines = [f"    <rpm:{kind}>"]
    for e in entries:
        attrs = "".join(f" {k}={quoteattr(e[k])}" for k in ("name", "flags", "epoch", "ver", "rel", "pre") if k in e)
        lines.append(f"      <rpm:entry{attrs}/>")
    lines.append(f"    </rpm:{kind}>")
    return "\n".join(lines) + "\n"


def _primary(p):
    files = "".join(f'    <file{" type=" + quoteattr(k) if k else ""}>{escape(f)}</file>\n'
                    for f, k in p["files"] if _PRIMARY_FILE.match(f))
    deps = "".join(_entries(k, p[k]) for k in ("provides", "requires", "conflicts", "obsoletes",
                                                 "suggests", "enhances", "recommends", "supplements"))
    return (f'<package type="rpm">\n  <name>{escape(p["name"])}</name>\n  <arch>{escape(p["arch"])}</arch>\n'
            f'  {_version(p)}\n  <checksum type="sha256" pkgid="YES">{p["checksum"]}</checksum>\n'
            f'  <summary>{escape(p["summary"])}</summary>\n  <description>{escape(p["description"])}</description>\n'
            f'  <packager>{escape(p["packager"])}</packager>\n  <url>{escape(p["url"])}</url>\n'
            f'  <time file="{p["file_time"]}" build="{p["build_time"]}"/>\n'
            f'  <size package="{p["package_size"]}" installed="{p["installed_size"]}" archive="{p["archive_size"]}"/>\n'
            f'  <location href={quoteattr(p["location"])}/>\n  <format>\n'
            f'    <rpm:license>{escape(p["license"])}</rpm:license>\n    <rpm:vendor>{escape(p["vendor"])}</rpm:vendor>\n'
            f'    <rpm:group>{escape(p["group"])}</rpm:group>\n    <rpm:buildhost>{escape(p["buildhost"])}</rpm:buildhost>\n'
            f'    <rpm:sourcerpm>{escape(p["sourcerpm"])}</rpm:sourcerpm>\n'
            f'    <rpm:header-range start="{p["header_start"]}" end="{p["header_end"]}"/>\n'
            f'{deps}{files}  </format>\n</package>\n')


def _filelists(p):
    files = "".join(f'  <file{" type=" + quoteattr(k) if k else ""}>{escape(f)}</file>\n' for f, k in p["files"])
    return (f'<package pkgid="{p["checksum"]}" name={quoteattr(p["name"])} arch={quoteattr(p["arch"])}>\n'
            f'  {_version(p)}\n{files}</package>\n')


def _other(p):
    logs = "".join(f'  <changelog author={quoteattr(author)} date="{date}">{escape(text)}</changelog>\n'
                   for author, date, text in p["changelog"])
    return (f'<package pkgid="{p["checksum"]}" name={quoteattr(p["name"])} arch={quoteattr(p["arch"])}>\n'
            f'  {_version(p)}\n{logs}</package>\n')


def createrepo(root, outputdir):
    """Équivalent de « createrepo_c --no-database --general-compress-type=gz --checksum=sha256
    --unique-md-filenames --outputdir outputdir root » : écrit outputdir/repodata."""
    packages = []
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d != "repodata" and not d.startswith("."))
        for name in sorted(names):
            if name.endswith(".rpm"):
                path = os.path.join(directory, name)
                packages.append(package_info(path, os.path.relpath(path, root).replace(os.sep, "/")))
    count = len(packages)
    documents = {
        "primary": ('<metadata xmlns="http://linux.duke.edu/metadata/common" '
                    f'xmlns:rpm="http://linux.duke.edu/metadata/rpm" packages="{count}">\n',
                    _primary, "</metadata>\n"),
        "filelists": (f'<filelists xmlns="http://linux.duke.edu/metadata/filelists" packages="{count}">\n',
                      _filelists, "</filelists>\n"),
        "other": (f'<otherdata xmlns="http://linux.duke.edu/metadata/other" packages="{count}">\n',
                  _other, "</otherdata>\n"),
    }
    repodata = os.path.join(outputdir, "repodata")
    os.makedirs(repodata, exist_ok=True)
    now = int(time.time())
    data_entries = []
    for kind, (head, render, tail) in documents.items():
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + "".join(render(p) for p in packages) + tail).encode()
        compressed = gzip.compress(xml, compresslevel=9, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        href = f"repodata/{digest}-{kind}.xml.gz"
        with open(os.path.join(outputdir, href), "wb") as f:
            f.write(compressed)
        data_entries.append(
            f'  <data type="{kind}">\n    <checksum type="sha256">{digest}</checksum>\n'
            f'    <open-checksum type="sha256">{hashlib.sha256(xml).hexdigest()}</open-checksum>\n'
            f'    <location href="{href}"/>\n    <timestamp>{now}</timestamp>\n'
            f'    <size>{len(compressed)}</size>\n    <open-size>{len(xml)}</open-size>\n  </data>\n')
    repomd = ('<?xml version="1.0" encoding="UTF-8"?>\n<repomd xmlns="http://linux.duke.edu/metadata/repo" '
              f'xmlns:rpm="http://linux.duke.edu/metadata/rpm">\n  <revision>{now}</revision>\n'
              + "".join(data_entries) + "</repomd>\n")
    with open(os.path.join(repodata, "repomd.xml"), "w", encoding="utf-8") as f:
        f.write(repomd)
