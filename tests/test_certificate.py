"""Certificat HTTPS du portail : renouvellement par l'autorité loco et import d'un certificat servi par SNI,
sans jamais toucher au certificat que voient les agents (autorité loco épinglée)."""
import os
import pathlib
import re
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


class TestEnterpriseAuthority(CertTest):
    """Autorité au choix : autorité d'entreprise importée, demande signée par elle, bascule et retour à loco."""

    QUIET = staticmethod(lambda *_: None)

    def setUp(self):
        super().setUp()
        lang = mock.patch.object(locoadmin, "LANG", "fr")        # messages de l'agent root en français : assertions stables
        lang.start()
        self.addCleanup(lang.stop)

    def authority(self, name="Corp Root CA"):
        d = os.path.join(self.dir, name.replace(" ", "-"))
        os.makedirs(d)
        key, crt = os.path.join(d, "ca.key"), os.path.join(d, "ca.crt")
        ssl("req", "-x509", "-config", os.path.join(self.dir, "minimal.cnf"), "-newkey", "ec", "-pkeyopt",
            "ec_paramgen_curve:P-256", "-nodes", "-days", "30", "-subj", f"/CN={name}", "-addext",
            "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign", "-keyout", key, "-out", crt)
        return key, crt

    def spool(self, text, upload, ext):
        pathlib.Path(locoadmin.TLS_SPOOL, upload + ext).write_text(text)
        return upload

    def sign(self, csr, ca, san="IP:192.168.42.208,DNS:loco.corp.example"):
        out, ext = os.path.join(self.dir, "issued.crt"), os.path.join(self.dir, "issued.cnf")
        pathlib.Path(ext).write_text(f"subjectAltName={san}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n")
        ssl("x509", "-req", "-in", csr, "-CA", ca[1], "-CAkey", ca[0], "-set_serial", "0x01", "-days", "30",
            "-out", out, "-extfile", ext)
        return self.text(out)

    def issuer(self):
        """Émetteur du certificat servi, au format « CN=… » quelle que soit la version d'OpenSSL (3.0 : « CN = … »)."""
        out = subprocess.run(["openssl", "x509", "-in", os.path.join(self.tls, "server.crt"), "-noout", "-issuer"],
                             capture_output=True, text=True).stdout
        return re.sub(r"\s*=\s*", "=", out.split("=", 1)[1].strip())

    def test_full_switch_and_back(self):
        corp = self.authority()
        locoadmin.import_ca({"upload": self.spool(self.text(corp[1]), "1" * 32, ".pem")}, log=self.QUIET)
        trust = self.text(os.path.join(self.tls, "trust.pem"))
        loco = self.text(os.path.join(self.tls, "ca.crt")).strip()
        self.assertTrue(trust.startswith(loco))                                   # autorité loco en tête : empreinte épinglée
        self.assertIn(self.text(corp[1]).strip(), trust)

        locoadmin.ca_csr({"names": "loco.corp.example"}, log=self.QUIET)
        csr = os.path.join(self.tls, "enterprise.csr")
        req = subprocess.run(["openssl", "req", "-in", csr, "-noout", "-text"], capture_output=True, text=True).stdout
        self.assertIn("IP Address:192.168.42.208", req)                            # l'adresse des agents, toujours
        self.assertIn("DNS:loco.corp.example", req)
        self.assertEqual(oct(os.stat(os.path.join(self.tls, "enterprise-request.key")).st_mode & 0o777), "0o600")

        issued = self.sign(csr, corp)
        locoadmin.import_enterprise_cert({"upload": self.spool(issued, "2" * 32, ".crt")}, log=self.QUIET)
        self.assertFalse(os.path.exists(csr))                                     # demande honorée : retirée
        self.assertIn("Corp Root CA", subprocess.run(["openssl", "x509", "-in", os.path.join(self.tls, "enterprise.crt"),
                                                      "-noout", "-issuer"], capture_output=True, text=True).stdout)
        self.assertEqual(self.issuer(), "CN=old")                               # préparé, pas encore servi

        locoadmin.set_tls_mode({"mode": "enterprise"}, log=self.QUIET)
        self.assertEqual(self.issuer(), "CN=Corp Root CA")
        self.assertEqual(locoadmin.tls_mode(), "enterprise")
        with self.assertRaises(ValueError):
            locoadmin.renew_cert(log=self.QUIET)                                  # le renouvellement passe par l'autorité
        with self.assertRaises(ValueError):
            locoadmin.remove_ca(log=self.QUIET)                                   # pas de retrait sous l'autorité en service

        locoadmin.set_tls_mode({"mode": "loco"}, log=self.QUIET)
        self.assertEqual(self.issuer(), "CN=test CA")
        self.assertEqual(locoadmin.tls_mode(), "loco")
        subprocess.run(["openssl", "verify", "-CAfile", os.path.join(self.tls, "ca.crt"), os.path.join(self.tls, "server.crt")],
                       check=True, capture_output=True)
        locoadmin.remove_ca(log=self.QUIET)
        self.assertEqual(self.text(os.path.join(self.tls, "trust.pem")).strip(), loco)
        self.assertFalse(os.path.exists(os.path.join(self.tls, "enterprise.crt")))

    def test_enterprise_certificate_refusals(self):
        corp, other = self.authority(), self.authority("Other CA")
        locoadmin.import_ca({"upload": self.spool(self.text(corp[1]), "1" * 32, ".pem")}, log=self.QUIET)
        locoadmin.ca_csr({"names": ""}, log=self.QUIET)
        csr = os.path.join(self.tls, "enterprise.csr")
        for issued, reason in ((self.sign(csr, corp, san="DNS:loco.corp.example"), "ne couvre pas"),
                               (self.sign(csr, other), "pas signé")):
            with self.assertRaises(ValueError) as e:
                locoadmin.import_enterprise_cert({"upload": self.spool(issued, "3" * 32, ".crt")}, log=self.QUIET)
            self.assertIn(reason, str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tls, "enterprise.crt")))
        self.assertEqual(os.listdir(locoadmin.TLS_SPOOL), [])                     # téléversements toujours effacés
        with self.assertRaises(ValueError):                                       # rien de prêt : pas de bascule
            locoadmin.set_tls_mode({"mode": "enterprise"}, log=self.QUIET)
        self.assertEqual(self.issuer(), "CN=old")

    def test_leaf_certificate_is_not_an_authority(self):
        key, csr = os.path.join(self.dir, "leaf.key"), os.path.join(self.dir, "leaf.csr")
        ssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=leaf",
            "-keyout", key, "-out", csr)
        leaf = self.sign(csr, self.authority())                                   # basicConstraints CA:FALSE
        with self.assertRaises(ValueError):
            locoadmin.import_ca({"upload": self.spool(leaf, "4" * 32, ".pem")}, log=self.QUIET)
        self.assertFalse(os.path.exists(os.path.join(self.tls, "enterprise-ca.pem")))

    def test_certificate_with_its_own_key_and_renewal_in_service(self):
        corp = self.authority()
        locoadmin.import_ca({"upload": self.spool(self.text(corp[1]), "1" * 32, ".pem")}, log=self.QUIET)
        for n, upload in enumerate(("5" * 32, "6" * 32)):
            key, csr = os.path.join(self.dir, f"k{n}.key"), os.path.join(self.dir, f"k{n}.csr")
            ssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=loco",
                "-keyout", key, "-out", csr)
            self.spool(self.sign(csr, corp), upload, ".crt")
            self.spool(self.text(key), upload, ".key")
            locoadmin.import_enterprise_cert({"upload": upload, "key": "1"}, log=self.QUIET)
            if not n:
                locoadmin.set_tls_mode({"mode": "enterprise"}, log=self.QUIET)
                first = self.text(os.path.join(self.tls, "server.crt"))
        self.assertNotEqual(self.text(os.path.join(self.tls, "server.crt")), first)    # en service : remplacé aussitôt
        self.assertEqual(self.issuer(), "CN=Corp Root CA")

    def test_switch_rolls_back_when_nginx_refuses(self):
        corp = self.authority()
        locoadmin.import_ca({"upload": self.spool(self.text(corp[1]), "1" * 32, ".pem")}, log=self.QUIET)
        locoadmin.ca_csr({"names": ""}, log=self.QUIET)
        locoadmin.import_enterprise_cert({"upload": self.spool(self.sign(os.path.join(self.tls, "enterprise.csr"), corp),
                                                               "7" * 32, ".crt")}, log=self.QUIET)
        self.reload_mock.return_value = False
        with self.assertRaises(ValueError):
            locoadmin.set_tls_mode({"mode": "enterprise"}, log=self.QUIET)
        self.assertEqual(self.issuer(), "CN=old")
        self.assertEqual(locoadmin.tls_mode(), "loco")

    def test_request_with_a_very_long_machine_name(self):
        corp = self.authority()
        locoadmin.import_ca({"upload": self.spool(self.text(corp[1]), "1" * 32, ".pem")}, log=self.QUIET)
        long_name = "fv-az1234-567.abcdefghijklmnopqrstuvwxyz0123.cx.internal.cloudapp.example"   # plus de 64 caractères
        with mock.patch.object(locoadmin.socket, "getfqdn", return_value=long_name):
            locoadmin.ca_csr({"names": ""}, log=self.QUIET)
        req = subprocess.run(["openssl", "req", "-in", os.path.join(self.tls, "enterprise.csr"), "-noout", "-text"],
                             capture_output=True, text=True).stdout
        self.assertIn("DNS:" + long_name, req)                                    # le nom long reste couvert

    def test_parameters_are_validated(self):
        with self.assertRaises(ValueError):
            locoadmin.tls_params("set-tls-mode", {"mode": "autre"})
        with self.assertRaises(ValueError):
            locoadmin.tls_params("ca-csr", {"names": "a b;rm -rf /"})
        self.assertEqual(locoadmin.tls_params("ca-csr", {"names": "Loco.Corp.example, loco.corp.example"}),
                         {"names": "loco.corp.example"})


if __name__ == "__main__":
    unittest.main()
