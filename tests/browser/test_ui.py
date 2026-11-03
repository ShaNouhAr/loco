"""Parcours dans un vrai navigateur : vrai portail sur une base temporaire, vrai JavaScript, mêmes en-têtes de sécurité.

Ces tests couvrent ce que le client de test de Flask ne voit pas : scripts, rafraîchissement en direct, géométrie de
la page, erreurs de la console. Ils demandent Playwright et Chromium, installés seulement en CI (voir ci.yml) :
    pip install playwright && playwright install --with-deps chromium
    python3 tests/browser/test_ui.py
Sans Chromium sur la machine, LOCO_BROWSER_CDP désigne un navigateur déjà lancé et LOCO_BROWSER_PORT le port du portail,
que ce navigateur doit pouvoir joindre sur 127.0.0.1.
"""
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import unittest
from unittest import mock

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import test_portal as fixture  # noqa: E402  (crée la base temporaire et le compte admin)
from test_fleet import Agent, LINUX, device  # noqa: E402
from test_fleet_pages import add_devices  # noqa: E402
import locoadmin  # noqa: E402
from loco import alerts, app, config, dashboard, deployments, wake  # noqa: E402
from loco.db import transaction  # noqa: E402

ARTIFACTS = ROOT / "test-results" / "browser"
PORT = int(os.environ.get("LOCO_BROWSER_PORT", "0"))
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 420, "height": 860}
# Pages que le parcours ne doit ni suivre ni charger comme une page.
NOT_PAGES = re.compile(r"/(logout|lang|set-language|agent/|win/|apt/|rpm/|flatpak/|flathub/|ca\.crt|sw\.js|metrics|healthz)|\.(msi|deb|rpm|exe|tar|gz|json|ps1|sh)(\?|$)")
LONG = 30000            # ms : ce qui dépend d'un rafraîchissement en direct (10 à 15 s)
logging.disable(logging.INFO)


server = make_server("127.0.0.1", PORT, app, threaded=True)
BASE = f"http://127.0.0.1:{server.server_port}"
threading.Thread(target=server.serve_forever, daemon=True).start()


def stamp(hours=0):
    return (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def add_app(slug, name, status="ready"):
    with transaction() as conn:
        conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (slug, name, "1.0", "exe", "/S", slug + ".exe", "b" * 64, status, "", stamp()))


def commands(action="update"):
    with transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM device_commands WHERE action=?", (action,)).fetchone()[0]


class BrowserTest(unittest.TestCase):
    viewport = DESKTOP
    signed_in = True

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cdp = os.environ.get("LOCO_BROWSER_CDP")
        cls.browser = cls.playwright.chromium.connect_over_cdp(cdp) if cdp else cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        with transaction() as conn:
            for table in ("device_commands", "device_group_members", "device_groups", "devices", "win_apps"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM settings WHERE key='fleet_red_threshold'")
            conn.execute("UPDATE users SET appearance='' WHERE username='admin'")
        fixture.reset_failures()
        dashboard._state_cache.clear()
        self.problems = []
        self.context = self.browser.new_context(viewport=self.viewport, locale="fr-FR", base_url=BASE)
        self.context.set_default_timeout(10000)
        self.page = self.watch(self.context.new_page())
        if self.signed_in:
            self.sign_in(self.page)
            self.page.wait_for_url(BASE + "/")

    def tearDown(self):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=ARTIFACTS / f"{self.id().rsplit('.', 2)[-2]}.{self._testMethodName}.png", full_page=True)
        finally:
            self.context.close()
        self.assertEqual(self.problems, [], "erreurs dans la console du navigateur")

    def watch(self, page):
        page.on("pageerror", lambda error: self.problems.append(f"{page.url} : exception : {error}"))

        def console(message):
            # Une navigation interrompt les requêtes en cours : ce n'est pas une erreur de la page.
            if message.type == "error" and not re.search(r"favicon|ERR_ABORTED|ERR_NETWORK_CHANGED", message.text):
                self.problems.append(f"{page.url} : console : {message.text}")
        page.on("console", console)
        return page

    def sign_in(self, page, password=fixture.ADMIN_PW):
        page.goto("/login")
        page.fill("[name=username]", "admin")
        page.fill("[name=password]", password)
        page.click(".auth-form button.primary")

    def confirm(self, accept=True):
        dialog = self.page.locator("dialog[open]")
        expect(dialog).to_be_visible()
        dialog.locator("[data-ok]" if accept else "[data-cancel]").click()

    def overflow(self):
        return self.page.evaluate("document.documentElement.scrollWidth - innerWidth")


class TestSignIn(BrowserTest):
    signed_in = False

    def test_wrong_then_right_password(self):
        self.sign_in(self.page, "mauvais-mot-de-passe")
        expect(self.page).to_have_url(re.compile(r"/login"))
        self.problems[:] = [p for p in self.problems if "401" not in p]      # refus attendu du serveur
        self.sign_in(self.page)
        expect(self.page.locator("h1")).to_have_text("Vue d'ensemble")


class EveryPage(BrowserTest):
    """Toutes les pages atteignables depuis les menus : aucune erreur, aucun ascenseur horizontal."""

    def crawl(self):
        ids = add_devices(3, prefix="poste", updates=4, inventory='{"updates": [{"name": "curl", "version": "1", "security": true}]}')
        add_app("7-zip", "7-Zip")
        add_app("gimp", "GIMP", "error")
        seen, queue, broken, wide = set(), ["/", f"/fleet/{ids[0]}", "/windows-apps/software/7-zip", "/account"], [], []
        request = self.context.request
        while queue and len(seen) < 150:
            path = queue.pop(0)
            if path in seen or NOT_PAGES.search(path):
                continue
            seen.add(path)
            head = request.get(path, max_redirects=0)
            if head.status in (301, 302) or "text/html" not in head.headers.get("content-type", "") or "content-disposition" in head.headers:
                continue
            response = self.page.goto(path, wait_until="load")
            if response.status >= 400:
                broken.append(f"{path} : {response.status}")
                continue
            if self.overflow() > 1:
                wide.append(f"{path} : {self.overflow()} px")
            for href in self.page.eval_on_selector_all(
                    "nav a[href], .nav a[href], .nav-panel a[href], .subnav a[href]", "links => links.map(a => a.getAttribute('href'))"):
                target = re.split(r"[?#]", href)[0]         # une page par adresse : ses filtres ne changent pas le gabarit
                if target.startswith("/") and target not in seen:
                    queue.append(target)
        print(f"{len(seen)} adresses visitées : {' '.join(sorted(seen))}", file=sys.stderr)
        missing = {"/", "/fleet", "/fleet/updates", "/fleet/settings", "/system", "/system/ha", "/windows-apps", "/windows-update", "/linux", "/account"} - seen
        self.assertEqual(missing, set(), "le parcours n'a pas atteint ces pages")
        self.assertEqual(broken, [])
        self.assertEqual(wide, [], "ascenseur horizontal")


class TestEveryPageDesktop(EveryPage):
    def test_every_page(self):
        self.crawl()


class TestEveryPagePhone(EveryPage):
    viewport = PHONE

    def test_every_page(self):
        self.crawl()


class TestFleet(BrowserTest):
    def setUp(self):
        super().setUp()
        add_devices(4, prefix="urgent", updates=40)
        add_devices(3, prefix="eteint", last_seen=stamp(3))
        add_devices(120, prefix="pc")

    def next_page(self):
        self.page.locator(".fleet-pager a", has_text="Suivante").click()

    def test_pages_keep_the_most_urgent_first_and_the_filters(self):
        self.page.goto("/fleet")
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(50)
        expect(self.page.locator(".host").first).to_have_text("urgent-000")
        expect(self.page.locator(".fleet-pager")).to_contain_text("Page 1 sur 3 · 127 postes")
        self.next_page()
        expect(self.page).to_have_url(re.compile(r"page=2"))
        self.page.locator(".chip", has_text="Windows").click()
        expect(self.page).not_to_have_url(re.compile(r"page="))

    def test_search_finds_a_machine_on_another_page_and_keeps_the_cursor(self):
        self.page.goto("/fleet")
        expect(self.page.locator(".host", has_text="pc-119")).to_have_count(0)
        box = self.page.locator("[name=q]")
        box.click()
        self.page.keyboard.type("PC-11", delay=30)
        expect(self.page).to_have_url(re.compile(r"q=PC-11"))
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(10)
        expect(box).to_be_focused()
        self.page.keyboard.type("9", delay=30)
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(1)
        expect(self.page.locator(".host")).to_have_text("pc-119")
        self.assertEqual(box.evaluate("e => [e.selectionStart, e.value.length]"), [6, 6])

    def test_no_result_offers_to_clear(self):
        self.page.goto("/fleet?q=introuvable")
        self.page.get_by_role("link", name="Effacer la recherche").click()
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(50)

    def test_selection_follows_pages_and_only_what_is_selected_is_updated(self):
        self.page.goto("/fleet")
        boxes = self.page.locator("[data-select]")
        bar = self.page.locator("[data-select-bar]")
        expect(bar).to_be_hidden()
        boxes.nth(0).check()
        boxes.nth(1).check()
        expect(bar).to_contain_text("Sélection : 2")
        self.next_page()
        expect(self.page).to_have_url(re.compile(r"page=2"))
        expect(bar).to_contain_text("dont 2 hors de cette page")
        boxes.nth(0).check()
        expect(bar).to_contain_text("Sélection : 3")
        self.page.locator("[data-select-submit]:not([data-no-reboot])").click()
        self.confirm(accept=False)
        self.assertEqual(commands(), 0)
        self.page.locator("[data-select-submit]:not([data-no-reboot])").click()
        self.confirm()
        expect(bar).to_be_hidden(timeout=LONG)
        self.assertEqual(commands(), 3)
        self.assertIn(self.page.evaluate("sessionStorage.getItem('loco-fleet-selection')"), (None, "[]"))

    def test_select_all_only_covers_the_page(self):
        self.page.goto("/fleet")
        self.page.locator("[data-select-all]").check()
        expect(self.page.locator("[data-select-bar]")).to_contain_text("Sélection : 50")
        self.next_page()
        expect(self.page).to_have_url(re.compile(r"page=2"))
        expect(self.page.locator("[data-select-all]")).not_to_be_checked()
        self.page.locator("[data-select-clear]").click()
        expect(self.page.locator("[data-select-bar]")).to_be_hidden()

    def test_offline_filter_and_second_click_removes_it(self):
        self.page.goto("/fleet")
        chip = self.page.locator(".chip", has_text="Hors ligne")
        chip.click()
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(3)
        self.page.locator(".chip", has_text="Hors ligne").click()
        expect(self.page).not_to_have_url(re.compile(r"voir="))

    def test_update_all_says_what_it_really_does(self):
        self.page.goto("/fleet?os=linux")
        button = self.page.locator('[data-open="#update-all"]')
        expect(button).to_contain_text("(4)")
        button.click()
        dialog = self.page.locator("#update-all")
        expect(dialog).to_contain_text("Mettre à jour 4 postes ?")
        dialog.locator(".btn.primary").click()
        expect(self.page).to_have_url(re.compile(r"/fleet/updates"))
        self.assertEqual(commands(), 4)


class TestCatalog(BrowserTest):
    def setUp(self):
        super().setUp()
        add_app("7-zip", "7-Zip")
        add_app("vlc", "VLC")
        add_app("gimp", "GIMP", "error")

    def bar(self):
        return self.page.evaluate("(() => { const r = document.querySelector('.selbar').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; })()")

    def test_selection_bar_stays_on_the_edge_of_the_screen_on_a_photo_background(self):
        self.page.goto("/windows-apps")
        self.assertIsNotNone(self.page.get_attribute("html", "data-bg"), "ce test vise le fond photo, qui est le défaut")
        top, _bottom, height = self.bar()
        self.assertGreaterEqual(top, height - 1, "la barre dépasse alors que rien n'est sélectionné")
        self.page.locator('input[data-ready="1"], input[data-ready]').first.check()
        expect(self.page.locator(".selbar")).to_have_class(re.compile(r"\bon\b"))
        self.page.wait_for_timeout(400)
        _top, bottom, height = self.bar()
        self.assertLessEqual(abs(bottom - height), 1, "la barre n'est pas calée sur le bas de l'écran")

    def test_removing_from_the_selection_asks_first_and_includes_failed_software(self):
        self.page.goto("/windows-apps")
        boxes = self.page.locator("tbody input[type=checkbox]")
        for i in range(boxes.count()):
            boxes.nth(i).check()
        remove = self.page.locator("[data-remove-selected] button")
        remove.click()
        expect(self.page.locator("dialog[open] [data-ok]")).to_have_class(re.compile(r"\bdanger\b"))
        self.confirm(accept=False)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM win_apps").fetchone()[0], 3)
        remove.click()
        self.confirm()
        expect(self.page.locator("tbody a", has_text="GIMP")).to_have_count(0, timeout=LONG)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM win_apps").fetchone()[0], 0)


class TestOverview(BrowserTest):
    def test_a_problem_appears_on_its_own_and_leaves_on_its_own(self):
        add_devices(1, prefix="poste")
        firing = []
        with mock.patch.object(alerts, "evaluate", lambda *_a, **_k: list(firing)), mock.patch.object(dashboard, "STATE_TTL", 0):
            self.page.goto("/")
            self.page.evaluate("window.notReloaded = true")
            expect(self.page.locator(".state-ok")).to_contain_text("Aucun point à traiter")
            firing.append({"key": "service_down", "severity": "critical", "subject": "", "body": "Services arrêtés : nginx."})
            expect(self.page.locator(".attention-row .st.err")).to_have_text("Services arrêtés : nginx.", timeout=LONG)
            self.assertIsNone(self.page.evaluate("window.notReloaded || null"), "la page devait s'être rechargée seule")
            firing.clear()
            expect(self.page.locator(".state-ok")).to_be_visible(timeout=LONG)

    def test_network_curve_survives_a_reload(self):
        self.page.goto("/")
        curve = self.page.locator("[data-net-dir=rx] .line")
        expect(curve).to_have_attribute("d", re.compile("L"), timeout=LONG)
        self.page.route("**/api/network", lambda route: route.abort())      # plus aucune nouvelle mesure après le rechargement
        self.page.reload()
        expect(curve).to_have_attribute("d", re.compile("L"))
        self.problems[:] = [p for p in self.problems if "api/network" not in p and "ERR_FAILED" not in p]

    def test_fleet_states_lead_to_the_filtered_list(self):
        add_devices(2, prefix="eteint", last_seen=stamp(3))
        with mock.patch.object(alerts, "evaluate", return_value=[]):
            self.page.goto("/")
        self.page.locator(".fleet-facts a", has_text="Hors ligne").click()
        expect(self.page).to_have_url(re.compile(r"voir=hors-ligne"))
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(2)


class TestUpdatesLive(BrowserTest):
    def progress(self, *items):
        states = [state for _name, state in items]
        return {"step": "updates", "phase": "install", "app": items[-1][0], "percent": 50,
                "items": [{"name": name, "state": state} for name, state in items],
                "counts": {"ok": states.count("ok"), "failed": states.count("failed"), "running": states.count("running"), "total": len(items)}}

    def test_packages_appear_one_by_one_without_reloading_then_the_failure_is_shown(self):
        agent = Agent(LINUX, "192.168.43.60")
        agent.enroll()
        with transaction() as conn:
            cmd = conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, sent_at, created_by) VALUES (?,?,?,?,?,?)",
                               (device(agent.uid)["id"], "update", "running", stamp(), stamp(), "admin")).lastrowid
        send = lambda state, *items: agent.post("/agent/api/result", {"id": cmd, "state": state, "output": "", "exit_code": None if state == "running" else 1,
                                                                       "progress": self.progress(*items)}, agent.token)
        # La page se recharge exprès toutes les 5 min (signature, pour les « il y a… ») : ne pas tomber sur ce créneau.
        left = 300 - time.time() % 300
        if left < 45:
            time.sleep(left + 1)
        self.page.goto("/fleet/updates")
        self.page.evaluate("window.sameDocument = true")
        send("running", ("libc6", "running"))
        expect(self.page.locator("[data-pkg]")).to_contain_text("libc6", timeout=LONG)
        send("running", ("libc6", "ok"), ("nginx", "running"))
        expect(self.page.locator("[data-pkg]")).to_contain_text("nginx", timeout=LONG)
        self.assertTrue(self.page.evaluate("window.sameDocument === true"), "l'avancement devait arriver sans recharger la page")
        send("failed", ("libc6", "ok"), ("nginx", "failed"))
        expect(self.page.locator(".upd-row, [data-updates-feed]").first).to_contain_text("nginx", timeout=LONG)
        expect(self.page.locator("[data-updates-feed]")).to_contain_text(re.compile(r"[ÉE]chec|échou"), timeout=LONG)


class TestPortPanel(BrowserTest):
    JOB = {"id": "20260101-000000-abcdef", "action": "set-https-port", "params": {"port": "9443"}, "user": "admin", "created": "2026-01-01 00:00:00"}

    def test_change_is_followed_until_it_ends_and_never_silently(self):
        jobs, conf = [], {"HTTPS_PORT": "8443", "HTTPS_OLD_PORTS": "", "PORTAL_PORT": "8088"}

        def asked(_action, params, user=""):
            self.assertEqual(params, {"port": "9443"})
            threading.Timer(0.5, lambda: jobs.append(dict(self.JOB, state="running"))).start()
        with mock.patch.object(locoadmin, "jobs", lambda limit=20: list(jobs)), mock.patch.object(locoadmin, "read_conf", lambda path=None: dict(conf)), \
                mock.patch.object(locoadmin, "running_job", lambda: None), mock.patch.object(locoadmin, "agent_running", lambda: True), \
                mock.patch.object(locoadmin, "ha_configured", lambda: False), mock.patch.object(locoadmin, "request", asked), \
                mock.patch.object(config, "HTTPS_URL", BASE):
            self.page.goto("/system#reseau")
            form = self.page.locator(".port-form")
            form.locator("[name=port]").fill("9443")
            form.locator("button").click()
            self.confirm()
            note = self.page.locator("[data-operations]")
            expect(note).to_contain_text("Changement en cours")
            jobs[:] = [dict(self.JOB, state="success")]
            conf.update(HTTPS_OLD_PORTS="8443")
            expect(self.page.locator(".note.warn")).to_contain_text("Le port 8443 reste ouvert", timeout=LONG)
            expect(self.page.locator("[data-operations]")).to_have_count(0)


class TestPortalPanel(BrowserTest):
    def test_change_is_followed_and_the_new_address_is_shown(self):
        name, jobs = "https://loco.lab.local:18083", []
        conf_path = os.path.join(config.ETC, "loco.conf")
        job = dict(TestPortPanel.JOB, action="set-portal-url", params={"url": name, "extend": "1"})

        def asked(_action, params, user=""):
            self.assertEqual(params, {"url": name, "extend": "1"})
            threading.Timer(0.5, lambda: jobs.append(dict(job, state="running"))).start()
        self.addCleanup(lambda: os.path.exists(conf_path) and os.unlink(conf_path))
        with mock.patch.object(locoadmin, "jobs", lambda limit=20: list(jobs)), mock.patch.object(locoadmin, "running_job", lambda: None), \
                mock.patch.object(locoadmin, "agent_running", lambda: True), mock.patch.object(locoadmin, "ha_configured", lambda: False), \
                mock.patch.object(locoadmin, "request", asked), mock.patch.object(config, "HTTPS_URL", BASE):
            self.page.goto("/system#reseau")
            panel = self.page.locator("#adresse-portail")
            expect(panel).to_contain_text(BASE)
            form = panel.locator(".portal-form")
            form.locator("[name=url]").fill(name)
            form.locator("[name=extend]").check()
            form.locator("button").click()
            expect(panel.locator("[data-operations]")).to_contain_text("Changement en cours")
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write(f'PORTAL_URL="{name}"\n')
            jobs[:] = [dict(job, state="success")]
            expect(panel.locator(".aside")).to_have_text(name, timeout=LONG)
            expect(panel.locator("[data-operations]")).to_have_count(0)
            expect(panel.locator("[name=url]")).to_have_value(name)


class TestSsoCheck(BrowserTest):
    IDENT = "0123456789abcdef"
    STEPS = [{"key": "dns", "title": "Résolution du nom", "state": "ok", "detail": "sso.lab.local : 10.0.0.1.", "fix": "", "value": ""},
             {"key": "client", "title": "Identifiant et secret client", "state": "unsure", "detail": "Réponse inattendue : <HTTP 500>.", "fix": "Faites un essai avec Tester.", "value": ""},
             {"key": "authorize", "title": "Demande de connexion", "state": "fail", "detail": "Authentik refuse cette URL de retour.", "fix": "Déclarez cette URL :", "value": "https://loco.lab.local:8443/login/sso/0123456789abcdef/callback"}]

    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM auth_directories")
            conn.execute("INSERT INTO auth_directories (id,kind,label,config,enabled,position,created_at) VALUES (?,?,?,?,1,0,'2026-11-03 10:00')",
                         (self.IDENT, "oidc", "Authentik", json.dumps({"preset": "authentik", "issuer": "https://sso.lab.local:9443/application/o/loco/", "client_id": "c", "client_secret": ""})))
        self.addCleanup(self.forget)

    def forget(self):
        with transaction() as conn:
            conn.execute("DELETE FROM auth_directories")

    def test_the_button_shows_every_step_and_the_url_to_copy_without_widening_the_page(self):
        with mock.patch("loco.sso_check.run", return_value=self.STEPS):
            self.page.goto("/accounts/directories")
            row = self.page.locator(f"#dir-check-{self.IDENT}")
            expect(row).to_be_hidden()
            self.page.locator(f'[data-sso-check="dir-check-{self.IDENT}"]').click()
            expect(row.locator(".check-summary")).to_contain_text("1 problème à corriger")
            expect(row.locator(".check-step")).to_have_count(3)
            expect(row.locator(".check-step").nth(1)).to_contain_text("<HTTP 500>")        # échappé, pas interprété
            expect(row.locator(".check-step .st.err")).to_have_text("Échec")
            expect(row.locator("input[readonly]")).to_have_value(self.STEPS[2]["value"])
            self.assertLessEqual(self.overflow(), 0)

    def test_saving_runs_it_once_without_a_click(self):
        with mock.patch("loco.sso_check.run", return_value=self.STEPS) as run:
            self.page.goto(f"/accounts/directories?verifier={self.IDENT}")
            expect(self.page.locator(f"#dir-check-{self.IDENT} .check-summary")).to_be_visible(timeout=LONG)
            self.assertEqual(run.call_count, 1)
            self.assertNotIn("verifier", self.page.url)
            self.page.reload()
            expect(self.page.locator(f"#dir-check-{self.IDENT}")).to_be_hidden()
            self.assertEqual(run.call_count, 1)


class TestAuthentikSetup(BrowserTest):
    def test_start_page_blueprint_and_plan_render_without_widening_the_page(self):
        from loco import authentik_setup
        self.page.goto("/accounts/directories/authentik")
        self.page.fill("input[name=url]", "https://sso.lab.local:9443")
        self.page.locator("button[value=blueprint]").click()
        expect(self.page.locator("textarea[readonly]")).to_contain_text("grant_types: [authorization_code]")
        self.assertLessEqual(self.overflow(), 0)
        tls = {"status": "confirm", "ca_pem": "-----BEGIN CERTIFICATE-----", "subject": "Racine Lab", "sha": "AA:" * 31 + "AA", "self_signed": False}
        with mock.patch.object(authentik_setup, "inspect", return_value=tls):
            self.page.goto("/accounts/directories/authentik")
            self.page.fill("input[name=url]", "https://sso.lab.local:9443")
            self.page.locator("button[value=inspect]").click()
            expect(self.page.locator(".note.warn")).to_contain_text("Racine Lab")
            self.assertEqual(self.page.locator("input[name=token]").count(), 0)
            self.assertLessEqual(self.overflow(), 0)


class TestWake(BrowserTest):
    CARD = {"name": "eth0", "mac": "bc:24:11:1c:a4:02", "kind": "ethernet", "up": True, "usb": False, "wol": "on", "networks": ["192.168.50.20/24"]}

    def enroll(self, name, cards):
        agent = Agent(dict(LINUX, hostname=name, agent_version="2.134.0", cards=cards, networks=["192.168.50.20/24"]), "192.168.50.20", uid=hashlib.sha256(name.encode()).hexdigest()[:32])
        agent.enroll()
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE uid=?", (stamp(2), agent.uid))
        return device(agent.uid)

    def test_only_the_wired_machine_is_offered_then_it_is_followed(self):
        wired, wifi = self.enroll("poste-filaire", [self.CARD]), self.enroll("portable-wifi", [dict(self.CARD, kind="wifi", wol="", mac="bc:24:11:1c:a4:03")])
        with mock.patch.object(wake, "server_networks", lambda: {"192.168.50.0/24": "192.168.50.1"}), mock.patch.object(wake, "send_packet") as send:
            self.page.goto("/fleet?voir=hors-ligne")
            row = self.page.locator("#fleet-table tr", has_text="poste-filaire")
            expect(self.page.locator("#fleet-table tr", has_text="portable-wifi").get_by_role("button", name="Réveiller")).to_have_count(0)
            self.assertLessEqual(self.overflow(), 0)
            row.get_by_role("button", name="Réveiller").click()
            expect(self.page.locator("#fleet-table tr", has_text="poste-filaire")).to_contain_text("réveil en attente", timeout=LONG)
            send.assert_called_once_with("bc:24:11:1c:a4:02", "192.168.50.0/24", "192.168.50.1")
            self.page.goto(f"/fleet/{wifi['id']}#details")
            expect(self.page.locator(".wake-hint")).to_contain_text("Aucune carte Ethernet")
            expect(self.page.locator(".head, .device-head").get_by_role("button", name="Réveiller")).to_have_count(0)
            self.assertLessEqual(self.overflow(), 0)
            self.assertTrue(wired["id"])


class TestRollouts(BrowserTest):
    def finish_wave(self, number, state):
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state=?,finished_at=? WHERE id IN (SELECT command_id FROM deployment_targets WHERE wave=?)", (state, stamp(), number))
            deployments.advance_rollouts(conn)

    def test_fleet_update_in_waves_pauses_on_failure_and_waits_for_a_decision(self):
        add_devices(6, prefix="poste", updates=3)
        with transaction() as conn:
            conn.execute("DELETE FROM deployment_targets")
            conn.execute("DELETE FROM deployments")
        self.page.goto("/fleet")
        self.page.locator('[data-open="#update-all"]').click()
        dialog = self.page.locator("#update-all")
        size = dialog.locator("[name=wave_size]")
        expect(size).to_be_hidden()
        dialog.locator("input[name=mode][value=waves]").check(force=True)
        expect(size).to_be_visible()
        size.fill("2")
        dialog.locator("[name=wave_delay]").fill("0")
        self.assertEqual(self.overflow(), 0)
        dialog.locator(".btn.primary").click()
        expect(self.page).to_have_url(re.compile(r"/fleet/deployments/[0-9a-f]+"))
        panel = self.page.locator("section.panel", has=self.page.locator("h2", has_text="Vagues"))
        expect(panel).to_contain_text("Vague 1 sur 3")
        self.assertEqual(commands(), 2)
        self.finish_wave(1, "failed")
        expect(panel.locator(".note.err")).to_contain_text("Vague 1 : 2 échecs", timeout=LONG)
        self.assertEqual(commands(), 2)
        panel.get_by_role("button", name="Lancer la vague suivante").click()
        expect(self.page.locator("dialog[open]")).to_contain_text("risquent le même problème")
        self.confirm()
        expect(panel).to_contain_text("Vague 2 sur 3", timeout=LONG)
        self.assertEqual(commands(), 4)

    def test_a_prepared_deployment_offers_waves_and_starts_only_the_first(self):
        ids = add_devices(5, prefix="lin", family="debian")
        with transaction() as conn:
            devices = conn.execute("SELECT id,uid,hostname,os_family FROM devices").fetchall()
            plan = {"kind": "linux", "bulk": True, "force": False, "groups": {"software": [], "devices": []},
                    "apps": [{"name": "acme", "kind": "deb", "family": "debian", "version": "1", "arch": "all", "repositories": ["debian/trixie"]}],
                    "devices": [{"id": d["id"], "uid": d["uid"], "hostname": d["hostname"], "os_family": d["os_family"], "reason": ""} for d in devices]}
        self.assertEqual(len(ids), 5)
        catalog = {"deb:acme": {"name": "acme", "kind": "deb", "family": "debian", "version": "1", "arch": "all", "archs": ["all"], "repositories": {"debian/trixie"}}}
        self.page.goto("/fleet/deployments")
        with mock.patch.object(deployments, "_package_catalog", return_value=catalog):
            with transaction() as conn:
                user = conn.execute("SELECT id,username,pw_version FROM users WHERE username='admin'").fetchone()
            owner = self.browser_owner()
            with transaction() as conn:
                conn.execute("INSERT INTO deployments(id,title,user_id,created_by,pw_version,owner,created_at,expires,plan) VALUES (?,?,?,?,?,?,?,?,?)",
                             ("c" * 32, "Paquets Linux · acme", user["id"], user["username"], user["pw_version"], owner, stamp(), 9999999999, json.dumps(plan)))
            self.page.goto("/fleet/deployments/" + "c" * 32)
            self.page.locator("input[name=mode][value=waves]").check(force=True)
            self.page.locator("[name=wave_size]").fill("2")
            self.page.get_by_role("button", name="Confirmer le déploiement").click()
            expect(self.page.locator("section.panel", has=self.page.locator("h2", has_text="Vagues"))).to_contain_text("Vague 1 sur 3")
        self.assertEqual(commands("install-package"), 2)
        self.page.set_viewport_size(PHONE)
        self.page.reload()
        self.assertLessEqual(self.overflow(), 1, "ascenseur horizontal sur téléphone")

    def browser_owner(self):
        """Empreinte de la session du navigateur, à laquelle une préparation est liée."""
        token = self.page.locator("input[name=csrf]").first.get_attribute("value")
        return hashlib.sha256(token.encode()).hexdigest()


class TestPageTools(BrowserTest):
    def test_system_tab_opens_from_its_address(self):
        self.page.goto("/system#reseau")
        expect(self.page.locator('[data-panel="reseau"]')).to_be_visible()
        expect(self.page.locator('[data-panel="etat"]')).to_be_hidden()

    def test_palette_finds_a_machine_and_opens_it(self):
        add_devices(1, prefix="zorin-jacques")
        self.page.goto("/")
        self.page.keyboard.press("Control+k")
        box = self.page.locator("#cmdk input")
        expect(box).to_be_focused()
        box.fill("zorin")
        self.page.locator(".cmdk-list a", has_text="zorin-jacques-000").click()
        expect(self.page).to_have_url(re.compile(r"/fleet/\d+"))

    def test_syslog_form_shows_what_the_protocol_needs(self):
        self.page.goto("/admin/syslog")
        warning = self.page.locator('[data-show-for="transport:tcp udp"]')
        self.page.locator("input[name=transport][value=tls]").check(force=True)
        expect(warning).to_be_hidden()
        self.page.locator("input[name=transport][value=udp]").check(force=True)
        expect(warning).to_be_visible()

    def test_theme_choice_survives_a_reload(self):
        self.page.goto("/account#apparence")
        with self.page.expect_response(lambda r: "/account/appearance" in r.url and r.request.method == "POST") as answer:
            self.page.locator('.theme-card[data-set-theme="light"]').click()
        self.assertEqual(answer.value.status, 200)
        expect(self.page.locator("html")).to_have_attribute("data-theme", "light")
        self.page.reload()
        expect(self.page.locator("html")).to_have_attribute("data-theme", "light")


if __name__ == "__main__":
    unittest.main(verbosity=2)
