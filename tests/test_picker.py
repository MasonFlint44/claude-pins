import json
import os
import re

from tests.helpers import FzfSandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"
SID4 = "44444444-4444-4444-4444-444444444444"


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class PickerTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.t2 = self.make_session(SID2, cwd=str(self.home / "git" / "cc"), age_days=9, title="Command center collector")
        self.t3 = self.make_session(SID3, cwd=str(self.home), age_days=26, title="Navimow schedule debug")
        for sid, alias in ((SID1, "standup-prep"), (SID2, "cc-collector"), (SID3, "rc-mower")):
            self.pin_aged(sid, alias)

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    def arg(self, call, flag):
        a = call["argv"]
        return a[a.index(flag) + 1] if flag in a else None

    def pin_rows(self, call):
        """The plain rows after the column-label line (fzf's --header-lines=1 row, id ``-``)."""
        lines = [plain(l) for l in call["lines"]]
        if lines and re.match(r"^-\talias\s+title\s+(directory|dir)\s+idle$", lines[0]):
            self.assertIn("--header-lines=1", call["argv"])
            return lines[1:]
        self.assertNotIn("--header-lines=1", call["argv"])
        return lines

    def labels(self, call):
        """Palette/help/editor rows without the group gutter: ``label   key``."""
        return [re.sub(r"^(?:\S+)?\s{2,}", "", plain(l).split("\t")[1]) for l in call["lines"]]

    def test_enter_opens(self):
        self.steps({"key": "", "select": ["cc-collector"]})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2])
        call = self.fzf_calls()[0]
        self.assertIn("--expect", call["argv"])
        self.assertIn("alt-t", self.arg(call, "--expect"))
        self.assertEqual(self.arg(call, "--prompt"), "📌 pins › ")
        self.assertIn("--header-first", call["argv"])
        rows = self.pin_rows(call)
        self.assertTrue(rows[0].startswith("standup-prep\t"))   # recency sort: newest first
        self.assertRegex(rows[2], r"rc-mower\s.*~\s+26d\s+⏳$")
        header = plain(self.arg(call, "--header")).split("\n")
        self.assertTrue(header[0].startswith("🟢 open  🚩 keep"))    # the legend first, over the hints (100 columns)
        self.assertEqual(header[1], "enter open · ctrl-x actions · f2 edit · ctrl-t new · alt-i details · f1 help")
        self.assertEqual(header[2:], [" "])                            # the status line, blank: a gap above the prompt

    def test_theme_and_glyph_switches(self):
        """Colour on: fzf gets the token --color spec and the rows carry 24-bit codes. Off (NO_COLOR or
        CLAUDE_PINS_COLOR=0): --color=bw and no codes. CLAUDE_PINS_GLYPHS=text drops the logo and the emoji."""
        from claude_pins import theme
        self.steps({"key": "esc"})
        r = self.run_pin(env={"NO_COLOR": "", "CLAUDE_PINS_COLOR": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        call = self.fzf_calls()[0]
        self.assertIn(f"--color={theme.fzf_colors()}", call["argv"])
        self.assertIn("prompt:#d97757", theme.fzf_colors())
        self.assertTrue(call["lines"][3].endswith(f"\x1b[{theme.sgr(theme.WARNING)}m 26d\x1b[0m\t ⏳"))  # gold idle; emoji bare; tab-separated columns
        self.assertIn("\x1b[1m", call["lines"][1])                      # the alias is bold
        self.assertIn("\x1b[2m", call["lines"][0])                      # the label row is dim
        for env in ({"NO_COLOR": "1"}, {"NO_COLOR": "", "CLAUDE_PINS_COLOR": "0"}):
            self.fzf_log.unlink()
            self.run_pin(env=env)
            call = self.fzf_calls()[0]
            self.assertIn("--color=bw", call["argv"])
            self.assertFalse(any("\x1b[" in l for l in call["lines"] + [self.arg(call, "--header")]))
        self.fzf_log.unlink()
        self.run_pin(env={"NO_COLOR": "", "CLAUDE_PINS_COLOR": "1", "CLAUDE_PINS_GLYPHS": "text"})
        call = self.fzf_calls()[0]
        self.assertEqual(self.arg(call, "--prompt"), "pins › ")
        rows = self.pin_rows(call)
        self.assertRegex(rows[2], r"rc-mower\s.*~\s+26d\s+⧗$")
        self.assertIn(f"\x1b[{theme.sgr(theme.WARNING)}m⧗\x1b[0m", call["lines"][3])   # text glyphs are coloured
        self.assertIn("● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⧗ expiring  ✗ expired", plain(self.arg(call, "--header")))

    def test_fork_and_worktree_keys(self):
        self.steps({"key": "alt-o", "select": ["standup-prep"]})
        self.run_pin()
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--fork-session", "--name", "standup-prep"])
        self.steps({"key": "alt-w", "select": ["standup-prep"]})
        self.run_pin()
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--worktree"])

    def test_touch_flash_and_cursor(self):
        self.steps({"key": "alt-t", "select": ["rc-mower"]}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.fzf_calls()
        self.assertEqual(len(calls), 2)
        header = plain(self.arg(calls[1], "--header")).split("\n")
        self.assertEqual(header[2], "✓ touched rc-mower")          # the flash takes the status line
        self.assertEqual(len(header), 3)
        self.assertTrue(header[0].startswith("🟢 open  🚩 keep"))    # the legend stays
        self.assertIn("enter open · ctrl-x actions · f2 edit · ctrl-t new · alt-i details · f1 help", header[1])
        self.assertNotIn("✓", plain(self.arg(calls[0], "--header")))
        # rc-mower is now newest → first row, and the cursor is restored on it via start:pos
        rows = self.pin_rows(calls[1])
        self.assertTrue(rows[0].startswith("rc-mower\t"))
        self.assertNotIn("start:pos", " ".join(calls[1]["argv"]))   # first row is fzf's default position
        self.assertNotRegex(rows[0], r"⏳")

    def test_keep_toggle_multi(self):
        self.steps({"key": "alt-k", "select": ["standup-prep", "rc-mower"]}, {"abort": True})
        self.run_pin()
        self.assertTrue(self.stored()["standup-prep"]["keep"])
        self.assertTrue(self.stored()["rc-mower"]["keep"])
        self.assertFalse(self.stored()["cc-collector"]["keep"])
        header = plain(self.arg(self.fzf_calls()[1], "--header"))
        self.assertIn("✓ keep on for standup-prep, rc-mower", header)
        self.assertRegex(self.pin_rows(self.fzf_calls()[1])[0], r"🚩")

    def test_unpin_and_undo(self):
        self.steps({"key": "alt-x", "select": ["cc-collector"]}, {"key": "alt-z"}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("✓ unpinned cc-collector · alt-z undo · session name cleared", plain(self.arg(calls[1], "--header")))
        self.assertEqual(len(self.pin_rows(calls[1])), 2)
        self.assertIn("✓ restored cc-collector (unpin) · session named 📌 cc-collector", plain(self.arg(calls[2], "--header")))
        self.assertEqual(len(self.pin_rows(calls[2])), 3)
        self.assertEqual(json.loads(self.t2.read_text().splitlines()[-1])["customTitle"], "📌 cc-collector")

    def test_expired_footer_show_prune(self):
        self.t3.unlink()
        self.steps({"key": "alt-a"}, {"key": "alt-p"}, {"key": "", "select": ["yes"]}, {"abort": True})
        r = self.run_pin()
        calls = self.fzf_calls()
        self.assertEqual(len(self.pin_rows(calls[0])), 2)
        self.assertEqual(self.arg(calls[0], "--border-label"), " 1 expired · alt-a show · pins prune ")
        self.assertEqual(len(self.pin_rows(calls[1])), 3)
        self.assertRegex(self.pin_rows(calls[1])[-1], r"rc-mower\s.*🔴$")
        ask = calls[2]                                                  # the yes/no is an fzf list
        self.assertEqual(self.arg(ask, "--prompt"), "📌 pins › prune › ")
        self.assertEqual(plain(self.arg(ask, "--header")).split("\n"),
                         ["enter choose · esc cancel", "prune 1 expired pin(s): rc-mower", "unpin them? (pins undo restores)", " "])
        self.assertEqual([l.split("\t")[1] for l in ask["lines"]], ["yes", "no"])
        self.assertNotIn("--bind", ask["argv"])                          # cursor on yes: no start:pos needed
        self.assertEqual(r.stdout, "")                                    # nothing printed under the screen
        self.assertIn("✓ pruned 1 · alt-z undo", plain(self.arg(calls[3], "--header")))
        self.assertEqual(len(self.pin_rows(calls[3])), 2)
        self.assertNotIn("--border-label", calls[3]["argv"])

    def test_prune_nothing(self):
        self.steps({"key": "alt-p"}, {"abort": True})
        self.run_pin()
        self.assertIn("nothing to prune", plain(self.arg(self.fzf_calls()[1], "--header")))

    def test_sort_cycle_and_env(self):
        self.steps({"key": "alt-s"}, {"key": "alt-s"}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        first = lambda c: [l.split("\t")[0] for l in self.pin_rows(c)]
        self.assertEqual(first(calls[0]), ["standup-prep", "cc-collector", "rc-mower"])
        self.assertEqual(first(calls[1]), ["cc-collector", "rc-mower", "standup-prep"])  # alias
        self.assertEqual(first(calls[2]), ["standup-prep", "cc-collector", "rc-mower"])  # pinned order
        self.steps({"abort": True})
        self.run_pin(env={"CLAUDE_PINS_SORT": "alias"})
        self.assertEqual(first(self.fzf_calls()[3]), ["cc-collector", "rc-mower", "standup-prep"])

    def test_empty_state(self):
        for a in ("standup-prep", "cc-collector", "rc-mower"):
            self.run_pin("rm", a)
        self.steps({"abort": True})
        self.run_pin()
        call = self.fzf_calls()[0]
        lines = [plain(l) for l in call["lines"]]
        self.assertEqual(len(lines), 1)                 # the message takes the sticky row: nothing to select
        self.assertIn("--header-lines=1", call["argv"])
        self.assertIn("No pins yet. ctrl-t pins a recent session, or run /pins:pin inside a Claude session.", lines[0])
        self.assertTrue(lines[0].startswith("-\t"))

    def test_palette_grouped_and_context(self):
        self.steps({"key": "ctrl-x", "select": ["standup-prep"]}, {"key": "", "select": ["touch"]}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        pal = calls[1]
        self.assertEqual(self.arg(pal, "--prompt"), "📌 pins › standup-prep › actions › ")
        raw = [plain(l).split("\t")[1] for l in pal["lines"]]
        self.assertRegex(raw[0], r"^open  Open\s+enter$")                 # group name in the gutter…
        self.assertRegex(raw[1], r"^      Open as fork\s+alt-o$")        # …only on the group's first row
        self.assertTrue(any(r.startswith("pin   Edit…") for r in raw))
        self.assertFalse(any("──" in r for r in raw))                     # no unselectable rows
        self.assertFalse(any(l.startswith("-\t") for l in pal["lines"]))
        rows = self.labels(pal)
        self.assertIn("Details                     alt-i", rows)
        self.assertIn("Toggle keep (off)           alt-k", rows)
        self.assertIn("Toggle fork mode (off)", rows)
        self.assertTrue(any(r.startswith("Show expired (0)") for r in rows))
        self.assertTrue(any(r.startswith("Undo (nothing to undo)") for r in rows))
        self.assertTrue(any(r.startswith("Sort: recency") for r in rows))
        self.assertIn("✓ touched standup-prep", plain(self.arg(calls[2], "--header")))

    def test_palette_expired_and_multi(self):
        self.t3.unlink()
        self.steps({"key": "alt-a"}, {"key": "ctrl-x", "select": ["rc-mower"]}, {"abort": True}, {"abort": True})
        self.run_pin()
        rows = self.labels(self.fzf_calls()[2])
        self.assertFalse(any(r.startswith(("Open", "Touch")) for r in rows))
        self.assertTrue(any(r.startswith("Unpin") for r in rows))
        self.steps({"key": "ctrl-x", "select": ["standup-prep", "cc-collector"]}, {"key": "", "select": ["unpin"]}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertEqual(self.arg(calls[-2], "--prompt"), "📌 pins › 2 selected › actions › ")
        rows = self.labels(calls[-2])
        self.assertFalse(any(r.startswith(("Open", "Edit", "Details")) for r in rows))
        self.assertEqual(set(self.stored()), {"rc-mower"})

    def test_palette_open_when_already_open(self):
        ps = self.root / "ps.txt"; ps.write_text(f"claude --resume {SID1}\n")
        self.steps({"key": "ctrl-x", "select": ["standup-prep"]}, {"abort": True}, {"abort": True})
        self.run_pin(env={"CLAUDE_PINS_PS": str(ps)})
        rows = self.labels(self.fzf_calls()[1])
        self.assertTrue(any(r.startswith("Resume anyway (open in another tab)") for r in rows))
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows if r.endswith(("enter", "alt-o", "alt-w"))))  # keys aligned

    def test_help_screen_rebind_reset(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"query": "f5"}, {"key": "ctrl-r", "select": ["touch"]},
                   {"key": "alt-r"}, {"abort": True}, {"abort": True})
        r = self.run_pin()
        calls = self.fzf_calls()
        help_call = calls[1]
        self.assertEqual(self.arg(help_call, "--prompt"), "📌 pins › help › ")
        header = plain(self.arg(help_call, "--header")).split("\n")
        self.assertTrue(header[0].startswith("🟢 open  🚩 keep  🔀 fork  🌳 worktree  ⏳ expiring  🔴 expired"))
        self.assertEqual(header[1], "enter rebind · ctrl-r reset row · alt-r reset all · esc back")
        self.assertEqual(header[2:], ["keymap: ~/.config/claude-pins/keys.toml", " "])
        self.assertTrue(any(b.startswith("focus:transform-header(") for b in self.binds(help_call)))  # re-fits too
        self.assertFalse(any(l.startswith("-\t") for l in help_call["lines"]))   # every row is an action
        rows = self.labels(help_call)
        self.assertIn("Touch transcript      alt-t", rows)
        ask = calls[2]                                                  # the key is typed on fzf's query line
        self.assertEqual(self.arg(ask, "--prompt"), "📌 pins › help › Touch transcript › ")
        self.assertEqual(self.arg(ask, "--query"), "alt-t")
        self.assertIn("--disabled", ask["argv"]); self.assertEqual(ask["lines"], [])
        self.assertEqual(plain(self.arg(ask, "--header")).split("\n"),
                         ["enter save · esc cancel · ctrl-u clear", "e.g. alt-t, f5; empty unbinds", " "])
        self.assertEqual(r.stdout, "")
        self.assertIn("✓ Touch transcript: f5", plain(self.arg(calls[3], "--header")))
        self.assertIn("Touch transcript      f5", self.labels(calls[3]))
        self.assertIn("✓ Touch transcript: reset to alt-t", plain(self.arg(calls[4], "--header")))
        self.assertIn("✓ keymap reset to defaults", plain(self.arg(calls[5], "--header")))
        keymap = self.home / ".config" / "claude-pins" / "keys.toml"
        self.assertIn('touch = "alt-t"', keymap.read_text())

    def test_rebind_conflict_and_invalid(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"query": "?"}, {"query": "alt-x"},
                   {"key": "", "select": ["yes"]}, {"abort": True}, {"abort": True})
        r = self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("printable character", plain(self.arg(calls[3], "--header")))   # the retry carries the error
        self.assertEqual(self.arg(calls[3], "--query"), "alt-t")
        ask = calls[4]
        self.assertEqual(plain(self.arg(ask, "--header")).split("\n")[1:], ["conflicts: Unpin", "bind anyway?", " "])
        self.assertIn("start:pos(2)", " ".join(ask["argv"]))                          # default no
        keymap = (self.home / ".config" / "claude-pins" / "keys.toml").read_text()
        self.assertIn('touch = "alt-x"', keymap)
        self.assertIn('unpin = ""', keymap)
        # remapped key shows up in hints and expect list
        self.steps({"abort": True})
        self.run_pin()
        call = self.fzf_calls()[-1]
        self.assertIn("alt-x", self.arg(call, "--expect"))
        self.assertNotIn("alt-t", self.arg(call, "--expect"))

    def test_new_pin_flow(self):
        sid4 = "44444444-4444-4444-4444-444444444444"
        self.make_session(sid4, cwd=str(self.home / "Documents"), age_days=0.1, title="Tax prep questions", n_turns=9)
        self.steps({"key": "ctrl-t"}, {"key": "", "select": [f"{sid4}"]}, {"query": ""}, {"abort": True})
        r = self.run_pin()
        calls = self.fzf_calls()
        new_call = calls[1]
        self.assertEqual(self.arg(new_call, "--prompt"), "📌 pins › new › ")
        rows = [plain(l) for l in new_call["lines"]]
        self.assertRegex(rows[0], r"^-\ttitle\s+directory\s+idle\s+msgs\s+pin$")   # the sticky label row
        self.assertIn("--header-lines=1", new_call["argv"])
        self.assertTrue(rows[1].split("\t")[0].endswith(f"{sid4}.jsonl"))
        self.assertRegex(rows[1], r"Tax prep questions\s+~/Documents\s+\d+[mh]\s+18 msgs")
        self.assertRegex(rows[2], r"Standup prep\s+.*📌 standup-prep$")               # the pin's alias, not "pinned"
        ask = calls[2]
        self.assertEqual(self.arg(ask, "--prompt"), "📌 pins › new › alias › ")
        self.assertEqual(self.arg(ask, "--query"), "tax-prep-questions")           # prefilled; an empty answer keeps it
        self.assertIn("suggested from the title", plain(self.arg(ask, "--header")))
        self.assertIn("tax-prep-questions", self.stored())
        self.assertEqual(self.stored()["tax-prep-questions"]["title"], "Tax prep questions")
        self.assertIn("✓ pinned as tax-prep-questions · session named 📌 tax-prep-questions",
                      plain(self.arg(calls[3], "--header")))
        self.assertTrue(self.pin_rows(calls[3])[0].startswith("tax-prep-questions\t"))
        self.assertIn('"customTitle":"📌 tax-prep-questions"',
                      (self.project_dir(str(self.home / "Documents")) / f"{sid4}.jsonl").read_text())

    def test_new_pin_already_pinned(self):
        self.steps({"key": "ctrl-t"}, {"key": "", "select": [SID1]}, {"abort": True})
        self.run_pin()
        self.assertIn("already pinned as standup-prep", plain(self.arg(self.fzf_calls()[2], "--header")))

    def test_query_prefilter(self):
        self.steps({"abort": True})
        self.run_pin("prep")  # matches standup-prep only → opens directly
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1])
        self.steps({"abort": True})
        self.run_pin("e")  # ambiguous → picker pre-filtered
        self.assertEqual(self.arg(self.fzf_calls()[-1], "--query"), "e")

    def test_preview_command(self):
        self.steps({"abort": True})
        self.run_pin()
        call = self.fzf_calls()[0]
        self.assertTrue(self.arg(call, "--preview").endswith("_preview {1}"))
        self.assertEqual(self.arg(call, "--preview-window"), "down,55%,border-rounded,wrap,<10(hidden)")
        self.assertNotIn("preview hidden", plain(self.arg(call, "--header")))  # 24 rows: the pane fits

    def test_preview_too_short(self):
        """Under fzf's threshold the pane hides itself; the header says so, and alt-v cannot bring it back."""
        self.steps({"key": "alt-v"}, {"key": "alt-v"}, {"abort": True})
        self.run_pin(env={"LINES": "18"})
        calls = self.fzf_calls()
        self.assertIn("preview hidden: terminal too short", plain(self.arg(calls[0], "--header")))
        self.assertNotIn("--preview", calls[1]["argv"])                     # off: alt-v turned it off
        self.assertNotIn("preview hidden", plain(self.arg(calls[1], "--header")))
        self.assertNotIn("--preview", calls[2]["argv"])                     # still off: too short to turn on
        self.assertIn("preview needs a taller terminal · alt-i for details", plain(self.arg(calls[2], "--header")))
        self.steps({"abort": True})
        self.run_pin(env={"LINES": "19"})
        self.assertNotIn("preview hidden", plain(self.arg(self.fzf_calls()[-1], "--header")))
        self.t3.unlink()                                                    # the expired footer costs one row
        self.steps({"abort": True})
        self.run_pin(env={"LINES": "19"})
        self.assertIn("preview hidden: terminal too short", plain(self.arg(self.fzf_calls()[-1], "--header")))

    def test_details_screen(self):
        self.steps({"key": "alt-i", "select": ["cc-collector"]}, {"abort": True}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        det = calls[1]
        self.assertEqual(self.arg(det, "--prompt"), "📌 pins › cc-collector › details › ")
        self.assertIn("--disabled", det["argv"])
        self.assertEqual(plain(self.arg(det, "--header")), "enter open · esc back\n ")
        body = [plain(l) for l in det["lines"]]
        self.assertTrue(all(l.startswith("-\t") for l in body))
        self.assertEqual(body[0], "-\tCommand center collector")
        self.assertTrue(any(l.startswith("-\tsession    " + SID2) for l in body))
        self.assertTrue(any("you        hello there" in l for l in body))
        self.assertEqual(len(calls), 3)                                     # esc: back to the list
        self.assertIn("start:pos(2)", " ".join(calls[2]["argv"]))          # cursor kept; rows count from 1 under the label row
        self.assertIsNone(self.claude_calls())
        self.steps({"key": "alt-i", "select": ["cc-collector"]}, {"key": "", "select": ["-"]})
        self.run_pin()
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2])   # enter on the details opens
        self.assertEqual(self.claude_calls()["cwd"], str(self.home / "git" / "cc"))

    def test_edit_from_picker(self):
        self.steps({"key": "f2", "select": ["standup-prep"]},
                   {"key": "", "select": ["title"]},     # editor: pick title field
                   {"query": "Standup prep (Tue)"},      # typed on the query line
                   {"key": "alt-s"},                      # save
                   {"abort": True})
        r = self.run_pin()
        self.assertEqual(self.stored()["standup-prep"]["title"], "Standup prep (Tue)")
        self.assertEqual(self.arg(self.fzf_calls()[2], "--prompt"), "📌 pins › standup-prep › edit › title › ")
        self.assertIn("✓ saved standup-prep", plain(self.arg(self.fzf_calls()[-1], "--header")))
        self.assertNotIn("session named", plain(self.arg(self.fzf_calls()[-1], "--header")))   # the alias did not change
        self.steps({"key": "f2", "select": ["standup-prep"]}, {"key": "", "select": ["alias"]}, {"query": "sp"},
                   {"key": "alt-s"}, {"abort": True})
        self.run_pin()
        self.assertIn("✓ saved sp · session named 📌 sp", plain(self.arg(self.fzf_calls()[-1], "--header")))
        self.assertEqual(json.loads(self.t1.read_text().splitlines()[-1])["customTitle"], "📌 sp")

    def header_after(self, index=-1):
        return plain(self.arg(self.fzf_calls()[index], "--header"))

    def test_open_expired_and_no_selection(self):
        self.t3.unlink()
        self.steps({"key": "alt-a"}, {"key": "", "select": ["rc-mower"]}, {"key": "", "select": []},
                   {"key": "alt-t", "select": []}, {"key": "ctrl-x", "select": []}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ rc-mower: transcript for session 33333333… is gone (expired) · pins unpin rc-mower",
                      self.header_after(2))
        self.assertEqual(len(self.fzf_calls()), 6)  # every empty selection just redraws
        self.assertIsNone(self.claude_calls())

    def test_open_cancelled_and_gone_flash(self):
        ps = self.root / "ps.txt"; ps.write_text(f"claude --resume {SID1}\n")
        self.steps({"key": "", "select": ["standup-prep"]}, {"abort": True})
        self.run_pin(input="2\n", env={"CLAUDE_PINS_PS": str(ps)})
        self.assertIn("cancelled", self.header_after())
        self.assertIsNone(self.claude_calls())

    def test_preview_toggle_and_sort_flash(self):
        self.steps({"key": "alt-v"}, {"key": "alt-v"}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("--preview", calls[0]["argv"]); self.assertNotIn("--preview", calls[1]["argv"])
        self.assertIn("--preview", calls[2]["argv"])

    def test_help_screen_cancel_paths(self):
        self.steps({"key": "f1"}, {"key": "", "select": []}, {"key": "ctrl-r"},
                   {"key": "", "select": ["touch"]}, {"abort": True},                                    # 3
                   {"key": "", "select": ["touch"]}, {"query": "ctrl-a"}, {"key": "", "select": ["no"]},  # 4
                   {"query": "alt-enter"}, {"key": "", "select": ["no"]}, {"abort": True},               # 5
                   {"abort": True}, {"abort": True})
        # 1: nothing selected (ignored)  2: reset without a target (ignored)  3: rebind cancelled with esc
        # 4: an editing key, declined (the key screen comes back)  5: alt-enter, declined, then esc
        r = self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("ctrl-a is one of fzf's query-editing keys", plain(self.arg(calls[7], "--header")))
        self.assertIn("Windows Terminal uses alt+enter", plain(self.arg(calls[9], "--header")))
        self.assertEqual(self.arg(calls[10], "--prompt"), "📌 pins › help › Touch transcript › ")
        keymap = (self.home / ".config" / "claude-pins" / "keys.toml")
        self.assertFalse(keymap.exists())  # nothing was ever bound

    def test_rebind_eof_at_confirmation_and_unbind(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"query": "alt-x"}, {"abort": True},
                   {"key": "", "select": ["touch"]}, {"abort": True}, {"abort": True}, {"abort": True})
        r = self.run_pin()  # conflict question gets esc → back to help; then the key screen itself is left with esc
        self.assertIn("conflicts: Unpin", self.header_after(3))
        self.assertFalse((self.home / ".config" / "claude-pins").exists())
        self.fzf_log.unlink()
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"query": ""}, {"abort": True}, {"abort": True})
        r = self.run_pin()  # an empty key unbinds
        self.assertIn('touch = ""', (self.home / ".config" / "claude-pins" / "keys.toml").read_text())
        self.assertIn("✓ Touch transcript: (unbound)", self.header_after(3))

    def test_new_pin_cancel_taken_and_empty(self):
        self.steps({"key": "ctrl-t"}, {"abort": True}, {"abort": True})
        self.run_pin()
        self.assertEqual(len(self.fzf_calls()), 3)
        sid4 = "44444444-4444-4444-4444-444444444444"
        self.make_session(sid4, age_days=0.1, title="Fresh")
        self.fzf_log.unlink()
        self.steps({"key": "ctrl-t"}, {"key": "", "select": [sid4]}, {"query": "rc-mower"}, {"abort": True}, {"abort": True})
        r = self.run_pin()  # taken alias, then esc cancels
        self.assertIn("✗ alias rc-mower is taken", self.header_after(3))    # the retry says why
        self.assertEqual(self.arg(self.fzf_calls()[3], "--query"), "fresh")   # and offers the suggestion again
        self.assertNotIn("fresh", self.stored())
        for t in (self.t1, self.t2, self.t3, self.project_dir(str(self.home / "git" / "proj")) / f"{sid4}.jsonl"):
            t.unlink()
        self.steps({"key": "ctrl-t"}, {"abort": True})
        self.run_pin()
        self.assertIn("no sessions found under ~/.claude/projects", self.header_after())

    def test_prune_cancel_and_undo_nothing(self):
        self.steps({"key": "alt-z"}, {"key": "alt-p"}, {"key": "alt-p"}, {"abort": True})
        r = self.run_pin()
        self.assertIn("nothing to undo", self.header_after(1))
        self.assertIn("nothing to prune", self.header_after(2))
        self.t3.unlink(); self.fzf_log.unlink()
        self.steps({"key": "alt-p"}, {"key": "", "select": ["no"]}, {"key": "alt-p"}, {"abort": True}, {"abort": True})
        r = self.run_pin()  # declined, then esc at the question
        self.assertIn("prune cancelled", self.header_after(2))
        self.assertIn("prune cancelled", self.header_after(4))
        self.assertIn("rc-mower", self.stored())

    def env_of(self, call):
        """The CLAUDE_PINS_* variables the screen exported for its shell snippets, colour stripped."""
        return {k: plain(v) for k, v in call["env"].items()}

    def binds(self, call):
        a = call["argv"]
        return [a[i + 1] for i, x in enumerate(a) if x == "--bind"]

    def run_again(self, *args, **kw):
        """run_pin with the fzf log emptied first, so fzf_calls() holds this run only."""
        if self.fzf_log.exists():
            self.fzf_log.unlink()
        return self.run_pin(*args, **kw)

    def test_refresh_reloads_in_place(self):
        """ctrl-r is a --bind to reload(pins _rows), not an --expect key; the row command carries the sort,
        the expired switch and the launch width."""
        self.steps({"key": "alt-s"}, {"key": "alt-a"}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        for call in calls:
            self.assertIn("--no-clear", call["argv"])
            self.assertNotIn("ctrl-r", self.arg(call, "--expect"))
        self.assertIn("ctrl-r:reload(COLUMNS=100 " + os.environ["CLAUDE_PINS_EXE"] + " _rows --sort recency)", self.binds(calls[0]))
        self.assertIn("ctrl-r:reload(COLUMNS=100 " + os.environ["CLAUDE_PINS_EXE"] + " _rows --sort alias)", self.binds(calls[1]))
        self.assertIn("ctrl-r:reload(COLUMNS=100 " + os.environ["CLAUDE_PINS_EXE"] + " _rows --sort alias --all)",
                      self.binds(calls[2]))

    def test_rows_subcommand_matches_the_screen(self):
        """pins _rows prints exactly the lines the picker sent fzf, label row first, so a reload cannot drift."""
        self.steps({"abort": True})
        self.run_pin()
        r = self.run_pin("_rows", "--sort", "recency")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), self.fzf_calls()[0]["lines"])
        r = self.run_pin("_rows", "--sort", "alias", env={"FZF_COLUMNS": "60", "NO_COLOR": "", "CLAUDE_PINS_COLOR": "1"})
        rows = r.stdout.splitlines()
        self.assertRegex(plain(rows[0]), r"^-\talias\s+title\s+directory\s+idle$")
        self.assertEqual([l.split("\t")[0] for l in rows[1:]], ["cc-collector", "rc-mower", "standup-prep"])
        self.assertIn("\x1b[", rows[1])                                              # coloured, like _preview
        self.assertTrue(all(len(plain(l).split("\t")[1]) <= 56 for l in rows))
        self.t3.unlink()
        self.assertNotIn("rc-mower", self.run_pin("_rows").stdout)
        self.assertIn("rc-mower", self.run_pin("_rows", "--all").stdout)
        for a in ("standup-prep", "cc-collector", "rc-mower"):
            self.run_pin("rm", a)
        self.assertEqual(self.run_pin("_rows").stdout.count("\n"), 1)
        self.assertIn("-\tNo pins yet. ctrl-t pins", self.run_pin("_rows").stdout)

    def test_version_gates(self):
        """Older fzf: the header re-fits on focus and change, reading the size from stty; 0.46 adds the resize
        event (rows and header re-fit on resize), 0.51 --with-shell, 0.63 the gap row under the prompt, and
        0.65.2 the info command. With the preview off the transform stays (it lays the header out) but the
        too-short note is empty."""
        self.steps({"abort": True})
        self.run_pin()
        call = self.fzf_calls()[0]
        binds = self.binds(call)
        self.assertNotIn("--info-command", call["argv"])
        self.assertNotIn("--with-shell", call["argv"])
        self.assertFalse(any(b.startswith("resize:") for b in binds))
        focus = next(b for b in binds if b.startswith("focus:"))
        self.assertIn("transform-preview-label(", focus)
        self.assertIn("+transform-header(c=$FZF_COLUMNS; h=$FZF_LINES; test -n \"$c\" || c=`stty size </dev/tty", focus)
        self.assertIn('test "$h" -lt 19 && s=$CLAUDE_PINS_NOTE', focus)
        change = next(b for b in binds if b.startswith("change:"))
        self.assertTrue(change.startswith("change:transform-header("))
        self.assertNotIn("(", change.split("transform-header(", 1)[1][:-1])       # fzf ends the action at one
        self.assertNotIn("[", change); self.assertNotIn("]", change)
        self.assertEqual(self.env_of(call)["CLAUDE_PINS_NOTE"], "preview hidden: terminal too short")
        self.assertEqual(self.env_of(call)["CLAUDE_PINS_LEGEND_CELLS"], "63")
        self.assertEqual(self.env_of(call)["CLAUDE_PINS_HINTS_CELLS"], "76")
        self.steps({"key": "alt-v"}, {"abort": True})
        self.run_again(env={"CLAUDE_PINS_FZF_STUB_VERSION": "0.53.0"})
        calls = self.fzf_calls()
        binds = self.binds(calls[0])
        self.assertNotIn("--info-command", calls[0]["argv"])
        self.assertIn("--with-shell", calls[0]["argv"])
        self.assertEqual(self.arg(calls[0], "--with-shell"), "sh -c")
        self.assertIn("--header-lines=1", calls[0]["argv"])                        # sticky rows sit above the prompt
        self.assertFalse(any(b.startswith(("change:", "focus:transform-header")) for b in binds))
        resize = next(b for b in binds if b.startswith("resize:"))
        self.assertTrue(resize.startswith("resize:reload(COLUMNS=100 "))
        self.assertIn(" _rows --sort recency)+transform-header(", resize)
        self.assertIn("+transform-header(", self.binds(calls[1])[-1])             # preview off: still re-fits
        self.assertEqual(self.env_of(calls[1])["CLAUDE_PINS_NOTE"], "")            # but no note
        self.steps({"abort": True})
        self.run_pin(env={"CLAUDE_PINS_FZF_STUB_VERSION": "0.63.0"})
        call = self.fzf_calls()[-1]
        self.assertIn("--header-lines=2", call["argv"])                           # the gap row, then the labels
        self.assertEqual(plain(call["lines"][0]), "-\t ")
        self.assertRegex(plain(call["lines"][1]), r"^-\talias\s+title")
        self.assertIn(" _rows --sort recency --gap)", next(b for b in self.binds(call) if b.startswith("resize:")))
        r = self.run_pin("_rows", "--sort", "recency", "--gap")
        self.assertEqual(r.stdout.splitlines(), call["lines"])                    # a reload draws the same
        self.steps({"abort": True})
        self.run_pin(env={"CLAUDE_PINS_FZF_STUB_VERSION": "0.65.1"})     # has --info-command, cuts its last cell
        self.assertNotIn("--info-command", self.fzf_calls()[-1]["argv"])
        self.steps({"abort": True})
        self.run_pin(env={"CLAUDE_PINS_FZF_STUB_VERSION": "0.65.2"})
        call = self.fzf_calls()[-1]
        self.assertIn('t="$FZF_MATCH_COUNT of $n $s"', self.arg(call, "--info-command"))
        self.t3.unlink()                                          # the expired footer costs a row: limit 20
        self.steps({"abort": True})
        self.run_pin()
        self.assertIn('test "$h" -lt 20', self.binds(self.fzf_calls()[-1])[1])

    def test_rebound_refresh_stays_a_bind(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["refresh"]}, {"query": "alt-u"}, {"abort": True}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("Refresh               ctrl-r", self.labels(calls[1]))
        self.assertIn("✓ Refresh: alt-u", plain(self.arg(calls[3], "--header")))
        self.assertIn("Refresh               alt-u", self.labels(calls[3]))
        main = calls[4]
        self.assertNotIn("alt-u", self.arg(main, "--expect"))
        self.assertNotIn("ctrl-r", self.arg(main, "--expect"))
        self.assertTrue(any(b.startswith("alt-u:reload(") for b in self.binds(main)))
        self.assertFalse(any(b.startswith("ctrl-r:") for b in self.binds(main)))
        self.steps({"key": "ctrl-x", "select": ["standup-prep"]}, {"key": "", "select": ["refresh"]}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("Refresh                     alt-u", self.labels(calls[-2]))
        self.assertEqual(len(calls), 8)                            # from the palette the restart is the refresh

    def test_dispatch_sees_pins_added_after_launch(self):
        """A pin added by another terminal and brought in by a reload opens on enter: the views are rebuilt
        from the store when the key arrives, not taken from the list built at launch."""
        self.make_session(SID4, cwd=str(self.home / "git" / "late"), age_days=0.5, title="Late arrival")
        pin = os.environ["CLAUDE_PINS_EXE"]
        self.steps({"key": "", "shell": f"{pin} add {SID4} late", "raw": [f"late\tlate  Late arrival"]})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("late", [l.split("\t")[0] for l in self.fzf_calls()[0]["lines"]])
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID4])
        self.assertEqual(self.claude_calls()["cwd"], str(self.home / "git" / "late"))

    def test_transcript_swept_while_picker_open(self):
        """Claude's retention sweep can delete a transcript while the picker sits open: enter then flashes."""
        self.steps({"key": "", "select": ["standup-prep"], "unlink": str(self.t1)}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ standup-prep: transcript for session 11111111… is gone (expired) · pins unpin standup-prep",
                      self.header_after())
        self.assertIsNone(self.claude_calls())
        rows = self.pin_rows(self.fzf_calls()[-1])
        self.assertFalse(any(r.startswith("standup-prep\t") for r in rows))  # the redraw already hides it
