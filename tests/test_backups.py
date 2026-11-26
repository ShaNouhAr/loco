"""Sauvegardes centralisées : page Sauvegardes, Proxmox relié par jeton d'API (certificat confirmé, secret scellé),
sauvegardes suivies et planifiées avec conservation, copie des sauvegardes de Home Assistant vers la destination externe.

Un faux Proxmox HTTPS (tests/fake_proxmox.py) et le faux Home Assistant (faux ssh, fausse commande ha, faux curl)
remplacent les vrais ; le reste est le vrai : chiffrement du secret, épinglage du certificat, file des tâches,
destination externe (dossier) et chiffrement openssl."""
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import fake_homeassistant as fake_ha
import fake_proxmox
import test_portal as fixture
from loco import auth, backups, features, homeassistant, offsite, permissions, proxmox
from loco.db import transaction

PASSWORD = "Sauvegardes-2026-Secret"


class BackupTest(unittest.TestCase):
    features = '["proxmox", "homeassistant"]'

    def setUp(self):
        self.pve = fake_proxmox.FakeProxmox().start()
        self.addCleanup(self.pve.stop)
        for patch in (mock.patch.object(proxmox, "POLL", 0.02), mock.patch.object(proxmox, "LOST", 0.5)):
            patch.start()
            self.addCleanup(patch.stop)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, self.features))
            for table in ("proxmox_hosts", "backup_plans", "homeassistant_hosts", "audit"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (offsite.CONFIG, offsite.STATE))
        self.addCleanup(self.reset)
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)

    def reset(self):
        self.idle()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?, ?, ?)", (features.OPTIONAL_SETTING, offsite.CONFIG, offsite.STATE))
            for table in ("proxmox_hosts", "backup_plans", "homeassistant_hosts"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM users WHERE username LIKE 'bk-%'")
            conn.execute("DELETE FROM access_roles WHERE id LIKE 'bk-%'")

    # ------------------------------------------------------------------ aides
    def idle(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end and (proxmox._WORKERS or proxmox._REFRESH or homeassistant._WORKERS or homeassistant._REFRESH):
            time.sleep(0.02)
        self.assertFalse(proxmox._WORKERS or homeassistant._WORKERS, "tâches encore en cours")

    def post(self, path, data=None, browser=None):
        return (browser or self.admin).post(path, data, page="/backups")

    def page(self, browser=None):
        return (browser or self.admin).c.get("/backups").get_data(as_text=True)

    def flashes(self, response):
        return response.headers.get("Location", "")

    def link(self, name="pve", confirm=True, secret=fake_proxmox.SECRET):
        self.post("/backups/proxmox/link", {"address": f"127.0.0.1:{self.pve.port}", "token_id": fake_proxmox.TOKEN_ID,
                                            "secret": secret, "name": name})
        row = self.host()
        if confirm and row is not None:
            self.post(f"/backups/proxmox/{row['id']}/confirm", {"fingerprint": json.loads(row["offered"])["fingerprint"]})
            self.idle()
        return self.host()

    def host(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM proxmox_hosts ORDER BY id DESC LIMIT 1").fetchone()

    def jobs(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM proxmox_jobs ORDER BY id").fetchall()

    def audit(self):
        with transaction() as conn:
            return [(r["action"], r["detail"]) for r in conn.execute("SELECT action, detail FROM audit ORDER BY id")]

    def member(self, name, grants):
        with transaction() as conn:
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("bk-" + name, "Bk " + name, permissions.role_key("Bk " + name), json.dumps(sorted(permissions.expand(grants)))))
            auth.create_user(conn, "bk-" + name, PASSWORD, "bk-" + name, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login("bk-" + name, PASSWORD).status_code, 302)
        return browser

    def everything(self):
        """Ce qui est gardé par loco (base, journal) : le secret du jeton ne doit y figurer nulle part en clair."""
        with transaction() as conn:
            rows = [dict(r) for table in ("proxmox_hosts", "proxmox_jobs", "audit", "settings")
                    for r in conn.execute(f"SELECT * FROM {table}")]
        return json.dumps(rows, default=str)


class TestFeature(BackupTest):
    features = '["homeassistant"]'

    def test_proxmox_is_optional_and_off_by_default(self):
        self.assertIn("proxmox", features.OPTIONAL)
        self.assertEqual(self.admin.c.post("/backups/proxmox/link", data={"csrf": self.admin.token("/backups")}).status_code, 403)
        html = self.page()
        self.assertNotIn('data-tab="proxmox"', html)
        self.assertIn('data-tab="homeassistant"', html)
        self.assertIn("Fonctionnalités", html)            # où l'activer, dit à l'administrateur

    def test_every_route_has_a_policy(self):
        routes = {r.endpoint for r in fixture.loco.app.url_map.iter_rules()
                  if r.endpoint.startswith(("proxmox_", "backup_plan", "backups"))}
        self.assertEqual(len(routes), 25)
        self.assertEqual(routes - set(permissions.ENDPOINTS), set())
        with fixture.loco.app.test_request_context():
            for endpoint in routes:
                wanted = (("proxmox",) if endpoint.startswith("proxmox_") else ("homeassistant",) if "homeassistant" in endpoint else ())
                self.assertEqual(features.required(endpoint), wanted, endpoint)

    def test_the_page_is_in_the_administration_menu(self):
        html = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn('href="/backups"', html)


class TestLink(BackupTest):
    def test_nothing_is_sent_before_the_certificate_is_confirmed(self):
        row = self.link(confirm=False)
        self.assertEqual(row["fingerprint"], "")
        self.assertEqual(self.pve.requests, [])               # poignée de main TLS seulement : aucun appel, aucun jeton
        self.assertGreaterEqual(self.pve.handshakes, 1)
        html = self.admin.c.get("/enrollment").get_data(as_text=True)    # une nouvelle liaison se confirme dans l'enrôlement
        self.assertIn(proxmox.pretty(self.pve.fingerprint), html)
        self.assertNotIn(proxmox.pretty(self.pve.fingerprint), self.page())    # pas dans les sauvegardes
        self.assertIn("pve.lan", html)
        self.assertNotIn(fake_proxmox.SECRET, html)
        self.assertNotIn(fake_proxmox.SECRET, self.everything())   # scellé dès l'enregistrement

    def test_linking_is_done_from_the_enrollment_page(self):
        """Mon parc › Enrôlement, onglet Proxmox VE : chaque étape y ramène, puis propose la liaison SSH pour Mon parc."""
        self.assertIn('href="/enrollment#proxmox"', self.page())          # Sauvegardes y renvoie
        r = self.post("/backups/proxmox/link", {"address": f"127.0.0.1:{self.pve.port}", "token_id": fake_proxmox.TOKEN_ID,
                                                "secret": fake_proxmox.SECRET, "name": "pve", "next": "enrollment"})
        self.assertTrue(r.headers["Location"].endswith("/enrollment#proxmox"))
        row = self.host()
        enrollment = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn('data-panel="proxmox"', enrollment)
        self.assertIn(proxmox.pretty(self.pve.fingerprint), enrollment)
        r = self.post(f"/backups/proxmox/{row['id']}/confirm", {"fingerprint": json.loads(row["offered"])["fingerprint"], "next": "enrollment"})
        self.assertTrue(r.headers["Location"].endswith("/enrollment#proxmox"))
        self.idle()
        enrollment = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn(f'/proxmox/{row["id"]}#ssh', enrollment)              # étape suivante : SSH, pour Mon parc
        self.assertNotIn(proxmox.pretty(self.pve.fingerprint), enrollment)  # plus rien à confirmer

    def test_confirming_pins_the_certificate_and_reads_the_machines(self):
        row = self.link()
        self.assertEqual(row["fingerprint"], self.pve.fingerprint)
        self.assertEqual(row["error"], "")
        self.assertTrue(all(r["token"] for r in self.pve.requests))
        data = proxmox.info(row)
        self.assertEqual([g["vmid"] for g in data["guests"]], [100, 101, 106])
        self.assertEqual([s["storage"] for s in proxmox.backup_storages(data)], ["local"])
        self.assertEqual(data["guests"][0]["backups"][0]["notes"], "sauvegarde manuelle")
        self.assertEqual(data["missing"], [])
        html = self.page()
        for text in ("web", "modele-debian", "dns", "Proxmox VE 9.2.21", "Sauvegarder maintenant"):
            self.assertIn(text, html)
        self.assertNotIn(fake_proxmox.SECRET, html + self.everything())
        self.assertIn("proxmox.liaison", [a for a, _d in self.audit()])

    def test_a_wrong_or_expired_confirmation_is_refused(self):
        row = self.link(confirm=False)
        self.post(f"/backups/proxmox/{row['id']}/confirm", {"fingerprint": "00" * 32})
        self.assertEqual(self.host()["fingerprint"], "")
        with transaction() as conn:
            conn.execute("UPDATE proxmox_hosts SET offered_at=? WHERE id=?", (time.time() - proxmox.OFFER_TTL - 1, row["id"]))
        self.post(f"/backups/proxmox/{row['id']}/confirm", {"fingerprint": self.pve.fingerprint})
        self.assertEqual(self.host()["fingerprint"], "")
        self.assertEqual(self.pve.requests, [])

    def test_cancelling_forgets_an_unconfirmed_server(self):
        row = self.link(confirm=False)
        self.post(f"/backups/proxmox/{row['id']}/confirm", {"cancel": "1"})
        self.assertIsNone(self.host())

    def test_bad_input_is_refused_before_any_connection(self):
        for address, token, secret in (("http://127.0.0.1:8006", fake_proxmox.TOKEN_ID, fake_proxmox.SECRET),
                                       ("127.0.0.1/api", fake_proxmox.TOKEN_ID, fake_proxmox.SECRET),
                                       ("-oProxy", fake_proxmox.TOKEN_ID, fake_proxmox.SECRET),
                                       ("127.0.0.1", "root", fake_proxmox.SECRET),
                                       ("127.0.0.1", fake_proxmox.TOKEN_ID, "x y")):
            self.post("/backups/proxmox/link", {"address": address, "token_id": token, "secret": secret})
        self.assertIsNone(self.host())
        self.assertEqual(self.pve.handshakes, 0)

    def test_a_changed_certificate_stops_the_token_until_confirmed_again(self):
        row = self.link()
        self.pve.restart()
        sent = len(self.pve.requests)
        self.assertIn("ne présente plus le certificat confirmé", proxmox.refresh(row["id"]))
        self.assertEqual(len(self.pve.requests), sent)        # rien n'est parti vers le nouveau certificat
        self.post(f"/backups/proxmox/{row['id']}/certificate")
        html = self.page()
        self.assertIn("diffère de celui confirmé", html)
        offered = json.loads(self.host()["offered"])
        self.assertEqual(offered["fingerprint"], self.pve.fingerprint)
        self.post(f"/backups/proxmox/{row['id']}/confirm", {"fingerprint": offered["fingerprint"]})
        self.idle()
        self.assertEqual(self.host()["error"], "")
        self.assertIn("proxmox.certificat", [a for a, _d in self.audit()])

    def test_a_refused_token_and_missing_rights_are_explained(self):
        row = self.link(secret="11111111-2222-3333-4444-555555555555")
        self.assertIn("refuse le jeton", row["error"])
        with transaction() as conn:
            conn.execute("DELETE FROM proxmox_hosts")
        self.pve.state["privileges"] = ["VM.Audit", "Datastore.Audit"]
        row = self.link()
        self.assertEqual(proxmox.info(row)["missing"], ["VM.Backup", "Datastore.AllocateSpace", "Sys.Audit"])
        self.assertIn("VM.Backup, Datastore.AllocateSpace, Sys.Audit", self.page())

    def test_removing_forgets_plans_and_jobs(self):
        row = self.link()
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"})
        self.idle()
        self.post("/backups/plans", {"kind": "proxmox", "host_id": row["id"], "scope": "all", "storage": "local", "mode": "snapshot",
                                     "keep": "3", "recurrence": "daily", "at_time": "02:00", "start_date": backups_date()})
        self.post(f"/backups/proxmox/{row['id']}/delete")
        self.assertIsNone(self.host())
        self.assertEqual(self.jobs(), [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_plans").fetchone()[0], 0)


def backups_date():
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


class TestProxmoxBackups(BackupTest):
    def test_backing_up_checked_machines_now(self):
        row = self.link()
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": ["100", "106"], "storage": "local", "mode": "snapshot"})
        self.idle()
        jobs = self.jobs()
        self.assertEqual([(j["vmid"], j["state"]) for j in jobs], [(100, "success"), (106, "success")])
        self.assertIn("Backup job finished successfully", jobs[0]["output"])
        started = [r for r in self.pve.requests if r["path"].endswith("/vzdump")]
        self.assertEqual([r["params"]["vmid"] for r in started], ["100", "106"])
        self.assertEqual(started[0]["params"]["notes-template"], "loco {{guestname}}")
        self.assertEqual(started[0]["params"]["storage"], "local")
        self.assertNotIn("prune-backups", started[0]["params"])   # conservation faite par loco, avec ses seuls droits
        self.assertEqual(len(self.pve.backups(vmid=100)), 2)
        html = self.page()
        self.assertIn("loco", proxmox.info(self.host())["guests"][0]["backups"][0]["notes"])
        self.assertIn("Réussie", html)

    def test_what_is_running_is_not_launched_twice(self):
        row = self.link()
        self.pve.state["steps"] = 1000
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"})
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"})
        self.assertEqual(len(self.jobs()), 1)
        time.sleep(0.2)
        self.assertIn("sauvegarde en cours", self.page())
        job = self.jobs()[0]
        self.post(f"/backups/proxmox/jobs/{job['id']}/cancel")
        self.idle()
        self.assertEqual(self.jobs()[0]["state"], "failed")
        self.assertTrue(any(r["method"] == "DELETE" and "/tasks/" in r["path"] for r in self.pve.requests))

    def test_a_storage_absent_from_the_node_or_unknown_is_refused(self):
        row = self.link()
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local-lvm", "mode": "snapshot"})
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "x y", "mode": "snapshot"}).status_code, 400)
        self.assertEqual(self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "--rm"}).status_code, 400)

    def test_a_refused_right_and_a_failed_backup_are_kept_with_their_reason(self):
        row = self.link()
        self.pve.state["fail"].add(106)
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "106", "storage": "local", "mode": "stop"})
        self.idle()
        self.assertEqual(self.jobs()[-1]["state"], "failed")
        self.assertIn("job errors", self.jobs()[-1]["output"])
        self.pve.state["privileges"].remove("VM.Backup")
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"})
        self.idle()
        self.assertIn("Permission check failed (/vms/100, VM.Backup)", self.jobs()[-1]["output"])
        with fixture.loco.app.test_request_context("/"):
            texts = [r["text"] for r in proxmox.attention()]
        self.assertTrue(any("2 sauvegardes en échec" in t for t in texts), texts)
        self.assertIn("2 sauvegardes en échec", self.admin.c.get("/").get_data(as_text=True))

    def test_a_plan_keeps_only_its_own_latest_backups(self):
        row = self.link()
        self.post("/backups/plans", {"kind": "proxmox", "host_id": row["id"], "vmid": "100", "storage": "local", "mode": "snapshot",
                                     "keep": "2", "recurrence": "daily", "at_time": "02:00", "start_date": backups_date()})
        with transaction() as conn:
            plan = conn.execute("SELECT * FROM backup_plans").fetchone()
        self.assertEqual((plan["guests"], plan["keep"], plan["enabled"]), ("100", 2, 1))
        # Une sauvegarde manuelle par loco et une protégée de la même planification : jamais supprimées.
        self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"})
        self.idle()
        self.pve.state["content"]["local"].append({"volid": "local:backup/vzdump-qemu-100-2020_01_01-00_00_00.vma.zst", "vmid": 100,
                                                   "ctime": 1577836800, "size": 1, "notes": f"loco plan {plan['id']} web", "protected": 1})
        start = datetime.datetime.combine(datetime.date.fromisoformat(plan["start_date"]), datetime.time(2, 0))
        for day in range(4):
            with transaction() as conn:
                backups.materialize(conn, start + datetime.timedelta(days=day, minutes=1))
            self.idle()
        notes = sorted(i["notes"] for i in self.pve.backups(vmid=100))
        self.assertEqual(notes.count(f"loco plan {plan['id']} web"), 3)        # 2 gardées + la protégée
        self.assertIn("sauvegarde manuelle", notes)                           # faite par Proxmox lui-même
        self.assertIn("loco web", notes)                                      # faite par loco, hors planification
        self.assertEqual([j["state"] for j in self.jobs()], ["success"] * 5)
        self.assertIn("plus ancienne", self.jobs()[-1]["output"])
        with transaction() as conn:
            plan = conn.execute("SELECT * FROM backup_plans").fetchone()
        self.assertTrue(plan["next_run"].endswith("02:00:00"))
        self.assertIn("Chaque jour à 02:00", self.page())

    def test_all_machines_means_those_of_the_day_without_templates(self):
        row = self.link()
        self.post("/backups/plans", {"kind": "proxmox", "host_id": row["id"], "scope": "all", "storage": "local", "mode": "snapshot",
                                     "keep": "0", "recurrence": "once", "at_time": "02:00", "start_date": backups_date()})
        self.pve.state["guests"].append({"vmid": 120, "name": "nouvelle", "type": "lxc", "node": "pve", "status": "running", "template": 0})
        proxmox.refresh(row["id"])
        with transaction() as conn:
            plan = conn.execute("SELECT * FROM backup_plans").fetchone()
            backups.materialize(conn, datetime.datetime.strptime(plan["next_run"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=5))
        self.idle()
        self.assertEqual(sorted(j["vmid"] for j in self.jobs()), [100, 106, 120])
        with transaction() as conn:
            plan = conn.execute("SELECT * FROM backup_plans").fetchone()
        self.assertEqual((plan["enabled"], plan["next_run"]), (0, ""))         # ponctuelle : terminée

    def test_following_resumes_after_a_restart_of_loco(self):
        row = self.link()
        self.pve.state["steps"] = 3
        with transaction() as conn:
            proxmox.queue(conn, row, [100], "local", "snapshot")
        # Le portail s'arrête juste après avoir lancé la tâche sur Proxmox : elle a son UPID, loco ne la suit plus.
        job = self.jobs()[0]
        upid = proxmox.request_api(row, "POST", "/nodes/pve/vzdump", {"vmid": 100, "storage": "local", "mode": "snapshot",
                                                                      "notes-template": "loco {{guestname}}"})
        with transaction() as conn:
            conn.execute("UPDATE proxmox_jobs SET state='running', upid=? WHERE id=?", (upid, job["id"]))
            conn.execute("INSERT INTO proxmox_jobs (host_id, node, vmid, storage, state, created_at) VALUES (?,?,?,?,?,?)",
                         (row["id"], "pve", 106, "local", "running", "2026-11-18 10:00:00"))   # coupé avant Proxmox
        proxmox.start()
        self.idle()
        jobs = self.jobs()
        self.assertEqual([(j["vmid"], j["state"]) for j in jobs], [(100, "success"), (106, "success")])
        self.assertEqual(sum(1 for r in self.pve.requests if r["path"].endswith("/vzdump")), 2)   # rien de relancé en double

    def test_declaring_a_nas_in_proxmox(self):
        row = self.link()
        data = {"type": "cifs", "storage": "nas-sauvegardes", "server": "192.168.1.20", "share": "proxmox",
                "username": "pve", "password": "Mot-De-Passe-Du-NAS"}
        self.post(f"/backups/proxmox/{row['id']}/storage", data)
        self.assertEqual(self.pve.state["declared"], [])              # droit Datastore.Allocate absent
        self.pve.state["privileges"].append("Datastore.Allocate")
        self.post(f"/backups/proxmox/{row['id']}/storage", data)
        self.assertEqual(self.pve.state["declared"][0]["password"], "Mot-De-Passe-Du-NAS")
        self.assertEqual(self.pve.state["declared"][0]["content"], "backup")
        self.assertNotIn("Mot-De-Passe-Du-NAS", self.everything())
        self.assertIn("nas-sauvegardes", [s["storage"] for s in proxmox.backup_storages(proxmox.info(self.host()))])
        self.post(f"/backups/proxmox/{row['id']}/storage", {"type": "nfs", "storage": "nas2", "server": "nas", "export": "relatif"})
        self.assertEqual(len(self.pve.state["declared"]), 1)


class TestRights(BackupTest):
    def test_each_step_has_its_own_right(self):
        row = self.link()
        viewer = self.member("lecteur", ["proxmox.view"])
        self.assertIn("web", self.page(viewer))
        self.assertNotIn("Sauvegarder maintenant", self.page(viewer))
        self.assertEqual(self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"},
                                   browser=viewer).status_code, 403)
        operator = self.member("operateur", ["proxmox.backup"])
        self.assertEqual(self.post(f"/backups/proxmox/{row['id']}/backup", {"vmid": "100", "storage": "local", "mode": "snapshot"},
                                   browser=operator).status_code, 302)
        self.idle()
        self.assertEqual(self.post(f"/backups/proxmox/{row['id']}/delete", browser=operator).status_code, 403)
        self.assertEqual(self.post("/backups/plans", {"kind": "homeassistant", "host_id": "1", "recurrence": "daily",
                                                      "at_time": "02:00", "start_date": backups_date()}, browser=operator).status_code, 403)
        nobody = self.member("rien", ["fleet.view"])
        self.assertEqual(nobody.c.get("/backups").status_code, 403)


class HomeAssistantCopyTest(BackupTest):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp(prefix="loco-bk-hass-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.state = os.path.join(self.folder, "state")
        self.ssh = fake_ha.install(self.folder)
        patch = mock.patch("loco.homeassistant.ssh_binary", return_value=self.ssh)
        patch.start()
        self.addCleanup(patch.stop)
        self.dest = os.path.join(self.folder, "nas")
        # Sauvegardes de départ du faux Home Assistant : de vraies archives, à la taille annoncée.
        data = json.load(open(os.path.join(self.state, "backups.json")))
        os.makedirs(os.path.join(self.state, "files"))
        for b in data["backups"]:
            b["size_bytes"] = 4096 + len(b["slug"])
            b["locations"] = [None]
            with open(os.path.join(self.state, "files", b["slug"] + ".tar"), "wb") as f:
                f.write((b["slug"].encode() * 600)[:b["size_bytes"]])
        data["backups"].append({"slug": "c0ffee00", "name": "Sur le NAS de HA", "date": "2026-11-09T03:00:00+00:00", "type": "full",
                                "size_bytes": 10, "location": "nas", "locations": ["nas"]})
        json.dump(data, open(os.path.join(self.state, "backups.json"), "w"))
        self.post("/homeassistant/link", {"host": "192.168.43.80", "port": "22", "username": "root", "name": "Maison", "next": "enrollment"})
        nonce = self.admin.c.get("/enrollment").get_data(as_text=True).split('name="nonce" value="')[1].split('"')[0]
        self.post("/homeassistant/confirm", {"nonce": nonce, "next": "enrollment"})
        self.idle()
        with transaction() as conn:
            self.ha = conn.execute("SELECT * FROM homeassistant_hosts").fetchone()

    def destination(self, passphrase=""):
        cfg = offsite.defaults()
        cfg.update(type="folder", folder={"path": self.dest}, crypt={"passphrase": passphrase})
        with transaction() as conn:
            offsite.save(cfg, conn)

    def copy(self, fresh=False):
        self.post(f"/backups/homeassistant/{self.ha['id']}/copy", {"fresh": "1"} if fresh else {})
        self.idle()
        with transaction() as conn:
            row = conn.execute("SELECT * FROM homeassistant_hosts WHERE id=?", (self.ha["id"],)).fetchone()
            job = conn.execute("SELECT * FROM homeassistant_jobs WHERE action='copy' ORDER BY id DESC LIMIT 1").fetchone()
        return backups.copy_state(row), job

    def copies(self):
        return sorted(n for n in os.listdir(self.dest) if n.startswith("homeassistant-")) if os.path.isdir(self.dest) else []

    def log(self, name):
        try:
            with open(os.path.join(self.state, name)) as f:
                return f.read()
        except OSError:
            return ""


class TestHomeAssistantCopy(HomeAssistantCopyTest):
    def test_without_a_destination_nothing_is_copied(self):
        state, job = self.copy()
        self.assertIsNone(job)
        self.assertIn("Choisissez d'abord une destination externe", self.page())

    def test_the_latest_local_backup_is_copied_byte_for_byte(self):
        self.destination()
        state, job = self.copy()
        self.assertEqual(job["state"], "success", job["output"])
        self.assertEqual(state["slug"], "1f3a9c20")                       # la plus récente gardée dans HA, pas celle du NAS de HA
        name, = self.copies()
        self.assertRegex(name, r"^homeassistant-\d+-\d{8}-\d{6}-1f3a9c20\.tar$")
        with open(os.path.join(self.dest, name), "rb") as got, open(os.path.join(self.state, "files", "1f3a9c20.tar"), "rb") as want:
            self.assertEqual(got.read(), want.read())
        self.assertEqual(os.listdir(backups.SPOOL), [])                    # rien ne reste sur le disque de loco
        # Le jeton du Supervisor passe par l'entrée de curl : jamais en argument, ni dans ce que voit ssh.
        self.assertIn('"token": true', self.log("curl.log"))
        self.assertNotIn("jeton-du-module", self.log("curl.log") + self.log("ssh.log"))
        again, job = self.copy()
        self.assertEqual(job["state"], "success")
        self.assertIn("déjà sur la destination", job["output"])
        self.assertEqual(len(self.copies()), 1)

    def test_a_fresh_backup_first_then_only_the_latest_copies_are_kept(self):
        self.destination()
        self.post(f"/backups/homeassistant/{self.ha['id']}/keep", {"keep": "2"})
        for _round in range(3):
            state, job = self.copy(fresh=True)
            self.assertEqual(job["state"], "success", job["output"])
            time.sleep(1.05)                                                # dates de sauvegarde distinctes (à la seconde)
        self.assertEqual(len(self.copies()), 2)
        self.assertEqual(state["copies"], 2)
        self.assertIn("plus ancienne", job["output"])
        self.assertEqual(sum(1 for line in self.log("commands.log").splitlines() if line.startswith("backups new")), 3)

    def test_the_copy_is_encrypted_with_the_destination_passphrase(self):
        self.destination(passphrase="Phrase-De-Passe-Externe")
        state, job = self.copy()
        self.assertEqual(job["state"], "success", job["output"])
        name, = self.copies()
        self.assertTrue(name.endswith(".tar.enc"))
        self.assertTrue(state["encrypted"])
        out = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:P", "-in", os.path.join(self.dest, name)],
                             env=dict(os.environ, P="Phrase-De-Passe-Externe"), capture_output=True)
        with open(os.path.join(self.state, "files", "1f3a9c20.tar"), "rb") as want:
            self.assertEqual(out.stdout, want.read())

    def test_a_truncated_or_refused_download_fails_and_leaves_nothing(self):
        self.destination()
        open(os.path.join(self.state, "download-short"), "w").close()
        state, job = self.copy()
        self.assertEqual(job["state"], "failed")
        self.assertIn("incomplète", job["output"])
        os.unlink(os.path.join(self.state, "download-short"))
        open(os.path.join(self.state, "download-fails"), "w").close()
        state, job = self.copy()
        self.assertIn("404", job["output"])
        self.assertEqual(self.copies(), [])
        self.assertEqual(os.listdir(backups.SPOOL), [])
        self.assertIn("copie en échec", self.page())

    def test_copies_of_home_assistant_and_backups_of_loco_never_prune_each_other(self):
        self.destination()
        os.makedirs(self.dest, exist_ok=True)
        for stamp in ("20260101-000000", "20260102-000000"):
            open(os.path.join(self.dest, f"loco-repo-{stamp}.tar.gz"), "w").close()
        self.copy()
        with transaction() as conn:
            cfg = offsite.load(conn)
        self.assertEqual(len(offsite._list(cfg)), 2)                              # celles de loco, et elles seules
        self.assertEqual(len(backups._remote_copies(cfg, self.ha["id"])), 1)
        self.assertEqual(backups._remote_copies(cfg, self.ha["id"] + 1), [])

    def test_a_planned_copy(self):
        self.destination()
        self.post("/backups/plans", {"kind": "homeassistant", "host_id": self.ha["id"], "fresh": "1", "recurrence": "weekly",
                                     "weekdays": ["0", "3"], "at_time": "03:30", "start_date": backups_date()})
        with transaction() as conn:
            plan = conn.execute("SELECT * FROM backup_plans").fetchone()
            backups.materialize(conn, datetime.datetime.strptime(plan["next_run"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=1))
        self.idle()
        self.assertEqual(len(self.copies()), 1)
        html = self.page()
        self.assertIn("sauvegarde complète d'abord", html)
        self.assertIn("03:30", html)
        with transaction() as conn:
            job = conn.execute("SELECT * FROM homeassistant_jobs WHERE action='copy'").fetchone()
        self.assertEqual((job["backup"], job["created_by"]), (1, "planification"))
        self.assertIn("Copie vers la destination externe", self.admin.c.get("/homeassistant/%d" % self.ha["id"]).get_data(as_text=True))

    def test_removing_the_instance_removes_its_plans(self):
        self.destination()
        self.post("/backups/plans", {"kind": "homeassistant", "host_id": self.ha["id"], "recurrence": "daily",
                                     "at_time": "03:30", "start_date": backups_date()})
        self.post(f"/homeassistant/{self.ha['id']}/delete")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_plans").fetchone()[0], 0)


class TestDestination(BackupTest):
    def test_the_form_comes_back_to_the_backups_page_and_can_leave_loco_out(self):
        dest = tempfile.mkdtemp(prefix="loco-bk-dest-")
        self.addCleanup(shutil.rmtree, dest, True)
        r = self.post("/system/offsite", {"offsite_type": "folder", "folder_path": dest, "crypt_passphrase": "",
                                          "loco_include_shown": "1", "from": "backups"})
        self.assertTrue(r.headers["Location"].endswith("/backups#destination"))
        with transaction() as conn:
            cfg = offsite.load(conn)
        self.assertFalse(cfg["loco"]["include"])
        self.assertEqual(cfg["type"], "folder")
        with mock.patch("loco.system.list_backups", return_value=[{"name": "loco-repo-20260101-000000.tar.gz"}]), \
                mock.patch("loco.offsite._put") as put, transaction() as conn:
            offsite.sync(conn)
        put.assert_not_called()
        self.post("/system/offsite", {"loco_include_shown": "1", "loco_include": "1"})
        with transaction() as conn:
            self.assertTrue(offsite.load(conn)["loco"]["include"])

    def test_large_files_go_to_s3_without_being_read_into_memory(self):
        cfg = offsite.defaults()
        cfg.update(type="s3", s3=dict(cfg["s3"], endpoint="s3.example", bucket="b", access_key="a", secret_key="s"))
        path = os.path.join(tempfile.mkdtemp(prefix="loco-bk-s3-"), "f.tar")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "wb") as f:
            f.write(b"x" * 3000000)
        seen = {}

        class Answer:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout):
            seen.update(length=req.headers.get("Content-length"), body=type(req.data).__name__,
                        digest=req.headers.get("X-amz-content-sha256"), data=req.data.read())
            return Answer()
        with mock.patch("urllib.request.urlopen", urlopen):
            offsite._put(cfg, path, "homeassistant-1-20260101-000000-abc.tar")
        import hashlib
        self.assertEqual(seen["length"], "3000000")
        self.assertEqual(seen["body"], "BufferedReader")
        self.assertEqual(seen["digest"], hashlib.sha256(b"x" * 3000000).hexdigest())
        self.assertEqual(len(seen["data"]), 3000000)


class TestPage(BackupTest):
    def test_the_page_polls_a_signature_and_shows_each_section(self):
        self.link()
        html = self.page()
        for tab in ("proxmox", "homeassistant", "loco", "destination"):
            self.assertIn(f'data-tab="{tab}"', html)
        self.assertIn('data-poll="/backups"', html)
        signature = self.admin.c.get("/backups", headers={"X-Requested-With": "fetch"}).get_json()["signature"]
        self.assertEqual(len(signature), 16)
        self.assertIn('data-refresh="pve-', html)

    def test_untrusted_names_are_bounded_and_escaped(self):
        self.pve.state["guests"].append({"vmid": 130, "name": "<script>alert(1)</script>" + "x" * 300, "type": "qemu",
                                         "node": "pve", "status": "running", "template": 0})
        self.pve.state["guests"].append({"vmid": 7, "name": "hors-limites", "type": "qemu", "node": "pve", "status": "running"})
        self.pve.state["guests"].append({"vmid": 131, "name": "mauvais-noeud", "type": "qemu", "node": "../x", "status": "running"})
        row = self.link()
        names = [g["name"] for g in proxmox.info(row)["guests"]]
        self.assertTrue(all(len(n) <= 64 for n in names))
        self.assertNotIn("hors-limites", names)
        self.assertNotIn("mauvais-noeud", names)
        html = self.page()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
