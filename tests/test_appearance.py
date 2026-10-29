"""Apparence par utilisateur : modèle (validation, bornage) et routes (enregistrement, fond, bornes)."""
import glob
import io
import os
import unittest

import test_portal  # noqa: F401  (initialise l'app, la base et le compte admin)
from test_portal import Browser, ADMIN_PW
from loco import appearance, auth
from loco.db import transaction as connect

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


class AppearanceModelTest(unittest.TestCase):
    def test_default(self):
        d = appearance.default()
        self.assertEqual(set(d), {"theme", "accent", "alpha", "bg"})
        self.assertIn(d["theme"], appearance.THEMES)
        self.assertIn(d["accent"], appearance.ACCENTS)
        self.assertTrue(d["bg"] == "" or appearance._valid_bg(d["bg"]))
        self.assertTrue(appearance.ALPHA_MIN <= d["alpha"] <= appearance.ALPHA_MAX)

    def test_parse_valid(self):
        a = appearance.parse({"theme": "midnight", "accent": "teal", "alpha": "80", "bg": "g:ocean"})
        self.assertEqual(a, {"theme": "midnight", "accent": "teal", "alpha": 80, "bg": "g:ocean"})

    def test_parse_rejects_unknown(self):
        d = appearance.default()
        a = appearance.parse({"theme": "rainbow", "accent": "gold", "bg": "g:nope"})
        self.assertEqual((a["theme"], a["accent"], a["bg"]), (d["theme"], d["accent"], d["bg"]))

    def test_parse_clamps_alpha(self):
        self.assertEqual(appearance.parse({"alpha": "5"})["alpha"], appearance.ALPHA_MIN)
        self.assertEqual(appearance.parse({"alpha": "200"})["alpha"], appearance.ALPHA_MAX)
        self.assertEqual(appearance.parse({"alpha": "abc"})["alpha"], appearance.default()["alpha"])

    def test_parse_bad_json(self):
        self.assertEqual(appearance.parse("{nope"), appearance.default())

    def test_presets_reference_valid_catalog(self):
        for key, label, theme, accent, alpha, bg in appearance.PRESETS:
            self.assertIn(theme, appearance.THEMES)
            self.assertIn(accent, appearance.ACCENTS)
            self.assertTrue(appearance.ALPHA_MIN <= alpha <= appearance.ALPHA_MAX)
            self.assertTrue(bg == "" or appearance._valid_bg(bg))

    def test_wallpaper_validation(self):
        default_bg = appearance.default()["bg"]
        self.assertEqual(appearance.parse({"bg": "w:montagne"})["bg"], "w:montagne")
        self.assertEqual(appearance.parse({"bg": "w:nope"})["bg"], default_bg)   # invalide -> défaut
        self.assertEqual(appearance.parse({"bg": "w:"})["bg"], default_bg)

    def test_wallpaper_files_present(self):
        wp = os.path.join(os.path.dirname(os.path.dirname(appearance.__file__)), "static", "wallpapers")
        for name in appearance.WALLPAPERS:
            self.assertTrue(os.path.exists(os.path.join(wp, f"{name}.jpg")), f"{name}.jpg manquant")
            self.assertTrue(os.path.exists(os.path.join(wp, f"{name}-thumb.jpg")), f"{name}-thumb.jpg manquant")


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


class LogoTest(unittest.TestCase):
    """Logo global de l'instance : admin seulement, servi publiquement, reflété partout."""

    def setUp(self):
        self.b = Browser()
        self.b.login("admin", ADMIN_PW)

    def tearDown(self):
        for f in glob.glob(os.path.join(appearance.BG_DIR, "logo.*")):
            os.unlink(f)

    def _set_logo(self, blob, name):
        return self.b.c.post("/appearance/logo",
                             data={"csrf": self.b.token("/"), "logo": (io.BytesIO(blob), name)},
                             content_type="multipart/form-data")

    def test_admin_sets_and_serves_logo(self):
        self.assertEqual(self._set_logo(PNG, "brand.png").status_code, 200)
        anon = Browser()                                   # servi sans authentification (page de connexion)
        img = anon.c.get("/appearance/logo.img")
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.get_data(), PNG)
        self.assertIn("default-src 'none'", img.headers.get("Content-Security-Policy", ""))
        img.close()
        html = self.b.c.get("/").get_data(as_text=True)    # en-tête + favicon pointent sur le logo
        self.assertIn("/appearance/logo.img", html)
        self.assertIn('<img class="brand-logo"', html)

    def test_svg_logo_accepted(self):
        self.assertEqual(self._set_logo(SVG, "logo.svg").status_code, 200)
        img = self.b.c.get("/appearance/logo.img")
        self.assertTrue(img.headers["Content-Type"].startswith("image/svg+xml"), img.headers["Content-Type"])
        img.close()

    def test_remove_restores_default(self):
        self._set_logo(PNG, "b.png")
        self.assertTrue(self.b.post("/appearance/logo/remove", {}).get_json()["ok"])
        gone = self.b.c.get("/appearance/logo.img")
        self.assertEqual(gone.status_code, 404)
        gone.close()
        html = self.b.c.get("/").get_data(as_text=True)
        self.assertNotIn("/appearance/logo.img", html)
        self.assertIn('viewBox="0 0 1118 750"', html)      # silhouette loco par défaut revenue

    def test_rejects_bad_format(self):
        self.assertEqual(self._set_logo(b"not an image at all", "x.bin").status_code, 415)

    def test_rejects_too_big(self):
        self.assertEqual(self._set_logo(PNG + b"\x00" * (appearance.MAX_LOGO + 10), "big.png").status_code, 413)

    def test_non_admin_cannot_set_logo(self):
        with connect() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE username='viewer_logo'").fetchone():
                auth.create_user(conn, "viewer_logo", "Viewer-Pass-123456", "viewer", must_change=False)
        v = Browser()
        v.login("viewer_logo", "Viewer-Pass-123456")
        r = v.c.post("/appearance/logo",
                     data={"csrf": v.token("/"), "logo": (io.BytesIO(PNG), "b.png")},
                     content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(v.c.post("/appearance/logo/remove", data={"csrf": v.token("/")}).status_code, 403)
