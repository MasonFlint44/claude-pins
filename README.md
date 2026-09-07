# claude-pins

Pin Claude Code sessions so you can find and resume them without remembering a UUID or the
directory they were launched from. A terminal command `pin` with an fzf picker, plus `/pins:pin`
and `/pins:unpin` slash commands shipped as a Claude Code plugin.

![the pin picker: four pins, a preview pane with branch, model, context and cost](docs/preview.svg)

```
$ pin                 # picker: enter opens, ctrl-space actions, alt-n pins a recent session, f1 help
$ pin standup         # one match → cd there and `claude --resume <id>`; several → picker pre-filtered
$ pin rc-mower --fork # one-off fork (new session id, original untouched)
$ pin add "rc mower" mower   # pin by title from the terminal; pin sessions lists what it matches
```

Inside Claude: `/pins:pin` pins the current session (Claude drafts an alias and title from the
conversation and confirms), `/pins:unpin` removes it. Claude Code namespaces plugin commands
with the plugin name, so that is how they appear in the command list. Everything else is
terminal-only.

## Install

1. Add the marketplace and the plugin in Claude Code:

   ```
   /plugin marketplace add MasonFlint44/claude-toolbox
   /plugin install pins@claude-toolbox
   ```

2. Run `/pins:install` once. It symlinks `bin/pin` into `~/.local/bin`, installs bash or zsh
   completion for the shell you use, and runs `pin doctor`, which checks for **fzf ≥ 0.44** (older or missing falls
   back to a numbered menu) and **ccusage** (optional; only for the cost line). `/pins:doctor`
   runs the same checks later and explains each line.

Python 3.10+ standard library only. Linux and macOS (WSL counts as Linux).

**Updating:** `/plugin update pins` (or auto-update for the marketplace in `/plugin`), then
`/pins:install` again, because the plugin directory moves on each version and the symlink
points into it. **Removing:** `/plugin uninstall pins`, delete `~/.local/bin/pin` and the
completion link; the store and cache below can go too.

### Plugin commands and skills

| | |
|---|---|
| `/pins:pin [alias [title…]]` | pin this session; with no arguments Claude drafts an alias and title from the conversation and confirms |
| `/pins:unpin` | unpin this session; says so if it is not pinned |
| `/pins:install` | symlink, completion, `pin doctor` |
| `/pins:doctor` | run `pin doctor` and explain each line with a fix |

## How it works

- A pin stores an alias, title, session id, directory, note, and launch options. The store is
  `~/.local/state/claude-pins/pins.json` (`XDG_STATE_HOME` honored, `CLAUDE_PINS_FILE`
  overrides). Local per machine; atomic writes; a corrupt file is copied to `.bak` and never
  clobbered.
- Opening a pin runs `cd <dir> && claude --resume <id>` plus the pin's launch flags. Per-pin
  **fork** mode adds `--fork-session`; **worktree** mode adds `--worktree`. `--fork`, `-w [name]`
  and `--resume` are one-off overrides.
- Claude Code deletes transcripts untouched for `cleanupPeriodDays` (default 30). A pin is only
  as durable as its transcript, so the picker shows ⏳ in the last 7 days (`CLAUDE_PINS_EXPIRE_WARN`)
  and 🔴 once the transcript is gone. Opening touches the transcript; pins with **keep** (🚩) are
  touched on every `pin` run and never expire while you use the tool. `pin prune` unpins the
  expired ones; `pin undo` restores the last unpin or prune.
- The cost line comes from [ccusage](https://github.com/ryoppippi/ccusage): offline first,
  and when its bundled price table has no price for a model the session used, one online run
  with a short timeout. If neither prices the model, the line says so and names the update
  command. Results are cached per transcript change.
- A session whose id appears in a running `claude` process is marked 🟢 and asks before
  resuming a second copy.
- If the pin's directory is gone: a Claude worktree (`<repo>/.claude/worktrees/<name>`) is
  recreated on its surviving `worktree-<name>` branch or fresh off HEAD, or you pick the repo
  root, `~`, another directory, or unpin. A branch mismatch is reported and checkout offered
  only when the tree is clean. Nothing switches branches on its own.

## Keys

| action | key | | action | key |
|---|---|---|---|---|
| open | enter | | new pin… | alt-n |
| open as fork | alt-o | | show expired | alt-a |
| open in new worktree | alt-w | | prune… | alt-p |
| actions palette | ctrl-space | | undo | alt-z |
| edit… | alt-e | | cycle sort | alt-s |
| details | alt-i | | toggle preview | alt-v |
| touch transcript | alt-t | | help / shortcuts | f1 |
| toggle keep | alt-k | | refresh | alt-r |
| unpin | alt-x | | multi-select | tab |

Esc always goes back exactly one level. Toggle fork mode and toggle worktree mode have no
default key; both live in the palette. Every key is remappable from the f1 screen (enter rebinds,
ctrl-r resets a row, ctrl-alt-r resets all) or by editing `~/.config/claude-pins/keys.toml`,
whose action names are `open`, `open_fork`, `open_worktree`, `palette`, `edit`, `details`,
`touch`, `keep`, `fork_mode`, `worktree_mode`, `unpin`, `new`, `expired`, `prune`, `undo`,
`sort`, `preview`, `refresh`, `help`, `select`. fzf's own query-editing keys and alt+enter (Windows
Terminal) are avoided on purpose. Markers: 🟢 open · 🚩 keep · 🔀 fork · 🌳 worktree · ⏳ expiring ·
🔴 expired. On a terminal without emoji (a non-UTF-8 locale, the Linux console, `TERM=dumb`) they
become the one-cell ● ⚑ ⑂ ⌂ ⧗ ✗ in the same colours and the 📌 leaves the prompt;
`CLAUDE_PINS_GLYPHS=emoji` or `text` decides by hand.

The colours are Claude Code's own: the prompt in clay, the pointer and matched letters in
periwinkle, the effort and context values on the budget statusline's green-to-red ramp, the
permission mode in the colour Claude's mode indicator gives it. Everything else is dim, so the
look holds on light and dark terminals alike; `NO_COLOR` or `CLAUDE_PINS_COLOR=0` turns it all off.

The list's `idle` column is the time since the transcript was last written, which is what the
retention clock counts. The preview pane below the list hides itself when it would get fewer
than ten rows (the header says `preview hidden: terminal too short`); alt-i opens the same
details on a screen of their own, with more of the last exchange, and enter there opens the pin.
Directories shorten fish-style when the column is narrow (`~/g/c/claude-pins`), keeping the last
component; a Claude worktree shows as `~/git/repo › name`.

The list is read from the store every time a screen returns; alt-r re-reads it in place, for a
picker left open while another terminal pinned something. On fzf 0.46 and newer the rows also
re-fit themselves when the terminal is resized, and the too-short note follows the height;
0.44 and 0.45 refit at the next screen and refresh the note on the next cursor move or
keystroke. On 0.65.2 and newer the counter reads `3 of 5 pins · 2 selected`.

In the editor the pane shows the pin as it would be saved, changed rows marked `*`. Enter
changes the highlighted field: booleans flip, choices open a short list with `(clear)`, and a
text field is typed on the query line under a `📌 pins › alias › edit › title ›` breadcrumb with
the current value already there (enter saves, esc cancels, ctrl-u clears). The directory field
lists completions of what is typed underneath; enter takes the highlighted one, or the text
when nothing matches. alt-s saves; esc goes back and offers save / discard / keep editing when
something changed. Every other question on the way (a new pin's alias, a key to rebind, prune,
a missing directory, a branch mismatch, a session already open) is an fzf screen too: nothing
drops to a text prompt while fzf is in use.

Without fzf the same questions are readline prompts with the current value pre-filled (ctrl-c
cancels a field) and numbered lists. In the no-fzf menu a bare letter is a list action and
letter+number a row action: `N` open, `oN` fork, `wN` worktree, `tN` touch, `eN` edit, `xN`
unpin, `pN` preview, `n` new, `a` show expired, `p` prune, `z` undo, `s` sort, `?` legend,
`q` quit.

## Command line

`pin --help` and `pin <subcommand> --help` print the same information.

```
pin [words…] [--fork | --resume | -w [name]] [--sort recency|alias|pinned] [--all] [--no-fzf]
```

| | |
|---|---|
| `pin` | the picker; enter opens, esc leaves |
| `pin <words…>` | loose match over alias and title, every word must appear; exactly one hit opens it, several open the picker pre-filtered, none prints "no pin matches" |
| `--fork` / `--resume` / `-w [name]` | one-off open mode: fork the session, plain resume ignoring the pin's modes, or a new worktree (optionally named); these apply to a unique match |
| `--sort`, `--all`, `--no-fzf` | starting sort, show expired pins from the start, use the numbered menu |

Every subcommand exits 0 on success and 1 with a one-line message on `stderr` otherwise.

| Subcommand | What it does |
|---|---|
| `pin add <session> <alias> [--title …] [--note …] [--cwd …] [--keep] [--fork] [--worktree] [--rename]` | pin a session. `<session>` is a session id, a unique id prefix (8+ characters), or words that must all appear in the title or directory of one of the 200 most recent sessions (quote them: `pin add "rc mower" mower`); several matches are listed with their ids. Title and directory default to the transcript's; a session given by full id with no transcript yet is accepted with a warning. Pinning an already pinned session says "already pinned as X" and, with `--title`/`--note`, updates it, or with `--rename`, renames it; a taken alias is refused with a suggested `alias-2` |
| `pin list [--all] [--sort …] [--json]` | the rows the picker shows, expired ones hidden unless `--all`; `--json` adds `state`, `age`, `open`, `markers`, `remaining_days` per pin |
| `pin sessions [words…] [--json]` | the 200 most recent sessions, newest first, with their short ids, titles, directories and a pinned marker; words filter the way `pin add` matches. This is what to run when you want to pin something by title from the terminal |
| `pin edit <alias> [flags]` | set fields directly: `--title`, `--note`, `--cwd`, `--rename <alias>`, `--model`, `--effort`, `--permission-mode` (empty string clears), `--keep`/`--no-keep`, `--fork`/`--no-fork`, `--worktree`/`--no-worktree`. With no flags, the interactive editor |
| `pin rename <alias> <new-alias>` | rename a pin; a taken alias is refused with a suggestion (`pin edit --rename` does the same) |
| `pin open <alias> [--fork] [--resume] [-w [name]]` | open by exact alias, with the same one-off modes as above; runs the already-open, missing-directory and branch prompts first |
| `pin rm <alias>` / `pin unpin <alias>` | unpin, no confirmation; "no pin named x" when there is none |
| `pin undo` | restore the last unpin or prune (the last ten are kept); a restored alias that is taken meanwhile comes back as `alias-2` |
| `pin prune [-y]` | unpin every expired pin after listing them and asking; `-y` skips the question; "nothing to prune" otherwise |
| `pin touch <alias>` | bump the transcript's mtime, restarting its retention clock |
| `pin doctor` | fzf version, ccusage and its price coverage across your sessions, store health, projects directory, cleanup period, keymap file; exit 1 if anything is ✗ |

Every run of any of these also touches the transcripts of pins with `keep`.

## Files and environment

| | |
|---|---|
| `~/.local/state/claude-pins/pins.json` | the store (`XDG_STATE_HOME`; `CLAUDE_PINS_FILE` overrides) |
| `~/.config/claude-pins/keys.toml` | keymap, written by the f1 screen; one `action = "key"` line per action, an empty string unbinds (`XDG_CONFIG_HOME`; `CLAUDE_PINS_KEYMAP` overrides) |
| `~/.cache/claude-pins/` | transcript summaries and cost answers, keyed on the transcript's mtime; safe to delete (`XDG_CACHE_HOME`) |
| `CLAUDE_CONFIG_DIR` | where Claude's `projects/` and `settings.json` live (default `~/.claude`) |
| `CLAUDE_PINS_SORT` | starting sort: `recency` (default), `alias`, `pinned` |
| `CLAUDE_PINS_EXPIRE_WARN` | days before expiry at which ⏳ shows (default 7) |
| `CLAUDE_PINS_NO_FZF` | force the numbered menu |
| `CLAUDE_PINS_GLYPHS` | `emoji` or `text` markers (default: emoji on a UTF-8 locale outside the Linux console) |
| `NO_COLOR` / `CLAUDE_PINS_COLOR=0` / `CLAUDE_PINS_COLOR=1` | never / never / always color |
| `CLAUDE_PINS_FZF`, `CLAUDE_PINS_CCUSAGE` | alternate binaries |
| `CLAUDE_PINS_PS`, `CLAUDE_PINS_NOW` | test hooks: a fake process table file, a fake clock (epoch seconds) |

## Development

```
python3 -m unittest            # store, reader, expiry, git worktrees, opener prompts, picker flows
bash tests/coverage.sh         # the same suite under coverage, bin/pin subprocesses included (needs uv)
bash tests/completion_check.sh # bash completion (CI also runs it on bash 3.2 / 4.4 / 5.2 images, plus shellcheck)
zsh tests/zsh_completion_check.sh                 # zsh completion
python3 -m unittest -v tests.test_fzf_real        # every screen's rows through the real fzf on PATH (CI: 0.44.1 to 0.74.3)
uv run docs/preview.py                            # regenerate the README preview
```

The tool itself is stdlib only. `pyproject.toml` exists for the dev tools (`coverage`, `pyte`),
which `uv sync --group dev` installs into `.venv`; `uv run` finds them without activating it.

Tests never run the real `claude`: a stub on `PATH` records the argv and cwd it was launched
with. Zero Claude usage in CI. A scripted stand-in for fzf drives the picker's flows, and
`tests/test_fzf_real.py` feeds what every screen sends to a real fzf to check that queries
match; CI runs that against four fzf versions, and locally it uses whichever fzf is on `PATH`.
`tests/test_plugin.py` checks the plugin files without a model: frontmatter, the commands'
dynamic-context snippets against a real store, the install skill's shell steps in a sandbox,
and that every line the doctor skill explains is one `pin doctor` prints.

```
tests/skills/run.sh [-m MODEL] [-n RUNS] [CASE...]   # headless skill runs through `claude -p`; paid, by hand
```

Each case under `tests/skills/cases/` is a prompt plus setup and check scripts, run with the
plugin loaded against a throwaway HOME whose `claude` on PATH is a stub, so no case can start
a real session. Seven cases (pin with and without arguments, already pinned, unpin, unpin
when nothing is pinned, install, doctor) cost about $0.50 on sonnet.

Whether the skills *trigger* on the right requests is a separate question, checked with the
[skill-creator](https://github.com/anthropics/claude-plugins-official) plugin's description
evaluator over twenty queries per skill in `tests/skills/triggers/` (ten that should trigger,
ten near-misses that should not):

```
tests/skills/triggers.sh -e [-m MODEL] [-r RUNS] [SKILL...]   # score the current descriptions
tests/skills/triggers.sh [-m MODEL] [SKILL...]                # optimizer: proposes a better description
```

The optimizer never edits `SKILL.md`; it prints the best description it found, scored on a
held-out split, for you to paste in. `-e -m haiku -r 1` is a two-minute smoke test per skill.

**Releasing:** add a `CHANGELOG.md` section, bump `version` in `.claude-plugin/plugin.json`
and `claude_pins/__init__.py` (a test keeps the three in step), commit `Version X.Y.Z`, tag
`vX.Y.Z` with the section as its message, `gh release create` with the same notes. The
marketplace needs no change: its entries carry no version.

MIT — see `LICENSE`. Version history in `CHANGELOG.md`.
