"""Paquets .deb et index APT en Python pur : remplace dpkg-deb et apt-ftparchive là où ils n'existent pas
(serveur Windows). Les serveurs Linux gardent les outils Debian."""

import datetime
import gzip
import hashlib
import io
import lzma
import os
import tarfile
from .i18n import _

try:  # Python 3.14 et plus, compilé avec zstd
    from compression import zstd as _zstd
except ImportError:  # pragma: no cover - selon la version de Python
    _zstd = None
try:  # module zstandard (Alpine : son Python 3.14 n'a pas le module _zstd)
    import zstandard as _zstandard
except ImportError:  # pragma: no cover - selon le système
    _zstandard = None

# Ordre des champs d'un paquet binaire (celui de dpkg) ; les autres suivent dans leur ordre d'origine.
_FIELD_ORDER = ("Package", "Source", "Version", "Architecture", "Maintainer", "Installed-Size", "Pre-Depends",
                "Depends", "Recommends", "Suggests", "Conflicts", "Breaks", "Replaces", "Provides", "Section",
                "Priority", "Homepage", "Description")


def _ar_members(stream):
    """Membres d'une archive ar (format des .deb)."""
    if stream.read(8) != b"!<arch>\n":
        raise ValueError(_("ce fichier n'est pas un paquet .deb valide"))
    pos = 8
    length = os.fstat(stream.fileno()).st_size
    while pos + 60 <= length:
        stream.seek(pos)
        header = stream.read(60)
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValueError(_('en-tête ar invalide'))
        name = header[:16].decode("ascii", "replace").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise ValueError(_("ce fichier n'est pas un paquet .deb valide")) from None
        start = pos + 60
        if size < 0 or start + size > length:
            raise ValueError(_('membre ar tronqué'))
        yield name, size
        pos = start + size + (size % 2)


def _decompress(name, blob):
    limit = 64 << 20  # Le contrôle ne doit pas charger le contenu installé du paquet.
    if name.endswith(".gz"):
        reader = gzip.GzipFile(fileobj=io.BytesIO(blob))
    elif name.endswith(".xz"):
        reader = lzma.LZMAFile(io.BytesIO(blob))
    elif name.endswith(".zst"):
        if _zstd is not None:
            reader = _zstd.ZstdFile(io.BytesIO(blob))
        elif _zstandard is not None:
            reader = _zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob))
        else:
            raise ValueError(_('paquet compressé en zstd : Python 3.14 avec zstd, ou le module zstandard, est requis'))
    elif name.endswith(".tar"):
        reader = io.BytesIO(blob)
    else:
        raise ValueError(_('compression non prise en charge : %(name)s', name=name))
    chunks, total = [], 0
    with reader:
        # Lecture en boucle : certains lecteurs rendent moins que demandé avant la fin.
        while total <= limit:
            chunk = reader.read(min(1 << 20, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    control = b"".join(chunks)
    if len(control) > limit:
        raise ValueError(_('archive de contrôle trop volumineuse (maximum 64 Mio)'))
    return control


def _control_text(path):
    """Contenu du fichier DEBIAN/control d'un paquet."""
    with open(path, "rb") as f:
        version = False
        control = None
        for name, size in _ar_members(f):
            if name == "debian-binary":
                version = size <= 16 and f.read(size).strip() == b"2.0"
            elif name.startswith("control.tar"):
                if size > 32 << 20:
                    raise ValueError(_('archive de contrôle trop volumineuse (maximum 32 Mio compressés)'))
                control = _decompress(name, f.read(size))
                break
    if not version or control is None:
        raise ValueError(_("ce fichier n'est pas un paquet .deb valide"))
    with tarfile.open(fileobj=io.BytesIO(control)) as tar:
        for member in tar.getmembers():
            if member.name in ("./control", "control") and member.isfile():
                return tar.extractfile(member).read().decode("utf-8")
    raise ValueError(_('fichier control absent du paquet'))


def control_text(path):
    """Normalise les erreurs de décompression pour les présenter comme un paquet invalide."""
    errors = (OSError, EOFError, tarfile.TarError, lzma.LZMAError, UnicodeError)
    if _zstd is not None:
        errors += (_zstd.ZstdError,)
    if _zstandard is not None:
        errors += (_zstandard.ZstdError,)
    try:
        return _control_text(path)
    except errors as exc:
        raise ValueError(_('archive de contrôle .deb invalide')) from exc


def parse_stanza(text):
    """Champs d'un paragraphe de contrôle Debian, lignes de continuation comprises, dans l'ordre."""
    fields, key = {}, None
    for line in text.splitlines():
        if not line.strip() and key is None:
            continue
        if line[:1] in (" ", "\t") and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            fields[key] = value.strip()
        elif not line.strip():
            break
    return fields


def deb_fields(path):
    fields = parse_stanza(control_text(path))
    if not all(fields.get(k) for k in ("Package", "Version", "Architecture")):
        raise ValueError(_('champs Package/Version/Architecture manquants'))
    return fields


def _digests(path):
    hashes = {"MD5sum": hashlib.md5(), "SHA1": hashlib.sha1(), "SHA256": hashlib.sha256(), "SHA512": hashlib.sha512()}
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            size += len(chunk)
            for h in hashes.values():
                h.update(chunk)
    return size, {k: h.hexdigest() for k, h in hashes.items()}


def packages_index(root, pool="pool"):
    """Équivalent de « apt-ftparchive packages pool » lancé dans root."""
    out = []
    directory = os.path.join(root, pool)
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not name.endswith(".deb") or not os.path.isfile(path):
            continue
        fields = deb_fields(path)
        size, digests = _digests(path)
        ordered = [k for k in _FIELD_ORDER if k in fields and k != "Description"]
        ordered += [k for k in fields if k not in _FIELD_ORDER]
        lines = [f"{k}: {fields[k]}" for k in ordered]
        lines += [f"Filename: {pool}/{name}", f"Size: {size}"]
        lines += [f"{k}: {v}" for k, v in digests.items()]
        if "Description" in fields:
            lines.append(f"Description: {fields['Description']}")
        out.append("\n".join(lines) + "\n")
    return "\n".join(out).encode("utf-8")


def gzip_file(path):
    """Comme « gzip -9nk » : sans nom ni date, résultat reproductible."""
    with open(path, "rb") as src, open(path + ".gz", "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as dst:
            dst.write(src.read())


def release_file(directory, origin, label, suite, codename):
    """Équivalent de « apt-ftparchive release . » avec Origin, Label, Suite et Codename."""
    names = sorted(n for n in os.listdir(directory)
                   if os.path.isfile(os.path.join(directory, n)) and n not in ("Release", "Release.gpg", "InRelease"))
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    lines = [f"Origin: {origin}", f"Label: {label}", f"Suite: {suite}", f"Codename: {codename}", f"Date: {date}"]
    algorithms = (("MD5Sum", hashlib.md5), ("SHA1", hashlib.sha1), ("SHA256", hashlib.sha256), ("SHA512", hashlib.sha512))
    entries = []
    for name in names:
        with open(os.path.join(directory, name), "rb") as f:
            data = f.read()
        entries.append((name, len(data), {label_: algo(data).hexdigest() for label_, algo in algorithms}))
    for label_, _algo in algorithms:
        lines.append(f"{label_}:")
        lines += [f" {digests[label_]} {size:>16} {name}" for name, size, digests in entries]
    return ("\n".join(lines) + "\n").encode("utf-8")
