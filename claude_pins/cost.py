"""Session cost via ccusage (offline), cached per transcript mtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Cost:
    status: str            # "ok" | "zero" | "missing" | "unknown" | "error"
    total_cost: float = 0.0
    total_tokens: int = 0

    def line(self, model: str = "", messages: int = 0) -> str:
        if self.status == "missing":
            return "install ccusage for session cost · npm i -g ccusage"
        if self.status == "ok":
            return f"est ${self.total_cost:.2f} (ccusage)"
        if self.status == "zero":
            if messages:
                return f"pricing unavailable for {model or 'this model'} · update ccusage"
            return "est $0.00 (ccusage)"
        if self.status == "unknown":
            return "not indexed by ccusage yet"
        return "unavailable (ccusage error)"


def ccusage_bin() -> str | None:
    override = os.environ.get("CLAUDE_PINS_CCUSAGE")
    if override:
        return override if os.path.exists(override) or shutil.which(override) else None
    return shutil.which("ccusage")


def _cache_file(session_id: str) -> Path:
    return config.cache_dir() / "cost" / f"{session_id}.json"


def session_cost(session_id: str, transcript: str | os.PathLike | None, *, timeout: float = 8.0) -> Cost:
    binary = ccusage_bin()
    if not binary:
        return Cost("missing")
    try:
        mtime = os.stat(transcript).st_mtime if transcript else 0.0
    except OSError:
        mtime = 0.0
    cache = _cache_file(session_id)
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("mtime") == mtime and data.get("status") in ("ok", "zero"):
            return Cost(data["status"], float(data.get("total_cost", 0)), int(data.get("total_tokens", 0)))
    except (OSError, ValueError, TypeError):
        pass
    try:
        p = subprocess.run([binary, "session", "--id", session_id, "--json", "--offline"],
                           capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "NO_COLOR": "1"})
        payload = json.loads(p.stdout) if p.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return Cost("error")
    if not isinstance(payload, dict):
        return Cost("unknown")
    total = float(payload.get("totalCost") or 0.0)
    tokens = int(payload.get("totalTokens") or 0)
    cost = Cost("ok" if total > 0 else "zero", total, tokens)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"mtime": mtime, "status": cost.status, "total_cost": total,
                                     "total_tokens": tokens}), encoding="utf-8")
    except OSError:
        pass
    return cost


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def price_check_online(timeout: float = 6.0) -> str:
    """For ``pin doctor``: can ccusage price a known session online? Returns a one-line verdict."""
    binary = ccusage_bin()
    if not binary:
        return "ccusage: not installed (npm i -g ccusage) — cost lines will say so"
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=timeout)
        version = p.stdout.strip().replace("ccusage ", "") or "?"
    except (OSError, subprocess.SubprocessError):
        return "ccusage: present but not runnable"
    try:
        p = subprocess.run([binary, "session", "--json"], capture_output=True, text=True, timeout=timeout)
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        return f"ccusage {version}: online price check timed out after {timeout:.0f}s (offline table still used)"
    except (OSError, subprocess.SubprocessError):
        ok = False
    return f"ccusage {version}: {'online price table reachable' if ok else 'online check failed; offline table in use'}"
