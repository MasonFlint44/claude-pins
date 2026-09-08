"""The built-in picker in a real pseudo-terminal: raw mode, the alternate screen, keys, a mouse report, a
resize signal, and the terminal handed back in cooked mode before claude starts.

Each step waits on a redraw, so these are few and each proves several things; the frames and flows are
in ``tests/test_tui.py``.
"""

from __future__ import annotations

import fcntl
import os
import signal
import struct
import termios
import unittest

from claude_pins import keys
from tests.helpers import PtyMixin, Sandbox

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
SID3 = "33333333-3333-3333-3333-333333333333"
COUNT3 = r"3 pins"


class NativePtyTests(PtyMixin, Sandbox):
    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        os.environ["TERM"] = "xterm-256color"
        self.make_session(SID1, cwd=str(self.home / "git" / "mower"), age_days=2, title="Navimow schedule debug")
        self.make_session(SID2, cwd=str(self.home / "git" / "command-center"), age_days=9, title="Command center collector")
        self.make_session(SID3, cwd=str(self.home / "git" / "dotclaude"), age_days=1, title="Standup prep")
        for sid, alias in ((SID1, "rc-mower"), (SID2, "cc-collector"), (SID3, "standup")):
            self.pin_aged(sid, alias)
        self.stub_claude_tty()
        from claude_pins import config
        config.noted_file().parent.mkdir(parents=True, exist_ok=True)
        config.noted_file().touch()                     # not the first run: no fzf note on the status line

    def finish(self, pid, fd):
        try:
            status = self.drain_until_exit(pid, fd)
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(fd)
        return status

    def test_query_then_enter_resumes_the_match(self):
        """The raw-mode picker draws fzf's chrome, filters as you type with the counter following, enter execs
        claude with the terminal back in cooked mode and the alternate screen left exactly once."""
        pid, fd = self.spawn(30)
        try:
            self.assertIn(b"\x1b[?1049h", self.wait_for(fd, COUNT3).encode() or self.raw)   # the alternate screen
            self.assertIn(b"\x1b[?1000h\x1b[?1006h", self.raw)                              # mouse reporting on
            screen = self.wait_for(fd, r"alias\s+title\s+directory\s+idle")
            self.assertIn("alt-i details · f1 help", screen)
            self.assertIn("📌 pins ›", screen)
            self.assertIn("session    " + SID3, self.wait_for(fd, r"session\s+" + SID3))    # the pane, async
            self.out = b""
            os.write(fd, b"navi")
            screen = self.wait_for(fd, r"1 of 3 pins")
            self.assertIn("rc-mower", screen)
            os.write(fd, b"\r")
            status = self.finish(pid, fd)
        except BaseException:
            os.kill(pid, 9); raise
        self.assertEqual(status, 0, self.plain_out()[-800:])
        calls = self.claude_calls()
        self.assertIsNotNone(calls, "claude was never launched")
        self.assertEqual(calls["argv"], ["--resume", SID1])
        self.assertEqual(calls["cwd"], str(self.home / "git" / "mower"))
        self.assertTrue(calls["cooked"], "claude started with the terminal still in raw mode")
        self.assertIn(b"\x1b[?1000l\x1b[?1006l", self.raw)                                    # mouse reporting off
        self.assertScreenRestoredOnce()

    def test_short_terminal_alt_keys_and_esc(self):
        """At 16 rows the pane is hidden and the status line says so; alt-v turns the preview off and then
        refuses to turn it back on; alt-i shows the details on their own screen; esc goes back one level each
        time; the alternate screen stays up across screens and is left once at the end. Every screen is a
        full redraw over the last, so each wait clears what was read and looks only at the new frame."""
        pid, fd = self.spawn(16)
        try:
            self.wait_for(fd, COUNT3)
            screen = self.wait_for(fd, r"preview hidden: terminal too short")
            self.assertNotIn("session    " + SID3, screen)
            self.out = b""
            os.write(fd, keys.encode("alt-v"))
            screen = self.wait_for(fd, r"🟢 open")
            self.wait_for(fd, COUNT3)
            self.assertNotIn("preview hidden", self.wait_for(fd, r"f1 help"))
            self.out = b""
            os.write(fd, keys.encode("alt-v"))
            self.wait_for(fd, r"preview needs a taller terminal · alt-i for details")
            self.out = b""
            os.write(fd, keys.encode("alt-i"))
            self.wait_for(fd, r"details ›")
            self.wait_for(fd, r"session\s+" + SID3)
            self.wait_for(fd, r"enter open · esc back")
            self.out = b""
            os.write(fd, b"\x1b")                                       # esc alone: back after the delay
            self.wait_for(fd, COUNT3)
            self.assertNotIn(b"\x1b[?1049l", self.raw)                  # four screens, one alternate screen
            os.write(fd, b"\x1b")
            status = self.finish(pid, fd)
        except BaseException:
            os.kill(pid, 9); raise
        self.assertEqual(status, 0)
        self.assertIsNone(self.claude_calls())
        self.assertScreenRestoredOnce()

    def test_mouse_and_resize(self):
        """A click moves the cursor (the pane follows), a resize signal re-lays the screen out and hides the
        pane when the terminal gets too short, and a double-click accepts."""
        pid, fd = self.spawn(30)
        try:
            self.wait_for(fd, COUNT3)
            self.wait_for(fd, r"session\s+" + SID3)
            self.out = b""
            os.write(fd, keys.mouse_report(4, 7))                       # row 7: the second pin (rc-mower)
            self.wait_for(fd, r"session\s+" + SID1)
            self.assertRegex(self.plain_out(), r"> rc-mower")
            self.out = b""
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 100, 0, 0))
            os.kill(pid, signal.SIGWINCH)
            screen = self.wait_for(fd, r"preview hidden: terminal too short")
            self.assertNotIn("session    " + SID1, screen)
            self.out = b""
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            os.kill(pid, signal.SIGWINCH)
            self.wait_for(fd, r"session\s+" + SID1)                     # the pane is back
            self.out = b""
            os.write(fd, keys.mouse_report(4, 7) + keys.mouse_report(4, 7))
            status = self.finish(pid, fd)
        except BaseException:
            os.kill(pid, 9); raise
        self.assertEqual(status, 0, self.plain_out()[-800:])
        self.assertEqual(self.claude_calls()["argv"], ["--resume", SID1])
        self.assertScreenRestoredOnce()

    def test_ctrl_z_suspends_on_the_normal_screen(self):
        """ctrl-z stops the picker with the terminal handed back (the normal screen, cooked); ``fg`` takes it
        again and redraws. Under an interactive bash: a process straight under the test's pty is an orphaned
        process group, whose SIGTSTP the kernel discards."""
        import shutil
        if not shutil.which("bash"):
            self.skipTest("no bash")
        pid, fd = self.spawn(30, 100, shell=True)
        try:
            self.wait_for(fd, r"\$ ")
            os.write(fd, b"pin\r")
            self.wait_for(fd, COUNT3)
            self.out = b""
            os.write(fd, b"\x1a")
            self.wait_for(fd, r"Stopped\s+pin[\s\S]*\$ ")               # bash's job line, then its prompt
            self.assertIn(b"\x1b[?1049l", self.out)                    # the normal screen while stopped
            self.out = b""
            os.write(fd, b"fg\r")
            self.wait_for(fd, COUNT3)                                   # redrawn on the alternate screen again
            self.assertIn(b"\x1b[?1049h", self.out)
            os.write(fd, b"\x1b")
            self.wait_for(fd, r"\$ ")
            os.write(fd, b"exit\r")
            status = self.finish(pid, fd)
        except BaseException:
            os.kill(pid, 9); raise
        self.assertEqual(status, 0)
        self.assertIsNone(self.claude_calls())

    def test_keys_subcommand_names_events(self):
        pid, fd = self.spawn(20, 80, "_keys")
        try:
            self.wait_for(fd, r"every event is named")
            os.write(fd, b"a" + keys.encode("alt-t") + keys.encode("f5") + keys.mouse_report(3, 4, scroll=1))
            screen = self.wait_for(fd, r"scroll-up at 3,4")
            self.assertIn("a\r\nalt-t\r\nf5\r\n", screen.replace("\r\n", "\r\n"))
            os.write(fd, b"\x1b")
            self.wait_for(fd, r"\r\nesc\r\n")                          # esc alone, after the delay
            os.write(fd, b"\x1b")
            status = self.finish(pid, fd)
        except BaseException:
            os.kill(pid, 9); raise
        self.assertEqual(status, 0)
        self.assertScreenRestoredOnce()


if __name__ == "__main__":
    unittest.main()
