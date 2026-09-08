"""The store operations behind the picker's keys, tested apart from any screen."""

from claude_pins import actions
from claude_pins.listing import build_views
from claude_pins.model import Pin, PinError
from claude_pins.render import View
from claude_pins.sessions import Expiry
from claude_pins.store import Store
from claude_pins.transcript import read_summary
from tests.helpers import Sandbox


class ActionTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.t1 = self.make_session("11111111-1111-1111-1111-111111111111", title="Standup prep", age_days=2)
        self.t2 = self.make_session("22222222-2222-2222-2222-222222222222", title="Mower", age_days=1)
        self.store = Store(self.store_path()).load()
        self.store.add(Pin(alias="standup", session_id="11111111-1111-1111-1111-111111111111", title="Standup prep",
                           cwd=str(self.home / "git" / "proj"), transcript=str(self.t1)))
        self.store.add(Pin(alias="mower", session_id="22222222-2222-2222-2222-222222222222", title="Mower",
                           cwd=str(self.home / "git" / "proj"), transcript=str(self.t2), keep=True))
        self.store.save()

    def views(self, **kw):
        return build_views(self.store, include_expired=True, with_summary=False, open_ids=set(), **kw)[0]

    def reload(self) -> Store:
        return Store(self.store.path).load()

    def test_toggle_follows_the_first_selected_pin(self):
        views = self.views(sort="alias")                     # mower (keep on), standup (keep off)
        out = actions.toggle(self.store, views, "keep")
        self.assertEqual(out.flash, "✓ keep off for mower, standup")
        self.assertEqual([p.keep for p in self.reload().pins], [False, False])
        out = actions.toggle(self.store, views[1:], "fork_mode")
        self.assertEqual(out.flash, "✓ fork on for standup")
        self.assertTrue(self.reload().require("standup").fork)
        self.assertIsNone(out.cursor)

    def test_touch_reports_what_it_could_touch(self):
        views = self.views(sort="alias")
        self.age(self.t1, 5)
        self.assertEqual(actions.touch(views).flash, "✓ touched mower, standup")
        self.assertLess(abs(self.t1.stat().st_mtime - self.t2.stat().st_mtime), 5)
        self.t1.unlink()
        self.assertEqual(actions.touch(self.views(sort="alias")[1:]).flash, "✗ nothing to touch (transcripts gone)")

    def test_unpin_undo_and_the_cursor(self):
        views = self.views(sort="alias")
        out = actions.unpin(self.store, views[:1], "alt-z")
        self.assertEqual((out.flash, out.cursor), ("✓ unpinned mower · alt-z undo", ""))
        self.assertEqual(self.reload().aliases(), ["standup"])
        out = actions.undo(self.store)
        self.assertEqual((out.flash, out.cursor), ("✓ restored mower (unpin) · session named 📌 mower", "mower"))
        self.assertEqual(sorted(self.reload().aliases()), ["mower", "standup"])
        out = actions.undo(self.store)
        self.assertEqual((out.flash, out.cursor), ("nothing to undo", None))

    def test_prune_asks_once_and_only_when_there_is_something(self):
        asked: list[list[str]] = []
        out = actions.prune(self.store, self.views(), lambda names: asked.append(names) or True, "z")
        self.assertEqual((out.flash, asked), ("nothing to prune", []))
        self.t2.unlink()
        out = actions.prune(self.store, self.views(), lambda names: asked.append(names) or False, "z")
        self.assertEqual((out.flash, asked), ("prune cancelled", [["mower"]]))
        self.assertEqual(sorted(self.reload().aliases()), ["mower", "standup"])
        out = actions.prune(self.store, self.views(), lambda names: True, "z")
        self.assertEqual(out.flash, "✓ pruned 1 · z undo")
        self.assertEqual(self.reload().aliases(), ["standup"])
        self.assertEqual(actions.undo(self.store).flash, "✓ restored mower (prune)")

    def test_open_plan_refuses_expired_pins_with_the_reason(self):
        views = self.views(sort="alias")
        # a view that still carries a summary (read before the sweep took the file) gets the short form
        stale = View(views[0].pin, Expiry("expired", 0.0, 0.0), summary=read_summary(self.t2))
        plan, flash = actions.open_plan(self.store, stale, "open")
        self.assertEqual((plan, flash), (None, "✗ mower has expired · unpin it or pin prune"))
        self.t2.unlink()
        gone = self.views(sort="alias")[0]
        self.assertTrue(gone.expiry.expired)
        plan, flash = actions.open_plan(self.store, gone, "open")
        self.assertEqual((plan, flash), (None, "✗ mower: transcript for session 22222222… is gone (expired) · pin unpin mower"))
        plan, flash = actions.open_plan(self.store, views[1], "open_fork")
        self.assertEqual(flash, "")
        self.assertIn("--fork-session", plan.argv)

    def test_pin_session_and_sort(self):
        t3 = self.make_session("33333333-3333-3333-3333-333333333333", title="Third")
        out = actions.pin_session(self.store, read_summary(t3), "third")
        self.assertEqual((out.flash, out.cursor), ("✓ pinned as third · session named 📌 third", "third"))
        self.assertEqual(self.reload().require("third").session_id, "33333333-3333-3333-3333-333333333333")
        with self.assertRaises(PinError):
            actions.pin_session(self.store, read_summary(t3), "another")      # the session is already pinned
        with self.assertRaises(PinError):
            actions.pin_session(self.store, read_summary(self.t1), "third")   # the alias is taken
        self.assertEqual(actions.cycle_sort("recency"), ("alias", actions.Outcome("sort: alias")))
