"""A thin, testable wrapper over an fzf process."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from . import config, theme
from .text import cells


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


def min_lines(*, bottom_border: bool = False) -> int:
    """The shortest terminal on which the pane still shows (the same arithmetic, solved for the height)."""
    lines = PREVIEW_MIN_ROWS
    while not preview_fits(lines, bottom_border=bottom_border):
        lines += 1
    return lines


# Versions that introduced what the picker uses beyond the 0.44 floor, from fzf's CHANGELOG. Everything
# below the floor's feature set is emitted unconditionally; these are gated on ``supports()``.
FEATURES = {
    "resize": (0, 46),            # the resize event, and $FZF_LINES / $FZF_COLUMNS / the count variables
    "with-shell": (0, 51),        # --with-shell; before it the snippets below run under the user's $SHELL
    "sticky-under-prompt": (0, 63),   # --header-lines rows stay at the list's top under --header-first; until
                                      # 0.62 they moved above the prompt with the header (no changelog entry)
    "info-command": (0, 65, 2),   # --info-command is 0.54.0, but inline-right cut its last cell until 0.65.2
    "transform-header": (0, 40),  # present on the floor; listed so the gate reads as a table
}


def supports(feature: str, version: tuple[int, ...] | None = None) -> bool:
    version = fzf_version() if version is None else version
    return version is not None and version >= FEATURES[feature]


# Shell that fzf runs itself, so nothing here may start Python: the header transform fires on every
# resize (0.46+) or on every cursor move and keystroke (0.44, where the resize event, $FZF_LINES and
# $FZF_COLUMNS are missing and the size comes from stty), and the info command on every keystroke.
# It is POSIX sh; --with-shell keeps it under sh from 0.51, before that it runs under $SHELL.
HINTS_VAR = "CLAUDE_PINS_HINTS"
HINTS_CELLS_VAR = "CLAUDE_PINS_HINTS_CELLS"
LEGEND_VAR = "CLAUDE_PINS_LEGEND"
LEGEND_CELLS_VAR = "CLAUDE_PINS_LEGEND_CELLS"
EXTRA_VAR = "CLAUDE_PINS_EXTRA"        # lines under the legend and hints (the help screen's keymap path)
STATUS_VAR = "CLAUDE_PINS_STATUS"      # the line above the prompt: a flash, else a space
NOTE_VAR = "CLAUDE_PINS_NOTE"          # what takes the status line when the terminal is too short for the pane
HEADER_GAP = 4          # the least space between the legend and the hints on one line
HEADER_MARGIN = 4       # fzf's two-cell header indent plus the two cells it cuts at the right edge (measured)


@dataclass
class Header:
    """The lines above the prompt (fzf's --header-first): the legend on the left and the hints
    right-justified on one line when the width allows ``HEADER_GAP`` between them, else the legend
    over the hints (hints alone stay left); then ``extra``; then the status line, which is the flash
    for one screen, the too-short note, or a space that keeps the prompt off the header. ``text()``
    is the launch-time layout and ``header_transform()`` the same arithmetic in shell, fed by ``env()``,
    so fzf re-fits the header itself on resize (or on every keystroke on 0.44). A header line holding
    one space is kept where a trailing newline would be dropped."""
    hints: str
    legend: str = ""
    extra: tuple[str, ...] = ()
    status: str = " "
    note: str = ""
    color: theme.Palette | None = None

    def _dim(self, text: str) -> str:
        return self.color(text, "dim") if self.color else text

    def env(self) -> dict[str, str]:
        return {HINTS_VAR: self._dim(self.hints), HINTS_CELLS_VAR: str(cells(self.hints)),
                LEGEND_VAR: self._dim(self.legend), LEGEND_CELLS_VAR: str(cells(self.legend)),
                EXTRA_VAR: "\n".join(self.extra), STATUS_VAR: self.status, NOTE_VAR: self.note}

    def text(self, columns: int | None = None, lines: int | None = None, *, bottom_border: bool = False) -> str:
        if columns is None or lines is None:
            from .render import terminal_height, terminal_width
            columns = terminal_width() if columns is None else columns
            lines = terminal_height() if lines is None else lines
        out = []
        pad = columns - cells(self.legend) - cells(self.hints) - HEADER_MARGIN
        if self.legend and pad >= HEADER_GAP:
            out.append(self._dim(self.legend) + " " * pad + self._dim(self.hints))
        elif self.legend:
            out += [self._dim(self.legend), self._dim(self.hints)]
        else:
            out.append(self._dim(self.hints))
        out += self.extra
        short = self.note and lines < min_lines(bottom_border=bottom_border)
        out.append(self.note if short else self.status)
        return "\n".join(out)


def header_transform(*, bottom_border: bool = False) -> str:
    """``Header.text()`` as a transform-header command over ``Header.env()``. No parentheses or brackets:
    fzf would take the first one as the end of the action."""
    limit = min_lines(bottom_border=bottom_border)
    size = 'stty size </dev/tty 2>/dev/null'
    return (f'c=$FZF_COLUMNS; h=$FZF_LINES; test -n "$c" || c=`{size} | cut -d" " -f2`; '
            f'test -n "$h" || h=`{size} | cut -d" " -f1`; '
            f'p=`expr "$c" - "${LEGEND_CELLS_VAR}" - "${HINTS_CELLS_VAR}" - {HEADER_MARGIN} 2>/dev/null`; '
            f'if test -n "${LEGEND_VAR}" && test -n "$p" && test "$p" -ge {HEADER_GAP}; '
            f'then printf "%s%*s%s\\n" "${LEGEND_VAR}" "$p" "" "${HINTS_VAR}"; '
            f'elif test -n "${LEGEND_VAR}"; then printf "%s\\n%s\\n" "${LEGEND_VAR}" "${HINTS_VAR}"; '
            f'else printf "%s\\n" "${HINTS_VAR}"; fi; '
            f'test -n "${EXTRA_VAR}" && printf "%s\\n" "${EXTRA_VAR}"; '
            f's=${STATUS_VAR}; test -n "${NOTE_VAR}" && test -n "$h" && test "$h" -lt {limit} && s=${NOTE_VAR}; '
            f'printf %s "$s"')


def header_binds(*, bottom_border: bool = False, version: tuple[int, ...] | None = None) -> list[tuple[str, str]]:
    """The binds that keep a header current: on resize where fzf has the event, else on every cursor move
    and keystroke (0.44), where the transform reads the size from stty."""
    transform = f"transform-header({header_transform(bottom_border=bottom_border)})"
    if supports("resize", version):
        return [("resize", transform)]
    return [("focus", transform), ("change", transform)]


GAP_ROW = Item("-", " ")    # a blank sticky row: the gap between the prompt and the list


def sticky(items: list[Item], header_lines: int, version: tuple[int, ...] | None) -> tuple[list[Item], int]:
    """The items with the gap row ahead of the sticky ones, on builds that draw sticky rows under the
    prompt (0.63+; before that they sit above it, and 0.53 crashes on enter over sticky rows alone)."""
    if version and supports("sticky-under-prompt", version):
        return [GAP_ROW, *items], header_lines + 1
    return items, header_lines


# ``3 of 5 pins · 2 selected``; ``5 pins`` when nothing is filtered out. The count variables exclude
# the sticky label row.
INFO_COMMAND = ('n=$FZF_TOTAL_COUNT; s=pins; test "$n" = 1 && s=pin; '
                'if test "$FZF_MATCH_COUNT" = "$n"; then t="$n $s"; else t="$FZF_MATCH_COUNT of $n $s"; fi; '
                'test "$FZF_SELECT_COUNT" -gt 0 && t="$t · $FZF_SELECT_COUNT selected"; printf %s "$t"')


def build_args(binary: str, *, prompt: str, header: str = "", expect: list[str] | None = None,
               query: str = "", multi: bool = False, preview: str | None = None,
               preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
               pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
               disabled: bool = False, ansi: bool = True, info: str = "inline-right",
               header_lines: int = 0, binds: list[tuple[str, str]] | None = None,
               info_command: str | None = None, version: tuple[int, ...] | None = None) -> list[str]:
    """``binds`` are (event or key, action) pairs; pairs on the same trigger are chained with ``+``,
    since a later --bind for a trigger would replace an earlier one rather than add to it. ``version``
    gates the options above the 0.44 floor."""
    # --no-clear leaves the alternate screen up between runs so the next screen draws over this one
    # instead of flashing the shell in between; leave_screen() drops it at the end.
    args = [binary, "--layout=reverse", "--delimiter=\t", "--with-nth=2..", "--tiebreak=index",
            "--no-sort", "--print-query", f"--info={info}", "--no-separator", "--no-clear",
            "--pointer", ">", "--marker", "▌", "--prompt", prompt, "--cycle", "--ellipsis", "…"]
    chains: dict[str, list[str]] = {}
    for trigger, action in binds or []:
        chains.setdefault(trigger, []).append(action)
    if ansi:
        args.append("--ansi")
    if version and supports("with-shell", version):
        args += ["--with-shell", "sh -c"]     # the snippets are POSIX sh; fzf would run them under $SHELL
    args.append("--color=bw" if not config.color_enabled() else f"--color={theme.fzf_colors()}")
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
    if info_command:
        args += ["--info-command", info_command]
    if preview:
        args += ["--preview", preview, "--preview-window", preview_window]
        if preview_label_cmd:
            chains.setdefault("focus", []).insert(0, f"transform-preview-label({preview_label_cmd})")
    if pos and pos > 1:
        chains.setdefault("start", []).append(f"pos({pos})")
    for trigger, actions in chains.items():
        args += ["--bind", f"{trigger}:{'+'.join(actions)}"]
    if border_label:
        args += ["--border", "bottom", "--border-label", border_label, "--border-label-pos", "2:bottom"]
    if extra:
        args += extra
    return args


def pin_exe() -> str:
    """How a command fzf runs (preview, reload) re-enters this tool, shell-quoted."""
    exe = os.environ.get("CLAUDE_PINS_EXE") or os.path.abspath(sys.argv[0])
    return shlex.quote(exe)


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
    return ("install fzf ≥ 0.44: brew install fzf, your package manager, or a release tarball from "
            "https://github.com/junegunn/fzf/releases unpacked into ~/.local/bin")


# ---- the alternate screen -----------------------------------------------------------------------

_alt_screen = False     # fzf ran with --no-clear and left the terminal on the alternate screen
_held = 0               # depth of hold_screen(): screens that want the next fzf to draw over this one


@contextlib.contextmanager
def hold_screen():
    """Keep the alternate screen up between the fzf runs inside this block (the picker's loop, the
    editor's), so they draw over each other; leaving the outermost block returns to the normal screen.
    A prompt run outside any hold (``pin prune``, ``pin edit``) drops the screen as soon as it ends,
    so what the command prints afterwards is seen."""
    global _held
    _held += 1
    try:
        yield
    finally:
        _held -= 1
        if _held == 0:
            leave_screen()


def screen_held() -> bool:
    return _held > 0


def leave_screen(*, force: bool = False) -> None:
    """Return to the normal screen if fzf left the alternate one up and no screen holds it (``force``
    ignores holds: exec and every way out of ``main``). fzf itself restores everything else on exit
    (cooked mode, cursor, mouse tracking; measured on 0.44.1, 0.53.0 and 0.67.0), so this one sequence
    is the whole restore. Idempotent."""
    global _alt_screen
    if not _alt_screen or (_held and not force):
        return
    _alt_screen = False
    seq = "\x1b[?1049l"
    try:
        if sys.stderr.isatty():
            sys.stderr.write(seq); sys.stderr.flush()
        elif sys.stdin.isatty():
            with open("/dev/tty", "w") as tty:
                tty.write(seq)
    except (OSError, ValueError):
        pass


def run(items: list[Item], *, prompt: str, header: str = "", expect: list[str] | None = None,
        query: str = "", multi: bool = False, preview: str | None = None,
        preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
        pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
        disabled: bool = False, ansi: bool = True, info: str = "inline-right",
        header_lines: int = 0, binds: list[tuple[str, str]] | None = None, info_command: str | None = None,
        env: dict[str, str] | None = None) -> Result | None:
    """Run fzf over ``items``; None when the user pressed esc/ctrl-c.

    With ``header_lines``, that many leading items are fzf's sticky header (column labels): shown like
    rows, never matched, selected or printed back; the gap row goes ahead of them where the build draws
    them under the prompt. ``env`` adds variables for the commands fzf runs."""
    global _alt_screen
    binary = fzf_bin()
    if not binary:
        return None
    version = fzf_version(binary)
    items, header_lines = sticky(items, header_lines, version)
    lines = lines_for(items)
    args = build_args(binary, prompt=prompt, header=header, expect=expect, query=query, multi=multi,
                      preview=preview, preview_window=preview_window, preview_label_cmd=preview_label_cmd,
                      pos=pos, border_label=border_label, extra=extra, disabled=disabled, ansi=ansi, info=info,
                      header_lines=header_lines, binds=binds, info_command=info_command, version=version)
    # stderr is inherited on purpose: fzf ≤ 0.4x draws its UI there (newer builds use /dev/tty),
    # and option errors should reach the user either way.
    try:
        p = subprocess.run(args, input="\n".join(lines) + ("\n" if lines else ""), stdout=subprocess.PIPE, text=True,
                           env={**os.environ, **env} if env else None)
    except OSError:
        return None
    _alt_screen = True
    if p.returncode not in (0, 1):   # 130 = esc/ctrl-c, 2 = bad option
        return None
    out = p.stdout.split("\n")
    q = out[0] if out else ""
    key = out[1] if expect and len(out) > 1 else ""
    rest = out[2:] if expect else out[1:]
    ids = [ln.split("\t", 1)[0] for ln in rest if ln]
    return Result(key, q, ids)
