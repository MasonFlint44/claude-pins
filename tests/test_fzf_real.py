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
from claude_pins import config, fzf  # noqa: E402

REAL_FZF = os.environ.get("CLAUDE_PINS_TEST_FZF") or shutil.which("fzf")
VERSION = fzf.fzf_version(REAL_FZF) if REAL_FZF else None
SKIP = None if VERSION and VERSION >= config.MIN_FZF else f"no fzf ≥ {'.'.join(map(str, config.MIN_FZF))} on PATH (found {VERSION})"
if SKIP and os.environ.get("CLAUDE_PINS_TEST_FZF"):
    raise RuntimeError(f"CLAUDE_PINS_TEST_FZF={os.environ['CLAUDE_PINS_TEST_FZF']}: {SKIP}")

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def filter_ids(items: list[fzf.Item], text: str, **kw) -> list[str]:
    """The ids fzf keeps for ``text`` with the picker's options for this screen (its own --query is replaced).

    --filter with --no-sort prints the --with-nth view instead of the whole line (0.44 and 0.74 alike), so
    that one option is dropped; the interactive picker prints whole lines and the tests parse ids from them.
    """
    kw = {k: v for k, v in kw.items() if k != "query"}
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
        keys = [a.key for a in ACTIONS if a.key and a.key not in ("enter", "tab")]
        kw = dict(prompt="pins › ", header="h\nflash", expect=keys, query="x", multi=True, preview="echo {1}",
                  preview_label_cmd="echo {1}", pos=2, border_label=" 2 expired ", disabled=True,
                  extra=["--bind", "alt-t:change-header(✓ touched)+reload(true)", "--bind", "enter:become(echo {1})",
                         "--expect", "ctrl-r,ctrl-alt-r"])
        self.assertEqual(filter_ids([fzf.Item("a", "b")], "x", **kw), [])  # accepted, nothing matches "x" against "b"

    def test_main_picker(self):
        items, kw = self.capture(lambda: self.picker().run())
        self.assertEqual([i.id for i in items], ["standup", "rc-mower", "cc-collector"])
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
        self.assertKeeps(items, kw, "fork", ["open_fork", "fork_mode"])
        self.assertKeeps(items, kw, "unpin", ["unpin"])
        self.assertEveryRowFindable(items, kw)

    def test_help_screen(self):
        p = self.picker()
        items, kw = self.capture(p.help_screen)
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

    The only test that drives the interactive binary. It proves the whole chain a user touches (terminal →
    fzf → --expect → opener → claude) once; the scripted stub covers the flows, ``RealFzfTests`` the matching.
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
        self.out = b""

    def tearDown(self):
        os.environ.pop("TERM", None)
        super().tearDown()

    def wait_for(self, fd: int, pattern: str, timeout: float = 15.0) -> str:
        """Read the terminal until ``pattern`` shows in the colour-stripped stream, or fail with what came."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, plain(self.out.decode("utf-8", "replace"))):
                return plain(self.out.decode("utf-8", "replace"))
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    self.out += os.read(fd, 65536)
                except OSError:
                    break
        self.fail(f"{pattern!r} never appeared; terminal so far:\n{plain(self.out.decode('utf-8', 'replace'))[-800:]}")

    def test_query_then_enter_resumes_the_match(self):
        from tests.helpers import PIN
        pid, fd = pty.fork()
        if pid == 0:  # the picker; its exec of the claude stub inherits the terminal
            os.execv(sys.executable, [sys.executable, str(PIN)])
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            self.wait_for(fd, r"3/3")                       # three pins listed
            os.write(fd, b"navi")
            screen = self.wait_for(fd, r"1/3")              # one survives the query
            self.assertIn("rc-mower", screen)
            os.write(fd, b"\r")
            deadline = time.time() + 15
            while time.time() < deadline:                   # drain until the child (now the claude stub) exits
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        self.out += os.read(fd, 65536)
                    except OSError:
                        break
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    break
            else:
                self.fail("the picker did not exit after enter")
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


if __name__ == "__main__":
    unittest.main()
