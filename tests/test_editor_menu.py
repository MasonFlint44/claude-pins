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

    def test_cancel_row(self):
        self.steps({"key": "", "select": ["note"]}, {"query": "a note"}, {"key": "", "select": ["cancel"]})
        r = self.run_pin("edit", "standup-prep")
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
        self.assertRegex(r.stdout, r"\n    alias\s+title\s+directory\s+idle\n 1  standup-prep")   # labels over the numbers
        self.assertRegex(r.stdout, r"1  standup-prep\s+Standup prep\s+~/git/proj\s+2d")
        self.assertRegex(r.stdout, r"2  rc-mower\s+Navimow schedule debug\s+~\s+26d\s+⏳\n 🟢 open  🚩 keep")   # legend under the table
        self.assertIn("N open · oN fork · wN worktree · tN touch · eN edit · xN unpin · pN preview", r.stdout)
        self.assertIn("n new · a show expired · p prune · z undo · s sort · ? help · q quit", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2])

    def test_menu_letter_actions(self):
        r = self.run_pin(input="t2\nx1\nz\np\n?\nq\n")
        self.assertIn("✓ touched rc-mower", r.stdout)
        self.assertIn("✓ unpinned rc-mower · z undo", r.stdout)  # touched → newest → row 1
        self.assertIn("✓ restored rc-mower (unpin)", r.stdout)
        self.assertIn("nothing to prune", r.stdout)
        self.assertIn("🟢 open  🚩 keep", r.stdout)
        self.assertIsNone(self.claude_calls())
        r = self.run_pin(input="?\nq\n", env={"CLAUDE_PINS_GLYPHS": "text"})    # the menu shares the glyph switch
        self.assertIn("● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⧗ expiring  ✗ expired", r.stdout)
        self.assertNotIn("🟢", r.stdout)

    def test_menu_preview_and_fork(self):
        r = self.run_pin(input="p1\no1\n")
        self.assertIn("dir        ~/git/proj", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--fork-session"])

    def test_menu_bad_input(self):
        r = self.run_pin(input="9\nzz\nq\n")
        self.assertIn("no row 9", r.stdout)
        self.assertIn("unknown command 'zz'", r.stdout)

    def test_menu_new_pin_and_sort(self):
        sid3 = "33333333-3333-3333-3333-333333333333"
        self.make_session(sid3, cwd=str(self.home / "Documents"), age_days=0.2, title="Tax prep questions")
        r = self.run_pin(input="n\n1\n\ns\na\nq\n")
        self.assertIn("pins › new", r.stdout)
        self.assertRegex(r.stdout, r"pins › new\n      title\s+directory\s+idle\s+msgs\s+pin\n   1  Tax prep questions\s+~/Documents")
        self.assertRegex(r.stdout, r"2  Standup prep\s+.*📌 standup-prep\n")
        self.assertIn("✓ pinned as tax-prep-questions", r.stdout)
        self.assertIn("sort: alias", r.stdout)
        self.assertIn("tax-prep-questions", {p["alias"] for p in json.loads(self.store_path().read_text())["pins"]})

    def test_menu_edit_without_fzf(self):
        r = self.run_pin(input="e1\n1\nStandup prep (Tue)\n9\n8\n3\ns\nq\n")
        self.assertIn("pins › standup-prep › edit", r.stdout)
        self.assertIn("N change field · s save · q cancel", r.stdout)
        self.assertIn("✓ saved standup-prep", r.stdout)
        pins = {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}
        self.assertEqual(pins["standup-prep"]["title"], "Standup prep (Tue)")
        self.assertTrue(pins["standup-prep"]["keep"])
        self.assertEqual(pins["standup-prep"]["launch"], {"permission_mode": "plan"})
        r = self.run_pin("edit", "standup-prep", input="10\nq\n")
        self.assertIn("no changes", r.stdout)

    def test_menu_prune_and_expired(self):
        self.t2.unlink()
        r = self.run_pin(input="a\np\ny\nz\nq\n")
        self.assertIn("1 expired · a show · p prune", r.stdout)
        self.assertRegex(r.stdout, r"2  rc-mower\s+Navimow schedule debug\s+~\s+🔴")
        self.assertIn("✓ pruned 1 · z undo", r.stdout)
        self.assertIn("✓ restored rc-mower (prune)", r.stdout)

    def test_menu_worktree_and_letter_without_number(self):
        r = self.run_pin(input="t\nw1\n")
        self.assertIn("t needs a row number (e.g. t1)", r.stdout)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--worktree"])

    def test_old_fzf_reason(self):
        os.environ.pop("CLAUDE_PINS_NO_FZF")
        self.stub("fzf", "#!/bin/sh\necho '0.38.0 (old)'\n")
        r = self.run_pin(input="q\n", env={"CLAUDE_PINS_FZF": str(self.bindir / "fzf")})
        self.assertIn("(fzf 0.38.0 is too old)", r.stdout)

    def test_menu_empty_query_and_missing_rows(self):
        sid3 = "33333333-3333-3333-3333-333333333333"
        self.make_session(sid3, age_days=0.1, title="Tax"); self.run_pin("add", sid3, "tax")
        r = self.run_pin("e", input="q\n")  # several hits: the menu opens filtered
        self.assertIn("pins › e", r.stdout)
        self.assertRegex(r.stdout, r"1  standup-prep\s+Standup prep")
        self.assertRegex(r.stdout, r"2  rc-mower\s+Navimow schedule debug")
        self.assertNotIn("tax", r.stdout)
        self.run_pin("rm", "tax")
        self.run_pin("rm", "standup-prep"); self.run_pin("rm", "rc-mower")
        r = self.run_pin(input="q\n")
        self.assertIn("No pins yet. n pins a recent session, or run /pins:pin inside a Claude session.", r.stdout)

    def test_menu_expired_open_and_errors(self):
        self.t2.unlink()
        r = self.run_pin(input="a\n2\nx9\nq\n")
        # the same flash the fzf picker shows (claude_pins.actions), not a shorter menu-only one
        self.assertIn("✗ rc-mower: transcript for session 22222222… is gone (expired) · pin unpin rc-mower", r.stdout)
        self.assertIn("no row 9", r.stdout)
        self.assertIsNone(self.claude_calls())
        r = self.run_pin(input="z\nq\n")
        self.assertIn("nothing to undo", r.stdout)
        r = self.run_pin(input="y1\nq\n")
        self.assertIn("unknown row action 'y'", r.stdout)
        r = self.run_pin(input="p\nn\nq\n")  # decline the prune
        self.assertNotIn("✓ pruned", r.stdout); self.assertEqual(self.run_pin("_complete").stdout.split(), ["standup-prep", "rc-mower"])
        r = self.run_pin(input="p\n")  # EOF at the prune question, then at the menu
        self.assertEqual(r.returncode, 0)

    def test_menu_open_cancelled_and_gone(self):
        import shutil
        shutil.rmtree(self.home / "git" / "proj")
        r = self.run_pin(input="1\n3\nq\n")  # missing dir → unpin
        self.assertIn("✓ unpinned standup-prep", r.stdout); self.assertIn("cancelled", r.stdout)

    def test_menu_new_pin_edge_cases(self):
        r = self.run_pin(input="n\n\nq\n")  # enter cancels
        self.assertNotIn("✓ pinned", r.stdout)
        r = self.run_pin(input="n\n1\nq\n")  # row 1 is the newest session, already pinned
        self.assertIn("already pinned as standup-prep", r.stdout)
        sid3 = "33333333-3333-3333-3333-333333333333"
        self.make_session(sid3, age_days=0.1, title="Fresh")
        r = self.run_pin(input="n\n1\nrc-mower\nq\n")  # taken alias → error flash
        self.assertIn("✗ alias rc-mower is taken", r.stdout)
        r = self.run_pin(input="n\n1\n")  # EOF at the alias prompt
        self.assertEqual(r.returncode, 0); self.assertNotIn("✓ pinned", r.stdout)
        r = self.run_pin(input="n\n")  # EOF at the session number
        self.assertEqual(r.returncode, 0)
        self.t1.unlink(); self.t2.unlink(); (self.project_dir(str(self.home / "git" / "proj")) / f"{sid3}.jsonl").unlink()
        r = self.run_pin(input="n\nq\n")
        self.assertIn("no sessions found", r.stdout)

    def test_plain_editor_fields(self):
        # cwd (4), model as free text (6), effort by number (7 → 2), clear it again (7 → 5), keep (9), save
        r = self.run_pin("edit", "standup-prep", input="4\n~/git/cc\n6\nclaude-opus-5\n7\n2\n7\n5\n9\ns\n")
        self.assertIn("✓ saved standup-prep", r.stdout)
        self.assertIn("📌 pins › standup-prep › edit (unsaved)", r.stdout)               # the plain form shares the crumb
        self.assertRegex(r.stdout, r"identity    1  title \*      Standup prep")           # and the gutter grouping
        self.assertRegex(r.stdout, r"\n              2  alias")
        self.assertIn("  1) low", r.stdout); self.assertIn("  5) (clear)", r.stdout)     # numbered choices
        p = self.stored()["standup-prep"]
        self.assertEqual(p["cwd"], str(self.home / "git" / "cc"))
        self.assertEqual(p["launch"], {"model": "claude-opus-5"})
        self.assertTrue(p["keep"])
        r = self.run_pin("edit", "standup-prep", input="2\nBAD\ns\n3\n")  # invalid alias refused, EOF cancels
        self.assertIn("✗ invalid alias 'BAD'", r.stdout); self.assertIn("standup-prep", self.stored())
        from claude_pins.prompt import prefills
        import readline
        r = self.run_pin("edit", "standup-prep", input="1\n\ns\nq\n")
        if prefills(readline):
            self.assertIn("✗ title is required", r.stdout)                  # an empty answer empties the field
        else:
            self.assertIn('title: enter keeps "Standup prep", c clears', r.stdout)   # libedit: bare enter keeps the value
            self.assertIn("✓ saved standup-prep", r.stdout)
            r = self.run_pin("edit", "standup-prep", input="1\nc\ns\nq\n")
            self.assertIn("✗ title is required", r.stdout)                    # and c empties it
        r = self.run_pin("edit", "standup-prep", input="2\nrc-mower\ns\nq\n")
        self.assertIn("✗ alias rc-mower is taken", r.stdout)
        r = self.run_pin("edit", "standup-prep", input="99\nabc\nq\n")  # ignored inputs
        self.assertIn("no changes", r.stdout)

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}


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


class MenuRaceTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.make_session(SID2, cwd=str(self.home), age_days=26, title="Navimow schedule debug")
        self.run_pin("add", SID1, "standup-prep"); self.run_pin("add", SID2, "rc-mower")

    def test_menu_transcript_swept_between_draw_and_open(self):
        """The menu is drawn, Claude's retention sweep deletes the transcript, then the user opens the row."""
        import subprocess, sys
        from tests.helpers import PIN
        proc = subprocess.Popen([sys.executable, str(PIN)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=dict(os.environ))

        def read_until(marker):
            buf = ""
            while not buf.endswith(marker):
                ch = proc.stdout.read(1)
                self.assertTrue(ch, f"pin exited before {marker!r}; got {buf!r}")
                buf += ch
            return buf
        first = read_until(" > ")
        self.assertRegex(first, r"1  standup-prep\s+Standup prep")
        self.t1.unlink()
        proc.stdin.write("1\n"); proc.stdin.flush()
        second = read_until(" > ")
        self.assertIn("✗ standup-prep: transcript for session 11111111… is gone (expired) · pin unpin standup-prep", second)
        self.assertRegex(second, r"1  rc-mower")  # the redraw drops the swept pin to the expired count
        self.assertIn("1 expired · a show · p prune", second)
        proc.stdin.write("q\n"); proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertIsNone(self.claude_calls())


class PlainTerminalTests(FzfSandbox):
    """The plain editor in a pseudo-terminal, which is the only place readline's pre-fill and ctrl-c can be
    seen: piped stdin (the MenuTests above) bypasses readline altogether."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        os.environ["TERM"] = "xterm-256color"
        self.make_session(SID1, age_days=2, title="Standup prep")
        self.run_pin("add", SID1, "standup-prep")

    def tearDown(self):
        os.environ.pop("TERM", None)
        super().tearDown()

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    def test_prefill_and_ctrl_c(self):
        import pty, select, sys, time
        from claude_pins.prompt import prefills
        try:
            import readline
        except ImportError:
            self.skipTest("no readline")
        if not prefills(readline):
            self.skipTest("libedit: the pre-input hook does not insert, the default is shown in the label instead")
        from tests.helpers import PIN
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(PIN), "edit", "standup-prep"], dict(os.environ))
        out = b""

        def wait_for(pattern: str) -> str:
            nonlocal out
            deadline = time.time() + 15
            while time.time() < deadline:
                text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.decode("utf-8", "replace"))
                if re.search(pattern, text):
                    return text
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        out += os.read(fd, 65536)
                    except OSError:
                        break
            self.fail(f"{pattern!r} never appeared:\n{out.decode('utf-8', 'replace')[-600:]}")

        try:
            wait_for(r"📌 pins › standup-prep › edit\r?\n")
            wait_for(r" > ")
            os.write(fd, b"1\r")
            wait_for(r"title: Standup prep")                    # the value is pre-filled, not just shown
            os.write(fd, b" (Tue)\r")
            wait_for(r"edit \(unsaved\)")
            wait_for(r"title \*\s+\*Standup prep \(Tue\)")
            os.write(fd, b"2\r")
            wait_for(r"alias: standup-prep")
            os.write(fd, b"\x03")                               # ctrl-c: the field is cancelled, the form stays
            wait_for(r"alias: standup-prep\r?\n[\s\S]*edit \(unsaved\)")
            os.write(fd, b"s\r")
            wait_for(r"✓ saved standup-prep")
            deadline = time.time() + 15
            while time.time() < deadline:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    break
                time.sleep(0.05)
            else:
                self.fail("pin did not exit")
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(fd)
        self.assertEqual(status, 0)
        self.assertEqual(self.stored()["standup-prep"]["title"], "Standup prep (Tue)")
        self.assertEqual(self.stored()["standup-prep"]["alias"], "standup-prep")
