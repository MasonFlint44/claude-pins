import json
import os
import re

from tests.helpers import FzfSandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class PickerTests(FzfSandbox):
    def setUp(self):
        super().setUp()
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.t2 = self.make_session(SID2, cwd=str(self.home / "git" / "cc"), age_days=9, title="Command center collector")
        self.t3 = self.make_session(SID3, cwd=str(self.home), age_days=26, title="Navimow schedule debug")
        for sid, alias in ((SID1, "standup-prep"), (SID2, "cc-collector"), (SID3, "rc-mower")):
            self.run_pin("add", sid, alias)

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
        self.assertEqual(header[0], "enter open · ctrl-space actions · alt-e edit · alt-n new · alt-i details · f1 help")
        self.assertTrue(header[1].startswith("● open  ⚑ keep"))    # the legend, until a flash displaces it

    def test_fork_and_worktree_keys(self):
        self.steps({"key": "alt-o", "select": ["standup-prep"]})
        self.run_pin()
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--fork-session"])
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
        self.assertEqual(header[1], "✓ touched rc-mower")          # the flash takes the legend's line
        self.assertEqual(len(header), 2)
        self.assertIn("enter open · ctrl-space actions · alt-e edit · alt-n new · alt-i details · f1 help", header[0])
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
        self.assertRegex(self.pin_rows(self.fzf_calls()[1])[0], r"⚑")

    def test_unpin_and_undo(self):
        self.steps({"key": "alt-x", "select": ["cc-collector"]}, {"key": "alt-z"}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertIn("✓ unpinned cc-collector · alt-z undo", plain(self.arg(calls[1], "--header")))
        self.assertEqual(len(self.pin_rows(calls[1])), 2)
        self.assertIn("✓ restored cc-collector (unpin)", plain(self.arg(calls[2], "--header")))
        self.assertEqual(len(self.pin_rows(calls[2])), 3)

    def test_expired_footer_show_prune(self):
        self.t3.unlink()
        self.steps({"key": "alt-a"}, {"key": "alt-p"}, {"abort": True})
        r = self.run_pin(input="y\n")
        calls = self.fzf_calls()
        self.assertEqual(len(self.pin_rows(calls[0])), 2)
        self.assertEqual(self.arg(calls[0], "--border-label"), " 1 expired · alt-a show · pin prune ")
        self.assertEqual(len(self.pin_rows(calls[1])), 3)
        self.assertRegex(self.pin_rows(calls[1])[-1], r"rc-mower\s.*✗$")
        self.assertIn("prune 1 expired pin(s): rc-mower", r.stdout)
        self.assertIn("✓ pruned 1 · alt-z undo", plain(self.arg(calls[2], "--header")))
        self.assertEqual(len(self.pin_rows(calls[2])), 2)
        self.assertNotIn("--border-label", calls[2]["argv"])

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
        lines = self.pin_rows(self.fzf_calls()[0])   # no label row over an empty list
        self.assertEqual(len(lines), 1)
        self.assertIn("No pins yet. alt-n pins a recent session, or run /pins:pin inside a Claude session.", lines[0])
        self.assertTrue(lines[0].startswith("-\t"))

    def test_palette_grouped_and_context(self):
        self.steps({"key": "ctrl-space", "select": ["standup-prep"]}, {"key": "", "select": ["touch"]}, {"abort": True})
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
        self.steps({"key": "alt-a"}, {"key": "ctrl-space", "select": ["rc-mower"]}, {"abort": True}, {"abort": True})
        self.run_pin()
        rows = self.labels(self.fzf_calls()[2])
        self.assertFalse(any(r.startswith(("Open", "Touch")) for r in rows))
        self.assertTrue(any(r.startswith("Unpin") for r in rows))
        self.steps({"key": "ctrl-space", "select": ["standup-prep", "cc-collector"]}, {"key": "", "select": ["unpin"]}, {"abort": True})
        self.run_pin()
        calls = self.fzf_calls()
        self.assertEqual(self.arg(calls[-2], "--prompt"), "📌 pins › 2 selected › actions › ")
        rows = self.labels(calls[-2])
        self.assertFalse(any(r.startswith(("Open", "Edit", "Details")) for r in rows))
        self.assertEqual(set(self.stored()), {"rc-mower"})

    def test_palette_open_when_already_open(self):
        ps = self.root / "ps.txt"; ps.write_text(f"claude --resume {SID1}\n")
        self.steps({"key": "ctrl-space", "select": ["standup-prep"]}, {"abort": True}, {"abort": True})
        self.run_pin(env={"CLAUDE_PINS_PS": str(ps)})
        rows = self.labels(self.fzf_calls()[1])
        self.assertTrue(any(r.startswith("Resume anyway (open in another tab)") for r in rows))
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows if r.endswith(("enter", "alt-o", "alt-w"))))  # keys aligned

    def test_help_screen_rebind_reset(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"key": "ctrl-r", "select": ["touch"]},
                   {"key": "ctrl-alt-r"}, {"abort": True}, {"abort": True})
        r = self.run_pin(input="f5\n")
        calls = self.fzf_calls()
        help_call = calls[1]
        self.assertEqual(self.arg(help_call, "--prompt"), "📌 pins › help › ")
        header = plain(self.arg(help_call, "--header")).split("\n")
        self.assertEqual(header[0], "enter rebind · ctrl-r reset row · ctrl-alt-r reset all · esc back")
        self.assertTrue(header[1].startswith("● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⏳ expiring  ✗ expired"))
        self.assertEqual(header[2], "keymap: ~/.config/claude-pins/keys.toml")
        self.assertFalse(any(l.startswith("-\t") for l in help_call["lines"]))   # every row is an action
        rows = self.labels(help_call)
        self.assertIn("Touch transcript      alt-t", rows)
        self.assertIn('new key for "Touch transcript"', r.stdout)
        self.assertIn("✓ Touch transcript: f5", plain(self.arg(calls[2], "--header")))
        self.assertIn("Touch transcript      f5", self.labels(calls[2]))
        self.assertIn("✓ Touch transcript: reset to alt-t", plain(self.arg(calls[3], "--header")))
        self.assertIn("✓ keymap reset to defaults", plain(self.arg(calls[4], "--header")))
        keymap = self.home / ".config" / "claude-pins" / "keys.toml"
        self.assertIn('touch = "alt-t"', keymap.read_text())

    def test_rebind_conflict_and_invalid(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"abort": True}, {"abort": True})
        r = self.run_pin(input="?\nalt-x\ny\n")
        self.assertIn("printable character", r.stdout)
        self.assertIn("conflicts: Unpin", r.stdout)
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
        self.steps({"key": "alt-n"}, {"key": "", "select": [f"{sid4}"]}, {"abort": True})
        r = self.run_pin(input="\n")
        calls = self.fzf_calls()
        new_call = calls[1]
        self.assertEqual(self.arg(new_call, "--prompt"), "📌 pins › new › ")
        rows = [plain(l) for l in new_call["lines"]]
        self.assertTrue(rows[0].split("\t")[0].endswith(f"{sid4}.jsonl"))
        self.assertRegex(rows[0], r"Tax prep questions\s+~/Documents\s+\d+[mh]\s+18 msgs")
        self.assertRegex(rows[1], r"Standup prep\s+.*⚑ pinned$")
        self.assertIn("alias (suggested from title · enter · ctrl-c cancel)", r.stdout)
        self.assertIn("tax-prep-questions", self.stored())
        self.assertEqual(self.stored()["tax-prep-questions"]["title"], "Tax prep questions")
        self.assertIn("✓ pinned as tax-prep-questions", plain(self.arg(calls[2], "--header")))
        self.assertTrue(self.pin_rows(calls[2])[0].startswith("tax-prep-questions\t"))

    def test_new_pin_already_pinned(self):
        self.steps({"key": "alt-n"}, {"key": "", "select": [SID1]}, {"abort": True})
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
        self.assertEqual(plain(self.arg(det, "--header")), "enter open · esc back")
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
        self.steps({"key": "alt-e", "select": ["standup-prep"]},
                   {"key": "", "select": ["title"]},     # editor: pick title field
                   {"key": "alt-s"},                      # save
                   {"abort": True})
        r = self.run_pin(input="Standup prep (Tue)\n")
        self.assertEqual(self.stored()["standup-prep"]["title"], "Standup prep (Tue)")
        self.assertIn("✓ saved standup-prep", plain(self.arg(self.fzf_calls()[-1], "--header")))

    def header_after(self, index=-1):
        return plain(self.arg(self.fzf_calls()[index], "--header"))

    def test_open_expired_and_no_selection(self):
        self.t3.unlink()
        self.steps({"key": "alt-a"}, {"key": "", "select": ["rc-mower"]}, {"key": "", "select": []},
                   {"key": "alt-t", "select": []}, {"key": "ctrl-space", "select": []}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ rc-mower has expired · unpin it or pin prune", self.header_after(2))
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
        self.steps({"key": "f1"}, {"key": "", "select": []}, {"key": "ctrl-r"}, {"key": "", "select": ["touch"]},
                   {"key": "", "select": ["touch"]}, {"key": "", "select": ["touch"]}, {"abort": True}, {"abort": True})
        # 1: nothing selected (ignored)  2: reset without a target (ignored)  3: rebind cancelled with EOF…
        r = self.run_pin(input="ctrl-a\nn\nalt-enter\n\n")
        # …4: an editing key, declined  5: alt-enter, accepted on bare enter (default is no → stays)
        self.assertIn("ctrl-a is one of fzf's query-editing keys", r.stdout)
        self.assertIn("Windows Terminal uses alt+enter", r.stdout)
        keymap = (self.home / ".config" / "claude-pins" / "keys.toml")
        self.assertFalse(keymap.exists())  # nothing was ever bound

    def test_rebind_eof_at_confirmation_and_unbind(self):
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"key": "", "select": ["touch"]}, {"abort": True}, {"abort": True})
        r = self.run_pin(input="alt-x\n")  # conflict question gets EOF → back to help; then unbind with an empty key
        self.assertIn("conflicts: Unpin", r.stdout)
        self.fzf_log.unlink()
        self.steps({"key": "f1"}, {"key": "", "select": ["touch"]}, {"abort": True}, {"abort": True})
        r = self.run_pin(input="\n")
        self.assertIn('touch = ""', (self.home / ".config" / "claude-pins" / "keys.toml").read_text())
        self.assertIn("✓ Touch transcript: (unbound)", self.header_after(2))

    def test_new_pin_cancel_taken_and_empty(self):
        self.steps({"key": "alt-n"}, {"abort": True}, {"abort": True})
        self.run_pin()
        self.assertEqual(len(self.fzf_calls()), 3)
        sid4 = "44444444-4444-4444-4444-444444444444"
        self.make_session(sid4, age_days=0.1, title="Fresh")
        self.steps({"key": "alt-n"}, {"key": "", "select": [sid4]}, {"abort": True})
        r = self.run_pin(input="rc-mower\n")  # taken alias, then EOF cancels
        self.assertIn("✗ alias rc-mower is taken", r.stdout)
        self.assertNotIn("fresh", self.stored())
        for t in (self.t1, self.t2, self.t3, self.project_dir(str(self.home / "git" / "proj")) / f"{sid4}.jsonl"):
            t.unlink()
        self.steps({"key": "alt-n"}, {"abort": True})
        self.run_pin()
        self.assertIn("no sessions found under ~/.claude/projects", self.header_after())

    def test_prune_cancel_and_undo_nothing(self):
        self.steps({"key": "alt-z"}, {"key": "alt-p"}, {"key": "alt-p"}, {"abort": True})
        r = self.run_pin(input="n\n")
        self.assertIn("nothing to undo", self.header_after(1))
        self.assertIn("nothing to prune", self.header_after(2))
        self.t3.unlink(); self.fzf_log.unlink()
        self.steps({"key": "alt-p"}, {"key": "alt-p"}, {"abort": True})
        r = self.run_pin(input="n\n")  # declined, then EOF
        self.assertIn("prune cancelled", self.header_after(1))
        self.assertIn("prune cancelled", self.header_after(2))
        self.assertIn("rc-mower", self.stored())

    def test_transcript_swept_while_picker_open(self):
        """Claude's retention sweep can delete a transcript while the picker sits open: enter then flashes."""
        self.steps({"key": "", "select": ["standup-prep"], "unlink": str(self.t1)}, {"abort": True})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ standup-prep: transcript for session 11111111… is gone (expired) · pin unpin standup-prep",
                      self.header_after())
        self.assertIsNone(self.claude_calls())
        rows = self.pin_rows(self.fzf_calls()[-1])
        self.assertFalse(any(r.startswith("standup-prep\t") for r in rows))  # the redraw already hides it
