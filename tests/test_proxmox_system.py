"""Proxmox VE, gestion du système : liaison SSH (clé de loco, clé d'hôte confirmée), relevé (versions, noyaux, simulation
d'apt-get dist-upgrade), mise à jour détachée suivie jusqu'à son code de sortie, redémarrage suivi par l'identifiant de
démarrage, lignes de Mon parc et sélection, droits. Le serveur SSH est simulé (FakePve) ; le reste est le vrai code."""
import base64
import json
import re
import time
import unittest
import uuid
from unittest import mock

import test_portal as fixture
from loco import auth, features, homeassistant, permissions, proxmox_system
from loco.db import transaction

PASSWORD = "Proxmox-Systeme-2026"
HOST_KEY = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))).decode()
SIM = """NOTE: This is only a simulation!
Reading package lists...
Calculating upgrade...
The following NEW packages will be installed:
  proxmox-kernel-7.0.14-30-pve-signed
3 upgraded, 1 newly installed, 0 to remove and 1 not upgraded.
Inst libssl3t64 [3.5.7-1~deb13u3] (3.5.9-1~deb13u1 Debian:13.8/stable, Debian-Security:13/stable-security [amd64])
Inst proxmox-kernel-7.0.14-30-pve-signed (7.0.14-30 Proxmox:9.2/stable [amd64])
Inst pve-manager [9.2.21] (9.2.22 Proxmox:9.2/stable [all])
Inst zlib1g [1:1.3.dfsg-1] (1:1.3.dfsg-1+deb13u1 Debian:13.8/stable [amd64])
Conf libssl3t64 (3.5.9-1~deb13u1 Debian:13.8/stable [amd64])
"""


class FakePve:
    """Ce que répond un Proxmox aux scripts de loco : relevé, lancement et suivi d'une mise à jour, redémarrage."""

    def __init__(self):
        self.boot, self.sim, self.kernels = str(uuid.uuid4()), SIM, ["7.0.14-15-pve", "7.0.14-23-pve"]
        self.upgrade_rc, self.unreachable, self.calls, self.polls, self.started = 0, False, [], {}, set()
        self.uptime, self.same_boot_id, self.pin = 3547934, False, ""

    def collect(self):
        return "\n".join(["@@host pve", "@@kernel 7.0.14-15-pve", f"@@boot {self.boot}", "@@uptime 3547934.26",
                          "@@pve pve-manager/9.2.21/4f6e0ac86f9e8c7f (running kernel: 7.0.14-15-pve)"]
                         + [f"@@kernelfile {k}" for k in self.kernels] + [f"@@pin {self.pin}", "@@nextpin "]
                         + ["@@lists 1795295284", "@@self 109.conf", "@@sim", self.sim, "@@simrc 0"]) + "\n"

    def run(self, row, script, timeout=40):
        self.calls.append(script)
        if self.unreachable:
            return 255, "ssh: connect to host 192.0.2.10 port 22: Connection refused"
        if script.startswith("export LC_ALL=C PATH="):
            return 0, self.collect()
        job = re.search(r"\bid=(\d+)", script)
        if "LOCO_UPGRADE" in script:
            self.started.add(job.group(1))
            return 0, "@@started\n"
        if "@@tail" in script:
            jid = job.group(1)
            if jid not in self.started:
                return 0, "@@log no\n@@active inactive\n@@tail\n"
            self.polls[jid] = self.polls.get(jid, 0) + 1
            log = "== apt-get update\nHit:1 http://deb.debian.org/debian trixie InRelease\n== apt-get dist-upgrade\nSetting up pve-manager (9.2.22) ...\n"
            if self.polls[jid] < 2:
                return 0, "@@log yes\n@@active active\n@@tail\n" + log
            return 0, f"@@log yes\n@@rc {self.upgrade_rc}\n@@active inactive\n@@tail\n" + log + f"== exit code {self.upgrade_rc}\n"
        if script.startswith("rm -f /var/lib/loco-upgrade"):
            return 0, ""
        if script == proxmox_system.BOOT_ID:
            return 0, f"{self.boot} {self.uptime}\n"
        if "systemctl reboot" in script:
            if self.same_boot_id:                     # conteneur : l'identifiant reste celui de l'hôte, la durée repart
                self.uptime = 4
            else:
                self.boot = str(uuid.uuid4())         # il revient avec un autre identifiant de démarrage
            return 0, "@@reboot\n"
        if "apt-get update -q" in script:
            return 0, "Hit:1 http://deb.debian.org/debian trixie InRelease\nReading package lists..."
        raise AssertionError("script inattendu : " + script[:120])


class ProxmoxSystemTest(unittest.TestCase):
    def setUp(self):
        self.pve = FakePve()
        for patch in (mock.patch.object(proxmox_system, "run", self.pve.run), mock.patch.object(proxmox_system, "POLL", 0.01),
                      mock.patch.object(proxmox_system, "STEP", 0.01), mock.patch.object(proxmox_system, "LOST", 0.3),
                      mock.patch.object(homeassistant, "ssh_binary", lambda: "/usr/bin/ssh")):
            patch.start()
            self.addCleanup(patch.stop)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["proxmox"]'))
            conn.execute("DELETE FROM proxmox_hosts")
            # Parc vide : les postes laissés par d'autres essais (mises à jour « en cours ») feraient attendre les
            # redémarrages décidés depuis Mon parc, et compteraient dans « Tout mettre à jour ».
            conn.execute("DELETE FROM devices")
        self.addCleanup(self.reset)
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)

    def reset(self):
        self.idle()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
            conn.execute("DELETE FROM proxmox_hosts")
            conn.execute("DELETE FROM devices WHERE uid='px-vm'")
            conn.execute("DELETE FROM users WHERE username LIKE 'px-%'")
            conn.execute("DELETE FROM access_roles WHERE id LIKE 'px-%'")

    def idle(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end and (proxmox_system._WORKERS or proxmox_system._REFRESH):
            time.sleep(0.02)
        self.assertFalse(proxmox_system._WORKERS, "tâches encore en cours")

    def host(self, ssh=True):
        """Un Proxmox relié par son jeton (relevé de l'API frais : rien n'est redemandé), et par SSH si demandé."""
        api = {"version": "9.2.21", "storages": [], "nodes": ["pve"], "missing": [],
               "guests": [{"vmid": 109, "name": "loco", "type": "qemu", "node": "pve", "status": "running", "template": False, "backups": [], "count": 0},
                          {"vmid": 116, "name": "web", "type": "lxc", "node": "pve", "status": "stopped", "template": False, "backups": [], "count": 0}],
               "node_status": {"pve": {"online": True, "cpu": 12.5, "cpus": 8, "mem": 8 * 2 ** 30, "mem_total": 32 * 2 ** 30,
                                       "disk": 20 * 2 ** 30, "disk_total": 100 * 2 ** 30, "uptime": 3600 * 30}}}
        with transaction() as conn:
            host_id = conn.execute("INSERT INTO proxmox_hosts (name, host, port, token_id, secret, fingerprint, info, checked, created_at, host_key) "
                                   "VALUES ('pve', '192.0.2.10', 8006, 'loco@pve!loco', 'x', ?, ?, ?, '2026-11-22 10:00:00', ?)",
                                   ("AB" * 32, json.dumps(api), time.time(), HOST_KEY if ssh else "")).lastrowid
        if ssh:
            proxmox_system.refresh(host_id)
        return self.row(host_id)

    def row(self, host_id):
        with transaction() as conn:
            return conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()

    def jobs(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM proxmox_system_jobs ORDER BY id").fetchall()

    def page(self, host_id, browser=None):
        return (browser or self.admin).c.get(f"/proxmox/{host_id}").get_data(as_text=True)

    def post(self, path, data=None, browser=None, page=None):
        return (browser or self.admin).post(path, data, page=page or path.rsplit("/system", 1)[0].rsplit("/ssh", 1)[0])

    def member(self, name, grants):
        with transaction() as conn:
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("px-" + name, "Px " + name, permissions.role_key("Px " + name), json.dumps(sorted(permissions.expand(grants)))))
            auth.create_user(conn, "px-" + name, PASSWORD, "px-" + name, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login("px-" + name, PASSWORD).status_code, 302)
        return browser


class TestSummary(unittest.TestCase):
    def test_what_the_upgrade_would_do_is_read_from_the_simulation(self):
        pve = FakePve()
        pve.sim = SIM + "Remv pve-old-tool [1.0]\n"
        info = proxmox_system.summarize(pve.collect())
        self.assertEqual((info["hostname"], info["pve"], info["kernel"], info["self"], info["uptime"]), ("pve", "9.2.21", "7.0.14-15-pve", 109, 3547934))
        self.assertEqual([u["name"] for u in info["updates"]][0], "libssl3t64")          # sécurité d'abord
        self.assertEqual((len(info["updates"]), info["security"], info["held"], info["removals"]), (4, 1, 1, ["pve-old-tool"]))
        kernel = next(u for u in info["updates"] if u["name"].startswith("proxmox-kernel"))
        self.assertEqual((kernel["old"], kernel["new"], kernel["fresh"]), ("", "7.0.14-30", True))
        self.assertTrue(info["kernel_update"])
        # Un noyau plus récent que celui en marche est installé : redémarrage requis (tri numérique, pas alphabétique).
        self.assertEqual((info["newest_kernel"], info["reboot_required"]), ("7.0.14-23-pve", True))
        pve.kernels = ["6.17.2-1-pve", "6.17.13-21-pve", "7.0.14-15-pve"]
        self.assertFalse(proxmox_system.summarize(pve.collect())["reboot_required"])

    def test_the_log_keeps_only_the_last_state_of_a_progress_line(self):
        log = "(Reading database ... 5%\r(Reading database ... 95%\r(Reading database ... 18937 files installed.)\r\nSetting up apt (3.0.4) ...\r\n"
        self.assertEqual(proxmox_system.tidy(log), "(Reading database ... 18937 files installed.)\nSetting up apt (3.0.4) ...\n")

    def test_a_pinned_kernel_is_the_one_that_boots(self):
        """proxmox-boot-tool kernel pin : le serveur redémarre toujours sur ce noyau. Il tourne déjà : aucun redémarrage
        requis, même si un plus récent est installé ; un autre noyau épinglé, lui, en demande un."""
        pve = FakePve()
        pve.pin = "7.0.14-15-pve"
        info = proxmox_system.summarize(pve.collect())
        self.assertEqual((info["pinned"], info["boot_kernel"], info["reboot_required"]), ("7.0.14-15-pve", "7.0.14-15-pve", False))
        pve.pin = "7.0.14-23-pve"
        self.assertTrue(proxmox_system.summarize(pve.collect())["reboot_required"])

    def test_the_collect_script_only_reads(self):
        script = proxmox_system.collect_script(["bc:24:11:41:65:50", "pas-une-mac; rm -rf /"])
        self.assertIn("apt-get -s -q -o Debug::NoLocking=1 dist-upgrade", script)
        self.assertIn("bc:24:11:41:65:50", script)
        self.assertNotIn("rm -rf", script)                                              # adresses MAC validées
        self.assertNotRegex(script, r"apt-get (?!-s)\S*\s*(?:install|upgrade|dist-upgrade|update)")


class TestLink(ProxmoxSystemTest):
    def test_backups_only_until_linked_over_ssh(self):
        row = self.host(ssh=False)
        html = self.page(row["id"])
        self.assertIn("Autoriser la clé de loco sur Proxmox", html)
        self.assertIn("&gt;&gt; /root/.ssh/authorized_keys", html)                   # la commande à lancer sur le nœud
        self.assertIn("ssh-ed25519", html)
        self.assertIn("sauvegardes seulement", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.post(f"/proxmox/{row['id']}/system/reboot").status_code, 409)

    def test_the_host_key_is_confirmed_before_anything(self):
        row = self.host(ssh=False)
        with mock.patch.object(homeassistant, "probe_host_key", return_value=("ssh-ed25519", HOST_KEY.split()[1])) as probe:
            self.post(f"/proxmox/{row['id']}/ssh/link", {"ssh_host": "192.0.2.10", "ssh_port": "22", "ssh_user": "root"})
        self.assertEqual(probe.call_args[0][:2], ("192.0.2.10", 22))
        self.assertEqual(self.row(row["id"])["host_key"], "")                           # rien d'enregistré avant la confirmation
        html = self.page(row["id"])
        self.assertIn(homeassistant.fingerprint(HOST_KEY.split()[1]), html)
        nonce = re.search(r'name="nonce" value="([0-9a-f]+)"', html).group(1)
        self.post(f"/proxmox/{row['id']}/ssh/confirm", {"nonce": nonce})
        linked = self.row(row["id"])
        self.assertEqual((linked["host_key"], linked["ssh_user"], linked["ssh_port"], linked["ssh_host"]), (HOST_KEY, "root", 22, ""))
        self.assertEqual(json.loads(linked["sys_info"])["pve"], "9.2.21")
        with transaction() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM audit WHERE action='proxmox.ssh-liaison'").fetchone())

    def test_a_refused_key_is_said_plainly(self):
        row = self.host()
        self.pve.unreachable = True
        self.assertIn("Connection refused", proxmox_system.refresh(row["id"]))
        with mock.patch.object(proxmox_system, "run", return_value=(255, "root@192.0.2.10: Permission denied (publickey).")):
            self.assertIn("/root/.ssh/authorized_keys", proxmox_system.refresh(row["id"]))
        self.assertIn("liaison SSH en échec", self.admin.c.get("/fleet").get_data(as_text=True))

    def test_unlinking_keeps_the_backups(self):
        row = self.host()
        self.post(f"/proxmox/{row['id']}/ssh/unlink")
        after = self.row(row["id"])
        self.assertEqual((after["host_key"], after["sys_info"], after["fingerprint"]), ("", "", "AB" * 32))


class TestPage(ProxmoxSystemTest):
    def test_the_page_tells_what_matters(self):
        row = self.host()
        html = self.page(row["id"])
        for text in ("Proxmox VE 9.2.21", "4 mises à jour", "redémarrage requis", "dont 1 de sécurité", "7.0.14-23-pve installé",
                     "loco tourne sur ce Proxmox (machine 109", "libssl3t64", "Un nouveau noyau fait partie des mises à jour",
                     "Rechercher les mises à jour", 'id="px-update"', "1 paquet retenu"):
            self.assertIn(text, html)

    def test_a_pinned_kernel_is_said(self):
        self.pve.pin = "7.0.14-15-pve"
        row = self.host()
        html = self.page(row["id"])
        self.assertIn("épinglé · 7.0.14-23-pve installé", html)
        self.assertNotIn("redémarrage requis", html)
        self.assertIn("le noyau 7.0.14-15-pve est épinglé", html)

    def test_an_upgrade_that_would_remove_proxmox_is_refused(self):
        self.pve.sim = SIM + "Remv proxmox-ve [9.2.0]\n"
        row = self.host()
        self.assertIn("retirerait le paquet proxmox-ve", self.page(row["id"]))
        self.post(f"/proxmox/{row['id']}/system/update")
        self.assertEqual(self.jobs(), [])


class TestJobs(ProxmoxSystemTest):
    def test_an_upgrade_runs_detached_and_is_followed_to_its_end(self):
        row = self.host()
        self.post(f"/proxmox/{row['id']}/system/update")
        self.idle()
        job = self.jobs()[0]
        self.assertEqual((job["action"], job["state"], job["created_by"]), ("update", "success", "admin"))
        self.assertIn("Setting up pve-manager", job["output"])
        started = next(s for s in self.pve.calls if "LOCO_UPGRADE" in s)
        self.assertIn("dist-upgrade", started)
        self.assertIn("systemd-run", started)
        self.assertNotIn("apt-get upgrade", started)                                    # jamais apt-get upgrade sur Proxmox
        self.assertTrue(any(s.startswith("rm -f /var/lib/loco-upgrade") for s in self.pve.calls))   # rangé après

    def test_then_reboot_waits_for_a_new_boot(self):
        row = self.host()
        before = self.pve.boot
        self.post(f"/proxmox/{row['id']}/system/update", {"reboot": "1"})
        self.idle()
        update, reboot = self.jobs()
        self.assertEqual((update["state"], reboot["action"], reboot["state"]), ("success", "reboot-after", "success"))
        self.assertEqual(reboot["boot_id"], before)
        self.assertNotEqual(self.pve.boot, before)

    def test_a_failed_upgrade_cancels_the_reboot(self):
        row = self.host()
        self.pve.upgrade_rc = 100
        self.post(f"/proxmox/{row['id']}/system/update", {"reboot": "1"})
        self.idle()
        update, reboot = self.jobs()
        self.assertEqual((update["state"], reboot["state"]), ("failed", "failed"))
        self.assertIn("n'a pas été redémarré", reboot["output"])
        self.assertFalse(any("systemctl reboot" in s for s in self.pve.calls))

    def test_a_restart_is_seen_by_the_uptime_too(self):
        row = self.host()
        self.pve.same_boot_id = True
        self.post(f"/proxmox/{row['id']}/system/reboot")
        self.idle()
        self.assertEqual(self.jobs()[0]["state"], "success")

    def test_check_and_reboot(self):
        row = self.host()
        self.post(f"/proxmox/{row['id']}/system/check")
        self.idle()
        self.post(f"/proxmox/{row['id']}/system/reboot")
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()], [("check", "success"), ("reboot", "success")])

    def test_a_job_left_running_by_a_stop_of_loco_is_not_started_late(self):
        row = self.host()
        with transaction() as conn:
            conn.execute("INSERT INTO proxmox_system_jobs (host_id, action, state, created_at, started_at) VALUES (?, 'update', 'running', ?, ?)",
                         (row["id"], "2026-11-22 08:00:00", "2026-11-22 08:00:00"))
            conn.execute("INSERT INTO proxmox_system_jobs (host_id, action, state, created_at, started_at) VALUES (?, 'reboot', 'running', ?, ?)",
                         (row["id"], "2026-11-22 08:00:00", "2026-11-22 08:00:00"))
        proxmox_system.start()
        self.idle()
        update, reboot = self.jobs()
        self.assertEqual((update["state"], reboot["state"]), ("failed", "failed"))
        self.assertIn("rien n'a été changé", update["output"])
        self.assertIn("n'a pas été redémarré", reboot["output"])
        self.assertFalse(any("LOCO_UPGRADE" in s or "systemctl reboot" in s for s in self.pve.calls))

    def test_an_upgrade_resumed_after_a_stop_of_loco_is_followed(self):
        row = self.host()
        with transaction() as conn:
            job_id = conn.execute("INSERT INTO proxmox_system_jobs (host_id, action, state, created_at, started_at) VALUES (?, 'update', 'running', ?, ?)",
                                  (row["id"], "2026-11-22 08:00:00", "2026-11-22 08:00:00")).lastrowid
        self.pve.started.add(str(job_id))                                               # elle a continué sur Proxmox
        proxmox_system.start()
        self.idle()
        self.assertEqual(self.jobs()[0]["state"], "success")


class TestFleet(ProxmoxSystemTest):
    def test_the_server_is_a_row_of_my_fleet(self):
        row = self.host()
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn(f'data-id="px-{row["id"]}"', html)
        self.assertIn(f"/proxmox/{row['id']}/fleet-update", html)
        self.assertIn("Proxmox VE 9.2.21", html)
        self.assertIn("4 mises à jour", html)
        self.assertIn("12 %", html)                                                     # processeur relevé par l'API (12,5 %)
        self.assertEqual(self.admin.post(f"/proxmox/{row['id']}/fleet-update", page="/fleet").status_code, 302)
        self.idle()
        self.assertEqual(self.jobs()[0]["action"], "update")

    def test_one_status_dot_per_row(self):
        """Comme un poste : « à jour », une seule pastille, et « redémarrage nécessaire » noté à part en dessous."""
        self.pve.sim = "Reading package lists...\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n"
        self.host()
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        row = html[html.index('<tr class="ha-row px-row">'):]
        cell = row[row.index('data-label="Mises à jour"'):row.index('data-label="Ressources"')]
        self.assertIn('<span class="muted">à jour</span>', cell)
        self.assertIn('class="reboot-note">', cell)
        self.assertIn("redémarrage nécessaire", cell)
        self.assertNotIn("redémarrage requis", cell)
        self.assertEqual(cell.count('class="st '), 1)
        self.assertIn('class="st ok"', cell)                                           # vert, comme un poste à jour

    def test_selection_updates_and_reboots_like_any_device(self):
        row = self.host()
        tok = self.admin.token("/fleet")
        got = self.admin.c.post("/fleet/update-selected", data={"csrf": tok, "ids": f"px-{row['id']}"}, content_type="multipart/form-data")
        self.assertEqual(got.get_json()["count"], 1)
        self.idle()
        got = self.admin.c.post("/fleet/reboot-selected", data={"csrf": tok, "ids": f"px-{row['id']}"}, content_type="multipart/form-data")
        self.assertEqual((got.get_json()["count"], got.get_json()["skipped"]), (1, 0))
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()], [("update", "success"), ("reboot-wait", "success")])
        self.assertIn("systemctl reboot", "".join(self.pve.calls))
        self.assertEqual(self.admin.c.post("/fleet/wake-selected", data={"csrf": tok, "ids": f"px-{row['id']}"},
                                           content_type="multipart/form-data").status_code, 400)    # aucun poste à réveiller

    def test_update_then_reboot_only_if_the_update_requires_it(self):
        row = self.host()
        tok = self.admin.token("/fleet")
        self.admin.c.post("/fleet/update-selected", data={"csrf": tok, "ids": f"px-{row['id']}", "reboot": "1"},
                          content_type="multipart/form-data")
        self.idle()
        self.assertEqual([(j["action"], j["state"]) for j in self.jobs()], [("update", "success"), ("reboot-needed", "success")])
        self.assertIn("systemctl reboot", "".join(self.pve.calls))                  # nouveau noyau : redémarré
        self.clear_jobs()
        self.pve.calls.clear()
        self.pve.pin = "7.0.14-15-pve"                                               # noyau épinglé : rien à redémarrer
        proxmox_system.refresh(row["id"])
        self.admin.c.post("/fleet/update-selected", data={"csrf": tok, "ids": f"px-{row['id']}", "reboot": "1"},
                          content_type="multipart/form-data")
        self.idle()
        jobs = self.jobs()
        self.assertEqual([(j["action"], j["state"]) for j in jobs], [("update", "success"), ("reboot-needed", "success")])
        self.assertEqual(jobs[1]["output"], "Aucun redémarrage nécessaire après la mise à jour.")
        self.assertNotIn("systemctl reboot", "".join(self.pve.calls))

    def test_a_reboot_from_my_fleet_waits_for_the_devices_updates(self):
        row = self.host()
        cmd = self.device_updating()
        with mock.patch.object(proxmox_system, "FLEET_STEP", 0.02):
            self.assertEqual(self.admin.post(f"/proxmox/{row['id']}/fleet-reboot", {}, page="/fleet").status_code, 302)
            deadline = time.time() + 5
            while time.time() < deadline and "En attente" not in (self.jobs()[0]["output"] or ""):
                time.sleep(0.02)
            job = self.jobs()[0]
            self.assertEqual((job["action"], job["state"]), ("reboot-wait", "running"))
            self.assertEqual(job["output"], "En attente de la fin des mises à jour du parc : 1 poste en cours.")
            self.assertNotIn("systemctl reboot", "".join(self.pve.calls))            # rien d'envoyé tant que le poste installe
            with transaction() as conn:
                conn.execute("UPDATE device_commands SET state='success' WHERE id=?", (cmd,))
            self.idle()
        self.assertEqual(self.jobs()[0]["state"], "success")
        self.assertIn("systemctl reboot", "".join(self.pve.calls))

    def test_the_wait_is_bounded(self):
        row = self.host()
        self.device_updating()
        with mock.patch.object(proxmox_system, "FLEET_STEP", 0.02), mock.patch.object(proxmox_system, "FLEET_WAIT", 0.3):
            self.admin.post(f"/proxmox/{row['id']}/fleet-reboot", {}, page="/fleet")
            self.idle()
        self.assertEqual(self.jobs()[0]["state"], "success")                       # au bout du délai, il redémarre
        self.assertIn("systemctl reboot", "".join(self.pve.calls))

    def test_update_all_includes_the_server_but_not_a_group(self):
        self.host()
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("Tout mettre à jour (1)", html)
        self.assertIn("Home Assistant et Proxmox se mettent à jour aussitôt", html)
        tok = self.admin.token("/fleet")
        self.assertEqual(self.admin.c.post("/fleet/update-all", data={"csrf": tok, "reboot": "1"}).status_code, 302)
        self.idle()
        self.assertEqual([j["action"] for j in self.jobs()], ["update", "reboot-needed"])
        self.clear_jobs()
        with transaction() as conn:
            gid = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Bureau-px', '2026-11-25 10:00:00')").lastrowid
        self.admin.c.post("/fleet/update-all", data={"csrf": tok, "group": str(gid)})
        self.idle()
        self.assertEqual(self.jobs(), [])                                           # un groupe ne contient que des postes
        with transaction() as conn:
            conn.execute("DELETE FROM device_groups WHERE id=?", (gid,))

    def test_filters_search_and_counts_treat_the_server_like_a_device(self):
        row = self.host()
        seen = lambda query: f"/proxmox/{row['id']}" in self.admin.c.get("/fleet" + query).get_data(as_text=True)    # noqa: E731
        self.assertTrue(seen(""))
        self.assertTrue(seen("?os=proxmox"))
        self.assertFalse(seen("?os=windows"))
        self.assertTrue(seen("?q=pve"))
        self.assertFalse(seen("?q=introuvable"))
        self.assertTrue(seen("?statut=orange"))
        self.assertFalse(seen("?statut=green"))
        self.assertTrue(seen("?voir=redemarrage"))                                 # nouveau noyau installé
        self.assertFalse(seen("?voir=hors-ligne"))
        html = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("Proxmox VE<span class=\"menu-count\">1</span>", html)        # famille du filtre Système
        self.assertIn(f"/proxmox/{row['id']}/fleet-reboot", html)                  # redémarrer depuis sa ligne
        self.assertIn("redémarrage nécessaire", html)
        found = self.admin.c.get("/fleet/search.json").get_json()["devices"]
        self.assertIn(("pve", f"/proxmox/{row['id']}"), [(d["title"], d["href"]) for d in found])

    def test_the_updates_feed_follows_the_server(self):
        row = self.host()
        self.admin.post(f"/proxmox/{row['id']}/fleet-update", {}, page="/fleet")
        self.idle()
        html = self.admin.c.get("/fleet/updates").get_data(as_text=True)
        self.assertIn("Mise à jour du système", html)
        self.assertIn(f'href="/proxmox/{row["id"]}"', html)
        self.assertIn("Réussie", html)

    def device_updating(self):
        """Un poste du parc, en ligne, en pleine mise à jour ; renvoie le numéro de sa commande."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            dev = conn.execute("INSERT INTO devices (uid, token_hash, hostname, os_family, os_name, enrolled_at, last_seen) "
                               "VALUES ('px-vm', 'x', 'px-vm', 'debian', 'Debian GNU/Linux 13', ?, ?)", (now, now)).lastrowid
            return conn.execute("INSERT INTO device_commands (device_id, action, state, created_at) VALUES (?, 'update', 'running', ?)",
                                (dev, now)).lastrowid

    def clear_jobs(self):
        with transaction() as conn:
            conn.execute("DELETE FROM proxmox_system_jobs")

    def test_rights(self):
        row = self.host()
        viewer = self.member("ro", ["proxmox.view", "fleet.view"])
        self.assertIn("4 mises à jour", viewer.c.get("/fleet").get_data(as_text=True))
        self.assertNotIn(f"/proxmox/{row['id']}/fleet-update", viewer.c.get("/fleet").get_data(as_text=True))
        html = self.page(row["id"], viewer)
        self.assertNotIn('id="px-update"', html)
        self.assertNotIn("/shell", html)
        for path in ("system/update", "system/reboot", "shell", "ssh/unlink"):
            self.assertEqual(viewer.c.post(f"/proxmox/{row['id']}/{path}", data={"csrf": viewer.token(f"/proxmox/{row['id']}")}).status_code, 403, path)
        self.assertEqual(self.jobs(), [])

    def test_the_overview_says_what_to_do(self):
        self.host()
        html = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Proxmox « pve » : 4 mises à jour disponibles.", html)
        self.assertIn("Proxmox « pve » : redémarrage requis (noyau 7.0.14-23-pve installé).", html)

    def test_off_with_the_feature(self):
        row = self.host()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertNotIn("px-row", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.admin.c.get(f"/proxmox/{row['id']}").status_code, 403)



class TestTerminal(ProxmoxSystemTest):
    def test_a_proxmox_terminal_is_kept_while_allowed(self):
        """Une session hors du parc n'est plus prise pour celle d'un Home Assistant : gardée tant que le droit et la
        fonctionnalité Proxmox le permettent, fermée sinon."""
        from loco import remote
        row = self.host()
        now = time.time()
        with transaction() as conn:
            admin = conn.execute("SELECT id, pw_version FROM users WHERE username='admin'").fetchone()
            conn.execute("INSERT INTO remote_sessions (id,user_id,owner,pw_version,kind,created,browser_seen,agent_seen,expires,target) "
                         "VALUES ('px-session',?,?,?,'ssh',?,?,?,?,?)", (admin["id"], "x", admin["pw_version"], now, now, now, now + 3600, f"proxmox:{row['id']}"))

        def state():
            with fixture.loco.app.test_request_context():
                remote._prune()
                from loco.db import db
                db().commit()
                return db().execute("SELECT state FROM remote_sessions WHERE id='px-session'").fetchone()["state"]
        self.assertEqual(state(), "waiting")
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertEqual(state(), "closed")
        with transaction() as conn:
            conn.execute("DELETE FROM remote_sessions WHERE id='px-session'")

if __name__ == "__main__":
    unittest.main()
