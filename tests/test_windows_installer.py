"""Installeur MSI de l'agent Windows : une mise à niveau pose la nouvelle version AVANT de retirer l'ancienne.

Le placement par défaut (RemoveExistingProducts après InstallValidate) retire l'ancienne version d'abord : une coupure de
courant entre les deux laissait le poste sans agent, dossier vide et service introuvable (vu le 2026-11-25 sur un vrai
PC, reproduit puis corrigé sur Windows 11 en coupant la machine à quatre moments de la mise à jour)."""
import os
import re
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
WXS = os.path.join(HERE, "..", "windows", "client", "loco-client.wxs")
AGENT = os.path.join(HERE, "..", "windows", "client", "LocoClient.cs")
NS = {"w": "http://schemas.microsoft.com/wix/2006/wi"}


class TestUpgradeOrder(unittest.TestCase):
    def setUp(self):
        self.product = ET.parse(WXS).getroot().find("w:Product", NS)

    def test_the_old_version_is_removed_after_the_new_one_is_in_place(self):
        self.assertIsNotNone(self.product.find("w:MajorUpgrade", NS))
        removal = self.product.find("w:InstallExecuteSequence/w:RemoveExistingProducts", NS)
        self.assertIsNotNone(removal)
        self.assertEqual(removal.get("After"), "InstallFinalize")

    def test_components_keep_their_identity_from_one_version_to_the_next(self):
        # Retirer l'ancienne version après coup ne touche à rien de la nouvelle seulement si chaque composant garde son
        # GUID d'une version à l'autre.
        guids = [c.get("Guid") for c in self.product.iter("{%s}Component" % NS["w"])]
        self.assertTrue(guids)
        for guid in guids:
            self.assertRegex(guid or "", r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class TestAgentUpdate(unittest.TestCase):
    def setUp(self):
        with open(AGENT, encoding="utf-8") as f:
            self.code = f.read()

    def test_the_update_log_survives_a_power_cut(self):
        self.assertRegex(self.code, r'/qn /norestart /l\*v! ')

    def test_settings_are_on_disk_before_they_replace_the_old_ones(self):
        save = self.code[self.code.index("static void SaveJson"):]
        save = save[:save.index("\n        }\n")]
        self.assertIn("FileOptions.WriteThrough", save)
        self.assertIn("Flush(true)", save)
        self.assertIn("File.Replace", save)

    def test_old_installers_are_pruned(self):
        self.assertIn("PruneMsiCacheSoon();", self.code)
        self.assertTrue(re.search(r"Normalize\(v\) >= Normalize\(running\)\) continue;", self.code))


if __name__ == "__main__":
    unittest.main()
