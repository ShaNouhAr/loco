"""Mises à jour d'un poste une par une, avec avancement en direct : sélection sur la fiche (Linux et Windows), contrôles,
transmission à l'agent, remontée de l'avancement, outil MCP, et le vrai code de l'agent Linux (apt, dnf, lecture au fil
de l'eau)."""
import json
import unittest
from unittest import mock

import fake_agent
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from test_mcp import Client, create
from loco.db import transaction

AGENT = fake_agent.load_agent()
LINUX_INV = {"updates": [{"name": "openssl", "version": "3.5.4-1", "security": True},
                         {"name": "tzdata", "version": "2026e"}, {"name": "curl", "version": "8.14.1-2"}],
             "flatpak": 2}
WINDOWS_INV = {"updates": [{"name": "Mise à jour cumulative 2026-10 (KB5070001)", "version": "KB5070001", "security": True,
                            "id": "0b3d6c4e-1111-4a5b-9c2d-aaaaaaaaaaaa"},
                           {"name": "Microsoft Defender – définition 1.441", "version": "KB2267602",
                            "id": "5f7e9d1c-2222-4b6c-8d3e-bbbbbbbbbbbb"}]}


class TestSelection(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")

    def linux(self, version=None):
        agent = Agent(dict(LINUX, **({"agent_version": version} if version else {})), "192.168.43.60")
        agent.enroll()
        agent.checkin(LINUX_INV)
        return agent, device(agent.uid)["id"]

    def select(self, dev_id, **form):
        return self.admin.post(f"/fleet/{dev_id}/action", dict({"action": "update", "scope": "selection"}, **form),
                               page=f"/fleet/{dev_id}")

    def commands(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM device_commands ORDER BY id").fetchall()

    def test_linux_packages_and_flatpak_chosen_one_by_one(self):
        agent, dev_id = self.linux()
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertIn('name="only" value="openssl"', page)
        self.assertIn('name="only_flatpak"', page)
        self.assertIn("Mettre à jour la sélection", page)
        r = self.select(dev_id, only=["openssl", "curl", "inconnu"], only_flatpak="1")
        self.assertEqual(r.status_code, 302)
        cmd = self.commands()[-1]
        self.assertEqual(json.loads(cmd["params"])["only"], {"packages": ["openssl", "curl"], "flatpak": True})
        self.assertIn("3 sélectionnées", self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True))
        sent = agent.checkin().get_json()["commands"][0]
        self.assertEqual((sent["action"], sent["only"]["packages"]), ("update", ["openssl", "curl"]))
        # Rien de reconnu, ou seulement l'inconnu : refusé.
        self.assertEqual(self.select(dev_id, only=["inconnu"]).status_code, 400)

    def test_an_old_agent_only_updates_everything(self):
        _agent, dev_id = self.linux("2.119.2")
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertNotIn('name="only"', page)
        self.assertIn("doit être en version 2.120.0 ou plus", page)
        self.assertEqual(self.select(dev_id, only=["openssl"]).status_code, 400)     # il ferait tout : refusé

    def test_windows_updates_by_id_without_catalog_software(self):
        agent = Agent(WINDOWS, "192.168.43.50")
        agent.enroll()
        agent.checkin(WINDOWS_INV)
        dev_id = device(agent.uid)["id"]
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertIn('value="0b3d6c4e-1111-4a5b-9c2d-aaaaaaaaaaaa"', page)
        self.assertEqual(self.select(dev_id, only=["0b3d6c4e-1111-4a5b-9c2d-aaaaaaaaaaaa"]).status_code, 302)
        sent = agent.checkin().get_json()["commands"][0]
        self.assertEqual(sent["only"], {"updates": ["0b3d6c4e-1111-4a5b-9c2d-aaaaaaaaaaaa"]})
        self.assertNotIn("apps", sent)                                    # logiciels du catalogue : par leur propre bouton
        # Inventaire d'un agent sans identifiants : pas de choix une par une.
        agent.checkin({"updates": [{"name": "KB sans id", "version": "KB1"}]})
        self.assertNotIn('name="only"', self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True))

    def test_live_progress_without_reloading_the_page(self):
        agent, dev_id = self.linux()
        self.select(dev_id, only=["openssl", "curl"])
        cmd = agent.checkin().get_json()["commands"][0]
        agent.post("/agent/api/result", {"id": cmd["id"], "state": "running", "output": "", "exit_code": None,
                                         "progress": {"step": "updates", "phase": "install", "app": "openssl",
                                                      "percent": 57}}, agent.token)
        live = self.admin.c.get(f"/fleet/{dev_id}/progress", headers={"X-Requested-With": "fetch"}).get_json()
        self.assertEqual(live["commands"][0]["percent"], 57)
        self.assertEqual(live["commands"][0]["app"], "openssl")
        self.assertIn("Installation · openssl", live["commands"][0]["label"])
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertIn('width="57" height="6" data-cmd-bar', page)
        self.assertIn('data-update-row="openssl"', page)
        # La progression ne change pas la signature de rechargement (suivie en direct, sans recharger).
        before = self.admin.c.get(f"/fleet/{dev_id}", headers={"X-Requested-With": "fetch"}).get_json()["signature"]
        agent.post("/agent/api/result", {"id": cmd["id"], "state": "running", "output": "", "exit_code": None,
                                         "progress": {"step": "updates", "phase": "install", "app": "curl", "percent": 80}},
                   agent.token)
        after = self.admin.c.get(f"/fleet/{dev_id}", headers={"X-Requested-With": "fetch"}).get_json()["signature"]
        self.assertEqual(before, after)

    def test_live_progress_lists_each_package_with_its_outcome(self):
        agent, dev_id = self.linux()
        self.select(dev_id, only=["openssl", "curl"])
        cmd = agent.checkin().get_json()["commands"][0]
        agent.post("/agent/api/result", {"id": cmd["id"], "state": "running", "output": "", "exit_code": None,
                   "progress": {"step": "updates", "phase": "install", "app": "curl", "percent": 60,
                                "items": [{"name": "openssl", "state": "ok"}, {"name": "curl", "state": "running"},
                                          {"name": "nginx", "state": "failed"}],
                                "counts": {"ok": 1, "failed": 1, "running": 1, "total": 3}}}, agent.token)
        live = self.admin.c.get(f"/fleet/{dev_id}/progress", headers={"X-Requested-With": "fetch"}).get_json()["commands"][0]
        self.assertEqual([(i["name"], i["state"]) for i in live["items"]],
                         [("openssl", "ok"), ("curl", "running"), ("nginx", "failed")])
        self.assertEqual((live["counts"]["ok"], live["counts"]["failed"]), (1, 1))
        # Même liste en direct sur le déroulé global, et rendue dans la page du poste.
        g = self.admin.c.get("/fleet/updates", headers={"X-Requested-With": "fetch"}).get_json()
        self.assertEqual(len(g["running"][0]["items"]), 3)
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertIn('class="pkg-progress"', page)
        self.assertIn('<li class="pkg-ok">', page)
        self.assertIn('<li class="pkg-failed">', page)

    def test_mcp_update_device_with_a_selection_and_progress(self):
        _agent, dev_id = self.linux()
        token, _r = create(self.admin, "Claude", ["update_device", "get_device"])
        client = Client(token)
        result = client.data("update_device", device="srv-web", updates=["tzdata"])
        self.assertTrue(result["queued"])
        self.assertEqual(json.loads(self.commands()[-1]["params"])["only"]["packages"], ["tzdata"])
        self.assertTrue(client.call("update_device", device="srv-web", updates=["nexistepas"])["isError"])
        info = client.data("get_device", device="srv-web")
        self.assertTrue(info["updates_selectable"])
        self.assertIn("sélectionnée", info["pending_actions"][0]["label"])


class TestLinuxAgent(unittest.TestCase):
    def test_apt_status_lines_drive_progress_and_stay_out_of_the_log(self):
        sent = []
        on_line = AGENT.apt_progress(lambda *a, **k: sent.append((a, k)))
        self.assertTrue(on_line("dlstatus:1:50.0000:Retrieving file 1 of 2"))
        self.assertTrue(on_line("pmstatus:openssl:amd64:50.0000:Installing openssl (amd64)"))
        self.assertTrue(on_line("pmstatus:tzdata:100.0000:Running dpkg"))
        self.assertFalse(on_line("Setting up openssl (3.5.4-1) ..."))
        self.assertEqual(sent[0], (("updates", "download"), {"percent": 15.0}))
        self.assertEqual(sent[1], (("updates", "install"), {"app": "openssl", "percent": 65.0}))
        self.assertEqual(sent[2][1]["app"], "tzdata")

    def test_dnf_counters(self):
        sent = []
        on_line = AGENT.dnf_progress(lambda *a, **k: sent.append((a, k)))
        on_line("(2/4): openssl-libs-3.2.2-6.el9.x86_64.rpm   1.2 MB/s | 2.3 MB     00:01")
        on_line("  Upgrading        : openssl-libs-1:3.2.2-6.el9.x86_64                     3/8 ")
        self.assertEqual(sent[0][1]["app"], "openssl-libs")
        self.assertEqual((sent[0][1]["index"], sent[0][1]["total"]), (2, 4))
        self.assertEqual((sent[1][0], sent[1][1]["app"], sent[1][1]["index"]), (("updates", "install"), "openssl-libs", 3))

    def test_run_stream_reads_as_it_goes(self):
        seen = []
        code, out = AGENT.run_stream(["sh", "-c", "echo un; echo pmstatus:x:50:y; echo deux; exit 3"],
                                     lambda line: seen.append(line) or line.startswith("pmstatus"))
        self.assertEqual((code, out), (3, "un\ndeux\n"))
        self.assertEqual(seen, ["un", "pmstatus:x:50:y", "deux"])
        code, out = AGENT.run_stream(["sleep", "5"], lambda line: False, timeout=0.3)
        self.assertEqual(code, 124)

    def test_progress_is_throttled_but_follows_each_phase(self):
        posts = []
        progress = AGENT.Progress(posts.append)
        progress("updates", "prepare")
        progress("updates", "install", app="a", percent=40)
        progress("updates", "install", app="b", percent=41)              # même phase, moins d'une seconde : ignoré
        progress("flatpak")
        self.assertEqual([p["step"] + "/" + p.get("phase", "") for p in posts], ["updates/prepare", "updates/install", "flatpak/"])

    def test_apt_per_package_tracking(self):
        prog = AGENT.Progress(lambda data: None)
        on_line = AGENT._compose(AGENT.apt_progress(prog), AGENT.apt_items(prog))
        for line in ("Unpacking openssl (3.5.4-1) over (3.5.3-1) ...", "Setting up openssl (3.5.4-1) ...",
                     "Unpacking curl (8.0) ...", "dpkg: error processing package curl (--configure):"):
            on_line(line)
        prog.finalize(ok=False)
        items, counts = prog._items_view()
        states = {i["name"]: i["state"] for i in items}
        self.assertEqual((states["openssl"], states["curl"]), ("ok", "failed"))
        self.assertEqual((counts["ok"], counts["failed"], counts["total"]), (1, 1, 2))

    def test_dnf_per_package_tracking(self):
        prog = AGENT.Progress(lambda data: None)
        on_line = AGENT.dnf_items(prog)
        on_line("  Upgrading        : openssl-libs-1:3.2.2-6.el9.x86_64                     1/2 ")
        on_line("  Verifying        : openssl-libs-1:3.2.2-6.el9.x86_64                     1/2 ")
        prog.finalize(ok=True)
        items, counts = prog._items_view()
        self.assertEqual(items[0]["name"], "openssl-libs")
        self.assertEqual(counts["ok"], 1)

    def test_selected_packages_keep_their_automatic_mark(self):
        calls = []

        def run(cmd, timeout=7200):
            calls.append(cmd)
            return (0, "tzdata\n") if cmd[:2] == ["apt-mark", "showauto"] else (0, "")
        with mock.patch.object(AGENT, "family", return_value="debian"), mock.patch.object(AGENT, "run", run), \
                mock.patch.object(AGENT, "apt_update", return_value=(0, "")), \
                mock.patch.object(AGENT, "run_stream", lambda cmd, on_line, timeout=7200: (calls.append(cmd), (0, "ok"))[1]), \
                mock.patch.object(AGENT.shutil, "which", return_value=None):
            code, _out = AGENT.update({"only": {"packages": ["openssl", "tzdata", "-o=evil"]}})
        self.assertEqual(code, 0)
        apt = next(c for c in calls if c[0] == "apt-get")
        self.assertEqual(apt[-3:], ["install", "openssl", "tzdata"])     # nom invalide écarté
        self.assertIn("APT::Status-Fd=1", apt)
        self.assertIn(["apt-mark", "auto", "tzdata"], calls)


if __name__ == "__main__":
    unittest.main()
