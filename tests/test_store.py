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

    def test_prior_title_round_trips_and_old_stores_load(self):
        s = Store().load()
        s.add(pin("one", prior_title="Their name"))
        s.unpin("one")
        s.save()
        s2 = Store().load()
        self.assertEqual(s2.restore_last()[1][0].prior_title, "Their name")     # kept through the tombstone
        s2.save()
        self.assertEqual(Store().load().pins[0].prior_title, "Their name")
        data = json.loads(self.store_path().read_text())
        del data["pins"][0]["prior_title"]                                      # a store from before 0.7.0
        self.store_path().write_text(json.dumps(data))
        self.assertEqual(Store().load().pins[0].prior_title, "")

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

    def test_hand_edited_undo_entries(self):
        """Junk in the undo list is dropped on load; an entry with an empty pin restores nothing."""
        s = Store().load()
        s.add(pin("a")); s.unpin("a"); s.save()
        data = json.loads(self.store_path().read_text())
        data["undo"] = ["oops", {"kind": "unpin"}, {"kind": "unpin", "pins": "no"}, {"kind": "unpin", "pins": [1]},
                        {"kind": "prune", "pins": [{}]}] + data["undo"]
        self.store_path().write_text(json.dumps(data))
        s = Store().load()
        self.assertEqual(len(s.undo), 2)
        kind, restored = s.restore_last()
        self.assertEqual((kind, [p.alias for p in restored]), ("unpin", ["a"]))
        kind, restored = s.restore_last()
        self.assertEqual((kind, restored), ("prune", []))
        s.undo = "nonsense"  # type: ignore[assignment]
        s.save()
        self.assertEqual(Store().load().undo, [])

    def test_unreadable_store(self):
        self.store_path().parent.mkdir(parents=True)
        self.store_path().mkdir()  # a directory where the file should be: OSError, not corruption
        with self.assertRaisesRegex(PinError, "cannot read"):
            Store().load()
        self.assertFalse(self.store_path().with_suffix(".json.bak").exists())

    def test_rename_rules(self):
        s = Store().load()
        s.add(pin("a")); s.add(pin("b"))
        with self.assertRaisesRegex(PinError, "alias b is taken; try b-2"):
            s.rename("a", "b")
        self.assertIs(s.rename("a", "a"), s.get("a"))
        s.rename("a", "c")
        self.assertEqual(s.aliases(), ["c", "b"])

    def test_unwritable_store_directory(self):
        d = self.store_path().parent
        d.mkdir(parents=True); d.chmod(0o500)
        try:
            s = Store().load(); s.add(pin("a"))
            with self.assertRaisesRegex(PinError, "cannot write .*pins.json"):
                s.save()
        finally:
            d.chmod(0o700)
        self.assertEqual(list(d.iterdir()), [])  # no temp file left behind
        # the replace itself failing (a directory sits where the file goes) is the same one-liner
        self.store_path().mkdir()
        with self.assertRaisesRegex(PinError, "cannot write"):
            s.save()
        self.assertEqual([p.name for p in d.iterdir()], ["pins.json"])

    def test_undo_skips_sessions_pinned_again(self):
        s = Store().load()
        s.add(pin("a", "11111111-1111-1111-1111-111111111111")); s.add(pin("b", "22222222-2222-2222-2222-222222222222"))
        s.unpin_many(["a", "b"], kind="prune")
        s.add(pin("a-again", "11111111-1111-1111-1111-111111111111"))  # re-pinned under another alias meanwhile
        kind, restored = s.restore_last()
        self.assertEqual((kind, [p.alias for p in restored]), ("prune", ["b"]))
        self.assertEqual(s.aliases(), ["a-again", "b"])
