import json
import os

from tests.helpers import FzfSandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"


class EditorTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        self.make_session(SID1, age_days=2, title="Standup prep")
        self.run_pin("add", SID1, "standup-prep")

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    def fields(self, call):
        import re
        return [re.sub(r"\x1b\[[0-9;]*m", "", l).split("\t")[2] for l in call["lines"]]

    def test_layout_and_dirty_marks(self):
        self.steps({"key": "", "select": ["keep"]}, {"abort": True})
        r = self.run_pin("edit", "standup-prep", input="n\n")
        calls = self.fzf_calls()
        rows = self.fields(calls[0])
        self.assertEqual(rows[0], "── identity ──")
        self.assertTrue(rows[1].startswith("title *      Standup prep"))
        self.assertTrue(rows[6].startswith("worktree     off"))
        self.assertIn("open in a fresh worktree each time", rows[6])
        self.assertTrue(rows[8].startswith("model        (default)"))
        self.assertIn("claude's default model", rows[8])
        self.assertTrue(rows[10].startswith("permission   (default)"))
        self.assertEqual(rows[-2], "Done"); self.assertEqual(rows[-1], "Cancel")
        self.assertEqual(self.arg(calls[0], "--prompt"), "pins › standup-prep › edit › ")
        # after toggling keep the form is dirty: breadcrumb + star
        self.assertEqual(self.arg(calls[1], "--prompt"), "pins › standup-prep › edit (unsaved) › ")
        self.assertTrue(self.fields(calls[1])[12].startswith("keep        *ON"))
        self.assertIn("save changes? [Y/n/c]", r.stdout)
        self.assertFalse(self.stored()["standup-prep"]["keep"])  # answered n
        self.assertIn("no changes", r.stdout)

    def test_dirty_esc_saves_on_enter(self):
        self.steps({"key": "", "select": ["fork"]}, {"abort": True})
        r = self.run_pin("edit", "standup-prep", input="\n")
        self.assertTrue(self.stored()["standup-prep"]["fork"])
        self.assertIn("✓ saved standup-prep", r.stdout)

    def test_choice_field_and_clear(self):
        self.steps({"key": "", "select": ["permission"]}, {"key": "", "select": ["plan"]},
                   {"key": "", "select": ["effort"]}, {"key": "", "select": ["(clear)"]},
                   {"key": "", "select": ["done"]})
        r = self.run_pin("edit", "standup-prep")
        calls = self.fzf_calls()
        self.assertEqual(self.arg(calls[1], "--prompt"), "pins › standup-prep › edit › permission › ")
        self.assertEqual([l.split("\t")[2] for l in calls[1]["lines"]], ["default", "acceptEdits", "plan", "auto", "bypassPermissions", "(clear)"])
        self.assertEqual(self.stored()["standup-prep"]["launch"], {"permission_mode": "plan"})

    def test_rename_and_alias_taken(self):
        self.make_session(SID2, title="Other"); self.run_pin("add", SID2, "other")
        self.steps({"key": "", "select": ["alias"]}, {"key": "alt-s"}, {"key": "", "select": ["alias"]}, {"key": "alt-s"})
        r = self.run_pin("edit", "standup-prep", input="other\nsp\n")
        self.assertIn("✗ alias other is taken", r.stdout)
        self.assertIn("sp", self.stored()); self.assertNotIn("standup-prep", self.stored())

    def test_cancel_row(self):
        self.steps({"key": "", "select": ["note"]}, {"key": "", "select": ["cancel"]})
        r = self.run_pin("edit", "standup-prep", input="a note\n")
        self.assertEqual(self.stored()["standup-prep"]["note"], "")

    def arg(self, call, flag):
        a = call["argv"]
        return a[a.index(flag) + 1] if flag in a else None


class MenuTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.t2 = self.make_session(SID2, cwd=str(self.home), age_days=26, title="Navimow schedule debug")
        self.run_pin("add", SID1, "standup-prep"); self.run_pin("add", SID2, "rc-mower")

    def test_menu_render_and_open(self):
        r = self.run_pin(input="2\n")
        self.assertRegex(r.stdout, r"pins\s+\(install fzf ≥ 0.44 for the full picker: pin doctor\)")
        self.assertRegex(r.stdout, r"1  standup-prep\s+Standup prep\s+~/git/proj\s+2d")
        self.assertRegex(r.stdout, r"2  rc-mower\s+Navimow schedule debug\s+~\s+26d\s+⏳")
        self.assertIn("N open · oN fork · wN worktree · tN touch · eN edit · xN unpin · pN preview", r.stdout)
        self.assertIn("n new · a show expired · p prune · z undo · s sort · ? help · q quit", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2])

    def test_menu_letter_actions(self):
        r = self.run_pin(input="t2\nx1\nz\np\n?\nq\n")
        self.assertIn("✓ touched rc-mower", r.stdout)
        self.assertIn("✓ unpinned rc-mower · z undo", r.stdout)  # touched → newest → row 1
        self.assertIn("✓ restored rc-mower (unpin)", r.stdout)
        self.assertIn("nothing to prune", r.stdout)
        self.assertIn("● open  ⚑ keep", r.stdout)
        self.assertIsNone(self.claude_calls())

    def test_menu_preview_and_fork(self):
        r = self.run_pin(input="p1\no1\n")
        self.assertIn("dir       ~/git/proj", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--fork-session"])

    def test_menu_bad_input(self):
        r = self.run_pin(input="9\nzz\nq\n")
        self.assertIn("no row 9", r.stdout)
        self.assertIn("unknown command 'zz'", r.stdout)

    def test_old_fzf_reason(self):
        os.environ.pop("CLAUDE_PINS_NO_FZF")
        self.stub("fzf", "#!/bin/sh\necho '0.38.0 (old)'\n")
        r = self.run_pin(input="q\n", env={"CLAUDE_PINS_FZF": str(self.bindir / "fzf")})
        self.assertIn("(fzf 0.38.0 is too old)", r.stdout)
