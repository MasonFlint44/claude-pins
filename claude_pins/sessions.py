"""Session discovery under ``~/.claude/projects``, expiry math, and already-open detection."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .model import UUID_RE, is_session_id

UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def now() -> float:
    raw = os.environ.get("CLAUDE_PINS_NOW")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return time.time()


# ---- discovery ------------------------------------------------------------------

def iter_transcripts(root: Path | None = None):
    """Yield every top-level session transcript path (newest first)."""
    root = root or config.projects_dir()
    found: list[tuple[float, Path]] = []
    try:
        project_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return []
    for d in project_dirs:
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if f.suffix == ".jsonl" and is_session_id(f.stem):
                try:
                    found.append((f.stat().st_mtime, f))
                except OSError:
                    continue
    found.sort(key=lambda t: t[0], reverse=True)
    return [f for _, f in found]


def find_transcript(session_id: str, hint: str | os.PathLike | None = None) -> Path | None:
    """Locate ``<id>.jsonl``: the hinted path first, then every project directory."""
    if hint:
        h = Path(hint)
        if h.is_file():
            return h
    root = config.projects_dir()
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return None
    for d in dirs:
        cand = d / f"{session_id}.jsonl"
        if cand.is_file():
            return cand
    return None


# ---- expiry ----------------------------------------------------------------------

@dataclass
class Expiry:
    state: str          # "ok" | "expiring" | "expired"
    age_seconds: float  # since last transcript write (0 when expired)
    remaining_days: float

    @property
    def expired(self) -> bool:
        return self.state == "expired"

    @property
    def expiring(self) -> bool:
        return self.state == "expiring"


def expiry_for(transcript: str | os.PathLike | None, *, cleanup_days: int | None = None,
               warn_days: int | None = None, at: float | None = None) -> Expiry:
    cleanup_days = config.cleanup_period_days() if cleanup_days is None else cleanup_days
    warn_days = config.expire_warn_days() if warn_days is None else warn_days
    at = now() if at is None else at
    try:
        mtime = os.stat(transcript).st_mtime if transcript else None
    except OSError:
        mtime = None
    if mtime is None:
        return Expiry("expired", 0.0, 0.0)
    age = max(0.0, at - mtime)
    remaining = cleanup_days - age / 86400
    state = "expiring" if remaining <= warn_days else "ok"
    return Expiry(state, age, max(0.0, remaining))


def format_age(seconds: float) -> str:
    """``2h`` under a day, then ``2d``."""
    if seconds < 3600:
        return f"{max(0, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


# ---- already-open detection -----------------------------------------------------

def process_table() -> list[str]:
    """Command lines of running processes; ``CLAUDE_PINS_PS`` points at a fake table for tests."""
    fake = os.environ.get("CLAUDE_PINS_PS")
    if fake is not None:
        try:
            return Path(fake).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    try:
        out = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return out.splitlines()


def open_session_ids(table: list[str] | None = None) -> set[str]:
    """Session IDs visible in the arguments of running ``claude`` processes."""
    table = process_table() if table is None else table
    ids: set[str] = set()
    for line in table:
        parts = line.split()
        if not parts:
            continue
        # Find the claude executable token: "claude", "/path/to/claude", "node .../claude"
        idx = next((i for i, tok in enumerate(parts[:3]) if os.path.basename(tok).lower().startswith("claude")), None)
        if idx is None:
            continue
        args = parts[idx + 1:]
        for i, tok in enumerate(args):
            if tok in ("--resume", "-r", "--session-id") and i + 1 < len(args) and is_session_id(args[i + 1]):
                ids.add(args[i + 1].lower())
            for flag in ("--resume=", "--session-id="):
                if tok.startswith(flag) and is_session_id(tok[len(flag):]):
                    ids.add(tok[len(flag):].lower())
    return ids


def session_id_from_env() -> str | None:
    for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        v = os.environ.get(key, "")
        if is_session_id(v):
            return v.lower()
    return None
