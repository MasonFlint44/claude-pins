#!/usr/bin/env python3
"""Regenerate docs/preview.svg and docs/preview.txt: the real picker, real fzf, fixture pins.

Needs python3, fzf ≥ 0.44 and the ``pyte`` terminal emulator (``uv sync --group dev`` installs it; dev only —
the tool itself is stdlib). Nothing touches your own pins: it runs in a throwaway HOME.
"""
from __future__ import annotations

import fcntl
import html
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import pyte  # noqa: E402

from tests.helpers import Sandbox  # noqa: E402

COLS, ROWS = 100, 34
PALETTE = {"default": "#d0d0d0", "black": "#1c1c1c", "red": "#ff6b6b", "green": "#8ce99a", "yellow": "#ffd43b",
           "blue": "#74c0fc", "magenta": "#e599f7", "cyan": "#66d9e8", "white": "#f8f9fa", "brown": "#ffd43b"}
DIM = "#7a7a7a"


class Screen(pyte.Screen):
    """pyte 0.8 keeps 24-bit colour and wide glyphs but drops SGR 2, and the picker's chrome is all dim: this
    records dim in the unused italics slot (nothing here emits SGR 3) so the SVG can grey it."""

    def select_graphic_rendition(self, *attrs):
        super().select_graphic_rendition(*attrs)
        if 2 in attrs:
            self.cursor.attrs = self.cursor.attrs._replace(italics=True)
        elif not attrs or 0 in attrs or 22 in attrs:
            self.cursor.attrs = self.cursor.attrs._replace(italics=False)


class Fixture(Sandbox):
    def runTest(self):
        pass


def capture(keys: list[bytes]) -> pyte.Screen:
    fx = Fixture(); fx.setUp()
    os.environ.pop("NO_COLOR", None)
    os.environ["CLAUDE_PINS_NOW"] = ""
    os.environ["CLAUDE_PINS_GLYPHS"] = "emoji"      # the snapshot is what a UTF-8 terminal shows
    home = fx.home
    s1, s2, s3, s4 = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                      "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444")
    fx.make_session(s1, cwd=str(home / "git" / "dotclaude"), age_days=2.1, title="Standup prep", n_turns=42,
                    prompt="so the pin command should also touch the transcript when it opens a fork?",
                    answer="Yes. The picker can bind keys to run a command and reload the list afterwards, "
                           "which is how the status flashes work.", usage={"input_tokens": 2, "cache_creation_input_tokens": 1200, "cache_read_input_tokens": 120000})
    fx.make_session(s2, cwd=str(home / "git" / "command-center"), age_days=9.3, title="Command center collector", n_turns=12)
    fx.make_session(s3, cwd=str(home), age_days=26.2, title="Navimow schedule debug", n_turns=5)
    fx.make_session(s4, cwd=str(home / "git" / "foo" / ".claude" / "worktrees" / "x"), age_days=1.4, title="USAA restructure", n_turns=8)
    repo = home / "git" / "dotclaude"
    for args in (["init", "-q", "-b", "main"], ["-c", "user.email=p@p", "-c", "user.name=p", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    # ccusage is stubbed so the cost line shows the real layout without a network or install.
    fx.stub("ccusage", "#!/bin/sh\n[ \"$1\" = --version ] && { echo 'ccusage 20.0.20'; exit 0; }\n"
                       "printf '{\"session\": [{\"period\": \"" + s1 + "\", \"totalCost\": 0.07, \"totalTokens\": 4800000, "
                       "\"modelBreakdowns\": [{\"modelName\": \"claude-fable-5-1\", \"cost\": 0.07, \"inputTokens\": 100}]}]}'\n")
    pin = str(REPO / "bin" / "pin")
    for sid, alias, extra in ((s1, "standup-prep", ["--keep", "--note", "Tuesday standup, uses jira-cards"]),
                              (s2, "cc-collector", []), (s3, "rc-mower", []), (s4, "insurance", ["--fork", "--worktree"])):
        subprocess.run([pin, "add", sid, alias], check=True, capture_output=True)
        if extra:
            subprocess.run([pin, "edit", alias, *extra], check=True, capture_output=True)
    ps = fx.root / "ps.txt"
    ps.write_text(f"claude --resume {s1}\n")
    os.environ["CLAUDE_PINS_PS"] = str(ps)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp(pin, [pin])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    out = b""

    def drain(seconds: float):
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return

    drain(2.0)
    for k in keys:
        os.write(fd, k)
        drain(1.2)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    screen = Screen(COLS, ROWS)
    pyte.ByteStream(screen).feed(out)
    fx.tearDown()
    return screen


def to_svg(screen: pyte.Screen) -> str:
    cw, ch = 8.4, 18
    width, height = int(COLS * cw + 24), int(ROWS * ch + 24)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<rect width="100%" height="100%" rx="8" fill="#1c1c1c"/>',
             '<g font-family="JetBrains Mono, Fira Code, SFMono-Regular, Menlo, Consolas, monospace" font-size="14" xml:space="preserve">']
    for y in range(ROWS):
        row = screen.buffer[y]
        x = 0
        while x < COLS:
            ch_ = row[x]
            run_start = x
            style = (ch_.fg, ch_.bg, ch_.bold, ch_.reverse, ch_.strikethrough, ch_.italics)
            text = ""
            while x < COLS and (row[x].fg, row[x].bg, row[x].bold, row[x].reverse, row[x].strikethrough, row[x].italics) == style:
                text += row[x].data or " "      # a wide glyph's second cell is empty: it keeps the cell count right
                x += 1
            if not text.strip():
                continue
            fg = PALETTE.get(style[0], "#d0d0d0") if not str(style[0]).isalnum() or style[0] in PALETTE else f"#{style[0]}" if len(str(style[0])) == 6 else "#d0d0d0"
            if style[5] and fg == PALETTE["default"]:
                fg = DIM
            attrs = f'fill="{fg}"'
            if style[2]:
                attrs += ' font-weight="bold"'
            if style[4]:
                attrs += ' text-decoration="line-through"'
            if style[3]:
                parts.append(f'<rect x="{12 + run_start * cw:.1f}" y="{12 + y * ch}" width="{len(text) * cw:.1f}" height="{ch}" fill="#3a3a3a"/>')
            parts.append(f'<text x="{12 + run_start * cw:.1f}" y="{12 + y * ch + 14}" {attrs}>{html.escape(text)}</text>')
    parts.append("</g></svg>")
    return "\n".join(parts)


def main() -> int:
    screen = capture([])
    text = "\n".join(line.rstrip() for line in screen.display).rstrip() + "\n"
    (REPO / "docs" / "preview.txt").write_text(text)
    (REPO / "docs" / "preview.svg").write_text(to_svg(screen))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
