"""Tests du portail (bibliothèque standard + Flask) : python3 -m unittest discover -s tests"""

import atexit
import io
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


if __name__ == "__main__":
    unittest.main()
