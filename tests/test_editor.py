import json
import re
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
        """Rows without the section gutter (``identity   title *  …`` → ``title *  …``)."""
        import re
        return [re.sub(r"^(?:\S+)?\s{2,}", "", re.sub(r"\x1b\[[0-9;]*m", "", l).split("\t")[1]) for l in call["lines"]]

    def test_layout_and_dirty_marks(self):
        self.steps({"key": "", "select": ["keep"]}, {"abort": True}, {"key": "", "select": ["discard"]})
        r = self.run_pin("edit", "standup-prep")
        calls = self.fzf_calls()
        raw = [re.sub(r"\x1b\[[0-9;]*m", "", l).split("\t")[1] for l in calls[0]["lines"]]
        self.assertTrue(raw[0].startswith("identity   title *      Standup prep"))   # section name in the gutter
        self.assertTrue(raw[1].startswith("           alias        standup-prep"))   # blank for the rest of it
        self.assertTrue(raw[3].startswith("location   cwd "))
        self.assertFalse(any(l.startswith("-\t") for l in calls[0]["lines"]))       # every row is a field
        rows = self.fields(calls[0])
        self.assertTrue(rows[0].startswith("title *      Standup prep"))
        self.assertTrue(rows[4].startswith("worktree     off"))
        self.assertIn("open in a fresh worktree each time", rows[4])
        self.assertTrue(rows[5].startswith("model        (default)"))
        self.assertIn("claude's default model", rows[5])
        self.assertTrue(rows[7].startswith("permission   (default)"))
        self.assertEqual(rows[-2], "Done"); self.assertEqual(rows[-1], "Cancel")
        self.assertEqual(self.arg(calls[0], "--prompt"), "📌 pins › standup-prep › edit › ")
        self.assertEqual(self.arg(calls[0], "--header"), "enter change · alt-s save · esc back\n ")
        # after toggling keep the form is dirty: breadcrumb + star
        self.assertEqual(self.arg(calls[1], "--prompt"), "📌 pins › standup-prep › edit (unsaved) › ")
        self.assertTrue(self.fields(calls[1])[8].startswith("keep        *ON"))
        self.assertIn("start:pos(9)", " ".join(calls[1]["argv"]))                    # cursor stays on keep
        draft = self.arg(calls[0], "--preview")
        self.assertRegex(draft, r" _preview --draft \S+pin-draft-\S+\.json$")        # the pane renders the draft
        self.assertEqual(self.arg(calls[0], "--preview-label"), " draft ")
        self.assertFalse(os.path.exists(draft.split()[-1]))                          # and the file is gone on exit
        ask = calls[2]                                                                # esc with changes: a list
        self.assertEqual(self.arg(ask, "--prompt"), "📌 pins › standup-prep › edit › unsaved › ")
        self.assertIn("save changes?", self.arg(ask, "--header"))
        self.assertEqual([l.split("\t")[1] for l in ask["lines"]], ["save", "discard", "keep editing"])
        self.assertFalse(self.stored()["standup-prep"]["keep"])  # discarded
        self.assertIn("no changes", r.stdout)

    def test_dirty_esc_saves_on_enter(self):
        self.steps({"key": "", "select": ["fork"]}, {"abort": True}, {"key": "", "select": ["save"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertTrue(self.stored()["standup-prep"]["fork"])
        self.assertIn("✓ saved standup-prep", r.stdout)

    def test_choice_field_and_clear(self):
        self.steps({"key": "", "select": ["permission"]}, {"key": "", "select": ["plan"]},
                   {"key": "", "select": ["effort"]}, {"key": "", "select": ["(clear)"]},
                   {"key": "", "select": ["done"]})
        r = self.run_pin("edit", "standup-prep")
        calls = self.fzf_calls()
        self.assertEqual(self.arg(calls[1], "--prompt"), "📌 pins › standup-prep › edit › permission › ")
        self.assertEqual([l.split("\t")[1] for l in calls[1]["lines"]], ["default", "acceptEdits", "plan", "auto", "bypassPermissions", "(clear)"])
        self.assertEqual(self.stored()["standup-prep"]["launch"], {"permission_mode": "plan"})

    def test_rename_and_alias_taken(self):
        self.make_session(SID2, title="Other"); self.run_pin("add", SID2, "other")
        self.steps({"key": "", "select": ["alias"]}, {"query": "other"}, {"key": "alt-s"},
                   {"key": "", "select": ["alias"]}, {"query": "sp"}, {"key": "alt-s"})
        r = self.run_pin("edit", "standup-prep")
        calls = self.fzf_calls()
        self.assertEqual(self.arg(calls[1], "--prompt"), "📌 pins › standup-prep › edit › alias › ")
        self.assertEqual(self.arg(calls[1], "--query"), "standup-prep")
        self.assertIn("✗ alias other is taken", self.arg(calls[3], "--header"))     # the form says why it stayed
        self.assertIn("sp", self.stored()); self.assertNotIn("standup-prep", self.stored())
        self.assertEqual(r.stdout.strip(), "✓ saved sp · session named 📌 sp")
        self.assertEqual(self.stored()["sp"]["prior_title"], "")

    def test_cancel_row(self):
        self.steps({"key": "", "select": ["note"]}, {"query": "a note"}, {"key": "", "select": ["cancel"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertEqual(self.stored()["standup-prep"]["note"], "")

    def arg(self, call, flag):
        a = call["argv"]
        return a[a.index(flag) + 1] if flag in a else None


class EditorEdgeTests(EditorTests):
    def test_dir_model_and_text_fields(self):
        (self.home / "git" / "cc").mkdir(parents=True)
        self.steps({"key": "", "select": ["cwd"]}, {"query": "~/git/c", "raw": ["~/git/cc/\t~/git/cc/"]},   # a reloaded row
                   {"key": "", "select": ["model"]}, {"key": "", "select": ["(type a model name…)"]}, {"query": "claude-x"},
                   {"key": "", "select": ["model"]}, {"key": "", "select": ["sonnet"]},
                   {"key": "", "select": ["note"]}, {"query": "a note"}, {"key": "", "select": ["done"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.stored()["standup-prep"]
        self.assertEqual((p["cwd"], p["launch"], p["note"]), (str(self.home / "git" / "cc"), {"model": "sonnet"}, "a note"))
        calls = self.fzf_calls()
        dirs = calls[1]                                                   # the directory field: query line over completions
        self.assertEqual(self.arg(dirs, "--prompt"), "📌 pins › standup-prep › edit › cwd › ")
        self.assertEqual(self.arg(dirs, "--query"), str(self.home / "git" / "proj"))
        self.assertIn("--disabled", dirs["argv"])
        self.assertTrue(any(b.startswith("change:reload(") and b.endswith(" _dirs {q})") for b in self.binds(dirs)))
        self.assertEqual([l.split("\t")[0] for l in dirs["lines"]], [str(self.home / "git" / "proj") + "/"])
        self.assertEqual(self.arg(calls[3], "--prompt"), "📌 pins › standup-prep › edit › model › ")
        self.assertEqual([l.split("\t")[1] for l in calls[3]["lines"]][:2], ["fable", "opus"])
        self.assertEqual(self.arg(calls[4], "--prompt"), "📌 pins › standup-prep › edit › model › ")
        self.assertIsNone(self.arg(calls[4], "--query"))                   # nothing to prefill: no --query
        self.assertIn("--disabled", calls[8]["argv"])                       # note: the same query-line screen
        self.fzf_log.unlink()                                               # typed text wins when nothing is listed
        self.steps({"key": "", "select": ["cwd"]}, {"query": "/no/such/place"}, {"key": "", "select": ["done"]})
        self.run_pin("edit", "standup-prep")
        self.assertEqual(self.stored()["standup-prep"]["cwd"], "/no/such/place")

    def binds(self, call):
        a = call["argv"]
        return [a[i + 1] for i, x in enumerate(a) if x == "--bind"]

    def test_cancel_paths(self):
        # choice screen aborted, field prompt cancelled with EOF, then esc with nothing dirty
        self.steps({"key": "", "select": ["effort"]}, {"abort": True}, {"key": "", "select": ["title"]}, {"abort": True},
                   {"abort": True})
        r = self.run_pin("edit", "standup-prep")
        self.assertIn("no changes", r.stdout)
        # dirty + esc: 'keep editing' goes back to the form, then done with the change kept
        self.steps({"key": "", "select": ["keep"]}, {"abort": True}, {"key": "", "select": ["keep editing"]},
                   {"key": "", "select": ["done"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertTrue(self.stored()["standup-prep"]["keep"])
        # dirty + esc + esc at the question: nothing saved
        self.steps({"key": "", "select": ["fork"]}, {"abort": True}, {"abort": True})
        r = self.run_pin("edit", "standup-prep")
        self.assertFalse(self.stored()["standup-prep"]["fork"])
        # done with nothing dirty is a no-op; a header row selection is ignored; save failure keeps editing
        self.steps({"key": "", "select": ["-"]}, {"key": "", "select": ["done"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertIn("no changes", r.stdout)
        self.fzf_log.unlink()
        self.steps({"key": "", "select": ["title"]}, {"query": ""}, {"key": "alt-s"}, {"key": "", "select": ["cancel"]})
        r = self.run_pin("edit", "standup-prep")
        self.assertIn("✗ title is required", self.arg(self.fzf_calls()[3], "--header"))
        self.assertEqual(self.stored()["standup-prep"]["title"], "Standup prep")
