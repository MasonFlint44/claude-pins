#!/usr/bin/env python3
"""Assert that a given fzf binary accepts every option/binding the picker uses (run in CI against 0.44.1)."""
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from claude_pins.keymap import ACTIONS  # noqa: E402

fzf = sys.argv[1] if len(sys.argv) > 1 else "fzf"
keys = [a.key for a in ACTIONS if a.key and a.key not in ("enter", "tab")]
args = [fzf, "--filter", "x", "--layout=reverse", "--delimiter=\t", "--with-nth=3..", "--nth=2", "--tiebreak=index",
        "--no-sort", "--print-query", "--info=inline-right", "--no-separator", "--pointer", ">", "--marker", "▌",
        "--prompt", "pins › ", "--cycle", "--ellipsis", "…", "--ansi", "--header", "h\nflash", "--expect", ",".join(keys),
        "--multi", "--preview", "echo {1}", "--preview-window", "down,55%,border-rounded,wrap",
        "--bind", "focus:transform-preview-label(echo {1})", "--bind", "start:pos(2)",
        "--border", "bottom", "--border-label", " 2 expired ", "--border-label-pos", "2:bottom",
        "--bind", "alt-t:change-header(✓ touched)+reload(true)", "--bind", "enter:become(echo {1})",
        "--expect", "ctrl-r,ctrl-alt-r", "--disabled"]
p = subprocess.run(args, input="a\tb\tc\n", capture_output=True, text=True)
if p.returncode not in (0, 1):
    print("fzf rejected the picker's option grammar:", p.stderr.strip())
    sys.exit(1)
print("fzf grammar ok:", subprocess.run([fzf, "--version"], capture_output=True, text=True).stdout.strip())
