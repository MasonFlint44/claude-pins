"""A thin, testable wrapper over an fzf process."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from . import config


@dataclass
class Result:
    key: str            # "" for enter
    query: str
    ids: list[str]      # first field of every selected line


@dataclass
class Item:
    id: str
    display: str
    search: str = ""    # what the query matches against (defaults to display)


def fzf_bin() -> str | None:
    override = os.environ.get("CLAUDE_PINS_FZF")
    if override:
        return override if os.path.exists(override) or shutil.which(override) else None
    return shutil.which("fzf")


def fzf_version(binary: str | None = None) -> tuple[int, ...] | None:
    binary = binary or fzf_bin()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def available() -> bool:
    if config.no_fzf():
        return False
    v = fzf_version()
    return v is not None and v >= config.MIN_FZF


def install_hint() -> str:
    return ("install fzf ≥ 0.44: curl -sSL https://github.com/junegunn/fzf/releases/download/0.67.0/"
            "fzf-0.67.0-linux_amd64.tar.gz | tar xz -C ~/.local/bin  (or: brew install fzf)")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(items: list[Item], *, prompt: str, header: str = "", expect: list[str] | None = None,
        query: str = "", multi: bool = False, preview: str | None = None,
        preview_window: str = "down,55%,border-rounded,wrap", preview_label_cmd: str | None = None,
        pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
        disabled: bool = False, ansi: bool = True) -> Result | None:
    """Run fzf over ``items``; None when the user pressed esc/ctrl-c."""
    binary = fzf_bin()
    if not binary:
        return None
    lines = []
    for it in items:
        search = (it.search or _ANSI.sub("", it.display)).replace("\t", " ")
        lines.append(f"{it.id}\t{search}\t{it.display}")
    args = [binary, "--layout=reverse", "--delimiter=\t", "--with-nth=3..", "--nth=2", "--tiebreak=index",
            "--no-sort", "--print-query", "--info=inline-right", "--no-separator",
            "--pointer", ">", "--marker", "▌", "--prompt", prompt, "--cycle", "--ellipsis", "…"]
    if ansi:
        args.append("--ansi")
    if not config.color_enabled():
        args.append("--color=bw")
    if header:
        args += ["--header", header]
    if expect:
        args += ["--expect", ",".join(expect)]
    if query:
        args += ["--query", query]
    if multi:
        args.append("--multi")
    if disabled:
        args.append("--disabled")
    if preview:
        args += ["--preview", preview, "--preview-window", preview_window]
        if preview_label_cmd:
            args += ["--bind", f"focus:transform-preview-label({preview_label_cmd})"]
    if pos and pos > 1:
        args += ["--bind", f"start:pos({pos})"]
    if border_label:
        args += ["--border", "bottom", "--border-label", border_label, "--border-label-pos", "2:bottom"]
    if extra:
        args += extra
    try:
        p = subprocess.run(args, input="\n".join(lines) + ("\n" if lines else ""), capture_output=True, text=True)
    except OSError:
        return None
    if p.returncode not in (0, 1):   # 130 = esc/ctrl-c, 2 = error
        return None
    out = p.stdout.split("\n")
    q = out[0] if out else ""
    key = out[1] if expect and len(out) > 1 else ""
    rest = out[2:] if expect else out[1:]
    ids = [ln.split("\t", 1)[0] for ln in rest if ln]
    return Result(key, q, ids)
