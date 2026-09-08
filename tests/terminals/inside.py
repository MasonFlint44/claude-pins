"""The driver ``tests/terminals/run.sh`` runs inside the container: an X server, a fixture HOME with four
pins, then each terminal in turn runs ``pin _keys`` while xdotool presses every key in ``KEYS`` and
clicks, and ``pin`` for a screenshot.

The report's columns: ``missing`` keys never reached the app (the terminal kept them: gnome-terminal's
F10 menu and F11, konsole's F11, tmux's ctrl-b prefix and the key after it); ``wrong`` arrived under
another name (stock xterm sends alt keys 8-bit, so they come as accented letters until
``XTerm*metaSendsEscape: true``; its backspace is ^H, so alt-bspace is ESC ^H); ``extra`` are events
nothing pressed. An F key that a terminal keeps would keep the focus too, so an esc and a ``q`` follow
each one and are dropped from the comparison when they reached the app.

Output in /out: <terminal>.keys.log (the raw terminal stream), <terminal>.png, results.json, report.md.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/repo")
from tests.helpers import Sandbox  # noqa: E402

OUT = Path("/out")
ONLY = sys.argv[1:]      # terminal names to run, or all

# ---- what to press: (xdotool key, expected event name) --------------------------------------------

KEYS: list[tuple[str, str]] = [
    ("a", "a"), ("space", " "), ("Return", "enter"), ("Tab", "tab"), ("shift+Tab", "shift-tab"),
    ("BackSpace", "bspace"), ("ctrl+h", "ctrl-h"), ("Delete", "del"), ("Insert", "insert"),
    ("Up", "up"), ("Down", "down"), ("Left", "left"), ("Right", "right"), ("Home", "home"), ("End", "end"),
    ("Prior", "pgup"), ("Next", "pgdn"),
    ("shift+Up", "shift-up"), ("shift+Down", "shift-down"), ("shift+Left", "shift-left"), ("shift+Right", "shift-right"),
    ("alt+Up", "alt-up"), ("alt+Down", "alt-down"), ("ctrl+Up", "ctrl-up"), ("ctrl+Down", "ctrl-down"),
    ("F1", "f1"), ("F2", "f2"), ("F3", "f3"), ("F4", "f4"), ("F5", "f5"), ("F6", "f6"), ("F7", "f7"), ("F8", "f8"),
    ("F9", "f9"), ("F10", "f10"), ("F11", "f11"), ("F12", "f12"),
    ("ctrl+x", "ctrl-x"), ("ctrl+r", "ctrl-r"), ("ctrl+t", "ctrl-t"), ("ctrl+space", "ctrl-space"),
    ("ctrl+a", "ctrl-a"), ("ctrl+e", "ctrl-e"), ("ctrl+b", "ctrl-b"), ("ctrl+f", "ctrl-f"), ("ctrl+d", "ctrl-d"),
    ("ctrl+u", "ctrl-u"), ("ctrl+w", "ctrl-w"), ("ctrl+y", "ctrl-y"), ("ctrl+k", "ctrl-k"), ("ctrl+n", "ctrl-n"),
    ("ctrl+p", "ctrl-p"), ("ctrl+j", "ctrl-j"), ("ctrl+l", "ctrl-l"), ("ctrl+o", "ctrl-o"), ("ctrl+g", "ctrl-g"),
    ("ctrl+v", "ctrl-v"), ("ctrl+q", "ctrl-q"), ("ctrl+s", "ctrl-s"),
    ("alt+o", "alt-o"), ("alt+w", "alt-w"), ("alt+i", "alt-i"), ("alt+t", "alt-t"), ("alt+k", "alt-k"),
    ("alt+x", "alt-x"), ("alt+a", "alt-a"), ("alt+p", "alt-p"), ("alt+z", "alt-z"), ("alt+s", "alt-s"),
    ("alt+v", "alt-v"), ("alt+r", "alt-r"), ("alt+e", "alt-e"), ("alt+n", "alt-n"),
    ("alt+b", "alt-b"), ("alt+d", "alt-d"), ("alt+f", "alt-f"), ("alt+BackSpace", "alt-bspace"),
    ("alt+shift+z", "alt-Z"), ("alt+1", "alt-1"), ("ctrl+alt+r", "ctrl-alt-r"), ("ctrl+alt+t", "ctrl-alt-t"),
    ("Escape", "esc"),
]

# ---- the terminals ----------------------------------------------------------------------------

def xterm(*cmd, meta=False):
    rm = ["-xrm", "XTerm*metaSendsEscape: true"] if meta else []
    return ["xterm", "-geometry", "120x36", *rm, "-e", *cmd]


TERMINALS = {
    "xterm": lambda *cmd: xterm(*cmd),
    "xterm-metaSendsEscape": lambda *cmd: xterm(*cmd, meta=True),
    "gnome-terminal": lambda *cmd: ["dbus-run-session", "--", "gnome-terminal", "--wait", "--geometry=120x36", "--", *cmd],
    "konsole": lambda *cmd: ["konsole", "--nofork", "-e", *cmd],
    "kitty": lambda *cmd: ["kitty", "-o", "initial_window_width=120c", "-o", "initial_window_height=36c",
                           "-o", "confirm_os_window_close=0", *cmd],
    "alacritty": lambda *cmd: ["alacritty", "-o", "window.dimensions.columns=120", "-o", "window.dimensions.lines=36",
                               "-e", *cmd],
    "ghostty": lambda *cmd: ["ghostty", "--window-width=120", "--window-height=36", "--confirm-close-surface=false",
                             "--gtk-single-instance=false", "-e", *cmd],
    "tmux-in-xterm": lambda *cmd: xterm("tmux", "-f", "/work/tmux.conf", "new-session", shlex.join(cmd), meta=True),
}
WINDOW_CLASS = {"xterm": "XTerm", "xterm-metaSendsEscape": "XTerm", "gnome-terminal": "Gnome-terminal",
                "konsole": "konsole", "kitty": "kitty", "alacritty": "Alacritty", "ghostty": "com.mitchellh.ghostty",
                "tmux-in-xterm": "XTerm"}


def sh(*args, check=False, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, check=check, **kw)


def xdo(*args) -> str:
    return sh("xdotool", *args).stdout.strip()


def plain(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    text = re.sub(r"\x1b[^[]", "", text)
    return text.replace("\r", "")


def wait_for(path: Path, pattern: str, timeout: float = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if re.search(pattern, plain(path.read_text(errors="replace"))):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def find_window(cls: str, timeout: float = 20) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        wids = xdo("search", "--onlyvisible", "--class", cls).split()
        if wids:
            return wids[-1]
        time.sleep(0.3)
    return None


def focus(wid: str) -> None:
    xdo("windowactivate", "--sync", wid)
    xdo("windowfocus", "--sync", wid)
    geo = dict(line.split("=") for line in xdo("getwindowgeometry", "--shell", wid).splitlines())
    x, y = int(geo["X"]) + int(geo["WIDTH"]) // 2, int(geo["Y"]) + int(geo["HEIGHT"]) // 2
    xdo("mousemove", str(x), str(y))
    time.sleep(0.3)


def events_from(log: Path) -> list[str]:
    text = plain(log.read_text(errors="replace"))
    if "press keys" not in text:
        return []
    body = text.split("press keys", 1)[1].split("\n", 1)[1] if "\n" in text.split("press keys", 1)[1] else ""
    lines = [line for line in body.split("\n") if line != "" and not line.startswith("Script done")]
    return lines


def run_keys(name: str, env: dict[str, str]) -> dict:
    log = OUT / f"{name}.keys.log"
    log.unlink(missing_ok=True)
    launcher = TERMINALS[name]("bash", "/work/wrap.sh", "keys", name)
    proc = subprocess.Popen(launcher, env=env, stdout=subprocess.DEVNULL, stderr=open(OUT / f"{name}.stderr", "w"))
    result = {"terminal": name, "started": False, "events": [], "missing": [], "wrong": [], "extra": [], "mouse": []}
    wid = find_window(WINDOW_CLASS[name])
    if not wid or not wait_for(log, "press keys"):
        proc.kill()
        return result
    result["started"] = True
    focus(wid)
    # one key at a time, a pause after each so esc alone stays esc and nothing merges
    for key, _ in KEYS:
        xdo("key", "--clearmodifiers", key)
        time.sleep(0.15)
        if key.startswith("F"):
            # a terminal that keeps an F key for its menu or help would keep the focus too: esc closes that,
            # and an esc that reached the app right after an F key is dropped from the comparison
            time.sleep(0.3); xdo("key", "--clearmodifiers", "Escape"); time.sleep(0.3)
            xdo("key", "--clearmodifiers", "q"); time.sleep(0.15)      # keeps two esc apart (two in a row end pin _keys)
    time.sleep(0.5)
    # the mouse: a click, a double-click, a right click, the wheel up and down, all on one cell
    geo = dict(line.split("=") for line in xdo("getwindowgeometry", "--shell", wid).splitlines())
    x, y = int(geo["X"]) + 200, int(geo["Y"]) + 160
    xdo("mousemove", str(x), str(y))
    xdo("click", "1"); time.sleep(0.7)
    xdo("click", "--repeat", "2", "--delay", "120", "1"); time.sleep(0.7)
    xdo("click", "3"); time.sleep(0.4)
    xdo("click", "4"); time.sleep(0.3)
    xdo("click", "5"); time.sleep(0.5)
    xdo("key", "Escape"); time.sleep(0.3); xdo("key", "Escape")
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        xdo("key", "ctrl+c"); time.sleep(0.5)
        proc.kill()
    got = events_from(log)
    result["events"] = got
    expected = [e for _, e in KEYS]
    is_mouse = lambda e: re.search(r" at \d+,\d+$", e) is not None
    keys_got = [e for e in got if not is_mouse(e)]
    cleaned = []
    for e in keys_got:
        if e in ("esc", "q") and cleaned and (re.fullmatch(r"f\d+", cleaned[-1]) or cleaned[-1] == "esc" and len(cleaned) > 1 and re.fullmatch(r"f\d+", cleaned[-2])):
            continue
        cleaned.append(e)
    keys_got = cleaned
    mouse_got = [e for e in got if is_mouse(e)]
    while keys_got and keys_got[-1] == "esc" and keys_got.count("esc") > 1:
        keys_got = keys_got[:-1]                        # the two closing esc presses; the tested one stays
    import difflib
    sm = difflib.SequenceMatcher(a=expected, b=keys_got, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            result["missing"] += expected[i1:i2]
        elif tag == "insert":
            result["extra"] += keys_got[j1:j2]
        elif tag == "replace":
            exp, act = expected[i1:i2], keys_got[j1:j2]
            for k in range(max(len(exp), len(act))):
                if k < len(exp) and k < len(act):
                    result["wrong"].append((exp[k], act[k]))
                elif k < len(exp):
                    result["missing"].append(exp[k])
                else:
                    result["extra"].append(act[k])
    result["mouse"] = mouse_got
    result["mouse_ok"] = all(any(re.fullmatch(pat, m) for m in mouse_got) for pat in
                             (r"left-click at \d+,\d+", r"double-left-click at \d+,\d+", r"right-click at \d+,\d+",
                              r"scroll-up at \d+,\d+", r"scroll-down at \d+,\d+"))
    return result


def run_picker(name: str, env: dict[str, str]) -> bool:
    log = OUT / f"{name}.picker.log"
    log.unlink(missing_ok=True)
    launcher = TERMINALS[name]("bash", "/work/wrap.sh", "picker", name)
    proc = subprocess.Popen(launcher, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wid = find_window(WINDOW_CLASS[name])
    ok = bool(wid) and wait_for(log, r"pins")
    if ok:
        time.sleep(2.5)                                 # the pane (ccusage stub) and the emoji font
        sh("import", "-window", wid, str(OUT / f"{name}.png"))
        focus(wid)
        xdo("key", "Down"); time.sleep(0.5)
        sh("import", "-window", wid, str(OUT / f"{name}.2.png"))
        xdo("key", "Escape")
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    return ok


# ---- the fixture --------------------------------------------------------------------------------

class Fixture(Sandbox):
    def runTest(self):
        pass


def build_fixture() -> tuple[Fixture, dict[str, str]]:
    fx = Fixture(); fx.setUp()
    os.environ.pop("NO_COLOR", None)
    os.environ["CLAUDE_PINS_COLOR"] = "1"
    home = fx.home
    s1, s2, s3, s4 = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                      "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444")
    fx.make_session(s1, cwd=str(home / "git" / "dotclaude"), age_days=2.1, title="Standup prep", n_turns=42,
                    prompt="so the pin command should also touch the transcript when it opens a fork?",
                    answer="Yes. The picker can bind keys to run a command and reload the list afterwards.",
                    usage={"input_tokens": 2, "cache_creation_input_tokens": 1200, "cache_read_input_tokens": 120000})
    fx.make_session(s2, cwd=str(home / "git" / "command-center"), age_days=9.3, title="Command center collector", n_turns=12)
    fx.make_session(s3, cwd=str(home), age_days=26.2, title="Navimow schedule debug", n_turns=5)
    fx.make_session(s4, cwd=str(home / "git" / "foo" / ".claude" / "worktrees" / "x"), age_days=1.4, title="USAA restructure", n_turns=8)
    fx.stub("ccusage", "#!/bin/sh\n[ \"$1\" = --version ] && { echo 'ccusage 20.0.20'; exit 0; }\n"
                       "printf '{\"session\": [{\"period\": \"" + s1 + "\", \"totalCost\": 0.07, \"totalTokens\": 4800000, "
                       "\"modelBreakdowns\": [{\"modelName\": \"claude-fable-5-1\", \"cost\": 0.07, \"inputTokens\": 100}]}]}'\n")
    pin = "/repo/bin/pin"
    for sid, alias, extra in ((s1, "standup-prep", ["--keep", "--note", "Tuesday standup"]),
                              (s2, "cc-collector", []), (s3, "rc-mower", []), (s4, "insurance", ["--fork", "--worktree"])):
        subprocess.run([sys.executable, pin, "add", sid, alias], check=True, capture_output=True)
        if extra:
            subprocess.run([sys.executable, pin, "edit", alias, *extra], check=True, capture_output=True)
    ps = fx.root / "ps.txt"
    ps.write_text(f"claude --resume {s1}\n")
    os.environ["CLAUDE_PINS_PS"] = str(ps)
    os.environ["CLAUDE_PINS_NO_FZF"] = "1"
    os.environ["PATH"] = f"{fx.bindir}:/usr/bin:/bin:/usr/local/bin"
    env = dict(os.environ)
    env.update({"DISPLAY": ":99", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TERM": "xterm-256color",
                "LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe", "XDG_RUNTIME_DIR": "/tmp/xdg-run",
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("COLUMNS", None); env.pop("LINES", None)
    keep = [k for k in env if k.startswith(("CLAUDE_PINS_", "CLAUDE_CONFIG", "XDG_", "HOME", "PATH", "LANG", "LC_", "TERM"))]
    Path("/out/env.sh").write_text("".join(f"export {k}={shlex.quote(env[k])}\n" for k in keep))
    return fx, env


def main() -> int:
    OUT.mkdir(exist_ok=True)
    Path("/tmp/xdg-run").mkdir(exist_ok=True)
    xvfb = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1600x1000x24", "+extension", "GLX", "+render", "-noreset"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    wm = subprocess.Popen(["openbox"], env={**os.environ, "DISPLAY": ":99"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    fx, env = build_fixture()
    results = []
    try:
        for name in (ONLY or TERMINALS):
            print(f"== {name}", flush=True)
            r = run_keys(name, env)
            r["picker"] = run_picker(name, env) if r["started"] else False
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "events"}), flush=True)
    finally:
        (OUT / "results.json").write_text(json.dumps(results, indent=1))
        report = ["| terminal | started | keys missing | keys wrong | extra | mouse | screenshot |", "|---|---|---|---|---|---|---|"]
        for r in results:
            report.append(f"| {r['terminal']} | {'yes' if r['started'] else 'NO'} | {', '.join(r['missing']) or '-'} | "
                          f"{', '.join(f'{a}→{b}' for a, b in r['wrong']) or '-'} | {', '.join(r['extra']) or '-'} | "
                          f"{'ok' if r.get('mouse_ok') else ', '.join(r['mouse']) or 'none'} | {'yes' if r.get('picker') else 'no'} |")
        (OUT / "report.md").write_text("\n".join(report) + "\n")
        print("\n".join(report))
        wm.kill(); xvfb.kill()
        fx.tearDown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
