"""Traductions : chaque texte marqué (gabarits, code, scripts du navigateur) a sa version anglaise, avec les mêmes
valeurs à insérer ; le choix de langue suit le compte, puis le cookie, puis le navigateur."""
import ast
import json
import os
import re
import unittest

import test_portal as fixture
from loco import app, i18n

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal")
PLACEHOLDER = re.compile(r"%\((\w+)\)s")
CALLS = {"_": 1, "N_": 1, "L_": 1, "n_": 2, "gettext": 1, "ngettext": 2}


def template_messages():
    found = {}
    folder = os.path.join(ROOT, "templates")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            source = f.read()
        for lineno, _func, message in app.jinja_env.extract_translations(source):
            for text in (message if isinstance(message, tuple) else (message,)):
                if text:
                    found.setdefault(text, f"templates/{name}:{lineno}")
    return found


def python_messages():
    found = {}
    folder = os.path.join(ROOT, "loco")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py") or name == "i18n.py":
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CALLS:
                for arg in node.args[:CALLS[node.func.id]]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        found.setdefault(arg.value, f"loco/{name}:{node.lineno}")
    return found


def js_messages():
    i18n._js_messages = None
    return {m: "static" for m in i18n.js_messages()}


def all_messages():
    found = {}
    for source in (template_messages(), python_messages(), js_messages()):
        for k, v in source.items():
            found.setdefault(k, v)
    return found


class TestCatalog(unittest.TestCase):
    def test_every_marked_text_has_an_english_translation(self):
        catalog = i18n.catalog("en")
        missing = [f"{where} : {text!r}" for text, where in sorted(all_messages().items(), key=lambda kv: kv[1])
                   if not catalog.get(text)]
        self.assertEqual(missing, [], f"{len(missing)} texte(s) sans traduction anglaise (portal/i18n/en.json)")

    def test_translations_keep_the_same_values(self):
        # Seuls les suffixes de pluriel faits main (%(v)s = « s » ou rien) peuvent manquer : l'anglais n'accorde pas
        # les participes (« 3 postes prévus » -> « 3 devices planned »).
        for text, english in i18n.catalog("en").items():
            with self.subTest(text=text):
                source, target = set(PLACEHOLDER.findall(text)), set(PLACEHOLDER.findall(english))
                self.assertLessEqual(target, source)
                self.assertEqual({p for p in source - target if not re.fullmatch(r"v\d*", p)}, set())

    def test_catalog_is_valid_json_without_stale_duplicates(self):
        with open(os.path.join(ROOT, "i18n", "en.json"), encoding="utf-8") as f:
            pairs = json.load(f, object_pairs_hook=lambda kv: kv)
        keys = [k for k, _v in pairs]
        self.assertEqual(len(keys), len(set(keys)), "clé en double dans en.json")


class TestLanguageChoice(unittest.TestCase):
    def setUp(self):
        fixture.reset_failures()

    def test_browser_language_then_cookie_then_account(self):
        c = fixture.loco.app.test_client()
        self.assertIn('<html lang="en"', c.get("/login", headers={"Accept-Language": "en-US,en;q=0.9"}).get_data(as_text=True))
        self.assertIn('<html lang="fr"', c.get("/login", headers={"Accept-Language": "fr-FR"}).get_data(as_text=True))
        b = fixture.Browser()
        b.login("admin", fixture.ADMIN_PW)
        r = b.post("/langue", {"lang": "en", "next": "/systeme"}, page="/")
        self.assertEqual(r.status_code, 302)
        self.assertIn('<html lang="en"', b.c.get("/", headers={"Accept-Language": "fr"}).get_data(as_text=True))
        b.post("/langue", {"lang": "fr", "next": "/"}, page="/")
        self.assertIn('<html lang="fr"', b.c.get("/").get_data(as_text=True))
        self.assertEqual(b.post("/langue", {"lang": "xx"}, page="/").status_code, 400)

    def test_next_stays_on_this_site(self):
        b = fixture.Browser()
        r = b.post("/langue", {"lang": "en", "next": "//evil.example/"}, page="/login")
        self.assertEqual(r.headers["Location"].rsplit("/", 1)[-1], "")


if __name__ == "__main__":
    unittest.main()
