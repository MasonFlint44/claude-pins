#!/usr/bin/env python3
"""Check a real fzf binary against the picker: every option/binding it uses must be accepted, and a query
must match the rows the picker feeds it (run in CI against 0.44.1, the supported floor)."""
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from claude_pins import fzf as wrapper  # noqa: E402
from claude_pins.keymap import ACTIONS  # noqa: E402

binary = sys.argv[1] if len(sys.argv) > 1 else "fzf"
keys = [a.key for a in ACTIONS if a.key and a.key not in ("enter", "tab")]
args = wrapper.build_args(binary, prompt="pins › ", header="h\nflash", expect=keys, query="x", multi=True,
                          preview="echo {1}", preview_label_cmd="echo {1}", pos=2, border_label=" 2 expired ",
                          extra=["--bind", "alt-t:change-header(✓ touched)+reload(true)",
                                 "--bind", "enter:become(echo {1})", "--expect", "ctrl-r,ctrl-alt-r"],
                          disabled=True)
p = subprocess.run(args + ["--filter", "x"], input="a\tb\n", capture_output=True, text=True)
if p.returncode not in (0, 1):
    print("fzf rejected the picker's option grammar:", p.stderr.strip())
    sys.exit(1)

# The picker's rows: coloured display text after a hidden id. Typing a word from the row must keep it, and
# the hidden id must come back untouched. (--nth on a hidden field silently matched nothing; see fzf.lines_for.)
# --filter with --no-sort prints the --with-nth view instead of the whole line (0.44 and 0.67 alike); the
# interactive picker prints whole lines, so the option is dropped here only.
items = [wrapper.Item("mower", "\x1b[1mmower\x1b[0m  rc mower session  ~/git/mower  3h"),
         wrapper.Item("other", "other  unrelated  ~/x  1d")]
rows = "\n".join(wrapper.lines_for(items)) + "\n"
for query, want in (("mow", ["mower"]), ("rc mower", ["mower"]), ("~/x", ["other"]), ("zzz", [])):
    args = [a for a in wrapper.build_args(binary, prompt="> ") if a != "--no-sort"] + ["--filter", query]
    p = subprocess.run(args,
                       input=rows, capture_output=True, text=True)
    got = [ln.split("\t", 1)[0] for ln in p.stdout.split("\n")[1:] if ln]  # line 1 is --print-query
    if got != want:
        print(f"fzf --filter {query!r} matched {got}, expected {want}")
        sys.exit(1)
print("fzf grammar and matching ok:", subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip())
