"""Run the real fzf over the rows every screen sends it, with the picker's exact options.

The scripted stub in the rest of the suite plays the user; it cannot tell whether fzf would match a query
against the rows, and that is exactly what broke once (a hidden search field that --nth never saw). Here
each screen is driven in-process with ``fzf.run`` replaced by a recorder, then the recorded items and
options go to a real fzf in ``--filter`` mode, which applies the same matcher as the interactive UI.

The binary comes from ``CLAUDE_PINS_TEST_FZF`` (CI points it at several downloaded versions) or from the
PATH at import time (a developer's own install, before the sandbox replaces PATH). Without one that is
new enough the module is skipped, which the test output says out loud.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
import unittest
from unittest import mock

from tests.helpers import FzfSandbox

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from claude_pins import config, fzf, theme  # noqa: E402

REAL_FZF = os.environ.get("CLAUDE_PINS_TEST_FZF") or shutil.which("fzf")
VERSION = fzf.fzf_version(REAL_FZF) if REAL_FZF else None
SKIP = None if VERSION and VERSION >= config.MIN_FZF else f"no fzf ≥ {'.'.join(map(str, config.MIN_FZF))} on PATH (found {VERSION})"
if SKIP and os.environ.get("CLAUDE_PINS_TEST_FZF"):
    raise RuntimeError(f"CLAUDE_PINS_TEST_FZF={os.environ['CLAUDE_PINS_TEST_FZF']}: {SKIP}")

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
COUNT3 = r"3/3|(?<!of )3 pins"   # the stock counter, or the --info-command text (0.65.2+; it passes through
                                 # ``0 of 3 pins`` while the rows load, which is not the finished screen)


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def filter_ids(items: list[fzf.Item], text: str, **kw) -> list[str]:
    """The ids fzf keeps for ``text`` with the picker's options for this screen (its own --query is replaced).

    --filter with --no-sort prints the --with-nth view instead of the whole line (0.44 and 0.74 alike), so
    that one option is dropped; the interactive picker prints whole lines and the tests parse ids from them.
    """
    kw = {k: v for k, v in kw.items() if k not in ("query", "env")}
    items, kw["header_lines"] = fzf.sticky(items, kw.get("header_lines", 0), VERSION)   # as ``fzf.run`` does
    args = [a for a in fzf.build_args(REAL_FZF, version=VERSION, **kw) if a != "--no-sort"] + ["--filter", text]
    rows = "\n".join(fzf.lines_for(items, columns=kw.get("nth") is not None)) + "\n"   # as ``fzf.run`` does
    p = subprocess.run(args, input=rows, capture_output=True, text=True)
    if p.returncode not in (0, 1):
        raise AssertionError(f"fzf {VERSION} rejected the options: {p.stderr.strip()}")
    out = p.stdout.split("\n")[1:]  # line 1 is the --print-query echo
    return [ln.split("\t", 1)[0] for ln in out if ln]


@unittest.skipIf(SKIP, SKIP)
class RealFzfTests(FzfSandbox):
    """One test per screen: capture what it sends, then ask the real fzf what a query keeps."""

    def setUp(self):
        super().setUp()
        os.environ.pop("NO_COLOR", None)
        os.environ["CLAUDE_PINS_COLOR"] = "1"  # rows carry colour codes, as in a terminal
        self.make_session(SID1, cwd=str(self.home / "git" / "mower"), age_days=2, title="Navimow schedule debug", n_turns=5)
        self.make_session(SID2, cwd=str(self.home / "git" / "command-center"), age_days=9, title="Command center collector")
        self.make_session(SID3, cwd=str(self.home / "git" / "dotclaude"), age_days=1, title="Standup prep")
        for sid, alias in ((SID1, "rc-mower"), (SID2, "cc-collector"), (SID3, "standup")):
            self.run_pin("add", sid, alias)
        self.calls: list[tuple[list[fzf.Item], dict]] = []

    def recorder(self, items, **kw):
        self.calls.append((list(items), kw))
        return None  # esc: every screen then returns

    def capture(self, drive) -> tuple[list[fzf.Item], dict]:
        self.calls.clear()
        with mock.patch.object(fzf, "run", self.recorder):
            drive()
        self.assertEqual(len(self.calls), 1, "expected exactly one fzf screen")
        return self.calls[0]

    def picker(self):
        from claude_pins.picker import Picker
        from claude_pins.store import load_store
        return Picker(load_store())

    def assertKeeps(self, items, kw, query, expected, *, only=True):
        got = filter_ids(items, query, **kw)
        real = [i for i in got if i != "-"]
        if only:
            self.assertEqual(real, expected, f"query {query!r} kept {real}")
        else:
            for e in expected:
                self.assertIn(e, real, f"query {query!r} kept {real}")

    def assertEveryRowFindable(self, items, kw):
        """Every selectable row is reachable by typing its first visible word; the id comes back intact."""
        for it in items:
            if it.id == "-" or not it.id:
                continue
            word = plain(it.display).split()[0]
            self.assertIn(it.id, filter_ids(items, word, **kw), f"{word!r} does not find {it.id!r}")

    def test_options_accepted(self):
        """Every option and binding the picker can emit, including the bindings only the stub sees."""
        from claude_pins.keymap import ACTIONS
        keys = [a.key for a in ACTIONS if a.key and a.key not in ("enter", "tab") and not a.bind]
        transform = fzf.header_transform(bottom_border=True)
        binds = [("focus", f"transform-header({transform})"), ("change", f"transform-header({transform})"),
                 ("change", f"reload({fzf.pin_exe()} _dirs --gap {{q}})"), ("alt-r", "reload(printf 'L\\na\\tb\\n')")]
        if fzf.supports("resize", VERSION):
            binds.append(("resize", f"reload(true)+transform-header({transform})"))
        self.assertEqual(fzf.header_binds(bottom_border=True, version=VERSION)[0][1], f"transform-header({transform})")
        kw = dict(prompt="📌 pins › ", header="h\nflash\nnote", expect=keys, query="x", multi=True, preview="echo {1}",
                  preview_label_cmd="echo {1}", pos=2, border_label=" 2 expired ", disabled=True, header_lines=1,
                  binds=binds, info_command=fzf.INFO_COMMAND if fzf.supports("info-command", VERSION) else None,
                  nth="1..3", extra=["--bind", "alt-t:change-header(✓ touched)+reload(true)", "--bind", "enter:become(echo {1})",
                         "--expect", "ctrl-r,ctrl-alt-r", "--preview-label", " draft "])
        items = [fzf.Item("-", "label"), fzf.Item("a", "b")]
        self.assertEqual(filter_ids(items, "x", **kw), [])  # accepted, nothing matches "x" against "b"
        self.assertEqual(filter_ids(items, "", **kw), ["a"])  # the header line is neither matched nor printed
        args = fzf.build_args(REAL_FZF, version=VERSION, **kw)
        for opt in ("--header-first", "--header-lines=1", "--disabled", "--no-clear", "--tabstop=1"):
            self.assertIn(opt, args)
        self.assertEqual(args[args.index("--nth") + 1], "1..3")
        self.assertEqual(("--with-shell" in args), VERSION >= (0, 51))
        if "--with-shell" in args:
            self.assertEqual(args[args.index("--with-shell") + 1], "sh -c")
        self.assertEqual(args[args.index("--preview-window") + 1], "down,55%,border-rounded,wrap,<10(hidden)")
        self.assertIn(f"focus:transform-preview-label(echo {{1}})+transform-header({transform})", args)  # one bind per trigger
        self.assertEqual(("--info-command" in args), VERSION >= (0, 65, 2))
        self.assertEqual(any(a.startswith("resize:") for a in args), VERSION >= (0, 46))
        self.assertIn(f"--color={theme.fzf_colors()}", args)             # every name and #rrggbb in the spec is accepted
        with self.assertRaisesRegex(AssertionError, "rejected"):          # and the check would notice a bad one
            filter_ids(items, "", prompt="> ", extra=["--color", "bogus:dim"])
        os.environ["NO_COLOR"] = "1"
        self.assertIn("--color=bw", fzf.build_args(REAL_FZF, **kw))

    def test_header_transform(self):
        """The header transform, run by this fzf: legend and hints on one line from 153 columns (four cells
        between them), stacked below that with the legend first, the extra line, and the status line as the
        flash, the too-short note under 20 rows, or a space. Python's launch-time layout is the same text."""
        from claude_pins.render import legend
        from claude_pins.theme import Palette
        hints = "enter open · ctrl-space actions · alt-e edit · alt-n new · alt-i details · f1 help"
        note = "preview hidden: terminal too short"
        cases = [(fzf.Header(hints, legend=legend(), note=note, color=Palette(True)), (153, 30), (152, 30), (200, 19)),
                 (fzf.Header(hints, legend=legend(), extra=("keymap: ~/k.toml",), status="✓ saved", color=Palette(True)),
                  (160, 30), (150, 30)),
                 (fzf.Header("enter run · esc back"), (200, 30), (40, 12))]
        for header, *sizes in cases:
            for cols, lines in sizes:
                # the transform bound to start, with the size fzf reports; --filter prints the header nowhere,
                # so it goes through a file
                out = self.home / "header.txt"
                env = {**os.environ, **header.env(), "FZF_COLUMNS": str(cols), "FZF_LINES": str(lines)}
                snippet = fzf.header_transform(bottom_border=True)
                self.assertNotIn("(", snippet); self.assertNotIn("[", snippet)
                p = subprocess.run(["sh", "-c", snippet], env=env, capture_output=True, text=True)
                self.assertEqual(p.stderr, "")
                self.assertEqual(p.stdout, header.text(cols, lines, bottom_border=True), f"{cols}x{lines}")
                out.write_text(p.stdout)
                # and fzf accepts the same text as its --header (colour codes included)
                items = [fzf.Item("a", "b")]
                self.assertEqual(filter_ids(items, "", prompt="> ", header=p.stdout), ["a"])
        h = cases[0][0]
        wide, narrow = plain(h.text(153, 30)).split("\n"), plain(h.text(152, 30)).split("\n")
        self.assertEqual(len(wide), 2); self.assertEqual(len(narrow), 3)
        self.assertTrue(wide[0].startswith("🟢 open") and wide[0].endswith("f1 help"))
        self.assertIn("🔴 expired    enter open", wide[0])                       # exactly four cells between
        self.assertEqual(wide[1], " ")
        self.assertEqual(narrow[:2], [plain(legend()), hints])
        self.assertEqual(plain(h.text(200, 19, bottom_border=True)).split("\n")[-1], note)
        self.assertEqual(plain(h.text(200, 20, bottom_border=True)).split("\n")[-1], " ")
        self.assertEqual(plain(cases[1][0].text(150, 30)).split("\n"), [plain(legend()), hints, "keymap: ~/k.toml", "✓ saved"])
        self.assertEqual(plain(cases[1][0].text(160, 30)).split("\n")[1:], ["keymap: ~/k.toml", "✓ saved"])
        self.assertEqual(cases[2][0].text(200, 30), "enter run · esc back\n ")     # hints alone stay left

    def test_reload_rows(self):
        """What ``pin _rows`` prints for a reload is what the launch sent: the label row stays the sticky header
        and every alias is findable."""
        r = self.run_pin("_rows", "--sort", "alias", env={"FZF_COLUMNS": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        items = [fzf.Item(*ln.split("\t", 1)) for ln in lines]
        self.assertEqual(items[0].id, "-")
        items_launch, kw = self.capture(lambda: self.picker().run())
        self.assertEqual(plain(items[0].display), plain(items_launch[0].display))
        kw = {**kw, "header_lines": 1}
        self.assertKeeps(items, kw, "", ["cc-collector", "rc-mower", "standup"])
        self.assertKeeps(items, kw, "alias", [])
        self.assertEveryRowFindable(items, kw)
        r = self.run_pin("_rows", "--sort", "alias", "--gap", env={"FZF_COLUMNS": "100"})
        self.assertEqual(r.stdout.splitlines()[0], "-\t ")                      # the gap row a 0.63+ reload keeps
        self.assertEqual(r.stdout.splitlines()[1:], lines)
        gap_items, n = fzf.sticky(items, 1, VERSION)                              # what this build's run() sends
        self.assertEqual((gap_items[0], n), (fzf.GAP_ROW, 2) if VERSION >= (0, 63) else (items[0], 1))

    def test_main_picker(self):
        self.run_pin("edit", "rc-mower", "--fork")                     # one marker glyph on that row
        items, kw = self.capture(lambda: self.picker().run())
        self.assertEqual([i.id for i in items], ["-", "standup", "rc-mower", "cc-collector"])
        self.assertEqual(kw["header_lines"], 1)
        self.assertKeeps(items, kw, "alias", [])                       # the label row is a header, never a match
        self.assertKeeps(items, kw, "navi", ["rc-mower"])              # a title word
        self.assertKeeps(items, kw, "rc-mow", ["rc-mower"])            # the alias
        self.assertKeeps(items, kw, "command-center", ["cc-collector"])  # the directory
        self.assertKeeps(items, kw, "'center", ["cc-collector"])       # fzf's exact-match syntax still works
        self.assertKeeps(items, kw, "zzz", [])
        self.assertKeeps(items, kw, "", ["standup", "rc-mower", "cc-collector"])
        self.assertEveryRowFindable(items, kw)
        # --nth stops at the directory: the idle time and the marker glyphs are drawn but never matched
        self.assertEqual(kw["nth"], "1..3")
        self.assertIn("2d", plain(items[2].display))
        self.assertKeeps(items, kw, "'2d", [])
        self.assertIn(theme.glyphs().fork, items[2].display)
        self.assertKeeps(items, kw, theme.glyphs().fork, [])
        # --tabstop=1 draws each tab as one space, so every boundary is a tab plus a space, and the
        # columns are the fields --nth counts: alias, title, directory, idle, markers
        columns = plain(items[2].display).split("\t")
        self.assertEqual(len(columns), 5)
        self.assertTrue(all(c.startswith(" ") for c in columns[1:]))
        self.assertEqual(columns[0].rstrip(), "rc-mower")

    def test_main_picker_narrow(self):
        """A squeezed directory still matches by its last component, which stays whole; the abbreviated
        leading components match only as displayed (fzf highlights matches, so hidden text would be a
        surprise)."""
        os.environ["COLUMNS"] = "60"
        items, kw = self.capture(lambda: self.picker().run())
        row = plain(next(i.display for i in items if i.id == "cc-collector"))
        self.assertIn("~/g/command-center", row)
        self.assertKeeps(items, kw, "command-center", ["cc-collector"])
        self.assertKeeps(items, kw, "'git", ["standup", "rc-mower"])       # the two directories still whole

    def test_main_picker_prefiltered(self):
        from claude_pins.picker import Picker
        from claude_pins.store import load_store
        items, kw = self.capture(lambda: Picker(load_store(), query="standup").run())
        self.assertEqual(kw["query"], "standup")
        self.assertKeeps(items, kw, kw["query"], ["standup"])

    def test_palette(self):
        from claude_pins.listing import build_views
        p = self.picker()
        views = build_views(p.store, sort=p.state.sort)[0]
        items, kw = self.capture(lambda: p.palette([views[0]]))
        self.assertTrue(all(i.id != "-" for i in items))                # gutter groups: every row is an action
        self.assertKeeps(items, kw, "fork", ["open_fork", "fork_mode"])
        self.assertKeeps(items, kw, "unpin", ["unpin"])
        self.assertKeeps(items, kw, "details", ["details"])
        self.assertKeeps(items, kw, "'pin", ["edit", "new", "unpin"])   # the gutter name matches on its group's first row
        self.assertEveryRowFindable(items, kw)

    def test_details_screen(self):
        from claude_pins.listing import build_views
        p = self.picker()
        views = build_views(p.store, sort=p.state.sort)[0]
        items, kw = self.capture(lambda: p.details(views[0]))
        self.assertTrue(kw["disabled"])
        self.assertTrue(all(i.id == "-" for i in items))
        self.assertEqual(filter_ids(items, "", **kw), ["-"] * len(items))   # options accepted; every line comes back
        self.assertTrue(any(SID3 in plain(i.display) for i in items))

    def test_help_screen(self):
        p = self.picker()
        items, kw = self.capture(p.help_screen)
        self.assertTrue(all(i.id != "-" for i in items))
        self.assertIn("🟢 open", kw["header"])                          # the legend moved into the header
        self.assertKeeps(items, kw, "f1", ["help"])                    # by key
        self.assertKeeps(items, kw, "touch", ["touch"])                # by title
        self.assertEveryRowFindable(items, kw)

    def test_session_chooser(self):
        p = self.picker()
        items, kw = self.capture(p.new_pin)
        self.assertEqual(len(items), 4)
        self.assertEqual((items[0].id, kw["header_lines"]), ("-", 1))               # the label row is sticky
        self.assertRegex(plain(items[0].display), r"^title\s+directory\s+idle\s+msgs\s+pin$")
        self.assertKeeps(items, kw, "'title", [])
        self.assertKeeps(items, kw, "standup", [i.id for i in items if SID3 in i.id])
        self.assertKeeps(items, kw, "dotclaude", [i.id for i in items if SID3 in i.id])
        self.assertEveryRowFindable(items, kw)
        # title and directory only: idle, the message count and the pinned tag (the pin's alias) are
        # outside --nth
        self.assertEqual(kw["nth"], "1..2")
        self.assertTrue(all("msgs" in plain(i.display) for i in items[1:]))
        self.assertTrue(plain(items[1].display).endswith(f"{theme.glyphs().pinned} standup"))
        self.assertKeeps(items, kw, "'msgs", [])
        self.assertKeeps(items, kw, "'9d", [])
        self.assertKeeps(items, kw, "cc-collector", [])                              # the alias tag of SID2's row

    def test_editor_and_choice(self):
        from claude_pins import editor
        from claude_pins.store import load_store
        items, kw = self.capture(lambda: editor.edit_pin(load_store(), "rc-mower", run=self.recorder))
        self.assertTrue(all(i.id != "-" for i in items))
        self.assertKeeps(items, kw, "alias", ["alias"], only=False)  # fuzzy: a hint elsewhere also matches
        self.assertKeeps(items, kw, "rc-mower", ["alias"])             # by current value
        self.assertKeeps(items, kw, "done", ["done"])
        self.assertEveryRowFindable(items, kw)
        items, kw = self.capture(lambda: editor.choose(self.recorder, "› model › ", ["fable", "opus", "sonnet"], "opus"))
        self.assertKeeps(items, kw, "son", ["sonnet"])
        self.assertKeeps(items, kw, "clear", [""])

    def test_prompt_screens(self):
        """The prompts fzf draws: a text field is the query line over no rows, a directory field the query
        line over ``pin _dirs`` rows reloading on change, a yes/no and a choice are two- and n-row lists."""
        from claude_pins import prompt
        with mock.patch.object(fzf, "run", self.recorder):
            with self.assertRaises(prompt.Cancelled):
                prompt.text("title", "Standup prep", crumb="📌 pins › a › edit › title › ")
        items, kw = self.calls[-1]
        self.assertEqual(items, [])
        self.assertEqual(kw["query"], "Standup prep"); self.assertTrue(kw["disabled"])
        self.assertEqual(filter_ids(items, "", **kw), [])                  # accepted; nothing to print but the query
        (self.home / "git" / "alpha").mkdir(); (self.home / "git" / "alps").mkdir()
        r = self.run_pin("_dirs", "~/git/al")
        self.assertEqual(r.stdout, "~/git/alpha/\t~/git/alpha/\n~/git/alps/\t~/git/alps/\n")
        self.assertEqual(self.run_pin("_dirs", "--gap", "~/git/al").stdout, "-\t \n" + r.stdout)
        with mock.patch.object(fzf, "run", self.recorder):
            with self.assertRaises(prompt.Cancelled):
                prompt.directory("~/git/al", crumb="📌 pins › a › edit › cwd › ")
        items, kw = self.calls[-1]
        self.assertEqual([i.id for i in items], ["~/git/alpha/", "~/git/alps/"])
        gap = " --gap" if fzf.supports("sticky-under-prompt") else ""              # decided by the fzf on PATH here
        self.assertTrue(any(t == "change" and f"_dirs{gap} {{q}}" in a for t, a in kw["binds"]))
        self.assertEqual(filter_ids(items, "", **kw), ["~/git/alpha/", "~/git/alps/"])   # the bind is accepted
        with mock.patch.object(fzf, "run", self.recorder):
            with self.assertRaises(prompt.Cancelled):
                prompt.yesno("unpin them?", False, crumb="📌 pins › prune › ", notes=["prune 1 expired pin(s): a"])
        items, kw = self.calls[-1]
        self.assertEqual(kw["pos"], 2)
        self.assertKeeps(items, kw, "yes", ["1"]); self.assertKeeps(items, kw, "no", ["2"])
        self.assertEveryRowFindable(items, kw)
        with mock.patch.object(fzf, "run", self.recorder):
            with self.assertRaises(prompt.Cancelled):
                prompt.choose(["save changes?"], ["save", "discard", "keep editing"], 1)
        items, kw = self.calls[-1]
        self.assertKeeps(items, kw, "keep", ["3"])
        self.assertEveryRowFindable(items, kw)


@unittest.skipIf(SKIP, SKIP)
class InteractiveSmokeTest(FzfSandbox):
    """The real picker in a pseudo-terminal: type a query, watch the list shrink, press enter, see claude run.

    These are the only tests that drive the interactive binary, and each step waits on a terminal redraw, so
    there are few of them and each proves several things: the whole chain a user touches (terminal → fzf →
    --expect → opener → claude), and what only a real terminal can show (the preview pane hiding itself at
    fzf's size threshold). The scripted stub covers the flows, ``RealFzfTests`` the matching.
    """

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_FZF"] = REAL_FZF
        os.environ["TERM"] = "xterm-256color"
        self.make_session(SID1, cwd=str(self.home / "git" / "mower"), age_days=2, title="Navimow schedule debug")
        self.make_session(SID2, cwd=str(self.home / "git" / "command-center"), age_days=9, title="Command center collector")
        self.make_session(SID3, cwd=str(self.home / "git" / "dotclaude"), age_days=1, title="Standup prep")
        for sid, alias in ((SID1, "rc-mower"), (SID2, "cc-collector"), (SID3, "standup")):
            self.run_pin("add", sid, alias)
        # This claude stub also records whether the terminal was back in cooked mode when it started.
        self.stub("claude", "#!/bin/sh\npython3 -c 'import json,os,sys,termios; a = termios.tcgetattr(0)[3]; "
                  "json.dump({\"argv\": sys.argv[1:], \"cwd\": os.getcwd(), "
                  "\"cooked\": bool(a & termios.ICANON and a & termios.ECHO)}, "
                  f"open(\"{self.argv_log}\", \"w\"))' \"$@\"\n")
        self.out = b""          # what wait_for has read since the last clear
        self.raw = b""          # everything read, uncleared

    def tearDown(self):
        os.environ.pop("TERM", None)
        super().tearDown()

    def assertScreenRestoredOnce(self):
        """--no-clear keeps the alternate screen up between fzf runs; it is left exactly once, at the end."""
        self.assertEqual(self.raw.count(b"\x1b[?1049l"), 1, "the alternate screen was left more than once")
        self.assertGreater(self.raw.rfind(b"\x1b[?1049l"), self.raw.rfind(b"\x1b[?1049h"))

    def wait_for(self, fd: int, pattern: str, timeout: float = 15.0, *, fresh: bool = False) -> str:
        """Read the terminal until ``pattern`` shows in the colour-stripped stream, or fail with what came.
        ``fresh`` matches only what came after the last alternate-screen entry, that is the newest fzf's
        drawing: a screen keeps drawing for a moment after the key that ends it (its header transform
        redraws on 0.44), and that tail would satisfy a wait meant for the next screen."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = self.out
            if fresh:
                out = out[out.rfind(b"\x1b[?1049h"):] if b"\x1b[?1049h" in out else b""
            if re.search(pattern, plain(out.decode("utf-8", "replace"))):
                return plain(out.decode("utf-8", "replace"))
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                self.out += chunk
                self.raw += chunk
        self.fail(f"{pattern!r} never appeared; terminal so far:\n{plain(self.out.decode('utf-8', 'replace'))[-800:]}")

    def drain_until_exit(self, pid: int, fd: int) -> int:
        """Read the terminal until the child exits; its exit status."""
        deadline = time.time() + 15
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                self.out += chunk
                self.raw += chunk
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                return status
        self.fail("the picker did not exit")

    def spawn(self, rows: int, cols: int = 100, *args: str) -> tuple[int, int]:
        from tests.helpers import PIN
        size = struct.pack("HHHH", rows, cols, 0, 0)
        pid, fd = pty.fork()
        if pid == 0:  # the picker; its exec of the claude stub inherits the terminal
            fcntl.ioctl(0, termios.TIOCSWINSZ, size)     # before exec, so Python never sees the default size
            # os.environ, not the C environ: an earlier test's ``import readline`` exported the real
            # terminal's LINES and COLUMNS there, and the picker would size itself by them
            os.execve(sys.executable, [sys.executable, str(PIN), *args], dict(os.environ))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
        return pid, fd

    def test_query_then_enter_resumes_the_match(self):
        """Also: alt-r reloads the rows in place (a pin added from another terminal appears, the label row
        stays the sticky header), and the terminal is back on the normal screen in cooked mode before
        claude starts."""
        pid, fd = self.spawn(30)
        try:
            self.wait_for(fd, COUNT3)                       # three pins listed
            screen = self.wait_for(fd, r"alias\s+title\s+directory\s+idle")   # the label row, under the prompt
            self.assertIn("alt-i details · f1 help", screen)
            self.assertIn("📌 pins ›", screen)
            self.assertIn("session    " + SID3, self.wait_for(fd, r"session\s+" + SID3))  # the preview pane is up
            sid4 = "44444444-4444-4444-4444-444444444444"
            self.make_session(sid4, cwd=str(self.home / "git" / "late"), age_days=0.5, title="Late arrival")
            self.run_pin("add", sid4, "late")               # another terminal pins something
            self.out = b""
            os.write(fd, b"\x1br")                          # alt-r: reload, no restart
            self.wait_for(fd, r"Late arrival")
            screen = self.wait_for(fd, r"4/4|(?<!of )4 pins")   # fzf counts the reload in as it reads it
            self.assertEqual(len(re.findall(r"alias\s+title\s+directory\s+idle", screen)), 1)
            self.assertNotIn(b"\x1b[?1049h", self.out)      # the same fzf: no new screen
            os.write(fd, b"navi")
            screen = self.wait_for(fd, r"1/4|1 of 4 pins")  # one survives the query
            self.assertIn("rc-mower", screen)
            os.write(fd, b"\r")
            status = self.drain_until_exit(pid, fd)         # the child is the claude stub by then
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(fd)
        self.assertEqual(status, 0, plain(self.out.decode("utf-8", "replace"))[-800:])
        calls = self.claude_calls()
        self.assertIsNotNone(calls, "claude was never launched")
        self.assertEqual(calls["argv"], ["--resume", SID1])
        self.assertEqual(calls["cwd"], str(self.home / "git" / "mower"))
        self.assertTrue(calls["cooked"], "claude started with the terminal still in raw mode")
        self.assertScreenRestoredOnce()

    def test_short_terminal_hides_the_preview(self):
        """At 16 rows fzf's ``<10(hidden)`` threshold hides the pane; the header says so, alt-v turns the preview
        off and then refuses to turn it back on, and alt-i still shows the details."""
        pid, fd = self.spawn(16)
        try:
            self.wait_for(fd, COUNT3)
            screen = self.wait_for(fd, r"preview hidden: terminal too short")
            self.assertNotIn("session    " + SID3, screen)                     # no pane drawn
            self.out = b""                          # each screen is a new fzf; wait for it to draw before typing
            os.write(fd, b"\x1bv")                                              # alt-v: off
            self.wait_for(fd, COUNT3, fresh=True)
            screen = self.wait_for(fd, r"🟢 open", fresh=True)                  # the legend stays on line 1
            self.assertNotIn("preview hidden", screen)                          # the status line is blank again
            self.out = b""
            os.write(fd, b"\x1bv")                                              # alt-v again: cannot turn on
            self.wait_for(fd, r"preview needs a taller terminal · alt-i for details", fresh=True)
            self.wait_for(fd, COUNT3, fresh=True)
            self.out = b""
            os.write(fd, b"\x1bi")                                              # alt-i: the details screen
            self.wait_for(fd, r"details ›", fresh=True)
            self.wait_for(fd, r"session\s+" + SID3, fresh=True)
            self.wait_for(fd, r"enter open · esc back", fresh=True)
            self.out = b""
            os.write(fd, b"\x1b")                                               # esc: back to the list
            self.wait_for(fd, COUNT3, fresh=True)
            self.wait_for(fd, r"🟢 open", fresh=True)
            self.assertNotIn(b"\x1b[?1049l", self.raw)                          # five screens, one alternate screen
            os.write(fd, b"\x1b")                                               # esc: leave
            status = self.drain_until_exit(pid, fd)
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(fd)
        self.assertEqual(status, 0)
        self.assertIsNone(self.claude_calls())
        self.assertScreenRestoredOnce()

    def test_editor_field_on_the_query_line(self):
        """``pin edit`` in a terminal: the form with the draft in its pane, the title typed on fzf's query line
        (enter over an empty list exits 1 and still prints the query), the star on the row and the mark in
        the pane, alt-s to save, and the shell back exactly once with the confirmation printed on it."""
        pid, fd = self.spawn(30, 100, "edit", "standup")
        try:
            self.wait_for(fd, r"standup › edit ›")
            self.wait_for(fd, r"opens\s+\(default\)")                            # the draft pane
            self.out = b""
            os.write(fd, b"\r")                                                  # enter: the title field (row 1)
            screen = self.wait_for(fd, r"enter save · esc cancel · ctrl-u clear", fresh=True)
            self.assertIn("standup › edit › title ›", screen)
            self.wait_for(fd, r"title › Standup prep", fresh=True)              # prefilled on the query line
            self.out = b""
            os.write(fd, b" (Tue)\r")
            self.wait_for(fd, r"edit \(unsaved\) ›", fresh=True)
            self.wait_for(fd, r"\*Standup prep \(Tue\)", fresh=True)             # the row's star
            self.wait_for(fd, r"\* Standup prep \(Tue\)", fresh=True)            # the pane's mark
            self.assertNotIn(b"\x1b[?1049l", self.raw)
            os.write(fd, b"\x1bs")                                               # alt-s
            status = self.drain_until_exit(pid, fd)
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(fd)
        self.assertEqual(status, 0, plain(self.out.decode("utf-8", "replace"))[-800:])
        self.assertScreenRestoredOnce()
        after = self.raw[self.raw.rfind(b"\x1b[?1049l"):].decode("utf-8", "replace")
        self.assertIn("✓ saved standup", after)                                 # printed once the shell is back
        r = self.run_pin("list", "--json")
        self.assertIn("Standup prep (Tue)", r.stdout)


if __name__ == "__main__":
    unittest.main()
