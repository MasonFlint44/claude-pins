"""The built-in picker: the decoder, the matcher, the frames it draws, and the flows through it.

The frames come from ``tui.Session`` run in process over a ``ScriptedTerminal``; the flows run ``bin/pin``
with the scripted terminal wired in (``TuiSandbox``), the way the fzf flows run it with the fzf stub.
``tests/test_tui_pty.py`` drives the real raw-mode terminal.
"""

from __future__ import annotations

import json
import os
import shutil
import unittest

from claude_pins import keys, query, tui
from claude_pins.keymap import _KEY_RE, _SPECIAL
from claude_pins.keys import Key, Mouse, Reader, decode, encode
from claude_pins.screen import Header, Hook, Item, Screen
from tests.helpers import TuiSandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"
MOUSE_NAMES = {"double-click", "left-click", "right-click", "scroll-up", "scroll-down", "preview-scroll-up",
               "preview-scroll-down"}


class DecoderTests(unittest.TestCase):
    TABLE = [
        (b"\r", "enter"), (b"\t", "tab"), (b"\x1b[Z", "shift-tab"), (b"\x7f", "bspace"), (b"\x08", "ctrl-h"),
        (b"\x00", "ctrl-space"), (b"\x01", "ctrl-a"), (b"\x1a", "ctrl-z"), (b"\x1c", "ctrl-\\"), (b"\x1f", "ctrl-/"),
        (b"\x1b[A", "up"), (b"\x1bOA", "up"), (b"\x1b[B", "down"), (b"\x1b[C", "right"), (b"\x1bOD", "left"),
        (b"\x1b[H", "home"), (b"\x1bOH", "home"), (b"\x1b[1~", "home"), (b"\x1b[7~", "home"),
        (b"\x1b[F", "end"), (b"\x1bOF", "end"), (b"\x1b[4~", "end"), (b"\x1b[8~", "end"),
        (b"\x1b[2~", "insert"), (b"\x1b[3~", "del"), (b"\x1b[5~", "pgup"), (b"\x1b[6~", "pgdn"),
        (b"\x1bOP", "f1"), (b"\x1bOS", "f4"), (b"\x1b[11~", "f1"), (b"\x1b[15~", "f5"), (b"\x1b[17~", "f6"),
        (b"\x1b[19~", "f8"), (b"\x1b[20~", "f9"), (b"\x1b[24~", "f12"),
        (b"\x1bt", "alt-t"), (b"\x1bZ", "alt-Z"), (b"\x1b\x14", "ctrl-alt-t"), (b"\x1b\x12", "ctrl-alt-r"),
        (b"\x1b ", "alt-space"), (b"\x1b\r", "alt-enter"), (b"\x1b\x7f", "alt-bspace"), (b"\x1b\x08", "ctrl-alt-bspace"),
        (b"\x1b[1;2A", "shift-up"), (b"\x1b[1;2B", "shift-down"), (b"\x1b[1;3B", "alt-down"), (b"\x1b\x1b[A", "alt-up"),
        (b"\x1b[1;5C", "ctrl-right"), (b"\x1b[1;6D", "ctrl-shift-left"), (b"\x1b[1;7A", "ctrl-alt-up"),
        (b"\x1b[1;4B", "alt-shift-down"), (b"\x1b[1;5H", "ctrl-home"), (b"\x1b[3;2~", "shift-del"), (b"\x1b[3;5~", "ctrl-del"),
        (b"\x1b\x1b", "esc"), (b"\x1b[200~", "paste-begin"),
    ]

    def test_table(self):
        for raw, name in self.TABLE:
            ev, n = decode(raw)
            self.assertEqual((ev, n), (Key(name), len(raw)), raw)

    def test_characters(self):
        self.assertEqual(decode(b"a"), (Key("char", "a"), 1))
        self.assertEqual(decode("é".encode()), (Key("char", "é"), 2))
        self.assertEqual(decode("🟢".encode()), (Key("char", "🟢"), 4))
        self.assertEqual(decode(b"\xff"), (Key("esc"), 1))                     # a bad rune reads as esc, as in fzf
        self.assertEqual(decode("🟢".encode()[:2]), (None, 0))                 # the rest is still coming

    def test_partial_sequences_wait(self):
        for raw in (b"\x1b", b"\x1b[", b"\x1b[1;", b"\x1b[<0;5", b"\x1bO"):
            self.assertEqual(decode(raw), (None, 0), raw)
        self.assertEqual(decode(b"\x1b[99~")[0], None)                         # unknown: dropped, bytes consumed
        self.assertEqual(decode(b"\x1b[99~")[1], 5)

    def test_mouse_reports(self):
        ev, n = decode(b"\x1b[<0;5;7M")
        self.assertEqual((ev, n), (Mouse(4, 6, "left", True), 9))
        self.assertEqual(decode(b"\x1b[<0;5;7m")[0], Mouse(4, 6, "left", False))
        self.assertEqual(decode(b"\x1b[<2;1;1M")[0], Mouse(0, 0, "right", True))
        self.assertEqual(decode(b"\x1b[<64;5;7M")[0], Mouse(4, 6, "", True, 1))
        self.assertEqual(decode(b"\x1b[<65;5;7M")[0], Mouse(4, 6, "", True, -1))
        self.assertEqual(decode(b"\x1b[<4;5;7M")[0], Mouse(4, 6, "left", True, shift=True))
        self.assertEqual(decode(b"\x1b[<0;x;7M"), (None, 6))
        self.assertEqual(keys.mouse_report(4, 6), b"\x1b[<0;5;7M")
        self.assertEqual(keys.mouse_report(4, 6, scroll=-1), b"\x1b[<65;5;7M")

    def test_every_keymap_name_decodes(self):
        """The names the keymap file accepts (fzf's) all come out of the decoder under the same name."""
        names = [n for n in _SPECIAL if n not in MOUSE_NAMES and n != "esc"]     # esc alone is the reader's call
        names += ["alt-t", "alt-Z", "alt-9", "ctrl-a", "ctrl-alt-r", "alt-/", "alt-\\", "alt-]", "alt-^", "f12"]
        for name in names:
            self.assertTrue(name in _SPECIAL or _KEY_RE.match(name), name)
            raw = encode(name)
            ev, n = decode(raw)
            want = Key("char", " ") if name == "space" else Key(keys.canonical(name))
            self.assertEqual((ev, n), (want, len(raw)), name)

    def test_esc_alone_needs_the_delay(self):
        class Fake:
            def __init__(self, chunks):
                self.chunks, self.t, self.waits = list(chunks), 0.0, []

            def read(self, timeout):
                if self.chunks:
                    c = self.chunks.pop(0)
                    if c is None:               # nothing came: the wait ran out
                        self.waits.append(timeout); self.t += timeout
                        return b""
                    return c
                self.t += timeout or 0
                return b""

            def clock(self):
                return self.t

        f = Fake([b"\x1b", None, b"x"])                      # esc, a pause, then x
        r = Reader(f.read, esc_delay=0.1, clock=f.clock)
        self.assertEqual(r.next(), Key("esc"))
        self.assertEqual(f.waits, [0.1])
        self.assertEqual(r.next(), Key("char", "x"))
        f = Fake([b"\x1b", b"x"])                            # x within the delay: alt-x
        self.assertEqual(Reader(f.read, esc_delay=0.1, clock=f.clock).next(), Key("alt-x"))
        f = Fake([b"\x1b[", None, b"\x1b[A"])                # a partial sequence left alone is dropped
        r = Reader(f.read, esc_delay=0.1, clock=f.clock)
        self.assertEqual(r.next(), Key("up"))
        f = Fake([b"\x1bt\x1b[B"])                           # several events in one read
        r = Reader(f.read, esc_delay=0.1, clock=f.clock)
        self.assertEqual([r.next(), r.next()], [Key("alt-t"), Key("down")])
        os.environ["ESCDELAY"] = "250"
        try:
            self.assertEqual(Reader(f.read).esc_delay, 0.25)
        finally:
            del os.environ["ESCDELAY"]
        self.assertEqual(Reader(f.read).esc_delay, keys.DEFAULT_ESC_DELAY)

    def test_double_click_timing(self):
        t = [0.0]
        chunks = [keys.mouse_report(3, 8), keys.mouse_report(3, 8), keys.mouse_report(3, 8), keys.mouse_report(3, 9)]
        r = Reader(lambda timeout: chunks.pop(0) if chunks else b"", clock=lambda: t[0])
        self.assertFalse(r.next().double)
        t[0] = 0.2
        self.assertTrue(r.next().double)                    # the second press within half a second
        t[0] = 1.0
        self.assertFalse(r.next().double)                   # too late
        t[0] = 1.1
        self.assertFalse(r.next().double)                   # another cell


class MatcherTests(unittest.TestCase):
    ROW = "rc-mower\t Navimow schedule debug\t ~/git/mower\t 26d\t ⏳"

    def keeps(self, q, nth="1..3"):
        return query.matches(q, self.ROW, nth)[0]

    def test_syntax(self):
        self.assertTrue(self.keeps("navi"))
        self.assertTrue(self.keeps("nvmw"))                                     # fuzzy: in order, anywhere
        self.assertFalse(self.keeps("wmvn"))
        self.assertTrue(self.keeps("sched deb"))                                # every term
        self.assertFalse(self.keeps("sched zzz"))
        self.assertTrue(self.keeps("zzz | sched"))                              # either
        self.assertTrue(self.keeps("'mower")); self.assertFalse(self.keeps("'mwr"))   # exact
        self.assertTrue(self.keeps("^rc")); self.assertFalse(self.keeps("^mower"))    # prefix
        self.assertTrue(self.keeps("!zzz")); self.assertFalse(self.keeps("!navi"))    # not
        self.assertTrue(self.keeps("!'mwr"))
        self.assertTrue(self.keeps("Navi")); self.assertFalse(self.keeps("NAVI"))     # smart case
        self.assertTrue(self.keeps("schedule\\ debug"))                        # an escaped space
        self.assertTrue(self.keeps(""))
        self.assertTrue(self.keeps("   "))
        self.assertEqual(query.matches("abc", "xaxbxc")[1], {1, 3, 5})
        self.assertEqual(query.matches("'bx", "xaxbxc")[1], {3, 4})
        self.assertEqual(query.matches("c$", "abc")[1], {2})
        self.assertEqual(query.matches("^ab$", "ab")[0], True)
        self.assertEqual(query.matches("^ab$", "abc")[0], False)

    def test_nth_scopes_the_columns(self):
        self.assertFalse(self.keeps("'26d"))                                    # the idle column is outside 1..3
        self.assertFalse(self.keeps("⏳"))
        self.assertTrue(self.keeps("'26d", nth=None))
        self.assertTrue(self.keeps("git", nth="1..3")); self.assertFalse(self.keeps("git", nth="1..2"))
        self.assertTrue(self.keeps("navi", nth="2")); self.assertFalse(self.keeps("rc-mower", nth="2"))
        self.assertTrue(self.keeps("⏳", nth="-1"))
        self.assertEqual(query.nth_range("a\tb\tc", "1..2"), (0, 4))
        self.assertEqual(query.nth_range("a\tb\tc", "..2"), (0, 4))
        self.assertEqual(query.nth_range("a\tb\tc", "2.."), (2, 5))
        self.assertEqual(query.nth_range("a\tb\tc", "9"), (4, 5))


class FrameTests(TuiSandbox):
    """One screen at a time, in process: what the built-in picker draws for a Screen and what it answers."""

    def setUp(self):
        super().setUp()
        os.environ["COLUMNS"] = "60"
        os.environ["LINES"] = "12"

    def run_screen(self, screen: Screen, *steps, rows: int | None = None, cols: int | None = None):
        self.steps(*steps)
        term = tui.ScriptedTerminal(str(self.script), str(self.tui_log))
        if rows:
            term.rows = rows
        if cols:
            term.cols = cols
        result = tui.Session(screen, term).run()
        return result, self.screens()[-1]["frame"]

    def rows(self, n=4):
        return [Item("-", "alias\tvalue")] + [Item(f"r{i}", f"row{i}\tvalue {i}") for i in range(1, n + 1)]

    def test_list_chrome(self):
        legend = "🟢 open  🚩 keep  🔀 fork  🌳 worktree"
        screen = Screen(self.rows(), prompt="pins › ", header=Header("enter open · esc back", legend=legend),
                        header_lines=1, nth="1..2", counter="pins", multi=True)
        res, frame = self.run_screen(screen, ["@down", "@tab", "@enter"])
        self.assertEqual(res.ids, ["r2"])
        self.assertEqual(res.key, "")
        self.assertEqual(frame[:3], ["  " + legend, "  enter open · esc back", ""])   # legend, hints (stacked), status
        self.assertRegex(frame[3], r"^pins ›\s+4 pins · 1 selected$")
        self.assertEqual(frame[4], "")                                          # the gap row under the prompt
        self.assertEqual(frame[5], "  alias value")                             # the sticky label row, tab as a space
        self.assertEqual(frame[6], "▌ row1 value 1")
        self.assertEqual(frame[7], "▌▌row2 value 2")                            # selected, and the cursor moved on
        self.assertEqual(frame[8], "> row3 value 3")

    def test_query_filters_counts_and_highlights(self):
        screen = Screen(self.rows(), prompt="> ", header_lines=1, nth="1..2", counter="pins")
        res, frame = self.run_screen(screen, ["3", "@enter"])
        self.assertEqual((res.query, res.ids), ("3", ["r3"]))
        self.assertRegex(frame[0], r"^> 3\s+1 of 4 pins$")
        self.assertEqual([l for l in frame[3:] if l], ["> row3 value 3"])
        res, frame = self.run_screen(screen, ["zzz", "@enter"])
        self.assertEqual((res.query, res.ids), ("zzz", []))                     # enter with no match: the query alone
        self.assertRegex(frame[0], r"0 of 4 pins$")
        res, _ = self.run_screen(screen, ["'value 2", "@enter"])
        self.assertEqual(res.ids, ["r2"])

    def test_expect_esc_and_pos(self):
        screen = Screen(self.rows(), prompt="> ", header_lines=1, expect=["alt-t", "f1"], pos=3)
        res, _ = self.run_screen(screen, ["@alt-t"])
        self.assertEqual((res.key, res.ids), ("alt-t", ["r3"]))
        res, _ = self.run_screen(screen, ["@f1"])
        self.assertEqual(res.key, "f1")
        for leave in ("@esc", "@ctrl-c", "@ctrl-g"):
            res, _ = self.run_screen(screen, [leave, "@pause"])
            self.assertIsNone(res, leave)
        res, _ = self.run_screen(screen, [])                                    # the script runs out: as esc
        self.assertIsNone(res)

    def test_movement(self):
        screen = Screen(self.rows(), prompt="> ", header_lines=1)
        for seq, want in ((["@up"], "r4"), (["@down"] * 4, "r1"), (["@ctrl-n", "@ctrl-p"], "r1"),
                          (["@pgdn"], "r4"), (["@pgdn", "@pgdn"], "r4"), (["@ctrl-j", "@ctrl-j", "@ctrl-k"], "r2"),
                          (["@end"], "r1")):
            res, _ = self.run_screen(screen, seq + ["@enter"])
            self.assertEqual(res.ids, [want], seq)

    def test_query_editing(self):
        screen = Screen([], prompt="title › ", query="Standup prep", disabled=True)
        cases = [(["@enter"], "Standup prep"), ([" (Tue)", "@enter"], "Standup prep (Tue)"),
                 (["@ctrl-u", "new", "@enter"], "new"), (["@ctrl-w", "@ctrl-w", "x", "@enter"], "x"),
                 (["@ctrl-a", "A ", "@enter"], "A Standup prep"), (["@home", "@del", "@enter"], "tandup prep"),
                 (["@bspace", "@bspace", "@enter"], "Standup pr"), (["@left", "@left", "X", "@enter"], "Standup prXep"),
                 (["@ctrl-a", "@alt-f", "@alt-d", "@enter"], "Standup"), (["@alt-b", "@ctrl-k", "@enter"], "Standup prep"),
                 (["@ctrl-u", "@ctrl-y", "@enter"], "Standup prep"), (["@ctrl-e", "!", "@enter"], "Standup prep!")]
        for seq, want in cases:
            res, frame = self.run_screen(screen, seq)
            self.assertEqual(res.query, want, seq)
        self.assertEqual(frame[0], "title › Standup prep!")                     # a text field: the query line, no rows
        self.assertEqual(frame[1:], [""] * (len(frame) - 1))
        res, _ = self.run_screen(Screen([], prompt="> ", disabled=True), ["@ctrl-d"])
        self.assertIsNone(res)                                                  # ctrl-d on an empty query leaves

    def test_multi_select_order_and_shift_tab(self):
        screen = Screen(self.rows(), prompt="> ", header_lines=1, multi=True)
        res, _ = self.run_screen(screen, ["@down", "@down", "@tab", "@shift-tab", "@enter"])
        self.assertEqual(res.ids, ["r3", "r4"])                                 # in selection order, as fzf prints
        res, _ = self.run_screen(screen, ["@tab", "@tab", "@shift-tab", "@shift-tab", "@enter"])
        self.assertEqual(res.ids, ["r1", "r3"])                                 # r2 toggled twice: off again
        res, _ = self.run_screen(screen, ["@tab", "@up", "@tab", "@enter"])     # toggle twice: nothing selected
        self.assertEqual(res.ids, ["r2"])
        res, _ = self.run_screen(Screen(self.rows(), prompt="> ", header_lines=1), ["@tab", "@enter"])
        self.assertEqual(res.ids, ["r1"])                                       # tab does nothing without multi

    def test_cycle_and_scrolling(self):
        screen = Screen(self.rows(30), prompt="> ", header_lines=1)
        res, frame = self.run_screen(screen, ["@up", "@enter"])
        self.assertEqual(res.ids, ["r30"])
        self.assertTrue(any(l.startswith("> row30") for l in frame))
        self.assertTrue(any(l.endswith("│") for l in frame))                    # a scrollbar on the right
        res, frame = self.run_screen(screen, ["@down"] * 12 + ["@enter"])
        self.assertEqual(res.ids, ["r13"])
        self.assertEqual([l for l in frame if l.startswith("> row")], ["> row13 value 13"])
        self.assertTrue(any(l.startswith("▌ row6") for l in frame))            # scrolled so the cursor stays visible

    def test_mouse(self):
        screen = Screen(self.rows(), prompt="> ", header_lines=1, multi=True, expect=["right-click"])
        res, _ = self.run_screen(screen, ["@click:2", "@enter"])
        self.assertEqual(res.ids, ["r3"])
        res, _ = self.run_screen(screen, ["@dblclick:1"])
        self.assertEqual((res.key, res.ids), ("", ["r2"]))                      # a double-click accepts
        res, _ = self.run_screen(screen, ["@rclick:3"])
        self.assertEqual((res.key, res.ids), ("right-click", ["r4"]))           # bound in the keymap: it ends the screen
        screen.expect = []
        res, _ = self.run_screen(screen, ["@rclick:3", "@enter"])
        self.assertEqual(res.ids, ["r4"])                                       # unbound: it toggles (selected → printed)
        res, _ = self.run_screen(screen, ["@wheel-down", "@wheel-down", "@wheel-up", "@enter"])
        self.assertEqual(res.ids, ["r2"])
        res, _ = self.run_screen(Screen(self.rows(), prompt="> ", header_lines=1, expect=["double-click"]), ["@dblclick:0"])
        self.assertEqual(res.key, "double-click")

    def test_preview_pane_and_its_threshold(self):
        self.make_session(SID1, cwd=str(self.home / "git" / "proj"), age_days=1, title="Standup prep")
        self.run_pin("add", SID1, "sp")
        screen = Screen([Item("-", "alias"), Item("sp", "sp\tStandup prep")], prompt="> ", header_lines=1,
                        preview=Hook("preview"), label_from_row=True)
        res, frame = self.run_screen(screen, ["@enter"], rows=20)
        pane = [l for l in frame if l.startswith("│") or l.startswith("╭") or l.startswith("╰")]
        self.assertEqual(len(pane), 11)                                         # 55% of 20 rows
        self.assertRegex(pane[0], r"^╭─+ sp ─+╮$")
        self.assertIn("│ dir        ~/git/proj", "\n".join(pane))
        self.assertIn("│ cusage@latest", "\n".join(pane))                       # long lines wrap inside the pane
        res, frame = self.run_screen(screen, ["@enter"], rows=18)               # 18 → 9 rows: under ten, hidden
        self.assertFalse(any(l.startswith("╭") for l in frame))
        res, frame = self.run_screen(Screen(screen.items, prompt="> ", header_lines=1, preview=Hook("preview"),
                                            preview_label=" draft ", footer=" 1 expired "), ["@enter"], rows=20)
        self.assertRegex([l for l in frame if l.startswith("╭")][0], r"^╭─+ draft ─+╮$")
        self.assertRegex(frame[-1], r"^── 1 expired ─+$")                        # the bottom border's label
        self.assertEqual(len([l for l in frame if l.startswith("│")]), 8)       # 19 rows left → 10 rows, 8 inside
        res, frame = self.run_screen(screen, ["@shift-down", "@shift-down", "@enter"], rows=14)
        self.assertFalse(any(l.startswith("╭") for l in frame))

    def test_preview_scrolls(self):
        text = "\n".join(f"line {i}" for i in range(1, 40))
        self.make_session(SID1, age_days=1, title="Long", answer=text, n_turns=1)
        self.run_pin("add", SID1, "long")
        screen = Screen([Item("long", "long")], prompt="> ", preview=Hook("preview"), label_from_row=True)
        res, frame = self.run_screen(screen, ["@shift-down", "@shift-down", "@enter"], rows=24)
        body = [l for l in frame if l.startswith("│")]
        self.assertTrue(body[0].startswith("│ dir") and "3/17" in body[0], body[0])   # scrolled two lines: fzf's position
        res, frame = self.run_screen(screen, ["@pwheel-down", "@pwheel-up", "@enter"], rows=24)
        self.assertIn("1/17", [l for l in frame if l.startswith("│")][0])

    def test_header_fits_the_width(self):
        from claude_pins.render import legend
        hints = "enter open · ctrl-x actions · f2 edit · ctrl-t new · alt-i details · f1 help"
        screen = Screen(self.rows(), prompt="> ", header=Header(hints, legend=legend(), note="preview hidden: terminal too short"),
                        header_lines=1, preview=Hook("preview"))
        res, frame = self.run_screen(screen, ["@enter"], cols=147, rows=30)
        self.assertRegex(frame[0], r"^  🟢 open .*🔴 expired    enter open .* f1 help$")   # one line, four cells between
        self.assertEqual(frame[1], "")
        self.assertEqual(frame[2], ">")                                         # the prompt line (frames are rstripped)
        res, frame = self.run_screen(screen, ["@enter"], cols=146, rows=30)
        self.assertTrue(frame[0].startswith("  🟢 open") and frame[1].startswith("  enter open"))   # one short: stacked
        res, frame = self.run_screen(screen, ["@enter"], cols=100, rows=30)
        self.assertTrue(frame[0].startswith("  🟢 open") and frame[1].startswith("  enter open"))   # stacked
        self.assertEqual(frame[2], "")
        res, frame = self.run_screen(screen, ["@enter"], cols=100, rows=16)
        self.assertEqual(frame[2], "  preview hidden: terminal too short")     # the note takes the status line
        screen.header = Header(hints, legend=legend(), status="✓ saved")
        res, frame = self.run_screen(screen, ["@enter"], cols=100, rows=16)
        self.assertEqual(frame[2], "  ✓ saved")

    def test_resize_reloads_the_rows(self):
        self.make_session(SID1, cwd=str(self.home / "git" / "proj"), age_days=1, title="Standup prep")
        self.run_pin("add", SID1, "standup-prep")
        from claude_pins.hooks import pin_rows
        items = pin_rows("recency", False, width=60)
        screen = Screen(items, prompt="> ", header_lines=1, nth="1..3", reload=Hook("rows", ("recency", "")),
                        refresh_key="ctrl-r", preview=Hook("preview"), label_from_row=True)
        res, frame = self.run_screen(screen, ["@resize:30x120", "@enter"], cols=60, rows=12)
        self.assertEqual(len(frame), 30)
        self.assertRegex(frame[3], r"^> standup-prep  Standup prep\s+~/git/proj\s+1d$")
        self.assertTrue(any(l.startswith("╭") for l in frame))                  # the pane fits after the resize
        self.assertGreater(len(frame[2]), 40)                                   # the label row laid out for 120 columns
        # the refresh key reloads: a pin added meanwhile appears
        self.make_session(SID2, age_days=0.5, title="Late")
        self.run_pin("add", SID2, "late")
        res, frame = self.run_screen(screen, ["@ctrl-r", "@enter"], cols=60, rows=12)
        self.assertTrue(any(l.startswith("▌ late") for l in frame))
        self.assertEqual(res.ids, ["standup-prep"])                              # the cursor stayed on its row

    def test_on_change_reloads_the_directory_rows(self):
        (self.home / "git" / "alpha").mkdir(parents=True); (self.home / "git" / "alps").mkdir()
        screen = Screen([], prompt="cwd › ", query="~/git/", disabled=True, on_change=Hook("dirs"))
        res, frame = self.run_screen(screen, ["al", "@enter"])
        self.assertEqual((res.query, res.ids), ("~/git/al", ["~/git/alpha/"]))
        self.assertEqual([l for l in frame[2:] if l], ["> ~/git/alpha/", "▌ ~/git/alps/"])
        res, _ = self.run_screen(screen, ["alx", "@enter"])
        self.assertEqual((res.query, res.ids), ("~/git/alx", []))               # nothing completes: the text comes back

    def test_painting_through_a_terminal_emulator(self):
        """What the backend writes, run through pyte, is the frame it composed; the current row, the
        pointer and the matches carry fzf's colours."""
        try:
            import pyte
        except ImportError:
            self.skipTest("pyte is not installed (uv sync --group dev)")
        os.environ.pop("NO_COLOR"); os.environ["CLAUDE_PINS_COLOR"] = "1"
        written = []

        class Capturing(tui.ScriptedTerminal):
            def write(self, text):
                written.append(text)

        from claude_pins.theme import Palette
        color = Palette(True)
        items = [Item("-", "alias\tvalue"), Item("r1", f"{color('row1', 'bold')}\t{color('value 1', 'dim')}"),
                 Item("r2", "row2\tvalue 2")]
        self.steps(["2", "@enter"])
        term = Capturing(str(self.script), str(self.tui_log))
        term.rows, term.cols = 8, 40
        tui.Session(Screen(items, prompt="> ", header_lines=1, nth="1..2"), term).run()
        screen = pyte.Screen(40, 8)
        pyte.ByteStream(screen).feed("".join(written).encode())
        self.assertEqual([l.rstrip() for l in screen.display][:4], ["> 2", "", "  alias value", "> row2 value 2"])
        row = screen.buffer[3]
        self.assertEqual((row[0].fg, row[0].bold, row[0].bg), ("b1b9f9", True, "303030"))      # the pointer, periwinkle on 236
        self.assertEqual((row[2].fg, row[2].bold, row[2].bg), ("e4e4e4", True, "303030"))      # text: bold 254 on 236
        self.assertEqual(row[5].fg, "b1b9f9")                                                   # the matched "2"
        self.assertEqual(row[20].bg, "default")                                                 # the padding is not painted
        os.environ["NO_COLOR"] = "1"; del os.environ["CLAUDE_PINS_COLOR"]
        written.clear()
        self.steps(["2", "@enter"])
        tui.Session(Screen(items, prompt="> ", header_lines=1, nth="1..2"), Capturing(str(self.script), str(self.tui_log))).run()
        screen = pyte.Screen(40, 8)
        pyte.ByteStream(screen).feed("".join(written).encode())
        row = screen.buffer[3]
        self.assertEqual((row[0].fg, row[0].bg, row[0].bold), ("default", "default", True))     # NO_COLOR: attributes only
        self.assertEqual((row[5].fg, row[2].bold), ("default", True))


class FlowTests(TuiSandbox):
    """``pin`` end to end through the built-in picker: the screens the menu used to stand in for."""

    def setUp(self):
        super().setUp()
        self.t1 = self.make_session(SID1, age_days=2, title="Standup prep")
        self.t2 = self.make_session(SID2, cwd=str(self.home), age_days=26, title="Navimow schedule debug")
        self.run_pin("add", SID1, "standup-prep"); self.run_pin("add", SID2, "rc-mower")

    def stored(self):
        return {p["alias"]: p for p in json.loads(self.store_path().read_text())["pins"]}

    def test_render_and_open(self):
        self.steps(["@down", "@enter"])
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID2])
        frame = self.frame(0)                                                   # the last frame: after the down key
        self.assertRegex(frame, r"\n  alias\s+title\s+directory\s+idle\n▌ standup-prep\s+Standup prep\s+~/git/proj\s+2d\n")
        self.assertRegex(frame, r"\n> rc-mower\s+Navimow schedule debug\s+~\s+26d  ⏳\n")
        self.assertIn("🟢 open  🚩 keep  🔀 fork  🌳 worktree  ⏳ expiring  🔴 expired", frame)
        self.assertIn("enter open · ctrl-x actions · f2 edit · ctrl-t new · alt-i details · f1 help", frame)
        self.assertRegex(frame, r"📌 pins ›\s+2 pins\n")
        self.assertIn("│ session    " + SID2, frame)                            # the pane follows the cursor
        self.assertNotIn("install fzf", frame)                                  # no nagging on the screen
        self.assertEqual(r.stdout, "")

    def test_query_words_prefilter_and_fork(self):
        self.steps(["@alt-o"])
        r = self.run_pin("e")                                                   # two hits: the picker, prefiltered
        self.assertEqual(self.screens()[0]["query"], "e")
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--fork-session"])
        self.steps(["@alt-w"])
        self.run_pin()
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1, "--worktree"])

    def test_touch_unpin_undo_prune_flashes(self):
        self.steps(["@down", "@alt-t"], ["@alt-x"], ["@alt-z"], ["@alt-p"], ["@esc", "@pause"])
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        headers = [s["header"].split("\n")[-1] for s in self.screens()]
        self.assertEqual(headers[1], "✓ touched rc-mower")
        self.assertEqual(headers[2], "✓ unpinned rc-mower · alt-z undo")         # touched → newest → the cursor stayed on it
        self.assertEqual(headers[3], "✓ restored rc-mower (unpin)")
        self.assertEqual(headers[4], "nothing to prune")
        self.assertIsNone(self.claude_calls())
        self.assertEqual(sorted(self.stored()), ["rc-mower", "standup-prep"])
        self.steps(["@esc", "@pause"])
        r = self.run_pin(env={"CLAUDE_PINS_GLYPHS": "text"})
        self.assertIn("● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⧗ expiring  ✗ expired", self.frame())

    def test_new_pin_and_sort(self):
        self.make_session(SID3, cwd=str(self.home / "Documents"), age_days=0.2, title="Tax prep questions")
        self.steps(["@ctrl-t"], ["@enter"], ["@enter"], ["@alt-s"], ["@esc", "@pause"])
        r = self.run_pin()
        screens = self.screens()
        self.assertEqual(screens[1]["prompt"], "📌 pins › new › ")
        self.assertRegex(screens[1]["frame"][2], r"^📌 pins ›  new ›\s+3 sessions$".replace("›  new", "› new"))
        self.assertRegex(screens[1]["frame"][4], r"^  title\s+directory\s+idle\s+msgs\s+pin$")
        self.assertRegex(screens[1]["frame"][5], r"^> Tax prep questions\s+~/Documents")
        self.assertRegex(screens[1]["frame"][6], r"Standup prep\s+.*📌 standup-prep$")
        self.assertIn("│ session    " + SID3, "\n".join(screens[1]["frame"]))   # the session's preview
        self.assertEqual(screens[2]["prompt"], "📌 pins › new › alias › ")
        self.assertEqual(screens[2]["query"], "tax-prep-questions")
        self.assertEqual(screens[3]["header"].split("\n")[-1], "✓ pinned as tax-prep-questions")
        self.assertTrue(screens[3]["frame"][6].startswith("> tax-prep-questions"))      # the cursor on the new pin
        self.assertEqual(screens[4]["header"].split("\n")[-1], "sort: alias")
        self.assertIn("tax-prep-questions", self.stored())

    def test_new_pin_edge_cases(self):
        self.steps(["@ctrl-t"], ["@esc", "@pause"], ["@esc", "@pause"])          # esc on the session list
        r = self.run_pin()
        self.assertNotIn("✓ pinned", self.screens()[2]["header"])
        self.steps(["@ctrl-t"], ["@enter"], ["@esc", "@pause"])                  # row 1 is the newest session: pinned
        r = self.run_pin()
        self.assertEqual(self.screens()[2]["header"].split("\n")[-1], "already pinned as standup-prep")
        self.make_session(SID3, age_days=0.1, title="Fresh")
        self.steps(["@ctrl-t"], ["@enter"], ["@ctrl-u", "rc-mower", "@enter"], ["@esc", "@pause"], ["@esc", "@pause"])
        r = self.run_pin()                                                      # a taken alias: the field asks again
        screens = self.screens()
        self.assertEqual(screens[3]["prompt"], "📌 pins › new › alias › ")
        self.assertIn("✗ alias rc-mower is taken", screens[3]["header"])
        self.assertNotIn("rc-mower-2", self.stored())
        self.t1.unlink(); self.t2.unlink(); (self.project_dir(str(self.home / "git" / "proj")) / f"{SID3}.jsonl").unlink()
        self.steps(["@ctrl-t"], ["@esc", "@pause"])
        r = self.run_pin()
        self.assertIn("no sessions found under", self.screens()[1]["header"])

    def test_edit_through_the_picker(self):
        self.steps(["@f2"], ["@enter"], [" (Tue)", "@enter"], ["@down"] * 8 + ["@enter"], ["@alt-s"], ["@esc", "@pause"])
        r = self.run_pin()
        screens = self.screens()
        self.assertEqual(screens[1]["prompt"], "📌 pins › standup-prep › edit › ")
        self.assertEqual(screens[2]["prompt"], "📌 pins › standup-prep › edit › title › ")
        self.assertEqual(screens[2]["result"]["query"], "Standup prep (Tue)")
        self.assertEqual(screens[3]["prompt"], "📌 pins › standup-prep › edit (unsaved) › ")
        self.assertRegex("\n".join(screens[3]["frame"]), r"title \*\s+\*Standup prep \(Tue\)")   # the row's star
        self.assertIn("* Standup prep (Tue)", "\n".join(screens[3]["frame"]))                    # and the pane's mark
        self.assertEqual(screens[5]["header"].split("\n")[-1], "✓ saved standup-prep")
        p = self.stored()["standup-prep"]
        self.assertEqual(p["title"], "Standup prep (Tue)"); self.assertTrue(p["keep"])

    def test_editor_fields_and_cancel(self):
        (self.home / "git" / "cc").mkdir(parents=True)
        # cwd; model typed in (the list wraps upward to "type a model name"); effort medium, then cleared; save
        self.steps(["@down", "@down", "@down", "@enter"], ["@ctrl-u", "~/git/c", "@enter"],
                   ["@down", "@down", "@enter"], ["@up", "@up", "@enter"], ["claude-opus-5", "@enter"],
                   ["@down", "@enter"], ["@down", "@enter"], ["@enter"], ["@up", "@up", "@enter"], ["@alt-s"])
        r = self.run_pin("edit", "standup-prep")
        self.assertEqual(r.returncode, 0, r.stderr)
        screens = self.screens()
        self.assertEqual(screens[1]["prompt"], "📌 pins › standup-prep › edit › cwd › ")
        self.assertEqual(screens[1]["result"]["ids"], ["~/git/cc/"])            # the completion under the typed text
        self.assertEqual(screens[3]["prompt"], "📌 pins › standup-prep › edit › model › ")
        self.assertEqual([l.split("\t")[1] for l in screens[3]["items"]],
                         ["fable", "opus", "sonnet", "haiku", "(type a model name…)", "(clear)"])
        self.assertEqual(screens[6]["prompt"], "📌 pins › standup-prep › edit › effort › ")
        self.assertEqual(screens[6]["result"]["ids"], ["medium"])
        self.assertEqual(screens[8]["result"]["ids"], [""])                     # (clear)
        self.assertIn("✓ saved standup-prep", r.stdout)
        p = self.stored()["standup-prep"]
        self.assertEqual(p["cwd"], str(self.home / "git" / "cc"))
        self.assertEqual(p["launch"], {"model": "claude-opus-5"})
        self.steps(["@down", "@enter"], ["@ctrl-u", "BAD", "@enter"], ["@alt-s"], ["@esc", "@pause"], ["@down", "@enter"])
        r = self.run_pin("edit", "standup-prep")                                # invalid alias refused, then discarded
        self.assertIn("✗ invalid alias 'BAD'", self.screens()[3]["header"])
        self.assertEqual(self.screens()[4]["prompt"], "📌 pins › standup-prep › edit › unsaved › ")
        self.assertIn("standup-prep", self.stored())
        self.assertIn("no changes", r.stdout)
        self.steps(["@enter"], ["@ctrl-u", "@enter"], ["@alt-s"], ["@esc", "@pause"], ["@esc", "@pause"])
        r = self.run_pin("edit", "standup-prep")
        self.assertIn("✗ title is required", self.screens()[3]["header"])

    def test_prune_and_expired(self):
        self.t2.unlink()
        self.steps(["@alt-a"], ["@alt-p"], ["@enter"], ["@alt-z"], ["@esc", "@pause"])
        r = self.run_pin()
        screens = self.screens()
        self.assertEqual(screens[0]["frame"][-1].rstrip("─"), "── 1 expired · alt-a show · pin prune ")
        self.assertRegex("\n".join(screens[1]["frame"]), r"▌ rc-mower\s+Navimow schedule debug\s+~\s+🔴")
        self.assertEqual(screens[1]["frame"][-1].rstrip("─"), "── 1 expired shown · pin prune ")
        self.assertEqual(screens[2]["prompt"], "📌 pins › prune › ")
        self.assertIn("prune 1 expired pin(s): rc-mower", screens[2]["header"])
        self.assertEqual(screens[3]["header"].split("\n")[-1], "✓ pruned 1 · alt-z undo")
        self.assertEqual(screens[4]["header"].split("\n")[-1], "✓ restored rc-mower (prune)")
        self.steps(["@alt-p"], ["@down", "@enter"], ["@esc", "@pause"])         # decline
        r = self.run_pin()
        self.assertEqual(self.screens()[2]["header"].split("\n")[-1], "prune cancelled")
        self.assertEqual(sorted(self.stored()), ["rc-mower", "standup-prep"])

    def test_expired_open_and_errors(self):
        self.t2.unlink()
        self.steps(["@alt-a"], ["@down", "@enter"], ["@esc", "@pause"])
        r = self.run_pin()
        self.assertEqual(self.screens()[2]["header"].split("\n")[-1],
                         "✗ rc-mower: transcript for session 22222222… is gone (expired) · pin unpin rc-mower")
        self.assertIsNone(self.claude_calls())
        self.steps(["@alt-z"], ["@esc", "@pause"])
        r = self.run_pin()
        self.assertEqual(self.screens()[1]["header"].split("\n")[-1], "nothing to undo")

    def test_open_cancelled_and_gone(self):
        shutil.rmtree(self.home / "git" / "proj")
        self.steps(["@enter"], ["@down", "@down", "@enter"], ["@esc", "@pause"])   # missing dir → unpin
        r = self.run_pin()
        screens = self.screens()
        self.assertEqual(screens[1]["prompt"], "📌 pins › standup-prep › open › ")
        self.assertIn("standup-prep: directory ~/git/proj is missing", screens[1]["header"])
        self.assertEqual(screens[2]["header"].split("\n")[-1], "✓ unpinned standup-prep · pin undo restores it · cancelled")
        self.assertNotIn("standup-prep", self.stored())

    def test_empty_store_and_dumb_terminal(self):
        self.run_pin("rm", "standup-prep"); self.run_pin("rm", "rc-mower")
        self.steps(["@esc", "@pause"])
        r = self.run_pin()
        self.assertIn("No pins yet. ctrl-t pins a recent session, or run /pins:pin inside a Claude session.", self.frame())

    def test_usable_needs_two_ttys_and_a_real_term(self):
        import io
        from unittest import mock

        class Tty(io.StringIO):
            def isatty(self):
                return True

        with mock.patch.object(tui.sys, "stdin", Tty()), mock.patch.object(tui.sys, "stdout", Tty()):
            os.environ.pop("CLAUDE_PINS_TUI_SCRIPT")
            os.environ["TERM"] = "xterm-256color"
            self.assertTrue(tui.usable())
            os.environ["TERM"] = "dumb"
            self.assertFalse(tui.usable())
        with mock.patch.object(tui.sys, "stdin", io.StringIO()):
            os.environ["TERM"] = "xterm"
            self.assertFalse(tui.usable())

    def test_transcript_swept_between_draw_and_open(self):
        self.steps({"send": ["@enter"], "unlink": str(self.t1)})
        r = self.run_pin()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.claude_calls())
        self.assertEqual(self.screens()[1]["header"].split("\n")[-1],
                         "✗ standup-prep: transcript for session 11111111… is gone (expired) · pin unpin standup-prep")

    def test_first_run_note_shows_once(self):
        """The built-in picker mentions fzf on the status line the first time it runs and never again (a
        marker file in the cache directory remembers); the help screen keeps one dim line about it."""
        from claude_pins import config
        self.assertFalse(config.noted_file().exists())
        self.steps(["@f1"], ["@esc", "@pause"], ["@esc", "@pause"])
        self.run_pin()
        screens = self.screens()
        self.assertEqual(screens[0]["header"].split("\n")[-1], "built-in picker in use · fzf adds ranked matching · pin doctor")
        self.assertEqual(screens[1]["header"].split("\n")[-2:],
                         ["built-in picker in use · fzf adds ranked matching · pin doctor", " "])   # under the keymap path
        self.assertIn("keymap: ~/.config/claude-pins/keys.toml", screens[1]["header"])
        self.assertEqual(screens[2]["header"].split("\n")[-1], " ")                              # once
        self.assertTrue(config.noted_file().exists())
        self.steps(["@esc", "@pause"])
        self.run_pin()
        self.assertEqual(self.screens()[0]["header"].split("\n")[-1], " ")                       # never again

    def test_no_terminal_cancels_a_question(self):
        shutil.rmtree(self.home / "git" / "proj")
        r = self.run_pin("open", "standup-prep", env={"CLAUDE_PINS_TUI_SCRIPT": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no terminal to answer 📌 pins › standup-prep › open; cancelled", r.stderr)
        self.assertIsNone(self.claude_calls())

    def test_keys_subcommand_needs_a_terminal(self):
        r = self.run_pin("_keys", env={"CLAUDE_PINS_TUI_SCRIPT": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a terminal", r.stderr)


if __name__ == "__main__":
    unittest.main()
