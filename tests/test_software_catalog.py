"""Catalogue local Windows et groupes : points à traiter, actions de la fiche d'un logiciel, aperçu des groupes, droits."""
import unittest

import test_permissions
from test_fleet import FleetTest, Agent, WINDOWS
from loco.db import transaction
from test_fleet import device


def add(slug, name, version, kind="msi", args="/qn", status="ready", error="", winget=""):
    with transaction() as conn:
        conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, error, detect, "
                     "winget_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (slug, name, version, kind, args, f"{slug}.{kind}", "c" * 64, status, error, "", winget))


class TestSoftwareCatalog(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM software_groups")
            conn.execute("DELETE FROM device_groups")
        add("7-zip", "7-Zip", "26.04", winget="7zip.7zip")
        add("vlc", "VLC media player", "3.0.21", kind="exe", args="")
        add("git", "Git", "2.51.0", kind="exe", args="/VERYSILENT", status="error", error="HTTP 404 (lien expiré)")
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [], "apps": [{"name": "7-Zip 24.09 (x64 edition)", "version": "24.09.00.0"}]})

    def test_list_counts_and_filters_what_needs_attention(self):
        page = self.admin.c.get("/windows-apps").get_data(as_text=True)
        self.assertIn("Mises à jour à déployer <b>1</b>", page)
        self.assertIn("Arguments manquants <b>1</b>", page)
        self.assertIn("Erreurs <b>1</b>", page)
        self.assertNotIn("En téléchargement", page)                       # aucun : pastille masquée
        self.assertIn("1 à mettre à jour", page)
        self.assertIn("HTTP 404 (lien expiré)", page)
        page = self.admin.c.get("/windows-apps?etat=parc").get_data(as_text=True)
        self.assertIn('data-slug="7-zip"', page)
        self.assertNotIn('data-slug="vlc"', page)
        page = self.admin.c.get("/windows-apps?etat=arguments").get_data(as_text=True)
        self.assertEqual(page.count("data-slug="), 1)
        self.assertIn('data-slug="vlc"', page)
        self.assertEqual(self.admin.c.get("/windows-apps?etat=inconnu").status_code, 200)   # filtre ignoré

    def test_detail_page_manages_the_software_and_returns_to_it(self):
        page = self.admin.c.get("/windows-apps/logiciel/7-zip").get_data(as_text=True)
        self.assertIn("Déployer la mise à jour", page)
        self.assertIn("Supprimer du catalogue", page)
        self.assertIn('id="edit"', page)
        r = self.admin.post("/windows-apps/vlc/edit", {"name": "VLC media player", "version": "3.0.21", "kind": "exe",
                                                       "args": "/S", "retour": "fiche"}, page="/windows-apps/logiciel/vlc")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps/logiciel/vlc"))
        r = self.admin.post("/windows-apps/vlc/edit", {"name": "VLC media player", "kind": "exe", "args": "/S"},
                            page="/windows-apps")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps"))
        self.assertNotIn("Arguments manquants", self.admin.c.get("/windows-apps").get_data(as_text=True))
        r = self.admin.post("/windows-apps/vlc/delete", {}, page="/windows-apps/logiciel/vlc")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps"))
        self.assertEqual(self.admin.c.get("/windows-apps/logiciel/vlc").status_code, 404)

    def test_readers_see_neither_management_nor_fleet_usage(self):
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        reader = test_permissions.TestPermissions.browser(
            self, "catalogue-lecture", test_permissions.TestPermissions.profile(self, "catalogue-lecture", {"windows.view"}))
        page = reader.c.get("/windows-apps").get_data(as_text=True)
        self.assertNotIn("Sur le parc", page)
        self.assertNotIn("Mises à jour à déployer", page)
        self.assertNotIn('data-open="#edit"', page)
        self.assertNotIn("Ajouter un logiciel", page)
        page = reader.c.get("/windows-apps/logiciel/7-zip").get_data(as_text=True)
        self.assertNotIn("Supprimer du catalogue", page)
        self.assertNotIn('data-open="#edit"', page)
        self.assertIn("Le droit de consultation du parc est requis.", page)


    def group(self, kind, name, members, description=""):
        prefix = "/windows-apps" if kind == "software" else "/parc"
        r = self.admin.post(prefix + "/groupes/enregistrer", {"name": name, "description": description,
                                                              "members": [str(m) for m in members]}, page=prefix + "/groupes")
        return int(r.headers["Location"].rstrip("/").split("/")[-1])

    def test_group_list_previews_members_and_their_state(self):
        with transaction() as conn:
            ids = {r[0]: r[1] for r in conn.execute("SELECT slug, id FROM win_apps")}
        self.group("software", "Socle", [ids["7-zip"], ids["vlc"]], "Tous les postes")
        self.group("software", "Outils IT", [ids["git"]])
        page = self.admin.c.get("/windows-apps/groupes").get_data(as_text=True)
        self.assertIn("Tous les postes", page)
        self.assertIn("2 prêts", page)
        self.assertIn("1 en erreur", page)
        self.assertIn('title="VLC media player"', page)
        dev = device(Agent(WINDOWS, "192.168.43.50").uid)["id"]
        self.group("device", "Compta", [dev])
        page = self.admin.c.get("/parc/groupes").get_data(as_text=True)
        self.assertIn("1 à mettre à jour", page)                           # 7-Zip en retard sur le poste
        self.assertIn(WINDOWS["hostname"], page)

    def test_editor_lists_members_first_and_readers_only_see_members(self):
        with transaction() as conn:
            ids = {r[0]: r[1] for r in conn.execute("SELECT slug, id FROM win_apps")}
        gid = self.group("software", "Socle", [ids["vlc"]])
        page = self.admin.c.get(f"/windows-apps/groupes/{gid}").get_data(as_text=True)
        self.assertLess(page.index("VLC media player"), page.index("7-Zip</a>"))     # membres d'abord
        self.assertIn("1</span> dans le groupe sur 3", page)
        self.assertIn("Supprimer le groupe", page)
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        reader = test_permissions.TestPermissions.browser(
            self, "groupes-lecture", test_permissions.TestPermissions.profile(self, "groupes-lecture", {"windows.view"}))
        page = reader.c.get(f"/windows-apps/groupes/{gid}").get_data(as_text=True)
        self.assertIn("VLC media player", page)
        self.assertNotIn("7-Zip</a>", page)
        self.assertNotIn('name="members"', page)
        self.assertNotIn("Supprimer le groupe", page)
        self.assertNotIn("Enregistrer", page)


if __name__ == "__main__":
    unittest.main()
