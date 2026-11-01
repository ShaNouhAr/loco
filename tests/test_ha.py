"""Haute disponibilité : chiffrement, paquet de configuration, relevé des dépôts, configuration keepalived (côté
agent root locoha), puis l'authentification, le garde du secours et les points d'accès de réplication (côté portail)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import test_portal as fixture  # fixe l'environnement et sys.path (portal/)
import locoha
from loco import ha, config

Browser, ADMIN_PW = fixture.Browser, fixture.ADMIN_PW


# --------------------------------------------------------------------------- côté agent (locoha, sans Flask)

class CryptoTest(unittest.TestCase):
    def test_seal_roundtrip_and_wrong_key(self):
        key = locoha.derive("S-secret", "enc")
        blob = locoha.seal(b"bonjour", key)
        self.assertTrue(blob.startswith(b"LHA1"))
        self.assertEqual(locoha.unseal(blob, key), b"bonjour")
        with self.assertRaises(ValueError):
            locoha.unseal(blob, locoha.derive("autre", "enc"))
        with self.assertRaises(ValueError):
            locoha.unseal(b"xxxx", key)

    def test_tokens_are_deterministic_and_purpose_separated(self):
        self.assertEqual(locoha.auth_token("S-x"), locoha.auth_token("S-x"))
        self.assertNotEqual(locoha.auth_token("S-x"), locoha.auth_token("S-y"))
        self.assertNotEqual(locoha.derive("S-x", "enc"), locoha.derive("S-x", "auth"))
        # Le vérificateur est l'empreinte du jeton (le portail ne garde jamais le secret).
        self.assertEqual(locoha.verifier(locoha.auth_token("S-x")),
                         locoha.verifier(locoha.auth_token("S-x")))


class KeepalivedConfTest(unittest.TestCase):
    def test_conf_has_the_expected_directives(self):
        conf = {"vip": "192.168.1.20/24", "interface": "eth0", "vrid": 20, "auth_pass": "deadbeef",
                "self": {"address": "192.168.1.11", "priority": 150}, "peer": {"address": "192.168.1.12"}}
        text = locoha.keepalived_conf(conf, "/usr/bin/python3", "/opt/loco/portal/locoadmin.py")
        self.assertIn("vrrp_instance loco", text)
        self.assertIn("state BACKUP", text)          # jamais MASTER : le contrôle de santé décide
        self.assertIn("nopreempt", text)
        self.assertIn("virtual_router_id 20", text)
        self.assertIn("priority 150", text)
        self.assertIn("unicast_src_ip 192.168.1.11", text)
        self.assertIn("192.168.1.12", text)
        self.assertIn("192.168.1.20/24 dev eth0", text)
        self.assertIn("ha-check", text)
        self.assertIn("ha-notify", text)
        self.assertIn("enable_script_security", text)


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.etc = os.path.join(self.tmp, "etc")
        self.state = os.path.join(self.tmp, "state")
        for d in (self.etc, self.state):
            os.makedirs(d)
        self.p = mock.patch.multiple(locoha, ETC=self.etc, STATE=self.state)
        self.p.start()
        self.addCleanup(self.p.stop)

    def write(self, rel, data, base=None):
        path = os.path.join(base or self.etc, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(data)
        return path

    def test_bundle_includes_config_and_skips_node_specific(self):
        self.write("loco.conf", "HOST=a\n")
        self.write("tls/server.crt", "cert")
        self.write("ha/node.json", "{}")             # propre au serveur : jamais répliqué
        self.write("server-shell", "boot")           # idem
        os.makedirs(os.path.join(self.state, "gnupg"))
        self.write("key", "secring", base=os.path.join(self.state, "gnupg"))
        blob = locoha.bundle()
        import io
        import tarfile
        names = tarfile.open(fileobj=io.BytesIO(blob)).getnames()
        self.assertIn("etc/loco.conf", names)
        self.assertIn("etc/tls/server.crt", names)
        self.assertIn("state/gnupg/key", names)
        self.assertFalse(any(n.startswith("etc/ha") for n in names))
        self.assertNotIn("etc/server-shell", names)

    def test_bundle_is_deterministic(self):
        self.write("loco.conf", "HOST=a\n")
        self.assertEqual(locoha.bundle(), locoha.bundle())

    def test_apply_bundle_writes_and_removes(self):
        self.write("loco.conf", "HOST=primary\n")
        self.write("appearance-x", "logo", base=self.state)  # pas dans STATE_DIRS -> absent du paquet
        blob = locoha.bundle()
        # Un second serveur, avec un fichier en trop à retirer.
        etc2, state2 = os.path.join(self.tmp, "etc2"), os.path.join(self.tmp, "state2")
        os.makedirs(os.path.join(etc2, "tls")); os.makedirs(state2)
        with open(os.path.join(etc2, "stale.conf"), "w") as f:
            f.write("old")
        with mock.patch.multiple(locoha, ETC=etc2, STATE=state2):
            changed = locoha.apply_bundle(blob)
            self.assertTrue(os.path.isfile(os.path.join(etc2, "loco.conf")))
            self.assertFalse(os.path.isfile(os.path.join(etc2, "stale.conf")))  # retiré : absent du principal
        self.assertIn("loco.conf", changed)


class ManifestSyncTest(unittest.TestCase):
    def test_manifest_and_sync_make_repos_identical(self):
        src = tempfile.mkdtemp()
        dst = tempfile.mkdtemp()
        os.makedirs(os.path.join(src, "apt", "dists"))
        with open(os.path.join(src, "apt", "dists", "Packages"), "wb") as f:
            f.write(b"Package: hello\n")
        os.makedirs(os.path.join(dst, "apt"))
        with open(os.path.join(dst, "apt", "obsolete"), "w") as f:
            f.write("vieux")                         # doit disparaître
        files = locoha.manifest(src)
        self.assertIn("apt/dists/Packages", files)

        def fetch(rel, f):
            with open(os.path.join(src, rel), "rb") as s:
                f.write(s.read())
        copied, size, removed = locoha.sync_files(files, fetch, root=dst)
        self.assertTrue(os.path.isfile(os.path.join(dst, "apt", "dists", "Packages")))
        self.assertFalse(os.path.isfile(os.path.join(dst, "apt", "obsolete")))
        self.assertEqual(locoha.manifest(dst), locoha.manifest(src))

    def test_safe_rel_blocks_traversal(self):
        self.assertIsNone(locoha.safe_rel("apt/../../etc/passwd"))
        self.assertIsNone(locoha.safe_rel("/etc/passwd"))
        self.assertIsNone(locoha.safe_rel("secret/x"))   # hors AREAS
        self.assertEqual(locoha.safe_rel("apt/dists/Packages"), "apt/dists/Packages")


# --------------------------------------------------------------------------- côté portail (Flask)

class HaDir:
    """Un /etc/loco/ha jetable + ha-out, avec les fichiers de vérification d'un principal appairé."""
    def __init__(self, secret="S-cluster", code="LHA-aaaaaaaaaaaaaaaaaaaaaaaa"):
        self.tmp = tempfile.mkdtemp()
        self.etc = os.path.join(self.tmp, "etc")
        self.agent = os.path.join(self.tmp, "agent")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.etc, "ha"))
        os.makedirs(self.agent)
        os.makedirs(self.data)
        self.secret, self.code = secret, code

    def __enter__(self):
        self.p = mock.patch.multiple(locoha, ETC=self.etc, AGENT_DIR=self.agent)
        self.p.start()
        self.pc = mock.patch.object(config, "DATA", self.data)
        self.pc.start()
        # fichiers de vérification (empreintes des jetons) + paquet d'appairage
        with open(locoha.verifier_path(), "w") as f:
            f.write(locoha.verifier(locoha.auth_token(self.secret)) + "\n")
        with open(locoha.join_verifier_path(), "w") as f:
            f.write(locoha.verifier(locoha.auth_token(self.code)) + "\n")
        os.makedirs(locoha.out_dir(), exist_ok=True)
        with open(locoha._out("join.bin"), "wb") as f:
            f.write(b"SEALED-PACKAGE")
        with open(locoha._out("state.json"), "w") as f:
            f.write('{"role":"primary"}')
        return self

    def __exit__(self, *a):
        self.pc.stop()
        self.p.stop()


class ApiAuthTest(unittest.TestCase):
    def setUp(self):
        from loco import app
        self.c = app.test_client()

    def hdr(self, token):
        return {"X-Loco-HA": token, "Content-Type": "application/json"}

    def get(self, path, **kw):
        r = self.c.get(path, **kw)
        r.get_data()                                 # matérialise le corps avant de refermer le fichier (send_file)
        r.close()                                    # CI sous -W error::ResourceWarning
        return r

    def test_state_requires_a_valid_token(self):
        with HaDir() as d, mock.patch.object(ha, "configured", return_value=True):
            self.assertEqual(self.get("/ha/api/state").status_code, 403)
            self.assertEqual(self.get("/ha/api/state", headers=self.hdr("mauvais")).status_code, 403)
            r = self.get("/ha/api/state", headers=self.hdr(locoha.auth_token(d.secret)))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()["role"], "primary")

    def test_file_endpoint_refuses_traversal_and_serves_repo(self):
        with HaDir() as d, mock.patch.object(ha, "configured", return_value=True):
            os.makedirs(os.path.join(d.data, "apt"))
            with open(os.path.join(d.data, "apt", "Packages"), "w") as f:
                f.write("Package: x\n")
            tok = self.hdr(locoha.auth_token(d.secret))
            self.assertEqual(self.get("/ha/api/file?p=../etc/passwd", headers=tok).status_code, 400)
            self.assertEqual(self.get("/ha/api/file?p=apt/Packages", headers=tok).data, b"Package: x\n")

    def test_join_checks_version_then_returns_package_and_enrolls(self):
        with HaDir() as d, mock.patch.object(ha, "configured", return_value=True), \
                mock.patch.object(ha.locoadmin, "request") as req:
            bad = self.c.post("/ha/api/join", headers=self.hdr(locoha.auth_token(d.code)),
                              data=json.dumps({"version": "0.0.0", "address": "192.168.1.12"}))
            self.assertEqual(bad.status_code, 409)
            req.assert_not_called()
            ok = self.c.post("/ha/api/join", headers=self.hdr(locoha.auth_token(d.code)),
                             data=json.dumps({"version": config.VERSION, "address": "192.168.1.12", "name": "lab-ha-2"}))
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.data, b"SEALED-PACKAGE")
            req.assert_called_once()
            self.assertEqual(req.call_args[0][0], "ha-peer")
            self.assertEqual(req.call_args[0][1]["address"], "192.168.1.12")

    def test_leave_requests_disable(self):
        with HaDir() as d, mock.patch.object(ha, "configured", return_value=True), \
                mock.patch.object(ha.locoadmin, "request") as req:
            self.assertEqual(self.c.post("/ha/api/leave", headers=self.hdr("mauvais")).status_code, 403)
            r = self.c.post("/ha/api/leave", headers=self.hdr(locoha.auth_token(d.secret)))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(req.call_args[0][0], "ha-disable")


class StandbyGuardTest(unittest.TestCase):
    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def test_standby_redirects_normal_pages_but_keeps_account_and_api(self):
        with mock.patch.object(ha, "role", return_value="standby"):
            # Une page normale est renvoyée vers la page HA.
            r = self.admin.c.get("/fleet")
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/system/ha"))
            # La page HA, le compte et l'état restent accessibles.
            self.assertEqual(self.admin.c.get("/system/ha").status_code, 200)
            self.assertEqual(self.admin.c.get("/healthz").status_code, 200)
            self.assertEqual(self.admin.c.get("/account").status_code, 200)
            # Les agents sont priés de réessayer (ils visent l'IP virtuelle).
            self.assertEqual(self.admin.c.post("/agent/api/checkin").status_code, 503)

    def test_primary_serves_normally(self):
        with mock.patch.object(ha, "role", return_value="primary"):
            self.assertEqual(self.admin.c.get("/fleet").status_code, 200)


if __name__ == "__main__":
    unittest.main()
