"""Paths, environment, and Claude Code settings the tool depends on.

Everything that touches the environment lives here so tests can override it
by setting environment variables (``CLAUDE_PINS_FILE``, ``CLAUDE_CONFIG_DIR``,
``XDG_*``) or by patching the functions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CLEANUP_DAYS = 30
DEFAULT_EXPIRE_WARN_DAYS = 7
MIN_FZF = (0, 44)


def home() -> Path:
    return Path(os.path.expanduser("~"))


def claude_config_dir() -> Path:
    """``CLAUDE_CONFIG_DIR`` if set, else ``~/.claude``."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else home() / ".claude"


def projects_dir() -> Path:
    return claude_config_dir() / "projects"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else home() / ".local" / "state"
    return root / "claude-pins"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else home() / ".cache"
    return root / "claude-pins"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else home() / ".config"
    return root / "claude-pins"


def pins_file() -> Path:
    env = os.environ.get("CLAUDE_PINS_FILE")
    return Path(env).expanduser() if env else state_dir() / "pins.json"


def keymap_file() -> Path:
    env = os.environ.get("CLAUDE_PINS_KEYMAP")
    return Path(env).expanduser() if env else config_dir() / "keys.toml"


def cleanup_period_days() -> int:
    """``cleanupPeriodDays`` from Claude's settings.json (default 30, minimum 1)."""
    for name in ("settings.json", "settings.local.json"):
        path = claude_config_dir() / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = data.get("cleanupPeriodDays")
        if isinstance(value, (int, float)) and value >= 1:
            return int(value)
    return DEFAULT_CLEANUP_DAYS


def expire_warn_days() -> int:
    raw = os.environ.get("CLAUDE_PINS_EXPIRE_WARN")
    try:
        return max(0, int(raw)) if raw else DEFAULT_EXPIRE_WARN_DAYS
    except ValueError:
        return DEFAULT_EXPIRE_WARN_DAYS


def default_sort() -> str:
    value = os.environ.get("CLAUDE_PINS_SORT", "recency").strip().lower()
    return value if value in SORT_ORDERS else "recency"


SORT_ORDERS = ("recency", "alias", "pinned")


def no_fzf() -> bool:
    return bool(os.environ.get("CLAUDE_PINS_NO_FZF"))


def color_enabled(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLAUDE_PINS_COLOR") == "1":
        return True
    if stream is None:
        return True
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def tilde(path: str | os.PathLike | None) -> str:
    """Render a path with ``~`` for the home directory."""
    if not path:
        return ""
    text = str(path)
    h = str(home())
    if text == h:
        return "~"
    if text.startswith(h + os.sep):
        return "~" + text[len(h):]
    return text


def configured_model() -> str:
    """The ``model`` from Claude's settings.json (e.g. ``claude-fable-5-1[1m]``), or ""."""
    try:
        data = json.loads((claude_config_dir() / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("model") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def context_window_for(model: str) -> int | None:
    """1M when settings pin this model to the ``[1m]`` context; otherwise None (caller assumes 200k)."""
    configured = configured_model()
    if configured.endswith("[1m]") and model and configured[:-4] == model:
        return 1_000_000
    return None
