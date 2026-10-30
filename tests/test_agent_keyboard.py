"""Agent Linux : un keysym se tape avec la touche ET le niveau (Maj, AltGr) de la disposition réelle du poste,
sans relâcher un modificateur tenu par le client ; relevé espacé d'une session distante silencieuse."""

import ctypes.util
import unittest

import fake_agent

agent = fake_agent.load_agent()
XKB = ctypes.util.find_library("xkbcommon") is not None


class TestInjector(unittest.TestCase):
    def setUp(self):
        # Table synthétique : « a » (30), « A » = Maj + 30, « @ » = AltGr + 11, Maj gauche (42), touche morte ^ (26), e (18).
        table = {0x61: (30, ()), 0x41: (30, (42,)), 0x40: (11, (100,)), 0xffe1: (42, ()), 0xfe52: (26, ()), 0x65: (18, ())}
        self.events = []
        self.keys = agent.KeyInjector(agent.KeyPlanner(table), lambda code, down: self.events.append((code, down)))

    def test_levels_dead_keys_and_unknown_keysyms(self):
        self.assertTrue(self.keys.key(0x41, True))
        self.assertTrue(self.keys.key(0x41, False))
        self.assertEqual(self.events, [(42, True), (30, True), (30, False), (42, False)])
        self.events.clear()
        self.keys.key(0xea, True)                                # ê : touche morte ^ puis e, à l'appui
        self.keys.key(0xea, False)
        self.assertEqual(self.events, [(26, True), (26, False), (18, True), (18, False)])
        self.assertFalse(self.keys.key(0x1f600, True))           # absent de la disposition : chemin habituel

    def test_a_modifier_held_by_the_client_is_never_released_for_it(self):
        self.keys.key(0xffe1, True)                              # Maj tenue dans le navigateur
        self.keys.key(0x41, True)
        self.keys.key(0x41, False)
        self.assertEqual(self.events, [(42, True), (30, True), (30, False)])
        self.keys.key(0xffe1, False)
        self.assertEqual(self.events[-1], (42, False))


@unittest.skipUnless(XKB, "libxkbcommon absente")
class TestRealLayouts(unittest.TestCase):
    def plan(self, layout, char):
        table = agent.xkb_table(layout)
        return agent.KeyPlanner(table).plan(agent._char_keysym(char))

    def test_azerty(self):
        self.assertEqual(self.plan("fr", "<"), [(86, ())])
        self.assertEqual(self.plan("fr", ">"), [(86, (42,))])
        self.assertEqual(self.plan("fr", "@"), [(11, (100,))])
        self.assertEqual(self.plan("fr", "A"), [(16, (42,))])
        self.assertEqual(self.plan("fr", "1"), [(2, (42,))])
        self.assertEqual(self.plan("fr", "ê"), [(26, ()), (18, ())])
        self.assertEqual(self.plan("fr", "€"), [(18, (100,))])

    def test_qwerty(self):
        self.assertEqual(self.plan("us", "@"), [(3, (42,))])
        self.assertEqual(self.plan("us", "a"), [(30, ())])


class TestIdlePolling(unittest.TestCase):
    def test_backoff(self):
        self.assertEqual([agent.remote_idle_delay(s) for s in (0, 2.9, 3, 29, 30, 600)], [0.025, 0.025, 0.1, 0.1, 0.25, 0.25])


if __name__ == "__main__":
    unittest.main()
