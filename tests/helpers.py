"""Shared test scaffolding: an isolated HOME with fake Claude config, fake clock, stub binaries."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)

REPO = Path(__file__).resolve().parent.parent
PIN = REPO / "bin" / "pin"

ENV_KEYS = [
    "HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR",
    "CLAUDE_PINS_FILE", "CLAUDE_PINS_KEYMAP", "CLAUDE_PINS_SORT", "CLAUDE_PINS_NO_FZF",
    "CLAUDE_PINS_EXPIRE_WARN", "NO_COLOR", "CLAUDE_PINS_COLOR", "PATH", "CLAUDE_PINS_NOW",
    "CLAUDE_PINS_PS", "CLAUDE_PINS_FZF", "CLAUDE_PINS_CCUSAGE", "CLAUDE_PINS_TEST_INPUT",
    "COLUMNS", "LINES", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_PINS_FZF_STUB_VERSION",
    "FZF_COLUMNS", "CLAUDE_PINS_GLYPHS", "LC_ALL", "LC_CTYPE", "LANG", "CLAUDE_PINS_TUI_SCRIPT",
    "CLAUDE_PINS_TUI_LOG", "TERM", "ESCDELAY", "CLAUDE_PINS_OS", "TERM_PROGRAM", "LC_TERMINAL", "ITERM_PROFILE",
    "KITTY_WINDOW_ID", "KITTY_CONFIG_DIRECTORY", "ALACRITTY_WINDOW_ID",
]


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def session_records(session_id: str, cwd: str, *, title="A session", custom=None,
                    prompt="hello there", answer="hi back", model="claude-fable-5-1",
                    effort="high", mode="auto", branch="main", n_turns=1,
                    usage=None, ts="2026-09-01T10:00:00.000Z") -> list[dict]:
    usage = usage or {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 120000, "output_tokens": 50}
    common = {"cwd": cwd, "sessionId": session_id, "version": "2.1.263", "gitBranch": branch,
              "isSidechain": False, "userType": "external", "timestamp": ts}
    recs: list[dict] = [{"type": "permission-mode", "permissionMode": mode, "sessionId": session_id},
                        {"type": "ai-title", "aiTitle": title, "sessionId": session_id}]
    if custom:
        recs.append({"type": "custom-title", "customTitle": custom, "sessionId": session_id})
    for i in range(n_turns):
        recs.append({**common, "type": "user", "uuid": f"u{i}", "parentUuid": None,
                     "message": {"role": "user", "content": prompt if i == n_turns - 1 else f"prompt {i}"}})
        recs.append({**common, "type": "assistant", "uuid": f"a{i}", "parentUuid": f"u{i}", "effort": effort,
                     "message": {"model": model, "role": "assistant", "usage": usage,
                                 "content": [{"type": "text", "text": answer if i == n_turns - 1 else f"answer {i}"}]}})
    recs.append({"type": "last-prompt", "lastPrompt": prompt, "sessionId": session_id})
    return recs


class Sandbox(unittest.TestCase):
    """An isolated HOME per test with a fake ``~/.claude/projects`` and a stub PATH."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.root = Path(tempfile.mkdtemp(prefix="pins-test-")).resolve()  # macOS: /var → /private/var
        self.home = self.root / "home"
        self.home.mkdir()
        self.claude_dir = self.home / ".claude"
        self.projects = self.claude_dir / "projects"
        self.projects.mkdir(parents=True)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.argv_log = self.root / "claude-argv.json"
        os.environ["HOME"] = str(self.home)
        for k in ENV_KEYS:
            if k not in ("HOME", "PATH"):
                os.environ.pop(k, None)
        os.environ["PATH"] = f"{self.bindir}:/usr/bin:/bin:/usr/local/bin"
        os.environ["NO_COLOR"] = "1"
        os.environ["CLAUDE_PINS_OS"] = "linux"          # no alt-keys note, whatever the runner (macOS CI included)
        os.environ["COLUMNS"] = "100"
        os.environ["CLAUDE_PINS_GLYPHS"] = "emoji"     # what a UTF-8 terminal gets; the text set has its own tests
        self.write_settings({})
        self.stub_claude()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    # helpers ------------------------------------------------------------------

    def write_settings(self, data: dict):
        (self.claude_dir / "settings.json").write_text(json.dumps(data))

    def stub(self, name: str, body: str) -> Path:
        path = self.bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def stub_claude(self):
        self.stub("claude", "#!/bin/sh\n"
                  f"python3 -c 'import json,os,sys; json.dump({{\"argv\": sys.argv[1:], \"cwd\": os.getcwd()}}, open(\"{self.argv_log}\", \"w\"))' \"$@\"\n")

    def claude_calls(self) -> dict | None:
        if not self.argv_log.exists():
            return None
        return json.loads(self.argv_log.read_text())

    def project_dir(self, cwd: str) -> Path:
        return self.projects / cwd.replace("/", "-").replace(".", "-")

    def make_session(self, session_id: str, cwd: str | None = None, age_days: float = 1.0, **kw) -> Path:
        cwd = cwd or str(self.home / "git" / "proj")
        Path(cwd).mkdir(parents=True, exist_ok=True)
        path = self.project_dir(cwd) / f"{session_id}.jsonl"
        write_jsonl(path, session_records(session_id, cwd, **kw))
        self.age(path, age_days)
        return path

    def age(self, path: Path, days: float):
        t = time.time() - days * 86400
        os.utime(path, (t, t))

    def store_path(self) -> Path:
        return self.home / ".local" / "state" / "claude-pins" / "pins.json"

    def run_pin(self, *args: str, input: str = "", env: dict | None = None):
        import subprocess
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run([sys.executable, str(PIN), *args], input=input, capture_output=True, text=True, env=e)

    def run_pin_tty(self, *args: str, columns: int = 100) -> str:
        """``pin`` with a pseudo-terminal on stdout (the tables add labels there); returns the plain text."""
        import pty
        import re
        import subprocess
        master, slave = pty.openpty()
        import fcntl, struct, termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, columns, 0, 0))
        env = {**os.environ, "NO_COLOR": "1"}
        p = subprocess.Popen([sys.executable, str(PIN), *args], stdin=subprocess.DEVNULL, stdout=slave,
                             stderr=subprocess.PIPE, env=env)
        os.close(slave)
        chunks = []
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        os.close(master)
        p.wait(timeout=30)
        text = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
        return re.sub(r"\x1b\[[0-9;]*m", "", text)


class FzfSandbox(Sandbox):
    """Sandbox with the scripted fzf stub wired in."""

    def setUp(self):
        super().setUp()
        self.script = self.root / "fzf-script.jsonl"
        self.fzf_log = self.root / "fzf-log.jsonl"
        os.environ["CLAUDE_PINS_FZF"] = str(REPO / "tests" / "fzf_stub.py")
        os.environ["CLAUDE_PINS_FZF_SCRIPT"] = str(self.script)
        os.environ["CLAUDE_PINS_FZF_LOG"] = str(self.fzf_log)
        os.environ["CLAUDE_PINS_EXE"] = str(PIN)

    def steps(self, *steps: dict):
        with open(self.script, "w") as fh:
            for s in steps:
                fh.write(json.dumps(s) + "\n")

    def fzf_calls(self) -> list[dict]:
        if not self.fzf_log.exists():
            return []
        return [json.loads(ln) for ln in self.fzf_log.read_text().splitlines() if ln]


class TuiSandbox(Sandbox):
    """Sandbox with the built-in picker's scripted terminal wired in: no fzf, every screen played from
    ``steps()`` and recorded in ``screens()``. A step is ``{"send": [...]}`` (see ``tui.ScriptedTerminal``)
    or, for short, the list of tokens itself."""

    def setUp(self):
        super().setUp()
        self.script = self.root / "tui-script.jsonl"
        self.tui_log = self.root / "tui-log.jsonl"
        os.environ["CLAUDE_PINS_TUI_SCRIPT"] = str(self.script)
        os.environ["CLAUDE_PINS_TUI_LOG"] = str(self.tui_log)
        os.environ["CLAUDE_PINS_NO_FZF"] = "1"
        os.environ["LINES"] = "30"
        os.environ["CLAUDE_PINS_EXE"] = str(PIN)

    def steps(self, *steps):
        """The screens' steps for the next run; the log of the previous run is dropped."""
        with open(self.script, "w") as fh:
            for s in steps:
                fh.write(json.dumps({"send": s} if isinstance(s, list) else s) + "\n")
        if self.tui_log.exists():
            self.tui_log.unlink()

    def screens(self) -> list[dict]:
        """One record per screen drawn: prompt, header, query, items, expect, frame and result."""
        if not self.tui_log.exists():
            return []
        return [json.loads(ln) for ln in self.tui_log.read_text().splitlines() if ln]

    def frame(self, n: int = -1) -> str:
        return "\n".join(self.screens()[n]["frame"])


class PtyMixin:
    """Driving ``pin`` in a pseudo-terminal: spawn it at a size, wait for what it draws, read until it
    exits. Each step waits on a redraw, so the tests that use this are few and each proves several
    things. Used with a Sandbox."""

    def stub_claude_tty(self):
        """A claude stub that also records whether the terminal was back in cooked mode when it started."""
        self.stub("claude", "#!/bin/sh\npython3 -c 'import json,os,sys,termios; a = termios.tcgetattr(0)[3]; "
                  "json.dump({\"argv\": sys.argv[1:], \"cwd\": os.getcwd(), "
                  "\"cooked\": bool(a & termios.ICANON and a & termios.ECHO)}, "
                  f"open(\"{self.argv_log}\", \"w\"))' \"$@\"\n")
        self.out = b""          # what wait_for has read since the last clear
        self.raw = b""          # everything read, uncleared

    def assertScreenRestoredOnce(self):
        """--no-clear keeps the alternate screen up between fzf runs; it is left exactly once, at the end."""
        self.assertEqual(self.raw.count(b"\x1b[?1049l"), 1, "the alternate screen was left more than once")
        self.assertGreater(self.raw.rfind(b"\x1b[?1049l"), self.raw.rfind(b"\x1b[?1049h"))

    def wait_for(self, fd: int, pattern: str, timeout: float = 15.0, *, fresh: bool = False, fail: bool = True) -> str:
        """Read the terminal until ``pattern`` shows in the colour-stripped stream, or fail with what came
        (with ``fail`` off, return it: a way to read for a while).
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
        if fail:
            self.fail(f"{pattern!r} never appeared; terminal so far:\n{plain(self.out.decode('utf-8', 'replace'))[-800:]}")
        return plain(self.out.decode("utf-8", "replace"))

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

    def spawn(self, rows: int, cols: int = 100, *args: str, shell: bool = False) -> tuple[int, int]:
        """``pin args`` in a new pseudo-terminal of ``rows`` × ``cols``; with ``shell`` an interactive bash
        with ``pin`` on its PATH instead, for what needs job control."""
        import fcntl, pty, struct, termios
        size = struct.pack("HHHH", rows, cols, 0, 0)
        pid, fd = pty.fork()
        if pid == 0:  # the picker; its exec of the claude stub inherits the terminal
            fcntl.ioctl(0, termios.TIOCSWINSZ, size)     # before exec, so Python never sees the default size
            # os.environ, not the C environ: an earlier test's ``import readline`` exported the real
            # terminal's LINES and COLUMNS there, and the picker would size itself by them
            env = dict(os.environ)
            if shell:
                os.symlink(PIN, self.bindir / "pin")
                os.symlink(sys.executable, self.bindir / "python3")     # the shebang's python3 is the test's
                env["PS1"] = "$ "
                os.execve(shutil.which("bash"), ["bash", "--norc", "--noprofile", "-i"], env)
            os.execve(sys.executable, [sys.executable, str(PIN), *args], env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
        return pid, fd


    def plain_out(self) -> str:
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", self.out.decode("utf-8", "replace"))
