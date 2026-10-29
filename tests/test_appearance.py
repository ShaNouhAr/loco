"""Apparence par utilisateur : modèle (validation, bornage) et routes (enregistrement, fond, bornes)."""
import glob
import io
import os
import unittest

import test_portal  # noqa: F401  (initialise l'app, la base et le compte admin)
from test_portal import Browser, ADMIN_PW
from loco import appearance
from loco.db import transaction as connect

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class AppearanceModelTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(appearance.default(), {"theme": "oled", "accent": "neutral", "alpha": 100, "bg": ""})

    def test_parse_valid(self):
        a = appearance.parse({"theme": "midnight", "accent": "teal", "alpha": "80", "bg": "g:ocean"})
        self.assertEqual(a, {"theme": "midnight", "accent": "teal", "alpha": 80, "bg": "g:ocean"})

    def test_parse_rejects_unknown(self):
        a = appearance.parse({"theme": "rainbow", "accent": "gold", "bg": "g:nope"})
        self.assertEqual((a["theme"], a["accent"], a["bg"]), ("oled", "neutral", ""))

    def test_parse_clamps_alpha(self):
        self.assertEqual(appearance.parse({"alpha": "5"})["alpha"], appearance.ALPHA_MIN)
        self.assertEqual(appearance.parse({"alpha": "200"})["alpha"], appearance.ALPHA_MAX)
        self.assertEqual(appearance.parse({"alpha": "abc"})["alpha"], 100)

    def test_parse_bad_json(self):
        self.assertEqual(appearance.parse("{nope"), appearance.default())

    def test_presets_reference_valid_catalog(self):
        for key, label, theme, accent, alpha, bg in appearance.PRESETS:
            self.assertIn(theme, appearance.THEMES)
            self.assertIn(accent, appearance.ACCENTS)
            self.assertTrue(appearance.ALPHA_MIN <= alpha <= appearance.ALPHA_MAX)
            self.assertTrue(bg == "" or appearance._valid_bg(bg))


class AppearanceRouteTest(unittest.TestCase):
    def setUp(self):
        self.b = Browser()
        self.b.login("admin", ADMIN_PW)

    def tearDown(self):
        with connect() as conn:
            conn.execute("UPDATE users SET appearance='' WHERE username='admin'")
        for f in glob.glob(os.path.join(appearance.BG_DIR, "*")):
            os.unlink(f)

    def test_save_and_render(self):
        r = self.b.post("/account/appearance", {"theme": "sepia", "accent": "amber", "alpha": "70", "bg": "g:rose"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"theme": "sepia", "accent": "amber", "alpha": 70, "bg": "g:rose"})
        html = self.b.c.get("/").get_data(as_text=True)
        for frag in ('data-theme="sepia"', 'data-accent="amber"', 'data-alpha="70"', 'data-bg="g:rose"', 'data-appearance="user"'):
            self.assertIn(frag, html)

    def test_bg_u_without_file_falls_back(self):
        r = self.b.post("/account/appearance", {"theme": "oled", "accent": "neutral", "alpha": "100", "bg": "u"})
        self.assertEqual(r.get_json()["bg"], "")

    def test_background_upload_serve_remove(self):
        tok = self.b.token("/")
        r = self.b.c.post("/account/appearance/background",
                          data={"csrf": tok, "background": (io.BytesIO(PNG), "wall.png")},
                          content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["bg"], "u")
        img = self.b.c.get("/appearance/background.img")
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.get_data(), PNG)
        img.close()
        rem = self.b.post("/account/appearance/background/remove", {})
        self.assertEqual(rem.get_json()["bg"], "")
        gone = self.b.c.get("/appearance/background.img")
        self.assertEqual(gone.status_code, 404)
        gone.close()

    def test_background_rejects_format(self):
        tok = self.b.token("/")
        r = self.b.c.post("/account/appearance/background",
                          data={"csrf": tok, "background": (io.BytesIO(b"not an image at all"), "x.txt")},
                          content_type="multipart/form-data")
        self.assertEqual(r.status_code, 415)

    def test_background_rejects_too_big(self):
        tok = self.b.token("/")
        big = PNG + b"\x00" * (appearance.MAX_BG + 10)
        r = self.b.c.post("/account/appearance/background",
                          data={"csrf": tok, "background": (io.BytesIO(big), "big.png")},
                          content_type="multipart/form-data")
        self.assertEqual(r.status_code, 413)

    def test_appearance_requires_login(self):
        anon = Browser()
        self.assertIn(anon.c.post("/account/appearance", data={"csrf": anon.token(), "theme": "light"}).status_code, (302, 401, 403))
        missing = anon.c.get("/appearance/background.img")
        self.assertIn(missing.status_code, (302, 401, 403, 404))
        missing.close()


if __name__ == "__main__":
    unittest.main()
