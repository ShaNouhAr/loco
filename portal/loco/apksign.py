"""Grave une petite configuration dans l'« APK Signing Block » d'un APK déjà signé, sans toucher à sa signature.

On ajoute une paire ID→valeur au bloc de signature — la seule section de l'APK que les schémas de signature v2/v3
ne protègent pas (lors du calcul de l'empreinte, l'offset du répertoire central dans l'EOCD est neutralisé, ce qui
autorise le bloc à grandir). L'APK reste installable et sa signature reste valable : aucune re-signature, donc aucun
keystore sur le serveur. Même principe que le tampon du MSI Windows, mais adapté au format APK. Technique employée en
production par Walle (Meituan) et VasDolly (Tencent). L'agent relit ensuite sa propre configuration dans son APK.

Voir docs/android.md, et portal/loco/fleet.py (route /agent/android/enroll.apk)."""

import struct

MAGIC = b"APK Sig Block 42"        # 16 octets, en toute fin du bloc de signature
LOCO_BLOCK_ID = 0x6C6F636F         # « loco » : notre paire, ignorée des vérificateurs qui ne la connaissent pas
VERITY_PADDING_ID = 0x42726577     # paire de bourrage d'alignement ajoutée par apksigner
PAGE = 4096
EOCD_SIG = b"\x50\x4b\x05\x06"


class ApkError(Exception):
    """APK illisible ou non signé en v2 (pas de bloc de signature à compléter)."""


def _find_eocd(data):
    """Position de l'« End Of Central Directory », cherché depuis la fin (nos APK n'ont pas de commentaire)."""
    start = max(0, len(data) - (22 + 0xFFFF))
    idx = data.rfind(EOCD_SIG, start)
    if idx < 0 or idx + 22 > len(data):
        raise ApkError("EOCD introuvable")
    return idx


def _cd_offset(data, eocd):
    """Offset du répertoire central (uint32 LE à EOCD+16)."""
    return struct.unpack_from("<I", data, eocd + 16)[0]


def _pairs(data, cd_off):
    """(paires [(id, valeur)…], offset de début du bloc) du bloc de signature juste avant le répertoire central."""
    if cd_off < 24 or bytes(data[cd_off - 16:cd_off]) != MAGIC:
        raise ApkError("bloc de signature APK absent (APK non signé en v2 ?)")
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]       # taille du bloc (copie de fin)
    block_start = cd_off - 8 - size
    if block_start < 0 or struct.unpack_from("<Q", data, block_start)[0] != size:
        raise ApkError("bloc de signature incohérent")
    pairs = []
    pos = block_start + 8
    end = cd_off - 24                                           # là où commence la taille de fin
    while pos < end:
        length = struct.unpack_from("<Q", data, pos)[0]
        if length < 4 or pos + 8 + length > end:
            raise ApkError("paire de bloc de signature invalide")
        pid = struct.unpack_from("<I", data, pos + 8)[0]
        pairs.append((pid, bytes(data[pos + 12:pos + 8 + length])))
        pos += 8 + length
    return pairs, block_start


def _build_block(pairs):
    """Reconstruit le bloc de signature depuis les paires, en l'alignant sur 4096 (comme apksigner)."""
    body = bytearray()
    for pid, value in pairs:
        body += struct.pack("<Q", 4 + len(value))              # longueur = id (4) + valeur
        body += struct.pack("<I", pid)
        body += value
    result = 8 + len(body) + 8 + 16                            # prefixe de taille + corps + taille de fin + magic
    if result % PAGE != 0:
        pad = PAGE - (result % PAGE)
        if pad < 12:                                           # une paire tient en 12 octets minimum
            pad += PAGE
        body += struct.pack("<Q", 4 + (pad - 12))
        body += struct.pack("<I", VERITY_PADDING_ID)
        body += b"\x00" * (pad - 12)
        result += pad
    size = result - 8                                          # « taille du bloc » = tout sauf le prefixe
    return struct.pack("<Q", size) + bytes(body) + struct.pack("<Q", size) + MAGIC


def stamp(data, value):
    """APK avec la paire loco posée à `value` (bytes). Remplace toute paire loco existante et le bourrage, réaligne,
    et corrige l'offset du répertoire central. Idempotent : re-tamponner remplace la valeur précédente."""
    data = bytearray(data)
    cd_off = _cd_offset(data, _find_eocd(data))
    pairs, block_start = _pairs(data, cd_off)
    kept = [(pid, v) for pid, v in pairs if pid not in (LOCO_BLOCK_ID, VERITY_PADDING_ID)]
    kept.append((LOCO_BLOCK_ID, bytes(value)))
    block = _build_block(kept)
    out = bytearray(bytes(data[:block_start]) + block + bytes(data[cd_off:]))
    struct.pack_into("<I", out, _find_eocd(out) + 16, block_start + len(block))
    return bytes(out)


def read_loco(data):
    """Valeur de la paire loco d'un APK, ou None (pour les tests ; l'agent fait l'équivalent en Kotlin, et avale
    de même un APK sans bloc de signature)."""
    try:
        pairs, _ = _pairs(bytearray(data), _cd_offset(data, _find_eocd(data)))
    except ApkError:
        return None
    return next((v for pid, v in pairs if pid == LOCO_BLOCK_ID), None)
