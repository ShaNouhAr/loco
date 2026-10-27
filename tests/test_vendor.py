"""Bibliothèques embarquées : chaque dossier de portal/static/vendor et chaque police tierce est déclaré dans
SOURCES.json (provenance et empreinte), avec sa licence à côté."""
import json
import os
import unittest

STATIC = os.path.join(os.path.dirname(__file__), "..", "portal", "static")


class VendorTest(unittest.TestCase):
    def test_every_vendored_library_is_declared_with_its_license(self):
        with open(os.path.join(STATIC, "vendor", "SOURCES.json"), encoding="utf-8") as f:
            sources = json.load(f)
        declared = {(e["name"].split("/")[-1].replace("addon-", "xterm-") if e["name"].startswith("@xterm/addon-") else
                     "xterm" if e["name"] == "@xterm/xterm" else e["name"].lower()) + "-" + e["version"] for e in sources}
        for entry in sources:
            self.assertTrue(entry.get("integrity") or entry.get("sha256"), entry["name"])
        for folder in os.listdir(os.path.join(STATIC, "vendor")):
            path = os.path.join(STATIC, "vendor", folder)
            if not os.path.isdir(path):
                continue
            self.assertIn(folder, declared, f"{folder} absent de SOURCES.json")
            self.assertTrue(any(n.upper().startswith(("LICENSE", "COPYING")) for n in os.listdir(path)), f"licence absente : {folder}")
        fonts = next(e for e in sources if e["name"] == "JetBrains Mono")
        for name in fonts["files"]:
            self.assertTrue(os.path.isfile(os.path.join(STATIC, name)), name)
        self.assertTrue(os.path.isfile(os.path.join(STATIC, "fonts", "JetBrainsMono-LICENSE.txt")))


if __name__ == "__main__":
    unittest.main()
