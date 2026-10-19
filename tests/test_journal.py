"""Journal : libellés, filtres et pages, export JSON (période, filtres ou tout), rotation configurable, droits."""
import datetime
import json
import unittest

import test_permissions
from test_fleet import FleetTest
from loco import auth, db, journal


def add_event(at, action, detail="", username="essai"):
    with db.transaction() as conn:
        conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                     (at, username, "192.168.43.50", action, detail))


def days_ago(n):
    return (datetime.datetime.now() - datetime.timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


class TestJournal(FleetTest):
    # Utilitaires des tests de permissions (comptes et profils jetables). Aucune référence à leur classe au
    # niveau du module : unittest y découvrirait et rejouerait leurs tests.
    profile = test_permissions.TestPermissions.profile
    browser = test_permissions.TestPermissions.browser
    cleanup_accounts = test_permissions.TestPermissions.cleanup_accounts
    post = staticmethod(test_permissions.TestPermissions.post)

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_accounts)
        with db.transaction() as conn:
            conn.execute("DELETE FROM audit")
            conn.execute("DELETE FROM settings WHERE key='audit_retention_days'")

    def test_export_whole_journal_or_a_period_as_json(self):
        add_event("2026-09-01 08:00:00", "essai.ancien")
        add_event("2026-10-10 12:00:00", "essai.periode", "accentué « é »")
        add_event("2026-10-15 23:59:59", "essai.fin")
        r = self.admin.c.get("/journal/export")
        self.assertEqual(r.mimetype, "application/json")
        self.assertIn("attachment;", r.headers["Content-Disposition"])
        doc = json.loads(r.get_data(as_text=True))
        actions = [e["action"] for e in doc["events"]]
        self.assertEqual(actions[:3], ["essai.ancien", "essai.periode", "essai.fin"])
        self.assertEqual(doc["count"], len(doc["events"]))
        self.assertEqual(doc["exported_by"], "admin")
        doc = json.loads(self.admin.c.get("/journal/export?debut=2026-10-01&fin=2026-10-15").get_data(as_text=True))
        self.assertEqual([e["action"] for e in doc["events"]], ["essai.periode", "essai.fin"])   # bornes incluses
        self.assertEqual(doc["events"][0]["detail"], "accentué « é »")
        self.assertEqual(doc["period"], {"from": "2026-10-01 00:00:00", "to": "2026-10-15 23:59:59"})
        # L'export est lui-même tracé.
        with db.transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit WHERE action='journal.export'").fetchone()[0], 2)

    def test_invalid_periods_are_refused(self):
        for query in ("debut=18/10/2026", "fin=2026-13-01", "debut=2026-10-15&fin=2026-10-01"):
            with self.subTest(query=query):
                self.assertEqual(self.admin.c.get(f"/journal/export?{query}").status_code, 400)

    def test_rotation_defaults_to_30_days_and_is_configurable(self):
        add_event(days_ago(45), "essai.45j")
        add_event(days_ago(20), "essai.20j")
        add_event(days_ago(5), "essai.5j")
        with db.transaction() as conn:
            self.assertEqual(auth.audit_retention(conn), 30)
            self.assertEqual(auth.purge_audit(conn), 1)
            remaining = {r[0] for r in conn.execute("SELECT action FROM audit")}
        self.assertEqual(remaining, {"essai.20j", "essai.5j"})
        self.admin.post("/journal/conservation", {"days": "7"}, page="/journal")
        with db.transaction() as conn:
            remaining = {r[0] for r in conn.execute("SELECT action FROM audit")}
        self.assertIn("essai.5j", remaining)
        self.assertNotIn("essai.20j", remaining)
        self.assertIn("journal.conservation", remaining)
        self.admin.post("/journal/conservation", {"days": "0"}, page="/journal")
        add_event(days_ago(4000), "essai.tres-ancien")
        with db.transaction() as conn:
            self.assertEqual(auth.purge_audit(conn), 0)                     # sans limite
        self.assertEqual(self.admin.post("/journal/conservation", {"days": "12"}, page="/journal").status_code, 400)

    def test_export_and_retention_follow_permissions(self):
        auditor = self.browser("journal-audit", "auditor")
        self.assertEqual(auditor.c.get("/journal/export").status_code, 200)
        self.assertEqual(self.post(auditor, "/journal/conservation", {"days": "7"}).status_code, 403)
        page = auditor.c.get("/journal").get_data(as_text=True)
        self.assertIn("Exporter en JSON", page)
        self.assertNotIn("Conservation", page)
        reader = self.browser("journal-lecture", self.profile("journal-lecture", {"audit.view"}))
        self.assertEqual(reader.c.get("/journal/export").status_code, 403)
        self.assertNotIn("Exporter en JSON", reader.c.get("/journal").get_data(as_text=True))


    def test_actions_get_readable_labels_and_one_category(self):
        self.assertEqual(journal.describe("parc.adresse-annoncee")["label"], "Adresse du serveur annoncée aux agents")
        self.assertEqual(journal.describe("essai.nouvelle-action")["label"], "Essai · nouvelle action")
        self.assertTrue(journal.describe("connexion.echec")["failure"])
        self.assertTrue(journal.describe("sso.echec")["failure"])
        for action, key in (("sso.connexion", "connexions"), ("sso.demande", "comptes"), ("parc.session.fin", "distant"),
                            ("parc.update", "parc"), ("groupes.machines", "parc"), ("groupes.logiciels", "logiciels"),
                            ("windows_update.domaines", "wu"), ("windows.ajout", "logiciels"), ("essai.x", "")):
            self.assertEqual(journal.category(action), key, action)
        self.assertEqual([a for a in journal.LABELS if not journal.category(a)], [])
        # Le filtre SQL range chaque action dans la même catégorie que l'affichage.
        for action in journal.LABELS:
            add_event(days_ago(0), action)
        with db.transaction() as conn:
            for key in journal.CATEGORIES:
                sql, values = journal.where({"categorie": key})
                found = {r[0] for r in conn.execute("SELECT action FROM audit" + sql, values)}
                self.assertEqual(found, {a for a in journal.LABELS if journal.category(a) == key}, key)

    def test_page_filters_group_by_day_and_paginate(self):
        add_event(days_ago(40), "connexion.echec", "mot de passe erroné", username="alice")
        add_event(days_ago(3), "parc.update", "poste-07", username="bob")
        for i in range(journal.PAGE + 5):
            add_event(days_ago(0), "connexion", f"ouverture {i}", username="alice")
        page = self.admin.c.get("/journal").get_data(as_text=True)
        self.assertIn("Aujourd&#39;hui", page)
        self.assertIn("Plus anciens", page)
        self.assertNotIn("poste-07", page)                                   # page suivante
        self.assertNotIn("ces dernières 24 heures", page)                    # échec vieux de 40 jours
        with db.transaction() as conn:
            last = conn.execute("SELECT id FROM audit ORDER BY id DESC LIMIT 1 OFFSET ?", (journal.PAGE - 1,)).fetchone()[0]
        page = self.admin.c.get(f"/journal?avant={last}").get_data(as_text=True)
        self.assertIn("poste-07", page)
        self.assertIn("Plus récents", page)
        self.assertNotIn("Plus anciens", page)
        page = self.admin.c.get("/journal?compte=bob").get_data(as_text=True)
        self.assertIn("Mise à jour demandée", page)
        self.assertNotIn("ouverture 1", page)
        page = self.admin.c.get("/journal?periode=7j&echecs=1").get_data(as_text=True)
        self.assertNotIn("mot de passe erroné", page)                        # hors période
        self.assertIn("mot de passe erroné", self.admin.c.get("/journal?echecs=1").get_data(as_text=True))
        page = self.admin.c.get("/journal?q=poste-0").get_data(as_text=True)
        self.assertIn("poste-07", page)
        self.assertNotIn("ouverture 1", page)
        self.assertIn("poste-07", self.admin.c.get("/journal?categorie=parc").get_data(as_text=True))
        self.assertNotIn("poste-07", self.admin.c.get("/journal?categorie=connexions").get_data(as_text=True))
        r = self.admin.c.get("/journal?debut=2026-10-15&fin=2026-10-01")
        self.assertEqual(r.status_code, 302)

    def test_export_follows_the_page_filters(self):
        recent = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        add_event(recent, "connexion.echec", "mot de passe erroné")       # loin de la limite des 24 heures
        self.assertIn("1 échec de connexion ces dernières 24 heures", self.admin.c.get("/journal").get_data(as_text=True))
        add_event(days_ago(1), "parc.update", "poste-07")
        doc = json.loads(self.admin.c.get("/journal/export?categorie=connexions").get_data(as_text=True))
        self.assertEqual([e["action"] for e in doc["events"]], ["connexion.echec"])
        self.assertEqual(doc["events"][0]["label"], "Échec de connexion")
        self.assertEqual(doc["filters"], {"categorie": "connexions"})
        doc = json.loads(self.admin.c.get("/journal/export?periode=7j&q=poste").get_data(as_text=True))
        self.assertEqual([e["action"] for e in doc["events"]], ["parc.update"])
        self.assertIsNotNone(doc["period"]["from"])
        with db.transaction() as conn:
            detail = conn.execute("SELECT detail FROM audit WHERE action='journal.export' ORDER BY id DESC").fetchone()[0]
        self.assertIn("q=poste", detail)


if __name__ == "__main__":
    unittest.main()
