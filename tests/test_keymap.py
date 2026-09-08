from claude_pins.keymap import ACTIONS, Keymap, key_warning, validate_key, parse
from tests.helpers import Sandbox


class KeymapTests(Sandbox):
    def test_defaults_match_design(self):
        km = Keymap()
        self.assertEqual(km.key("open"), "enter")
        self.assertEqual(km.key("palette"), "ctrl-x")
        self.assertEqual(km.key("help"), "f1")
        self.assertEqual(km.key("fork_mode"), "")
        self.assertEqual(km.key("refresh"), "ctrl-r")
        self.assertEqual({a.id for a in ACTIONS if a.bind}, {"refresh"})   # the one key that stays inside fzf
        bound = km.bound()
        self.assertEqual(len(bound), len([a for a in ACTIONS if a.key]))  # no duplicate default keys
        self.assertNotIn("alt-enter", bound)
        for k in bound:
            self.assertIsNone(validate_key(k), k)
            self.assertIsNone(key_warning(k), k)

    def test_validate(self):
        self.assertIsNone(validate_key("alt-t"))
        self.assertIsNone(validate_key("f5"))
        self.assertIsNone(validate_key("shift-tab"))
        self.assertIsNone(validate_key(""))
        self.assertIn("printable", validate_key("?"))
        self.assertIn("not an fzf key", validate_key("super-x"))
        self.assertIn("editing", key_warning("ctrl-a"))
        self.assertIn("Windows Terminal", key_warning("alt-enter"))

    def test_conflicts_set_reset_persist(self):
        km = Keymap()
        self.assertEqual(km.conflicts("touch", "alt-x"), ["unpin"])
        km.set("touch", "f5")
        self.assertEqual(km.key("touch"), "f5")
        self.assertFalse(km.is_default())
        path = km.save()
        self.assertTrue(path.exists())
        km2 = Keymap.load()
        self.assertEqual(km2.key("touch"), "f5")
        km2.reset("touch")
        self.assertEqual(km2.key("touch"), "alt-t")
        km2.set("open", "alt-z"); km2.reset()
        self.assertTrue(km2.is_default())

    def test_parse_tolerant(self):
        text = '# c\nopen = "enter"  # trailing\nunpin = alt-x\n\n[junk]\nbad line\nkeep=\'alt-k\'\n'
        self.assertEqual(parse(text), {"open": "enter", "unpin": "alt-x", "keep": "alt-k"})
        km = Keymap(parse('unknown = "x"\nopen = ""'))
        self.assertEqual(km.key("open"), "")
        self.assertNotIn("unknown", km.keys)
