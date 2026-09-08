"""Whether alt keys reach the picker in this terminal.

Every macOS terminal starts with the Option key typing symbols and accents (alt-t is †, alt-i is a dead
key), so an alt binding arrives as text until the terminal's Option-as-Meta setting is on, and each
terminal keeps that setting somewhere different. Stock xterm is the same on any system: Meta sets the
high bit of the character until ``XTerm*metaSendsEscape`` is true. This reads the setting where the
terminal keeps it, so the picker can say once which switch a user needs and stay quiet for one who has
already set it, and ``pins doctor`` can report it. Everything is read with the standard library and
any file that cannot be read or parsed leaves the state unknown, which is treated like off: the note
shows, the doctor says so.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ON, OFF, UNKNOWN = "on", "off", "unknown"


@dataclass(frozen=True)
class Probe:
    terminal: str   # "Terminal.app", "iTerm2", "VS Code", "Ghostty", "Kitty", "Alacritty", "WezTerm", "xterm", "" if unknown
    state: str      # ON, OFF or UNKNOWN
    setting: str    # the switch's name in that terminal, "" for an unknown terminal

    @property
    def on(self) -> bool:
        return self.state == ON

    def note(self) -> str:
        """The one line the picker shows: what to switch on, in this terminal's words."""
        if self.terminal:
            return f"alt keys need {self.setting} in {self.terminal}"
        return "alt keys need the terminal's Option as Meta setting"

    def doctor_line(self) -> str:
        if not self.terminal:
            return "· alt keys: not sure this terminal sends them · set its Option as Meta switch"
        if self.on:
            return f"✓ alt keys: {self.terminal} sends them"
        if self.state == UNKNOWN:
            return f"· alt keys: not sure {self.terminal} sends them · check {self.setting}"
        return f"· alt keys: {self.terminal} does not send them · set {self.setting}"


def platform() -> str:
    """``sys.platform``, or ``CLAUDE_PINS_OS`` (a test hook: the tests fake a Mac on Linux and a Linux box on
    the macOS runner)."""
    return os.environ.get("CLAUDE_PINS_OS") or sys.platform


def applies(env: dict[str, str] | None = None) -> bool:
    """A Mac, an ssh session from iTerm2 (the one terminal that says so across ssh), or xterm anywhere."""
    env = os.environ if env is None else env
    return platform() == "darwin" or env.get("LC_TERMINAL") == "iTerm2" or bool(env.get("XTERM_VERSION"))


def probe(env: dict[str, str] | None = None, home: Path | None = None) -> Probe | None:
    """The terminal and whether it sends alt keys, or None where the question does not arise."""
    env = dict(os.environ) if env is None else env
    if not applies(env):
        return None
    home = home or Path(os.path.expanduser("~"))
    program = env.get("TERM_PROGRAM", "")
    if env.get("XTERM_VERSION") and not program:
        return Probe("xterm", _xterm(home, env), "XTerm*metaSendsEscape: true")
    if program == "Apple_Terminal":
        return Probe("Terminal.app", _terminal_app(home), '"Use Option as Meta key"')
    if program == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2":
        return Probe("iTerm2", _iterm2(home, env.get("ITERM_PROFILE", "")), '"Left Option key: Esc+"')
    if program == "vscode":
        return Probe("VS Code", _vscode(home), "terminal.integrated.macOptionIsMeta")
    if program == "ghostty":
        return Probe("Ghostty", _ghostty(home, env), "macos-option-as-alt")
    if program == "WezTerm":
        return Probe("WezTerm", ON, "send_composed_key_when_left_alt_is_pressed")     # off by default: alt is meta
    if env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty":
        return Probe("Kitty", _kitty(home, env), "macos_option_as_alt")
    if env.get("ALACRITTY_WINDOW_ID"):
        return Probe("Alacritty", _alacritty(home, env), "window.option_as_alt")
    return Probe("", UNKNOWN, "")


# ---- one reader per terminal ------------------------------------------------------------------------

def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _terminal_app(home: Path) -> str:
    """The default profile's ``useOptionAsMetaKey`` in Terminal.app's preferences (absent means off)."""
    path = home / "Library" / "Preferences" / "com.apple.Terminal.plist"
    try:
        with open(path, "rb") as f:
            prefs = plistlib.load(f)
        name = prefs["Default Window Settings"]
        profile = prefs["Window Settings"][name]
    except (OSError, KeyError, TypeError, ValueError, plistlib.InvalidFileException):
        return UNKNOWN
    return ON if profile.get("useOptionAsMetaKey") is True else OFF


def _iterm2(home: Path, profile_name: str) -> str:
    """The profile's "Option Key Sends" (2 is Esc+; 0 normal, 1 meta, which sets the high bit and is no use
    to a picker) in iTerm2's preferences; the right key counts too. ``ITERM_PROFILE`` names the profile,
    else the default one."""
    path = home / "Library" / "Preferences" / "com.googlecode.iterm2.plist"
    try:
        with open(path, "rb") as f:
            prefs = plistlib.load(f)
        profiles = prefs["New Bookmarks"]
        profile = next((p for p in profiles if p.get("Name") == profile_name), None) if profile_name else None
        if profile is None:
            profile = next((p for p in profiles if p.get("Default Bookmark") == "Yes"), profiles[0])
        left, right = profile.get("Option Key Sends", 0), profile.get("Right Option Key Sends", 0)
    except (OSError, KeyError, IndexError, TypeError, ValueError, plistlib.InvalidFileException):
        return UNKNOWN
    return ON if 2 in (left, right) else OFF


_VSCODE_SETTING = re.compile(r'"terminal\.integrated\.macOptionIsMeta"\s*:\s*(true|false)')


def _vscode(home: Path) -> str:
    """``terminal.integrated.macOptionIsMeta`` in the user settings (JSON with comments, so a search, not a
    parse); the first of the VS Code builds whose settings mention it decides, and no mention is off."""
    support = home / "Library" / "Application Support"
    for build in ("Code", "Code - Insiders", "VSCodium", "Cursor"):
        text = _read(support / build / "User" / "settings.json")
        if text is None:
            continue
        found = _VSCODE_SETTING.findall(text)
        if found:
            return ON if found[-1] == "true" else OFF
    return OFF


def _config_home(env: dict[str, str], home: Path) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    return Path(base).expanduser() if base else home / ".config"


def _ghostty(home: Path, env: dict[str, str]) -> str:
    """``macos-option-as-alt`` (true, left or right) in Ghostty's config: the XDG file, then the Application
    Support one, which Ghostty loads second and so wins; the last line wins within a file, absent is off."""
    candidates = [_config_home(env, home) / "ghostty" / "config",
                  home / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config"]
    state = OFF
    for path in candidates:
        text = _read(path)
        if text is None:
            continue
        for m in re.finditer(r"^\s*macos-option-as-alt\s*=\s*\"?([\w-]*)\"?", text, re.M):
            state = ON if m.group(1).lower() in ("true", "left", "right") else OFF
    return state


def _kitty(home: Path, env: dict[str, str]) -> str:
    """``macos_option_as_alt`` (yes, left, right or both) in kitty.conf; the last line wins, absent is off."""
    base = Path(env["KITTY_CONFIG_DIRECTORY"]).expanduser() if env.get("KITTY_CONFIG_DIRECTORY") \
        else _config_home(env, home) / "kitty"
    text = _read(base / "kitty.conf")
    if text is None:
        return OFF
    state = OFF
    for m in re.finditer(r"^\s*macos_option_as_alt\s+(\S+)", text, re.M):
        state = ON if m.group(1).lower() in ("yes", "true", "left", "right", "both") else OFF
    return state


def _alacritty(home: Path, env: dict[str, str]) -> str:
    """``option_as_alt`` (Both, OnlyLeft, OnlyRight or None) in alacritty.toml; absent is off."""
    candidates = [_config_home(env, home) / "alacritty" / "alacritty.toml",
                  home / ".alacritty.toml", home / ".config" / "alacritty.toml"]
    for path in candidates:
        text = _read(path)
        if text is None:
            continue
        m = re.search(r"^\s*option_as_alt\s*=\s*\"(\w+)\"", text, re.M)
        if not m:
            return OFF
        return ON if m.group(1).lower() in ("both", "onlyleft", "onlyright") else OFF
    return OFF


_XTERM_META = re.compile(r"^\s*[^:!\n]*metaSendsEscape\s*:\s*(\S+)", re.M | re.I)
_XTERM_8BIT = re.compile(r"^\s*[^:!\n]*eightBitInput\s*:\s*(\S+)", re.M | re.I)


def _xterm(home: Path, env: dict[str, str]) -> str:
    """``metaSendsEscape`` true or ``eightBitInput`` false in the X resources: what ``xrdb -query`` has
    loaded into the server when xrdb is there, else ``~/.Xresources`` and ``~/.Xdefaults``; the last
    line for a resource wins, and nothing said is xterm's default, off."""
    text = None
    xrdb = shutil.which("xrdb", path=env.get("PATH"))
    if xrdb:
        try:
            p = subprocess.run([xrdb, "-query"], capture_output=True, text=True, timeout=2, env=env)
            if p.returncode == 0:
                text = p.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    if text is None:
        text = "\n".join(t for t in (_read(home / ".Xresources"), _read(home / ".Xdefaults")) if t is not None)
    meta = _XTERM_META.findall(text)
    eight = _XTERM_8BIT.findall(text)
    if meta and meta[-1].lower() in ("true", "on", "yes", "1"):
        return ON
    if eight and eight[-1].lower() in ("false", "off", "no", "0"):
        return ON
    return OFF


__all__ = ["Probe", "ON", "OFF", "UNKNOWN", "applies", "probe", "platform"]
