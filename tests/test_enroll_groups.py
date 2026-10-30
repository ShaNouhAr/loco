"""Inscription directement dans un groupe de machines : clé propre au groupe, acceptée par l'inscription de l'agent,
les scripts, les paquets et le MSI, renouvelable, et page Enrôlement qui bascule sur elle."""
import os
import re
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, CA_PEM, LINUX, WINDOWS, device
from test_scope import ScopeTest
from loco import fleet
from loco.db import transaction


class TestGroupEnrollment(ScopeTest):
    def group_key(self, gid, browser=None):
        page = (browser or self.admin).c.get(f"/enrollment?groupe={gid}").get_data(as_text=True)
        found = re.search(r"key=(g\d+-[A-Za-z0-9_-]+)", page)
        return found.group(1) if found else None

    def members(self, gid):
        with transaction() as conn:
            return {r[0] for r in conn.execute("SELECT device_id FROM device_group_members WHERE group_id=?", (gid,))}

    def test_an_agent_enrolled_with_a_group_key_joins_the_group(self):
        key = self.group_key(self.batb)
        self.assertTrue(key.startswith(f"g{self.batb}-"))
        self.assertEqual(self.group_key(self.batb), key)                 # stable d'une page à l'autre
        agent = Agent(dict(WINDOWS, hostname="pc-bat-b-03"), "192.168.43.73")
        self.assertEqual(agent.enroll(key=key).status_code, 200)
        self.assertIn(device(agent.uid)["id"], self.members(self.batb))
        with transaction() as conn:
            detail = conn.execute("SELECT detail FROM audit WHERE action='parc.inscription' ORDER BY id DESC").fetchone()[0]
        self.assertIn("groupe Bâtiment B", detail)
        # Réinscription d'un poste existant avec la clé d'un autre groupe : il le rejoint, sans quitter le sien.
        self.assertEqual(self.agents["b1"].enroll(key=self.group_key(self.servers)).status_code, 200)
        self.assertIn(self.ids["b1"], self.members(self.servers))
        self.assertIn(self.ids["b1"], self.members(self.batb))
        # La clé du serveur inscrit toujours, sans groupe.
        plain = Agent(dict(LINUX, hostname="srv-libre"), "192.168.43.91")
        self.assertEqual(plain.enroll().status_code, 200)
        self.assertNotIn(device(plain.uid)["id"], self.members(self.batb) | self.members(self.servers))

    def test_scripts_and_packages_accept_it_and_bad_keys_are_refused(self):
        key = self.group_key(self.batb)
        secret = key.split("-", 1)[1]
        public = fixture.loco.app.test_client()                         # téléchargé par le poste, sans session
        script = public.get(f"/agent/linux.sh?key={key}")
        self.assertEqual(script.status_code, 200)
        self.assertIn(f"--key '{key}'", script.get_data(as_text=True))
        for bad in (f"g{self.batb}-{'x' * 24}", f"g{self.servers}-{secret}", f"g999-{secret}", key + "x", "g0-" + secret):
            self.assertEqual(Agent(LINUX, "192.168.43.90").enroll(key=bad).status_code, 403, bad)
            self.assertEqual(public.get(f"/agent/linux.sh?key={bad}").status_code, 403, bad)
        # Nouvelle clé : l'ancienne n'inscrit plus.
        r = self.admin.post(f"/fleet/groups/{self.batb}/enroll-key", page=f"/enrollment?groupe={self.batb}")
        self.assertEqual(r.status_code, 302)
        renewed = self.group_key(self.batb)
        self.assertNotEqual(renewed, key)
        self.assertEqual(Agent(LINUX, "192.168.43.92").enroll(key=key).status_code, 403)
        self.assertEqual(public.get(f"/agent/linux.sh?key={key}").status_code, 403)
        # Groupe supprimé : sa clé non plus.
        with transaction() as conn:
            conn.execute("DELETE FROM device_groups WHERE id=?", (self.batb,))
        self.assertEqual(Agent(LINUX, "192.168.43.93").enroll(key=renewed).status_code, 403)

    def test_page_and_msi_carry_the_group_key(self):
        key = self.group_key(self.batb)
        page = self.admin.c.get(f"/enrollment?groupe={self.batb}").get_data(as_text=True)
        self.assertIn(f"KEY={key}", page)                               # déploiement en masse (GPO, Intune)
        self.assertIn(f'href="/agent/windows/enroll.msi?groupe={self.batb}"', page)
        self.assertIn(f'href="/agent/loco-client.deb?key={key}', page)
        self.assertIn("Changer la clé de ce groupe", page)
        self.assertIn(f'href="/enrollment?groupe={self.batb}"', self.admin.c.get("/fleet/groups").get_data(as_text=True))
        placeholder = b"@LOCO_PROVISION_KEY@" + b"." * 76
        blob = b"MSI\x00" + placeholder + b"end"
        with tempfile.TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "loco-client.msi")
            with open(msi, "wb") as f:
                f.write(blob)
            with mock.patch.object(fleet, "agent_msi", return_value=(msi, "sha")), \
                    mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
                stamped = self.admin.c.get(f"/agent/windows/enroll.msi?groupe={self.batb}").get_data()
                missing = self.admin.c.get("/agent/windows/enroll.msi?groupe=999999")
        self.assertEqual(len(stamped), len(blob))
        self.assertIn(key.encode(), stamped)
        self.assertEqual(missing.status_code, 404)

    def test_a_restricted_profile_enrolls_only_into_its_assigned_groups(self):
        _role, tech = self.technician(extra=("fleet.enroll",))
        with transaction() as conn:
            compta = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Comptabilité', '')").lastrowid
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (compta, self.ids["compta"]))
        page = tech.c.get("/enrollment").get_data(as_text=True)
        self.assertIn(f"key=g{self.batb}-", page)                       # son groupe, présélectionné
        self.assertNotIn(Agent.key(), page)                              # jamais la clé du serveur : hors de son parc
        self.assertNotIn('<option value="">', page)                      # pas de « Aucun groupe »
        self.assertNotIn(f'<option value="{self.servers}"', page)        # contient un de ses postes, sans lui être attribué
        self.assertNotIn(f'<option value="{compta}"', page)
        self.assertIn(f"key=g{self.batb}-", tech.c.get("/enrollment?groupe=").get_data(as_text=True))
        for gid in (self.servers, compta):
            self.assertEqual(tech.c.get(f"/enrollment?groupe={gid}").status_code, 404)
            self.assertEqual(tech.c.get(f"/agent/windows/enroll.msi?groupe={gid}").status_code, 404)
        self.assertEqual(tech.post(f"/fleet/groups/{self.batb}/enroll-key", page="/enrollment").status_code, 403)
        groups_page = tech.c.get("/fleet/groups").get_data(as_text=True)
        self.assertIn(f'href="/enrollment?groupe={self.batb}"', groups_page)
        self.assertNotIn(f'href="/enrollment?groupe={self.servers}"', groups_page)
        # Un poste inscrit avec sa clé de groupe apparaît aussitôt dans son parc.
        agent = Agent(dict(WINDOWS, hostname="pc-bat-b-09"), "192.168.43.79")
        agent.enroll(key=self.group_key(self.batb, tech))
        self.assertIn("pc-bat-b-09", tech.c.get("/fleet").get_data(as_text=True))

    def test_a_restricted_profile_without_groups_gets_no_key(self):
        role_id, _r = self.profile("Serveur web seul", {"fleet.view", "fleet.enroll"}, devices=["srv"])
        tech = self.account("tech", role_id)
        page = tech.c.get("/enrollment").get_data(as_text=True)
        self.assertIn("Votre profil ne couvre aucun groupe", page)
        self.assertNotIn(Agent.key(), page)
        self.assertNotIn("key=g", page)
        self.assertEqual(tech.c.get("/agent/windows/enroll.msi").status_code, 403)

if __name__ == "__main__":
    unittest.main()
