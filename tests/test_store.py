import json
from pathlib import Path

from claude_pins.model import Pin, PinError, kebab, next_free_alias, validate_alias
from claude_pins.store import Store, UNDO_CAP
from tests.helpers import Sandbox


def pin(alias, sid=None, **kw):
    return Pin(alias=alias, session_id=sid or f"{alias}-0000-0000-0000-000000000000", **kw)


class ModelTests(Sandbox):
    def test_alias_validation(self):
        self.assertEqual(validate_alias(" standup-prep "), "standup-prep")
        for bad in ("", "Standup", "a--b", "-a", "a b", "a_b"):
            with self.assertRaises(PinError):
                validate_alias(bad)

    def test_kebab(self):
        self.assertEqual(kebab("Pin Claude sessions"), "pin-claude-sessions")
        self.assertEqual(kebab("USAA restructure!!"), "usaa-restructure")
        self.assertEqual(kebab("   "), "pin")
        self.assertLessEqual(len(kebab("word " * 30)), 40)

    def test_next_free_alias(self):
        self.assertEqual(next_free_alias("a", ["b"]), "a")
        self.assertEqual(next_free_alias("a", ["a"]), "a-2")
        self.assertEqual(next_free_alias("a", ["a", "a-2"]), "a-3")
        self.assertEqual(next_free_alias("a-2", ["a", "a-2"]), "a-3")


class StoreTests(Sandbox):
    def test_roundtrip_and_atomic(self):
        s = Store().load()
        self.assertEqual(s.pins, [])
        s.add(pin("one", title="One", keep=True))
        s.save()
        self.assertTrue(self.store_path().exists())
        self.assertFalse(list(self.store_path().parent.glob(".pins-*")))  # no temp left behind
        s2 = Store().load()
        self.assertEqual(s2.pins[0].title, "One")
        self.assertTrue(s2.pins[0].keep)
        self.assertTrue(s2.pins[0].pinned_at)
        self.assertEqual(oct(self.store_path().stat().st_mode & 0o777), "0o600")

    def test_uniqueness(self):
        s = Store().load()
        s.add(pin("one", "11111111-1111-1111-1111-111111111111", title="First"))
        with self.assertRaisesRegex(PinError, "already pinned as one"):
            s.add(pin("two", "11111111-1111-1111-1111-111111111111"))
        with self.assertRaisesRegex(PinError, "taken.*try one-2"):
            s.add(pin("one", "22222222-2222-2222-2222-222222222222"))

    def test_corrupt_recovery(self):
        self.store_path().parent.mkdir(parents=True)
        self.store_path().write_text("{not json")
        with self.assertRaisesRegex(PinError, "corrupt"):
            Store().load()
        bak = self.store_path().with_suffix(".json.bak")
        self.assertEqual(bak.read_text(), "{not json")  # kept a copy
        self.assertEqual(self.store_path().read_text(), "{not json")  # never clobbered
        # a later good store does not overwrite the existing .bak
        self.store_path().write_text("[1,2]")
        with self.assertRaises(PinError):
            Store().load()
        self.assertEqual(bak.read_text(), "{not json")

    def test_unpin_undo_tombstones(self):
        s = Store().load()
        s.add(pin("a")); s.add(pin("b")); s.add(pin("c"))
        s.unpin("a")
        self.assertEqual(s.aliases(), ["b", "c"])
        self.assertEqual(s.last_undo()["kind"], "unpin")
        s.unpin_many(["b", "c"], kind="prune")
        s.save()
        s = Store().load()
        kind, restored = s.restore_last()
        self.assertEqual(kind, "prune")
        self.assertEqual(sorted(p.alias for p in restored), ["b", "c"])
        kind, restored = s.restore_last()
        self.assertEqual([p.alias for p in restored], ["a"])
        with self.assertRaisesRegex(PinError, "nothing to undo"):
            s.restore_last()

    def test_undo_alias_collision_after_restore(self):
        s = Store().load()
        s.add(pin("a", "11111111-1111-1111-1111-111111111111"))
        s.unpin("a")
        s.add(pin("a", "22222222-2222-2222-2222-222222222222"))
        _, restored = s.restore_last()
        self.assertEqual(restored[0].alias, "a-2")

    def test_undo_cap(self):
        s = Store().load()
        for i in range(UNDO_CAP + 5):
            s.add(pin(f"p{i}", f"{i:08d}-0000-0000-0000-000000000000"))
            s.unpin(f"p{i}")
        s.save()
        self.assertEqual(len(json.loads(self.store_path().read_text())["undo"]), UNDO_CAP)

    def test_missing_pin(self):
        s = Store().load()
        with self.assertRaisesRegex(PinError, "no pin named x"):
            s.unpin("x")
