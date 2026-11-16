"""Wazuh (fonctionnalité facultative) : réglages et mot de passe d'enrôlement, paquets officiels vérifiés, commande
remise à l'agent, état remonté par les postes, droits et périmètre ; agent Linux (installation, retrait, état) avec des
commandes simulées ; lecture de l'état par l'agent Windows (sonde Mono)."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, device
from loco import auth, features, permissions, wazuh
from loco.db import transaction

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Enrolement-Wazuh-2026"
VERSIONS = (["4.14.10", "4.14.9", "4.13.1"], 0, "")


def ident(family, hostname, version="2.166.0"):
    names = {"debian": "Debian GNU/Linux 13 (trixie)", "rhel": "Rocky Linux 9.6 (Blue Onyx)", "windows": "Microsoft Windows 11 Professionnel",
             "android": "Android 15"}
    return {"hostname": hostname, "agent_version": version, "addresses": ["192.168.43.90"], "os": {"family": family, "name": names[family]}}


CONNECTED = {"installed": True, "version": "v4.14.9", "active": True, "status": "connected", "manager": "wazuh.lab.test", "id": "003", "name": "pc"}
MANAGER = {"installed": False, "role": "manager", "version": "v4.14.9", "active": True}
HOST_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIGxvY28td2F6dWgtZXNzYWktY2xlLWQtaG90ZS0wMDE="
REMOVED = "Provide the ID of the agent to be removed (or '\\q' to quit): 003\nAgent '003' removed.\n"
EMPTY = "Choose your action: A,E,L,R or Q: \n** No agent available. You need to add one first.\n\nmanage_agents: Exiting.\n"
ABSENT = "Provide the ID of the agent to be removed (or '\\q' to quit): %s\n2026/11/16 13:34:12 manage_agents: ERROR: 9011: Agent ID not found\n"


class WazuhTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp(prefix="loco-wazuh-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        for patch in (mock.patch.object(wazuh, "WAZUH_DIR", self.folder), mock.patch.object(wazuh, "versions", return_value=VERSIONS)):
            patch.start()
            self.addCleanup(patch.stop)
        wazuh._STATE.clear()
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["wazuh"]'))
            conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (wazuh.SETTING, wazuh.VERSIONS_SETTING))
            conn.execute("DELETE FROM audit")
            conn.execute("DELETE FROM users WHERE username LIKE 'wazuh-%'")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
        self.addCleanup(self.reset)

    def reset(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?, ?, ?)", (features.OPTIONAL_SETTING, wazuh.SETTING, wazuh.VERSIONS_SETTING))
            conn.execute("DELETE FROM users WHERE username LIKE 'wazuh-%'")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
            conn.execute("DELETE FROM device_groups")

    # ------------------------------------------------------------------ aides
    def post(self, path, data=None, browser=None):
        return (browser or self.admin).post(path, data, page="/fleet/wazuh")

    def page(self, browser=None):
        return (browser or self.admin).c.get("/fleet/wazuh").get_data(as_text=True)

    def configure(self, **form):
        values = {"manager": "wazuh.lab.test", "version": "4.14.9", "group": "servers", "password": PASSWORD}
        values.update(form)
        return self.post("/fleet/wazuh/settings", values)

    def machine(self, family="debian", hostname="pc-wazuh", version="2.166.0", ip="192.168.43.90", state=None):
        agent = Agent(ident(family, hostname, version), ip)
        self.assertEqual(agent.enroll().status_code, 200)
        if state is not None:
            self.assertEqual(agent.post("/agent/api/checkin", dict(agent.ident, wazuh=state), agent.token).status_code, 200)
        return agent, device(agent.uid)["id"]

    def package(self, name, content=b"paquet officiel"):
        """Paquet déjà téléchargé et vérifié par loco."""
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(content)
        with open(os.path.join(self.folder, name + ".json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}, f)
        return hashlib.sha256(content).hexdigest()

    def commands(self, dev_id):
        with transaction() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM device_commands WHERE device_id=? ORDER BY id", (dev_id,))]

    def account(self, name, grants, groups=(), devices=()):
        whole = not groups and not devices
        r = self.admin.post("/accounts/profiles/add", {
            "label": "Profil " + name, "description": "", "permissions": sorted(permissions.expand(grants)),
            "fleet_scope": "all" if whole else "some", "scope_group": [str(g) for g in groups], "scope_device": [str(d) for d in devices]},
            page="/accounts/profiles/new")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        with transaction() as conn:
            auth.create_user(conn, "wazuh-" + name, "Wazuh-Password-2026", r.headers["Location"].rsplit("/", 1)[-1], must_change=False)
        fixture.reset_failures()
        browser = fixture.Browser()
        browser.login("wazuh-" + name, "Wazuh-Password-2026")
        return browser


class TestFeature(WazuhTest):
    def test_off_by_default_and_hidden_until_enabled(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertEqual(self.admin.c.get("/fleet/wazuh").status_code, 403)
        self.assertNotIn("/fleet/wazuh", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.post("/fleet/wazuh/install", {"only": "1"}).status_code, 403)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["wazuh"]'))
        self.assertIn("/fleet/wazuh", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertIn("Wazuh n'est pas encore réglé", self.page())

    def test_listed_with_the_optional_features(self):
        html = self.admin.c.get("/administration/features").get_data(as_text=True)
        self.assertIn("Agent Wazuh", html)
        self.assertIn("wazuh", features.OPTIONAL)


class TestSettings(WazuhTest):
    def test_password_is_sealed_never_shown_and_never_journaled(self):
        self.assertEqual(self.configure().status_code, 302)
        with transaction() as conn:
            stored = conn.execute("SELECT value FROM settings WHERE key=?", (wazuh.SETTING,)).fetchone()[0]
            journal = " ".join(f"{r['action']} {r['detail']}" for r in conn.execute("SELECT action, detail FROM audit"))
            self.assertEqual(wazuh.load(conn)["password"], PASSWORD)
        self.assertNotIn(PASSWORD, stored)
        self.assertNotIn(PASSWORD, journal)
        self.assertIn("mot de passe d'enrôlement modifié", journal)
        html = self.page()
        self.assertNotIn(PASSWORD, html)
        self.assertIn(wazuh.MASK, html)
        self.assertIn("wazuh.lab.test", html)

    def test_untouched_field_keeps_the_password_and_the_box_removes_it(self):
        self.configure()
        self.configure(password=wazuh.MASK, group="docker")
        with transaction() as conn:
            self.assertEqual(wazuh.load(conn)["password"], PASSWORD)
            self.assertEqual(wazuh.load(conn)["group"], "docker")
        self.configure(password="")
        with transaction() as conn:
            self.assertEqual(wazuh.load(conn)["password"], PASSWORD)
        self.configure(password=wazuh.MASK, clear_password="1")
        with transaction() as conn:
            self.assertEqual(wazuh.load(conn)["password"], "")
        self.assertNotIn(wazuh.MASK, self.page())

    def test_invalid_values_are_refused_and_nothing_is_saved(self):
        for form in ({"manager": "https://wazuh.lab.test"}, {"manager": "wazuh lab"}, {"manager": ""}, {"version": "dernière"},
                     {"version": "4.14"}, {"group": "serveurs web"}, {"group": "a;rm -rf /"}, {"password": "mot\nde passe"}):
            with self.subTest(form=form):
                self.assertEqual(self.configure(**form).status_code, 302)
                with transaction() as conn:
                    self.assertEqual(wazuh.load(conn)["manager"], "")
        self.assertEqual(self.configure(manager="192.168.10.13", group="servers, docker").status_code, 302)
        with transaction() as conn:
            self.assertEqual(wazuh.load(conn)["group"], "servers,docker")
            self.assertEqual(wazuh.load(conn)["manager"], "192.168.10.13")

    def test_a_sealed_value_from_another_key_reads_as_no_password(self):
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (wazuh.SETTING, json.dumps({"manager": "m", "version": "4.14.9", "group": "", "password": "pas-un-jeton"})))
            self.assertEqual(wazuh.load(conn)["password"], "")


class TestRights(WazuhTest):
    def test_each_step_has_its_right(self):
        self.configure()
        _agent, dev = self.machine()
        stranger = self.account("sans", {"fleet.view"})
        self.assertEqual(stranger.c.get("/fleet/wazuh").status_code, 403)
        self.assertNotIn("/fleet/wazuh", stranger.c.get("/fleet").get_data(as_text=True))
        reader = self.account("lecteur", {"wazuh.view"})
        html = self.page(reader)
        self.assertIn("pc-wazuh", html)
        self.assertNotIn('name="password"', html)                       # ni réglages ni boutons d'action
        self.assertNotIn('name="ids"', html)
        self.assertEqual(self.post("/fleet/wazuh/settings", {"manager": "x", "version": "4.14.9"}, reader).status_code, 403)
        self.assertEqual(self.post("/fleet/wazuh/install", {"only": str(dev)}, reader).status_code, 403)
        self.assertEqual(self.post("/fleet/wazuh/remove", {"only": str(dev)}, reader).status_code, 403)
        installer = self.account("poseur", {"wazuh.install"})
        self.assertEqual(self.post("/fleet/wazuh/settings", {"manager": "x", "version": "4.14.9"}, installer).status_code, 403)
        self.assertEqual(self.post("/fleet/wazuh/install", {"only": str(dev)}, installer).status_code, 302)
        self.assertEqual([c["action"] for c in self.commands(dev)], ["wazuh"])

    def test_an_account_limited_to_part_of_the_fleet_cannot_reach_the_rest(self):
        self.configure()
        _a, inside = self.machine(hostname="pc-dedans", ip="192.168.43.91")
        _b, outside = self.machine(hostname="pc-dehors", ip="192.168.43.92")
        limited = self.account("limite", {"wazuh.install"}, devices=[inside])
        html = self.page(limited)
        self.assertIn("pc-dedans", html)
        self.assertNotIn("pc-dehors", html)
        self.post("/fleet/wazuh/install", {"ids": [str(inside), str(outside)]}, limited)
        self.assertEqual(len(self.commands(inside)), 1)
        self.assertEqual(self.commands(outside), [])

    def test_a_command_is_dropped_when_its_author_lost_the_right(self):
        self.configure()
        self.package("wazuh-agent_4.14.9-1_amd64.deb")
        agent, dev = self.machine()
        installer = self.account("ephemere", {"wazuh.install"})
        self.post("/fleet/wazuh/install", {"only": str(dev)}, installer)
        with transaction() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE username='wazuh-ephemere'")
        self.assertEqual(agent.checkin().get_json()["commands"], [])
        self.assertEqual(self.commands(dev)[0]["state"], "cancelled")


class TestState(WazuhTest):
    def test_state_reported_at_contact_is_cleaned_and_kept(self):
        agent, dev = self.machine(state=dict(CONNECTED, key="0123456789abcdef", status="connected", extra="x" * 5000))
        with transaction() as conn:
            stored = json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0])
        self.assertEqual(stored, {"installed": True, "version": "4.14.9", "active": True, "status": "connected",
                                  "manager": "wazuh.lab.test", "id": "003", "name": "pc"})
        agent.post("/agent/api/checkin", dict(agent.ident, wazuh={"installed": True, "version": "<script>", "status": "bizarre", "active": 1}), agent.token)
        with transaction() as conn:
            stored = json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0])
        self.assertEqual((stored["version"], stored["status"], stored["active"]), ("", "", True))
        agent.post("/agent/api/checkin", dict(agent.ident, wazuh={"installed": False}), agent.token)
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0]), {"installed": False})

    def test_an_older_loco_agent_says_nothing_and_keeps_what_was_known(self):
        agent, dev = self.machine(state=CONNECTED)
        agent.checkin()                                                  # contact sans la clé « wazuh »
        with transaction() as conn:
            self.assertTrue(json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0])["installed"])

    def test_what_the_page_says_for_each_situation(self):
        self.configure(version="4.14.10")
        cases = (("pc-absent", {"installed": False}, "Non installé"), ("pc-ok", dict(CONNECTED, version="4.14.10"), "Connecté"),
                 ("pc-attente", dict(CONNECTED, status="pending", version="4.14.10"), "En attente du manager"),
                 ("pc-coupe", dict(CONNECTED, status="disconnected", version="4.14.10"), "Déconnecté"),
                 ("pc-arrete", dict(CONNECTED, active=False, status="", version="4.14.10"), "Arrêté"))
        ids = {}
        for index, (name, state, _label) in enumerate(cases):
            ids[name] = self.machine(hostname=name, ip=f"192.168.43.{100 + index}", state=state)[1]
        _old, old = self.machine(hostname="pc-ancien", ip="192.168.43.120", version="2.165.0")
        _late, late = self.machine(hostname="pc-retard", ip="192.168.43.121", state=CONNECTED)                       # agent 4.14.9 < 4.14.10
        _far, far = self.machine(hostname="pc-ailleurs", ip="192.168.43.122", state=dict(CONNECTED, version="4.14.10", manager="autre.lab.test"))
        with fixture.loco.app.test_request_context("/", headers={"Accept-Language": "fr"}):
            from loco.db import db
            rows = {r["hostname"]: wazuh.view(r) for r in db().execute("SELECT * FROM devices")}
        for name, _state, label in cases:
            self.assertEqual(rows[name]["label"], label, name)
        self.assertEqual((rows["pc-ok"]["level"], rows["pc-attente"]["level"], rows["pc-arrete"]["level"]), ("ok", "warn", "err"))
        self.assertIn("doit d'abord se mettre à jour", rows["pc-ancien"]["detail"])
        self.assertTrue(rows["pc-retard"]["outdated"])
        self.assertIn("4.14.9 à mettre à jour", rows["pc-retard"]["detail"])
        self.assertEqual(rows["pc-ailleurs"]["level"], "warn")
        self.assertIn("relié à autre.lab.test", rows["pc-ailleurs"]["detail"])
        html = self.page()
        for name, _state, label in cases:
            self.assertIn(label, html)
        self.assertIn("Mettre à jour", html)                             # bouton du poste en retard

    def test_only_computers_are_listed(self):
        self.machine(family="android", hostname="tel-wazuh", ip="192.168.43.130")
        self.machine(family="rhel", hostname="srv-rocky", ip="192.168.43.131")
        html = self.page()
        self.assertNotIn("tel-wazuh", html)
        self.assertIn("srv-rocky", html)

    def test_device_page_shows_the_state_and_offers_the_actions(self):
        self.configure()
        _agent, dev = self.machine(state=CONNECTED)
        html = self.admin.c.get(f"/fleet/{dev}").get_data(as_text=True)
        self.assertIn("Agent Wazuh", html)
        self.assertIn("Connecté", html)
        self.assertIn("Retirer l'agent Wazuh", html)
        reader = self.account("fiche", {"fleet.view"})
        self.assertNotIn("Agent Wazuh", reader.c.get(f"/fleet/{dev}").get_data(as_text=True))


class TestInstall(WazuhTest):
    def test_nothing_is_queued_before_wazuh_is_configured(self):
        _agent, dev = self.machine()
        self.assertEqual(self.post("/fleet/wazuh/install", {"only": str(dev)}).status_code, 302)
        self.assertEqual(self.commands(dev), [])

    def test_command_waits_for_the_package_then_carries_what_the_agent_needs(self):
        self.configure(group="servers,docker")
        agent, dev = self.machine()
        self.assertEqual(self.post("/fleet/wazuh/install", {"only": str(dev)}).status_code, 302)
        with mock.patch.object(wazuh.threading, "Thread") as thread:
            self.assertEqual(agent.checkin().get_json()["commands"], [])         # loco n'a pas encore le paquet : il le télécharge
            self.assertEqual(agent.checkin().get_json()["commands"], [])
        self.assertEqual(thread.call_count, 1)                                   # un seul téléchargement lancé
        self.assertEqual(thread.call_args.kwargs["args"], ("wazuh-agent_4.14.9-1_amd64.deb", "4.14.9"))
        self.assertEqual(self.commands(dev)[0]["state"], "queued")
        sha = self.package("wazuh-agent_4.14.9-1_amd64.deb")
        sent = agent.checkin().get_json()["commands"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["action"], "wazuh")
        self.assertEqual(sent[0]["wazuh"], {"op": "install", "manager": "wazuh.lab.test", "group": "servers,docker", "password": PASSWORD,
                                            "package": {"file": "wazuh-agent_4.14.9-1_amd64.deb", "version": "4.14.9", "sha256": sha,
                                                        "size": len(b"paquet officiel"), "url": "/agent/wazuh/wazuh-agent_4.14.9-1_amd64.deb"}})
        row = self.commands(dev)[0]
        self.assertEqual(row["state"], "sent")
        self.assertNotIn(PASSWORD, json.dumps(row))                               # le mot de passe n'est pas gardé avec la commande
        agent.post("/agent/api/result", {"id": row["id"], "state": "success", "output": "Agent Wazuh 4.14.9 installé et connecté.", "exit_code": 0,
                                         "wazuh": CONNECTED}, agent.token)
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0])["status"], "connected")
            journal = " ".join(r["detail"] for r in conn.execute("SELECT detail FROM audit"))
        self.assertNotIn(PASSWORD, journal)
        self.assertIn("pc-wazuh", journal)

    def test_each_system_gets_its_own_package(self):
        self.configure(password="")
        expected = {"debian": "wazuh-agent_4.14.9-1_amd64.deb", "rhel": "wazuh-agent-4.14.9-1.x86_64.rpm", "windows": "wazuh-agent-4.14.9-1.msi"}
        for index, (family, name) in enumerate(expected.items()):
            self.package(name)
            agent, dev = self.machine(family=family, hostname="pc-" + family, ip=f"192.168.43.{140 + index}")
            self.post("/fleet/wazuh/install", {"only": str(dev), "group": ""})
            spec = agent.checkin().get_json()["commands"][0]["wazuh"]
            self.assertEqual((spec["package"]["file"], spec["group"], spec["password"]), (name, "", ""), family)

    def test_arm_computer_gets_the_arm_package(self):
        self.configure()
        self.package("wazuh-agent_4.14.9-1_arm64.deb")
        agent = Agent(ident("debian", "pi-wazuh"), "192.168.43.150")
        agent.enroll()
        agent.checkin({"updates": [], "packages": [], "flatpaks": [], "system": {"arch": "aarch64"}, "reboot_required": False})
        dev = device(agent.uid)["id"]
        self.post("/fleet/wazuh/install", {"only": str(dev)})
        self.assertEqual(agent.checkin().get_json()["commands"][0]["wazuh"]["package"]["file"], "wazuh-agent_4.14.9-1_arm64.deb")

    def test_several_computers_at_once_and_no_duplicate(self):
        self.configure()
        ids = [self.machine(hostname=f"pc-lot-{i}", ip=f"192.168.43.{160 + i}")[1] for i in range(3)]
        self.post("/fleet/wazuh/install", {"ids": [str(i) for i in ids], "group": "docker"})
        self.post("/fleet/wazuh/install", {"ids": [str(i) for i in ids], "group": "docker"})          # déjà demandé : rien de plus
        for dev in ids:
            rows = self.commands(dev)
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0]["params"]), {"op": "install", "group": "docker"})
        self.assertIn("installation demandée", self.page())
        self.assertEqual(self.post("/fleet/wazuh/install", {"ids": ["abc"]}).status_code, 400)

    def test_an_older_loco_agent_gets_the_command_once_updated(self):
        self.configure()
        self.package("wazuh-agent_4.14.9-1_amd64.deb")
        agent, dev = self.machine(version="2.165.0")
        self.post("/fleet/wazuh/install", {"only": str(dev)})
        self.assertEqual(agent.checkin().get_json()["commands"], [])
        self.assertEqual(self.commands(dev)[0]["state"], "queued")
        agent.ident["agent_version"] = "2.166.0"
        self.assertEqual(len(agent.checkin().get_json()["commands"]), 1)

    def test_the_command_waits_while_the_feature_is_off(self):
        self.configure()
        self.package("wazuh-agent_4.14.9-1_amd64.deb")
        agent, dev = self.machine()
        self.post("/fleet/wazuh/install", {"only": str(dev)})
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertEqual(agent.checkin().get_json()["commands"], [])
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["wazuh"]'))
        self.assertEqual(len(agent.checkin().get_json()["commands"]), 1)

    def test_a_failed_download_fails_the_command_with_its_reason(self):
        self.configure()
        agent, dev = self.machine()
        self.post("/fleet/wazuh/install", {"only": str(dev)})
        wazuh._STATE["wazuh-agent_4.14.9-1_amd64.deb"] = ("empreinte SHA-512 différente", time.time())
        self.assertEqual(agent.checkin().get_json()["commands"], [])
        row = self.commands(dev)[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("empreinte SHA-512 différente", row["output"])

    def test_removal_needs_no_package_and_no_password(self):
        self.configure()
        agent, dev = self.machine(state=CONNECTED)
        location = self.post("/fleet/wazuh/remove", {"only": str(dev), "from": "device"}).headers["Location"]
        self.assertEqual(urllib.parse.urlsplit(location).path, f"/fleet/{dev}")       # Werkzeug ancien : adresse absolue
        sent = agent.checkin().get_json()["commands"]
        self.assertEqual(sent[0]["wazuh"], {"op": "remove"})
        self.assertRegex(self.admin.c.get(f"/fleet/{dev}").get_data(as_text=True), "Retrait de l(&#39;|')agent Wazuh")


class TestManagerHost(WazuhTest):
    def test_the_manager_host_is_recognised_and_left_alone(self):
        self.configure()
        _agent, dev = self.machine(hostname="em-wazuh", state=MANAGER)
        self.machine(hostname="pc-equipe", ip="192.168.43.91", state=CONNECTED)
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT wazuh FROM devices WHERE id=?", (dev,)).fetchone()[0]),
                             {"installed": False, "role": "manager", "version": "4.14.9", "active": True})
        html = self.page()
        self.assertIn("Manager Wazuh", html)
        self.assertNotIn("Arrêté", html)
        self.assertRegex(html, r'name="ids" value="%d" data-pick aria-label="em-wazuh" disabled' % dev)
        self.assertRegex(html, r'Postes équipés</div><div class="v">1<span class="v-unit">/ 1</span>')   # le manager n'est pas compté
        for path in ("/fleet/wazuh/install", "/fleet/wazuh/remove"):
            self.assertEqual(self.post(path, {"only": str(dev)}).status_code, 302)
        self.assertEqual(self.commands(dev), [])
        page = self.admin.c.get(f"/fleet/{dev}").get_data(as_text=True)
        self.assertIn("Manager Wazuh", page)
        self.assertNotIn("Retirer l'agent Wazuh", page)
        self.assertNotIn("Installer l'agent Wazuh", page)

    def test_a_stopped_manager_is_said_so(self):
        _agent, dev = self.machine(hostname="em-wazuh", state=dict(MANAGER, active=False))
        with fixture.loco.app.test_request_context("/", headers={"Accept-Language": "fr"}):
            from loco.db import db
            view = wazuh.view(db().execute("SELECT * FROM devices WHERE id=?", (dev,)).fetchone())
        self.assertEqual((view["label"], view["level"], view["installed"], view["server"]), ("Manager Wazuh", "err", False, True))
        self.assertIn("le service du manager Wazuh ne tourne pas", view["detail"])

    def test_a_command_queued_before_the_host_was_known_as_manager_is_refused(self):
        self.configure()
        agent, dev = self.machine(hostname="em-wazuh")
        self.post("/fleet/wazuh/remove", {"only": str(dev)})
        agent.post("/agent/api/checkin", dict(agent.ident, wazuh=MANAGER), agent.token)
        self.assertEqual(agent.checkin().get_json()["commands"], [])
        [cmd] = self.commands(dev)
        self.assertEqual(cmd["state"], "failed")
        self.assertIn("manager Wazuh", cmd["output"])


def run_forced_command(line, sent):
    """Ce que fait sshd de la ligne d'authorized_keys : commande imposée (entre guillemets, \\" pour un guillemet), lancée
    par le shell du compte avec SSH_ORIGINAL_COMMAND = ce que le client a envoyé. manage_agents est remplacé par un
    script qui écrit ses arguments."""
    command = re.search(r'command="((?:[^"\\]|\\.)*)"', line).group(1).replace('\\"', '"')
    with tempfile.TemporaryDirectory() as work:
        probe = os.path.join(work, "manage_agents")
        Path(probe).write_text('#!/bin/sh\nprintf "[%s]" "$@"\n')
        os.chmod(probe, 0o755)
        done = subprocess.run(["sh", "-c", command.replace(wazuh.MANAGE_AGENTS, probe)], env={"PATH": os.environ["PATH"],
                              "SSH_ORIGINAL_COMMAND": sent}, cwd=work, capture_output=True, text=True, timeout=20)
        return done.stdout, sorted(os.listdir(work))


class TestUnenroll(WazuhTest):
    def setUp(self):
        super().setUp()
        keys = tempfile.mkdtemp(prefix="loco-wazuh-key-")
        self.addCleanup(shutil.rmtree, keys, True)
        for patch in (mock.patch.object(wazuh, "KEY_DIR", keys), mock.patch.object(wazuh, "KEY_PATH", os.path.join(keys, "id_ed25519")),
                      mock.patch.object(wazuh, "unenroll_soon", wazuh._unenroll)):           # retrait de l'entrée sans fil d'exécution
            patch.start()
            self.addCleanup(patch.stop)
        self.configure()

    def cfg(self):
        with transaction() as conn:
            return wazuh.load(conn)

    def link(self):
        with transaction() as conn:
            wazuh._save(conn, dict(wazuh.load(conn), host_key="ssh-ed25519 " + HOST_KEY))

    def removal(self, reported=None, state="success", device_state=CONNECTED, ssh=(0, REMOVED)):
        """Retrait demandé, remis à l'agent, puis son compte rendu : (sortie de la commande, appels ssh)."""
        agent, dev = self.machine(state=device_state)
        self.post("/fleet/wazuh/remove", {"only": str(dev)})
        [sent] = agent.checkin().get_json()["commands"]
        with mock.patch.object(wazuh, "_ssh", return_value=ssh) as call:
            data = {"id": sent["id"], "state": state, "output": "Agent Wazuh retiré du poste.", "exit_code": 0 if state == "success" else 1}
            if reported is not False:
                data["wazuh"] = reported or {"installed": False}
            self.assertEqual(agent.post("/agent/api/result", data, agent.token).status_code, 200)
        return self.commands(dev)[-1]["output"], [c.args[1] for c in call.call_args_list]

    def audit(self):
        with transaction() as conn:
            return [dict(r) for r in conn.execute("SELECT username, action, detail FROM audit WHERE action LIKE 'parc.wazuh.%'")]

    def test_the_authorized_line_lets_only_the_digits_through(self):
        line = wazuh.authorized_line()
        self.assertTrue(line.startswith('restrict,command="/var/ossec/bin/manage_agents -r '))
        self.assertTrue(line.endswith(wazuh.public_key()))
        self.assertEqual(run_forced_command(line, "/var/ossec/bin/manage_agents -r 003")[0], "[-r][003]")
        out, files = run_forced_command(line, '$(touch pwn); `touch pwn2`; * 7 > fichier')
        self.assertEqual((out, files), ("[-r][27]", ["manage_agents"]))                 # rien n'est lancé ni écrit
        self.assertEqual(run_forced_command(line, "")[0], "[-r]")

    def test_the_key_for_the_manager_is_its_own(self):
        from loco import homeassistant
        self.assertNotEqual(wazuh.public_key(), homeassistant.public_key())
        self.assertEqual(stat.S_IMODE(os.stat(wazuh.KEY_PATH).st_mode), 0o600)

    def test_access_is_set_in_two_steps_with_the_host_key_confirmed(self):
        page = self.page()
        self.assertIn("Retrait des entrées du manager", page)
        self.assertIn("restrict,command=", page)
        with mock.patch("loco.homeassistant.probe_host_key", return_value=("ssh-ed25519", HOST_KEY)) as probe:
            self.post("/fleet/wazuh/access", {"ssh_user": "root", "ssh_port": "2222"})
        self.assertEqual(probe.call_args.args[:2], ("wazuh.lab.test", 2222))
        self.assertFalse(wazuh.linked(self.cfg()))                    # rien d'enregistré avant la confirmation
        page = self.page()
        from loco.homeassistant import fingerprint
        self.assertIn(fingerprint(HOST_KEY), page)
        self.assertEqual(self.post("/fleet/wazuh/access/confirm", {"nonce": "0" * 16}).status_code, 400)
        with mock.patch("loco.homeassistant.probe_host_key", return_value=("ssh-ed25519", HOST_KEY)):
            self.post("/fleet/wazuh/access", {"ssh_user": "root", "ssh_port": "2222"})
        nonce = self.page().split('name="nonce" value="')[1].split('"')[0]
        with mock.patch.object(wazuh, "_ssh", return_value=(0, ABSENT % wazuh.PROBE_ID)) as call:
            self.post("/fleet/wazuh/access/confirm", {"nonce": nonce})
        self.assertEqual(call.call_args.args[1], "echo loco-shell-ouvert; /var/ossec/bin/manage_agents -r 99999999")
        cfg = self.cfg()
        self.assertEqual((cfg["ssh_user"], cfg["ssh_port"], cfg["host_key"]), ("root", "2222", "ssh-ed25519 " + HOST_KEY))
        self.assertIn(("admin", "parc.wazuh.acces"), [(a["username"], a["action"]) for a in self.audit()])
        page = self.page()
        self.assertIn("accès réglé", page)
        self.assertIn("root@wazuh.lab.test:2222", page)
        self.assertIn("puis loco efface son entrée sur le manager", page)

    def test_the_access_test_tells_a_full_shell_from_the_restricted_key(self):
        self.link()
        cfg = self.cfg()
        with mock.patch.object(wazuh, "_ssh", return_value=(0, ABSENT % wazuh.PROBE_ID)):
            self.assertEqual(wazuh.check(cfg)[0], "ok")
        with mock.patch.object(wazuh, "_ssh", return_value=(0, "loco-shell-ouvert\n" + ABSENT % wazuh.PROBE_ID)):
            self.assertEqual(wazuh.check(cfg)[0], "warn")
        with mock.patch.object(wazuh, "_ssh", return_value=(0, EMPTY)):                     # manager sans aucun agent inscrit
            self.assertEqual(wazuh.check(cfg)[0], "ok")
        with mock.patch.object(wazuh, "_ssh", return_value=(255, "root@wazuh.lab.test: Permission denied (publickey).")), \
                fixture.loco.app.test_request_context("/", headers={"Accept-Language": "fr"}):
            with self.assertRaisesRegex(wazuh.LinkError, "refuse la clé de loco"):
                wazuh.check(cfg)

    def test_ssh_uses_loco_key_and_the_confirmed_host_key_only(self):
        self.link()
        seen = {}

        def fake(argv, **kwargs):
            known = [a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile=")][0]
            seen.update(argv=argv, known=Path(known).read_text(), path=known)
            return subprocess.CompletedProcess(argv, 0, REMOVED.encode())
        with mock.patch("loco.homeassistant.ssh_binary", return_value="/usr/bin/ssh"), mock.patch.object(wazuh.subprocess, "run", fake):
            self.assertIn("003", wazuh.unenroll(self.cfg(), "003"))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("-i") + 1], wazuh.KEY_PATH)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(argv[-3:], ["--", "wazuh.lab.test", "/var/ossec/bin/manage_agents -r 003"])
        self.assertEqual(seen["known"], "wazuh.lab.test ssh-ed25519 %s\n" % HOST_KEY)
        self.assertFalse(os.path.exists(seen["path"]))
        with self.assertRaises(wazuh.LinkError):
            wazuh.unenroll(self.cfg(), "000")                     # jamais le manager lui-même

    def test_removal_also_removes_the_entry_from_the_manager(self):
        self.link()
        output, calls = self.removal()
        self.assertEqual(calls, ["/var/ossec/bin/manage_agents -r 003"])
        self.assertIn("Agent Wazuh retiré du poste.\nEntrée 003 retirée du manager Wazuh.", output)
        [entry] = [a for a in self.audit() if a["action"] == "parc.wazuh.desenrolement"]
        self.assertEqual(entry["username"], "admin")
        self.assertIn("pc-wazuh", entry["detail"])

    def test_an_entry_already_gone_is_not_an_error(self):
        self.link()
        output, _calls = self.removal(ssh=(0, ABSENT % "003"))
        self.assertIn("Entrée 003 déjà absente du manager Wazuh.", output)
        with transaction() as conn:
            conn.execute("DELETE FROM devices")
        output, _calls = self.removal(ssh=(0, EMPTY))
        self.assertIn("Entrée 003 déjà absente du manager Wazuh.", output)

    def test_a_refused_access_is_reported_with_the_removal(self):
        self.link()
        output, _calls = self.removal(ssh=(255, "root@wazuh.lab.test: Permission denied (publickey)."))
        self.assertIn("Entrée 003 non retirée du manager : Le manager refuse la clé de loco", output)

    def test_without_access_the_entry_stays_and_the_removal_says_so(self):
        output, calls = self.removal()
        self.assertEqual(calls, [])
        self.assertIn("Entrée 003 laissée sur le manager", output)
        self.assertIn("Son entrée reste sur le manager", self.page())

    def test_nothing_is_removed_from_the_manager_when_unsure(self):
        self.link()
        cases = ((dict(reported={"installed": True, "id": "003", "manager": "wazuh.lab.test"}), "encore présent sur le poste"),
                 (dict(reported=MANAGER), "encore présent sur le poste"),
                 (dict(reported=False), "encore présent sur le poste"),
                 (dict(device_state=dict(CONNECTED, manager="autre.lab.test")), "relié à autre.lab.test"),
                 (dict(device_state=dict(CONNECTED, manager="")), "relié à ?"),
                 (dict(device_state=dict(CONNECTED, id="000")), "Aucune entrée connue"),
                 (dict(device_state=dict(CONNECTED, id="")), "Aucune entrée connue"),
                 (dict(state="failed"), None))
        for index, (kwargs, note) in enumerate(cases):
            with transaction() as conn:
                conn.execute("DELETE FROM devices")
            output, calls = self.removal(**kwargs)
            self.assertEqual(calls, [], kwargs)
            if note:
                self.assertIn(note, output, kwargs)
            else:
                self.assertEqual(output, "Agent Wazuh retiré du poste.")

    def test_an_installation_never_touches_the_manager(self):
        self.link()
        agent, dev = self.machine(state=CONNECTED)
        self.package("wazuh-agent_4.14.9-1_amd64.deb")
        self.post("/fleet/wazuh/install", {"only": str(dev)})
        [sent] = agent.checkin().get_json()["commands"]
        with mock.patch.object(wazuh, "_ssh") as call:
            agent.post("/agent/api/result", {"id": sent["id"], "state": "success", "output": "ok", "exit_code": 0,
                                             "wazuh": {"installed": False}}, agent.token)
        call.assert_not_called()

    def test_a_new_manager_address_needs_its_host_key_confirmed_again(self):
        self.link()
        self.configure(manager="wazuh2.lab.test")
        self.assertEqual(self.cfg()["host_key"], "")
        self.link()
        self.configure(manager="WAZUH2.lab.test", group="docker")                  # même adresse : l'accès reste
        self.assertTrue(self.cfg()["host_key"])

    def test_access_can_be_removed_and_needs_the_settings_right(self):
        self.link()
        installer = self.account("poseur", {"wazuh.view", "wazuh.install", "fleet.view"})
        for path in ("/fleet/wazuh/access", "/fleet/wazuh/access/test", "/fleet/wazuh/access/delete", "/fleet/wazuh/access/confirm"):
            self.assertEqual(self.post(path, {"ssh_user": "root", "ssh_port": "22"}, browser=installer).status_code, 403, path)
        self.assertNotIn("Retrait des entrées du manager", self.page(installer))
        self.post("/fleet/wazuh/access/delete")
        self.assertFalse(wazuh.linked(self.cfg()))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TestPackages(WazuhTest):
    def serve(self, files):
        def urlopen(request, timeout=0):
            url = request.full_url
            if url not in files:
                raise OSError("404 " + url)
            return FakeResponse(files[url])
        return mock.patch.object(wazuh.urllib.request, "urlopen", urlopen)

    def test_official_package_is_kept_only_with_the_published_checksum(self):
        name, content = "wazuh-agent-4.14.9-1.msi", b"MSI officiel" * 1000
        base = "https://packages.wazuh.com/4.x"
        good = {f"{base}/windows/{name}": content,
                f"{base}/checksums/wazuh/4.14.9/{name}.sha512": (hashlib.sha512(content).hexdigest() + "  dist/" + name + "\n").encode()}
        with self.serve(dict(good, **{f"{base}/checksums/wazuh/4.14.9/{name}.sha512": b"0" * 128 + b"  x\n"})):
            wazuh._fetch_package(name, "4.14.9")
        self.assertFalse(os.path.exists(os.path.join(self.folder, name)))
        self.assertIn("SHA-512", wazuh._STATE[name][0])
        self.assertEqual([n for n in os.listdir(self.folder) if n.startswith(".dl-")], [])
        with self.serve(good):
            wazuh._fetch_package(name, "4.14.9")
        self.assertNotIn(name, wazuh._STATE)
        self.assertEqual(wazuh.package_info(name), {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)})
        self.assertEqual(wazuh.package_state(name, "4.14.9"), "ready")

    def test_urls_follow_the_official_layout(self):
        self.assertEqual(wazuh.package_url("wazuh-agent_4.14.9-1_arm64.deb", "4.14.9"),
                         "https://packages.wazuh.com/4.x/apt/pool/main/w/wazuh-agent/wazuh-agent_4.14.9-1_arm64.deb")
        self.assertEqual(wazuh.package_url("wazuh-agent-5.0.1-1.aarch64.rpm", "5.0.1"), "https://packages.wazuh.com/5.x/yum/wazuh-agent-5.0.1-1.aarch64.rpm")
        self.assertEqual(wazuh.package_name("rhel", "arm64", "4.14.9"), "wazuh-agent-4.14.9-1.aarch64.rpm")
        self.assertNotEqual(wazuh.package_state("../../etc/passwd", "4.14.9"), "ready")
        self.assertNotEqual(wazuh.package_state("wazuh-agent-4.14.9-1.msi", "4.14.9; rm"), "ready")

    def test_package_is_served_like_the_agent_files_and_nothing_else(self):
        """Comme le fait l'agent loco : une simple requête, sans session ni jeton (paquet public, vérifié par empreinte)."""
        self.package("wazuh-agent-4.14.9-1.msi", b"contenu")
        c = fixture.loco.app.test_client()
        r = c.get("/agent/wazuh/wazuh-agent-4.14.9-1.msi")
        self.assertEqual((r.status_code, r.get_data()), (200, b"contenu"))
        r.close()
        for name in ("wazuh-agent-9.9.9-1.msi", "autre.msi", "wazuh-agent-4.14.9-1.msi.json", ".dl-abc"):
            self.assertEqual(c.get("/agent/wazuh/" + name).status_code, 404, name)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertNotEqual(c.get("/agent/wazuh/wazuh-agent-4.14.9-1.msi").status_code, 200)       # fonctionnalité coupée

    def test_the_linux_agent_fetches_the_package_the_way_the_server_serves_it(self):
        """Bout en bout sans réseau : l'agent Linux télécharge par sa fonction réelle, branchée sur le portail de test."""
        content = b"paquet officiel"
        sha = self.package("wazuh-agent_4.14.9-1_amd64.deb", content)
        c = fixture.loco.app.test_client()

        def download(server, path, timeout=120):
            r = c.get(path, headers={"User-Agent": "loco-client/2.166.0"})         # ce que fait download() : ni jeton ni session
            self.assertEqual(r.status_code, 200, path)
            data = r.get_data()
            r.close()
            return data
        seen = {}

        def installer(command, **kwargs):
            seen["package"] = Path(command[-1]).read_bytes()
            return subprocess.CompletedProcess(command, 1, stdout="arrêt voulu du test\n")
        cmd = {"id": 1, "action": "wazuh", "wazuh": {"op": "install", "manager": "wazuh.lab.test", "group": "", "password": "",
               "package": {"file": "wazuh-agent_4.14.9-1_amd64.deb", "sha256": sha, "url": "/agent/wazuh/wazuh-agent_4.14.9-1_amd64.deb"}}}
        with mock.patch.object(agent, "download", download), mock.patch.object(agent.subprocess, "run", installer), \
                mock.patch.object(agent, "family", return_value="debian"):
            code, _out = agent.wazuh_apply("https://loco.test:8443", cmd)
        self.assertEqual((code, seen["package"]), (1, content))

    def test_versions_are_read_from_the_official_index(self):
        index = ("Package: wazuh-manager\nVersion: 4.99.0-1\n\nPackage: wazuh-agent\nVersion: 4.14.9-1\n\n"
                 "Package: wazuh-agent\nVersion: 4.14.10-1\n\nPackage: wazuh-agent\nVersion: 4.2.7-1\n\nPackage: wazuh-agent\nVersion: pas-une-version\n")
        files = {"https://packages.wazuh.com/4.x/apt/dists/stable/main/binary-amd64/Packages.gz": gzip.compress(index.encode())}
        with self.serve(files):
            self.assertEqual(wazuh.fetch_versions(), ["4.14.10", "4.14.9", "4.2.7"])
        with self.serve({}), self.assertRaises(ValueError):
            wazuh.fetch_versions()

    def test_changing_the_version_drops_the_packages_of_the_old_one(self):
        self.configure()
        self.package("wazuh-agent_4.14.9-1_amd64.deb")
        self.configure(version="4.14.10", password=wazuh.MASK)
        self.assertEqual(os.listdir(self.folder), [])


# --------------------------------------------------------------------------- agent Linux

spec = importlib.util.spec_from_file_location("loco_client_wazuh", str(ROOT / "portal" / "agents" / "loco-client.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


@unittest.skipUnless(os.name == "posix", "Agent Linux")
class TestLinuxAgent(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="loco-ossec-")
        self.addCleanup(shutil.rmtree, self.home, True)
        for patch in (mock.patch.object(agent, "WAZUH_HOME", self.home), mock.patch.object(agent, "LANG", "fr"),
                      mock.patch.object(agent, "family", return_value="debian"), mock.patch.object(agent.time, "sleep")):
            patch.start()
            self.addCleanup(patch.stop)
        self.calls = []
        self.kind = "agent"

    def installed(self, status="connected", manager="ancien.lab.test", running=True):
        """Arborescence d'un agent Wazuh installé (comme le pose le paquet officiel)."""
        for folder in ("bin", "etc", "var/run"):
            os.makedirs(os.path.join(self.home, folder), exist_ok=True)
        Path(self.home, "bin/wazuh-control").write_text("#!/bin/sh\n")
        Path(self.home, "etc/ossec.conf").write_text(
            "<ossec_config>\n  <client>\n    <server>\n      <address>%s</address>\n      <port>1514</port>\n    </server>\n"
            "    <enrollment>\n      <enabled>yes</enabled>\n    </enrollment>\n  </client>\n  <syscheck>\n    <address>intact</address>\n  </syscheck>\n</ossec_config>\n" % manager)
        Path(self.home, "etc/client.keys").write_text("007 pc-wazuh any 9f8e7d6c5b4a39281706f5e4d3c2b1a0cle-secrete\n")
        Path(self.home, "var/run/wazuh-agentd.state").write_text("# State file for wazuh-agentd\nstatus='%s'\nlast_keepalive=''\n" % status)
        Path(self.home, "var/run", "wazuh-agentd-%d.pid" % (os.getpid() if running else 4194000)).write_text("")

    def fake_run(self, cmd, timeout=7200):
        self.calls.append(cmd)
        return (0, 'WAZUH_VERSION="v4.14.9"\nWAZUH_REVISION="rc2"\nWAZUH_TYPE="%s"\n' % self.kind) if cmd[1:] == ["info"] else (0, "")

    def test_nothing_installed(self):
        self.assertEqual(agent.wazuh_state(), {"installed": False})

    def test_state_is_read_on_the_spot_without_the_key(self):
        self.installed()
        with mock.patch.object(agent, "run", self.fake_run):
            state = agent.wazuh_state()
        self.assertEqual(state, {"installed": True, "version": "4.14.9", "active": True, "status": "connected", "manager": "ancien.lab.test",
                                 "id": "007", "name": "pc-wazuh"})
        self.assertNotIn("cle-secrete", json.dumps(state))

    def test_a_stopped_agent_has_no_link_whatever_the_state_file_says(self):
        self.installed(running=False)
        with mock.patch.object(agent, "run", self.fake_run):
            state = agent.wazuh_state()
        self.assertEqual((state["active"], state["status"]), (False, ""))

    def test_manager_address_is_replaced_in_the_client_section_only(self):
        self.installed()
        self.assertTrue(agent.wazuh_set_manager("wazuh.lab.test"))
        text = Path(self.home, "etc/ossec.conf").read_text()
        self.assertIn("<address>wazuh.lab.test</address>", text)
        self.assertIn("<address>intact</address>", text)
        self.assertFalse(agent.wazuh_set_manager("wazuh.lab.test"))               # déjà la bonne : rien à écrire

    def command(self, content=b"paquet", **spec):
        base = {"op": "install", "manager": "wazuh.lab.test", "group": "servers,docker", "password": PASSWORD,
                "package": {"file": "wazuh-agent_4.14.9-1_amd64.deb", "version": "4.14.9", "sha256": hashlib.sha256(b"paquet").hexdigest(),
                            "size": 6, "url": "/agent/wazuh/wazuh-agent_4.14.9-1_amd64.deb"}}
        base.update(spec)
        return {"id": 1, "action": "wazuh", "wazuh": base}, content

    def apply(self, cmd, content, install_code=0):
        """Lance wazuh_apply avec un téléchargement et des commandes simulés ; le paquet « installé » pose l'arborescence."""
        seen = {}

        def installer(command, **kwargs):
            seen["command"], seen["env"] = command, kwargs["env"]
            seen["package"] = Path(command[-1]).read_bytes() if os.path.exists(command[-1]) else None
            if install_code == 0:
                self.installed(status="pending", manager=kwargs["env"]["WAZUH_MANAGER"])
            return subprocess.CompletedProcess(command, install_code, stdout="(Reading database ... 5%\n(Reading database ... 100%\nSetting up wazuh-agent\n")

        def download(server, path, timeout=120):
            seen["download"] = (server, path)
            return content

        with mock.patch.object(agent, "download", download), mock.patch.object(agent.subprocess, "run", installer), \
                mock.patch.object(agent, "run", self.fake_run):
            code, out = agent.wazuh_apply("https://loco.test:8443", cmd)
        return code, out, seen

    def test_install_configures_through_the_package_and_hides_the_password(self):
        cmd, content = self.command()
        code, out, seen = self.apply(cmd, content)
        self.assertEqual(code, 0, out)
        self.assertEqual(seen["download"], ("https://loco.test:8443", "/agent/wazuh/wazuh-agent_4.14.9-1_amd64.deb"))
        self.assertEqual(seen["package"], b"paquet")
        self.assertEqual(seen["command"][0], "apt-get")
        self.assertEqual((seen["env"]["WAZUH_MANAGER"], seen["env"]["WAZUH_AGENT_GROUP"]), ("wazuh.lab.test", "servers,docker"))
        everything = json.dumps([seen["command"], seen["env"], self.calls, out])
        self.assertNotIn(PASSWORD, everything)                                    # ni argument, ni environnement, ni sortie
        secret = Path(self.home, "etc/authd.pass")
        self.assertEqual(secret.read_text(), PASSWORD + "\n")
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o640)
        self.assertIn(["systemctl", "restart", "wazuh-agent"], self.calls)
        self.assertIn("pas encore connecté au manager wazuh.lab.test", out)
        self.assertNotIn("Reading database", out)                                 # bruit d'apt écarté du compte rendu
        self.assertFalse(os.path.exists(os.path.dirname(seen["command"][-1])))     # dossier de travail retiré

    def test_a_refused_enrollment_is_explained_by_the_agent_log(self):
        cmd, content = self.command()
        os.makedirs(os.path.join(self.home, "logs"))
        Path(self.home, "logs/ossec.log").write_text(
            "2026/11/16 09:30:01 wazuh-agentd: INFO: Requesting a key from server: wazuh.lab.test\n"
            "2026/11/16 09:30:17 wazuh-agentd: ERROR: Duplicate agent name: pc-wazuh. Unable to add agent (from manager)\n"
            "2026/11/16 09:30:27 wazuh-agentd: ERROR: Duplicate agent name: pc-wazuh. Unable to add agent (from manager)\n")
        code, out, _seen = self.apply(cmd, content)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("Duplicate agent name: pc-wazuh"), 1)             # la même erreur n'est dite qu'une fois
        self.assertIn("Journal de l'agent Wazuh : wazuh-agentd: ERROR: Duplicate agent name", out)

    def test_install_without_password_or_group(self):
        cmd, content = self.command(password="", group="")
        code, _out, seen = self.apply(cmd, content)
        self.assertEqual(code, 0)
        self.assertNotIn("WAZUH_AGENT_GROUP", seen["env"])
        self.assertFalse(Path(self.home, "etc/authd.pass").exists())

    def test_a_password_left_from_before_is_removed_when_loco_has_none(self):
        """Mot de passe retiré des réglages : un manager qui n'en attend pas refuse l'agent qui en envoie un
        (« Invalid request for new agent »). Réappliquer doit donc retirer l'ancien authd.pass du poste."""
        self.installed()
        Path(self.home, "etc/authd.pass").write_text("ancien mot de passe\n")
        cmd, content = self.command(password="")
        with mock.patch.object(agent, "download", lambda *a, **k: content), \
                mock.patch.object(agent.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, stdout="")), \
                mock.patch.object(agent, "run", self.fake_run):
            code, out = agent.wazuh_apply("https://loco.test:8443", cmd)
        self.assertEqual(code, 0, out)
        self.assertFalse(Path(self.home, "etc/authd.pass").exists())
        self.assertIn("Mot de passe d'enrôlement retiré du poste", out)

    def test_an_existing_agent_is_pointed_to_the_configured_manager(self):
        self.installed(manager="ancien.lab.test")

        def keep_manager(command, **kwargs):                                       # mise à jour du paquet : ossec.conf est gardé
            return subprocess.CompletedProcess(command, 0, stdout="")
        cmd, content = self.command()
        with mock.patch.object(agent, "download", lambda *a, **k: content), mock.patch.object(agent.subprocess, "run", keep_manager), \
                mock.patch.object(agent, "run", self.fake_run):
            code, out = agent.wazuh_apply("https://loco.test:8443", cmd)
        self.assertEqual(code, 0, out)
        self.assertIn("<address>wazuh.lab.test</address>", Path(self.home, "etc/ossec.conf").read_text())
        self.assertIn("installé et connecté au manager wazuh.lab.test", out)
        self.assertIn("agent 007", out)

    def test_wrong_checksum_installs_nothing(self):
        cmd, _content = self.command()
        code, out, seen = self.apply(cmd, b"autre chose")
        self.assertEqual(code, 1)
        self.assertIn("SHA-256", out)
        self.assertNotIn("command", seen)

    def test_incomplete_or_hostile_command_is_refused(self):
        for spec in ({"manager": "wazuh.lab.test; reboot"}, {"group": "a b"}, {"manager": ""},
                     {"package": {"file": "../../evil.deb", "url": "/agent/wazuh/x", "sha256": "0"}},
                     {"package": {"file": "wazuh-agent_4.14.9-1_amd64.deb", "url": "https://ailleurs.test/x.deb", "sha256": "0"}}, {"op": "autre"}):
            with self.subTest(spec=spec):
                cmd, content = self.command(**spec)
                code, _out, seen = self.apply(cmd, content)
                self.assertEqual(code, 1)
                self.assertNotIn("download", seen)

    def test_failed_installation_reports_the_package_manager_output(self):
        cmd, content = self.command()
        code, out, _seen = self.apply(cmd, content, install_code=100)
        self.assertEqual(code, 100)
        self.assertIn("Setting up wazuh-agent", out)
        self.assertNotIn(["systemctl", "restart", "wazuh-agent"], self.calls)

    def test_removal_purges_the_package(self):
        self.installed()
        with mock.patch.object(agent, "run", self.fake_run):
            code, out = agent.wazuh_apply("https://loco.test:8443", {"id": 2, "action": "wazuh", "wazuh": {"op": "remove"}})
        self.assertEqual(code, 0)
        self.assertIn(["apt-get", "-y", "-q", "-o", "DPkg::Lock::Timeout=300", "purge", "wazuh-agent"], self.calls)
        self.assertIn("retiré", out)

    def test_removal_of_an_absent_agent_is_a_success(self):
        shutil.rmtree(self.home)
        with mock.patch.object(agent, "run", self.fake_run):
            code, _out = agent.wazuh_apply("https://loco.test:8443", {"id": 2, "action": "wazuh", "wazuh": {"op": "remove"}})
        self.assertEqual((code, self.calls), (0, []))

    def test_red_hat_family_uses_dnf(self):
        cmd, content = self.command(package={"file": "wazuh-agent-4.14.9-1.x86_64.rpm", "version": "4.14.9", "sha256": hashlib.sha256(b"paquet").hexdigest(),
                                             "size": 6, "url": "/agent/wazuh/wazuh-agent-4.14.9-1.x86_64.rpm"})
        with mock.patch.object(agent, "family", return_value="rhel"), mock.patch.object(agent.shutil, "which", return_value="/usr/bin/dnf"):
            code, _out, seen = self.apply(cmd, content)
        self.assertEqual(code, 0)
        self.assertEqual(seen["command"][:3], ["dnf", "-y", "install"])

    def test_on_the_manager_the_state_says_so_without_reading_the_enrolled_agents(self):
        self.installed(running=False)
        Path(self.home, "var/run", "wazuh-remoted-%d.pid" % os.getpid()).write_text("")
        self.kind = "server"
        with mock.patch.object(agent, "run", self.fake_run):
            state = agent.wazuh_state()
        self.assertEqual(state, {"installed": False, "role": "manager", "version": "4.14.9", "active": True})
        self.assertNotIn("pc-wazuh", json.dumps(state))                 # client.keys du manager : les agents inscrits

    def test_a_manager_is_recognised_even_without_the_version_command(self):
        self.installed(running=False)
        Path(self.home, "bin/wazuh-analysisd").write_text("")
        with mock.patch.object(agent, "run", return_value=(1, "")):
            state = agent.wazuh_state()
        self.assertEqual((state.get("role"), state["active"]), ("manager", False))

    def test_nothing_is_installed_or_removed_on_the_manager(self):
        self.installed()
        self.kind = "server"
        for op in ("install", "remove"):
            with mock.patch.object(agent, "run", self.fake_run):
                code, out = agent.wazuh_apply("https://loco.test:8443", {"id": 2, "action": "wazuh", "wazuh": {"op": op}})
            self.assertEqual(code, 1)
            self.assertIn("manager Wazuh", out)
        self.assertEqual([c for c in self.calls if c[1:] != ["info"]], [])

    def test_identity_carries_the_state(self):
        with mock.patch.object(agent, "run", self.fake_run):
            self.assertEqual(agent.identity()["wazuh"], {"installed": False})


# --------------------------------------------------------------------------- agent Windows (lecture de l'état)

@unittest.skipUnless(shutil.which("mcs") and shutil.which("mono"), "Compilateur .NET/Mono (job MSI)")
class TestWindowsAgentState(unittest.TestCase):
    HARNESS = r'''
using System; using System.IO; using Loco;
class Probe {
    static void Main(string[] args) {
        string home = args[0];
        Console.WriteLine(WazuhState.Installed(home));
        var absent = WazuhState.Read(Path.Combine(home, "ailleurs"), false, "");
        Console.WriteLine(absent["installed"] + "|" + absent.Count);
        foreach (bool running in new[] { true, false }) {
            var s = WazuhState.Read(home, running, "v4.14.9");
            Console.WriteLine(s["installed"] + "|" + s["version"] + "|" + s["active"] + "|" + s["status"] + "|" + s["manager"] + "|" + s["id"] + "|" + s["name"]);
        }
        string conf = File.ReadAllText(Path.Combine(home, "ossec.conf"));
        string changed = WazuhState.WithManager(conf, "wazuh.lab.test");
        Console.WriteLine(changed.Contains("<address>wazuh.lab.test</address>") + "|" + changed.Contains("<address>intact</address>") + "|" + (WazuhState.WithManager(changed, "wazuh.lab.test") == changed));
        Console.WriteLine(string.Join("#", WazuhState.LastErrors(home, 2)));
        Console.WriteLine(WazuhState.Manager.IsMatch("wazuh.lab.test") + "|" + WazuhState.Manager.IsMatch("a\" /x") + "|" + WazuhState.Group.IsMatch("servers,docker") + "|" + WazuhState.Group.IsMatch("a b")
            + "|" + WazuhState.Package.IsMatch("wazuh-agent-4.14.9-1.msi") + "|" + WazuhState.Package.IsMatch("..\\evil.msi"));
    }
}'''

    def test_state_is_read_from_the_agent_folder_without_the_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            home = path / "ossec-agent"
            home.mkdir()
            (home / "wazuh-agent.exe").write_bytes(b"MZ")
            (home / "wazuh-agent.state").write_text("# State file for wazuh-agent\r\nstatus='connected'\r\nlast_keepalive=''\r\n")
            (home / "ossec.conf").write_text("<ossec_config>\r\n  <client>\r\n    <server>\r\n      <address>ancien.lab.test</address>\r\n    </server>\r\n  </client>\r\n"
                                              "  <syscheck><address>intact</address></syscheck>\r\n</ossec_config>\r\n")
            (home / "client.keys").write_text("012 PC-COMPTA any 0123456789abcdefcle-secrete\r\n")
            (home / "ossec.log").write_text("2026/11/16 09:30:01 wazuh-agent: INFO: Requesting a key\r\n2026/11/16 09:30:17 wazuh-agent: ERROR: Invalid password (from manager)\r\n"
                                            "2026/11/16 09:30:27 wazuh-agent: ERROR: Invalid password (from manager)\r\n")
            (path / "Probe.cs").write_text(self.HARNESS)
            subprocess.run(["mcs", "-warnaserror+", "-sdk:4.6.2", "-r:System.Core.dll", "-out:" + str(path / "Probe.exe"),
                            str(ROOT / "windows/client/WazuhState.cs"), str(path / "Probe.cs")], check=True, capture_output=True)
            out = subprocess.run(["mono", str(path / "Probe.exe"), str(home)], check=True, capture_output=True, text=True).stdout
        self.assertNotIn("cle-secrete", out)
        self.assertEqual(out.splitlines(), ["True", "False|1", "True|4.14.9|True|connected|ancien.lab.test|012|PC-COMPTA",
                                            "True|4.14.9|False||ancien.lab.test|012|PC-COMPTA", "True|True|True", "wazuh-agent: ERROR: Invalid password (from manager)",
                                            "True|False|True|False|True|False"])


if __name__ == "__main__":
    unittest.main()
