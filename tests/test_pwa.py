"""Support PWA : manifeste, service worker, page hors-ligne, icônes, métas (points d'entrée publics)."""
import json
import os
import unittest

import test_portal  # noqa: F401  (initialise l'app)
from test_portal import ADMIN_PW, Browser
import loco


class PwaTest(unittest.TestCase):
    def test_manifest(self):
        r = Browser().c.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["Content-Type"].startswith("application/manifest+json"), r.headers["Content-Type"])
        data = json.loads(r.get_data())
        self.assertEqual(data["name"], "loco")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(any(i["sizes"] == "512x512" for i in data["icons"]))
        self.assertTrue(any(i.get("purpose") == "maskable" for i in data["icons"]))

    def test_service_worker(self):
        r = Browser().c.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["Content-Type"])
        self.assertEqual(r.headers["Service-Worker-Allowed"], "/")
        body = r.get_data(as_text=True)
        self.assertIn("addEventListener", body)
        self.assertIn("/offline", body)
        self.assertNotIn("{{", body)                      # version rendue, aucun reste de template

    def test_offline_page(self):
        r = Browser().c.get("/offline")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Hors ligne", html)
        self.assertIn("icon-192.png", html)

    def test_icons_present(self):
        icons = os.path.join(os.path.dirname(os.path.dirname(loco.pwa.__file__)), "static", "icons")
        for name in ("icon-192.png", "icon-512.png", "icon-maskable.png"):
            self.assertTrue(os.path.exists(os.path.join(icons, name)), name)

    def test_meta_in_rendered_pages(self):
        b = Browser(); b.login("admin", ADMIN_PW)
        html = b.c.get("/").get_data(as_text=True)
        self.assertIn('rel="manifest"', html)
        self.assertIn("/manifest.webmanifest", html)
        self.assertIn('name="theme-color"', html)
        self.assertIn("apple-touch-icon", html)

    def test_endpoints_public(self):
        anon = Browser()
        for path in ("/manifest.webmanifest", "/sw.js", "/offline"):
            self.assertEqual(anon.c.get(path).status_code, 200, path)

    def test_csp_allows_worker_and_manifest(self):
        r = Browser().c.get("/offline")
        csp = r.headers.get("Content-Security-Policy", "")
        self.assertIn("worker-src 'self'", csp)
        self.assertIn("manifest-src 'self'", csp)
