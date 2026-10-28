"""Caches servis par le serveur : analyse (nginx sliced, apt-cacher-ng), purge au cas par cas ou en entier, routes."""
import importlib.util
import os
import shutil
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
from test_portal import ADMIN_PW, Browser
from loco import appliance

# locoadmin est hors du paquet loco (l'agent tourne en root sans l'app) : importé à part pour ses fonctions pures.
_spec = importlib.util.spec_from_file_location("locoadmin", os.path.join(fixture.ROOT, "portal", "locoadmin.py"))
locoadmin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locoadmin)


def _nginx_file(root, key, size, sub="ab/cd"):
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.urandom(8).hex())
    with open(path, "wb") as f:
        f.write(b"binheader\nKEY: " + key + b"\nHTTP/1.1 200 OK\r\n\r\n" + b"x" * size)
    return path


class TestCacheScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lr-cache-")
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_nginx_merges_slices_into_one_object(self):
        wu = os.path.join(self.root, "wucache")
        os.makedirs(wu)
        _nginx_file(wu, b"dl.ms.com/a/big.cab bytes=0-1048575", 1000)
        _nginx_file(wu, b"dl.ms.com/a/big.cab bytes=1048576-2097151", 1000, "zz/zz")
        _nginx_file(wu, b"dl.ms.com/a/small.psf bytes=0-10", 10, "11/22")
        total, count, objs = locoadmin._scan_nginx(wu)
        self.assertEqual(count, 3)                       # trois fichiers sur le disque
        self.assertEqual(len(objs), 2)                   # deux objets logiques
        big = next(o for o in objs.values() if o["name"] == "big.cab")
        self.assertEqual(big["parts"], 2)
        self.assertEqual(big["context"], "dl.ms.com/a")
        self.assertGreater(total, 2000)

    def test_purge_object_then_everything(self):
        wu = os.path.join(self.root, "wucache")
        os.makedirs(wu)
        _nginx_file(wu, b"h/a.bin bytes=0-1", 50)
        _nginx_file(wu, b"h/a.bin bytes=2-3", 50, "zz/zz")
        _nginx_file(wu, b"h/b.bin bytes=0-1", 50, "11/22")
        a = next(o for o in locoadmin._scan_nginx(wu)[2].values() if o["name"] == "a.bin")
        with mock.patch.object(locoadmin, "read_conf", return_value={"DATA": self.root}):
            self.assertGreater(locoadmin.purge_cache("wu", entry_id=a["id"]), 0)
            self.assertEqual(locoadmin._scan_nginx(wu)[1], 1)   # seul b.bin reste
            self.assertEqual(locoadmin.purge_cache("wu", entry_id="bad"), 0)
            locoadmin.purge_cache("wu", everything=True)
            self.assertEqual(locoadmin._scan_nginx(wu)[1], 0)

    def test_acng_ignores_head_and_internal_files(self):
        acng = os.path.join(self.root, "apt")
        pkg = os.path.join(acng, "debrep/pool/curl.deb")
        os.makedirs(os.path.dirname(pkg))
        os.makedirs(os.path.join(acng, "_xstore"))
        for path, blob in ((pkg, b"d" * 100), (pkg + ".head", b"HTTP/1.1 200\n"), (os.path.join(acng, "_xstore", "x"), b"z")):
            with open(path, "wb") as f:
                f.write(blob)
        total, count, objs = locoadmin._scan_acng(acng)
        self.assertEqual(count, 1)                        # .head et _xstore ignorés
        obj = next(iter(objs.values()))
        self.assertEqual(obj["name"], "curl.deb")
        with mock.patch.object(locoadmin, "_cache_dir", return_value=acng), \
             mock.patch.object(locoadmin, "_cache_kind", return_value="acng"):
            locoadmin.purge_cache("apt", entry_id=obj["id"])
        self.assertFalse(os.path.exists(pkg))
        self.assertFalse(os.path.exists(pkg + ".head"))    # en-têtes associés retirés aussi

    def test_cache_request_validates(self):
        with self.assertRaises(ValueError):
            locoadmin.cache_request("purge", "nope", everything=True)
        with self.assertRaises(ValueError):
            locoadmin.cache_request("purge", "wu", entry_id="not-hex")


FAKE = {"caches": {"wu": {"present": True, "bytes": 2048, "count": 3, "shown": 2, "scanned": "2026-10-28 20:00",
                          "entries": [{"id": "a" * 16, "name": "big.cab", "context": "dl.ms.com/a", "bytes": 2000, "parts": 2, "at": "2026-10-28 19:59"},
                                      {"id": "b" * 16, "name": "small.psf", "context": "dl.ms.com/a", "bytes": 48, "parts": 1, "at": "2026-10-28 19:58"}]}}}


@unittest.skipUnless(hasattr(appliance, "locoadmin"), "agent d'administration absent")
class TestCacheRoutes(unittest.TestCase):
    def setUp(self):
        self.b = Browser()
        self.b.login("admin", ADMIN_PW)

    def test_state_lists_entries_and_requests_scan(self):
        with mock.patch.object(appliance.locoadmin, "cache_index", return_value=FAKE), \
             mock.patch.object(appliance.locoadmin, "cache_request") as req:
            data = self.b.c.get("/cache/state?areas=wu&scan=1").get_json()
        self.assertIn("wu", data)
        self.assertTrue(data["wu"]["can_purge"])
        self.assertEqual(len(data["wu"]["entries"]), 2)
        self.assertEqual(data["wu"]["count"], 3)
        req.assert_called_with("scan", "wu", user="admin")

    def test_purge_one_and_all(self):
        with mock.patch.object(appliance.locoadmin, "cache_request") as req:
            r = self.b.post("/cache/purge", {"area": "wu", "id": "a" * 16}, page="/windows-update")
            self.assertEqual(r.status_code, 200)
            req.assert_called_with("purge", "wu", entry_id="a" * 16, everything=False, user="admin")
            self.b.post("/cache/purge", {"area": "wu", "all": "1"}, page="/windows-update")
            self.assertTrue(req.call_args.kwargs["everything"])

    def test_unknown_area_rejected(self):
        r = self.b.post("/cache/purge", {"area": "nope", "all": "1"}, page="/windows-update")
        self.assertEqual(r.status_code, 400)
