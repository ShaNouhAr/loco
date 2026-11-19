"""Opérations de fichiers de l'agent Linux (loco-client.py) : surtout l'espace disque (op_usage), qui additionne la
taille RÉCURSIVE de chaque élément d'un dossier, du plus gros au plus petit, sans suivre les liens ni dépasser un budget."""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loco_client_files", ROOT / "portal/agents/loco-client.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class TestUsage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="loco-usage-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        # big/ = 2000 + 1000 (sous-dossier) = 3000 o ; fichier.txt = 500 o ; small/ = 10 o
        os.makedirs(os.path.join(self.dir, "big", "sub"))
        os.makedirs(os.path.join(self.dir, "small"))
        self._write("big/a.bin", 2000)
        self._write("big/sub/b.bin", 1000)
        self._write("small/c.bin", 10)
        self._write("fichier.txt", 500)

    def _write(self, rel, size):
        with open(os.path.join(self.dir, rel), "wb") as f:
            f.write(b"x" * size)

    def test_recursive_sizes_sorted_largest_first(self):
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")
        self.assertEqual([(e["name"], e["type"], e["size"]) for e in head["entries"]],
                         [("big", "dir", 3000), ("fichier.txt", "file", 500), ("small", "dir", 10)])
        self.assertEqual(head["total"], 3510)
        self.assertFalse(head["partial"])
        self.assertEqual(head["parent"], os.path.dirname(self.dir))

    def test_a_symlinked_folder_is_not_followed(self):
        os.symlink(self.dir, os.path.join(self.dir, "loop"))   # lien : taille 0, et surtout pas de boucle infinie
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")
        link = next(e for e in head["entries"] if e["name"] == "loop")
        self.assertEqual((link["type"], link["size"]), ("dir", 0))
        self.assertEqual(head["total"], 3510)                  # le lien n'ajoute rien (pas de double comptage)

    def test_the_budget_bounds_the_scan(self):
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")   # budget large : rien de partiel
        self.assertFalse(head["partial"])
        size, partial = agent.FileSession()._tree_size(self.dir, 0)       # échéance dépassée d'emblée
        self.assertTrue(partial)

    def test_unknown_op_is_refused(self):
        with self.assertRaises(ValueError):
            agent.FileSession().handle({"op": "nope", "path": self.dir}, b"")


if __name__ == "__main__":
    unittest.main()
