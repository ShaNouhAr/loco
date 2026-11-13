"""Parcours dans un vrai navigateur : vrai portail sur une base temporaire, vrai JavaScript, mêmes en-têtes de sécurité.

Ces tests couvrent ce que le client de test de Flask ne voit pas : scripts, rafraîchissement en direct, géométrie de
la page, erreurs de la console. Ils demandent Playwright et Chromium, installés seulement en CI (voir ci.yml) :
    pip install playwright && playwright install --with-deps chromium
    python3 tests/browser/test_ui.py
Sans Chromium sur la machine, LOCO_BROWSER_CDP désigne un navigateur déjà lancé et LOCO_BROWSER_PORT le port du portail,
que ce navigateur doit pouvoir joindre sur 127.0.0.1.
"""
import base64
import datetime
import hashlib
import json
import logging
import os
import select
import socket
import socketserver
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
from test_fleet import Agent, LINUX, WINDOWS, device  # noqa: E402
from test_fleet_pages import add_devices  # noqa: E402
import locoadmin  # noqa: E402
from loco import alerts, app, config, dashboard, deployments, relay, wake  # noqa: E402
from loco.db import transaction  # noqa: E402

ARTIFACTS = ROOT / "test-results" / "browser"
PORT = int(os.environ.get("LOCO_BROWSER_PORT", "0"))
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 420, "height": 860}
# Pages que le parcours ne doit ni suivre ni charger comme une page.
NOT_PAGES = re.compile(r"/(logout|lang|set-language|agent/|win/|apt/|rpm/|flatpak/|flathub/|ca\.crt|sw\.js|metrics|healthz)|\.(msi|deb|rpm|exe|tar|gz|json|ps1|sh)(\?|$)")
LONG = 30000            # ms : ce qui dépend d'un rafraîchissement en direct (10 à 15 s)
logging.disable(logging.INFO)


server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
sessions_relay = relay.Relay()
assert sessions_relay.start(0)


class Front(socketserver.BaseRequestHandler):
    """Le rôle de nginx devant le portail : les connexions persistantes vont au relais, tout le reste au portail."""
    def handle(self):
        head = b""
        while b"\r\n" not in head and len(head) < 8192:
            chunk = self.request.recv(8192)
            if not chunk:
                return
            head += chunk
        path = head.split(b" ", 2)[1] if head.count(b" ") >= 2 else b""
        persistent = path == b"/agent/relay" or re.match(rb"^/fleet/session/[0-9a-f]+/ws$", path)
        with socket.create_connection(("127.0.0.1", sessions_relay.port if persistent else server.server_port)) as backend:
            backend.sendall(head)
            pair = {self.request: backend, backend: self.request}
            while True:
                for ready in select.select(list(pair), [], [], 60)[0]:
                    data = ready.recv(65536)
                    if not data:
                        return
                    pair[ready].sendall(data)


class FrontServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


front = FrontServer(("127.0.0.1", PORT), Front)
threading.Thread(target=front.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{front.server_address[1]}"


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
            for table in ("update_blocks", "device_commands", "device_group_members", "device_groups", "devices", "win_apps"):
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

    def pick(self, filter_label, choice):
        """Ouvre une liste de filtre et choisit une ligne."""
        self.page.locator(f"summary[aria-label='{filter_label}']").click()
        self.page.locator("details.filter[open] .menu-item", has_text=re.compile(r"^\s*" + re.escape(choice))).first.click()

    def test_pages_keep_the_most_urgent_first_and_the_filters(self):
        self.page.goto("/fleet")
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(50)
        expect(self.page.locator(".host").first).to_have_text("urgent-000")
        expect(self.page.locator(".fleet-pager")).to_contain_text("Page 1 sur 3 · 127 postes")
        self.next_page()
        expect(self.page).to_have_url(re.compile(r"page=2"))
        self.pick("Filtrer par système", "Windows")
        expect(self.page).not_to_have_url(re.compile(r"page="))
        expect(self.page).to_have_url(re.compile(r"os=windows"))

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
        self.pick("Filtrer par situation", "Hors ligne")
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(3)
        expect(self.page.locator("summary[aria-label='Filtrer par situation']")).to_contain_text("Hors ligne")
        self.pick("Filtrer par situation", "Toutes les situations")
        expect(self.page).not_to_have_url(re.compile(r"voir="))

    def test_the_system_list_filters_by_distribution_and_closes_on_escape(self):
        add_devices(2, start=500, prefix="deb", family="debian", os_name="Debian GNU/Linux 13 (trixie)")
        add_devices(1, start=510, prefix="ubu", family="debian", os_name="Ubuntu 24.04.1 LTS")
        self.page.goto("/fleet")
        summary = self.page.locator("summary[aria-label='Filtrer par système']")
        summary.click()
        menu = self.page.locator("details.filter[open] .menu-list")
        expect(menu.locator(".menu-item.sub", has_text="Debian")).to_be_visible()
        self.assertLessEqual(self.overflow(), 0)
        self.page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
        self.pick("Filtrer par système", "Ubuntu")
        expect(self.page).to_have_url(re.compile(r"dist=ubuntu"))
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(1)
        expect(summary).to_contain_text("Ubuntu")
        self.page.get_by_role("link", name="Effacer les filtres").click()
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(50)

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


class TestRefreshInPlace(BrowserTest):
    """Mon parc suit le parc sans se recharger : seules les zones marquées [data-refresh] sont remplacées."""

    def test_a_machine_coming_back_only_changes_its_row(self):
        add_devices(3, prefix="pc")
        add_devices(1, prefix="eteint", last_seen=stamp(3))
        self.page.goto("/fleet")
        self.page.evaluate("window.notReloaded = true")
        row = self.page.locator("#fleet-table tbody tr", has_text="eteint-000")
        expect(row.locator(".device-availability")).to_have_class(re.compile(r"\berr\b"))
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE hostname='eteint-000'", (stamp(),))
        expect(row.locator(".device-availability")).to_have_class(re.compile(r"\bok\b"), timeout=LONG)
        self.assertTrue(self.page.evaluate("window.notReloaded || null"), "la liste devait suivre sans se recharger")
        expect(self.page.locator("#fleet-table tbody tr.changed")).to_have_count(1)      # elle seule est éclairée
        row.locator("td").nth(2).click()                                                 # la ligne remplacée reste cliquable
        expect(self.page).to_have_url(re.compile(r"/fleet/\d+$"))

    def test_the_update_all_window_follows_the_button(self):
        add_devices(2, prefix="retard", updates=3)
        add_devices(1, prefix="pc")
        self.page.goto("/fleet")
        self.page.evaluate("window.notReloaded = true")
        button = self.page.locator("[data-open='#update-all']")
        expect(button).to_contain_text("(2)")
        with transaction() as conn:
            conn.execute("UPDATE devices SET updates=3 WHERE hostname='pc-000'")
        expect(button).to_contain_text("(3)", timeout=LONG)
        self.assertTrue(self.page.evaluate("window.notReloaded || null"), "la liste devait suivre sans se recharger")
        button.click()
        dialog = self.page.locator("dialog#update-all")
        expect(dialog.locator("h2")).to_have_text("Mettre à jour 3 postes ?")
        expect(dialog.locator("[name=wave_size]")).to_be_hidden()
        dialog.locator("label", has_text="Par vagues").click()                           # les choix du formulaire remplacé vivent
        expect(dialog.locator("[name=wave_size]")).to_be_visible()

    def test_nothing_is_replaced_under_an_open_menu(self):
        add_devices(2, prefix="pc")
        add_devices(1, prefix="eteint", last_seen=stamp(3))
        self.page.goto("/fleet")
        self.page.evaluate("""() => { const d = document.createElement('details'); d.id = 'essai'; d.open = true;
                                      document.querySelector('[data-refresh=actions]').appendChild(d); }""")
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE hostname='eteint-000'", (stamp(),))
        self.page.wait_for_timeout(20000)                                                # un relevé au moins (toutes les 15 s)
        expect(self.page.locator("#essai")).to_have_count(1)
        expect(self.page.locator("tr", has_text="eteint-000").locator(".device-availability")).to_have_class(re.compile(r"\berr\b"))

    def test_a_first_machine_reloads_the_page(self):
        # La liste n'existe pas encore : la structure diffère, la page se recharge comme avant.
        self.page.goto("/fleet")
        self.page.evaluate("window.notReloaded = true")
        expect(self.page.locator("#fleet-table")).to_have_count(0)
        add_devices(1, prefix="premier")
        expect(self.page.locator("#fleet-table tbody tr")).to_have_count(1, timeout=LONG)
        self.assertIsNone(self.page.evaluate("window.notReloaded || null"))


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
            self.assertTrue(self.page.evaluate("window.notReloaded || null"), "la page devait suivre sans se recharger")
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
            expect(self.page.locator("#fleet-table tr", has_text="poste-filaire")).to_contain_text("réveil en cours", timeout=LONG)
            send.assert_called_once_with("bc:24:11:1c:a4:02", "192.168.50.0/24", "192.168.50.1")
            self.page.goto(f"/fleet/{wifi['id']}#details")
            expect(self.page.locator(".wake-hint")).to_contain_text("Aucune carte Ethernet")
            expect(self.page.locator(".head, .device-head").get_by_role("button", name="Réveiller")).to_have_count(0)
            self.assertLessEqual(self.overflow(), 0)
            self.assertTrue(wired["id"])


class TestRelayChannel(BrowserTest):
    """Session distante par le canal persistant : le serveur pousse, le navigateur n'interroge plus."""

    def polls(self):
        return self.page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.endsWith('/io')).length")

    def tearDown(self):
        # xterm.js mesure ses caractères avec des styles posés en ligne, que la politique de contenu refuse sans gêner
        # l'affichage (rendu DOM de ce navigateur sans carte graphique) : bruit connu de la page du terminal.
        self.problems[:] = [p for p in self.problems if "Applying inline style violates" not in p]
        super().tearDown()

    def test_the_terminal_follows_the_agent_without_polling(self):
        agent = Agent(dict(LINUX, hostname="poste-canal"), "192.168.43.79", uid=hashlib.sha256(b"poste-canal").hexdigest()[:32])
        agent.enroll()
        dev = device(agent.uid)
        self.page.goto(f"/fleet/{dev['id']}")
        self.page.locator(".device-actions form", has=self.page.locator("input[name=kind][value=terminal]")).first.locator("button").first.click()
        self.page.wait_for_url(re.compile(r"/fleet/session/"))
        sid = self.page.url.rsplit("/", 1)[1].split("?")[0]
        expect(self.page.locator("#remote-overlay")).to_be_visible()
        self.page.wait_for_timeout(1500)                                            # le canal s'ouvre, les relevés HTTPS s'arrêtent
        before = self.polls()
        self.page.wait_for_timeout(2000)
        self.assertLessEqual(self.polls() - before, 1, "le navigateur interroge encore alors que le canal est ouvert")
        # L'agent prend la session et écrit : la page l'apprend sans rien demander.
        hello = base64.b64encode(b"bonjour du poste\r\n").decode()
        agent.post("/agent/api/remote", {"sessions": [{"id": sid, "state": "active", "seq": 1, "ack": 0, "data": hello}]}, agent.token)
        expect(self.page.locator("#remote-overlay")).to_be_hidden(timeout=3000)
        expect(self.page.locator("#remote-status")).to_contain_text("Connecté")
        # Une frappe part par le canal et attend l'agent en base.
        self.page.locator("#remote-screen textarea").focus()
        self.page.keyboard.type("ls")
        deadline, typed = time.time() + 5, b""
        while time.time() < deadline and typed != b"ls":
            answer = agent.post("/agent/api/remote", {"sessions": [{"id": sid, "state": "active", "seq": 1, "ack": 0, "data": ""}]}, agent.token).get_json()
            typed = b"".join(base64.b64decode(p["data"]) for p in answer["sessions"][0]["packets"])
            time.sleep(0.1)
        self.assertEqual(typed, b"ls")
        self.assertLessEqual(self.polls() - before, 2)

    def test_without_the_relay_the_session_still_works(self):
        agent = Agent(dict(LINUX, hostname="poste-repli"), "192.168.43.80", uid=hashlib.sha256(b"poste-repli").hexdigest()[:32])
        agent.enroll()
        dev = device(agent.uid)
        self.page.route(re.compile(r"/fleet/session/[0-9a-f]+/ws$"), lambda route: route.abort())
        self.page.add_init_script("window.WebSocket = function () { throw new Error('canal refusé'); };")
        self.page.goto(f"/fleet/{dev['id']}")
        self.page.locator(".device-actions form", has=self.page.locator("input[name=kind][value=terminal]")).first.locator("button").first.click()
        self.page.wait_for_url(re.compile(r"/fleet/session/"))
        sid = self.page.url.rsplit("/", 1)[1].split("?")[0]
        hello = base64.b64encode(b"bonjour\r\n").decode()
        agent.post("/agent/api/remote", {"sessions": [{"id": sid, "state": "active", "seq": 1, "ack": 0, "data": hello}]}, agent.token)
        expect(self.page.locator("#remote-overlay")).to_be_hidden(timeout=5000)
        self.page.wait_for_timeout(1500)
        self.assertGreater(self.polls(), 5)                                         # allers-retours HTTPS, comme avant


class TestRemoteConsent(BrowserTest):
    def test_waiting_for_approval_then_a_refusal_is_explained(self):
        agent = Agent(dict(WINDOWS, hostname="poste-accord"), "192.168.43.78", uid=hashlib.sha256(b"poste-accord").hexdigest()[:32])
        agent.enroll()
        dev = device(agent.uid)
        self.page.goto(f"/fleet/{dev['id']}")
        self.page.locator(".device-actions details.menu summary").click()
        self.page.get_by_role("button", name="Bureau distant, avec l'accord de l'utilisateur").click()
        self.page.wait_for_url(re.compile(r"/fleet/session/"))
        sid = self.page.url.rsplit("/", 1)[1].split("?")[0]
        session = agent.post("/agent/api/remote", {"sessions": []}, agent.token).get_json()["sessions"][0]
        self.assertEqual(session["consent"], {"by": "admin", "assistant": False})
        agent.post("/agent/api/remote", {"sessions": [{"id": sid, "state": "active", "seq": 0, "ack": 0, "data": ""}]}, agent.token)
        expect(self.page.locator("#remote-overlay-title")).to_have_text("En attente de l'accord de l'utilisateur…", timeout=LONG)
        expect(self.page.locator("#remote-status")).to_contain_text("En attente de l'accord")
        refusal = "La personne devant le poste a refusé l'ouverture du bureau distant."
        agent.post("/agent/api/remote", {"sessions": [{"id": sid, "state": "closed", "seq": 0, "ack": 0, "data": "", "error": refusal}]}, agent.token)
        expect(self.page.locator("#remote-overlay-title")).to_have_text(refusal, timeout=LONG)
        self.problems[:] = [p for p in self.problems if refusal not in p]    # noVNC journalise cette fermeture attendue


class TestUpdateBlocks(BrowserTest):
    def test_blocking_from_the_device_then_lifting_it(self):
        agent = Agent(dict(LINUX, hostname="poste-bloque"), "192.168.43.77", uid=hashlib.sha256(b"poste-bloque").hexdigest()[:32])
        agent.enroll()
        agent.checkin({"updates": [{"name": "openssl", "version": "3.0.1", "security": True}, {"name": "curl", "version": "8.0"}], "flatpak": 0})
        dev = device(agent.uid)
        self.page.goto(f"/fleet/{dev['id']}")
        row = self.page.locator('[data-update-row="openssl"]')
        row.get_by_role("link", name="Bloquer").click()
        expect(self.page.locator('input[name="pattern"]')).to_have_value("openssl")
        self.assertLessEqual(self.overflow(), 0)
        expect(self.page.locator('[data-show-for="family:windows"]')).to_be_hidden()
        self.page.locator('input[name="reason"]').fill("casse nginx")
        self.page.locator("#bloquer button.primary").click()
        expect(self.page.locator("table")).to_contain_text("openssl")
        expect(self.page.locator("table")).to_contain_text("casse nginx")
        expect(self.page.locator("table")).to_contain_text("1 poste")
        self.page.goto(f"/fleet/{dev['id']}")
        row = self.page.locator('[data-update-row="openssl"]')
        expect(row).to_contain_text("bloquée")
        expect(row.locator("input[type=checkbox]")).to_have_count(0)
        self.assertEqual(agent.checkin().get_json()["update_blocks"], {"patterns": ["openssl"]})
        self.page.goto("/fleet/blocks")
        self.page.get_by_role("button", name="Lever").click()
        self.confirm()
        expect(self.page.locator(".panel", has_text="Blocages en place")).to_contain_text("Aucune mise à jour bloquée")
        self.assertEqual(agent.checkin().get_json()["update_blocks"], {"patterns": []})


class TestWakeSetting(BrowserTest):
    def test_enabling_asks_for_confirmation_then_the_page_says_it_is_waiting(self):
        card = dict(TestWake.CARD, wol="off")
        agent = Agent(dict(LINUX, hostname="poste-reglage", agent_version="2.135.0", cards=[card], networks=["192.168.50.20/24"]), "192.168.50.20",
                      uid=hashlib.sha256(b"poste-reglage").hexdigest()[:32])
        agent.enroll()
        dev = device(agent.uid)
        self.page.goto(f"/fleet/{dev['id']}#details")
        self.page.locator('[data-tab="details"]').click()
        expect(self.page.locator(".wake-hint")).to_contain_text("Activer le réveil")
        self.page.locator(".wake-policy").get_by_role("button", name="Activer le réveil").click()
        self.confirm()
        self.page.locator('[data-tab="details"]').click()
        expect(self.page.locator(".wake-hint")).to_contain_text("appliquée à son prochain contact", timeout=LONG)
        self.assertEqual(agent.checkin().get_json()["wake"], {"enabled": True})
        self.page.goto("/fleet")
        self.page.locator("#fleet-table tr", has_text="poste-reglage").locator("input[type=checkbox]").first.check()
        expect(self.page.locator("[data-select-bar]").get_by_role("button", name="Activer le réveil")).to_be_visible()
        self.assertLessEqual(self.overflow(), 0)


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

    def test_look_tried_in_the_dialog_is_applied_to_the_login_screen(self):
        self.addCleanup(self.forget_appearance_policy)
        self.page.goto("/fleet")
        self.page.locator("[data-open-appearance]").first.evaluate("el => el.click()")
        button = self.page.locator("[data-apply-login]")
        expect(button).to_be_disabled()                                 # le compte a l'apparence de l'écran de connexion
        expect(self.page.locator('[data-login-state="same"]')).to_be_visible()
        self.page.locator('.ap-preset[data-theme="sapin"]').click()
        expect(button).to_be_enabled()
        with self.page.expect_response(lambda r: "/system/appearance/login" in r.url) as answer:
            button.click()
        self.assertEqual(answer.value.status, 200)
        expect(button).to_be_disabled()
        visitor = self.browser.new_context(viewport=self.viewport, locale="fr-FR", base_url=BASE)
        self.addCleanup(visitor.close)
        page = visitor.new_page()
        page.goto("/login")
        expect(page.locator("html")).to_have_attribute("data-theme", "sapin")
        expect(page.locator("html")).to_have_attribute("data-bg", "w:foret")

    def test_login_screen_fields_appear_only_for_its_own_look(self):
        self.page.goto("/system/appearance")
        fields = self.page.locator('[data-show-for="login_mode:custom"]')
        expect(fields).to_be_hidden()
        self.page.locator("input[name=login_mode][value=custom]").check(force=True)
        expect(fields).to_be_visible()

    def forget_appearance_policy(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key='appearance_policy'")


class AndroidAppsPages(BrowserTest):
    """Pages des applications Android (fonctionnalité expérimentale) : barre de navigation avec l'entrée de plus,
    pages sans ascenseur horizontal ni erreur, sur ordinateur et téléphone. Index local fabriqué : pas d'Internet."""

    def setUp(self):
        super().setUp()
        from loco import android_apps, features
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["android"]'))
            conn.execute("DELETE FROM android_apps")
            conn.execute("UPDATE android_sources SET enabled=0")
            source = conn.execute("INSERT INTO android_sources (name, url, fingerprint, position, added_at, apps, synced_at, checked_at) "
                                  "VALUES ('Dépôt de démonstration', 'https://demo.test/fdroid/repo', ?, 40, ?, 1, ?, ?)",
                                  ("c" * 64, stamp(), stamp()[:16], stamp()[:16])).lastrowid
            conn.execute("INSERT INTO android_apps (package, store, source_id, name, added_at) VALUES "
                         "('org.example.notes', 'fdroid', ?, 'Carnet de notes très utile au quotidien', ?), "
                         "('com.example.game', 'play', NULL, 'Jeu', ?)", (source, stamp(), stamp()))
        version = {"added": 1, "file": {"name": "/org.example.notes_3.apk", "sha256": "d" * 64, "size": 123456},
                   "manifest": {"versionName": "1.3.0-beta-très-longue-version-123456", "versionCode": 3,
                                "usesSdk": {"minSdkVersion": 26, "targetSdkVersion": 34}, "signer": {"sha256": ["e" * 64]}}}
        row = android_apps.project("org.example.notes", {"metadata": {"name": {"en-US": "Carnet de notes très utile au quotidien"},
                                   "summary": {"en-US": "Prendre des notes"}, "categories": ["Writing"], "description": {"en-US": "Texte. " * 400}},
                                   "versions": {"a": version}})
        with android_apps._index() as idx:
            idx.execute("DELETE FROM apps")
            idx.execute("DELETE FROM sources")
            idx.execute("INSERT INTO apps (source_id, " + ", ".join(android_apps.COLUMNS) + ") VALUES (?" + ", ?" * len(android_apps.COLUMNS) + ")",
                        (source, *(row[c] for c in android_apps.COLUMNS)))
            idx.execute("INSERT INTO sources (source_id, url, fingerprint, timestamp) VALUES (?, 'https://demo.test/fdroid/repo', ?, 1)",
                        (source, "c" * 64))
        self.source = source
        self.addCleanup(self.restore)

    def restore(self):
        from loco import features
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
            conn.execute("DELETE FROM android_apps")
            conn.execute("DELETE FROM android_sources WHERE known=''")
            conn.execute("UPDATE android_sources SET enabled=1")

    def check_pages(self):
        for path in ("/android/apps", "/android/store?category=all", f"/android/store/{self.source}/org.example.notes",
                     "/android/sources", "/fleet/deployments/new-android"):
            self.page.goto(path)
            self.assertLessEqual(self.overflow(), 0, path)
            bar = self.page.evaluate("(() => { const b = document.querySelector('.top-in'); return b.scrollWidth - b.clientWidth })()")
            self.assertLessEqual(bar, 0, f"{path} : la barre du haut déborde")


class TestAndroidAppsDesktop(AndroidAppsPages):
    def test_pages_fit_on_a_computer(self):
        self.check_pages()
        self.page.goto("/android/apps")
        expect(self.page.locator(".nav a.on")).to_have_text("Android")
        self.page.locator("[data-open='#add-play']").click()
        expect(self.page.locator("#add-play")).to_be_visible()


class TestAndroidAppsPhone(AndroidAppsPages):
    viewport = PHONE

    def test_pages_fit_on_a_phone(self):
        self.check_pages()


class HomeAssistantPages(BrowserTest):
    """Home Assistant (fonctionnalité facultative), face à un faux Home Assistant : faux client ssh et fausse commande
    `ha` (tests/fake_homeassistant.py). Android est activé aussi : c'est la barre de navigation la plus chargée."""

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        import fake_homeassistant
        from loco import features, homeassistant
        self.fake, self.hass = fake_homeassistant, homeassistant
        self.folder = tempfile.mkdtemp(prefix="loco-hass-ui-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        ssh = fake_homeassistant.install(self.folder)
        for patch in (mock.patch("loco.homeassistant.ssh_binary", return_value=ssh), mock.patch.object(homeassistant, "STEPS", (0.1, 0.1))):
            patch.start()
            self.addCleanup(patch.stop)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["homeassistant"]'))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["android"]'))
            conn.execute("DELETE FROM homeassistant_hosts")
            conn.execute("DELETE FROM remote_sessions")
        self.addCleanup(self.restore)

    def restore(self):
        from loco import adb, features
        with transaction() as conn:
            conn.execute("UPDATE remote_sessions SET state='closed' WHERE state!='closed'")
        deadline = time.time() + 20
        while time.time() < deadline and (self.hass._WORKERS or self.hass._REFRESH or adb._WORKERS):
            time.sleep(0.05)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (features.OPTIONAL_SETTING, features.EXPERIMENTAL_SETTING))
            conn.execute("DELETE FROM homeassistant_hosts")
            conn.execute("DELETE FROM remote_sessions")

    def tearDown(self):
        self.problems[:] = [p for p in self.problems if "Applying inline style violates" not in p]      # xterm.js, voir TestRelayChannel
        super().tearDown()

    def linked(self):
        """Instance déjà reliée (clé d'hôte confirmée), avec son premier relevé."""
        with transaction() as conn:
            host_id = conn.execute("INSERT INTO homeassistant_hosts (name, host, port, username, host_key, created_at, created_by) "
                                   "VALUES ('Maison', '192.168.43.80', 22, 'root', ?, ?, 'admin')",
                                   ("ssh-ed25519 " + self.fake.HOST_KEY, stamp())).lastrowid
        self.assertEqual(self.hass.refresh(host_id), "")
        return host_id

    def ran(self):
        try:
            return [line for line in (Path(self.folder) / "state" / "commands.log").read_text().splitlines() if "--raw-json" not in line and "--help" not in line]
        except OSError:
            return []

    def bar(self):
        return self.page.evaluate("(() => { const b = document.querySelector('.top-in'); return b.scrollWidth - b.clientWidth })()")


class TestHomeAssistantDesktop(HomeAssistantPages):
    def test_linking_shows_the_fingerprint_before_anything_else(self):
        self.page.goto("/homeassistant")
        expect(self.page.locator(".nav a.on")).to_have_attribute("aria-label", "Home Assistant")
        expect(self.page.locator("#hass-public-key")).to_contain_text("ssh-ed25519 AAAA")
        self.page.fill("[name=host]", "192.168.43.80")
        self.page.fill("[name=name]", "Maison")
        self.page.click("form[action$='/homeassistant/link'] button")
        digest = base64.b64encode(hashlib.sha256(base64.b64decode(self.fake.HOST_KEY)).digest()).decode().rstrip("=")
        expect(self.page.locator(".hass-confirm dd.mono")).to_have_text("SHA256:" + digest)
        expect(self.page.locator(".hass-host")).to_have_count(0)                     # rien n'est relié avant la confirmation
        self.page.click(".hass-confirm button.primary")
        host = self.page.locator(".hass-host")
        expect(host.locator(".metric").nth(0)).to_contain_text("2026.11.2 disponible")
        expect(host.locator(".metric").nth(2)).to_contain_text("à jour")             # le Supervisor n'a rien à proposer
        expect(host.locator(".metric").nth(2)).not_to_contain_text("disponible")
        expect(host.locator(".panel-h .pill.warn")).to_have_text("3 mises à jour")
        self.assertLessEqual(self.overflow(), 0)
        self.assertLessEqual(self.bar(), 0, "la barre du haut déborde")

    def test_an_update_is_confirmed_followed_in_place_and_not_offered_twice(self):
        self.linked()
        (Path(self.folder) / "state" / "slow").write_text("2")
        self.page.goto("/homeassistant")
        host = self.page.locator(".hass-host")
        host.locator("tr", has_text="Home Assistant Core").locator("button").click()
        dialog = self.page.locator("#hass-update")
        expect(dialog).to_be_visible()
        expect(dialog.locator("[data-fill-text=what]")).to_have_text("Maison : Home Assistant Core 2026.11.1 → 2026.11.2")
        expect(dialog.locator("[name=backup]")).to_be_checked()
        dialog.locator("button.primary").click()
        expect(self.page.locator(".flash, .toast").first).to_contain_text("Mise à jour lancée")
        self.page.evaluate("window.kept = true")
        row = host.locator(".hass-table").first.locator("tr", has_text="Home Assistant Core")
        expect(row.locator(".busy-note")).to_be_visible(timeout=LONG)                # en cours : plus de bouton sur cette ligne
        expect(row.locator("button")).to_have_count(0)
        expect(host.locator(".metric").nth(0)).to_contain_text("à jour", timeout=LONG)
        expect(host.locator(".metric").nth(0)).to_contain_text("2026.11.2")
        expect(host.locator("tr", has_text="sauvegarde avant").locator(".st.ok")).to_have_text("Réussie")
        self.assertTrue(self.page.evaluate("window.kept === true"), "la page a été rechargée au lieu d'être actualisée en place")
        self.assertEqual(self.ran(), ["core update --backup --no-progress"])

    def test_the_log_opens_on_demand(self):
        self.linked()
        self.page.goto("/homeassistant")
        self.page.locator(".hass-log > summary").click()
        expect(self.page.locator("[data-hass-log-text]")).to_contain_text("Home Assistant initialized")

    def test_the_terminal_is_a_real_shell_under_its_own_entry(self):
        self.linked()
        self.page.goto("/homeassistant")
        self.page.locator(".hass-actions form[action$='/shell'] button").first.click()
        self.page.wait_for_url(re.compile(r"/fleet/session/"))
        expect(self.page.locator("#remote-status")).to_contain_text("Connecté")
        expect(self.page.locator("#remote-status")).to_contain_text("terminal de Home Assistant")
        expect(self.page.locator(".nav a.on")).to_have_attribute("aria-label", "Home Assistant")
        expect(self.page.locator(".head a").first).to_have_attribute("href", "/homeassistant")
        self.page.locator("#remote-screen textarea").focus()
        self.page.keyboard.type("ha core restart\n")
        deadline = time.time() + 10
        while time.time() < deadline and self.ran() != ["core restart"]:
            time.sleep(0.1)
        self.assertEqual(self.ran(), ["core restart"])                               # tapé dans la page, exécuté par le shell
        self.page.click("#remote-close")
        expect(self.page.locator("#remote-status")).to_contain_text("Session fermée")


class TestHomeAssistantPhone(HomeAssistantPages):
    viewport = PHONE

    def test_the_page_fits_on_a_phone_even_with_every_entry_in_the_bar(self):
        self.linked()
        for path in ("/homeassistant", "/homeassistant?relier=1", "/"):
            self.page.goto(path)
            self.assertLessEqual(self.overflow(), 0, path)
            self.assertLessEqual(self.bar(), 0, f"{path} : la barre du haut déborde")
        self.page.goto("/homeassistant")
        self.page.locator(".hass-actions details.menu > summary").first.click()
        expect(self.page.locator(".hass-actions details.menu[open] .menu-list").first).to_be_visible()
        self.assertLessEqual(self.overflow(), 0, "menu ouvert")


if __name__ == "__main__":
    unittest.main(verbosity=2)
