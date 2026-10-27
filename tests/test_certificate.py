"""Certificat HTTPS du portail : renouvellement par l'autorité loco et import d'un certificat servi par SNI,
sans jamais toucher au certificat que voient les agents (autorité loco épinglée)."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, "portal")
import locoadmin

PORTAL = """server {
    listen 8443 ssl;
    server_name _;
    ssl_certificate     /etc/loco/tls/server.crt;
    ssl_certificate_key /etc/loco/tls/server.key;
    location ^~ /apt/ {
        location ~ /\\. { deny all; }   # accolades sur une ligne
    }
}
server {
    listen 8088;
    server_name _;
}
"""


def ssl(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


class CertTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tls = os.path.join(self.dir, "tls")
        os.makedirs(self.tls)
        nginx = os.path.join(self.dir, "nginx")
        os.makedirs(os.path.join(nginx, "sites-available"))
        os.makedirs(os.path.join(nginx, "sites-enabled"))
        self.site = os.path.join(nginx, "sites-available", "loco-portal.conf")
        pathlib.Path(self.site).write_text(PORTAL.replace("/etc/loco/tls", self.tls))
        patches = {"TLS": self.tls, "TLS_SPOOL": os.path.join(self.dir, "spool"), "PORTAL_SITE": self.site,
                   "SNI_SITE": os.path.join(nginx, "sites-available", "loco-portal.sni.conf"),
                   "SNI_LINK": os.path.join(nginx, "sites-enabled", "loco-portal.sni.conf")}
        for name, value in patches.items():
            p = mock.patch.object(locoadmin, name, value)
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(locoadmin.TLS_SPOOL)
        self.reload = mock.patch.object(locoadmin, "_reload_nginx", return_value=True)
        self.reload_mock = self.reload.start()
        self.addCleanup(self.reload.stop)
        conf = mock.patch.object(locoadmin, "read_conf", return_value={"HOST": "192.168.42.208"})
        conf.start()
        self.addCleanup(conf.stop)
        self.ca()

    def ca(self):
        # Configuration minimale, comme install.sh : OpenSSL 1.1 (Rocky 8) ajouterait sinon les extensions du fichier
        # système à celles de -addext (deux basicConstraints : autorité invalide).
        minimal = os.path.join(self.dir, "minimal.cnf")
        pathlib.Path(minimal).write_text("[req]\ndistinguished_name=dn\n[dn]\n")
        ssl("req", "-x509", "-config", minimal, "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
            "-subj", "/CN=test CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign",
            "-keyout", os.path.join(self.tls, "ca.key"), "-out", os.path.join(self.tls, "ca.crt"))
        ssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30", "-subj", "/CN=old",
            "-keyout", os.path.join(self.tls, "server.key"), "-out", os.path.join(self.tls, "server.crt"))

    def external(self, names, upload="a" * 32, other_key=False):
        """Certificat « d'entreprise » auto-signé pour des noms DNS, déposé dans le spool comme le fait le portail."""
        crt, key = (os.path.join(locoadmin.TLS_SPOOL, upload + e) for e in (".crt", ".key"))
        ssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30", "-subj", "/CN=x",
            "-addext", "subjectAltName=" + ",".join("DNS:" + n for n in names), "-keyout", key, "-out", crt)
        if other_key:
            ssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key)
        return upload

    def text(self, path):
        return pathlib.Path(path).read_text()

    def test_renew_signs_with_the_loco_authority(self):
        locoadmin.renew_cert(log=lambda *_: None)
        crt = os.path.join(self.tls, "server.crt")
        subprocess.run(["openssl", "verify", "-CAfile", os.path.join(self.tls, "ca.crt"), crt], check=True, capture_output=True)
        san = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-ext", "subjectAltName"], capture_output=True, text=True).stdout
        self.assertIn("IP Address:192.168.42.208", san)
        self.assertEqual(oct(os.stat(os.path.join(self.tls, "server.key")).st_mode & 0o777), "0o600")

    def test_renew_rolls_back_when_nginx_refuses(self):
        before = self.text(os.path.join(self.tls, "server.crt"))
        self.reload_mock.return_value = False
        with self.assertRaises(ValueError):
            locoadmin.renew_cert(log=lambda *_: None)
        self.assertEqual(self.text(os.path.join(self.tls, "server.crt")), before)        # ancien certificat rétabli

    def test_renew_refused_for_a_certificate_supplied_at_installation(self):
        pathlib.Path(self.tls, "custom").touch()
        with self.assertRaises(ValueError):
            locoadmin.renew_cert(log=lambda *_: None)

    def test_import_serves_the_certificate_by_sni_only(self):
        locoadmin.import_cert({"upload": self.external(["loco.example.org", "*.lab.example.org"])}, log=lambda *_: None)
        sni = self.text(locoadmin.SNI_SITE)
        self.assertIn("server_name loco.example.org *.lab.example.org;", sni)
        self.assertIn(os.path.join(self.tls, "imported.crt"), sni)
        self.assertNotIn(os.path.join(self.tls, "server.crt"), sni)
        self.assertIn("location ^~ /apt/", sni)                                          # mêmes emplacements que le portail
        self.assertNotIn("8088", sni)                                                    # seul le bloc HTTPS
        self.assertTrue(os.path.islink(locoadmin.SNI_LINK))
        self.assertIn("server_name _;", self.text(self.site))                            # bloc principal intact
        self.assertLess("loco-portal.conf", os.path.basename(locoadmin.SNI_LINK))       # chargé après : reste par défaut
        self.assertEqual(os.listdir(locoadmin.TLS_SPOOL), [])                            # fichiers téléversés effacés
        locoadmin.remove_imported_cert(log=lambda *_: None)
        self.assertFalse(os.path.exists(locoadmin.SNI_SITE))
        self.assertFalse(os.path.exists(os.path.join(self.tls, "imported.key")))

    def test_import_refuses_the_agents_address_and_a_wrong_key(self):
        with mock.patch.object(locoadmin, "read_conf", return_value={"HOST": "loco.example.org"}):
            with self.assertRaises(ValueError):
                locoadmin.import_cert({"upload": self.external(["loco.example.org"])}, log=lambda *_: None)
        with self.assertRaises(ValueError):
            locoadmin.import_cert({"upload": self.external(["web.example.org"], other_key=True)}, log=lambda *_: None)
        self.assertFalse(os.path.exists(os.path.join(self.tls, "imported.crt")))
        self.assertFalse(os.path.exists(locoadmin.SNI_SITE))

    def test_wildcard_coverage(self):
        self.assertTrue(locoadmin._name_covers("*.example.org", "loco.example.org"))
        self.assertFalse(locoadmin._name_covers("*.example.org", "a.b.example.org"))
        self.assertFalse(locoadmin._name_covers("*.example.org", "192.168.42.208"))


if __name__ == "__main__":
    unittest.main()
