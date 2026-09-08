"""The fzf backend: a Screen lowered to fzf's options, the process, and what comes back."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys

from . import config, theme
from .screen import HEADER_GAP, HEADER_MARGIN, Hook, Item, Result, Screen, entered_alt_screen, min_lines


def lines_for(items: list[Item], *, columns: bool = False) -> list[str]:
    """One ``id<tab>display`` line per item. The id is hidden with --with-nth and comes back in the output.

    There is no hidden search field: fzf applies --nth to the line *after* --with-nth has cut it down, so
    a field that is not displayed cannot be matched either (true since at least 0.44). Matching therefore
    runs over the displayed text, which --ansi strips of colour first. With ``columns`` the display's own
    tabs are kept as field boundaries so ``--nth`` can pick the columns to match (``--tabstop=1`` draws
    each as one space); otherwise a stray tab is flattened so it cannot shift the id.
    """
    if columns:
        return [f"{it.id}\t{it.display}" for it in items]
    return [f"{it.id}\t{it.display.replace(chr(9), ' ')}" for it in items]


# The preview pane sits below the list and hides itself when it would get fewer than ten rows (fzf
# measures the pane's own rows, bottom border included, and re-checks on every resize; the arithmetic is
# ``screen.preview_fits``).
PREVIEW_WINDOW = "down,55%,border-rounded,wrap,<10(hidden)"


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


def info_command(noun: str = "pins") -> str:
    """``3 of 5 pins · 2 selected``; ``5 pins`` when nothing is filtered out; ``1 pin``. The count
    variables exclude the sticky label row."""
    return (f'n=$FZF_TOTAL_COUNT; s={noun}; test "$n" = 1 && s={noun.rstrip("s")}; '
            'if test "$FZF_MATCH_COUNT" = "$n"; then t="$n $s"; else t="$FZF_MATCH_COUNT of $n $s"; fi; '
            'test "$FZF_SELECT_COUNT" -gt 0 && t="$t · $FZF_SELECT_COUNT selected"; printf %s "$t"')


INFO_COMMAND = info_command()


def build_args(binary: str, *, prompt: str, header: str = "", expect: list[str] | None = None,
               query: str = "", multi: bool = False, preview: str | None = None,
               preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
               pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
               disabled: bool = False, ansi: bool = True, info: str = "inline-right",
               header_lines: int = 0, binds: list[tuple[str, str]] | None = None,
               info_command: str | None = None, nth: str | None = None,
               version: tuple[int, ...] | None = None) -> list[str]:
    """``binds`` are (event or key, action) pairs; pairs on the same trigger are chained with ``+``,
    since a later --bind for a trigger would replace an earlier one rather than add to it. ``nth`` is
    fzf's field range to match, counted over the display's tab-separated columns (``1..3`` for the
    picker's alias, title and directory). ``version`` gates the options above the 0.44 floor."""
    # --no-clear leaves the alternate screen up between runs so the next screen draws over this one
    # instead of flashing the shell in between; leave_screen() drops it at the end.
    args = [binary, "--layout=reverse", "--delimiter=\t", "--with-nth=2..", "--tabstop=1", "--tiebreak=index",
            "--no-sort", "--print-query", f"--info={info}", "--no-separator", "--no-clear",
            "--pointer", ">", "--marker", "▌", "--prompt", prompt, "--cycle", "--ellipsis", "…"]
    if nth:
        args += ["--nth", nth]
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


OPTIONAL_NOTE = "optional; the built-in picker draws the same screens"     # doctor's fzf row


def install_hint() -> str:
    return ("install fzf ≥ 0.44: brew install fzf, your package manager, or a release tarball from "
            "https://github.com/junegunn/fzf/releases unpacked into ~/.local/bin")


def run(items: list[Item], *, prompt: str, header: str = "", expect: list[str] | None = None,
        query: str = "", multi: bool = False, preview: str | None = None,
        preview_window: str = PREVIEW_WINDOW, preview_label_cmd: str | None = None,
        pos: int | None = None, border_label: str = "", extra: list[str] | None = None,
        disabled: bool = False, ansi: bool = True, info: str = "inline-right",
        header_lines: int = 0, binds: list[tuple[str, str]] | None = None, info_command: str | None = None,
        nth: str | None = None, env: dict[str, str] | None = None) -> Result | None:
    """Run fzf over ``items``; None when the user pressed esc/ctrl-c.

    With ``header_lines``, that many leading items are fzf's sticky header (column labels): shown like
    rows, never matched, selected or printed back; the gap row goes ahead of them where the build draws
    them under the prompt. With ``nth`` the displays are tab-separated columns and only that field
    range is matched. ``env`` adds variables for the commands fzf runs."""
    binary = fzf_bin()
    if not binary:
        return None
    version = fzf_version(binary)
    items, header_lines = sticky(items, header_lines, version)
    lines = lines_for(items, columns=nth is not None)
    args = build_args(binary, prompt=prompt, header=header, expect=expect, query=query, multi=multi,
                      preview=preview, preview_window=preview_window, preview_label_cmd=preview_label_cmd,
                      pos=pos, border_label=border_label, extra=extra, disabled=disabled, ansi=ansi, info=info,
                      header_lines=header_lines, binds=binds, info_command=info_command, nth=nth, version=version)
    # stderr is inherited on purpose: fzf ≤ 0.4x draws its UI there (newer builds use /dev/tty),
    # and option errors should reach the user either way.
    try:
        p = subprocess.run(args, input="\n".join(lines) + ("\n" if lines else ""), stdout=subprocess.PIPE, text=True,
                           env={**os.environ, **env} if env else None)
    except OSError:
        return None
    entered_alt_screen()
    if p.returncode not in (0, 1):   # 130 = esc/ctrl-c, 2 = bad option
        return None
    out = p.stdout.split("\n")
    q = out[0] if out else ""
    key = out[1] if expect and len(out) > 1 else ""
    rest = out[2:] if expect else out[1:]
    ids = [ln.split("\t", 1)[0] for ln in rest if ln]
    return Result(key, q, ids)


# ---- a Screen as fzf options -----------------------------------------------------------------------------

def hook_command(hook: Hook, *, version: tuple[int, ...] | None = None, width: int | None = None) -> str:
    """The shell that runs a hook from inside fzf: ``pin`` re-entered with the hidden subcommand, the row
    under the cursor as ``{1}`` and the query as ``{q}``. The rows commands are told the gap row rather
    than asking fzf its version on every reload, and get the launch width as the fallback for builds
    without $FZF_COLUMNS (the width fzf reports wins where it exists)."""
    gap = " --gap" if supports("sticky-under-prompt", version) else ""
    if hook.name in ("preview", "spreview"):
        return f"{pin_exe()} _{hook.name} {{1}}"
    if hook.name == "draft":
        return f"{pin_exe()} _preview --draft {shlex.quote(hook.args[0])}"
    if hook.name == "rows":
        sort, show = hook.args
        cols = f"COLUMNS={width} " if width else ""
        return f"{cols}{pin_exe()} _rows --sort {sort}{gap}" + (" --all" if show == "all" else "")
    if hook.name == "dirs":
        return f"{pin_exe()} _dirs{gap} {{q}}"
    raise ValueError(f"unknown hook {hook.name}")


def run_screen(screen: Screen) -> Result | None:
    """Lower a Screen to ``run()``: the header text and its transform, the hooks as shell, the counter as
    the info command where fzf draws it whole, the footer as a bottom border label."""
    from .render import terminal_width
    version = fzf_version()
    binds: list[tuple[str, str]] = []
    if screen.reload:
        reload = f"reload({hook_command(screen.reload, version=version, width=terminal_width())})"
        if screen.refresh_key:
            binds.append((screen.refresh_key, reload))
        if supports("resize", version):
            binds.append(("resize", reload))
    if screen.on_change:
        binds.append(("change", f"reload({hook_command(screen.on_change, version=version)})"))
    env = None
    header = ""
    if screen.header:
        header = screen.header_text()
        env = screen.header.env()
        if screen.header.legend or screen.header.note:      # a header that re-fits with the width or height
            binds += header_binds(bottom_border=screen.bottom_border, version=version)
    preview_label_cmd = None
    extra: list[str] = []
    if screen.preview:
        if screen.label_from_row:
            preview_label_cmd = "echo ' '{1}' '"
        elif screen.preview_label:
            extra += ["--preview-label", screen.preview_label]
    info = "inline-right" if screen.counter else "hidden"
    cmd = info_command(screen.counter) if screen.counter and supports("info-command", version) else None
    return run(screen.items, prompt=screen.prompt, header=header, expect=list(screen.expect), query=screen.query,
               multi=screen.multi, pos=screen.pos,
               preview=hook_command(screen.preview, version=version) if screen.preview else None,
               preview_label_cmd=preview_label_cmd, border_label=screen.footer, extra=extra or None,
               disabled=screen.disabled, info=info, header_lines=screen.header_lines, binds=binds or None,
               info_command=cmd, nth=screen.nth, env=env)
