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


class TestKeypad(unittest.TestCase):
    """Pavé numérique : le chiffre tapé dans le navigateur arrive tel quel, quel que soit Verr. num. sur le poste."""
    def test_keypad_keysyms_become_their_main_block_equivalent(self):
        k = agent.KEYPAD_KEYS
        self.assertEqual([k[0xffb0 + n] for n in range(10)], [ord(str(n)) for n in range(10)])
        self.assertEqual([k[s] for s in (0xffaa, 0xffab, 0xffac, 0xffad, 0xffae, 0xffaf, 0xffbd)], [ord(c) for c in "*+,-./="])
        self.assertEqual((k[0xff8d], k[0xff95], k[0xff96], k[0xff9f]), (0xff0d, 0xff50, 0xff51, 0xffff))   # Entrée, Origine, gauche, Suppr
        self.assertNotIn(0xff7f, k)                              # Verr. num. reste lui-même

    def serve(self, messages, keycodes):
        """Session RFB complète sur un faux poste : renvoie les frappes reçues par le moteur."""
        import io
        import struct
        from unittest import mock
        events = []

        class Backend:
            width, height, interval = 4, 4, 0
            def key(self, sym, down): events.append(("sym", sym, bool(down))); return True
            def set_cursor(self, enabled): pass
            def flush(self): pass
            def close(self): pass
        if keycodes:
            Backend.keycode = lambda self, code, down: events.append(("code", code, bool(down)))
        hello = b"RFB 003.008\n" + b"\x01" + b"\x01"
        encodings = struct.pack(">BxHi", 2, 1, agent.QEMU_EXT_KEYS)
        fake = mock.Mock(buffer=io.BytesIO(hello + encodings + b"".join(messages)))
        with mock.patch.object(agent.sys, "stdin", fake), mock.patch.object(agent.sys, "stdout", mock.Mock(buffer=io.BytesIO())):
            agent._rfb_serve(Backend())
        return events

    def test_keypad_digit_is_typed_as_its_character_even_with_physical_keycodes(self):
        import struct
        plain = lambda sym, down: struct.pack(">BBxxI", 4, down, sym)
        qemu = lambda sym, code, down: struct.pack(">BBHII", 255, 0, down, sym, code)
        # Sans touches physiques (X11) : KP_7 puis KP_Enter deviennent « 7 » et Entrée.
        self.assertEqual(self.serve([plain(0xffb7, 1), plain(0xffb7, 0), plain(0xff8d, 1), plain(0xff8d, 0)], False),
                         [("sym", 0x37, True), ("sym", 0x37, False), ("sym", 0xff0d, True), ("sym", 0xff0d, False)])
        # Avec touches physiques (Wayland) : le pavé passe par son caractère, une lettre garde sa touche (a = 30).
        self.assertEqual(self.serve([qemu(0xffb7, 0x47, 1), qemu(0xffb7, 0x47, 0), qemu(0x61, 0x1e, 1), qemu(0x61, 0x1e, 0)], True),
                         [("sym", 0x37, True), ("sym", 0x37, False), ("code", 30, True), ("code", 30, False)])


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

    def test_rest_follows_the_server_within_bounds(self):
        # Sans session : la cadence vient du serveur (fiche du poste ouverte, petit parc), entre 0,25 s et 5 s.
        answers = ({"interval": 0.3}, {"interval": 1}, {"interval": 5}, {"interval": 0.01}, {"interval": 60}, {}, {"interval": "x"},
                   {"interval": None}, {"interval": float("nan")})
        self.assertEqual([agent.remote_rest_delay(a) for a in answers], [0.3, 1, 5, 0.25, 5, 5, 5, 5, 5])


if __name__ == "__main__":
    unittest.main()
