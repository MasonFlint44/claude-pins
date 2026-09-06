"""Plain-text prompts (§6.7): numbered choices, yes/no, free text. ctrl-c cancels."""

from __future__ import annotations

import os
import sys


class Cancelled(Exception):
    pass


def _ask(text: str) -> str:
    try:
        return input(text)
    except (KeyboardInterrupt, EOFError):
        print()
        raise Cancelled()


def choose(header: list[str], options: list[str], default: int = 1, *, stream=None) -> int:
    """Print a numbered menu, return the 1-based choice. ``default`` is used on bare enter."""
    out = stream or sys.stdout
    for line in header:
        print(f" {line}", file=out)
    print(file=out)
    for i, opt in enumerate(options, 1):
        tag = "        (enter)" if i == default else ""
        print(f"  {i}) {opt}{tag}", file=out)
    print(file=out)
    while True:
        raw = _ask(f" choice [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print(f" pick 1–{len(options)}", file=out)


def yesno(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = _ask(f" → {question} {hint} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def text(label: str, default: str = "") -> str:
    """One-line text prompt pre-filled with ``default`` (readline insert when available)."""
    try:
        import readline  # noqa: F401

        def hook():
            readline.insert_text(default)
            readline.redisplay()

        readline.set_pre_input_hook(hook)
    except ImportError:
        readline = None  # type: ignore
        if default:
            label = f"{label} [{default}]"
    try:
        value = _ask(f" {label}: ")
    finally:
        if readline is not None:
            readline.set_pre_input_hook(None)
    if readline is None and not value.strip():
        return default
    return value.strip()


def pick_directory() -> str | None:
    """Ask for a directory path; tab completion over paths when readline is available."""
    try:
        import readline

        def complete(txt, state):
            base = os.path.expanduser(txt)
            d, prefix = os.path.split(base)
            d = d or "."
            try:
                names = [n for n in os.listdir(d) if n.startswith(prefix)]
            except OSError:
                names = []
            paths = []
            for n in sorted(names):
                full = os.path.join(d, n)
                if os.path.isdir(full):
                    shown = os.path.join(os.path.dirname(txt), n) if os.path.dirname(txt) else n
                    paths.append(shown + "/")
            return paths[state] if state < len(paths) else None

        readline.set_completer_delims(" \t\n")
        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass
    value = _ask(" directory: ").strip()
    if not value:
        return None
    path = os.path.abspath(os.path.expanduser(value))
    return path if os.path.isdir(path) else None
