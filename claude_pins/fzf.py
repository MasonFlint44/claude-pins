"""A thin, testable wrapper over an fzf process."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
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
    display: str    # what fzf shows and, minus colour codes, what the query matches


def lines_for(items: list[Item]) -> list[str]:
    """One ``id<tab>display`` line per item. The id is hidden with --with-nth and comes back in the output.

    There is no hidden search field: fzf applies --nth to the line *after* --with-nth has cut it down, so
    a field that is not displayed cannot be matched either (true since at least 0.44). Matching therefore
    runs over the displayed text, which --ansi strips of colour first.
    """
    return [f"{it.id}\t{it.display.replace(chr(9), ' ')}" for it in items]


# The preview pane sits below the list and hides itself when it would get fewer than ten rows (fzf
# measures the pane's own rows, bottom border included, and re-checks on every resize).
PREVIEW_WINDOW = "down,55%,border-rounded,wrap,<10(hidden)"
PREVIEW_MIN_ROWS = 10
PREVIEW_SHARE = 55


def preview_fits(lines: int, *, bottom_border: bool = False) -> bool:
    """Python's copy of fzf's threshold arithmetic for PREVIEW_WINDOW, so the picker can say when the pane
    is hidden: the pane gets 55% of the rows left after the bottom border, and hides under ten."""
    pane = (lines - (1 if bottom_border else 0)) * PREVIEW_SHARE // 100
    return pane >= PREVIEW_MIN_ROWS


def build_args(binary: str, *, prompt: str, header: str = "", expect: list[str] | None = None,
               query: str = "", multi: bool = False, preview: str | None = None,
               preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
               pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
               disabled: bool = False, ansi: bool = True, info: str = "inline-right",
               header_lines: int = 0) -> list[str]:
    args = [binary, "--layout=reverse", "--delimiter=\t", "--with-nth=2..", "--tiebreak=index",
            "--no-sort", "--print-query", f"--info={info}", "--no-separator",
            "--pointer", ">", "--marker", "▌", "--prompt", prompt, "--cycle", "--ellipsis", "…"]
    if ansi:
        args.append("--ansi")
    if not config.color_enabled():
        args.append("--color=bw")
    if header:
        args += ["--header", header, "--header-first"]
    if header_lines:
        args += [f"--header-lines={header_lines}"]
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
    return args


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



def run(items: list[Item], *, prompt: str, header: str = "", expect: list[str] | None = None,
        query: str = "", multi: bool = False, preview: str | None = None,
        preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
        pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
        disabled: bool = False, ansi: bool = True, info: str = "inline-right",
        header_lines: int = 0) -> Result | None:
    """Run fzf over ``items``; None when the user pressed esc/ctrl-c.

    With ``header_lines``, that many leading items are fzf's sticky header (column labels): shown like
    rows, never matched, selected or printed back."""
    binary = fzf_bin()
    if not binary:
        return None
    lines = lines_for(items)
    args = build_args(binary, prompt=prompt, header=header, expect=expect, query=query, multi=multi,
                      preview=preview, preview_window=preview_window, preview_label_cmd=preview_label_cmd,
                      pos=pos, border_label=border_label, extra=extra, disabled=disabled, ansi=ansi, info=info,
                      header_lines=header_lines)
    # stderr is inherited on purpose: fzf ≤ 0.4x draws its UI there (newer builds use /dev/tty),
    # and option errors should reach the user either way.
    try:
        p = subprocess.run(args, input="\n".join(lines) + ("\n" if lines else ""), stdout=subprocess.PIPE, text=True)
    except OSError:
        return None
    if p.returncode not in (0, 1):   # 130 = esc/ctrl-c, 2 = bad option
        return None
    out = p.stdout.split("\n")
    q = out[0] if out else ""
    key = out[1] if expect and len(out) > 1 else ""
    rest = out[2:] if expect else out[1:]
    ids = [ln.split("\t", 1)[0] for ln in rest if ln]
    return Result(key, q, ids)
