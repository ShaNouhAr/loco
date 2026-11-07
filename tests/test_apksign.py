"""Tampon de configuration dans le « bloc de signature » d'un APK (loco.apksign) : l'APK reste un ZIP valide, ses
entrées et sa signature v2/v3 ne bougent pas, et la configuration loco se relit. Mêmes calculs que l'agent en Kotlin
(Provisioning.kt). N'utilise pas d'APK réel : la vérification « apksigner verify » sur un vrai APK se fait au build."""

import io
import struct
import unittest
import zipfile

from loco import apksign

V2_SCHEME_ID = 0x7109871A          # identifiant de la paire « APK Signature Scheme v2 »


def synthetic_apk(sig_pairs=((V2_SCHEME_ID, b"pretend-v2-signature" * 6),)):
    """APK synthétique : un ZIP minimal (comme les entrées d'un APK) suivi d'un bloc de signature APK portant les
    paires fournies (par défaut une fausse signature v2) et aligné sur 4096, exactement comme apksigner le produit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", b"\x00manifest\x00" * 32)
        z.writestr("classes.dex", b"dex\n" * 400)
        z.writestr("resources.arsc", b"arsc" * 100)
    raw = bytearray(buf.getvalue())
    cd = apksign._cd_offset(raw, apksign._find_eocd(raw))
    block = apksign._build_block(list(sig_pairs))
    apk = bytearray(bytes(raw[:cd]) + block + bytes(raw[cd:]))
    struct.pack_into("<I", apk, apksign._find_eocd(apk) + 16, cd + len(block))
    return bytes(apk)


def _pairs(data):
    return dict(apksign._pairs(bytearray(data), apksign._cd_offset(data, apksign._find_eocd(data)))[0])


def _block_span(data):
    pairs, start = apksign._pairs(bytearray(data), apksign._cd_offset(data, apksign._find_eocd(data)))
    return apksign._cd_offset(data, apksign._find_eocd(data)) - start


class TestApkSign(unittest.TestCase):
    CFG = b'{"server":"https://loco.example:8443","key":"enrol-key","ca":"abcdef0123"}'

    def test_stamp_keeps_a_valid_zip_with_intact_entries(self):
        apk = synthetic_apk()
        out = apksign.stamp(apk, self.CFG)
        z, before = zipfile.ZipFile(io.BytesIO(out)), zipfile.ZipFile(io.BytesIO(apk))
        self.assertIsNone(z.testzip())
        for name in ("AndroidManifest.xml", "classes.dex", "resources.arsc"):
            self.assertEqual(z.read(name), before.read(name))
        self.assertEqual(apksign.read_loco(out), self.CFG)

    def test_existing_signature_pair_is_preserved_byte_for_byte(self):
        sig = b"PRETEND-v2-signature-bytes" * 4
        out = apksign.stamp(synthetic_apk(sig_pairs=((V2_SCHEME_ID, sig),)), self.CFG)
        pairs = _pairs(out)
        self.assertEqual(pairs[V2_SCHEME_ID], sig)                 # la signature n'est pas touchée
        self.assertIn(apksign.LOCO_BLOCK_ID, pairs)

    def test_signing_block_stays_4096_aligned(self):
        self.assertEqual(_block_span(apksign.stamp(synthetic_apk(), self.CFG)) % apksign.PAGE, 0)

    def test_restamp_replaces_without_duplicating(self):
        once = apksign.stamp(synthetic_apk(), b'{"server":"https://a","key":"k1"}')
        twice = apksign.stamp(once, b'{"server":"https://b","key":"k2"}')
        pairs = apksign._pairs(bytearray(twice), apksign._cd_offset(twice, apksign._find_eocd(twice)))[0]
        self.assertEqual(sum(1 for pid, _ in pairs if pid == apksign.LOCO_BLOCK_ID), 1)
        self.assertEqual(apksign.read_loco(twice), b'{"server":"https://b","key":"k2"}')
        self.assertEqual(_pairs(twice)[V2_SCHEME_ID], b"pretend-v2-signature" * 6)

    def test_offset_of_central_directory_is_fixed(self):
        out = apksign.stamp(synthetic_apk(), self.CFG)
        cd = apksign._cd_offset(out, apksign._find_eocd(out))
        self.assertEqual(out[cd:cd + 4], b"PK\x01\x02")            # l'offset de l'EOCD pointe bien sur le répertoire central

    def test_zip_without_signing_block_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", b"hello")
        with self.assertRaises(apksign.ApkError):
            apksign.stamp(buf.getvalue(), b"{}")
        self.assertIsNone(apksign.read_loco(buf.getvalue()))       # pas de bloc → None, sans lever


if __name__ == "__main__":
    unittest.main()
