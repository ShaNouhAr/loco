"""Catalogue local Windows et groupes : points à traiter, actions de la fiche d'un logiciel, aperçu des groupes, droits."""
import unittest
from unittest import mock

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
        page = self.admin.c.get("/windows-apps/software/7-zip").get_data(as_text=True)
        self.assertIn("Déployer la mise à jour", page)
        self.assertIn("Supprimer du catalogue", page)
        self.assertIn('id="edit"', page)
        r = self.admin.post("/windows-apps/vlc/edit", {"name": "VLC media player", "version": "3.0.21", "kind": "exe",
                                                       "args": "/S", "retour": "fiche"}, page="/windows-apps/software/vlc")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps/software/vlc"))
        r = self.admin.post("/windows-apps/vlc/edit", {"name": "VLC media player", "kind": "exe", "args": "/S"},
                            page="/windows-apps")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps"))
        self.assertNotIn("Arguments manquants", self.admin.c.get("/windows-apps").get_data(as_text=True))
        r = self.admin.post("/windows-apps/vlc/delete", {}, page="/windows-apps/software/vlc")
        self.assertTrue(r.headers["Location"].endswith("/windows-apps"))
        self.assertEqual(self.admin.c.get("/windows-apps/software/vlc").status_code, 404)

    def test_readers_see_neither_management_nor_fleet_usage(self):
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        reader = test_permissions.TestPermissions.browser(
            self, "catalogue-lecture", test_permissions.TestPermissions.profile(self, "catalogue-lecture", {"windows.view"}))
        page = reader.c.get("/windows-apps").get_data(as_text=True)
        self.assertNotIn("Sur le parc", page)
        self.assertNotIn("Mises à jour à déployer", page)
        self.assertNotIn('data-open="#edit"', page)
        self.assertNotIn("Ajouter un logiciel", page)
        page = reader.c.get("/windows-apps/software/7-zip").get_data(as_text=True)
        self.assertNotIn("Supprimer du catalogue", page)
        self.assertNotIn('data-open="#edit"', page)
        self.assertIn("Le droit de consultation du parc est requis.", page)


    def group(self, kind, name, members, description=""):
        prefix = "/windows-apps" if kind == "software" else "/fleet"
        r = self.admin.post(prefix + "/groups/save", {"name": name, "description": description,
                                                              "members": [str(m) for m in members]}, page=prefix + "/groups")
        return int(r.headers["Location"].rsplit("#group-", 1)[1])

    def test_group_list_previews_members_and_their_state(self):
        with transaction() as conn:
            ids = {r[0]: r[1] for r in conn.execute("SELECT slug, id FROM win_apps")}
        self.group("software", "Socle", [ids["7-zip"], ids["vlc"]], "Tous les postes")
        self.group("software", "Outils IT", [ids["git"]])
        page = self.admin.c.get("/windows-apps/groups").get_data(as_text=True)
        self.assertIn("Tous les postes", page)
        self.assertIn("2 prêts", page)
        self.assertIn("1 en erreur", page)
        self.assertIn('title="VLC media player"', page)
        dev = device(Agent(WINDOWS, "192.168.43.50").uid)["id"]
        self.group("device", "Compta", [dev])
        page = self.admin.c.get("/fleet/groups").get_data(as_text=True)
        self.assertIn("1 à mettre à jour", page)                           # 7-Zip en retard sur le poste
        self.assertIn(WINDOWS["hostname"], page)

    def test_editor_lists_members_first_and_readers_only_see_members(self):
        with transaction() as conn:
            ids = {r[0]: r[1] for r in conn.execute("SELECT slug, id FROM win_apps")}
        gid = self.group("software", "Socle", [ids["vlc"]])
        page = self.admin.c.get(f"/windows-apps/groups/{gid}").get_data(as_text=True)
        self.assertLess(page.index("VLC media player"), page.index("7-Zip</a>"))     # membres d'abord
        self.assertIn("1</span> dans le groupe sur 3", page)
        self.assertIn("Supprimer le groupe", page)
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        reader = test_permissions.TestPermissions.browser(
            self, "groupes-lecture", test_permissions.TestPermissions.profile(self, "groupes-lecture", {"windows.view"}))
        page = reader.c.get(f"/windows-apps/groups/{gid}").get_data(as_text=True)
        self.assertIn("VLC media player", page)
        self.assertNotIn("7-Zip</a>", page)
        self.assertNotIn('name="members"', page)
        self.assertNotIn("Supprimer le groupe", page)
        self.assertNotIn("Enregistrer", page)


if __name__ == "__main__":
    unittest.main()


class TestInsecureSource(FleetTest):
    """Ignorer le certificat du site source : seulement avec une empreinte de référence, et l'intégrité reste vérifiée."""
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug,name,version,kind,args,filename,sha256,status,error,detect,"
                         "winget_id,expected_sha256,source_url,source_filename) VALUES "
                         "('gimp','GIMP','3',' exe','/S','','','error',?,'','GIMP.GIMP',?,?,?)",
                         ("Certificat HTTPS invalide sur le site source.", "a" * 64,
                          "https://src.invalid/gimp.exe", "gimp.exe"))
            conn.execute("INSERT INTO win_apps (slug,name,version,kind,args,filename,sha256,status,error,detect,"
                         "winget_id,expected_sha256,source_url,source_filename) VALUES "
                         "('tool','Tool','1','exe','/S','','','error','HTTP 404','','', '', ?, 'tool.exe')",
                         ("https://src.invalid/tool.exe",))

    def _flag(self, slug):
        with transaction() as conn:
            return conn.execute("SELECT allow_insecure FROM win_apps WHERE slug=?", (slug,)).fetchone()[0]

    def test_route_sets_flag_and_retries_only_with_a_reference_hash(self):
        import loco.winapps as wa
        calls = []
        with mock.patch.object(wa, "start_fetch", lambda row: calls.append(row["slug"])):
            self.admin.post("/windows-apps/gimp/trust-source", {"allow": "1", "retour": "fiche"},
                            page="/windows-apps/software/gimp")
            self.admin.post("/windows-apps/tool/trust-source", {"allow": "1", "retour": "fiche"},
                            page="/windows-apps/software/tool")
        self.assertEqual(self._flag("gimp"), 1)                        # empreinte présente : autorisé + re-téléchargement
        self.assertEqual(calls, ["gimp"])
        self.assertEqual(self._flag("tool"), 0)                        # pas d'empreinte : refusé

    def test_restore_verification_clears_the_flag(self):
        import loco.winapps as wa
        with transaction() as conn:
            conn.execute("UPDATE win_apps SET allow_insecure=1 WHERE slug='gimp'")
        with mock.patch.object(wa, "start_fetch", lambda row: None):
            self.admin.post("/windows-apps/gimp/trust-source", {"allow": "0", "retour": "fiche"},
                            page="/windows-apps/software/gimp")
        self.assertEqual(self._flag("gimp"), 0)

    def test_start_fetch_asks_for_insecure_only_when_a_hash_is_present(self):
        import loco.winapps as wa
        from loco import app
        from loco.db import db
        captured = {}

        class _Thread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                captured["insecure"] = (kwargs or {}).get("insecure")
            def start(self):
                pass
        with transaction() as conn:
            conn.execute("UPDATE win_apps SET allow_insecure=1 WHERE slug IN ('gimp','tool')")
        with app.app_context(), mock.patch.object(wa.threading, "Thread", _Thread):
            wa.start_fetch(db().execute("SELECT * FROM win_apps WHERE slug='gimp'").fetchone())
            self.assertTrue(captured["insecure"])                      # empreinte présente -> capture sans vérif cert
            wa.start_fetch(db().execute("SELECT * FROM win_apps WHERE slug='tool'").fetchone())
            self.assertFalse(captured["insecure"])                     # sans empreinte -> on garde la vérification

    def test_download_skips_cert_check_only_when_insecure(self):
        import loco.util as util
        import ssl, tempfile
        seen = {}

        class _Resp:
            headers = {}
            def geturl(self): return "https://src.invalid/f.exe"
            def read(self, n=-1): return b""
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=0, context=None):
            seen["context"] = context
            return _Resp()
        with mock.patch.object(util.urllib.request, "urlopen", fake_urlopen), tempfile.TemporaryDirectory() as d:
            util.download("https://src.invalid/f.exe", d, insecure=True)
            self.assertEqual(seen["context"].verify_mode, ssl.CERT_NONE)
            self.assertFalse(seen["context"].check_hostname)
            util.download("https://src.invalid/f.exe", d, insecure=False)
            self.assertIsNone(seen["context"])                         # vérification par défaut
