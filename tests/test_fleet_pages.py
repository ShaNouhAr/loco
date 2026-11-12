"""Mon parc paginé : pages, recherche sur tout le parc, filtres conservés, compteur de « Tout mettre à jour », rafraîchissement."""
import datetime
import html
from pathlib import Path
import re
import secrets
import urllib.parse
from unittest import mock

import test_scope
from test_fleet import FleetTest
from loco import fleet
from loco.db import transaction

ROOT = Path(__file__).resolve().parents[1]


def add_devices(count, start=0, prefix="pc", updates=0, family="windows", **extra):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ids = []
    with transaction() as conn:
        columns = {r["name"]: r for r in conn.execute("PRAGMA table_info(devices)")}
        for i in range(start, start + count):
            row = {"uid": secrets.token_hex(8), "hostname": f"{prefix}-{i:03d}", "os_family": family, "os_name": "Windows 11" if family == "windows" else "Debian GNU/Linux 13",
                   "agent_version": "2.127.0", "addresses": f"172.17.0.1 10.0.{i // 256}.{i % 256}", "ip": f"10.0.{i // 256}.{i % 256}", "last_seen": now, "last_scan": now, "state": "active",
                   "updates": updates, "security_updates": 0, "enrolled_at": now, "token_hash": secrets.token_hex(16), "inventory": '{"updates": []}'}
            row.update(extra)
            for name, column in columns.items():
                if column["notnull"] and column["dflt_value"] is None and name not in row and name != "id":
                    row[name] = ""
            keys = [k for k in row if k in columns]
            ids.append(conn.execute(f"INSERT INTO devices ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys]).lastrowid)
    return ids


def hosts(page):
    return re.findall(r'class="host nowrap">([^<]+)<', page)


class FleetPagesTest(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM device_groups")

    def get(self, query="", browser=None):
        r = (browser or self.admin).c.get("/fleet" + query)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)


class TestPages(FleetPagesTest):
    def test_no_pager_up_to_one_full_page(self):
        add_devices(fleet.PAGE_SIZE)
        page = self.get()
        self.assertEqual(len(hosts(page)), fleet.PAGE_SIZE)
        self.assertNotIn("fleet-pager", page)

    def test_pages_cover_every_machine_once(self):
        add_devices(120)
        seen = []
        for number, size in ((1, 50), (2, 50), (3, 20)):
            found = hosts(self.get(f"?page={number}"))
            self.assertEqual(len(found), size)
            seen += found
        self.assertEqual(sorted(seen), [f"pc-{i:03d}" for i in range(120)])
        self.assertIn("Page 2 sur 3 · 120 postes", self.get("?page=2"))

    def test_only_useful_links_are_shown(self):
        add_devices(120)
        self.assertNotIn(">Précédente<", self.get())
        self.assertIn(">Suivante<", self.get())
        self.assertIn(">Précédente<", self.get("?page=3"))
        self.assertNotIn(">Suivante<", self.get("?page=3"))

    def test_page_out_of_range_or_invalid_stays_usable(self):
        add_devices(60)
        self.assertIn("Page 2 sur 2", self.get("?page=99"))
        for bad in ("abc", "-3", "0", ""):
            self.assertIn("Page 1 sur 2", self.get(f"?page={bad}"))

    def test_most_urgent_first_across_pages(self):
        add_devices(60, prefix="ajour")
        add_devices(3, prefix="zz-urgent", updates=50)
        self.assertEqual(hosts(self.get())[:3], ["zz-urgent-000", "zz-urgent-001", "zz-urgent-002"])

    def test_links_keep_filters_and_filters_go_back_to_page_one(self):
        add_devices(120)
        page = self.get("?os=windows&q=pc&page=2")
        pager = page.split("fleet-pager", 1)[1].split("</nav>", 1)[0]
        for href in re.findall(r'href="([^"]+)"', pager):
            args = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query)
            self.assertEqual((args["os"], args["q"]), (["windows"], ["pc"]))
        filters = page.split('class="filter-bar"', 1)[1].split('<div class="row">', 1)[0]
        self.assertNotIn("page=", filters)
        self.assertIn("q=pc", filters)

    def test_systems_are_offered_by_family_then_by_distribution_without_version(self):
        add_devices(2, prefix="w11", os_name="Microsoft Windows 11 Professionnel (10.0.26100)")
        add_devices(1, start=10, prefix="w10", os_name="Windows 10 Pro")
        add_devices(1, start=20, prefix="srv", os_name="Windows Server 2025 Standard")
        add_devices(3, start=30, prefix="deb", family="debian", os_name="Debian GNU/Linux 13 (trixie)")
        add_devices(1, start=40, prefix="deb12", family="debian", os_name="Debian GNU/Linux 12 (bookworm)")
        add_devices(2, start=50, prefix="ubu", family="debian", os_name="Ubuntu 24.04.1 LTS")
        add_devices(1, start=60, prefix="rocky", family="rhel", os_name="Rocky Linux 9.4 (Blue Onyx)")
        add_devices(1, start=70, prefix="tel", family="android", os_name="Android 16 · CPH2581")
        page = self.get()
        menu = page.split('aria-label="Filtrer par système"', 1)[1].split("</details>", 1)[0]
        entries = re.findall(r'<a class="menu-item (sub )?\s*(on)?\s*" role="menuitem" href="([^"]+)">([^<]+)<span class="menu-count">(\d+)</span>', menu)
        self.assertEqual([(bool(sub), html.unescape(href), label, int(n)) for sub, _on, href, label, n in entries], [
            (False, "/fleet", "Tous les systèmes", 12),
            (False, "/fleet?os=windows", "Windows", 4),
            (True, "/fleet?dist=windows-10", "Windows 10", 1),
            (True, "/fleet?dist=windows-11", "Windows 11", 2),
            (True, "/fleet?dist=windows-server-2025", "Windows Server 2025", 1),
            (False, "/fleet?os=linux", "Linux", 7),
            (True, "/fleet?dist=debian", "Debian", 4),                    # 12 et 13 ensemble : la distribution, pas la version
            (True, "/fleet?dist=rocky-linux", "Rocky Linux", 1),
            (True, "/fleet?dist=ubuntu", "Ubuntu", 2),
            (False, "/fleet?os=android", "Android", 1),                   # une seule distribution, du même nom : pas de sous-ligne
        ])
        self.assertEqual(sorted(hosts(self.get("?dist=debian"))), ["deb-030", "deb-031", "deb-032", "deb12-040"])
        self.assertEqual(sorted(hosts(self.get("?dist=windows-11"))), ["w11-000", "w11-001"])
        self.assertEqual(len(hosts(self.get("?os=linux"))), 7)             # Android n'est plus compté avec Linux
        self.assertEqual(hosts(self.get("?os=android")), ["tel-070"])
        self.assertEqual(len(hosts(self.get("?dist=inconnue"))), 12)       # valeur inconnue : rien n'est filtré
        chosen = self.get("?dist=debian&statut=green")
        self.assertRegex(chosen, r'<summary class="chip on" aria-label="Filtrer par système"><span class="filter-k">Système</span>Debian <b>4</b>')
        self.assertIn('name="dist" value="debian"', chosen)                # la recherche garde la distribution
        self.assertIn('href="/fleet?dist=debian"', chosen)                 # « Tous les états » la garde aussi
        self.assertIn(">Effacer les filtres</a>", chosen)
        self.assertNotIn(">Effacer les filtres</a>", page)

    def test_without_android_the_family_is_not_offered_and_empty_ones_stay(self):
        add_devices(1, prefix="deb", family="debian", os_name="Debian GNU/Linux 13 (trixie)")
        menu = self.get().split('aria-label="Filtrer par système"', 1)[1].split("</details>", 1)[0]
        self.assertIn('href="/fleet?os=windows">Windows<span class="menu-count">0</span>', menu)
        self.assertNotIn("Android", menu)

    def test_filter_applies_before_cutting_into_pages(self):
        add_devices(60, prefix="win")
        add_devices(60, start=100, prefix="lin", family="debian")
        self.assertIn("Page 1 sur 2 · 60 postes", self.get("?os=linux"))
        self.assertTrue(all(name.startswith("lin") for name in hosts(self.get("?os=linux"))))


class TestSearch(FleetPagesTest):
    def test_finds_a_machine_on_another_page(self):
        add_devices(120)
        self.assertNotIn("pc-119", hosts(self.get()))
        self.assertEqual(hosts(self.get("?q=pc-119")), ["pc-119"])

    def test_ignores_case_and_accents_and_needs_every_word(self):
        add_devices(1, prefix="Pôle-Compta")
        add_devices(1, prefix="pole-rh")
        self.assertEqual(hosts(self.get("?q=POLE+compta")), ["Pôle-Compta-000"])
        self.assertEqual(len(hosts(self.get("?q=pole"))), 2)

    def test_matches_system_any_address_and_group(self):
        ids = add_devices(2)
        with transaction() as conn:
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Bâtiment B', '')").lastrowid
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (group, ids[1]))
        self.assertEqual(hosts(self.get("?q=batiment")), ["pc-001"])
        self.assertEqual(hosts(self.get("?q=10.0.0.1")), ["pc-001"])
        self.assertEqual(len(hosts(self.get("?q=172.17.0.1"))), 2)      # adresse déclarée, même non affichée
        self.assertEqual(len(hosts(self.get("?q=windows"))), 2)

    def test_counters_follow_the_search(self):
        add_devices(3, prefix="win")
        add_devices(2, prefix="lin", family="debian")
        page = self.get("?q=win")
        self.assertRegex(page, r"Tous <b>3</b>")
        self.assertRegex(page, r'Linux<span class="menu-count">0</span>')

    def test_no_result_explains_and_offers_to_clear_while_keeping_filters(self):
        add_devices(2)
        page = self.get("?q=introuvable&os=windows")
        self.assertIn("Aucun poste ne correspond à la recherche.", page)
        self.assertIn("Effacer la recherche", page)
        self.assertIn('name="q" value="introuvable"', page)
        self.assertNotIn('href="/fleet?os=windows&amp;q=introuvable">Effacer', page)
        self.assertIn('<a href="/fleet?os=windows">Effacer la recherche', page)

    def test_search_is_escaped_and_bounded(self):
        add_devices(1)
        page = self.get('?q="><script>alert(1)</script>')
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn('name="q" value="' + "a" * 100 + '"', self.get("?q=" + "a" * 500))

    def test_a_limited_account_cannot_find_other_machines(self):
        ids = add_devices(1, prefix="mine")
        add_devices(1, prefix="other")
        self.ids = {"mine": ids[0]}
        self.addCleanup(lambda: test_scope.ScopeTest.cleanup(self))
        role, _r = test_scope.ScopeTest.profile(self, "Technicien", {"fleet.view"}, devices=["mine"])
        tech = test_scope.ScopeTest.account(self, "tech", role)
        self.assertEqual(hosts(self.get("?q=other", tech)), [])
        self.assertEqual(hosts(self.get("?q=mine", tech)), ["mine-000"])


class TestUpdateAllCount(FleetPagesTest):
    def queued(self):
        with transaction() as conn:
            return conn.execute("SELECT COUNT(*) FROM device_commands WHERE action='update' AND state='queued'").fetchone()[0]

    def test_count_says_what_the_action_really_does_whatever_the_filter(self):
        add_devices(2, prefix="win", updates=3)
        add_devices(3, prefix="lin", family="debian", updates=3)
        for query in ("", "?os=windows", "?statut=green", "?q=win-000", "?page=1"):
            self.assertIn("Tout mettre à jour (5)", self.get(query), query)
        self.admin.post("/fleet/update-all", {}, page="/fleet")
        self.assertEqual(self.queued(), 5)

    def test_count_covers_every_page(self):
        add_devices(70, updates=3)
        self.assertIn("Tout mettre à jour (70)", self.get("?page=2"))


class TestRefresh(FleetPagesTest):
    def poll(self, query=""):
        return self.admin.c.get("/fleet" + query, headers={"X-Requested-With": "fetch"}).get_json()["signature"]

    def test_poll_only_prepares_the_rows_when_a_page_is_rendered(self):
        add_devices(60)
        with mock.patch.object(fleet, "_enrich", wraps=fleet._enrich) as enrich:
            mark = self.poll()
            enrich.assert_not_called()
            page = self.get()
            enrich.assert_called_once()
            self.assertEqual(len(enrich.call_args[0][0]), fleet.PAGE_SIZE)
        self.assertIn(f'data-signature="{mark}"', page)

    def test_change_on_another_page_is_noticed(self):
        ids = add_devices(120)
        mark = self.poll()
        target = next(i for i in ids if device_hostname(i) == "pc-119")
        with transaction() as conn:
            conn.execute("UPDATE devices SET updates=50 WHERE id=?", (target,))
        self.assertNotEqual(self.poll(), mark)


class TestBrowserScript(FleetPagesTest):
    def test_selection_and_search_scripts_are_in_place(self):
        script = (ROOT / "portal/static/app.js").read_text(encoding="utf-8")
        self.assertIn('SEL_KEY = "loco-fleet-selection"', script)
        self.assertIn("[data-search-form]", script)
        add_devices(1)
        self.assertIn("data-search-form", self.get())
        self.assertNotIn("data-filter", self.get())


def device_hostname(dev_id):
    with transaction() as conn:
        return conn.execute("SELECT hostname FROM devices WHERE id=?", (dev_id,)).fetchone()[0]
