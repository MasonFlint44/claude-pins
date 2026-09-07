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
COUNT3 = r"3/3|3 pins"      # the stock counter, or the --info-command text on builds that have it (0.54+)


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def filter_ids(items: list[fzf.Item], text: str, **kw) -> list[str]:
    """The ids fzf keeps for ``text`` with the picker's options for this screen (its own --query is replaced).

    --filter with --no-sort prints the --with-nth view instead of the whole line (0.44 and 0.74 alike), so
    that one option is dropped; the interactive picker prints whole lines and the tests parse ids from them.
    """
    kw = {k: v for k, v in kw.items() if k not in ("query", "env")}
    args = [a for a in fzf.build_args(REAL_FZF, **kw) if a != "--no-sort"] + ["--filter", text]
    rows = "\n".join(fzf.lines_for(items)) + "\n"
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
                 ("alt-r", "reload(printf 'L\\na\\tb\\n')")]
        if fzf.supports("resize", VERSION):
            binds.append(("resize", f"reload(true)+transform-header({transform})"))
        kw = dict(prompt="📌 pins › ", header="h\nflash\nnote", expect=keys, query="x", multi=True, preview="echo {1}",
                  preview_label_cmd="echo {1}", pos=2, border_label=" 2 expired ", disabled=True, header_lines=1,
                  binds=binds, info_command=fzf.INFO_COMMAND if fzf.supports("info-command", VERSION) else None,
                  extra=["--bind", "alt-t:change-header(✓ touched)+reload(true)", "--bind", "enter:become(echo {1})",
                         "--expect", "ctrl-r,ctrl-alt-r"])
        items = [fzf.Item("-", "label"), fzf.Item("a", "b")]
        self.assertEqual(filter_ids(items, "x", **kw), [])  # accepted, nothing matches "x" against "b"
        self.assertEqual(filter_ids(items, "", **kw), ["a"])  # the header line is neither matched nor printed
        args = fzf.build_args(REAL_FZF, **kw)
        for opt in ("--header-first", "--header-lines=1", "--disabled", "--no-clear"):
            self.assertIn(opt, args)
        self.assertEqual(args[args.index("--preview-window") + 1], "down,55%,border-rounded,wrap,<10(hidden)")
        self.assertIn(f"focus:transform-preview-label(echo {{1}})+transform-header({transform})", args)  # one bind per trigger
        self.assertEqual(("--info-command" in args), VERSION >= (0, 65, 2))
        self.assertEqual(any(a.startswith("resize:") for a in args), VERSION >= (0, 46))
        self.assertIn(f"--color={theme.fzf_colors()}", args)             # every name and #rrggbb in the spec is accepted
        with self.assertRaisesRegex(AssertionError, "rejected"):          # and the check would notice a bad one
            filter_ids(items, "", prompt="> ", extra=["--color", "bogus:dim"])
        os.environ["NO_COLOR"] = "1"
        self.assertIn("--color=bw", fzf.build_args(REAL_FZF, **kw))

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

    def test_main_picker(self):
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
        self.assertEqual(len(items), 3)
        self.assertKeeps(items, kw, "standup", [i.id for i in items if SID3 in i.id])
        self.assertKeeps(items, kw, "dotclaude", [i.id for i in items if SID3 in i.id])
        self.assertEveryRowFindable(items, kw)

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

    def wait_for(self, fd: int, pattern: str, timeout: float = 15.0) -> str:
        """Read the terminal until ``pattern`` shows in the colour-stripped stream, or fail with what came."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, plain(self.out.decode("utf-8", "replace"))):
                return plain(self.out.decode("utf-8", "replace"))
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

    def spawn(self, rows: int, cols: int = 100) -> tuple[int, int]:
        from tests.helpers import PIN
        pid, fd = pty.fork()
        if pid == 0:  # the picker; its exec of the claude stub inherits the terminal
            os.execv(sys.executable, [sys.executable, str(PIN)])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        return pid, fd

    def test_query_then_enter_resumes_the_match(self):
        """Also: alt-r reloads the rows in place (a pin added from another terminal appears, the label row
        stays the sticky header), and the terminal is back on the normal screen in cooked mode before
        claude starts."""
        pid, fd = self.spawn(30)
        try:
            screen = self.wait_for(fd, COUNT3)              # three pins listed
            self.assertRegex(screen, r"alias\s+title\s+directory\s+idle")   # the label row, under the prompt
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
            screen = self.wait_for(fd, COUNT3)
            self.assertIn("preview hidden: terminal too short", screen)
            self.assertNotIn("session    " + SID3, screen)                     # no pane drawn
            self.out = b""                          # each screen is a new fzf; wait for it to draw before typing
            os.write(fd, b"\x1bv")                                              # alt-v: off
            self.wait_for(fd, COUNT3)
            self.wait_for(fd, r"🟢 open")                                        # the legend is back on line 2
            self.out = b""
            os.write(fd, b"\x1bv")                                              # alt-v again: cannot turn on
            self.wait_for(fd, r"preview needs a taller terminal · alt-i for details")
            self.wait_for(fd, COUNT3)
            self.out = b""
            os.write(fd, b"\x1bi")                                              # alt-i: the details screen
            self.wait_for(fd, r"details ›")
            self.wait_for(fd, r"session\s+" + SID3)
            self.wait_for(fd, r"enter open · esc back")
            os.write(fd, b"\x1b")                                               # esc: back to the list
            self.wait_for(fd, COUNT3)
            self.wait_for(fd, r"🟢 open")
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


if __name__ == "__main__":
    unittest.main()
