"""Journal des versions en trois langues : CHANGELOG.md (anglais, par défaut), CHANGELOG.fr.md et CHANGELOG.es.md
listent les mêmes versions, dans le même ordre, avec autant de points par version, et s'ouvrent sur la version en
cours. Le portail les lit selon la langue du compte (loco.appliance.changelog_path) et l'agent d'administration les
relit pour la version publiée (locoadmin.release_changelog)."""
import os
import re
import unittest

from test_portal import config

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FILES = {"en": "CHANGELOG.md", "fr": "CHANGELOG.fr.md", "es": "CHANGELOG.es.md"}
BARS = {
    "en": "**English** · [Français](CHANGELOG.fr.md) · [Español](CHANGELOG.es.md)",
    "fr": "[English](CHANGELOG.md) · **Français** · [Español](CHANGELOG.es.md)",
    "es": "[English](CHANGELOG.md) · [Français](CHANGELOG.fr.md) · **Español**",
}
HEADING = re.compile(r"^## (\S+)(?: — (\d{4}-\d{2}-\d{2}))?$")


def read(lang):
    with open(os.path.join(ROOT, FILES[lang]), encoding="utf-8") as f:
        return f.read()


def sections(text):
    """[(version, date, nombre de points, nombre de sous-points)] dans l'ordre du fichier."""
    found, current = [], None
    for line in text.splitlines():
        m = HEADING.match(line)
        if line.startswith("## ") and not m:
            raise AssertionError(f"titre de version mal formé : {line!r}")
        if m:
            current = [m.group(1), m.group(2) or "", 0, 0]
            found.append(current)
        elif current is not None and line.startswith("- "):
            current[2] += 1
        elif current is not None and re.match(r"^ {2,}- ", line):
            current[3] += 1
    return [tuple(s) for s in found]


class ChangelogTranslations(unittest.TestCase):
    def test_each_file_opens_with_its_language_bar(self):
        for lang, bar in BARS.items():
            with self.subTest(lang=lang):
                self.assertEqual(read(lang).splitlines()[0], bar)

    def test_the_three_files_list_the_same_versions_in_the_same_order(self):
        reference = [s[:2] for s in sections(read("fr"))]
        self.assertGreater(len(reference), 200)
        for lang in ("en", "es"):
            with self.subTest(lang=lang):
                self.assertEqual([s[:2] for s in sections(read(lang))], reference)

    def test_each_version_has_as_many_points_in_each_language(self):
        reference = sections(read("fr"))
        for lang in ("en", "es"):
            for ref, other in zip(reference, sections(read(lang))):
                with self.subTest(lang=lang, version=ref[0]):
                    self.assertEqual(other[2:], ref[2:])

    def test_the_current_version_comes_first_everywhere(self):
        for lang in FILES:
            with self.subTest(lang=lang):
                self.assertEqual(sections(read(lang))[0][0], config.VERSION)

    def test_the_portal_reads_the_english_file_by_default(self):
        self.assertEqual(os.path.basename(config.CHANGELOG_PATH), "CHANGELOG.md")


if __name__ == "__main__":
    unittest.main()
