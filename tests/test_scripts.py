"""Scripts : bibliothèque versionnée, exécution figée par poste compatible, droits revérifiés, agent Linux réel."""
import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import unittest

import test_permissions
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from loco import features
from loco.db import transaction

ROOT = Path(__file__).resolve().parents[1]


class TestScripts(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM scripts"); conn.execute("DELETE FROM script_runs")
            conn.execute("DELETE FROM audit WHERE action LIKE 'scripts.%'")         # tests indépendants les uns des autres
            conn.execute("DELETE FROM settings WHERE key=?", (features.SETTING,))
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        self.win = Agent(WINDOWS, "192.168.43.50"); self.win.enroll()
        self.lin = Agent(LINUX, "192.168.43.60"); self.lin.enroll()

    def save(self, sid=0, **form):
        values = {"name": "Nettoyage", "platform": "windows", "description": "", "content": "Remove-Item $env:TEMP\\* -Recurse"}
        values.update(form)
        path = f"/fleet/scripts/{sid}/save" if sid else "/fleet/scripts/save"
        r = self.admin.post(path, values, page="/fleet/scripts")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return int(r.headers["Location"].rstrip("/").split("/")[-1])

    def schedule_script(self, sid, **extra):
        values = {"version": "1", "device": str(device(self.lin.uid)["id"]), "when": "schedule", "recurrence": "daily",
                  "at_time": "02:00", "start_date": datetime.date.today().isoformat()}
        values.update(extra)
        return self.admin.post(f"/fleet/scripts/{sid}/run", values, page="/fleet/scripts")

    def make_due(self):
        past = (datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            conn.execute("UPDATE schedules SET next_run=? WHERE next_run!=''", (past,))

    def test_scheduled_script_runs_at_each_occurrence(self):
        with transaction() as conn:
            conn.execute("DELETE FROM schedules")
        sid = self.save(platform="linux", content="echo bonjour")
        r = self.schedule_script(sid)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/fleet/schedules", r.headers["Location"])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM script_runs").fetchone()[0], 0)   # rien avant l'heure
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])
        self.make_due()
        self.assertEqual([c["action"] for c in self.lin.checkin().get_json()["commands"]], ["run-script"])
        with transaction() as conn:
            sched = conn.execute("SELECT * FROM schedules").fetchone()
            run = conn.execute("SELECT * FROM script_runs").fetchone()
        self.assertEqual((sched["last_ref"], sched["last_result"], run["version"]), (run["id"], "1 poste(s)", 1))

    def test_a_script_changed_after_planning_pauses_its_schedule(self):
        with transaction() as conn:
            conn.execute("DELETE FROM schedules")
        sid = self.save(platform="linux", content="echo bonjour")
        self.schedule_script(sid)
        self.save(sid, platform="linux", content="echo autre chose")                # nouvelle version non relue
        self.make_due()
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])
        with transaction() as conn:
            sched = conn.execute("SELECT enabled, last_result FROM schedules").fetchone()
        self.assertEqual(sched["enabled"], 0)
        self.assertTrue(sched["last_result"].startswith("Script modifié"))

    def run_now(self, sid, agent):
        r = self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": str(device(agent.uid)["id"])},
                            page="/fleet/scripts")
        self.assertEqual(r.status_code, 302)
        return int(r.headers["Location"].rstrip("/").split("/")[-1])

    def test_queue_can_be_emptied_and_history_cleared(self):
        sid = self.save(platform="linux", content="echo bonjour")
        done, waiting = self.run_now(sid, self.lin), self.run_now(sid, self.lin)
        with transaction() as conn:                                     # la première est terminée, la seconde attend
            conn.execute("UPDATE device_commands SET state='success', exit_code=0 WHERE id IN "
                         "(SELECT command_id FROM script_run_targets WHERE run_id=?)", (done,))
        page = self.admin.c.get("/fleet/scripts").get_data(as_text=True)
        self.assertIn("Vider la file (1)", page)
        self.assertIn("Tout vider", page)
        # Une exécution en cours ne se supprime pas ; vider l'historique la garde.
        self.assertEqual(self.admin.post(f"/fleet/scripts/runs/{waiting}/delete", page="/fleet/scripts").status_code, 302)
        self.assertEqual(self.admin.post("/fleet/scripts/history/clear", page="/fleet/scripts").status_code, 302)
        with transaction() as conn:
            self.assertEqual([r["id"] for r in conn.execute("SELECT id FROM script_runs")], [waiting])
        # Vider la file : l'envoi en attente est annulé, l'exécution devient supprimable.
        self.assertEqual(self.admin.post("/fleet/scripts/runs/cancel-all", page="/fleet/scripts").status_code, 302)
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])
        self.assertEqual(self.admin.post(f"/fleet/scripts/runs/{waiting}/delete", page="/fleet/scripts").status_code, 302)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM script_runs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM script_run_targets").fetchone()[0], 0)

    def test_history_filters_and_clearing_rights(self):
        sid = self.save(platform="linux", content="echo bonjour")
        run = self.run_now(sid, self.lin)
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state='failed', exit_code=1 WHERE id IN "
                         "(SELECT command_id FROM script_run_targets WHERE run_id=?)", (run,))
        page = self.admin.c.get("/fleet/scripts?historique=echecs").get_data(as_text=True)
        self.assertIn(f"/fleet/scripts/runs/{run}", page)
        self.assertNotIn(f"/fleet/scripts/runs/{run}\"", self.admin.c.get("/fleet/scripts?historique=reussies").get_data(as_text=True))
        runner = test_permissions.TestPermissions.browser(
            self, "script-runner", test_permissions.TestPermissions.profile(self, "script-runner", {"scripts.view", "scripts.run", "fleet.view"}))
        self.assertNotIn("Tout vider", runner.c.get("/fleet/scripts").get_data(as_text=True))
        self.assertEqual(runner.post("/fleet/scripts/history/clear", page="/fleet/scripts").status_code, 403)
        self.assertEqual(runner.post(f"/fleet/scripts/runs/{run}/delete", page="/fleet/scripts").status_code, 403)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM script_runs").fetchone()[0], 1)

    def test_past_schedule_is_refused(self):
        sid = self.save(platform="linux", content="echo bonjour")
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.schedule_script(sid, recurrence="once", start_date=yesterday).status_code, 400)

    def test_versions_follow_content_and_runs_keep_their_snapshot(self):
        sid = self.save()
        self.save(sid, name="Nettoyage des fichiers temporaires")             # nom seul : même version
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT version FROM scripts").fetchone()[0], 1)
        self.save(sid, content="Write-Output 'v2'")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT version FROM scripts").fetchone()[0], 2)
        # Version affichée périmée : refus, pour exécuter ce qui a été relu.
        stale = self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": str(device(self.win.uid)["id"])},
                                page="/fleet/scripts")
        self.assertEqual(stale.status_code, 409)
        r = self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "2", "device": [str(device(self.win.uid)["id"]),
                            str(device(self.lin.uid)["id"])]}, page="/fleet/scripts")
        run_path = r.headers["Location"]
        self.save(sid, content="Write-Output 'v3'")                           # modifié après le lancement
        command = self.win.checkin().get_json()["commands"][0]
        self.assertEqual(command["action"], "run-script")
        self.assertEqual(command["script"]["content"], "Write-Output 'v2'")
        self.assertEqual(command["script"]["sha256"], hashlib.sha256(b"Write-Output 'v2'").hexdigest())
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])       # poste Linux ignoré à l'envoi
        self.win.post("/agent/api/result", {"id": command["id"], "state": "failed", "output": "Accès refusé", "exit_code": 5},
                      self.win.token)
        page = self.admin.c.get(run_path).get_data(as_text=True)
        self.assertIn("Accès refusé", page)
        self.assertIn("code 5", page)
        with transaction() as conn:
            actions = {r[0] for r in conn.execute("SELECT action FROM audit WHERE action LIKE 'scripts.%'")}
        self.assertEqual(actions, {"scripts.ajout", "scripts.modification", "scripts.execution"})

    def test_running_needs_its_own_right_rechecked_before_sending(self):
        sid = self.save(platform="linux", content="echo bonjour")
        reader = test_permissions.TestPermissions.browser(
            self, "scripts-lecture", test_permissions.TestPermissions.profile(self, "scripts-lecture", {"scripts.view", "fleet.view"}))
        self.assertEqual(reader.c.get("/fleet/scripts").status_code, 200)
        self.assertEqual(reader.c.get(f"/fleet/scripts/{sid}/run").status_code, 403)
        runner = test_permissions.TestPermissions.browser(
            self, "scripts-run", test_permissions.TestPermissions.profile(self, "scripts-run", {"scripts.run"}))
        r = runner.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": str(device(self.lin.uid)["id"])}, page="/account")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:                                            # droit retiré avant le contact de l'agent
            conn.execute("UPDATE access_roles SET permissions='[]' WHERE id='perm-scripts-run'")
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM device_commands WHERE action='run-script'").fetchone()[0], "cancelled")

    def test_python_script_reaches_both_a_windows_and_a_linux_machine(self):
        sid = self.save(platform="python", name="Disque", content="import shutil; print(shutil.disk_usage('/'))")
        for agent in (self.win, self.lin):
            self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": str(device(agent.uid)["id"])}, page="/fleet/scripts")
            cmd = agent.checkin().get_json()["commands"][0]
            self.assertEqual(cmd["action"], "run-script")
            self.assertEqual(cmd["script"]["platform"], "python")
        page = self.admin.c.get(f"/fleet/scripts/{sid}/run").get_data(as_text=True)
        self.assertIn("Python", page)

    def test_feature_off_hides_scripts_and_holds_commands(self):
        sid = self.save(platform="linux", content="echo bonjour")
        self.admin.post(f"/fleet/scripts/{sid}/run", {"version": "1", "device": str(device(self.lin.uid)["id"])}, page="/fleet/scripts")
        form = {k: "1" for k in features.FEATURES if k != "scripts"}
        self.admin.post("/administration/features", form, page="/administration/features")
        self.assertEqual(self.admin.c.get("/fleet/scripts").status_code, 403)
        self.assertNotIn("/fleet/scripts", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.lin.checkin().get_json()["commands"], [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM device_commands WHERE action='run-script'").fetchone()[0], "queued")


@unittest.skipUnless(os.name == "posix", "Agent Linux")
class TestLinuxAgentScript(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_scripts", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.agent.LANG = "fr"  # messages en français (langue de référence) pour des assertions stables

    def command(self, content, sha=None, platform="linux"):
        return {"script": {"platform": platform, "content": content, "sha256": sha or hashlib.sha256(content.encode()).hexdigest()}}

    def test_runs_the_verified_script_and_reports_code_and_output(self):
        code, out = self.agent.run_script(self.command("echo été\necho erreur >&2\nexit 4"))
        self.assertEqual((code, out), (4, "été\nerreur"))
        self.assertEqual(self.agent.run_script(self.command("#!/bin/sh\necho direct")), (0, "direct"))
        self.assertEqual(self.agent.run_script(self.command("echo pirate", sha="0" * 64))[0], 1)
        self.assertEqual(self.agent.run_script(self.command("echo x", platform="windows")),
                         (1, "Script destiné à un autre système."))

    def test_runs_a_python_script_with_the_interpreter(self):
        code, out = self.agent.run_script(self.command("import sys\nprint('py', 1 + 1)\nsys.exit(3)", platform="python"))
        self.assertEqual((code, out), (3, "py 2"))


if __name__ == "__main__":
    unittest.main()
