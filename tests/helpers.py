"""Shared test scaffolding: an isolated HOME with fake Claude config, fake clock, stub binaries."""

from __future__ import annotations

import json
import sys
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN = REPO / "bin" / "pin"

ENV_KEYS = [
    "HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR",
    "CLAUDE_PINS_FILE", "CLAUDE_PINS_KEYMAP", "CLAUDE_PINS_SORT", "CLAUDE_PINS_NO_FZF",
    "CLAUDE_PINS_EXPIRE_WARN", "NO_COLOR", "CLAUDE_PINS_COLOR", "PATH", "CLAUDE_PINS_NOW",
    "CLAUDE_PINS_PS", "CLAUDE_PINS_FZF", "CLAUDE_PINS_CCUSAGE", "CLAUDE_PINS_TEST_INPUT",
    "COLUMNS", "LINES", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_PINS_FZF_STUB_VERSION",
    "FZF_COLUMNS", "CLAUDE_PINS_GLYPHS", "LC_ALL", "LC_CTYPE", "LANG",
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

