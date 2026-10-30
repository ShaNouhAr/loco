"""Tests du portail (bibliothèque standard + Flask) : python3 -m unittest discover -s tests"""

import atexit
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="lr-test-")
for sub in ("data", "state", "etc"):
    os.makedirs(os.path.join(TMP, sub), exist_ok=True)
os.environ.update(LOCO_DATA=os.path.join(TMP, "data"), LOCO_STATE=os.path.join(TMP, "state"), LOCO_ETC=os.path.join(TMP, "etc"),
                  LOCO_SECURE_COOKIE="0", LOCO_ADMIN_URL="https://repo.test:8443", LOCO_PUBLIC_URL="http://repo.test:8088",
                  LOCO_HOST="repo.test", LOCO_WU_LOG=os.path.join(TMP, "wu.log"))
sys.path.insert(0, os.path.join(ROOT, "portal"))

import loco  # noqa: E402
from loco import auth, config, util  # noqa: E402
from loco.db import transaction as connect  # noqa: E402

loco.startup()
ADMIN_PW = "Admin-Password-2026"
with connect() as conn:
    auth.create_user(conn, "admin", ADMIN_PW, "admin", must_change=False)


@atexit.register
def _cleanup():
    """À la fin du processus : les autres modules de test (APT, RPM) partagent ce dossier et sa base."""
    if shutil.which("gpgconf") and os.path.isdir(config.GNUPGHOME):
        subprocess.run(["gpgconf", "--homedir", config.GNUPGHOME, "--kill", "gpg-agent"],
                       capture_output=True, check=False)
    shutil.rmtree(TMP, ignore_errors=True)


class Browser:
    def __init__(self):
        self.c = loco.app.test_client()

    def token(self, path="/login"):
        html = self.c.get(path).get_data(as_text=True)
        m = re.search(r'name="csrf" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def login(self, user, pw):
        return self.c.post("/login", data={"csrf": self.token(), "username": user, "password": pw})

    def post(self, path, data=None, page="/"):
        d = dict(data or {})
        d["csrf"] = self.token(page)
        return self.c.post(path, data=d)


def reset_failures():
    with connect() as conn:
        conn.execute("DELETE FROM login_failures")


class TestUtil(unittest.TestCase):
    def test_safe_next(self):
        for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/\t/evil", "javascript:x", ""):
            self.assertEqual(util.safe_next(bad), "/", bad)
        self.assertEqual(util.safe_next("/linux?x=1"), "/linux?x=1")

    def test_helpers(self):
        self.assertEqual(util.slugify("L'été « spécial »"), "l-ete-special")
        self.assertEqual(util.kind_for("Setup.MSI"), "msi")
        self.assertEqual(util.kind_for("archive"), "file")
        self.assertEqual(util.human(88112952), "84,0 Mo")
        self.assertEqual(util.date_fr("2026-10-12 20:57"), "12/10 20:57")

    def test_download_rejects_other_schemes(self):
        with self.assertRaises(ValueError):
            util.download("file:///etc/passwd", TMP)

    def test_password_policy(self):
        self.assertIsNotNone(auth.password_problem("court"))
        self.assertIsNotNone(auth.password_problem("bob-bob-bob-bob-bob", "bob"))
        self.assertIsNotNone(auth.password_problem("aaaaaaaaaaaaaaaa"))
        self.assertIsNone(auth.password_problem("Un-Vrai-Mot-De-Passe"))


class TestAuth(unittest.TestCase):
    def setUp(self):
        reset_failures()

    def test_pages_require_login(self):
        b = Browser()
        for path in ("/", "/linux", "/windows-apps", "/accounts", "/system", "/api/status"):
            r = b.c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"])

    def test_login_and_headers(self):
        b = Browser()
        self.assertEqual(b.login("admin", ADMIN_PW).status_code, 302)
        r = b.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertNotIn("<script>", r.get_data(as_text=True))       # aucun script en ligne (CSP)
        self.assertNotIn('style="', r.get_data(as_text=True))       # aucun style en ligne (CSP)

    def test_wrong_password_and_lockout(self):
        for _ in range(config.LOGIN_MAX_PER_USER):
            self.assertEqual(Browser().login("admin", "mauvais").status_code, 401)
        r = Browser().login("admin", ADMIN_PW)
        self.assertEqual(r.status_code, 429)
        reset_failures()
        self.assertEqual(Browser().login("admin", ADMIN_PW).status_code, 302)

    def test_csrf_required(self):
        b = Browser()
        b.login("admin", ADMIN_PW)
        self.assertEqual(b.c.post("/windows-apps/add", data={"name": "x"}).status_code, 400)

    def test_viewer_and_must_change(self):
        with connect() as conn:
            auth.create_user(conn, "lecteur", "Temporaire-123456", "viewer", must_change=True)
        b = Browser()
        r = b.login("lecteur", "Temporaire-123456")
        self.assertTrue(r.headers["Location"].endswith("/account"))
        self.assertTrue(b.c.get("/linux").headers["Location"].endswith("/account"))
        # Page dédiée : formulaire seul, aucun lien de navigation vers le reste du portail.
        page = b.c.get("/account").get_data(as_text=True)
        self.assertIn("Choisissez votre mot de passe", page)
        self.assertNotIn('class="nav"', page)
        self.assertNotIn('href="/linux"', page)
        self.assertEqual(b.c.get("/api/status", headers={"X-Requested-With": "fetch"}).status_code, 403)
        r = b.post("/account", {"current": "Temporaire-123456", "new": "court", "confirm": "court"}, page="/account")
        self.assertIn("au moins", b.c.get(r.headers["Location"]).get_data(as_text=True))
        b.post("/account", {"current": "Temporaire-123456", "new": "Nouveau-Secret-2026x", "confirm": "Nouveau-Secret-2026x"},
               page="/account")
        self.assertEqual(b.c.get("/linux").status_code, 200)
        self.assertEqual(b.post("/windows-apps/add", {"name": "x", "url": "https://x.test/a.exe"}).status_code, 403)
        self.assertEqual(b.c.get("/accounts").status_code, 403)
        with connect() as conn:
            conn.execute("DELETE FROM users WHERE username='lecteur'")

    def test_last_admin_protected(self):
        b = Browser()
        b.login("admin", ADMIN_PW)
        with connect() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        b.post(f"/accounts/{uid}/delete", page="/accounts")
        with connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone())


class TestClients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, kind, args, filename, sha256, status) VALUES "
                         "('app-o-test', 'L''appli « test »', 'exe', '/S', 'setup prog.exe', 'ab12', 'ready')")

    def test_install_script_follows_scheme(self):
        c = loco.app.test_client()
        https = c.get("/win/i/app-o-test,absent", base_url="https://repo.test:8443").get_data(as_text=True)
        self.assertIn("'https://repo.test:8443/win/files/app-o-test/setup%20prog.exe'", https)
        self.assertIn("Name = 'L''appli « test »'", https)            # apostrophe échappée pour PowerShell
        self.assertIn("Absent du catalogue (ou pas encore pret) : absent", https)
        http = c.get("/win/i/app-o-test", base_url="http://repo.test:8088").get_data(as_text=True)
        self.assertIn("'http://repo.test:8088/win/files/", http)

    def test_install_script_rejects_bad_slugs(self):
        c = loco.app.test_client()
        self.assertEqual(c.get("/win/i/..%2fetc").status_code, 404)
        self.assertEqual(c.get("/win/i/" + ",".join(["a"] * 101)).status_code, 404)

    def test_setup_script(self):
        c = loco.app.test_client()
        s = c.get("/linux/setup.sh", base_url="http://repo.test:8088").get_data(as_text=True)
        self.assertIn('BASE="http://repo.test:8088"', s)
        self.assertIn('HTTPS_BASE="https://repo.test:8443"', s)
        self.assertIn("--trust-ca", s)
        self.assertIn('Acquire::https::Proxy "DIRECT";', s)
        self.assertIn('APT::Update::Error-Mode=any', s)

    def test_proxmox_script(self):
        c = loco.app.test_client()
        s = c.get("/linux/proxmox.sh", base_url="http://repo.test:8088").get_data(as_text=True)
        self.assertIn('BASE="http://repo.test:8088"', s)
        self.assertIn('Acquire::https::Proxy "DIRECT";', s)
        self.assertIn('APT::Update::Error-Mode=any', s)
        # Choix de la variante et retrait.
        self.assertIn("--enterprise", s)
        self.assertIn("--no-subscription", s)
        self.assertIn("--remove", s)
        self.assertIn("--trust-ca", s)
        # Remplace (ne duplique pas) le dépôt Proxmox trouvé dans sources.list.d.
        self.assertIn("/etc/apt/sources.list.d/*.sources", s)
        self.assertIn("/debian/pve", s)
        self.assertIn("pve-no-subscription", s)
        self.assertIn("pve-enterprise", s)
        self.assertIn("download.proxmox.com/debian/pve", s)
        self.assertIn("enterprise.proxmox.com/debian/pve", s)
        self.assertIn("proxmox-archive-keyring.gpg", s)

    def test_flatpak_endpoints(self):
        os.makedirs(config.APT_DIR, exist_ok=True)
        with open(os.path.join(config.APT_DIR, "loco.gpg"), "wb") as f:
            f.write(b"\x99cle-de-test")
        c = loco.app.test_client()
        repo = c.get("/flatpak/loco.flatpakrepo", base_url="https://repo.test:8443").get_data(as_text=True)
        self.assertIn("Url=https://repo.test:8443/flatpak/repo/", repo)
        self.assertIn("GPGKey=mWNsZS1kZS10ZXN0", repo)
        setup = c.get("/flatpak/setup.sh", base_url="http://repo.test:8088").get_data(as_text=True)
        self.assertIn('BASE="http://repo.test:8088"', setup)
        self.assertIn("flatpak remote-modify --system flathub", setup)

    def test_flatpak_rejects_bad_ref(self):
        reset_failures()
        b = Browser()
        b.login("admin", ADMIN_PW)
        for ref in ("app/../../etc/x86_64/stable", "app/org.x/x86_64/stable;rm", "--delete"):
            self.assertEqual(b.post("/flatpak/delete", {"ref": ref}).status_code, 400, ref)

    def test_metrics(self):
        with mock.patch("loco.system.service_active", return_value=True):
            body = loco.app.test_client().get("/metrics").get_data(as_text=True)
        self.assertIn('loco_info{version="%s"} 1' % config.VERSION, body)
        self.assertIn('loco_windows_apps{status="ready"}', body)


class TestLastLoginAddress(unittest.TestCase):
    """Comptes : adresse de la dernière connexion, enregistrée à chaque connexion et reprise du journal pour l'existant."""

    def test_accounts_page_shows_where_each_account_last_logged_in(self):
        reset_failures()
        b = Browser()
        b.c.post("/login", data={"csrf": b.token(), "username": "admin", "password": ADMIN_PW},
                 headers={"X-Forwarded-For": "192.168.43.77"})
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT last_ip FROM users WHERE username='admin'").fetchone()[0], "192.168.43.77")
        page = b.c.get("/accounts").get_data(as_text=True)
        self.assertIn('depuis <span class="mono">192.168.43.77</span>', page)
        self.assertIn(">historique</a>", page)
        self.assertRegex(page, r'href="/log\?[^"]*compte=admin')

    def test_existing_accounts_take_the_address_of_their_last_login_from_the_journal(self):
        import sqlite3
        from loco.db import MIGRATIONS
        script = next(m for m in MIGRATIONS if "last_ip" in m)
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);"
                           "CREATE TABLE audit (id INTEGER PRIMARY KEY, username TEXT, ip TEXT, action TEXT);"
                           "INSERT INTO users (username) VALUES ('marie'), ('jamais');"
                           "INSERT INTO audit (username, ip, action) VALUES ('marie', '192.168.43.20', 'connexion'),"
                           " ('marie', '192.168.43.21', 'sso.connexion'), ('marie', '192.168.43.99', 'comptes.ajout');")
        conn.executescript(script)
        self.assertEqual(dict(conn.execute("SELECT username, last_ip FROM users")), {"marie": "192.168.43.21", "jamais": ""})
        conn.close()


class TestLease(unittest.TestCase):
    def setUp(self):
        with connect() as conn:
            conn.execute("DELETE FROM settings WHERE key LIKE 'lease_%'")

    def test_single_holder_until_it_expires(self):
        import json
        import time
        from loco import db
        self.assertTrue(db.lease("t", ttl=60))                 # libre : on le prend
        self.assertTrue(db.lease("t", ttl=60))                 # même instance : renouvelle
        with connect() as conn:                                # un autre processus le tient (frais)
            conn.execute("INSERT INTO settings(key,value) VALUES('lease_t',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps({"holder": "OTHER", "until": time.time() + 120}),))
        self.assertFalse(db.lease("t", ttl=60))                # occupé : on s'abstient
        with connect() as conn:                                # bail expiré
            conn.execute("UPDATE settings SET value=? WHERE key='lease_t'",
                         (json.dumps({"holder": "OTHER", "until": time.time() - 1}),))
        self.assertTrue(db.lease("t", ttl=60))                 # on reprend la main


class TestBackups(unittest.TestCase):
    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        self.tmp = tempfile.mkdtemp()
        self._orig = config.BACKUP_DIR
        config.BACKUP_DIR = self.tmp
        self.name = "loco-test-20250101-000000.tar.gz"
        with open(os.path.join(self.tmp, self.name), "wb") as f:
            f.write(b"\x1f\x8b\x08\x00backup-content")

    def tearDown(self):
        config.BACKUP_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(os.path.join(config.STATE, "restore-uploads"), ignore_errors=True)

    def test_download_rejects_bad_names_and_unknown_files(self):
        self.assertEqual(self.admin.c.get("/system/backups/..%2f..%2fetc%2fpasswd").status_code, 404)
        self.assertEqual(self.admin.c.get("/system/backups/loco-x.tar.gz").status_code, 404)
        self.assertEqual(self.admin.c.get("/system/backups/loco-absent-20990101-000000.tar.gz").status_code, 404)

    def test_download_serves_the_archive_as_attachment(self):
        r = self.admin.c.get("/system/backups/" + self.name)
        try:
            self.assertEqual(r.status_code, 200)
            self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
            self.assertEqual(r.data, b"\x1f\x8b\x08\x00backup-content")
        finally:
            r.close()

    def test_restore_submits_the_chosen_backup_to_the_agent(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/restore", {"archive": self.name}, page="/system")
            self.assertEqual(r.status_code, 302)
            la.request.assert_called_once()
            self.assertEqual(la.request.call_args[0][0], "restore")
            self.assertEqual(la.request.call_args[0][1], {"archive": self.name})

    def test_restore_refuses_an_unknown_archive(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/restore", {"archive": "loco-absent-20990101-000000.tar.gz"}, page="/system")
            self.assertEqual(r.status_code, 302)
            la.request.assert_not_called()

    def test_delete_submits_to_the_agent(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/backups/" + self.name + "/delete", {}, page="/system")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(la.request.call_args[0][0], "delete-backup")
            self.assertEqual(la.request.call_args[0][1], {"name": self.name})

    def test_delete_rejects_an_unknown_backup(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/backups/loco-absent-20990101-000000.tar.gz/delete", {}, page="/system")
            self.assertEqual(r.status_code, 302)
            la.request.assert_not_called()

    @staticmethod
    def _archive_bytes(entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, content in entries:
                m = tarfile.TarInfo(name)
                m.size = len(content)
                tar.addfile(m, io.BytesIO(content))
        return buf.getvalue()

    def _upload(self, blob, filename="backup.tar.gz"):
        return self.admin.c.post("/system/restore/upload", content_type="multipart/form-data",
                                 data={"csrf": self.admin.token("/system"),
                                       "archive": (io.BytesIO(blob), filename)})

    def test_uploaded_backup_is_verified_then_submitted(self):
        good = self._archive_bytes([("etc/loco/loco.conf", b"DATA=/srv/loco\n"), ("portal.db", b"db")])
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self._upload(good)
            self.assertEqual(r.status_code, 302)
            la.request.assert_called_once()
            self.assertEqual(la.request.call_args[0][0], "restore")
            self.assertIn("upload", la.request.call_args[0][1])

    def test_uploaded_invalid_archive_is_rejected_without_restoring(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self._upload(b"this is not a gzip archive")
            self.assertEqual(r.status_code, 302)
            la.request.assert_not_called()

    def test_retention_is_sent_to_the_agent(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/backup-retention", {"keep": "5"}, page="/system")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(la.request.call_args[0][0], "set-backup-keep")
            self.assertEqual(la.request.call_args[0][1], {"keep": 5})

    def test_retention_rejects_out_of_range(self):
        with mock.patch("loco.system.locoadmin") as la:
            la.running_job.return_value = None
            la.agent_running.return_value = True
            r = self.admin.post("/system/backup-retention", {"keep": "999"}, page="/system")
            self.assertEqual(r.status_code, 302)
            la.request.assert_not_called()


class TestFirewallPorts(unittest.TestCase):
    """Référence des ports à ouvrir dans Système : port HTTPS toujours listé, le reste suit les fonctionnalités."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def test_section_lists_the_https_port(self):
        import urllib.parse
        port = str(urllib.parse.urlsplit(config.HTTPS_URL).port or 8443)
        html = self.admin.c.get("/system").get_data(as_text=True)
        self.assertIn("Ports réseau", html)
        self.assertIn(port, html)

    def test_ports_follow_enabled_features(self):
        from loco import system
        with loco.app.test_request_context():
            with mock.patch("loco.features.disabled", return_value=set()):
                ports = {p["port"] for p in system.firewall_ports()}
            self.assertIn(str(config.ACNG_PORT), ports)              # APT actif → cache 3142
            self.assertIn("80, 443", ports)                          # Windows Update actif
            with mock.patch("loco.features.disabled", return_value={"apt", "wu", "wu_proxy"}):
                after = system.firewall_ports()
            ports = {p["port"] for p in after}
            self.assertNotIn(str(config.ACNG_PORT), ports)           # APT coupé → plus de 3142
            self.assertNotIn("80, 443", ports)                       # Windows Update coupé
            self.assertTrue(any(p["always"] for p in after))         # HTTPS reste


class TestCertificateRoutes(unittest.TestCase):
    """Système → certificat : renouveler (autorité loco) et importer un certificat pour un nom DNS (SNI)."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        for name in ("running_job", "agent_running", "request"):
            p = mock.patch.object(loco.system.locoadmin, name)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.running_job.return_value = None
        self.agent_running.return_value = True

    def pem(self, names, other_key=False):
        d = tempfile.mkdtemp()
        crt, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
                        "-subj", "/CN=x", "-addext", "subjectAltName=" + ",".join("DNS:" + n for n in names),
                        "-keyout", key, "-out", crt], check=True, capture_output=True)
        if other_key:
            subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key],
                           check=True, capture_output=True)
        with open(crt, "rb") as c, open(key, "rb") as k:
            return c.read(), k.read()

    def upload(self, crt, key):
        return self.admin.c.post("/system/certificate/import", content_type="multipart/form-data", data={
            "csrf": self.admin.token("/system"), "certificate": (io.BytesIO(crt), "loco.crt"), "key": (io.BytesIO(key), "loco.key")})

    def test_import_is_checked_then_handed_to_the_agent(self):
        r = self.upload(*self.pem(["loco.example.org"]))
        self.assertEqual(r.status_code, 302)
        action, params = self.request.call_args[0][:2]
        self.assertEqual(action, "import-cert")
        spool = loco.system.TLS_SPOOL
        self.assertTrue(os.path.isfile(os.path.join(spool, params["upload"] + ".key")))
        self.assertEqual(oct(os.stat(os.path.join(spool, params["upload"] + ".key")).st_mode & 0o777), "0o600")

    def test_import_refused_before_reaching_the_agent(self):
        for pem in (self.pem([config.HOST]),                      # adresse des agents : ils perdraient la connexion
                    self.pem(["web.example.org"], other_key=True)):  # clé d'un autre certificat
            self.upload(*pem)
        self.request.assert_not_called()

    def test_renew_goes_to_the_agent_and_panel_shows_the_actions(self):
        page = self.admin.c.get("/system").get_data(as_text=True)
        self.assertIn("/system/certificate/renew", page)
        self.assertIn("/system/certificate/import", page)
        self.admin.post("/system/certificate/renew", {}, page="/system")
        self.assertEqual(self.request.call_args[0][0], "renew-cert")


class TestCertificateAuthority(unittest.TestCase):
    """Système → Certificat : autorité d'entreprise (DER, PKCS#7), demande, certificat (PFX), bascule ; refus clairs."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        for name in ("running_job", "agent_running", "request"):
            p = mock.patch.object(loco.system.locoadmin, name)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.running_job.return_value = None
        self.agent_running.return_value = True
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(shutil.rmtree, config.TLS_DIR, True)
        minimal = os.path.join(self.dir, "min.cnf")
        with open(minimal, "w") as f:
            f.write("[req]\ndistinguished_name=dn\n[dn]\n")
        self.ca_key, self.ca_crt = os.path.join(self.dir, "ca.key"), os.path.join(self.dir, "ca.crt")
        self.ssl("req", "-x509", "-config", minimal, "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                 "-days", "30", "-subj", "/CN=Corp Root CA", "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,keyCertSign", "-keyout", self.ca_key, "-out", self.ca_crt)

    def ssl(self, *args):
        subprocess.run(["openssl", *args], check=True, capture_output=True)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def leaf(self, san):
        key, csr, crt, ext = (os.path.join(self.dir, n) for n in ("l.key", "l.csr", "l.crt", "l.ext"))
        self.ssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=loco",
                 "-keyout", key, "-out", csr)
        with open(ext, "w") as f:
            f.write(f"subjectAltName={san}\nbasicConstraints=CA:FALSE\n")
        self.ssl("x509", "-req", "-in", csr, "-CA", self.ca_crt, "-CAkey", self.ca_key, "-set_serial", "0x02",
                 "-days", "30", "-out", crt, "-extfile", ext)
        return key, crt

    def post(self, path, **files):
        data = {"csrf": self.admin.token("/system")}
        for name, value in files.items():
            data[name] = (io.BytesIO(value), name) if isinstance(value, bytes) else value
        return self.admin.c.post(path, content_type="multipart/form-data", data=data)

    def test_authority_in_der_or_pkcs7_is_accepted_and_a_server_certificate_refused(self):
        der, p7b = os.path.join(self.dir, "ca.cer"), os.path.join(self.dir, "ca.p7b")
        self.ssl("x509", "-in", self.ca_crt, "-outform", "DER", "-out", der)
        self.ssl("crl2pkcs7", "-nocrl", "-certfile", self.ca_crt, "-outform", "DER", "-out", p7b)
        for path in (der, p7b):
            self.request.reset_mock()
            self.assertEqual(self.post("/system/certificate/authority", authority=self.read(path)).status_code, 302)
            action, params = self.request.call_args[0][:2]
            self.assertEqual(action, "import-ca")
            spooled = self.read(os.path.join(loco.system.TLS_SPOOL, params["upload"] + ".pem"))
            self.assertIn(b"-----BEGIN CERTIFICATE-----", spooled)                 # remis en PEM pour l'agent root
        self.request.reset_mock()
        self.post("/system/certificate/authority", authority=self.read(self.leaf("DNS:repo.test")[1]))
        self.request.assert_not_called()                                           # certificat de serveur : refusé

    def test_request_and_its_download(self):
        self.post("/system/certificate/request", names="loco.corp.example Loco.Corp.Example")
        self.assertEqual(self.request.call_args[0][:2], ("ca-csr", {"names": "loco.corp.example"}))
        self.request.reset_mock()
        self.post("/system/certificate/request", names="pas bon;nom")
        self.request.assert_not_called()
        self.assertEqual(self.admin.c.get("/system/certificate/request.csr").status_code, 404)
        os.makedirs(config.TLS_DIR, exist_ok=True)
        with open(os.path.join(config.TLS_DIR, "enterprise.csr"), "w") as f:
            f.write("-----BEGIN CERTIFICATE REQUEST-----\nX\n-----END CERTIFICATE REQUEST-----\n")
        r = self.admin.c.get("/system/certificate/request.csr")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])

    def test_pfx_is_converted_and_checked_before_reaching_the_agent(self):
        key, crt = self.leaf(f"DNS:{config.HOST}")
        pfx = os.path.join(self.dir, "loco.pfx")
        self.ssl("pkcs12", "-export", "-inkey", key, "-in", crt, "-certfile", self.ca_crt, "-passout", "pass:s3cret", "-out", pfx)
        self.post("/system/certificate/enterprise", pfx=self.read(pfx), password="faux")
        self.request.assert_not_called()                                           # mot de passe incorrect
        self.post("/system/certificate/enterprise", pfx=self.read(pfx), password="s3cret")
        action, params = self.request.call_args[0][:2]
        self.assertEqual((action, params["key"]), ("import-enterprise-cert", "1"))
        spool = loco.system.TLS_SPOOL
        self.assertEqual(oct(os.stat(os.path.join(spool, params["upload"] + ".key")).st_mode & 0o777), "0o600")
        self.assertIn(b"PRIVATE KEY", self.read(os.path.join(spool, params["upload"] + ".key")))

    def test_enterprise_certificate_refusals(self):
        key, crt = self.leaf("DNS:autre.example")
        self.post("/system/certificate/enterprise", certificate=self.read(crt), key=self.read(key))
        self.request.assert_not_called()                                           # ne couvre pas l'adresse des agents
        key, crt = self.leaf(f"DNS:{config.HOST}")
        self.post("/system/certificate/enterprise", certificate=self.read(crt))
        self.request.assert_not_called()                                           # sans clé ni demande en attente

    @staticmethod
    def _drop_devices():
        with connect() as conn:
            conn.execute("DELETE FROM devices WHERE uid LIKE 'trust-%'")

    def test_switch_and_readiness(self):
        self.post("/system/certificate/mode", mode="enterprise")
        self.assertEqual(self.request.call_args[0][:2], ("set-tls-mode", {"mode": "enterprise"}))
        self.assertEqual(self.admin.post("/system/certificate/mode", {"mode": "autre"}, page="/system").status_code, 400)
        os.makedirs(config.TLS_DIR, exist_ok=True)
        with open(self.ca_crt) as src, open(os.path.join(config.TLS_DIR, "trust.pem"), "w") as dst:
            bundle = src.read()
            dst.write(bundle)
        sha = hashlib.sha256(bundle.encode()).hexdigest()
        with connect() as conn:
            conn.execute("DELETE FROM devices WHERE uid LIKE 'trust-%'")
            for i, trust in enumerate((sha, "", sha)):
                conn.execute("INSERT INTO devices (uid, token_hash, hostname, enrolled_at, trust_sha256) VALUES (?,?,?,?,?)",
                             (f"trust-{i}", "x", f"poste-{i}", "2026-10-28 10:00:00", trust))
        self.addCleanup(self._drop_devices)
        with loco.app.test_request_context():
            r = loco.system.trust_readiness()
        mine = [d for d in r["waiting"] if d["uid"].startswith("trust-")]
        self.assertEqual([d["hostname"] for d in mine], ["poste-1"])
        page = self.admin.c.get("/system").get_data(as_text=True)
        self.assertIn('data-tab="ca-corp"', page)
        self.assertIn("/system/certificate/authority", page)


class TestAccountCreation(unittest.TestCase):
    """Création de compte : mot de passe choisi ou généré, changement à la première connexion au choix."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        self.addCleanup(self._drop)

    @staticmethod
    def _drop():
        with connect() as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'pw-%'")

    def _create(self, **data):
        return self.admin.post("/accounts/add", {"role": "viewer", **data}, page="/accounts")

    def _row(self, name):
        with connect() as conn:
            return conn.execute("SELECT must_change FROM users WHERE username=?", (name,)).fetchone()

    def test_chosen_password_and_no_forced_change(self):
        self._create(username="pw-choisi", password="Correct-Horse-42", must_change=["0"])
        self.assertEqual(self._row("pw-choisi")[0], 0)
        page = self.admin.c.get("/accounts").get_data(as_text=True)
        self.assertIn("Correct-Horse-42", page)                          # encart « Identifiants à transmettre »
        self.assertIn("tant qu'elle ne le change pas", page)
        other = Browser()
        self.assertEqual(other.login("pw-choisi", "Correct-Horse-42").status_code, 302)        # connexion acceptée
        self.assertEqual(Browser().login("pw-choisi", "mauvais-mot-de-passe").status_code, 401)  # refusée

    def test_empty_password_is_generated_and_must_change(self):
        self._create(username="pw-genere", password="", must_change=["0", "1"])
        self.assertEqual(self._row("pw-genere")[0], 1)
        page = self.admin.c.get("/accounts").get_data(as_text=True)
        self.assertRegex(page, r'id="issued-pw"[^>]*>[a-zA-Z2-9]{5}(-[a-zA-Z2-9]{5}){3}<')

    def test_weak_password_is_refused(self):
        self._create(username="pw-faible", password="court")
        self.assertIsNone(self._row("pw-faible"))
        self._create(username="pw-nom", password="pw-nom-pw-nom-123")            # contient le nom du compte
        self.assertIsNone(self._row("pw-nom"))

    def test_scripted_call_without_the_switch_still_forces_the_change(self):
        self._create(username="pw-script")
        self.assertEqual(self._row("pw-script")[0], 1)

    def test_creation_dialog_offers_profiles_as_cards_and_keeps_a_refused_entry(self):
        page = self.admin.c.get("/accounts").get_data(as_text=True)
        self.assertIn('data-open="#new-account"', page)
        self.assertIn('class="role-card"', page)
        self.assertIn("Consultation des appareils", page)                  # description du profil Lecture seule
        self.assertNotIn("data-open-on-load", page)
        self._create(username="admin", password="")                         # identifiant déjà pris
        page = self.admin.c.get("/accounts").get_data(as_text=True)
        self.assertIn("data-open-on-load", page)                              # la fenêtre se rouvre…
        self.assertIn('value="admin"', page)                                  # … avec la saisie

    def test_form_offers_the_generator(self):
        page = self.admin.c.get("/accounts").get_data(as_text=True)
        self.assertIn('data-generate="new-password"', page)
        self.assertIn('name="must_change"', page)


class TestWindowsUpdateBlock(unittest.TestCase):
    """Gel des mises à jour Windows par poste : case décochée par défaut, consigne portée au check-in."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        with connect() as conn:
            cur = conn.execute("INSERT INTO devices (uid, token_hash, hostname, os_family, os_name, enrolled_at) "
                               "VALUES ('wu-dev', 'x', 'poste-win', 'windows', 'Windows 11', '2026-01-01 00:00:00')")
            self.dev_id = cur.lastrowid
        self.addCleanup(self._drop)

    def _drop(self):
        with connect() as conn:
            conn.execute("DELETE FROM devices WHERE uid='wu-dev'")

    def _blocked(self):
        with connect() as conn:
            return conn.execute("SELECT block_windows_update FROM devices WHERE id=?", (self.dev_id,)).fetchone()[0]

    def test_default_is_off_and_the_toggle_shows_on_the_page(self):
        self.assertEqual(self._blocked(), 0)
        page = self.admin.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True)
        self.assertIn("Bloquer les mises à jour automatiques de Windows", page)
        self.assertNotRegex(page, r'name="block"[^>]*checked')

    def test_toggle_on_then_off(self):
        self.admin.post(f"/fleet/{self.dev_id}/windows-update", {"block": "1"}, page=f"/fleet/{self.dev_id}")
        self.assertEqual(self._blocked(), 1)
        page = self.admin.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True)
        self.assertRegex(page, r'name="block"[^>]*checked')
        self.admin.post(f"/fleet/{self.dev_id}/windows-update", {}, page=f"/fleet/{self.dev_id}")   # case décochée : rien envoyé
        self.assertEqual(self._blocked(), 0)

    def test_refused_on_a_non_windows_device(self):
        with connect() as conn:
            conn.execute("UPDATE devices SET os_family='debian' WHERE id=?", (self.dev_id,))
        r = self.admin.post(f"/fleet/{self.dev_id}/windows-update", {"block": "1"}, page=f"/fleet/{self.dev_id}")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._blocked(), 0)

    def test_group_freezes_only_its_windows_members(self):
        with connect() as conn:
            linux = conn.execute("INSERT INTO devices (uid, token_hash, hostname, os_family, enrolled_at) "
                                 "VALUES ('wu-lin', 'x', 'poste-deb', 'debian', '2026-01-01 00:00:00')").lastrowid
            gid = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Atelier', '2026-01-01 00:00:00')").lastrowid
            for d in (self.dev_id, linux):
                conn.execute("INSERT INTO device_group_members (group_id, device_id) VALUES (?,?)", (gid, d))
            self.addCleanup(lambda: self._drop_group(gid, linux))
        page = self.admin.c.get("/fleet/groups").get_data(as_text=True)
        self.assertIn("Geler Windows Update sur les membres", page)
        self.admin.post(f"/fleet/groups/{gid}/windows-update", {"block": "1"}, page="/fleet/groups")
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT block_windows_update FROM devices WHERE id=?", (self.dev_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT block_windows_update FROM devices WHERE id=?", (linux,)).fetchone()[0], 0)
        page = self.admin.c.get("/fleet/groups").get_data(as_text=True)
        self.assertIn("Rendre Windows Update aux membres", page)
        self.admin.post(f"/fleet/groups/{gid}/windows-update", {"block": "0"}, page="/fleet/groups")
        self.assertEqual(self._blocked(), 0)

    @staticmethod
    def _drop_group(gid, linux):
        with connect() as conn:
            conn.execute("DELETE FROM device_groups WHERE id=?", (gid,))
            conn.execute("DELETE FROM devices WHERE id=?", (linux,))


class TestDownloadErrors(unittest.TestCase):
    """Un certificat « pas encore valide » ou « expiré » désigne l'horloge du serveur, pas le site source."""

    def test_clock_errors_point_to_the_server_time(self):
        import urllib.error
        from loco.util import explain
        with loco.app.test_request_context():
            early = explain(urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                                  "certificate is not yet valid (_ssl.c:1029)"))
            other = explain(urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                                  "unable to get local issuer certificate (_ssl.c:1029)"))
        self.assertIn("date de ce serveur", early)
        self.assertIn("NTP", early)
        self.assertEqual(other, "Certificat HTTPS invalide sur le site source.")


class TestFirewallEgress(unittest.TestCase):
    """Flux sortants vers Internet dans Système : tirés de la configuration et des fonctionnalités actives."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def _egress(self, off=frozenset(), **kw):
        from loco import system
        with loco.app.test_request_context(), \
             mock.patch("loco.features.disabled", return_value=set(off)), \
             mock.patch("loco.system._os_repositories", return_value=["http://deb.debian.org/debian", "file:/srv/x"]), \
             mock.patch("loco.system._acng_hosts", return_value=[]), \
             mock.patch.object(system.locoadmin, "source", return_value="ShaNouhAr/loco"):
            return {e["service"]: e for e in system.firewall_egress(**kw)}

    def test_lists_upstreams_of_enabled_features(self):
        rows = self._egress(time_info={"container": False, "servers": ["10.0.0.5", "fr.pool.ntp.org"]})
        ntp = rows["Synchronisation de l'heure (NTP)"]
        self.assertEqual((ntp["hosts"], ntp["ports"], ntp["proto"]), (["10.0.0.5", "fr.pool.ntp.org"], "123", "UDP"))
        self.assertEqual(rows["Mises à jour du système du serveur"]["hosts"], ["deb.debian.org"])   # file: ignoré
        self.assertIn("api.github.com", rows["Mises à jour de loco (GitHub)"]["hosts"])
        self.assertIn("deb.debian.org", rows["Cache de paquets Linux (miroirs des postes)"]["hosts"])
        wu = rows["Cache Windows Update"]
        self.assertIn("*.windowsupdate.com", wu["hosts"])
        self.assertEqual(wu["ports"], "80, 443")
        self.assertIn("cdn.winget.microsoft.com", rows["Catalogue de logiciels Windows (winget et éditeurs)"]["hosts"])

    def test_disabled_features_and_containers_drop_their_flows(self):
        rows = self._egress(off={"apt", "rpm", "flatpak", "wu", "wu_proxy", "windows_apps"},
                            time_info={"container": True, "servers": []})
        self.assertNotIn("Synchronisation de l'heure (NTP)", rows)        # heure gérée par l'hôte
        self.assertNotIn("Cache Windows Update", rows)
        self.assertNotIn("Cache de paquets Linux (miroirs des postes)", rows)
        self.assertIn("Mises à jour de loco (GitHub)", rows)               # toujours nécessaire

    def test_configured_alerts_and_offsite_backup_appear(self):
        from loco import alerts, offsite
        cfg = alerts.defaults()
        cfg["email"].update(enabled=True, host="SMTP.example.org", port=465)
        cfg["webhook"].update(enabled=True, url="https://hooks.example.org:8443/x")
        bk = offsite.defaults()
        bk.update(type="sftp")
        bk["sftp"].update(host="backup.example.org", port=2222)
        with mock.patch("loco.alerts.load", return_value=cfg), mock.patch("loco.offsite.load", return_value=bk):
            rows = self._egress()
        self.assertEqual((rows["Alertes par e-mail (SMTP)"]["hosts"], rows["Alertes par e-mail (SMTP)"]["ports"]),
                         (["smtp.example.org"], "465"))
        self.assertEqual(rows["Alertes par webhook"]["ports"], "8443")
        self.assertEqual(rows["Sauvegarde hors site (SFTP)"]["ports"], "2222")

    def test_page_shows_the_section_and_a_copyable_list(self):
        html = self.admin.c.get("/system").get_data(as_text=True)
        self.assertIn("Flux sortants", html)
        self.assertIn('class="btn sm copy"', html)


class TestServerTimeDisplay(unittest.TestCase):
    """État NTP affiché : lu dans le fichier déposé par l'agent root (le portail n'est pas privilégié) ;
    repli sur timedatectl (hôtes systemd) quand ce fichier manque."""

    def test_reads_the_agent_status_file(self):
        import json
        import tempfile
        import time as _time
        from loco import system
        data = {"at": _time.time(), "container": False, "backend": "chrony", "enabled": True, "synchronized": True,
                "source": "162.159.200.1", "stratum": 3, "offset_ms": -1.2, "servers": ["fr.pool.ntp.org", "10.0.0.5"],
                "sources": [{"address": "162.159.200.1", "state": "used", "stratum": 3, "reach": "377"},
                            {"address": "10.0.0.5", "state": "unreachable", "stratum": 0, "reach": "0"}]}
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self.addCleanup(os.remove, path)
        with mock.patch.object(system.locoadmin, "TIME_STATUS", path), \
             mock.patch("loco.system._virt", return_value=("qemu", False)), \
             mock.patch("loco.system.os.readlink", return_value="zoneinfo/Europe/Paris"):
            info = system.server_time()
        self.assertTrue(info["fresh"])
        self.assertTrue(info["ntp"] and info["synchronized"])
        self.assertEqual(info["source"], "162.159.200.1")
        self.assertEqual(info["stratum"], 3)
        self.assertEqual(info["servers"], ["fr.pool.ntp.org", "10.0.0.5"])            # plusieurs serveurs NTP
        self.assertEqual([x["state"] for x in info["sources"]], ["used", "unreachable"])
        self.assertEqual(info["timezone"], "Europe/Paris")

    def test_unresponsive_configured_server_is_reported(self):
        import tempfile
        import time as _time
        from loco import system
        data = {"at": _time.time(), "container": False, "enabled": True, "synchronized": True, "source": "213.251.128.249",
                "servers": ["192.168.100.23"], "sources": [], "only": False,
                "configured": [{"name": "192.168.100.23", "state": "unreachable"}]}
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self.addCleanup(os.remove, path)
        with mock.patch.object(system.locoadmin, "TIME_STATUS", path), \
             mock.patch("loco.system._virt", return_value=("qemu", False)):
            info = system.server_time()
        self.assertEqual(info["unused"], ["192.168.100.23"])
        data["configured"][0]["state"] = "used"
        with open(path, "w") as f:
            json.dump(data, f)
        with mock.patch.object(system.locoadmin, "TIME_STATUS", path), \
             mock.patch("loco.system._virt", return_value=("qemu", False)):
            self.assertEqual(system.server_time()["unused"], [])

    def test_stale_file_falls_back_to_timedatectl(self):
        import time as _time
        from loco import system
        stale = json.dumps({"at": _time.time() - 600, "enabled": True, "synchronized": True})
        show = mock.Mock(returncode=0, stdout="NTP=yes\nNTPSynchronized=yes\n")
        with mock.patch("loco.system.open", mock.mock_open(read_data=stale)), \
             mock.patch("loco.system.shutil.which", return_value="/usr/bin/timedatectl"), \
             mock.patch("loco.system._virt", return_value=("qemu", False)), \
             mock.patch("loco.system.subprocess.run", return_value=show), \
             mock.patch("loco.system.os.readlink", return_value="zoneinfo/UTC"):
            info = system.server_time()
        self.assertFalse(info["fresh"])                                               # fichier périmé ignoré
        self.assertTrue(info["ntp"] and info["synchronized"])                          # repli timedatectl


class TestNavigation(unittest.TestCase):
    """Navigation rangée : administration par sections, onglets contextuels, Système en onglets, menu compte."""

    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def subnav(self, path):
        html = self.admin.c.get(path).get_data(as_text=True)
        block = html.split('<nav class="tabs subnav" aria-label="Administration">', 1)
        return re.findall(r'>([^<]+)</a>', block[1].split("</nav>", 1)[0]) if len(block) == 2 else []

    def test_admin_tabs_follow_the_current_section(self):
        self.assertEqual(self.subnav("/accounts"), ["Comptes", "Profils d'accès", "Annuaires et SSO", "Serveur MCP"])
        self.assertIn("Système", self.subnav("/system"))
        self.assertNotIn("Comptes", self.subnav("/system"))
        self.assertEqual(self.subnav("/log"), ["Journal", "Alertes"])

    def test_admin_menu_has_sections_and_account_menu_groups_settings(self):
        html = self.admin.c.get("/").get_data(as_text=True)
        for title in ("Accès", "Serveur", "Suivi"):
            self.assertIn(f'<div class="nav-section-h">{title}</div>', html)
        menu = html.split('<details class="menu user-menu">', 1)[1].split("</details>", 1)[0]
        for part in ('href="/account"', 'data-open-appearance', 'name="lang" value="en"', 'action="/logout"'):
            self.assertIn(part, menu)

    def test_system_page_is_split_in_tabs(self):
        html = self.admin.c.get("/system").get_data(as_text=True)
        for tab in ("etat", "sauvegardes", "reseau"):
            self.assertIn(f'data-tab="{tab}"', html)
            self.assertIn(f'data-panel="{tab}"', html)


if __name__ == "__main__":
    unittest.main()
