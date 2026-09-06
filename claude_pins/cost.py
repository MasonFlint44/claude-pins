"""Session cost via ccusage, cached per transcript mtime.

Offline first (bundled price table, no network). When the offline table has no price for a model
the session used (its entries come back at $0), fall back to one online run with a short timeout;
online failures are remembered for a few minutes so a preview pane never stalls repeatedly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

UPDATE_HINT = "npm i -g ccusage@latest"
ONLINE_RETRY_SECONDS = 600


@dataclass
class Cost:
    status: str                 # "ok" | "partial" | "missing" | "unknown" | "error"
    total_cost: float = 0.0
    total_tokens: int = 0
    source: str = "offline"     # "offline" | "online"
    unpriced: list[str] = field(default_factory=list)

    def line(self, model: str = "", messages: int = 0) -> str:
        if self.status == "missing":
            return f"install ccusage for session cost · {UPDATE_HINT}"
        if self.status == "ok":
            via = "ccusage online" if self.source == "online" else "ccusage"
            return f"est ${self.total_cost:.2f} ({via})"
        if self.status == "partial":
            models = ", ".join(_short(m) for m in self.unpriced) or model or "this model"
            head = f"est ≥ ${self.total_cost:.2f} · " if self.total_cost > 0 else ""
            return f"{head}no price for {models} · {UPDATE_HINT}"
        if self.status == "unknown":
            return "not indexed by ccusage yet"
        return "unavailable (ccusage error)"


def _short(model: str) -> str:
    return model[len("claude-"):] if model.startswith("claude-") else model


def ccusage_bin() -> str | None:
    override = os.environ.get("CLAUDE_PINS_CCUSAGE")
    if override:
        return override if os.path.exists(override) or shutil.which(override) else None
    return shutil.which("ccusage")


def _cache_file(session_id: str) -> Path:
    return config.cache_dir() / "cost" / f"{session_id}.json"


def _listing(binary: str, *, offline: bool, timeout: float) -> list[dict] | str:
    """Every session row from ``ccusage session --json`` (rows carry ``modelBreakdowns``), or an error string."""
    args = [binary, "session", "--json"] + (["--offline"] if offline else [])
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ, "NO_COLOR": "1"})
        if p.returncode != 0:
            return f"exit {p.returncode}"
        payload = json.loads(p.stdout) if p.stdout.strip() else {}
    except subprocess.TimeoutExpired:
        return "timeout"
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return str(e)
    rows = payload.get("session") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and rows is None:
        rows = payload.get("sessions")
    return rows if isinstance(rows, list) else "bad json"


def _row_for(rows: list[dict], session_id: str) -> dict | None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("sessionId") or r.get("period") or "") == session_id:
            return r
    return None


def unpriced_models(row: dict) -> list[str]:
    """Models with tokens but $0 in the row's per-model breakdown — the price table does not know them."""
    out = []
    for b in row.get("modelBreakdowns") or []:
        if not isinstance(b, dict):
            continue
        tokens = sum(int(b.get(k) or 0) for k in ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens"))
        if tokens > 0 and float(b.get("cost") or 0) == 0 and b.get("modelName"):
            out.append(str(b["modelName"]))
    return sorted(out)


def session_cost(session_id: str, transcript: str | os.PathLike | None, *, timeout: float = 8.0,
                 online_timeout: float = 5.0, allow_online: bool = True) -> Cost:
    binary = ccusage_bin()
    if not binary:
        return Cost("missing")
    try:
        mtime = os.stat(transcript).st_mtime if transcript else 0.0
    except OSError:
        mtime = 0.0
    cache = _cache_file(session_id)
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        cached = {}
    if cached.get("mtime") == mtime and cached.get("status") in ("ok", "partial"):
        retry = cached.get("online_retry_at", 0)
        if cached["status"] == "ok" or not allow_online or (retry and time.time() < retry):
            return Cost(cached["status"], float(cached.get("total_cost", 0)), int(cached.get("total_tokens", 0)),
                        cached.get("source", "offline"), list(cached.get("unpriced", [])))
    rows = _listing(binary, offline=True, timeout=timeout)
    if isinstance(rows, str):
        return Cost("error")
    row = _row_for(rows, session_id)
    if row is None:
        return Cost("unknown")
    cost = _from_row(row, "offline")
    online_retry_at = 0.0
    if cost.status == "partial" and allow_online:
        online = _listing(binary, offline=False, timeout=online_timeout)
        orow = _row_for(online, session_id) if isinstance(online, list) else None
        if orow is not None:
            better = _from_row(orow, "online")
            if better.status == "ok" or better.total_cost > cost.total_cost:
                cost = better
        if cost.status != "ok":
            online_retry_at = time.time() + ONLINE_RETRY_SECONDS
    _write_cache(cache, {"mtime": mtime, "status": cost.status, "total_cost": cost.total_cost,
                         "total_tokens": cost.total_tokens, "source": cost.source,
                         "unpriced": cost.unpriced, "online_retry_at": online_retry_at})
    return cost


def _write_cache(cache: Path, data: dict) -> None:
    """Atomic, per-process temp file: concurrent fzf previews may price the same session at once."""
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{cache.stem}-", suffix=".tmp", dir=str(cache.parent))
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        os.replace(tmp, cache)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _from_row(row: dict, source: str) -> Cost:
    total = float(row.get("totalCost") or 0.0)
    tokens = int(row.get("totalTokens") or 0)
    unpriced = unpriced_models(row)
    if not unpriced and (total > 0 or tokens == 0):
        return Cost("ok", total, tokens, source)
    if not unpriced and tokens > 0:  # no breakdown detail: $0 with tokens means unpriced
        unpriced = sorted(str(m) for m in row.get("modelsUsed") or [])
    return Cost("partial", total, tokens, source, unpriced)


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def _coverage(rows: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """Per model across every session: (sessions using it, sessions where it came back at $0)."""
    used: dict[str, int] = {}
    unpriced: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        for b in r.get("modelBreakdowns") or []:
            if not isinstance(b, dict) or not b.get("modelName"):
                continue
            tokens = sum(int(b.get(k) or 0) for k in ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens"))
            if not tokens:
                continue
            m = str(b["modelName"])
            used[m] = used.get(m, 0) + 1
            if float(b.get("cost") or 0) == 0:
                unpriced[m] = unpriced.get(m, 0) + 1
    return used, unpriced


def doctor_line(timeout: float = 8.0) -> str:
    """For ``pin doctor``: version, and which models across all sessions the offline/online tables cannot price."""
    binary = ccusage_bin()
    if not binary:
        return f"✗ ccusage: not installed ({UPDATE_HINT}) — cost lines will say so"
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=timeout)
        version = p.stdout.strip().replace("ccusage ", "") or "?"
    except (OSError, subprocess.SubprocessError):
        return "✗ ccusage: present but not runnable"
    rows = _listing(binary, offline=True, timeout=timeout)
    if isinstance(rows, str):
        return f"✗ ccusage {version}: offline listing failed ({rows})"
    used, missing = _coverage(rows)
    if not used:
        return f"✓ ccusage {version}: no priced sessions yet"
    if not missing:
        return f"✓ ccusage {version}: offline price table covers all {len(used)} models in your sessions"
    names = ", ".join(f"{_short(m)} ({n} sessions)" for m, n in sorted(missing.items()))
    online = _listing(binary, offline=False, timeout=timeout)
    if isinstance(online, str):
        return f"· ccusage {version}: offline table has no price for {names}; online fallback unreachable ({online}) · {UPDATE_HINT}"
    online_used, still = _coverage(online)
    if any(m not in online_used for m in missing):
        return f"· ccusage {version}: offline table has no price for {names}; online listing incomplete · {UPDATE_HINT}"
    if still:
        return f"· ccusage {version}: no price for {', '.join(_short(m) for m in sorted(still))} even online · {UPDATE_HINT}"
    return f"· ccusage {version}: offline table has no price for {names}; the online fallback prices them · {UPDATE_HINT} to avoid the network"
