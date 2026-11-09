"""Applications Android : index F-Droid signés, catalogue, Google Play et déploiement vers l'application loco."""
import base64
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

import test_portal as fixture
from test_android import ANDROID
from test_apksign import synthetic_apk
from test_fleet import Agent, FleetTest, device
from loco import android_apps, apksign, config, features, fleet
from loco.db import transaction

HERE = Path(__file__).resolve().parent
FDROID_FP = android_apps.KNOWN["fdroid"][2]
REPO = "https://repo.test/fdroid/repo"
APK3 = b"PK\x03\x04 notes 1.3" + b"\0" * 64
APK_ARM = b"PK\x03\x04 arm build" + b"\0" * 32
APK_X86 = b"PK\x03\x04 x86 build" + b"\0" * 32
ICON = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
SIGNER = "ab" * 32


def sha(data):
    return hashlib.sha256(data).hexdigest()


def b64(data):
    return base64.b64encode(hashlib.sha256(data).digest())


def make_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "depot de test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(datetime.datetime(2020, 1, 1)).not_valid_after(datetime.datetime(2040, 1, 1))
            .sign(key, hashes.SHA256()))
    return key, cert


KEY, CERT = make_key()
FINGERPRINT = sha(CERT.public_bytes(serialization.Encoding.DER))


def make_jar(entry, attributes=False, key=KEY, cert=CERT, tamper_entry=False, duplicate=False):
    """Entrée signée comme celles de F-Droid : entry.json, manifeste, fichier .SF et signature PKCS#7 détachée."""
    data = json.dumps(entry).encode()
    manifest = b"Manifest-Version: 1.0\r\n\r\nName: entry.json\r\nSHA-256-Digest: " + b64(data) + b"\r\n\r\n"
    sf = b"Signature-Version: 1.0\r\nSHA-256-Digest-Manifest: " + b64(manifest) + b"\r\n\r\n"
    options = [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary]
    if not attributes:
        options.append(pkcs7.PKCS7Options.NoAttributes)
    block = pkcs7.PKCS7SignatureBuilder().set_data(sf).add_signer(cert, key, hashes.SHA256()).sign(serialization.Encoding.DER, options)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("entry.json", data + (b" " if tamper_entry else b""))
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                z.writestr("entry.json", b"{}")
        z.writestr("META-INF/MANIFEST.MF", manifest)
        z.writestr("META-INF/TEST.SF", sf)
        z.writestr("META-INF/TEST.RSA", block)
    return out.getvalue()


def version(code, name, data, abis=(), beta=False, min_sdk=26):
    v = {"added": 1700000000000 + code, "file": {"name": f"/org.example_{code}.apk", "sha256": sha(data), "size": len(data)},
         "manifest": {"versionName": name, "versionCode": code, "usesSdk": {"minSdkVersion": min_sdk, "targetSdkVersion": 34},
                      "signer": {"sha256": [SIGNER]}, **({"nativecode": list(abis)} if abis else {})}}
    if beta:
        v["releaseChannels"] = ["Beta"]
    return v


INDEX = {
    "repo": {"name": {"en-US": "Dépôt de test"}, "timestamp": 1},
    "packages": {
        "org.example.notes": {
            "metadata": {"name": {"en-US": "Notes", "fr": "Carnet"}, "summary": {"en-US": 'Take {notes} "quoted" \\ back } ]'},
                         "description": {"en-US": "<p>First</p><ul><li>one</li></ul>"}, "categories": ["Writing"],
                         "icon": {"en-US": {"name": "/org.example.notes/en-US/icon.png", "sha256": sha(ICON), "size": len(ICON)}},
                         "license": "GPL-3.0-only", "webSite": "https://example.org", "sourceCode": "javascript:alert(1)",
                         "lastUpdated": 1700000000000},
            "versions": {"a": version(3, "1.3", APK3), "b": version(4, "1.4-beta", b"beta", beta=True),
                         "c": version(2, "1.2", b"old")}},
        "org.example.arm": {
            "metadata": {"name": {"en-US": "Natif"}, "categories": ["System"]},
            "versions": {"x": version(12, "2.0", APK_X86, abis=("x86_64",)), "y": version(11, "2.0", APK_ARM, abis=("arm64-v8a",))}},
        "bad package!": {"metadata": {"name": "x"}, "versions": {"a": version(1, "1", b"x")}},
        "org.example.nofile": {"metadata": {"name": {"en-US": "Sans fichier"}}, "versions": {"a": {"manifest": {"versionCode": 1}}}},
    },
}
INDEX_BYTES = json.dumps(INDEX, ensure_ascii=False).encode()


def entry_for(index_bytes=INDEX_BYTES, timestamp=1793762637383):
    return {"timestamp": timestamp, "version": 30000,
            "index": {"name": "/index-v2.json", "sha256": sha(index_bytes), "size": len(index_bytes), "numPackages": 4}}


class FakeResponse(io.BytesIO):
    def __init__(self, data, gz=False):
        super().__init__(gzip.compress(data) if gz else data)
        self.headers = {"Content-Encoding": "gzip"} if gz else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Repo:
    """Faux dépôt servi à la place d'Internet : entrée signée, index, logo et APK."""

    def __init__(self, jar=None, index=INDEX_BYTES, gz=False):
        self.files = {"/entry.jar": jar if jar is not None else make_jar(entry_for(index)), "/index-v2.json": index,
                      "/org.example.notes/en-US/icon.png": ICON, "/org.example_3.apk": APK3, "/org.example_11.apk": APK_ARM,
                      "/org.example_12.apk": APK_X86}
        self.gz, self.calls = gz, []

    def urlopen(self, req, timeout=None, context=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.calls.append(url)
        path = url[len(REPO):]
        if not url.startswith(REPO) or path not in self.files:
            raise OSError(f"introuvable : {url}")
        return FakeResponse(self.files[path], gz=self.gz and path.endswith(".json"))

    def __enter__(self):
        self.patch = mock.patch("loco.android_apps.urllib.request.urlopen", side_effect=self.urlopen)
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class TestSignedEntry(unittest.TestCase):
    """Vérification des entrées signées (entry.jar), sans openssl : celle de F-Droid et des entrées fabriquées."""

    def test_real_fdroid_entry_is_verified_with_its_published_key(self):
        jar = (HERE / "data" / "fdroid-entry.jar").read_bytes()
        entry = android_apps.read_entry(jar, FDROID_FP)
        self.assertEqual(entry["index"]["name"], "/index-v2.json")
        self.assertEqual(android_apps.jar_signers(jar), FDROID_FP)
        with self.assertRaises(ValueError):
            android_apps.read_entry(jar, android_apps.KNOWN["izzy"][2])

    def test_generated_entries_with_and_without_signed_attributes(self):
        for attributes in (False, True):
            entry = android_apps.read_entry(make_jar({"timestamp": 5}, attributes=attributes), FINGERPRINT)
            self.assertEqual(entry["timestamp"], 5)

    def test_tampering_and_foreign_keys_are_refused(self):
        other_key, other_cert = make_key()
        cases = {"contenu modifié": (make_jar({"timestamp": 5}, tamper_entry=True), FINGERPRINT),
                 "autre clé": (make_jar({"timestamp": 5}, key=other_key, cert=other_cert), FINGERPRINT),
                 "doublon": (make_jar({"timestamp": 5}, duplicate=True), FINGERPRINT),
                 "pas une archive": (b"not a zip", FINGERPRINT)}
        for label, (jar, fingerprint) in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                android_apps.read_entry(jar, fingerprint)
        # Signature valide, mais pas avec la clé confirmée.
        with self.assertRaises(ValueError):
            android_apps.read_entry(make_jar({"timestamp": 5}), "0" * 64)

    def test_manifest_digest_must_match(self):
        jar = make_jar({"timestamp": 5})
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(jar)) as src, zipfile.ZipFile(out, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "META-INF/MANIFEST.MF":
                    data = data.replace(b"Manifest-Version: 1.0", b"Manifest-Version: 1.1")
                dst.writestr(name, data)
        with self.assertRaises(ValueError):
            android_apps.read_entry(out.getvalue(), FINGERPRINT)


class TestIndex(unittest.TestCase):
    def test_packages_are_cut_one_by_one_despite_tricky_strings(self):
        with fixture.loco.app.app_context():
            pass
        path = Path(config.DATA) / "index-test.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(INDEX_BYTES)
        try:
            found = dict(android_apps.iter_packages(str(path)))
        finally:
            path.unlink()
        self.assertEqual(set(found), {"org.example.notes", "org.example.arm", "bad package!", "org.example.nofile"})
        self.assertEqual(found["org.example.notes"]["metadata"]["summary"]["en-US"], 'Take {notes} "quoted" \\ back } ]')

    def test_projection_keeps_stable_versions_texts_and_checked_links(self):
        row = android_apps.project("org.example.notes", INDEX["packages"]["org.example.notes"])
        versions = json.loads(row["versions"])
        self.assertEqual([v["code"] for v in versions], [3, 2])          # bêta exclue
        self.assertEqual(row["version"], "1.3")
        self.assertEqual(json.loads(row["names"]), {"en": "Notes", "fr": "Carnet"})
        self.assertEqual(json.loads(row["descriptions"])["en"], "First\n• one")
        self.assertEqual(row["website"], "https://example.org")
        self.assertEqual(row["source_code"], "")                           # lien non http(s) écarté
        self.assertEqual(json.loads(row["icon"])["sha256"], sha(ICON))
        self.assertIsNone(android_apps.project("bad package!", INDEX["packages"]["bad package!"]))
        self.assertIsNone(android_apps.project("org.example.nofile", INDEX["packages"]["org.example.nofile"]))

    def test_compatible_version_follows_android_level_and_processor(self):
        versions = json.loads(android_apps.project("org.example.arm", INDEX["packages"]["org.example.arm"])["versions"])
        self.assertEqual(android_apps.compatible(versions, 33, ["arm64-v8a", "armeabi-v7a"])["code"], 11)
        self.assertEqual(android_apps.compatible(versions, 33, ["x86_64"])["code"], 12)
        self.assertIsNone(android_apps.compatible(versions, 33, ["mips"]))
        self.assertIsNone(android_apps.compatible(versions, 21, ["x86_64"]))        # Android trop ancien


class AppsTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.reset_experimental)
        self.enable_android()
        with transaction() as conn:
            conn.execute("DELETE FROM android_apps")
            conn.execute("DELETE FROM android_sources WHERE known=''")
            conn.execute("UPDATE android_sources SET enabled=0")          # jamais Internet pendant les tests
            self.source = conn.execute("INSERT INTO android_sources (name, url, fingerprint, position, added_at) "
                                       "VALUES ('Dépôt de test', ?, ?, 50, '2026-11-06 10:00')", (REPO, FINGERPRINT)).lastrowid
        self.addCleanup(self.restore_sources)
        android_apps._APK_STATE.clear()
        if os.path.exists(android_apps.INDEX_DB):        # index local d'un test précédent (même identifiant de source)
            with android_apps._index() as idx:
                idx.execute("DELETE FROM apps")
                idx.execute("DELETE FROM sources")

    def reset_experimental(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))

    def enable_android(self):
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (features.EXPERIMENTAL_SETTING, json.dumps(["android"])))

    def restore_sources(self):
        with transaction() as conn:
            conn.execute("DELETE FROM android_apps")
            conn.execute("DELETE FROM android_sources WHERE known=''")
            conn.execute("UPDATE android_sources SET enabled=1")

    def sync(self, **repo):
        with Repo(**repo) as r:
            android_apps.refresh(self.source)
        return r

    def source_row(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM android_sources WHERE id=?", (self.source,)).fetchone()

    def android(self, version=None, sdk=33, abis=("x86_64",), apps=(), uid=None):
        version = config.VERSION if version is None else version       # « courant » suit la version, pas un numéro figé
        ident = dict(ANDROID, agent_version=version)
        a = Agent(ident, "192.168.43.71", uid=uid or ("%032x" % abs(hash((version, sdk, tuple(abis), len(apps)))))[:32])
        self.assertEqual(a.enroll().status_code, 200)
        a.checkin({"updates": [], "apps": list(apps), "system": {"sdk": sdk, "abis": list(abis), "can_install": True}})
        return a

    def add(self, package="org.example.notes"):
        r = self.admin.post("/android/apps/add", {"store": "fdroid", "source": self.source, "package": package}, page="/android/apps")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            return conn.execute("SELECT id FROM android_apps WHERE package=?", (package,)).fetchone()[0]


class TestSync(AppsTest):
    def test_index_is_verified_stored_and_not_downloaded_again_when_unchanged(self):
        repo = self.sync(gz=True)
        row = self.source_row()
        self.assertEqual((row["apps"], row["error"]), (2, ""))
        self.assertEqual(row["index_timestamp"], 1793762637383)
        self.assertIn(REPO + "/index-v2.json", repo.calls)
        repo = self.sync()
        self.assertEqual(repo.calls, [REPO + "/entry.jar"])            # même entrée signée : index inchangé

    def test_an_older_signed_entry_is_refused_as_a_replay(self):
        self.sync()
        self.sync(jar=make_jar(entry_for(timestamp=1000)))
        self.assertIn("plus ancien", self.source_row()["error"])

    def test_wrong_index_or_wrong_key_leaves_an_error_and_no_apps(self):
        self.sync(jar=make_jar(entry_for()), index=INDEX_BYTES.replace(b"Carnet", b"Carnat"))   # même taille, autre contenu
        self.assertIn("SHA-256", self.source_row()["error"])
        other_key, other_cert = make_key()
        self.sync(jar=make_jar(entry_for(), key=other_key, cert=other_cert))
        self.assertIn("signature", self.source_row()["error"])
        with fixture.loco.app.test_request_context():
            self.assertEqual(android_apps.browse("", "all")[1], 0)


class TestPages(AppsTest):
    def test_pages_need_the_experimental_feature(self):
        self.reset_experimental()
        self.assertEqual(self.admin.c.get("/android/apps").status_code, 403)
        self.enable_android()
        self.assertEqual(self.admin.c.get("/android/apps").status_code, 200)

    def test_store_detail_sources_and_catalog(self):
        self.sync()
        page = self.admin.c.get("/android/store?q=notes").get_data(as_text=True)
        self.assertIn("Carnet", page)                                      # nom français de l'index
        self.assertIn("Dépôt de test", page)
        detail = self.admin.c.get(f"/android/store/{self.source}/org.example.notes").get_data(as_text=True)
        self.assertIn("1.3", detail)
        self.assertIn(SIGNER, detail)
        self.assertNotIn("javascript:", detail)
        sources = self.admin.c.get("/android/sources").get_data(as_text=True)
        self.assertIn(FINGERPRINT.upper()[:2] + ":" + FINGERPRINT.upper()[2:4], sources)
        self.add()
        self.assertIn("org.example.notes", self.admin.c.get("/android/apps").get_data(as_text=True))
        with mock.patch("loco.android_apps.urllib.request.urlopen", side_effect=OSError("hors ligne")):
            self.assertEqual(self.admin.c.get(f"/android/icons/{self.source}/org.example.notes").status_code, 204)
        with Repo(), self.admin.c.get(f"/android/icons/{self.source}/org.example.notes") as icon:
            self.assertEqual((icon.status_code, icon.data), (200, ICON))

    def test_play_entries_accept_a_link_or_a_package_name(self):
        r = self.admin.post("/android/apps/add", {"store": "play", "package": "https://play.google.com/store/apps/details?id=com.example.game&hl=fr",
                                                  "name": "Jeu"}, page="/android/apps")
        self.assertEqual(r.status_code, 302)
        r = self.admin.post("/android/apps/add", {"store": "play", "package": "pas un paquet"}, page="/android/apps")
        with transaction() as conn:
            rows = conn.execute("SELECT package, store, name FROM android_apps").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("com.example.game", "play", "Jeu")])

    def test_known_source_and_custom_source_with_confirmed_key(self):
        with transaction() as conn:
            conn.execute("DELETE FROM android_sources WHERE known='izzy'")
        with mock.patch("loco.android_apps.refresh_in_background"):
            self.admin.post("/android/sources/known", {"known": "izzy"}, page="/android/sources")
            with transaction() as conn:
                self.assertEqual(conn.execute("SELECT fingerprint FROM android_sources WHERE known='izzy'").fetchone()[0],
                                 android_apps.KNOWN["izzy"][2])
            with transaction() as conn:
                conn.execute("DELETE FROM android_sources WHERE id=?", (self.source,))
            with Repo():
                check = self.admin.post("/android/sources/check", {"url": REPO + "/"}, page="/android/sources")
                self.assertIn(FINGERPRINT.upper()[:2], check.get_data(as_text=True))
                refused = self.admin.post("/android/sources/add", {"url": REPO, "fingerprint": FINGERPRINT}, page="/android/sources")
                self.assertEqual(refused.status_code, 302)
                with transaction() as conn:
                    self.assertIsNone(conn.execute("SELECT 1 FROM android_sources WHERE url=?", (REPO,)).fetchone())
                self.admin.post("/android/sources/add", {"url": REPO, "fingerprint": FINGERPRINT, "confirmed": "1",
                                                         "name": "Maison"}, page="/android/sources")
            with transaction() as conn:
                row = conn.execute("SELECT * FROM android_sources WHERE url=?", (REPO,)).fetchone()
                self.source = row["id"]
        self.assertEqual((row["name"], row["fingerprint"]), ("Maison", FINGERPRINT))

    def test_a_source_used_by_the_catalog_cannot_be_removed(self):
        self.sync()
        self.add()
        self.admin.post(f"/android/sources/{self.source}/delete", page="/android/sources")
        self.assertIsNotNone(self.source_row())


class TestDeployment(AppsTest):
    def deploy(self, app_ids, dev, form=None):
        data = {"app": [str(a) for a in app_ids], "device": str(dev["id"]), **(form or {})}
        r = self.admin.post("/fleet/deployments/prepare-android", data, page="/fleet/deployments/new-android")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        deployment_id = r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        r = self.admin.post(f"/fleet/deployments/{deployment_id}/confirm", page=f"/fleet/deployments/{deployment_id}")
        self.assertEqual(r.status_code, 302)
        return deployment_id

    def place_apk(self, data):
        os.makedirs(android_apps.APK_DIR, exist_ok=True)
        Path(android_apps.apk_path(sha(data))).write_bytes(data)
        self.addCleanup(lambda: Path(android_apps.apk_path(sha(data))).unlink(missing_ok=True))

    def test_compatible_apk_is_sent_once_loco_holds_it_then_the_device_downloads_it(self):
        self.sync()
        app_id = self.add("org.example.arm")
        a = self.android(abis=("arm64-v8a",))
        compose = self.admin.c.get(f"/fleet/deployments/new-android?app={app_id}")
        self.assertEqual(compose.status_code, 200)
        with mock.patch("loco.android_apps.threading.Thread"):          # pas de téléchargement réel ici
            deployment_id = self.deploy([app_id], device(a.uid))
            self.assertEqual(a.checkin().get_json()["commands"], [])     # APK pas encore chez loco : la commande attend
        self.place_apk(APK_ARM)
        android_apps._APK_STATE.clear()
        commands = a.checkin().get_json()["commands"]
        self.assertEqual(len(commands), 1)
        item = commands[0]["apps"][0]
        self.assertEqual((commands[0]["action"], item["code"], item["sha256"], item["signer"]), ("install-apk", 11, sha(APK_ARM), SIGNER))
        self.assertNotIn("source_url", item)
        self.assertEqual(a.c.get(item["url"]).status_code, 401)
        with a.c.get(item["url"], headers={"Authorization": f"Bearer {a.token}"}) as got:
            self.assertEqual((got.status_code, got.data), (200, APK_ARM))
        a.result(commands[0]["id"], "success", "Natif 2.0 : installée.",
                 inventory={"updates": [], "apps": [{"name": "Natif", "version": "2.0", "publisher": "org.example.arm", "code": 11,
                                                    "installer": "org.loco.agent"}]})
        page = self.admin.c.get(f"/fleet/deployments/{deployment_id}").get_data(as_text=True)
        self.assertIn("Natif 2.0 : installée.", page)
        device_page = self.admin.c.get(f"/fleet/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn("à jour", device_page)
        self.assertIn("loco", device_page)

    def test_apk_is_fetched_and_checked_before_being_kept(self):
        item = {"sha256": sha(APK3), "size": len(APK3), "source_url": REPO + "/org.example_3.apk"}
        with Repo():
            android_apps._fetch_apk(item)
            android_apps._fetch_apk(dict(item, sha256="0" * 64))
        self.addCleanup(lambda: Path(android_apps.apk_path(sha(APK3))).unlink(missing_ok=True))
        self.assertEqual(Path(android_apps.apk_path(sha(APK3))).read_bytes(), APK3)
        self.assertFalse(os.path.exists(android_apps.apk_path("0" * 64)))
        self.assertIn("SHA-256", android_apps._APK_STATE["0" * 64][0])

    def test_play_entries_open_the_store_and_installed_or_old_agents_are_skipped(self):
        self.sync()
        notes = self.add()
        self.admin.post("/android/apps/add", {"store": "play", "package": "com.example.game"}, page="/android/apps")
        with transaction() as conn:
            game = conn.execute("SELECT id FROM android_apps WHERE package='com.example.game'").fetchone()[0]
        current = self.android(apps=[{"name": "Notes", "version": "1.3", "publisher": "org.example.notes", "code": 3,
                                      "installer": "org.loco.agent"}])
        self.deploy([notes, game], device(current.uid))
        commands = current.checkin().get_json()["commands"]
        self.assertEqual([i["store"] for i in commands[0]["apps"]], ["play"])   # Notes 1.3 déjà installée
        old = self.android(version="2.138.0", uid="b" * 32)
        r = self.admin.post("/fleet/deployments/prepare-android", {"app": [str(game)], "device": str(device(old.uid)["id"])},
                            page="/fleet/deployments/new-android")
        self.assertEqual(r.status_code, 400)                              # aucun appareil capable d'installer

    def test_updates_are_offered_only_for_apps_installed_by_loco(self):
        self.sync()
        self.add()
        by_loco = self.android(apps=[{"name": "Notes", "version": "1.2", "publisher": "org.example.notes", "code": 2,
                                      "installer": "org.loco.agent"}])
        by_play = self.android(uid="c" * 32, apps=[{"name": "Notes", "version": "1.2", "publisher": "org.example.notes",
                                                    "code": 2, "installer": "com.android.vending"}])
        with fixture.loco.app.test_request_context():
            self.assertEqual(len(android_apps.updates_for(device(by_loco.uid))), 1)
            self.assertEqual(android_apps.updates_for(device(by_play.uid)), [])
        page = self.admin.c.get("/android/apps").get_data(as_text=True)
        self.assertIn("à mettre à jour", page)
        play_page = self.admin.c.get(f"/fleet/{device(by_play.uid)['id']}").get_data(as_text=True)
        self.assertIn("Google Play", play_page)


class TestAgentUpdate(AppsTest):
    def test_older_android_apps_are_offered_the_current_apk(self):
        old = self.android(version="2.138.0")
        with mock.patch("loco.fleet.agent_apk", return_value=("/tmp/loco-agent.apk", "d" * 64)):
            answer = old.checkin().get_json()
            self.assertEqual(answer["agent_apk"], {"version": config.VERSION, "sha256": "d" * 64})
            current = self.android(uid="e" * 32)
            self.assertNotIn("agent_apk", current.checkin().get_json())
        self.reset_experimental()
        with mock.patch("loco.fleet.agent_apk", return_value=("/tmp/loco-agent.apk", "d" * 64)):
            self.assertNotIn("agent_apk", old.checkin().get_json())

    def test_inventory_keeps_version_codes_installers_and_system(self):
        a = self.android(apps=[{"name": "Notes", "version": "1.2", "publisher": "org.example.notes", "code": 2,
                                "installer": "org.fdroid.fdroid"}], abis=("arm64-v8a", "armeabi-v7a"))
        dev = device(a.uid)
        with fixture.loco.app.test_request_context():
            self.assertEqual(android_apps.installed(dev)["org.example.notes"], {"version": "1.2", "code": 2, "installer": "org.fdroid.fdroid"})
            self.assertEqual(android_apps.device_system(dev), (33, ["arm64-v8a", "armeabi-v7a"], True))


class TestHelpers(unittest.TestCase):
    def test_play_links_and_release_names(self):
        self.assertEqual(android_apps.play_package("https://play.google.com/store/apps/details?id=org.videolan.vlc&hl=fr"), "org.videolan.vlc")
        self.assertEqual(android_apps.play_package(" org.videolan.vlc "), "org.videolan.vlc")
        for bad in ("https://evil.example/?id=org.videolan.vlc", "vlc", "org.videolan.vlc; rm"):
            with self.assertRaises(ValueError):
                android_apps.play_package(bad)
        self.assertEqual(android_apps.android_release(26), "Android 8.0")
        self.assertTrue(re.match(r"Android \d+\+", android_apps.android_release(99)))

    def test_known_sources_are_https_with_full_fingerprints(self):
        for key, (name, url, fingerprint, _desc) in android_apps.KNOWN.items():
            self.assertTrue(url.startswith("https://"), key)
            self.assertRegex(fingerprint, r"^[0-9a-f]{64}$", key)
        with transaction() as conn:
            seeded = {r["known"]: r["fingerprint"] for r in conn.execute("SELECT * FROM android_sources WHERE known!=''")}
        for key, fingerprint in seeded.items():
            self.assertEqual(fingerprint, android_apps.KNOWN[key][2])


class TestEnrollApk(AppsTest):
    """APK préconfiguré téléchargé depuis l'enrôlement : l'adresse, la clé et l'empreinte sont gravées dans le bloc
    de signature, l'APK reste un ZIP valide (sa signature n'est pas touchée) et l'agent y relira sa configuration."""

    def _apk_file(self):
        f = tempfile.NamedTemporaryFile(suffix=".apk", delete=False)
        f.write(synthetic_apk())
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_page_offers_the_preconfigured_apk(self):
        with mock.patch("loco.fleet.agent_apk", return_value=("/loco-agent.apk", "a" * 64)):
            page = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn('data-panel="android"', page)
        self.assertIn("/agent/android/enroll.apk", page)
        self.assertIn("s'inscrit toute seule", page)

    def test_button_stays_and_prefetch_runs_when_apk_absent(self):
        # APK pas encore en cache : le bouton reste proposé, la note le signale et le téléchargement de fond est lancé.
        with mock.patch("loco.fleet.agent_apk", return_value=(None, "absent")), \
             mock.patch.object(fleet, "prefetch_apk") as prefetch:
            page = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn("/agent/android/enroll.apk", page)
        self.assertIn("pas encore présent", page)
        prefetch.assert_called_once()

    def test_download_stamps_server_key_and_ca(self):
        key = Agent.key()
        with mock.patch("loco.fleet.agent_apk", return_value=(self._apk_file(), "a" * 64)), \
             mock.patch.object(fleet, "_ca_fingerprint", return_value="deadbeef42"):
            r = self.admin.c.get("/agent/android/enroll.apk")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/vnd.android.package-archive"))
        self.assertIn("filename=loco-agent.apk", r.headers["Content-Disposition"])
        self.assertEqual(json.loads(apksign.read_loco(r.data)),
                         {"server": config.HTTPS_URL, "key": key, "ca": "deadbeef42"})
        self.assertIsNone(zipfile.ZipFile(io.BytesIO(r.data)).testzip())          # l'APK reste un ZIP valide

    def test_requires_the_android_feature(self):
        self.reset_experimental()
        self.assertEqual(self.admin.c.get("/agent/android/enroll.apk").status_code, 404)


if __name__ == "__main__":
    unittest.main()
