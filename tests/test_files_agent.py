"""Gestionnaire de fichiers de l'agent Linux : opérations exécutées sur de vrais fichiers dans un dossier jetable,
et découpage des trames [longueur en-tête][longueur données] partagées avec le navigateur.

On charge loco-client.py comme un module (comme test_linux_agent) ; rien n'est installé ni mis sur le réseau."""

import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "portal" / "agents" / "loco-client.py"
spec = importlib.util.spec_from_file_location("loco_client", str(SOURCE))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.LANG_EN = False  # messages en français (langue de référence) pour des assertions stables


class TestFileSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, ignore_errors=True)
        self.fs = agent.FileSession()
        self.addCleanup(self.fs.cleanup)

    def op(self, op, data=b"", **header):
        header.update(op=op, id=header.get("id", 1))
        return self.fs.handle(header, data)

    def test_hello_describes_the_host(self):
        reply, data = self.op("hello")
        self.assertEqual(reply["os"], "linux")
        self.assertEqual(reply["sep"], "/")
        self.assertTrue(reply["home"])
        self.assertTrue(any(r["path"] == "/" for r in reply["roots"]))
        self.assertEqual(data, b"")

    def test_write_in_chunks_then_read_back_and_temp_is_gone(self):
        path = os.path.join(self.dir, "note.txt")
        self.op("write", b"Hello ", path=path, offset=0)
        self.assertTrue(os.path.exists(path + ".loco-part"))      # écriture dans un fichier temporaire
        self.assertFalse(os.path.exists(path))
        reply, _ = self.op("write", b"world!", path=path, offset=6, final=True)
        self.assertEqual(reply["final"], True)
        self.assertFalse(os.path.exists(path + ".loco-part"))     # renommé atomiquement à la fin
        head, chunk = self.op("read", path=path, offset=0, length=1024)
        self.assertEqual(chunk, b"Hello world!")
        self.assertEqual((head["size"], head["eof"]), (12, True))
        head, chunk = self.op("read", path=path, offset=6, length=3)
        self.assertEqual((chunk, head["eof"]), (b"wor", False))

    def test_list_sorts_folders_first_and_reports_types(self):
        os.mkdir(os.path.join(self.dir, "zeta"))
        with open(os.path.join(self.dir, "alpha.txt"), "w") as f:
            f.write("x" * 5)
        os.symlink(os.path.join(self.dir, "alpha.txt"), os.path.join(self.dir, "link"))
        reply, _ = self.op("list", path=self.dir)
        names = [(e["name"], e["type"], e["link"]) for e in reply["entries"]]
        self.assertEqual(names[0], ("zeta", "dir", False))        # dossiers avant fichiers
        by_name = {e["name"]: e for e in reply["entries"]}
        self.assertEqual(by_name["alpha.txt"]["size"], 5)
        self.assertTrue(by_name["link"]["link"])
        self.assertEqual(reply["parent"], os.path.dirname(self.dir))

    def test_rename_move_copy_and_collisions(self):
        a = os.path.join(self.dir, "a")
        b = os.path.join(self.dir, "b")
        os.mkdir(a)
        os.mkdir(b)
        with open(os.path.join(a, "f.txt"), "w") as f:
            f.write("data")
        self.op("copy", path=a, to=b)                              # copie récursive du dossier
        self.assertTrue(os.path.exists(os.path.join(b, "a", "f.txt")))
        reply, _ = self.op("rename", path=os.path.join(a, "f.txt"), name="g.txt")
        self.assertEqual(reply["path"], os.path.join(a, "g.txt"))
        self.op("move", path=os.path.join(a, "g.txt"), to=b)
        self.assertTrue(os.path.exists(os.path.join(b, "g.txt")))
        self.assertFalse(os.path.exists(os.path.join(a, "g.txt")))
        with self.assertRaises(ValueError):                        # écraser un élément existant est refusé
            self.op("move", path=os.path.join(b, "a"), to=b)

    def test_delete_removes_trees_and_chmod_sets_mode(self):
        tree = os.path.join(self.dir, "tree")
        os.makedirs(os.path.join(tree, "inner"))
        with open(os.path.join(tree, "inner", "x"), "w") as f:
            f.write("x")
        self.op("delete", path=tree)
        self.assertFalse(os.path.exists(tree))
        target = os.path.join(self.dir, "script.sh")
        with open(target, "w") as f:
            f.write("#!/bin/sh\n")
        self.op("chmod", path=target, mode=0o750)
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o750)

    def test_mkdir_and_stat(self):
        made = os.path.join(self.dir, "new")
        self.op("mkdir", path=made)
        self.assertTrue(os.path.isdir(made))
        reply, _ = self.op("stat", path=made)
        self.assertTrue(reply["exists"])
        self.assertEqual(reply["type"], "dir")
        missing, _ = self.op("stat", path=os.path.join(self.dir, "ghost"))
        self.assertFalse(missing["exists"])

    def test_cancel_and_cleanup_remove_partial_uploads(self):
        p = os.path.join(self.dir, "big")
        self.op("write", b"partial", path=p, offset=0)
        self.op("write_cancel", path=p)
        self.assertFalse(os.path.exists(p + ".loco-part"))
        q = os.path.join(self.dir, "big2")
        self.op("write", b"partial", path=q, offset=0)
        self.fs.cleanup()
        self.assertFalse(os.path.exists(q + ".loco-part"))

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.op("rm -rf")                                      # ni un nom d'identifiant valide ni une méthode op_*
        with self.assertRaises(ValueError):
            self.op("cleanup")                                     # pas d'accès aux méthodes hors op_*


class TestFrames(unittest.TestCase):
    def test_frames_split_the_byte_stream_by_their_lengths(self):
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        agent._write_frame(write, {"op": "read", "id": 9}, b"\x00\xff binaire")
        agent._write_frame(write, {"op": "list", "id": 10}, b"")
        os.close(write)
        header, data = agent._read_frame(read)
        self.assertEqual((header["op"], header["id"], data), ("read", 9, b"\x00\xff binaire"))
        header, data = agent._read_frame(read)
        self.assertEqual((header["op"], data), ("list", b""))
        self.assertIsNone(agent._read_frame(read))                 # fin de flux propre

    def test_oversized_frame_is_refused(self):
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        os.write(write, struct.pack(">II", agent.FILE_HEADER_MAX + 1, 0))
        os.close(write)
        with self.assertRaises(ValueError):
            agent._read_frame(read)


if __name__ == "__main__":
    unittest.main()
