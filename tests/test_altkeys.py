"""The alt-keys probe: which terminal, and whether its Option-as-Meta or metaSendsEscape switch is on, read
from a fake home."""

from __future__ import annotations

import os
import plistlib
import tempfile
import unittest
from pathlib import Path

from claude_pins import altkeys
from claude_pins.altkeys import OFF, ON, UNKNOWN, Probe, probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="pins-mac-"))
        self._os = os.environ.get("CLAUDE_PINS_OS")
        os.environ["CLAUDE_PINS_OS"] = "darwin"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)
        if self._os is None:
            os.environ.pop("CLAUDE_PINS_OS", None)
        else:
            os.environ["CLAUDE_PINS_OS"] = self._os

    def probe(self, **env) -> Probe | None:
        return probe(env, self.home)

    def write(self, *parts: str, text: str = "", data: bytes = b"") -> Path:
        path = self.home.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if data:
            path.write_bytes(data)
        else:
            path.write_text(text)
        return path

    def test_only_on_a_mac_over_ssh_from_iterm2_or_in_xterm(self):
        os.environ["CLAUDE_PINS_OS"] = "linux"
        self.assertIsNone(self.probe(TERM_PROGRAM="vscode"))
        self.assertEqual(self.probe(XTERM_VERSION="XTerm(379)").terminal, "xterm")
        self.assertEqual(self.probe(LC_TERMINAL="iTerm2"), Probe("iTerm2", UNKNOWN, '"Left Option key: Esc+"'))
        os.environ["CLAUDE_PINS_OS"] = "darwin"
        self.assertEqual(self.probe(), Probe("", UNKNOWN, ""))
        self.assertEqual(self.probe(TERM_PROGRAM="tmux").note(), "alt keys need the terminal's Option as Meta setting")
        self.assertEqual(altkeys.platform(), "darwin")
        os.environ.pop("CLAUDE_PINS_OS")
        import sys
        self.assertEqual(altkeys.platform(), sys.platform)

    def test_terminal_app(self):
        env = dict(TERM_PROGRAM="Apple_Terminal")
        self.assertEqual(self.probe(**env), Probe("Terminal.app", UNKNOWN, '"Use Option as Meta key"'))   # no plist
        plist = self.write("Library", "Preferences", "com.apple.Terminal.plist", data=plistlib.dumps(
            {"Default Window Settings": "Pro", "Window Settings": {"Basic": {"useOptionAsMetaKey": True}, "Pro": {}}}))
        self.assertEqual(self.probe(**env).state, OFF)                        # the default profile decides
        plist.write_bytes(plistlib.dumps({"Default Window Settings": "Basic",
                                          "Window Settings": {"Basic": {"useOptionAsMetaKey": True}}}))
        self.assertEqual(self.probe(**env).state, ON)
        self.assertEqual(self.probe(**env).note(), 'alt keys need "Use Option as Meta key" in Terminal.app')
        plist.write_bytes(plistlib.dumps({"Default Window Settings": "Gone", "Window Settings": {}}))
        self.assertEqual(self.probe(**env).state, UNKNOWN)                    # names a profile that is not there
        plist.write_bytes(b"not a plist")
        self.assertEqual(self.probe(**env).state, UNKNOWN)

    def test_iterm2(self):
        env = dict(TERM_PROGRAM="iTerm.app", ITERM_PROFILE="Work")
        self.assertEqual(self.probe(**env).state, UNKNOWN)
        plist = self.write("Library", "Preferences", "com.googlecode.iterm2.plist", data=plistlib.dumps(
            {"New Bookmarks": [{"Name": "Default", "Default Bookmark": "Yes", "Option Key Sends": 0},
                               {"Name": "Work", "Option Key Sends": 1, "Right Option Key Sends": 2}]}))
        self.assertEqual(self.probe(**env).state, ON)                         # the right key sends Esc+
        self.assertEqual(self.probe(TERM_PROGRAM="iTerm.app").state, OFF)     # no ITERM_PROFILE: the default profile
        self.assertEqual(self.probe(TERM_PROGRAM="iTerm.app", ITERM_PROFILE="Nope").state, OFF)   # unknown name: default
        plist.write_bytes(plistlib.dumps({"New Bookmarks": [{"Name": "Only", "Option Key Sends": 2}]}))
        self.assertEqual(self.probe(TERM_PROGRAM="iTerm.app").state, ON)      # no default flag: the first profile
        plist.write_bytes(plistlib.dumps({"New Bookmarks": []}))
        self.assertEqual(self.probe(**env).state, UNKNOWN)

    def test_vscode(self):
        env = dict(TERM_PROGRAM="vscode")
        self.assertEqual(self.probe(**env), Probe("VS Code", OFF, "terminal.integrated.macOptionIsMeta"))
        settings = self.write("Library", "Application Support", "Code", "User", "settings.json",
                              text='{\n  // trailing commas and comments: not JSON\n  "editor.fontSize": 14,\n}\n')
        self.assertEqual(self.probe(**env).state, OFF)
        settings.write_text('{ "terminal.integrated.macOptionIsMeta" : true, }')
        self.assertEqual(self.probe(**env).state, ON)
        settings.write_text('{ "terminal.integrated.macOptionIsMeta": false }')
        self.assertEqual(self.probe(**env).state, OFF)
        settings.write_text('{}')
        self.write("Library", "Application Support", "Cursor", "User", "settings.json",
                   text='{"terminal.integrated.macOptionIsMeta": true}')
        self.assertEqual(self.probe(**env).state, ON)                         # another build's settings mention it

    def test_ghostty(self):
        env = dict(TERM_PROGRAM="ghostty")
        self.assertEqual(self.probe(**env), Probe("Ghostty", OFF, "macos-option-as-alt"))
        conf = self.write(".config", "ghostty", "config", text="font-size = 13\nmacos-option-as-alt = true\n")
        self.assertEqual(self.probe(**env).state, ON)
        conf.write_text("macos-option-as-alt = true\nmacos-option-as-alt = false\n")
        self.assertEqual(self.probe(**env).state, OFF)                        # the last line wins
        conf.write_text("macos-option-as-alt = left\n")
        self.assertEqual(self.probe(**env).state, ON)
        conf.unlink()
        support = self.write("Library", "Application Support", "com.mitchellh.ghostty", "config",
                             text="macos-option-as-alt=right\n")
        self.assertEqual(self.probe(**env).state, ON)
        self.write("xdg", "ghostty", "config", text="macos-option-as-alt = false\n")
        self.assertEqual(self.probe(**env, XDG_CONFIG_HOME=str(self.home / "xdg")).state, ON)    # Application Support wins
        support.unlink()
        self.assertEqual(self.probe(**env, XDG_CONFIG_HOME=str(self.home / "xdg")).state, OFF)

    def test_kitty(self):
        self.assertEqual(self.probe(KITTY_WINDOW_ID="1"), Probe("Kitty", OFF, "macos_option_as_alt"))
        self.assertEqual(self.probe(TERM="xterm-kitty").terminal, "Kitty")
        conf = self.write(".config", "kitty", "kitty.conf", text="# kitty\nmacos_option_as_alt yes\n")
        self.assertEqual(self.probe(KITTY_WINDOW_ID="1").state, ON)
        conf.write_text("macos_option_as_alt both\n")
        self.assertEqual(self.probe(KITTY_WINDOW_ID="1").state, ON)
        conf.write_text("macos_option_as_alt no\n")
        self.assertEqual(self.probe(KITTY_WINDOW_ID="1").state, OFF)
        self.write("kc", "kitty.conf", text="macos_option_as_alt left\n")
        self.assertEqual(self.probe(KITTY_WINDOW_ID="1", KITTY_CONFIG_DIRECTORY=str(self.home / "kc")).state, ON)

    def test_alacritty(self):
        self.assertEqual(self.probe(ALACRITTY_WINDOW_ID="7"), Probe("Alacritty", OFF, "window.option_as_alt"))
        conf = self.write(".config", "alacritty", "alacritty.toml", text='[window]\noption_as_alt = "Both"\n')
        self.assertEqual(self.probe(ALACRITTY_WINDOW_ID="7").state, ON)
        conf.write_text('[window]\noption_as_alt = "OnlyLeft"\n')
        self.assertEqual(self.probe(ALACRITTY_WINDOW_ID="7").state, ON)
        conf.write_text('[window]\noption_as_alt = "None"\n')
        self.assertEqual(self.probe(ALACRITTY_WINDOW_ID="7").state, OFF)
        conf.write_text('[window]\ndecorations = "Full"\n')
        self.assertEqual(self.probe(ALACRITTY_WINDOW_ID="7").state, OFF)

    def test_wezterm_is_on_by_default(self):
        probe = self.probe(TERM_PROGRAM="WezTerm")
        self.assertEqual(probe.state, ON)
        self.assertTrue(probe.on)
        self.assertEqual(probe.doctor_line(), "✓ alt keys: WezTerm sends them")

    def test_xterm(self):
        """xterm on any system: metaSendsEscape true or eightBitInput false in what xrdb has loaded, else in
        ~/.Xresources and ~/.Xdefaults; nothing said is off. A tmux started inside xterm inherits
        XTERM_VERSION and is xterm underneath; a TERM_PROGRAM names another terminal over it."""
        os.environ["CLAUDE_PINS_OS"] = "linux"
        env = dict(XTERM_VERSION="XTerm(379)")
        self.assertEqual(self.probe(**env), Probe("xterm", OFF, "XTerm*metaSendsEscape: true"))
        self.assertEqual(self.probe(**env).note(), "alt keys need XTerm*metaSendsEscape: true in xterm")
        res = self.write(".Xresources", text="XTerm*faceName: Mono\nXTerm*metaSendsEscape: true\n")
        self.assertEqual(self.probe(**env).state, ON)
        res.write_text("xterm*eightBitInput:  false\n")
        self.assertEqual(self.probe(**env).state, ON)
        res.write_text("XTerm.vt100.metaSendsEscape: true\n*metaSendsEscape: false\n")
        self.assertEqual(self.probe(**env).state, OFF)                        # the last line wins
        res.unlink()
        self.write(".Xdefaults", text="XTerm*metaSendsEscape:\ttrue\n")
        self.assertEqual(self.probe(**env).state, ON)
        # xrdb on PATH answers for the server and the files are not read
        bindir = self.home / "bin"; bindir.mkdir()
        xrdb = bindir / "xrdb"
        xrdb.write_text("#!/bin/sh\n[ \"$1\" = -query ] && printf 'XTerm*metaSendsEscape:\\tfalse\\n'\n")
        xrdb.chmod(0o755)
        self.assertEqual(self.probe(**env, PATH=str(bindir)).state, OFF)
        xrdb.write_text("#!/bin/sh\nprintf 'XTerm*eightBitInput:\\tfalse\\n'\n"); xrdb.chmod(0o755)
        self.assertEqual(self.probe(**env, PATH=str(bindir)).state, ON)
        xrdb.write_text("#!/bin/sh\nexit 1\n"); xrdb.chmod(0o755)                 # no DISPLAY: back to the files
        self.assertEqual(self.probe(**env, PATH=str(bindir)).state, ON)
        self.assertEqual(self.probe(**env, TERM_PROGRAM="tmux").terminal, "")   # something else names itself
        os.environ["CLAUDE_PINS_OS"] = "darwin"
        self.assertEqual(self.probe(**env).terminal, "xterm")                  # XQuartz: still xterm

    def test_doctor_lines(self):
        self.assertEqual(Probe("VS Code", OFF, "terminal.integrated.macOptionIsMeta").doctor_line(),
                         "· alt keys: VS Code does not send them · set terminal.integrated.macOptionIsMeta")
        self.assertEqual(Probe("Terminal.app", UNKNOWN, '"Use Option as Meta key"').doctor_line(),
                         '· alt keys: not sure Terminal.app sends them · check "Use Option as Meta key"')
        self.assertEqual(Probe("", UNKNOWN, "").doctor_line(),
                         "· alt keys: not sure this terminal sends them · set its Option as Meta switch")


if __name__ == "__main__":
    unittest.main()
