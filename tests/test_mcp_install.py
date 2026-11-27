"""Installateurs MCP pour Claude Code et Codex : jeton créé au téléchargement avec les outils choisis, installateur
Windows (.cmd + PowerShell) et macOS/Linux (sh) ; le script Linux est exécuté pour de bon sur un dossier personnel
d'essai : configuration de Claude Code (~/.claude.json) et de Codex (~/.codex/config.toml) écrite, le reste gardé."""
import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from test_mcp import Client, McpTest
from loco import config, mcp, mcp_install
from loco.db import transaction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallTest(McpTest):
    def download(self, target="claude", system="linux", tools=("list_devices",), browser=None, **form):
        data = {"app": target, "system": system, "name": "Claude Code" if target == "claude" else "Codex", "expiry": "30",
                "tools": list(tools)}
        data.update(form)
        return (browser or self.admin).post("/accounts/mcp/install", data, page="/accounts/mcp")

    def token_in(self, text):
        return re.search(r"loco_mcp_[A-Za-z0-9_-]{43}", text).group(0)


class TestPage(InstallTest):
    def test_the_tiles_open_a_window_per_application_with_the_system_of_the_browser(self):
        page = self.admin.c.get("/accounts/mcp", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}).get_data(as_text=True)
        self.assertIn('class="app-tile claude" data-open="#mcp-install-claude"', page)
        self.assertIn('class="app-tile codex" data-open="#mcp-install-codex"', page)
        dialog = page[page.index('id="mcp-install-claude"'):page.index('id="mcp-install-codex"')]
        self.assertIn('<option value="windows" selected>', dialog)
        self.assertRegex(dialog, r'value="list_devices" role="switch" data-read checked')       # consultation d'office
        self.assertNotRegex(dialog, r'value="read_file" role="switch"[^>]*checked')             # jamais l'accès distant
        self.assertNotIn("claude://", page)                                                       # plus de message pré-rempli
        mac = self.admin.c.get("/accounts/mcp", headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)"}).get_data(as_text=True)
        self.assertIn('<option value="macos" selected>', mac)


class TestDownload(InstallTest):
    def test_the_installer_carries_a_new_token_limited_to_the_chosen_tools(self):
        r = self.download(tools=["list_devices", "get_device"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Content-Disposition"], 'attachment; filename="ajouter-loco-a-claude.sh"')
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        text = r.get_data(as_text=True)
        token = self.token_in(text)
        self.assertIn(f"URL='{mcp._endpoint_url()}'", text)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM mcp_tokens WHERE client_id='installateur:claude'").fetchone()
        self.assertEqual((row["name"], json.loads(row["tools"])), ("Claude Code", ["list_devices", "get_device"]))
        self.assertEqual(row["expires_at"][:10], (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d"))
        self.assertEqual(sorted(Client(token).names()), ["get_device", "list_devices"])
        self.assertIn("obtenu par l'installateur Claude", self.admin.c.get("/accounts/mcp").get_data(as_text=True).replace("&#39;", "'"))

    def test_the_file_is_named_in_the_language_of_the_account(self):
        with transaction() as conn:
            before = conn.execute("SELECT lang FROM users WHERE username='admin'").fetchone()[0]
            conn.execute("UPDATE users SET lang='en' WHERE username='admin'")

        def restore():
            with transaction() as conn:
                conn.execute("UPDATE users SET lang=? WHERE username='admin'", (before,))
        self.addCleanup(restore)
        self.assertEqual(self.download(system="windows").headers["Content-Disposition"], 'attachment; filename="add-loco-to-claude.cmd"')
        with transaction() as conn:
            conn.execute("UPDATE users SET lang='es' WHERE username='admin'")
        self.assertEqual(self.download(target="codex").headers["Content-Disposition"], 'attachment; filename="anadir-loco-a-codex.sh"')

    def test_each_system_gets_its_file(self):
        for system, name in (("windows", "ajouter-loco-a-codex.cmd"), ("macos", "ajouter-loco-a-codex.command"),
                             ("linux", "ajouter-loco-a-codex.sh")):
            r = self.download(target="codex", system=system)
            self.assertEqual(r.headers["Content-Disposition"], f'attachment; filename="{name}"', system)
        windows = self.download(system="windows").get_data()
        self.assertNotIn(b"\n", windows.replace(b"\r\n", b""))                                   # fins de ligne Windows
        text = windows.decode()
        self.assertTrue(text.startswith("@echo off\r\n"))
        self.assertEqual(text.count("#=== loco PowerShell"), 2)                                 # appel + repère
        self.assertIn("$Token = '" + self.token_in(text) + "'", text)
        self.assertIn("'Ajouter loco à Claude'", text)

    def test_only_an_account_allowed_to_create_tokens_downloads_and_forged_tools_are_refused(self):
        browser = self.operator()
        self.assertEqual(self.download(browser=browser, tools=["reboot_device"]).status_code, 400)  # outil hors de ses droits
        self.assertEqual(self.download(browser=browser).status_code, 200)
        csrf = browser.token("/accounts/mcp")
        with transaction() as conn:
            conn.execute("UPDATE access_roles SET permissions=? WHERE id='ia'", (json.dumps(["fleet.view"]),))
        r = browser.c.post("/accounts/mcp/install", data={"app": "claude", "system": "linux", "name": "x", "expiry": "30",
                                                          "tools": ["list_devices"], "csrf": csrf})
        self.assertEqual(r.status_code, 403)
        for bad in ({"app": "cursor"}, {"system": "amiga"}, {"expiry": "7"}):
            self.assertEqual(self.download(**bad).status_code, 400, bad)

    def test_apostrophes_of_translations_are_quoted_for_each_shell(self):
        self.assertEqual(mcp_install.ps("l'application"), "'l''application'")
        self.assertEqual(mcp_install.sh("l'application"), "'l'\\''application'")
        for lang in ("fr", "en", "es"):
            for system in ("linux", "macos"):
                with mock.patch("loco.i18n.locale", return_value=lang):
                    text = self.download(system=system).get_data(as_text=True)
                checked = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
                self.assertEqual(checked.returncode, 0, (lang, system, checked.stderr))
            self.assertIn({"fr": "Ajouter loco à Claude", "en": "Add loco to Claude", "es": "Añadir loco a Claude"}[lang], text)


class TestLinuxInstaller(InstallTest):
    """Le vrai script, lancé sur un dossier personnel d'essai (loco injoignable : la vérification du certificat passe)."""

    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(prefix="loco-home-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_installer(self, target):
        script = os.path.join(self.home, f"ajouter-loco-a-{target}.sh")
        with open(script, "wb") as f:
            f.write(self.download(target=target).get_data())
        env = {"HOME": self.home, "PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
        done = subprocess.run(["sh", script], input="n\n", text=True, capture_output=True, env=env, timeout=120)
        return done, script

    def test_claude_code_gets_loco_and_keeps_the_rest_of_its_configuration(self):
        with open(os.path.join(self.home, ".claude.json"), "w") as f:
            json.dump({"numStartups": 7, "mcpServers": {"autre": {"type": "stdio", "command": "x"}},
                       "projects": {"C:/Users/a": {"x": 1}, "c:/Users/a": {"x": 2}}}, f)
        done, script = self.run_installer("claude")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("loco est ajouté à Claude Code", done.stdout)
        with open(os.path.join(self.home, ".claude.json")) as f:
            data = json.load(f)
        self.assertEqual(data["numStartups"], 7)
        self.assertEqual(data["projects"], {"C:/Users/a": {"x": 1}, "c:/Users/a": {"x": 2}})       # clés au cas près gardées
        self.assertEqual(data["mcpServers"]["autre"], {"type": "stdio", "command": "x"})
        loco = data["mcpServers"]["loco"]
        self.assertEqual((loco["type"], loco["url"]), ("http", mcp._endpoint_url()))
        token = loco["headers"]["Authorization"].removeprefix("Bearer ")
        self.assertEqual(Client(token).names(), ["list_devices"])
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude.json.avant-loco")))
        self.assertFalse(os.path.exists(script))                                                # il s'efface
        self.assertNotIn(token, done.stdout + done.stderr)                                      # jamais affiché

    def test_codex_gets_loco_and_an_older_loco_section_is_replaced(self):
        os.makedirs(os.path.join(self.home, ".codex"))
        with open(os.path.join(self.home, ".codex", "config.toml"), "w") as f:
            f.write('model = "gpt-5"\n\n[mcp_servers.loco]\nurl = "https://ancien.example/mcp"\n\n[mcp_servers.loco.env]\nX = "1"\n\n'
                    '[mcp_servers.autre]\ncommand = "autre"\n')
        done, script = self.run_installer("codex")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.home, ".codex", "config.toml")) as f:
            text = f.read()
        self.assertNotIn("ancien.example", text)
        self.assertNotIn("[mcp_servers.loco.env]", text)
        self.assertIn('model = "gpt-5"', text)
        self.assertIn('[mcp_servers.autre]\ncommand = "autre"', text)
        self.assertEqual(text.count("[mcp_servers.loco]"), 1)
        self.assertIn(f'url = "{mcp._endpoint_url()}"', text)
        token = re.search(r'"Authorization" = "Bearer (loco_mcp_[^"]+)"', text).group(1)
        self.assertEqual(Client(token).names(), ["list_devices"])
        self.assertFalse(os.path.exists(script))

    def test_a_fresh_computer_gets_new_files(self):
        done, _script = self.run_installer("codex")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.home, ".codex", "config.toml")))
        done, _script = self.run_installer("claude")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.home, ".claude.json")) as f:
            self.assertIn("loco", json.load(f)["mcpServers"])


class TestAuthority(InstallTest):
    def test_enterprise_installer_embeds_only_the_root_that_signed_the_portal_chain(self):
        from pathlib import Path
        from test_sso_check import INTERMEDIATE, OTHER_ROOT, ROOT, issue, pem

        with tempfile.TemporaryDirectory() as folder:
            tls = Path(folder)
            leaf = issue("loco.example.test", INTERMEDIATE, sans=("loco.example.test",))[0]
            (tls / "mode").write_text("enterprise\n")
            (tls / "enterprise.crt").write_text(pem(leaf, INTERMEDIATE[0]))
            (tls / "enterprise-ca.pem").write_text(pem(OTHER_ROOT[0], ROOT[0]))
            with mock.patch.object(config, "TLS_DIR", folder), \
                    mock.patch.object(config, "portal_url", return_value="https://loco.example.test:8443"):
                installer = self.download(system="windows").get_data(as_text=True)
                self.assertEqual(mcp_install._authority_pem("https://unrelated.example.test:8443/mcp"), ("", ""))
            self.assertIn(pem(ROOT[0]).replace("\n", "\r\n"), installer)
            self.assertNotIn(pem(OTHER_ROOT[0]).replace("\n", "\r\n"), installer)
            self.assertNotIn(pem(INTERMEDIATE[0]).replace("\n", "\r\n"), installer)

    def test_the_authority_of_loco_travels_with_the_installer_when_it_signed_the_certificate(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        folder = tempfile.mkdtemp(prefix="loco-tls-")
        self.addCleanup(shutil.rmtree, folder, True)
        now = datetime.datetime.now(datetime.timezone.utc)

        def certificate(subject, issuer, key, signer, ca):
            return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=9))
                    .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True).sign(signer, hashes.SHA256()))
        ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "loco CA essai")])
        ca = certificate(ca_name, ca_name, ca_key, ca_key, True)
        server = certificate(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "loco.essai")]), ca_name, server_key, ca_key, False)
        for name, cert in (("ca.crt", ca), ("server.crt", server)):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        pem = ca.public_bytes(serialization.Encoding.PEM).decode()
        fingerprint = ca.fingerprint(hashes.SHA256()).hex().upper()
        with mock.patch.object(config, "TLS_DIR", folder):
            linux = self.download().get_data(as_text=True)
            windows = self.download(system="windows").get_data(as_text=True)
        self.assertIn(pem, linux)
        self.assertIn(":".join(fingerprint[i:i + 2] for i in range(0, 64, 2)), linux)
        self.assertIn(pem.replace("\n", "\r\n"), windows)
        self.assertIn("-----BEGIN CERTIFICATE-----", windows)
        plain = self.download().get_data(as_text=True)                                         # sans autorité loco : rien
        self.assertNotIn("BEGIN CERTIFICATE", plain)


if __name__ == "__main__":
    unittest.main()
