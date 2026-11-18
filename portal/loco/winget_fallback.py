"""Repli à winget : installeur officiel de l'éditeur pour les logiciels que winget ne sait pas (ou plus) mettre à jour.

Certains manifestes winget publics pointent un fichier que l'éditeur a retiré (HWiNFO : le manifeste public reste figé
sur une version dont l'installeur n'existe plus → 404) alors que l'éditeur publie bien une version plus récente ailleurs.
Pour ces cas, loco résout l'installeur officiel courant depuis une source ouverte (sans navigateur ni service tiers) et
confie au poste son adresse ; l'agent le télécharge et le pose après avoir vérifié sa signature Authenticode (l'éditeur
attendu). Rien n'est ajouté au catalogue de loco. Le résultat est mis en cache pour ne pas interroger la source à chaque
demande ; il est aussi préchauffé lors du renouvellement quotidien de l'index winget.
"""

import re
import threading
import time
import urllib.request

TTL = 6 * 3600
_CACHE = {}
_LOCK = threading.Lock()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(1 << 20).decode("utf-8", "replace")


def _head_ok(url, timeout=5):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return getattr(resp, "status", resp.getcode()) == 200
    except Exception:
        return False


def _version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _resolve_hwinfo():
    # Flux RSS officiel de REALiX : le GET passe (seul le HEAD est challengé par Cloudflare), CORS ouvert, fait pour être
    # lu. On retient la plus haute version stable annoncée — « HWiNFO vX.YY released » ; les bêtas s'écrivent, elles,
    # « vX.YY-NNNN Beta released » et ne matchent donc pas. L'installeur est pris sur le miroir « SAC ftp » que HWiNFO
    # liste lui-même sur sa page de téléchargement (ouvert, ne garde que la version courante).
    text = _get("https://www.hwinfo.com/news.xml/")
    stable = re.findall(r"<title>\s*HWiNFO\s+v([0-9]+\.[0-9]+)\s+released", text)
    if not stable:
        return None
    ver = max(stable, key=_version_key)
    url = "https://www.sac.sk/download/utildiag/hwi_%sx.exe" % ver.replace(".", "")
    if not _head_ok(url):
        return None
    return {"url": url, "type": "inno", "args": "/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
            "signer": "REALiX", "version": ver}


# Logiciels que winget ne peut pas mettre à jour sur le poste et pour lesquels loco sait retrouver l'installeur officiel.
RESOLVERS = {"REALiX.HWiNFO": _resolve_hwinfo}


def descriptor(pid):
    """Descripteur de repli {url, type, args, signer, version} pour ce logiciel, ou None. Mis en cache (TTL)."""
    resolver = RESOLVERS.get(pid)
    if resolver is None:
        return None
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(pid)
        if hit and now - hit[0] < TTL:
            return hit[1]
    try:
        desc = resolver()
    except Exception:
        desc = None
    with _LOCK:
        # Un échec de résolution est gardé brièvement (10 min) pour ne pas marteler la source à chaque requête.
        _CACHE[pid] = (now if desc else now - TTL + 600, desc)
    return desc


def descriptors(pids):
    """{pid: descripteur} pour les logiciels de la liste qui ont un repli résolu (les autres passent par winget)."""
    out = {}
    for pid in pids or []:
        if pid in RESOLVERS:
            d = descriptor(pid)
            if d:
                out[pid] = d
    return out


def prewarm():
    """Résout tous les replis connus et remplit le cache (appelé au renouvellement quotidien de l'index winget)."""
    for pid in RESOLVERS:
        try:
            descriptor(pid)
        except Exception:
            pass
