"""Parc accessible d'un profil : ce qu'un compte limité à des groupes et postes voit et vise, partout (Mon parc,
fiche, recherche, groupes, scripts, planifications, sessions distantes, MCP), et la délégation qui ne l'élargit pas."""
import datetime
import json
import unittest

import test_portal as fixture
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from test_mcp import Client, create
from loco import auth, permissions, remote
from loco.db import db, transaction

PW = "Tech-Password-2026"


class ScopeTest(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            for table in ("scripts", "script_runs", "schedules", "device_groups", "mcp_tokens", "remote_sessions"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM users WHERE username IN ('tech', 'chef')")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
        self.addCleanup(self.cleanup)
        self.agents = {}
        for name, ident, ip in (("b1", dict(WINDOWS, hostname="pc-bat-b-01"), "192.168.43.71"),
                                ("b2", dict(LINUX, hostname="pc-bat-b-02"), "192.168.43.72"),
                                ("srv", LINUX, "192.168.43.60"), ("compta", WINDOWS, "192.168.43.50")):
            agent = Agent(ident, ip)
            agent.enroll()
            self.agents[name] = agent
        self.ids = {name: device(a.uid)["id"] for name, a in self.agents.items()}
        with transaction() as conn:
            self.batb = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Bâtiment B', '')").lastrowid
            self.servers = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Serveurs', '')").lastrowid
            conn.executemany("INSERT INTO device_group_members VALUES (?,?)",
                             [(self.batb, self.ids["b1"]), (self.batb, self.ids["b2"]), (self.servers, self.ids["srv"])])

    def cleanup(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username IN ('tech', 'chef')")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
            conn.execute("DELETE FROM device_groups")

    def profile(self, label, grants, scope="some", groups=(), devices=(), browser=None):
        """Profil créé par l'interface ; renvoie (identifiant, réponse)."""
        r = (browser or self.admin).post("/accounts/profiles/add", {
            "label": label, "description": "", "permissions": sorted(permissions.expand(grants)), "fleet_scope": scope,
            "scope_group": [str(g) for g in groups], "scope_device": [str(self.ids[d]) for d in devices]},
            page="/accounts/profiles/new")
        role_id = r.headers.get("Location", "").rsplit("/", 1)[-1] if r.status_code == 302 else None
        return role_id, r

    def account(self, username, role_id):
        with transaction() as conn:
            auth.create_user(conn, username, PW, role_id, must_change=False)
        fixture.reset_failures()
        browser = fixture.Browser()
        browser.login(username, PW)
        return browser

    def technician(self, extra=()):
        """« tech » : bâtiment B (groupe) + le serveur web (poste) ; consulter, mettre à jour, terminal, scripts."""
        role_id, r = self.profile("Techniciens bâtiment B", {"fleet.view", "fleet.update", "fleet.bulk_update", "remote.terminal",
                                                             "scripts.run", "fleet.groups", "mcp.manage", *extra},
                                  groups=[self.batb], devices=["srv"])
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return role_id, self.account("tech", role_id)


class TestProfiles(ScopeTest):
    def test_profile_stores_and_shows_its_fleet(self):
        role_id, _browser = self.technician()
        with transaction() as conn:
            row = conn.execute("SELECT whole_fleet FROM access_roles WHERE id=?", (role_id,)).fetchone()
            groups = [r[0] for r in conn.execute("SELECT group_id FROM access_role_groups WHERE role_id=?", (role_id,))]
            devices = [r[0] for r in conn.execute("SELECT device_id FROM access_role_devices WHERE role_id=?", (role_id,))]
            logged = conn.execute("SELECT detail FROM audit WHERE action='profils.parc' ORDER BY id DESC").fetchone()
        self.assertEqual((row["whole_fleet"], groups, devices), (0, [self.batb], [self.ids["srv"]]))
        self.assertIn("groupes Bâtiment B; postes srv-web", logged["detail"])
        listing = self.admin.c.get("/accounts/profiles").get_data(as_text=True)
        self.assertIn("1 groupe · 1 poste", listing)
        self.assertIn("Tout le parc", listing)                          # profils intégrés
        editor = self.admin.c.get(f"/accounts/profiles/{role_id}").get_data(as_text=True)
        self.assertIn(f'name="scope_group" value="{self.batb}" checked', editor)

    def test_a_deleted_group_leaves_the_profile_fleet(self):
        role_id, _browser = self.technician()
        with transaction() as conn:
            conn.execute("DELETE FROM device_groups WHERE id=?", (self.batb,))
            remaining = conn.execute("SELECT COUNT(*) FROM access_role_groups WHERE role_id=?", (role_id,)).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_changing_the_fleet_signs_its_accounts_out(self):
        role_id, tech = self.technician()
        self.assertEqual(tech.c.get("/fleet").status_code, 200)
        r = self.admin.post(f"/accounts/profiles/{role_id}/edit", {
            "label": "Techniciens bâtiment B", "description": "", "fleet_scope": "some", "scope_group": [str(self.batb)],
            "permissions": sorted(permissions.expand({"fleet.view"}))}, page=f"/accounts/profiles/{role_id}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(tech.c.get("/fleet").status_code, 302)        # reconnexion demandée


class TestRestrictedAccount(ScopeTest):
    def test_fleet_pages_only_show_the_profile_fleet(self):
        _role, tech = self.technician()
        page = tech.c.get("/fleet").get_data(as_text=True)
        for name in ("pc-bat-b-01", "pc-bat-b-02", "srv-web"):
            self.assertIn(name, page)
        self.assertNotIn("pc-compta", page)
        titles = [d["title"] for d in tech.c.get("/fleet/search.json").get_json()["devices"]]
        self.assertEqual(sorted(titles), ["pc-bat-b-01", "pc-bat-b-02", "srv-web"])
        self.assertEqual(tech.c.get(f"/fleet/{self.ids['compta']}").status_code, 404)
        self.assertEqual(tech.c.get(f"/fleet/{self.ids['b1']}").status_code, 200)
        # Un poste ajouté plus tard au groupe entre dans le parc.
        with transaction() as conn:
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (self.batb, self.ids["compta"]))
        self.assertEqual(tech.c.get(f"/fleet/{self.ids['compta']}").status_code, 200)

    def test_bulk_updates_stay_in_the_fleet(self):
        _role, tech = self.technician()
        for agent in self.agents.values():
            agent.checkin({"updates": [{"name": "maj", "version": "1"}]})
        r = tech.post("/fleet/update-selected", {"ids": [str(i) for i in self.ids.values()]}, page="/fleet")
        self.assertEqual(r.get_json()["count"], 3)
        with transaction() as conn:
            queued = {r[0] for r in conn.execute("SELECT device_id FROM device_commands WHERE action='update'")}
        self.assertNotIn(self.ids["compta"], queued)
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")
        tech.post("/fleet/update-all", {}, page="/fleet")
        with transaction() as conn:
            queued = {r[0] for r in conn.execute("SELECT device_id FROM device_commands WHERE action='update'")}
        self.assertEqual(queued, {self.ids["b1"], self.ids["b2"], self.ids["srv"]})

    def test_groups_are_reduced_to_the_fleet_and_not_managed(self):
        _role, tech = self.technician()
        page = tech.c.get("/fleet/groups").get_data(as_text=True)
        self.assertIn("Bâtiment B", page)
        self.assertIn("Serveurs", page)                                  # contient le serveur web, du parc
        self.assertNotIn("Créer un groupe", page)                        # « Gérer les groupes » : tout le parc seulement
        r = tech.post("/fleet/groups/save", {"name": "Mon groupe", "members": [str(self.ids["b1"])]}, page="/fleet/groups")
        self.assertEqual(r.status_code, 403)
        with transaction() as conn:
            conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Comptabilité', '')")
        self.assertNotIn("Comptabilité", tech.c.get("/fleet/groups").get_data(as_text=True))

    def test_scripts_target_and_show_only_the_fleet(self):
        _role, tech = self.technician()
        with transaction() as conn:
            sid = conn.execute("INSERT INTO scripts (name, platform, content, created_at, updated_at) "
                               "VALUES ('Nettoyage', 'linux', 'true', '', '')").lastrowid
        # Groupe Serveurs + un poste hors parc forgé : refusé ; groupe seul : seulement ses postes du parc.
        r = tech.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": [str(self.ids["compta"])]}, page="/fleet/scripts")
        self.assertEqual(r.status_code, 404)
        with transaction() as conn:
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (self.servers, self.ids["compta"]))
        r = tech.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device_group": [str(self.servers)]}, page="/fleet/scripts")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            targets = {r[0] for r in conn.execute("SELECT device_id FROM script_run_targets")}
        self.assertEqual(targets, {self.ids["srv"]})
        # Une exécution de l'administrateur sur un poste hors parc n'existe pas pour le technicien.
        admin_run = self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": [str(self.ids["srv"]), str(self.ids["b2"])]},
                                    page="/fleet/scripts")
        other = int(admin_run.headers["Location"].rsplit("/", 1)[-1])
        self.assertEqual(tech.c.get(f"/fleet/scripts/runs/{other}").status_code, 200)
        with transaction() as conn:
            hidden = conn.execute("INSERT INTO script_runs (script_id, name, version, platform, content, sha256, created_by, created_at) "
                                  "VALUES (?, 'Nettoyage', 1, 'linux', 'true', '', 'admin', '')", (sid,)).lastrowid
            conn.execute("INSERT INTO script_run_targets (run_id, device_id, hostname) VALUES (?,?,?)",
                         (hidden, self.ids["compta"], "pc-compta"))
        self.assertEqual(tech.c.get(f"/fleet/scripts/runs/{hidden}").status_code, 404)
        self.assertNotIn(f"/fleet/scripts/runs/{hidden}\"", tech.c.get("/fleet/scripts").get_data(as_text=True))

    def test_schedules_keep_the_author_fleet(self):
        _role, tech = self.technician()
        start = datetime.date.today().isoformat()
        plan = {"action": "update", "recurrence": "daily", "at_time": "02:00", "start_date": start}
        r = tech.post("/fleet/schedules/save", dict(plan, target="all"), page="/fleet/schedules")
        self.assertEqual(r.status_code, 403)                            # « Tout le parc » : pas pour un profil limité
        self.assertNotIn('value="all"', tech.c.get("/fleet/schedules/new").get_data(as_text=True))
        with transaction() as conn:
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (self.servers, self.ids["compta"]))
        r = tech.post("/fleet/schedules/save", dict(plan, target="selection", device_group=[str(self.servers)]),
                      page="/fleet/schedules")
        self.assertEqual(r.status_code, 302)
        self.admin.post("/fleet/schedules/save", dict(plan, target="all"), page="/fleet/schedules")
        with transaction() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()
            past = (datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE schedules SET next_run=? WHERE id=?", (past, rows[0]["id"]))
        self.assertEqual(json.loads(rows[0]["targets"])["role"], _role)
        self.assertEqual(len(tech.c.get("/fleet/schedules").get_data(as_text=True).split('id="planif-')) - 1, 1)
        self.assertEqual(tech.c.get(f"/fleet/schedules/{rows[1]['id']}").status_code, 404)
        self.agents["srv"].checkin()                                    # l'occurrence due se matérialise
        with transaction() as conn:
            queued = {r[0] for r in conn.execute("SELECT device_id FROM device_commands WHERE action='update'")}
        self.assertEqual(queued, {self.ids["srv"]})                     # pas pc-compta, ajouté au groupe hors parc

    def test_deployments_with_a_device_outside_the_fleet_are_hidden(self):
        _role, tech = self.technician(extra=("deployments.view", "windows.view", "fleet.install"))
        with transaction() as conn:
            conn.execute("DELETE FROM deployments")
            for dep_id, names in (("dep-in", ("b1",)), ("dep-out", ("b1", "compta"))):
                plan = {"kind": "windows", "bulk": True, "apps": [], "groups": {"software": [], "devices": []},
                        "devices": [{"id": self.ids[n], "uid": self.agents[n].uid, "hostname": n, "reason": ""} for n in names]}
                conn.execute("INSERT INTO deployments (id, title, user_id, created_by, pw_version, owner, created_at, expires, "
                             "state, plan) VALUES (?, ?, 1, 'admin', 1, '', '2026-10-30 10:00', 0, 'confirmed', ?)",
                             (dep_id, dep_id, json.dumps(plan)))
        page = tech.c.get("/fleet/deployments").get_data(as_text=True)
        self.assertIn("/fleet/deployments/dep-in", page)
        self.assertNotIn("/fleet/deployments/dep-out", page)
        self.assertEqual(tech.c.get("/fleet/deployments/dep-out").status_code, 404)
        self.assertEqual(self.admin.c.get("/fleet/deployments/dep-out").status_code, 200)
        compose = tech.c.get("/fleet/deployments/new").get_data(as_text=True)
        self.assertIn("pc-bat-b-01", compose)
        self.assertNotIn("pc-compta", compose)

    def test_remote_sessions_close_when_the_device_leaves_the_fleet(self):
        _role, tech = self.technician()
        self.assertEqual(tech.post(f"/fleet/{self.ids['compta']}/session", {"kind": "terminal"}, page="/fleet").status_code, 404)
        r = tech.post(f"/fleet/{self.ids['b1']}/session", {"kind": "terminal"}, page="/fleet")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            conn.execute("DELETE FROM device_group_members WHERE device_id=?", (self.ids["b1"],))
        with fixture.loco.app.test_request_context():
            db().execute("BEGIN IMMEDIATE")
            remote._prune()
            db().commit()
        with transaction() as conn:
            session = conn.execute("SELECT state, error FROM remote_sessions").fetchone()
        self.assertEqual((session["state"], session["error"]), ("closed", "Poste sorti du parc du profil."))

    def test_mcp_tokens_follow_the_creator_fleet(self):
        _role, tech = self.technician()
        token, _r = create(tech, "Claude", ["list_devices", "get_device", "list_device_groups"])
        client = Client(token)
        names = sorted(d["hostname"] for d in client.data("list_devices")["devices"])
        self.assertEqual(names, ["pc-bat-b-01", "pc-bat-b-02", "srv-web"])
        self.assertTrue(client.call("get_device", device="pc-compta")["isError"])
        self.assertTrue(client.call("get_device", device=str(self.ids["compta"]))["isError"])
        groups = {g["name"]: [d["hostname"] for d in g["devices"]] for g in client.data("list_device_groups")["groups"]}
        self.assertEqual(groups, {"Bâtiment B": ["pc-bat-b-01", "pc-bat-b-02"], "Serveurs": ["srv-web"]})


class TestDelegation(ScopeTest):
    def test_a_restricted_manager_cannot_widen_a_fleet(self):
        role_id, _r = self.profile("Chefs bâtiment B", {"fleet.view", "roles.manage", "users.view", "users.edit", "users.create"},
                                   groups=[self.batb])
        chef = self.account("chef", role_id)
        # Pas de « Tout le parc », ni un groupe qu'il n'a pas.
        self.assertEqual(self.profile("Large", {"fleet.view"}, scope="all", browser=chef)[1].status_code, 403)
        self.assertEqual(self.profile("Serveurs", {"fleet.view"}, groups=[self.servers], browser=chef)[1].status_code, 403)
        self.assertEqual(self.profile("Hors parc", {"fleet.view"}, devices=["compta"], browser=chef)[1].status_code, 403)
        narrow, r = self.profile("Poste b1", {"fleet.view"}, devices=["b1"], browser=chef)
        self.assertEqual(r.status_code, 302)
        editor = chef.c.get("/accounts/profiles/new").get_data(as_text=True)
        self.assertIn('name="fleet_scope" value="all" disabled', editor)
        self.assertNotIn(f'name="scope_group" value="{self.servers}"', editor)
        self.assertIn(f'name="scope_group" value="{self.batb}"', editor)
        # Il attribue ses propres profils limités, jamais les profils intégrés (tout le parc).
        page = chef.c.get("/accounts").get_data(as_text=True)
        self.assertIn("Poste b1", page)
        with fixture.loco.app.test_request_context():
            from flask import g
            with transaction() as conn:
                g.user = conn.execute("SELECT * FROM users WHERE username='chef'").fetchone()
            self.assertTrue(auth.can_assign(narrow))
            self.assertFalse(auth.can_assign("viewer"))
            self.assertFalse(auth.can("fleet.groups"))


if __name__ == "__main__":
    unittest.main()
