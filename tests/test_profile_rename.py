"""Profils intégrés : un nouveau nom s'applique aux installations existantes, sans écraser un profil personnalisé."""
import unittest

import test_portal  # noqa: F401  (environnement du portail de test)
from loco import permissions
from loco.db import transaction


class TestBuiltinProfileRename(unittest.TestCase):
    def tearDown(self):
        with transaction() as conn:
            conn.execute("DELETE FROM access_roles WHERE id='custom_conflict'")
            permissions.seed_roles(conn)

    def test_old_builtin_name_is_renamed_on_startup(self):
        with transaction() as conn:
            conn.execute("UPDATE access_roles SET label='Technicien du parc', label_key=? WHERE id='operator'",
                         (permissions.role_key("Technicien du parc"),))
            permissions.seed_roles(conn)
            self.assertEqual(conn.execute("SELECT label FROM access_roles WHERE id='operator'").fetchone()[0], "Opérateur")

    def test_custom_profile_with_the_new_name_is_left_alone(self):
        with transaction() as conn:
            conn.execute("UPDATE access_roles SET label='Technicien du parc', label_key=? WHERE id='operator'",
                         (permissions.role_key("Technicien du parc"),))
            conn.execute("INSERT INTO access_roles (id,label,label_key,description,permissions,builtin) VALUES "
                         "('custom_conflict','Opérateur',?,'','[]',0)", (permissions.role_key("Opérateur"),))
            permissions.seed_roles(conn)                                    # aucun conflit d'unicité
            self.assertEqual(conn.execute("SELECT label FROM access_roles WHERE id='operator'").fetchone()[0],
                             "Technicien du parc")


if __name__ == "__main__":
    unittest.main()
