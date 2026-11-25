"""Home Assistant (fonctionnalité facultative) : liaison SSH avec la clé de loco, clé d'hôte confirmée, relevé,
mises à jour, sauvegardes, redémarrages, droits et terminal.

Un faux ssh et une fausse commande `ha` (tests/fake_homeassistant.py) remplacent le client SSH et Home Assistant ; tout
le reste est le vrai : clé de loco, file des tâches, relais du terminal."""
import base64
import hashlib
import json
import subprocess
import os
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

import fake_homeassistant as fake
import test_portal as fixture
from loco import adb, auth, db, features, homeassistant, permissions
from loco.db import transaction

PASSWORD = "Domotique-2026-Secret"


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(key)).digest()).decode().rstrip("=")


class HomeAssistantTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="loco-hass-")
        self.state = os.path.join(self.folder, "state")
        self.ssh = fake.install(self.folder)
        for patch in (mock.patch("loco.homeassistant.ssh_binary", return_value=self.ssh),
                      mock.patch.object(homeassistant, "DOWN", 3), mock.patch.object(homeassistant, "BACK", 5),
                      mock.patch.object(homeassistant, "STEPS", (0.05, 0.05))):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.folder, True)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["homeassistant"]'))
            conn.execute("DELETE FROM remote_sessions")
            conn.execute("DELETE FROM homeassistant_hosts")
            conn.execute("DELETE FROM audit")
        self.addCleanup(self.reset)
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)

    def reset(self):
        self.idle()
        with transaction() as conn:
            conn.execute("UPDATE remote_sessions SET state='closed' WHERE state!='closed'")
        for _ in range(100):                     # les relais de terminal se terminent quand leur session est fermée
            if not adb._WORKERS:
                break
            time.sleep(0.05)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
            conn.execute("DELETE FROM remote_sessions")
            conn.execute("DELETE FROM homeassistant_hosts")
            conn.execute("DELETE FROM users WHERE username LIKE 'hass-%'")
            conn.execute("DELETE FROM access_roles WHERE id LIKE 'hass-%'")

    # ------------------------------------------------------------------ aides
    def idle(self, timeout=20):
        """Attend la fin des tâches et des relevés en arrière-plan."""
        end = time.time() + timeout
        while time.time() < end and (homeassistant._WORKERS or homeassistant._REFRESH):
            time.sleep(0.05)
        self.assertFalse(homeassistant._WORKERS, "tâches encore en cours")

    def flag(self, name, value=""):
        with open(os.path.join(self.state, name), "w") as f:
            f.write(value)

    def lines(self, name):
        try:
            with open(os.path.join(self.state, name)) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def calls(self):
        return [json.loads(line) for line in self.lines("ssh.log")]

    def commands(self):
        """Commandes `ha` qui changent quelque chose (les relevés --raw-json sont écartés)."""
        return [line for line in self.lines("commands.log") if "--raw-json" not in line and "--help" not in line]

    def post(self, path, data=None, browser=None):
        return (browser or self.admin).post(path, data, page="/homeassistant")

    def page(self, browser=None):
        return (browser or self.admin).c.get("/homeassistant").get_data(as_text=True)

    def page_host(self, row, browser=None):
        return (browser or self.admin).c.get("/homeassistant/%d" % row["id"]).get_data(as_text=True)

    def link(self, host="192.168.43.80", port="22", username="root", name="Maison"):
        """Les deux étapes : adresse saisie, puis empreinte confirmée. Renvoie la ligne de l'instance."""
        self.post("/homeassistant/link", {"host": host, "port": port, "username": username, "name": name})
        nonce = self.page().split('name="nonce" value="')[1].split('"')[0]
        self.post("/homeassistant/confirm", {"nonce": nonce})
        return self.host()

    def host(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM homeassistant_hosts ORDER BY id DESC LIMIT 1").fetchone()

    def jobs(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM homeassistant_jobs ORDER BY id").fetchall()

    def audit(self):
        with transaction() as conn:
            return [(r["action"], r["detail"]) for r in conn.execute("SELECT action, detail FROM audit WHERE action LIKE 'homeassistant.%' ORDER BY id")]

    def member(self, name, grants):
        """Compte dont le profil ne porte que les droits donnés."""
        with transaction() as conn:
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("hass-" + name, "Hass " + name, permissions.role_key("Hass " + name), json.dumps(sorted(permissions.expand(grants)))))
            auth.create_user(conn, "hass-" + name, PASSWORD, "hass-" + name, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login("hass-" + name, PASSWORD).status_code, 302)
        return browser


class TestOptionalFeature(HomeAssistantTest):
    def test_off_by_default_and_listed_as_optional(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertEqual(self.admin.c.get("/homeassistant").status_code, 403)
        self.assertNotIn('href="/homeassistant"', self.admin.c.get("/").get_data(as_text=True))
        page = self.admin.c.get("/administration/features").get_data(as_text=True)
        self.assertLess(page.index("Expérimental"), page.index("Facultatif"))
        self.assertLess(page.index("Facultatif"), page.index('name="homeassistant"'))
        self.assertNotIn('name="homeassistant" value="1" checked', page)

    def test_an_administrator_turns_it_on_and_off(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        on = {k: "1" for k in features.FEATURES if k not in features.EXPERIMENTAL}
        self.admin.post("/administration/features", on, page="/administration/features")
        self.assertEqual(self.admin.c.get("/homeassistant").status_code, 200)
        self.assertIn('href="/homeassistant"', self.admin.c.get("/").get_data(as_text=True))
        with transaction() as conn:
            self.assertEqual(features._opted_in(conn, features.EXPERIMENTAL_SETTING), set())    # l'un ne déborde pas sur l'autre
        on.pop("homeassistant")
        self.admin.post("/administration/features", on, page="/administration/features")
        self.assertEqual(self.admin.c.get("/homeassistant").status_code, 403)

    def test_every_route_is_declared_and_follows_the_feature(self):
        routes = {e for e in fixture.loco.app.view_functions if e.startswith("homeassistant")}
        self.assertEqual(len(routes), 13)
        for endpoint in routes:
            self.assertTrue(permissions.ENDPOINTS[endpoint].startswith("homeassistant."), endpoint)
            with fixture.loco.app.test_request_context():
                self.assertEqual(features.required(endpoint), ("homeassistant",))


class TestKeys(HomeAssistantTest):
    def test_the_key_of_loco_is_private_and_stable(self):
        with fixture.loco.app.app_context():
            first = homeassistant.public_key()
            self.assertEqual(first, homeassistant.public_key())
        self.assertTrue(first.startswith("ssh-ed25519 AAAA"))
        self.assertEqual(stat.S_IMODE(os.stat(homeassistant.KEY_PATH).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(homeassistant.KEY_DIR).st_mode), 0o700)
        with open(homeassistant.KEY_PATH) as f:
            private = f.read()
        self.assertIn("BEGIN OPENSSH PRIVATE KEY", private)
        page = self.page()
        self.assertIn(first.split()[1], page)                       # la clé publique est montrée, à copier
        self.assertNotIn(private.splitlines()[1], page)

    def test_nothing_is_stored_before_the_host_key_is_confirmed(self):
        self.post("/homeassistant/link", {"host": "192.168.43.80", "port": "2222", "username": "hassio", "name": "Maison"})
        self.assertIsNone(self.host())
        page = self.page()
        self.assertIn(fingerprint(fake.HOST_KEY), page)
        self.assertIn("hassio@192.168.43.80:2222", page)
        probe = self.calls()
        self.assertEqual(len(probe), 1)                              # un seul échange : le relevé de la clé d'hôte
        self.assertEqual(probe[0]["options"]["PreferredAuthentications"], "none")
        self.assertEqual(probe[0]["identity"], "")                   # la clé de loco n'y est pas présentée
        self.assertEqual(probe[0]["port"], "2222")
        # Sans la preuve que la page de confirmation a été vue : refusé, et la demande est oubliée.
        self.assertEqual(self.post("/homeassistant/confirm", {"nonce": "0" * 16}).status_code, 400)
        self.assertIsNone(self.host())
        self.assertNotIn(fingerprint(fake.HOST_KEY), self.page())

    def test_confirming_links_the_instance_with_that_key_only(self):
        row = self.link(port="2222", username="hassio")
        self.assertEqual((row["name"], row["host"], row["port"], row["username"]), ("Maison", "192.168.43.80", 2222, "hassio"))
        self.assertEqual(row["host_key"], "ssh-ed25519 " + fake.HOST_KEY)
        self.assertEqual(row["error"], "")
        self.assertIn(("homeassistant.liaison", f"Maison (hassio@192.168.43.80:2222), clé d'hôte {fingerprint(fake.HOST_KEY)}"), self.audit())
        call = self.calls()[-1]
        self.assertEqual(call["options"]["StrictHostKeyChecking"], "yes")
        self.assertEqual(call["options"]["IdentitiesOnly"], "yes")
        self.assertEqual(call["options"]["BatchMode"], "yes")
        self.assertEqual(call["options"]["-F"], os.devnull)
        self.assertEqual(call["options"]["GlobalKnownHostsFile"], os.devnull)
        self.assertEqual(call["identity"], homeassistant.KEY_PATH)
        self.assertEqual((call["user"], call["host"], call["port"]), ("hassio", "192.168.43.80", "2222"))
        self.assertTrue(call["command"].startswith("sh -c "))         # pas de shell de connexion : un profil cassé l'arrêterait
        self.assertFalse(os.path.exists(call["options"]["UserKnownHostsFile"]))   # écrit pour la commande, puis effacé
        self.assertEqual([n for n in os.listdir(homeassistant.KEY_DIR) if n.startswith("known-")], [])

    def test_cancelling_forgets_the_request(self):
        self.post("/homeassistant/link", {"host": "192.168.43.80", "port": "22", "username": "root"})
        self.post("/homeassistant/confirm", {"cancel": "1"})
        self.assertIsNone(self.host())
        self.assertNotIn(fingerprint(fake.HOST_KEY), self.page())

    def test_only_a_plain_address_and_user_reach_ssh(self):
        for form in ({"host": "-oProxyCommand=id", "username": "root"}, {"host": "http://ha.local", "username": "root"},
                     {"host": "ha.local; id", "username": "root"}, {"host": "ha.local", "username": "root;id"},
                     {"host": "ha.local", "username": "-oX"}, {"host": "ha.local", "username": "root", "port": "70000"},
                     {"host": "", "username": "root"}):
            self.post("/homeassistant/link", dict({"port": "22"}, **form))
        self.assertEqual(self.calls(), [])
        self.assertIsNone(self.host())

    def test_a_changed_host_key_stops_everything_until_confirmed_again(self):
        row = self.link()
        self.flag("hostkey", fake.OTHER_KEY + "\n")
        self.post(f"/homeassistant/{row['id']}/refresh")
        self.assertIn("plus celle qui a été confirmée", self.host()["error"])
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "core"})
        self.idle()
        self.assertEqual(self.commands(), [])                         # rien n'est lancé sur un hôte non reconnu
        self.assertEqual(self.jobs()[0]["state"], "failed")
        self.post(f"/homeassistant/{row['id']}/hostkey")
        page = self.page()
        self.assertIn(fingerprint(fake.OTHER_KEY), page)
        self.assertIn("diffère de celle confirmée", page)
        self.assertEqual(self.host()["host_key"], "ssh-ed25519 " + fake.HOST_KEY)    # tant que ce n'est pas confirmé
        self.post("/homeassistant/confirm", {"nonce": page.split('name="nonce" value="')[1].split('"')[0]})
        row = self.host()
        self.assertEqual((row["host_key"], row["error"]), ("ssh-ed25519 " + fake.OTHER_KEY, ""))
        self.assertIn(("homeassistant.cle-hote", f"Maison : {fingerprint(fake.OTHER_KEY)}"), self.audit())

    def test_a_refused_key_and_a_silent_host_are_explained(self):
        self.flag("denied")
        row = self.link()
        self.assertIn("refuse la clé de loco", row["error"])
        self.assertIn("refuse la clé de loco", self.page())
        os.unlink(os.path.join(self.state, "denied"))
        self.flag("down", "1")
        self.post(f"/homeassistant/{row['id']}/refresh")
        self.assertIn("Connection refused", self.host()["error"])
        self.post(f"/homeassistant/{row['id']}/refresh")
        self.assertEqual(self.host()["error"], "")

    def test_without_an_ssh_client_the_page_says_so(self):
        with mock.patch("loco.homeassistant.ssh_binary", return_value=None):
            self.assertIn("openssh-client", self.page())
            self.post("/homeassistant/link", {"host": "192.168.43.80", "port": "22", "username": "root"})
        self.assertIsNone(self.host())


class TestReading(HomeAssistantTest):
    def test_versions_updates_addons_backups_and_health(self):
        row = self.link()
        info = json.loads(row["info"])
        self.assertEqual([(u["target"], u["version"], u["latest"]) for u in info["updates"]],
                         [("core", "2026.11.1", "2026.11.2"), ("os", "18.4", "18.5"), ("addon:core_mosquitto", "6.5.0", "6.5.1")])
        self.assertEqual((info["supervisor"]["version"], info["supervisor"]["outdated"]), ("2026.11.0", False))
        self.assertEqual([a["name"] for a in info["addons"]], ["Advanced SSH & Web Terminal", "Mosquitto broker", "Zigbee2MQTT"])
        self.assertEqual((info["disk_free"], info["disk_total"]), (int(40.2 * 1073741824), int(62.3 * 1073741824)))
        self.assertEqual([b["name"] for b in info["backups"]], ["Automatique", "core_2026.11.0"])       # la plus récente d'abord
        self.assertRegex(info["backups"][0]["date"], r"^2026-11-07 \d\d:00$")                          # à l'heure du serveur
        self.assertEqual(info["backups"][0]["size"], 432642457)
        self.assertEqual(info["issues"], [{"type": "no_current_backup", "context": "system"}])
        page = self.page_host(row)
        for text in ("2026.11.1", "2026.11.2 disponible", "18.5 disponible", "Mosquitto broker", "6.5.0 → 6.5.1", "Zigbee2MQTT",
                     "arrêté", "Automatique", "chiffrée", "Aucune sauvegarde récente", "3 mises à jour", "Tout mettre à jour"):
            self.assertIn(text, page, text)
        for text in ("2026.11.0 disponible", "22.0.3 →", "2.6.3-1 →"):               # ce qui est à jour n'annonce rien
            self.assertNotIn(text, page, text)

    def test_resources_and_haos_detection(self):
        # summarize déduit « Home Assistant OS » du système, et calcule mémoire/processeur depuis le relevé /proc.
        found = {
            "core info": {"version": "2026.11.1"}, "os info": {"version": "18.4"},
            "supervisor info": {"version": "2026.11.0", "healthy": True, "supported": True},
            "host info": {"hostname": "homeassistant", "operating_system": "Home Assistant OS 18.4",
                          "disk_total": 62.3, "disk_free": 40.2},
            "addons": {"addons": []}, "backups": {"backups": []}, "resolution info": {"issues": []},
            "resources": {"mem_total_kb": 3946540, "mem_avail_kb": 2560192, "nproc": 2,
                          "cpu1": "cpu  100 0 50 1000 10 0 5 0 0", "cpu2": "cpu  130 0 50 1070 10 0 5 0 0"},
        }
        info = homeassistant.summarize(found)
        self.assertTrue(info["haos"])
        self.assertEqual(info["memory_total"], 3946540 * 1024)
        self.assertEqual(info["memory_used"], (3946540 - 2560192) * 1024)
        self.assertEqual(info["cores"], 2)
        self.assertEqual(info["cpu_percent"], 30.0)                 # 30 occupés sur 100 de delta (70 oisifs)
        found["host info"]["operating_system"] = "Debian GNU/Linux 12"       # installation sans Home Assistant OS
        self.assertFalse(homeassistant.summarize(found)["haos"])
        self.assertIsNone(homeassistant._cpu_percent("", ""))       # relevé absent : pas de pourcentage inventé

    def test_the_resource_probe_is_valid_shell_and_part_of_the_reading(self):
        self.assertEqual(subprocess.run(["sh", "-n", "-c", homeassistant.RESOURCES]).returncode, 0)
        self.assertIn("RESOURCES", homeassistant._collect.__code__.co_names)   # ajouté au script de relevé

    def test_a_profile_written_for_bash_no_longer_stops_the_link(self):
        """Retour réel : un module SSH dont un profil n'est pas compris par sh arrêtait le shell de connexion
        (« sh: /dev/fd/64: line 14: syntax error: unexpected "(" ») avant toute commande. loco n'en lance plus."""
        self.flag("broken-profile")
        row = self.link()
        self.assertEqual(row["error"], "")
        self.assertEqual(json.loads(row["info"])["updates"][0]["version"], "2026.11.1")
        self.assertTrue(all("jeton-du-module" not in c["command"] for c in self.calls()))   # jeton jamais dans la commande

    def test_the_prelude_finds_the_token_without_a_login_shell_and_never_prints_it(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": tempfile.mkdtemp()}
        self.addCleanup(shutil.rmtree, env["HOME"], True)
        script = 'printf "%s|%s" "${SUPERVISOR_TOKEN:+jeton présent}" "$(command -v sh >/dev/null && echo chemin)"'
        done = subprocess.run(["sh", "-c", homeassistant.PRELUDE + script], env=dict(env, SUPERVISOR_TOKEN="jeton-du-module"),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(done.stdout.decode(), "jeton présent|chemin")          # déjà là : gardé tel quel
        done = subprocess.run(["sh", "-c", homeassistant.PRELUDE + "echo suite"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual((done.returncode, done.stdout.decode().strip().splitlines()[-1]), (0, "suite"))   # introuvable : le script tourne quand même

    def test_an_older_command_only_knows_addons_and_a_warning_does_not_hide_the_answer(self):
        self.flag("old-cli")
        self.flag("noisy")
        row = self.link()
        info = json.loads(row["info"])
        self.assertEqual(len(info["addons"]), 3)
        self.assertEqual(len(info["updates"]), 3)
        self.assertIn("addons --raw-json", self.lines("commands.log"))
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "addon:core_mosquitto", "backup": "1"})
        self.idle()
        self.assertEqual(self.commands(), ["addons update core_mosquitto --backup --no-progress"])
        self.assertEqual(self.jobs()[0]["state"], "success")

    def test_the_recent_command_is_asked_for_apps_never_for_the_deprecated_name(self):
        self.link()
        words = {line.split()[0] for line in self.lines("commands.log")}
        self.assertIn("apps", words)
        self.assertNotIn("addons", words)

    def test_a_shape_that_is_not_home_assistant_is_refused_cleanly(self):
        row = self.link()
        with open(os.path.join(self.state, "core-info.json"), "w") as f:
            f.write("pas du JSON")
        self.post(f"/homeassistant/{row['id']}/refresh")
        self.assertIn("illisible", self.host()["error"])
        self.assertIsNotNone(json.loads(self.host()["info"]))          # le dernier relevé lisible reste affiché
        self.assertEqual(self.admin.c.get("/homeassistant").status_code, 200)

    def test_untrusted_values_are_bounded_and_escaped(self):
        fake.write_state(self.state, dict(fake.STATE, addons={"addons": [
            {"name": "<script>alert(1)</script>" + "x" * 500, "slug": "bon_module", "version": "1", "version_latest": "2", "update_available": True, "state": "started"},
            {"name": "Injection", "slug": "x; reboot", "version": "1", "version_latest": "2", "update_available": True, "state": "started"},
            "pas un module"]}))
        row = self.link()
        info = json.loads(row["info"])
        self.assertEqual([a["slug"] for a in info["addons"]], ["bon_module"])                         # nom de module invalide : écarté
        self.assertLessEqual(len(info["addons"][0]["name"]), 80)
        self.assertNotIn("<script>alert(1)", self.page())

    def test_the_page_polls_a_signature_and_refreshes_in_the_background(self):
        row = self.link()
        r = self.admin.c.get("/homeassistant", headers={"X-Requested-With": "fetch"})
        first = r.get_json()["signature"]
        self.assertEqual(first, self.admin.c.get("/homeassistant", headers={"X-Requested-With": "fetch"}).get_json()["signature"])
        with transaction() as conn:
            conn.execute("UPDATE homeassistant_hosts SET checked=? WHERE id=?", (time.time() - 3600, row["id"]))
        before = len(self.calls())
        self.admin.c.get("/homeassistant", headers={"X-Requested-With": "fetch"})                    # relevé trop ancien : refait
        self.idle()
        self.assertEqual(len(self.calls()), before + 1)
        self.assertNotEqual(first, self.admin.c.get("/homeassistant", headers={"X-Requested-With": "fetch"}).get_json()["signature"])

    def test_the_overview_reports_what_needs_attention(self):
        row = self.link()
        page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Home Assistant « Maison » : 3 mises à jour disponibles.", page)
        reader = self.member("accueil", {"dashboard.view"})
        self.assertNotIn("Home Assistant «", reader.c.get("/").get_data(as_text=True))       # sans le droit : rien
        self.flag("denied")
        self.post(f"/homeassistant/{row['id']}/refresh")
        page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Home Assistant « Maison » : la liaison échoue.", page)
        self.assertNotIn("mises à jour disponibles.", page.split("Home Assistant « Maison »")[1][:60])
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertNotIn("Home Assistant «", self.admin.c.get("/").get_data(as_text=True))  # fonctionnalité coupée : rien

    def test_the_log_is_read_on_demand(self):
        row = self.link()
        r = self.admin.c.get(f"/homeassistant/{row['id']}/log")
        self.assertIn("Home Assistant initialized", r.get_json()["log"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")


class TestHacs(HomeAssistantTest):
    """Dépôts HACS : repérés par le registre des entités (platform=marketplace), mis à jour par le service update.install."""

    FOUND = {
        "core info": {"version": "2026.11.1", "version_latest": "2026.11.1", "update_available": False},
        "os info": {"version": "18.4", "version_latest": "18.4", "update_available": False},
        "supervisor info": {"version": "2026.11.0", "version_latest": "2026.11.0", "update_available": False,
                            "healthy": True, "supported": True},
        "host info": {"hostname": "maison", "operating_system": "Home Assistant OS 18.4",
                      "disk_total": 62.3, "disk_free": 40.2},
        "addons": {"addons": []}, "backups": {"backups": []}, "resolution info": {"issues": []},
        "hacs": {"hacs": [
            {"id": "update.hacs_mushroom_update", "name": "Mushroom", "outdated": True, "version": "5.2.3", "latest": "5.3.0"},
            {"id": "update.hacs_card_mod_update", "name": "card-mod", "outdated": False, "version": "3.4.3", "latest": "3.4.3"},
        ]},
    }

    def test_repositories_are_read_sorted_and_the_outdated_ones_become_updates(self):
        info = homeassistant.summarize(self.FOUND)
        self.assertEqual([h["name"] for h in info["hacs"]], ["card-mod", "Mushroom"])      # triés, insensibles à la casse
        self.assertEqual([(h["target"], h["outdated"]) for h in info["hacs"]],
                         [("hacs:update.hacs_card_mod_update", False), ("hacs:update.hacs_mushroom_update", True)])
        updates = [(u["target"], u["version"], u["latest"]) for u in info["updates"]]
        self.assertIn(("hacs:update.hacs_mushroom_update", "5.2.3", "5.3.0"), updates)
        self.assertNotIn("hacs:update.hacs_card_mod_update", [u["target"] for u in info["updates"]])   # à jour : rien

    def test_an_update_accepts_the_marketplace_warning_then_installs_and_refuses_an_unknown_one(self):
        cmd = homeassistant.command({"action": "update-hacs", "target": "hacs:update.hacs_mushroom_update", "backup": 0})
        self.assertIn("marketplace/warning/accept", cmd)           # HA 2026.11 bloque sans l'acceptation (par utilisateur)
        self.assertIn('"entity_id":"update.hacs_mushroom_update"', cmd)
        self.assertIn("core/api/services/update/install", cmd)
        self.assertIn("$SUPERVISOR_TOKEN", cmd)                    # le jeton vient de l'environnement, jamais en argument
        self.assertNotIn("__SLUG__", cmd)                          # l'entité a bien remplacé le gabarit
        for bad in ("hacs:update.Mushroom", "hacs:rm -rf /", "hacs:sensor.x", "hacs:"):
            with self.assertRaises(homeassistant.LinkError):
                homeassistant.command({"action": "update-hacs", "target": bad, "backup": 0})

    def test_a_hacs_update_runs_with_the_addons_before_the_core(self):
        info = {"updates": [
            {"target": "os", "name": "OS", "version": "18.4", "latest": "18.5"},
            {"target": "hacs:update.hacs_mushroom_update", "name": "Mushroom", "version": "5.2.3", "latest": "5.3.0"},
            {"target": "core", "name": "Core", "version": "a", "latest": "b"},
            {"target": "addon:core_mosquitto", "name": "Mosquitto", "version": "1", "latest": "2"},
            {"target": "supervisor", "name": "Supervisor", "version": "1", "latest": "2"},
        ]}
        actions = [a for a, _t, _l in homeassistant._update_items(info, "")]
        self.assertEqual(actions[0], "update-supervisor")
        self.assertEqual(actions[-2:], ["update-core", "update-os"])
        self.assertEqual(set(actions[1:3]), {"update-addon", "update-hacs"})
        one = homeassistant._update_items(info, "hacs:update.hacs_mushroom_update")
        self.assertEqual([(a, t) for a, t, _l in one], [("update-hacs", "hacs:update.hacs_mushroom_update")])

    def test_the_hacs_probe_and_install_are_valid_shell_and_the_embedded_client_is_valid_python(self):
        self.assertEqual(subprocess.run(["sh", "-n", "-c", homeassistant.HACS_COLLECT]).returncode, 0)
        # La commande complète d'une mise à jour HACS (acceptation WebSocket embarquée + installation) est du shell valide.
        cmd = homeassistant.command({"action": "update-hacs", "target": "hacs:update.mushroom_update", "backup": 0})
        self.assertEqual(subprocess.run(["sh", "-n", "-c", cmd]).returncode, 0)
        # Le petit client WebSocket en python pur (entre les marqueurs du heredoc) compile sans erreur.
        body = homeassistant.MARKETPLACE_ACCEPT.split("<<'LOCO_WS'", 1)[1].split("\n", 1)[1].split("\nLOCO_WS", 1)[0]
        compile(body, "<marketplace-accept>", "exec")
        self.assertIn("HACS_COLLECT", homeassistant._collect.__code__.co_names)   # ajouté au script de relevé

    def test_the_page_lists_the_repositories_and_offers_the_update(self):
        row = self.link()
        info = homeassistant.summarize(self.FOUND)
        with transaction() as conn:                                  # relevé déjà frais : la fiche ne relance rien en fond
            conn.execute("UPDATE homeassistant_hosts SET info=?, checked=? WHERE id=?",
                         (json.dumps(info), time.time(), row["id"]))
        page = self.page_host(self.host())
        for text in ("HACS", "Mushroom", "card-mod", "5.2.3 → 5.3.0", "à mettre à jour",
                     "hacs:update.hacs_mushroom_update", "dépôts de la communauté"):
            self.assertIn(text, page, text)
        self.assertIn('data-open="#hass-update"', page)             # la mise à jour du dépôt est proposée

    def test_a_running_update_is_shown_as_busy_and_not_offered_again(self):
        row = self.link()
        info = homeassistant.summarize(self.FOUND)
        with transaction() as conn:
            conn.execute("UPDATE homeassistant_hosts SET info=?, checked=? WHERE id=?",
                         (json.dumps(info), time.time(), row["id"]))
            conn.execute("INSERT INTO homeassistant_jobs (host_id, action, target, state, created_at) VALUES (?,?,?,?,?)",
                         (row["id"], "update-hacs", "hacs:update.hacs_mushroom_update", "running", homeassistant._stamp()))
        page = self.page_host(self.host())
        self.assertIn("en cours", page)                             # la tâche lancée s'affiche comme telle
        self.assertNotIn('data-open="#hass-update"', page)          # … et son bouton ne se reclique pas


class TestFleetSelection(HomeAssistantTest):
    """Les instances Home Assistant se cochent dans Mon parc comme les autres postes : mise à jour et redémarrage groupés."""

    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM devices")           # parc vide : seuls les comptes de l'instance comptent

    def test_instances_are_selectable_and_acted_on(self):
        row = self.link()                                             # instance avec des mises à jour disponibles
        parc = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn('data-id="ha-%d"' % row["id"], parc)           # case à cocher sur la ligne de l'instance
        tok = self.admin.token("/fleet")
        up = self.admin.c.post("/fleet/update-selected", data={"csrf": tok, "ids": "ha-%d" % row["id"]},
                               content_type="multipart/form-data")
        self.assertEqual(up.status_code, 200)
        self.assertGreaterEqual(up.get_json()["count"], 1)           # ses mises à jour disponibles sont lancées
        self.idle()
        reb = self.admin.c.post("/fleet/reboot-selected", data={"csrf": tok, "ids": "ha-%d" % row["id"]},
                                content_type="multipart/form-data")
        self.assertEqual(reb.get_json()["count"], 1)                 # redémarrage du système lancé
        self.idle()

    def test_the_rights_are_those_of_its_own_buttons(self):
        row = self.link()
        viewer = self.member("viewer", ["homeassistant.view", "fleet.view", "fleet.update", "fleet.reboot"])
        got = viewer.c.post("/fleet/update-selected",
                            data={"csrf": viewer.token("/fleet"), "ids": "ha-%d" % row["id"]},
                            content_type="multipart/form-data")
        self.assertEqual(got.get_json().get("count"), 0)             # peut voir, pas mettre à jour : rien n'est lancé
        self.idle()
        updater = self.member("updater", ["homeassistant.view", "homeassistant.update", "fleet.view", "fleet.update", "fleet.reboot"])
        self.assertIn('data-id="ha-%d"' % row["id"], updater.c.get("/fleet").get_data(as_text=True))
        tok = updater.token("/fleet")
        got = updater.c.post("/fleet/update-selected", data={"csrf": tok, "ids": "ha-%d" % row["id"]}, content_type="multipart/form-data")
        self.assertEqual(got.get_json()["count"], 1)                 # le droit de mettre à jour suffit (plus « gérer »)
        self.idle()
        got = updater.c.post("/fleet/reboot-selected", data={"csrf": tok, "ids": "ha-%d" % row["id"]}, content_type="multipart/form-data")
        self.assertEqual(got.get_json()["count"], 0)                 # redémarrer : son propre droit
        self.idle()
        self.assertFalse(any(j["action"] == "reboot-host" for j in self.jobs()))

    def test_update_all_takes_it_with_a_backup_first(self):
        row = self.link()
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("Tout mettre à jour (1)", html)
        self.assertEqual(self.admin.c.post("/fleet/update-all", data={"csrf": self.admin.token("/fleet")}).status_code, 302)
        self.idle()
        jobs = self.jobs()
        self.assertTrue(jobs and all(j["host_id"] == row["id"] for j in jobs))
        self.assertEqual({j["action"]: j["backup"] for j in jobs}.get("update-core"), 1)    # sauvegarde avant, comme sa page
        feed = self.admin.c.get("/fleet/updates").get_data(as_text=True)
        self.assertIn("Mise à jour de Home Assistant", feed)
        self.assertIn('href="/homeassistant/%d"' % row["id"], feed)

    def test_the_fleet_schedule_updates_it_too(self):
        self.link()
        with transaction() as conn:
            sched = conn.execute("INSERT INTO schedules (action, targets, recurrence, at_time, start_date, next_run, created_by, created_at, updated_at) "
                                 "VALUES ('update', ?, 'once', '02:00', '2026-01-01', '2026-01-01 02:00:00', 'admin', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                                 (json.dumps({"all": True}),)).lastrowid
        self.addCleanup(self.drop_schedules)
        from loco import schedules
        from loco.db import db
        with fixture.loco.app.test_request_context():
            schedules.materialize(db())
            db().commit()
        self.idle()
        jobs = self.jobs()
        self.assertTrue(jobs and all(j["created_by"] == "admin" for j in jobs))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT last_result FROM schedules WHERE id=?", (sched,)).fetchone()[0], "1 poste")

    @staticmethod
    def drop_schedules():
        with transaction() as conn:
            conn.execute("DELETE FROM schedules WHERE created_at='2026-01-01 00:00:00' AND action='update'")

    def test_its_row_follows_the_filters_and_shows_its_resources(self):
        row = self.link()
        seen = lambda query: "/homeassistant/%d\"" % row["id"] in self.admin.c.get("/fleet" + query).get_data(as_text=True)    # noqa: E731
        self.assertTrue(seen("?os=homeassistant"))
        self.assertFalse(seen("?os=linux"))
        self.assertTrue(seen("?q=maison"))
        self.assertFalse(seen("?q=introuvable"))
        self.assertFalse(seen("?voir=redemarrage"))
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("Home Assistant<span class=\"menu-count\">1</span>", html)
        ha_row = html.split('<tr class="ha-row">', 1)[1].split("</tr>", 1)[0]
        self.assertIn("Disque", ha_row)                              # ressources de l'hôte, plus un simple tiret

    def test_a_row_offers_a_direct_update_button(self):
        row = self.link()                                             # instance avec des mises à jour disponibles
        parc = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("/homeassistant/%d/fleet-update" % row["id"], parc)   # bouton « Mettre à jour » sur la ligne HA
        self.assertEqual(self.post("/homeassistant/%d/fleet-update" % row["id"]).status_code, 302)   # revient sur Mon parc
        self.idle()
        self.assertTrue(any(j["action"].startswith("update-") for j in self.jobs()))   # ses mises à jour sont lancées
        # sans le droit de mettre à jour : pas de bouton, et la route refuse
        viewer = self.member("ro", ["homeassistant.view", "fleet.view"])
        self.assertNotIn("/homeassistant/%d/fleet-update" % row["id"], viewer.c.get("/fleet").get_data(as_text=True))
        refused = viewer.c.post("/homeassistant/%d/fleet-update" % row["id"], data={"csrf": viewer.token("/fleet")})
        self.assertEqual(refused.status_code, 403)


class TestJobs(HomeAssistantTest):
    def test_one_update_with_a_backup_first(self):
        row = self.link()
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "core", "backup": "1"})
        self.idle()
        job = self.jobs()[0]
        self.assertEqual((job["action"], job["state"], job["backup"], job["created_by"]), ("update-core", "success", 1, "admin"))
        self.assertEqual(job["label"], "Home Assistant Core 2026.11.1 → 2026.11.2")
        self.assertEqual(self.commands(), ["core update --backup --no-progress"])
        info = json.loads(self.host()["info"])                        # relevé refait après la tâche
        self.assertEqual((info["core"]["version"], info["core"]["outdated"]), ("2026.11.2", False))
        self.assertEqual(len(info["backups"]), 3)
        self.assertIn(("homeassistant.update-core", "Maison : Home Assistant Core 2026.11.1 → 2026.11.2, sauvegarde avant"), self.audit())
        page = self.page_host(row)
        self.assertIn("Réussie", page)
        self.assertIn("sauvegarde avant", page)

    def test_everything_in_the_advised_order_and_the_system_comes_back(self):
        row = self.link()
        self.post("/homeassistant/update", {"host_id": row["id"], "target": ""})
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()],
                         [("update-addon", "success"), ("update-core", "success"), ("update-os", "success")])
        self.assertEqual(self.commands(), ["apps update core_mosquitto --no-progress", "core update --no-progress", "os update --no-progress"])
        self.assertIn("a redémarré et répond de nouveau", self.jobs()[-1]["output"])
        info = json.loads(self.host()["info"])
        self.assertEqual(info["updates"], [])
        self.assertEqual(self.host()["error"], "")
        self.assertIn("à jour", self.page())

    def test_what_is_running_is_shown_and_not_launched_twice(self):
        row = self.link()
        self.flag("slow", "1.5")
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "core"})
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "core"})
        self.post("/homeassistant/update", {"host_id": row["id"], "target": ""})          # le reste seulement
        self.assertEqual([j["action"] for j in self.jobs()], ["update-core", "update-addon", "update-os"])
        page = self.page_host(row)
        self.assertIn("mise à jour en cours", page)
        self.assertIn("en attente", page)
        self.assertNotIn("Tout mettre à jour", page)                   # plus rien à lancer
        self.post(f"/homeassistant/{row['id']}/backup")
        self.post(f"/homeassistant/{row['id']}/backup")
        self.assertEqual([j["action"] for j in self.jobs()].count("backup"), 1)
        self.assertIn("Sauvegarde en cours", self.page_host(row))
        os.unlink(os.path.join(self.state, "slow"))
        self.idle(40)
        self.assertEqual({j["state"] for j in self.jobs()}, {"success"})

    def test_a_failure_is_kept_with_its_output(self):
        row = self.link()
        self.flag("fail-addon")
        self.post("/homeassistant/update", {"host_id": row["id"], "target": "addon:core_mosquitto"})
        self.idle()
        job = self.jobs()[0]
        self.assertEqual(job["state"], "failed")
        self.assertIn("image pull failed", job["output"])
        page = self.page_host(row)
        self.assertIn("Échec", page)
        self.assertIn("image pull failed", page)
        self.assertIn("6.5.0 → 6.5.1", page)                           # toujours proposée

    def test_an_unknown_target_launches_nothing(self):
        row = self.link()
        for target in ("addon:inconnu", "addon:x; reboot", "kernel"):
            self.post("/homeassistant/update", {"host_id": row["id"], "target": target})
        self.assertEqual(self.post("/homeassistant/update", {"host_id": "999", "target": "core"}).status_code, 404)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.commands(), [])

    def test_backup_restart_and_reboot(self):
        row = self.link()
        self.post(f"/homeassistant/{row['id']}/backup")
        self.post(f"/homeassistant/{row['id']}/restart", {"what": "core"})
        self.post(f"/homeassistant/{row['id']}/restart", {"what": "host"})
        self.assertEqual(self.post(f"/homeassistant/{row['id']}/restart", {"what": "tout"}).status_code, 400)
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()],
                         [("backup", "success"), ("restart-core", "success"), ("reboot-host", "success")])
        commands = self.commands()
        self.assertRegex(commands[0], r"^backups new --name loco \d{4}-\d\d-\d\d \d\d:\d\d --no-progress$")
        self.assertEqual(commands[1:], ["core restart --no-progress", "host reboot --no-progress"])
        self.assertEqual(len(json.loads(self.host()["info"])["backups"]), 3)
        self.assertEqual([a for a, _d in self.audit()][-3:], ["homeassistant.backup", "homeassistant.restart-core", "homeassistant.reboot-host"])

    def test_a_system_that_does_not_come_back_is_a_failure(self):
        row = self.link()
        self.flag("never-back")
        with mock.patch.object(homeassistant, "BACK", 0.3):
            self.post(f"/homeassistant/{row['id']}/restart", {"what": "host"})
            self.idle()
        job = self.jobs()[0]
        self.assertEqual(job["state"], "failed")
        self.assertIn("pas répondu", job["output"])
        self.assertIn("Connection refused", self.host()["error"])

    def test_a_restart_that_never_reached_home_assistant_is_not_awaited(self):
        row = self.link()
        self.flag("down", "1")
        self.post(f"/homeassistant/{row['id']}/restart", {"what": "host"})
        self.idle()
        job = self.jobs()[0]
        self.assertEqual(job["state"], "failed")
        self.assertIn("Connection refused", job["output"])
        self.assertNotIn("a redémarré", job["output"])
        self.assertEqual(self.commands(), [])

    def test_tasks_cut_by_a_restart_of_loco(self):
        row = self.link()
        with transaction() as conn:
            conn.execute("INSERT INTO homeassistant_jobs (host_id, action, state, created_at, started_at) VALUES (?, 'update-core', 'running', '2026-11-13 09:00:00', '2026-11-13 09:00:01')", (row["id"],))
            conn.execute("INSERT INTO homeassistant_jobs (host_id, action, created_at) VALUES (?, 'restart-core', '2026-11-13 09:00:02')", (row["id"],))
        homeassistant.start()
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()], [("update-core", "failed"), ("restart-core", "success")])
        self.assertIn("redémarrage de loco", self.jobs()[0]["output"])
        self.assertEqual(self.commands(), ["core restart --no-progress"])  # la tâche coupée n'est pas rejouée

    def test_removing_an_instance_forgets_its_tasks(self):
        row = self.link()
        self.post(f"/homeassistant/{row['id']}/backup")
        self.idle()
        self.post(f"/homeassistant/{row['id']}/delete")
        self.assertIsNone(self.host())
        self.assertEqual(self.jobs(), [])
        self.assertIn(("homeassistant.retrait", "Maison"), self.audit())


class TestRights(HomeAssistantTest):
    def test_each_action_has_its_own_right(self):
        row = self.link()
        outsider = self.member("dehors", {"fleet.view", "remote.terminal"})
        self.assertEqual(outsider.c.get("/homeassistant").status_code, 403)
        self.assertNotIn('href="/homeassistant"', outsider.c.get("/fleet").get_data(as_text=True))
        reader = self.member("lecture", {"homeassistant.view"})
        page = self.page_host(row, reader)
        self.assertIn("2026.11.1", page)
        for text in ("Mettre à jour", "Sauvegarder", "Redémarrer", ">Terminal<", "Retirer de loco", "Relier un Home Assistant", "ssh-ed25519"):
            self.assertNotIn(text, page, text)
        for path, form in (("/homeassistant/update", {"host_id": row["id"], "target": "core"}), (f"/homeassistant/{row['id']}/backup", {}),
                           (f"/homeassistant/{row['id']}/restart", {"what": "core"}), (f"/homeassistant/{row['id']}/shell", {}),
                           (f"/homeassistant/{row['id']}/delete", {}), (f"/homeassistant/{row['id']}/hostkey", {}),
                           ("/homeassistant/link", {"host": "192.168.43.81", "username": "root"}), ("/homeassistant/confirm", {})):
            self.assertEqual(reader.post(path, form, page="/account").status_code, 403, path)
        self.assertEqual(reader.post(f"/homeassistant/{row['id']}/refresh", page="/account").status_code, 302)
        self.assertEqual(reader.c.get(f"/homeassistant/{row['id']}/log").status_code, 200)
        self.assertEqual(self.jobs(), [])
        updater = self.member("maj", {"homeassistant.update"})
        page = self.page_host(row, updater)
        self.assertIn("Mettre à jour", page)
        self.assertNotIn("Sauvegarder", page)
        self.assertEqual(updater.post("/homeassistant/update", {"host_id": row["id"], "target": "core"}, page="/account").status_code, 302)
        self.idle()
        self.assertEqual(self.jobs()[0]["created_by"], "hass-maj")

    def test_the_default_profiles_get_nothing_but_the_administrator(self):
        for role, (_label, _text, grants) in permissions.DEFAULTS.items():
            held = {g for g in grants if g.startswith("homeassistant.")}
            self.assertEqual(held, {g for g in permissions.PERMISSIONS if g.startswith("homeassistant.")} if role == "admin" else set(), role)


class TestTerminal(HomeAssistantTest):
    def open(self, row, browser=None):
        r = self.post(f"/homeassistant/{row['id']}/shell", browser=browser)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def io(self, sid, data="", seq=0, ack=0, browser=None, **more):
        form = dict({"ack": str(ack), "cols": "90", "rows": "24"}, **more)
        if data:
            form.update(seq=str(seq), data=base64.b64encode(data.encode()).decode())
        return (browser or self.admin).post(f"/fleet/session/{sid}/io", form, page="/account")

    def read_until(self, sid, text, ack=0, timeout=10):
        out, end = "", time.time() + timeout
        while time.time() < end:
            answer = self.io(sid, ack=ack).get_json()
            for p in answer["packets"]:
                out += base64.b64decode(p["data"]).decode("utf-8", "replace")
                ack = p["seq"]
            if text in out or answer["state"] == "closed":
                return out, ack, answer
            time.sleep(0.1)
        return out, ack, answer

    def session(self, sid):
        with transaction() as conn:
            return conn.execute("SELECT * FROM remote_sessions WHERE id=?", (sid,)).fetchone()

    def test_a_real_shell_through_the_session_page(self):
        row = self.link()
        sid = self.open(row)
        session = self.session(sid)
        self.assertEqual((session["device_id"], session["kind"], session["target"]), (None, "ssh", f"homeassistant:{row['id']}"))
        known = os.path.join(homeassistant.KEY_DIR, "session-" + sid)
        self.assertEqual(stat.S_IMODE(os.stat(known).st_mode), 0o600)
        page = self.admin.c.get(f"/fleet/session/{sid}").get_data(as_text=True)
        for text in ('data-kind="terminal"', "Maison", "Retour à Home Assistant", 'href="/homeassistant"', f'action="/homeassistant/{row["id"]}/shell"',
                     "xterm"):                                         # le terminal du navigateur est bien chargé
            self.assertIn(text, page, text)
        self.assertNotIn("/fleet/None", page)
        self.read_until(sid, "maison$")
        self.io(sid, "echo loco-$((6*7)); ha core info --raw-json | cut -c1-15\n", seq=1)
        out, ack, answer = self.read_until(sid, '{"result": "ok"')
        self.assertIn("loco-42", out)
        self.assertEqual(answer["state"], "active")
        call = self.calls()[-1]
        self.assertEqual((call["flags"], call["command"], call["options"]["StrictHostKeyChecking"]), (["-tt"], "", "yes"))
        self.assertIn(("homeassistant.terminal", f"Maison, {sid}"), self.audit())
        self.io(sid, "exit\n", seq=2, ack=ack)
        _out, _ack, answer = self.read_until(sid, "\0jamais", ack=ack)
        self.assertEqual(answer["state"], "closed")
        self.assertEqual(self.session(sid)["error"], "Session terminée.")
        for _ in range(100):                                           # le relais range derrière lui
            if not adb._WORKERS:
                break
            time.sleep(0.05)
        self.assertEqual([n for n in os.listdir(homeassistant.KEY_DIR) if n.startswith("session-")], [])

    def test_closed_when_the_right_the_feature_or_the_instance_goes(self):
        row = self.link()
        member = self.member("shell", {"homeassistant.shell"})
        sid = self.open(row, member)
        self.assertEqual(self.io(sid, browser=member).get_json()["state"] in ("waiting", "active"), True)
        self.assertEqual(self.io(sid).status_code, 404)                 # la session d'un autre compte
        with transaction() as conn:
            conn.execute("UPDATE access_roles SET permissions=? WHERE id='hass-shell'", (json.dumps(["homeassistant.view"]),))
        self.assertEqual(member.c.get("/homeassistant").status_code, 200)
        self.admin.c.get(f"/fleet/session/{self.open(row)}")            # toute ouverture fait le ménage
        self.assertEqual(self.session(sid)["state"], "closed")
        sid = self.open(row)
        self.post(f"/homeassistant/{row['id']}/delete")
        self.assertEqual(self.session(sid)["state"], "closed")

    def test_refused_without_a_confirmed_host_key_or_over_the_limit(self):
        row = self.link()
        for _ in range(2):
            self.open(row)
        self.assertEqual(self.post(f"/homeassistant/{row['id']}/shell").status_code, 409)
        with transaction() as conn:
            conn.execute("UPDATE remote_sessions SET state='closed'")
            conn.execute("UPDATE homeassistant_hosts SET host_key='' WHERE id=?", (row["id"],))
        self.assertEqual(self.post(f"/homeassistant/{row['id']}/shell").status_code, 409)


class TestMigration(unittest.TestCase):
    def test_sessions_may_live_outside_the_fleet(self):
        with transaction() as conn:
            columns = {r["name"]: r for r in conn.execute("PRAGMA table_info(remote_sessions)")}
            self.assertEqual(columns["device_id"]["notnull"], 0)
            self.assertIn("target", columns)
            for name in ("terminal_fallback", "consent", "browser_attached", "agent_seen", "input_seq", "output_seq", "cols", "rows"):
                self.assertIn(name, columns)
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='remote_sessions_device'").fetchone())
            self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0], 50)
            self.assertEqual(len(db.MIGRATIONS), conn.execute("PRAGMA user_version").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
