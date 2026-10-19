"""Apparence : thèmes OLED, sombre doux et clair, couleurs d'accent, appliqués avant l'affichage."""
from pathlib import Path
import unittest

import test_portal as fixture

ROOT = Path(__file__).resolve().parents[1]


class TestAppearance(unittest.TestCase):
    def test_pages_offer_every_theme_and_accent(self):
        admin = fixture.Browser()
        admin.login("admin", fixture.ADMIN_PW)
        for page in (admin.c.get("/").get_data(as_text=True), fixture.Browser().c.get("/login").get_data(as_text=True)):
            for theme in ("oled", "dim", "light"):
                self.assertIn(f'data-set-theme="{theme}"', page)
            for accent in ("neutral", "blue", "green", "violet", "orange", "pink"):
                self.assertIn(f'data-set-accent="{accent}"', page)

    def test_styles_define_each_theme_and_accent(self):
        css = (ROOT / "portal/static/style.css").read_text()
        for selector in ('[data-theme="dim"]', '[data-accent="blue"]', '[data-accent="pink"]', ".btn.primary { background: var(--accent)"):
            self.assertIn(selector, css)
        loader = (ROOT / "portal/static/theme.js").read_text()
        self.assertIn('t === "dim"', loader)          # thème mémorisé reconnu
        self.assertIn("data-accent", loader)


if __name__ == "__main__":
    unittest.main()
