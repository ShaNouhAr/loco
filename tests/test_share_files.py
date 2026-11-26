"""Partages ouverts dans le navigateur : le service des fichiers (jamais hors du dossier d'un partage, aucun lien suivi,
noms lisibles par Windows, taille limite tenue, partage plein qui ne peut que diminuer), son socket (le fichier ouvert
passé au portail), son lancement par l'agent root sous loco-share ; puis les routes du portail — droits (gestionnaire,
utilisateur Samba lié en lecture ou en écriture), téléversement par fragments, téléchargement par morceaux, aperçus sans
risque, archive ZIP, tâches et journal.

Le service tourne pour de vrai sur des dossiers temporaires, derrière un vrai socket Unix (sans root : il n'entend que
le compte qui lance les essais)."""
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

import test_portal as fixture  # en premier : loco chargé sur des dossiers temporaires
import locosamba
from loco import permissions
from loco.db import transaction
from test_samba import PortalTest

SECRET = "Partage-Secret-2026"
MIB = 1 << 20


def publish(status, folders, **over):
    """État publié par l'agent : un partage par dossier (taille limite 10 Mio, rien d'occupé)."""
    shares = {name: dict({"path": path, "mount": "/", "limit": 10 * MIB, "used": 0, "measured": time.time(), "full": False,
                          "error": ""}, **over.get(name, {})) for name, path in folders.items()}
    data = {}
    if os.path.exists(status):
        with open(status, encoding="utf-8") as f:
            data = json.load(f)
    data.update(shares=shares, at=time.time())
    with open(status, "w", encoding="utf-8") as f:
        json.dump(data, f)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="loco-files-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.root, self.photos = os.path.join(self.work, "documents"), os.path.join(self.work, "photos")
        os.mkdir(self.root)
        os.mkdir(self.photos)
        self.outside = os.path.join(self.work, "secret")
        os.mkdir(self.outside)
        with open(os.path.join(self.outside, "cle.txt"), "w") as f:
            f.write("secret")
        self.status = os.path.join(self.work, "samba.json")
        self.publish()
        self.files = locosamba.ShareFiles(self.status)

    def publish(self, **over):
        publish(self.status, {"Documents": self.root, "Photos": self.photos}, **over)

    def call(self, op, **req):
        req.setdefault("share", "Documents")
        reply, fds = self.files.handle(dict(req, op=op))
        for fd in fds:
            self.addCleanup(lambda fd=fd: _close(fd))
        return reply, fds

    def ok(self, op, **req):
        reply, fds = self.call(op, **req)
        self.assertTrue(reply["ok"], reply)
        return reply, fds

    def refused(self, op, code, **req):
        reply, _fds = self.call(op, **req)
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"], code, reply)
        return reply

    def put(self, rel, data=b"x", root=None):
        path = os.path.join(root or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def wait(self, reply):
        for _i in range(200):
            view, _fds = self.call("job", job=reply["job"])
            if view["state"] != "running":
                return view
            time.sleep(0.02)
        self.fail("tâche jamais finie")

    def upload(self, name, data, folder="", **done):
        reply, fds = self.ok("upload", dir=folder, name=name, size=len(data))
        os.write(fds[0], data)
        return self.call("upload_done", dir=folder, name=name, token=reply["token"], size=len(data), **done)[0]


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        pass


class TestReading(ServiceTest):
    def test_folders_first_and_the_upload_folder_hidden(self):
        self.put("b.txt")
        self.put("A/c.txt")
        os.mkdir(os.path.join(self.root, locosamba.STAGING))
        reply, _fds = self.ok("list", path="")
        self.assertEqual([(e["name"], e["type"]) for e in reply["entries"]], [("A", "dir"), ("b.txt", "file")])
        self.assertEqual(self.ok("list", path="A")[0]["entries"][0]["size"], 1)
        self.refused("list", "not-found", path=locosamba.STAGING)

    def test_paths_never_leave_the_share(self):
        for path in ("..", "A/../..", "./x", "a\x00b"):
            self.refused("list", "bad-path", path=path)
        # Un chemin « absolu » reste relatif au partage : /etc n'est que le dossier « etc » du partage.
        self.refused("list", "not-found", path="/etc")
        self.refused("list", "no-share", share="Inconnu", path="")
        self.publish(Documents={"error": "La partition %(mount)s n'est plus montée."})
        self.refused("list", "no-share", path="")

    def test_symbolic_links_are_shown_never_followed(self):
        os.symlink(self.outside, os.path.join(self.root, "evasion"))
        os.symlink(os.path.join(self.outside, "cle.txt"), os.path.join(self.root, "cle.txt"))
        entries = {e["name"]: e for e in self.ok("list", path="")[0]["entries"]}
        self.assertTrue(entries["evasion"]["link"] and entries["cle.txt"]["link"])
        self.assertEqual(entries["evasion"]["type"], "other")
        self.assertIn(self.call("list", path="evasion")[0]["error"], ("link", "not-dir"))
        self.assertIn(self.call("list", path="evasion/")[0]["error"], ("link", "not-dir"))
        self.assertIn(self.call("open", path="cle.txt")[0]["error"], ("link", "not-found"))
        self.assertEqual(self.ok("search", path="", q="cle")[0]["entries"][0]["name"], "cle.txt")    # le lien, pas sa cible
        job = self.ok("delete", paths=["evasion", "cle.txt"])[0]
        self.assertEqual(self.wait(job)["errors"], [])
        self.assertTrue(os.path.exists(os.path.join(self.outside, "cle.txt")))
        self.assertEqual(os.listdir(self.root), [])

    def test_open_passes_the_file_itself(self):
        self.put("notes.txt", b"bonjour")
        reply, fds = self.ok("open", path="notes.txt")
        self.assertEqual((reply["name"], reply["size"]), ("notes.txt", 7))
        self.assertEqual(os.read(fds[0], 100), b"bonjour")
        self.refused("open", "is-dir", path="")
        os.mkfifo(os.path.join(self.root, "tube"))
        self.assertFalse(self.call("open", path="tube")[0]["ok"])            # jamais bloqué sur un tube nommé

    def test_search_ignores_case_and_accents(self):
        self.put("Photos/2026/Été à Lyon.JPG")
        self.put("Photos/hiver.jpg")
        found = self.ok("search", path="", q="ete a lyon")[0]["entries"]
        self.assertEqual([(e["dir"], e["name"]) for e in found], [("Photos/2026", "Été à Lyon.JPG")])
        self.refused("search", "bad-query", path="", q="  ")
        with mock.patch.object(locosamba, "FILES_FOUND", 1):
            self.assertTrue(self.ok("search", path="", q="j")[0]["partial"])

    def test_disk_usage_and_folder_size(self):
        self.put("gros/a.bin", b"x" * 3000)
        self.put("gros/b/c.bin", b"x" * 1000)
        self.put("petit.txt", b"x" * 10)
        usage = self.ok("usage", path="")[0]
        self.assertEqual([(e["name"], e["size"]) for e in usage["entries"]], [("gros", 4000), ("petit.txt", 10)])
        self.assertEqual(usage["total"], 4010)
        size = self.ok("size", path="gros")[0]
        self.assertEqual((size["size"], size["files"], size["dirs"]), (4000, 2, 1))

    def test_requests_never_bring_the_service_down(self):
        self.assertEqual(self.files.handle(None)[0]["error"], "bad-op")
        self.assertEqual(self.files.handle({"op": "../x"})[0]["error"], "bad-op")
        self.refused("list", "bad-path", path=12)
        self.refused("delete", "bad-path", paths="x")
        self.refused("upload_done", "bad-path", dir="", name="a.txt", token="pas-un-jeton")
        self.assertFalse(self.call("upload", dir="", name="a.txt", size="beaucoup")[0]["ok"])


class TestChanges(ServiceTest):
    def test_names_windows_can_open(self):
        for name in ("a:b", "que?", "con", "CON.txt", "fin.", "fin ", "tab\there"):
            self.refused("mkdir", "bad-name", path=name)
        self.refused("mkdir", "bad-path", path="")
        self.refused("mkdir", "not-found", path=locosamba.STAGING)          # le dossier des envois n'existe pas ici
        self.ok("mkdir", path="Été 2026")
        self.refused("mkdir", "exists", path="Été 2026")
        self.refused("mkdir", "not-found", path="absent/enfant")
        self.ok("mkdir", path="a/b/c", parents=True)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "a/b/c")))
        self.ok("newfile", path="a/vide.txt")
        self.refused("newfile", "exists", path="a/vide.txt")

    def test_rename_never_overwrites(self):
        self.put("a.txt", b"a")
        self.put("b.txt", b"b")
        self.refused("rename", "exists", path="a.txt", name="b.txt")
        self.ok("rename", path="a.txt", name="A.txt")                        # changer la casse seulement
        self.assertEqual(sorted(os.listdir(self.root)), ["A.txt", "b.txt"])
        self.refused("rename", "bad-name", path="A.txt", name="x/y")
        self.refused("rename", "not-found", path="absent", name="z")

    def test_upload_lands_whole_with_its_date(self):
        reply = self.upload("rapport.pdf", b"%PDF-1.7", mtime=1700000000000)
        self.assertTrue(reply["ok"], reply)
        path = os.path.join(self.root, "rapport.pdf")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.7")
        self.assertEqual(int(os.stat(path).st_mtime), 1700000000)
        self.assertEqual(reply["mtime"], os.stat(path).st_mtime_ns // 1000000)
        self.assertEqual(os.listdir(os.path.join(self.root, locosamba.STAGING)), [])

    def test_upload_conflicts_are_never_silent(self):
        self.put("a.txt", b"ancien")
        self.assertEqual(self.upload("a.txt", b"nouveau")["error"], "exists")
        self.assertTrue(self.upload("a.txt", b"deux", conflict="rename")["ok"])
        self.assertTrue(self.upload("a.txt", b"remplace", conflict="replace")["ok"])
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"remplace")
        with open(os.path.join(self.root, "a (2).txt"), "rb") as f:
            self.assertEqual(f.read(), b"deux")
        # Enregistré depuis l'éditeur pendant qu'un autre l'a modifié : refusé.
        stale = os.stat(os.path.join(self.root, "a.txt")).st_mtime_ns // 1000000 - 5000
        self.assertEqual(self.upload("a.txt", b"x", conflict="replace", expect=stale)["error"], "changed")
        os.mkdir(os.path.join(self.root, "dossier"))
        self.assertEqual(self.upload("dossier", b"x", conflict="replace")["error"], "is-dir")

    def test_upload_size_and_cancel(self):
        reply, fds = self.ok("upload", dir="", name="court.bin", size=10)
        os.write(fds[0], b"123")
        self.assertEqual(self.call("upload_done", dir="", name="court.bin", token=reply["token"], size=10)[0]["error"], "size")
        self.ok("upload_cancel", token=reply["token"])
        self.assertEqual(os.listdir(os.path.join(self.root, locosamba.STAGING)), [])
        self.refused("upload", "bad-name", dir="", name="a|b", size=1)
        self.refused("upload", "not-found", dir="absent", name="a.txt", size=1)

    def test_the_size_limit_holds(self):
        self.publish(Documents={"used": 9 * MIB})
        self.assertEqual(self.refused("upload", "quota", dir="", name="gros.bin", size=2 * MIB)["values"]["free"], MIB)
        self.ok("upload", dir="", name="a.bin", size=MIB // 2)
        self.refused("upload", "quota", dir="", name="b.bin", size=MIB // 2 + 1)      # le premier envoi compte déjà
        self.put("source.bin", b"x" * (MIB // 2 + 1))
        self.refused("copy", "quota", paths=["source.bin"], to="")

    def test_a_full_share_can_only_shrink(self):
        self.put("dossier/a.txt")
        self.publish(Documents={"full": True})
        self.refused("upload", "full", dir="", name="a.bin", size=1)
        self.refused("mkdir", "full", path="nouveau")
        self.refused("newfile", "full", path="b.txt")
        self.refused("copy", "full", paths=["dossier"], to="")
        self.ok("rename", path="dossier", name="classeur")
        self.assertEqual(self.wait(self.ok("move", paths=["classeur/a.txt"], to="")[0])["state"], "done")
        self.assertEqual(self.wait(self.ok("delete", paths=["a.txt"])[0])["state"], "done")

    def test_copy_and_move(self):
        self.put("Photos/2026/a.jpg", b"image")
        os.utime(os.path.join(self.root, "Photos/2026/a.jpg"), (1600000000, 1600000000))
        self.ok("mkdir", path="Archives")
        view = self.wait(self.ok("copy", paths=["Photos"], to="Archives")[0])
        self.assertEqual((view["state"], view["done"], view["total"]), ("done", 5, 5))
        copied = os.path.join(self.root, "Archives/Photos/2026/a.jpg")
        self.assertEqual(int(os.stat(copied).st_mtime), 1600000000)
        self.refused("copy", "into-itself", paths=["Archives"], to="Archives/Photos")
        self.refused("move", "into-itself", paths=["Archives"], to="Archives")
        # Copie dans son propre dossier : « Photos (2) ».
        self.wait(self.ok("copy", paths=["Photos"], to="")[0])
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Photos (2)")))
        # Nom déjà pris à destination : refus, sauf décision (passer, garder les deux, remplacer).
        self.assertEqual(self.ok("conflicts", paths=["Photos"], to="Archives")[0]["conflicts"], ["Photos"])
        self.assertEqual(self.wait(self.ok("copy", paths=["Photos"], to="Archives")[0])["errors"][0]["error"], "exists")
        self.wait(self.ok("copy", paths=["Photos"], to="Archives", decisions={"Photos": "skip"})[0])
        self.wait(self.ok("copy", paths=["Photos"], to="Archives", decisions={"Photos": "rename"})[0])
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Archives"))), ["Photos", "Photos (2)"])
        self.put("Photos/nouvelle.jpg")
        self.wait(self.ok("move", paths=["Photos"], to="Archives", decisions={"Photos": "replace"})[0])
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Archives/Photos"))), ["2026", "nouvelle.jpg"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "Photos")))

    def test_copy_without_copy_file_range(self):
        # Python construit sans copy_file_range (selon la bibliothèque C) : la copie se fait par lecture et écriture.
        self.put("a.bin", bytes(range(256)) * 1200)
        real = os.copy_file_range
        del os.copy_file_range
        try:
            self.assertEqual(self.wait(self.ok("copy", paths=["a.bin"], to="")[0])["state"], "done")
        finally:
            os.copy_file_range = real
        with open(os.path.join(self.root, "a (2).bin"), "rb") as f:
            self.assertEqual(f.read(), bytes(range(256)) * 1200)

    def test_between_two_shares(self):
        self.put("a.txt", b"document")
        self.wait(self.ok("copy", paths=["a.txt"], to_share="Photos", to="")[0])
        self.wait(self.ok("move", paths=["a.txt"], to_share="Photos", to="", decisions={"a.txt": "rename"})[0])
        self.assertEqual(sorted(os.listdir(self.photos)), ["a (2).txt", "a.txt"])
        self.assertEqual(os.listdir(self.root), [])

    def test_delete_takes_whole_folders(self):
        self.put("vieux/a/b/c.txt")
        self.put("vieux/d.txt")
        view = self.wait(self.ok("delete", paths=["vieux", "absent"])[0])
        self.assertEqual(view["state"], "done")
        self.assertEqual([e["name"] for e in view["errors"]], ["absent"])
        self.assertEqual(os.listdir(self.root), [])

    def test_zip_of_a_selection_or_a_folder(self):
        self.put("Photos/2026/a.jpg", b"aaa")
        self.put("notes.txt", b"bonjour")
        os.mkdir(os.path.join(self.root, locosamba.STAGING))
        for names, expected in ((["Photos", "notes.txt"], ["Photos/", "Photos/2026/", "Photos/2026/a.jpg", "notes.txt"]),
                                ([], ["Photos/", "Photos/2026/", "Photos/2026/a.jpg", "notes.txt"])):
            _reply, fds = self.ok("zip", path="", names=names)
            data = b""
            while True:
                chunk = os.read(fds[0], 65536)
                if not chunk:
                    break
                data += chunk
            archive = zipfile.ZipFile(io.BytesIO(data))
            self.assertEqual(archive.namelist(), expected)
            self.assertEqual(archive.read("notes.txt"), b"bonjour")
        self.refused("zip", "not-found", path="", names=["absent"])
        self.refused("zip", "bad-path", path="", names=["../x"])

    def test_abandoned_uploads_are_cleared(self):
        reply, _fds = self.ok("upload", dir="", name="a.bin", size=1)
        staging = os.path.join(self.root, locosamba.STAGING)
        old = os.path.join(staging, "0" * 32 + ".part")
        with open(old, "w"):
            pass
        os.utime(old, (time.time() - 2 * locosamba.STAGING_TTL,) * 2)
        os.utime(os.path.join(staging, reply["token"] + ".part"), (time.time() - 2 * locosamba.STAGING_TTL,) * 2)
        self.files.tidy()
        self.assertEqual(os.listdir(staging), [reply["token"] + ".part"])          # en cours : gardé


def serve_files(test, allowed):
    path = os.path.join(test.work, "files.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    test.addCleanup(server.close)
    server.bind(path)
    server.listen(16)
    worker = threading.Thread(target=locosamba.files_serve,
                              args=(server.dup().detach(), allowed, test.files), daemon=True)
    worker.start()

    def stop():
        server.shutdown(socket.SHUT_RDWR)
        worker.join(timeout=5)
        test.assertFalse(worker.is_alive())

    test.addCleanup(stop)
    return path


class TestSocket(ServiceTest):
    def test_the_portal_receives_the_open_file(self):
        self.put("notes.txt", b"bonjour")
        path = serve_files(self, {os.getuid()})
        reply, fds = locosamba.files_call({"op": "open", "share": "Documents", "path": "notes.txt"}, path=path)
        self.assertTrue(reply["ok"])
        self.assertEqual(os.read(fds[0], 100), b"bonjour")
        os.close(fds[0])
        reply, fds = locosamba.files_call({"op": "list", "share": "Documents", "path": ""}, path=path)
        self.assertEqual((reply["entries"][0]["name"], fds), ("notes.txt", []))

    def test_only_the_portal_account_is_heard(self):
        path = serve_files(self, {os.getuid() + 12345})
        with self.assertRaises((OSError, ValueError)):
            locosamba.files_call({"op": "list", "share": "Documents", "path": ""}, path=path, timeout=5)


class TestAgentLaunch(unittest.TestCase):
    def test_launched_as_the_share_owner_and_stopped_with_the_feature(self):
        worker = locosamba.Worker.__new__(locosamba.Worker)
        worker.enabled, worker.files_proc, worker.files_sock, worker.files_at = True, None, None, 0.0
        sock = mock.Mock()
        sock.fileno.return_value = 9
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch.object(locosamba, "installed", return_value=True), \
                mock.patch.object(locosamba, "_exists", return_value=True), \
                mock.patch.object(locosamba, "_owner_ids", return_value=(991, 992)), \
                mock.patch.object(locosamba, "files_socket", return_value=sock), \
                mock.patch.object(locosamba.subprocess, "Popen", return_value=proc) as popen:
            worker.files_keep()
            worker.files_keep()                                            # déjà lancé : rien de plus
            self.assertEqual(popen.call_count, 1)
            args, kwargs = popen.call_args
            self.assertEqual(args[0][1:], ["-I", locosamba.HERE, "files", "9"])
            self.assertEqual((kwargs["user"], kwargs["group"], kwargs["extra_groups"], kwargs["pass_fds"], kwargs["umask"]),
                             (991, 992, [], (9,), 0o007))
            worker.enabled = False
            with mock.patch.object(locosamba.os, "unlink"):
                worker.files_keep()
        proc.terminate.assert_called_once()
        sock.close.assert_called_once()
        self.assertIsNone(worker.files_sock)


class FilesPortalTest(PortalTest):
    """Portail des partages, avec un vrai service des fichiers derrière un socket temporaire."""

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.work, "documents")
        os.mkdir(self.root)
        self.files = locosamba.ShareFiles(self.status_path)
        sock_path = serve_files(self, {os.getuid()})
        patch = mock.patch.object(locosamba, "FILES_SOCK", sock_path)
        patch.start()
        self.addCleanup(patch.stop)
        with transaction() as conn:
            cur = conn.execute("INSERT INTO samba_shares (name, mount, folder, size_limit) VALUES ('Documents', '/', 'documents', ?)",
                               (10 * MIB,))
            self.share = cur.lastrowid
        publish(self.status_path, {"Documents": self.root})
        self.base = f"/shares/{self.share}/files"

    def linked(self, name, grants, right):
        """Compte loco avec ces droits, lié à un utilisateur Samba qui a ce droit sur le partage (None : aucun)."""
        browser, uid = self.member(name, grants)
        with transaction() as conn:
            account = conn.execute("INSERT INTO samba_accounts (user_id, login) VALUES (?, ?)", (uid, "sm-" + name)).lastrowid
            if right:
                conn.execute("INSERT INTO samba_access (share_id, account_id, can_write) VALUES (?, ?, ?)",
                             (self.share, account, 1 if right == "write" else 0))
        return browser, account

    def op(self, browser, op, **args):
        return browser.post(self.base + "/op", {"op": op, "args": json.dumps(args)}, page="/shares")

    def put(self, rel, data=b"x"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def wait(self, browser, job):
        for _i in range(200):
            view = browser.c.get(f"{self.base}/job/{job}", headers={"X-Requested-With": "fetch"}).get_json()
            if view.get("state") != "running":
                return view
            time.sleep(0.02)
        self.fail("tâche jamais finie")

    def upload(self, browser, name, data, folder="", chunk=None):
        begin = browser.post(self.base + "/upload", {"dir": folder, "name": name, "size": str(len(data))}, page="/shares")
        self.assertEqual(begin.status_code, 200, begin.get_data(as_text=True))
        token, csrf = begin.get_json()["token"], browser.token("/shares")
        chunk = chunk or max(1, len(data))
        for offset in range(0, len(data), chunk):
            sent = browser.c.post(f"{self.base}/upload/{token}?offset={offset}", data=data[offset:offset + chunk],
                                  content_type="application/octet-stream",
                                  headers={"X-CSRF-Token": csrf, "X-Requested-With": "fetch"})
            self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True))
        return browser.post(f"{self.base}/upload/{token}/done", {"mtime": "1700000000000"}, page="/shares")


class TestFilesPortal(FilesPortalTest):
    def test_the_right_is_needed(self):
        anonymous = fixture.Browser()
        self.assertEqual(anonymous.c.get(self.base).status_code, 302)                     # vers la connexion
        self.assertEqual(anonymous.c.get(self.base + "/list?path=", headers={"X-Requested-With": "fetch"}).status_code, 401)
        viewer, _uid = self.member("lecteur", {"samba.view"})
        self.assertEqual(viewer.c.get(self.base).status_code, 403)
        self.assertNotIn(self.base, viewer.c.get("/shares").get_data(as_text=True))
        self.assertIn("samba.files", permissions.PERMISSIONS)

    def test_a_manager_opens_every_share_to_write(self):
        page = self.admin.c.get("/shares").get_data(as_text=True)
        self.assertIn(f'href="{self.base}"', page)
        listing = self.admin.c.get(self.base + "/list?path=", headers={"X-Requested-With": "fetch"}).get_json()
        self.assertEqual((listing["mode"], listing["entries"]), ("write", []))
        self.assertEqual(self.op(self.admin, "mkdir", path="Factures").status_code, 200)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Factures")))
        html = self.admin.c.get(self.base).get_data(as_text=True)
        self.assertIn('data-backend="share"', html)
        self.assertIn('data-mode="write"', html)

    def test_a_linked_reader_reads_but_never_writes(self):
        self.put("notes.txt", b"bonjour")
        reader, _account = self.linked("lectrice", {"samba.files"}, "read")
        listing = reader.c.get(self.base + "/list?path=", headers={"X-Requested-With": "fetch"}).get_json()
        self.assertEqual((listing["mode"], [e["name"] for e in listing["entries"]]), ("read", ["notes.txt"]))
        with reader.c.get(self.base + "/raw?path=notes.txt&dl=1") as response:
            self.assertEqual(response.get_data(), b"bonjour")
        self.assertEqual(self.op(reader, "mkdir", path="x").status_code, 403)
        self.assertEqual(self.op(reader, "delete", paths=["notes.txt"]).status_code, 403)
        self.assertEqual(reader.post(self.base + "/upload", {"dir": "", "name": "a.txt", "size": "1"}, page="/shares").status_code, 403)
        self.assertTrue(os.path.exists(os.path.join(self.root, "notes.txt")))
        # Sa page : ses partages seulement, rien de leur gestion.
        page = reader.c.get("/shares").get_data(as_text=True)
        self.assertIn(f'href="{self.base}"', page)
        self.assertNotIn("Partitions", page)

    def test_a_linked_writer_writes(self):
        writer, _account = self.linked("redactrice", {"samba.files"}, "write")
        self.assertEqual(self.upload(writer, "a.txt", b"contenu").status_code, 200)
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"contenu")

    def test_no_access_no_share(self):
        stranger, account = self.linked("inconnue", {"samba.files"}, None)
        self.assertEqual(stranger.c.get(self.base).status_code, 404)
        with stranger.c.get(self.base + "/raw?path=x") as response:
            self.assertEqual(response.status_code, 404)
        with transaction() as conn:
            conn.execute("INSERT INTO samba_access (share_id, account_id, can_write) VALUES (?, ?, 1)", (self.share, account))
            conn.execute("UPDATE samba_accounts SET disabled=1 WHERE id=?", (account,))
        self.assertEqual(stranger.c.get(self.base).status_code, 404)                   # utilisateur Samba désactivé

    def test_upload_by_fragments_then_download_by_ranges(self):
        data = bytes(range(256)) * 40
        done = self.upload(self.admin, "donnees.bin", data, chunk=1000)
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.get_json()["name"], "donnees.bin")
        with self.admin.c.get(self.base + "/raw?path=donnees.bin&dl=1") as full:
            self.assertEqual((full.status_code, full.get_data()), (200, data))
            self.assertIn("attachment", full.headers["Content-Disposition"])
        with self.admin.c.get(self.base + "/raw?path=donnees.bin", headers={"Range": "bytes=100-199"}) as part:
            self.assertEqual((part.status_code, part.get_data(), part.headers["Content-Range"]), (206, data[100:200], f"bytes 100-199/{len(data)}"))
        with self.admin.c.get(self.base + "/raw?path=donnees.bin", headers={"Range": "bytes=99999-"}) as outside:
            self.assertEqual(outside.status_code, 416)
        with self.admin.c.get(self.base + "/raw?path=donnees.bin", headers={"If-None-Match": full.headers["ETag"]}) as again:
            self.assertEqual(again.status_code, 304)
        self.assertEqual(int(os.stat(os.path.join(self.root, "donnees.bin")).st_mtime), 1700000000)

    def test_a_fragment_needs_the_page_token_and_its_place(self):
        begin = self.admin.post(self.base + "/upload", {"dir": "", "name": "a.bin", "size": "4"}, page="/shares").get_json()
        url = f"{self.base}/upload/{begin['token']}"
        self.assertEqual(self.admin.c.post(url + "?offset=0", data=b"ab", content_type="application/octet-stream").status_code, 400)
        csrf = self.admin.token("/shares")
        headers = {"X-CSRF-Token": csrf, "X-Requested-With": "fetch"}
        self.assertEqual(self.admin.c.post(url + "?offset=0", data=b"ab", content_type="application/octet-stream", headers=headers).status_code, 200)
        late = self.admin.c.post(url + "?offset=0", data=b"ab", content_type="application/octet-stream", headers=headers)
        self.assertEqual((late.status_code, late.get_json()["written"]), (409, 2))   # le navigateur reprend où en est le serveur
        self.assertEqual(self.admin.c.post(url + "?offset=2", data=b"abc", content_type="application/octet-stream", headers=headers).status_code, 400)
        other, _account = self.linked("autre", {"samba.files", "samba.manage"}, "write")
        self.assertEqual(other.c.post(url + "?offset=2", data=b"cd", content_type="application/octet-stream",
                                      headers={"X-CSRF-Token": other.token("/shares"), "X-Requested-With": "fetch"}).status_code, 404)

    def test_previews_never_run_anything(self):
        self.put("photo.png", b"\x89PNG\r\n\x1a\n")
        self.put("page.html", b"<script>alert(1)</script>")
        self.put("doc.pdf", b"%PDF-1.7")
        with self.admin.c.get(self.base + "/raw?path=photo.png") as image:
            self.assertEqual((image.mimetype, image.headers["Content-Disposition"].split(";")[0]), ("image/png", "inline"))
            self.assertIn("sandbox", image.headers["Content-Security-Policy"])
        with self.admin.c.get(self.base + "/raw?path=page.html") as page:
            self.assertEqual((page.mimetype, page.headers["Content-Disposition"].split(";")[0]), ("application/octet-stream", "attachment"))
            self.assertEqual(page.headers["X-Content-Type-Options"], "nosniff")
        with self.admin.c.get(self.base + "/raw?path=doc.pdf") as pdf:
            self.assertEqual((pdf.mimetype, pdf.headers["X-Frame-Options"]), ("application/pdf", "SAMEORIGIN"))

    def test_zip_download(self):
        self.put("Photos/a.jpg", b"aaa")
        self.put("notes.txt", b"bonjour")
        with self.admin.post(self.base + "/zip", {"path": "", "names": ["Photos", "notes.txt"]}, page="/shares") as response:
            self.assertEqual(response.mimetype, "application/zip")
            self.assertIn("documents.zip", response.headers["Content-Disposition"].lower())
            archive = zipfile.ZipFile(io.BytesIO(response.get_data()))
        self.assertEqual(archive.namelist(), ["Photos/", "Photos/a.jpg", "notes.txt"])

    def test_jobs_belong_to_who_started_them_and_land_in_the_journal(self):
        self.put("vieux/a.txt")
        job = self.op(self.admin, "delete", paths=["vieux"]).get_json()["job"]
        self.assertEqual(self.wait(self.admin, job)["state"], "done")
        other, _account = self.linked("collegue", {"samba.files", "samba.manage"}, "write")
        self.assertEqual(other.c.get(f"{self.base}/job/{job}", headers={"X-Requested-With": "fetch"}).status_code, 404)
        self.upload(self.admin, "nouveau.txt", b"abc")
        with transaction() as conn:
            actions = [r[0] for r in conn.execute("SELECT action FROM audit WHERE action LIKE 'partages.fichiers.%' ORDER BY id")]
        self.assertEqual(actions[-2:], ["partages.fichiers.suppression", "partages.fichiers.televersement"])

    def test_copy_from_another_share(self):
        photos = os.path.join(self.work, "photos")
        os.mkdir(photos)
        with open(os.path.join(photos, "vacances.jpg"), "wb") as f:
            f.write(b"jpg")
        with transaction() as conn:
            other = conn.execute("INSERT INTO samba_shares (name, mount, folder, size_limit) VALUES ('Photos', '/', 'photos', ?)",
                                 (10 * MIB,)).lastrowid
        publish(self.status_path, {"Documents": self.root, "Photos": photos})
        job = self.op(self.admin, "copy", **{"from": other, "paths": ["vacances.jpg"], "to": ""}).get_json()["job"]
        self.assertEqual(self.wait(self.admin, job)["state"], "done")
        self.assertTrue(os.path.exists(os.path.join(self.root, "vacances.jpg")))
        # Un lecteur seul du partage d'origine ne peut pas en déplacer.
        reader, account = self.linked("deplace", {"samba.files"}, "write")
        with transaction() as conn:
            conn.execute("INSERT INTO samba_access (share_id, account_id, can_write) VALUES (?, ?, 0)", (other, account))
        self.assertEqual(self.op(reader, "move", **{"from": other, "paths": ["vacances.jpg"], "to": ""}).status_code, 403)

    def test_a_full_share_only_shrinks(self):
        self.put("a.txt")
        publish(self.status_path, {"Documents": self.root}, Documents={"full": True})
        refused = self.admin.post(self.base + "/upload", {"dir": "", "name": "b.txt", "size": "1"}, page="/shares")
        self.assertEqual((refused.status_code, refused.get_json()["code"]), (507, "full"))
        job = self.op(self.admin, "delete", paths=["a.txt"]).get_json()["job"]
        self.assertEqual(self.wait(self.admin, job)["state"], "done")

    def test_service_down_is_said(self):
        with mock.patch.object(locosamba, "FILES_SOCK", os.path.join(self.work, "absent.sock")):
            response = self.admin.c.get(self.base + "/list?path=", headers={"X-Requested-With": "fetch"})
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "service"))

    def test_unpublished_share_is_unavailable(self):
        publish(self.status_path, {})
        self.assertEqual(self.admin.c.get(self.base + "/list?path=", headers={"X-Requested-With": "fetch"}).status_code, 503)
        self.assertNotIn(f'href="{self.base}"', self.admin.c.get("/shares").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
