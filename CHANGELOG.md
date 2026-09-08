# Changelog

Versions follow the `version` field in `.claude-plugin/plugin.json`; Claude
Code offers a plugin update when that field changes. Each version is a git
tag (`v0.3.1`) and a GitHub release with this section as its notes.

## Unreleased

- **Pins name their sessions.** Pinning renames the Claude session to `📌 alias`, the way
  `/rename` does, so the pin shows in Claude's own `/resume` picker, prompt box and
  terminal title; the picker shows it at once and a running session catches up within a
  few turns. `pin rename` and `pin edit --rename` rename the session too, unpinning puts
  back the name the session had before (or clears it), and `pin undo` names it again; each
  command says what it did to the name. A name you set with `/rename` after pinning is
  left alone. A fork opened from a pin is named with the plain alias instead of inheriting
  `📌 alias`. `pin sessions` and the new-pin screen list a pinned session under the pin's
  title, next to its 📌 tag. Naming keeps the transcript's modification time, so it changes
  neither the idle time nor the retention clock. A pin made before this release does not
  name its session until it is unpinned and pinned again.
- The built-in picker gives a lone esc its whole delay even when the preview
  or a resize wakes it part-way through, so on a terminal that sends ESC and
  the rest of a key separately, alt-i pressed right after a cursor move can
  no longer be read as esc. It also no longer repaints an identical frame
  when the preview's last post arrives, which could leave a stale frame
  after a resize.

## 0.6.0 — 2026-09-07

- **The same picker without fzf.** The numbered menu is gone. When fzf is
  missing or older than 0.44 (or with `--no-fzf`), `pin` draws every screen
  itself in a raw-mode terminal: the same header, prompt and counter, the
  pointer, marker and current-row highlight, the preview pane and its size
  rule, the palette, editor, details and help screens and every prompt, with
  the same keys, the keymap file included, and a mouse (click, double-click to
  open, right click to select, the wheel over the list or the pane; shift-up
  and shift-down scroll the pane from the keyboard). Typing filters with fzf's
  syntax (`'exact`, `^prefix`, `suffix$`, `!not`, `a | b`, smart case) and
  highlights the matches; rows keep their order, as they always have under
  fzf (`--no-sort`). The preview is computed off the input loop, so the cursor
  never waits on ccusage; a resize re-lays the rows out. The look was matched
  to fzf 0.67 cell for cell through a terminal emulator, and a test keeps it so.
- **fzf is optional.** `pin doctor` marks a missing or old fzf with `·` and
  exits 0, saying that the built-in picker draws the same screens and how to
  install fzf. The doctor and install skills say the same.
- **Alt keys on macOS and in xterm.** Every Mac terminal types symbols on
  Option until it is told to send it as Meta, and stock xterm sets the high
  bit, which is why alt-i and the other alt keys did nothing there. The picker
  now names the switch for the terminal it is in (Terminal.app, iTerm2, VS
  Code, Ghostty, Kitty, Alacritty, xterm; WezTerm is on already) on its status
  line the first time it runs and on the f1 screen, reading each terminal's
  settings so a user who has set it sees nothing, and `pin doctor` reports it.
  The README has the table.
- **Keys that reach the picker everywhere.** The actions palette is ctrl-x
  (ctrl-space never reaches a program in VS Code's terminal, which keeps it
  for its own suggestions), refresh is ctrl-r, edit is f2 and new pin is
  ctrl-t; the f1 screen's reset-all is alt-r. Alt keys stay for the rest. A
  saved `keys.toml` keeps whatever it names.
- `pin _keys` names every key and mouse event as the picker reads it, for
  checking a terminal or a bug report. `tests/terminals/run.sh` does that for
  xterm, GNOME Terminal, Konsole, Kitty, Alacritty, Ghostty and tmux under
  Docker; the keys those terminals keep for themselves are recorded in the
  README, and none of them is bound.
- A question asked where there is no terminal (a pipe, a script) is now
  cancelled with a line on stderr instead of read from stdin; `TERM=dumb` gets
  the list and a pointer at the commands.
- The counter on the new-pin screen reads `3 sessions` (fzf 0.65.2 and newer),
  as the picker's reads `3 pins`.

## 0.5.1 — 2026-09-07

- **A calmer top.** The marker legend and the key hints share one line when the
  terminal is wide enough (about 150 columns), legend left and hints at the right
  edge, and stack with the legend first otherwise; fzf re-fits them on resize
  (on 0.44 and 0.45 on the next cursor move or keystroke). A status line then
  separates them from the prompt: it carries a flash for one screen, or the
  too-short note, and is blank the rest of the time, so the legend no longer
  disappears when something is confirmed. On fzf 0.63 and newer a blank row
  separates the prompt from the column labels (and from the list on every other
  screen); older builds draw the labels above the prompt, which is where fzf put
  them all along, and get no extra row. The blank line above the prompt is on
  every screen, the editor and the prompts included.
- fzf runs the picker's shell snippets under `sh` (`--with-shell`, fzf 0.51 and
  newer) instead of the login shell, so a fish user's picker no longer depends
  on the snippets being fish-compatible.
- **The query matches what you would type.** The picker filters on alias, title
  and directory, and the new-pin screen on title and directory; the idle time,
  the message count and the marker glyphs are shown but no longer matched, so
  `1d` stops keeping every pin idle for a day. A squeezed directory matches as
  it is drawn, its last component always whole.
- **Labels where a table needs them.** The new-pin screen has the picker's
  column labels, and `pin list` and `pin sessions` print them on a terminal
  (`pin list` adds the marker legend under the table); piped output stays bare
  rows. The no-fzf menu gets the labels over both of its tables and the legend
  under the pin table, not only on `?`. A session that is already pinned shows
  📌 and the pin's alias instead of the keep flag and the word "pinned", which
  contradicted the legend; the text glyph set gets ⚲ for it.
- The id `pin sessions` lists is the shortest prefix, eight characters or more,
  that is unique among the recent sessions, the way git abbreviates, so it
  always resolves in `pin add`; the ambiguity listing uses the same prefixes.

## 0.5.0 — 2026-09-07

The picker's look is new, the editor lives inside fzf, and nothing flickers or
prompts in plain text on the way. The layout, theme and glyphs come first
below, then the screens, in the order they are met.

- **Layout.** The hints sit above the prompt (fzf's `--header-first`) with the
  marker legend under them, and a status flash takes the legend's line so the
  list never moves. The prompt carries a 📌 breadcrumb on every screen. A
  sticky label row names the columns; the age column is `idle`, the time since
  the transcript was last written. Alias and directory columns are as wide as
  their longest value, capped near a fifth and a third of the terminal; the
  directory gives way first when the terminal is narrow, shortening fish-style
  (`~/g/c/claude-pins`) and keeping its last component. A Claude worktree shows
  as `~/git/repo › name`. Widths are measured in terminal cells, so emoji no
  longer push columns out of line.
- **Theme.** Claude Code's own colours: the prompt in clay, the pointer and
  matched letters in periwinkle, the multi-select marker in green, and the
  chrome (hints, legend, counter, borders, labels, group names) dim rather than
  grey, so light terminals read as well as dark ones. In the preview the effort
  and the context percentage sit on the budget statusline's green-to-red ramp
  and the permission mode wears the colour Claude's mode indicator gives it.
  Flashes are green for ✓ and coral for ✗. `CLAUDE_PINS_COLOR=0` joins
  `NO_COLOR`, and `CLAUDE_PINS_COLOR=1` forces colour on.
- **Emoji markers**: 🟢 open, 🚩 keep, 🔀 fork, 🌳 worktree, ⏳ expiring,
  🔴 expired, chosen when the locale is UTF-8 and the terminal is not the
  Linux console or `dumb`. Elsewhere the one-cell set ● ⚑ ⑂ ⌂ ⧗ ✗ stays, now
  in the same colours the emoji carry, and the prompt drops its 📌.
  `CLAUDE_PINS_GLYPHS=emoji|text` overrides the detection, for the picker and
  the numbered menu alike.
- **Preview.** The branch row reads `main`, `main checked out · session ran on
  feat/x` (yellow) or `(not a git repo)`; the transcript size sits with the
  message count on a `transcript` row; the full session id is the last metadata
  row; `last 2d` became `last activity 2d ago`. The pane hides itself when it
  would get fewer than ten rows, decided by terminal height and re-evaluated
  on resize; the header then says so, and alt-v cannot bring it back until the
  terminal is taller.
- **Details screen** on alt-i (also in the palette): the whole preview on its own
  screen with more of the last exchange; enter opens the pin, esc goes back.
- **Palette, help and editor** group their rows with a dim gutter name instead
  of separator rows, so every row is selectable, and the key column starts after
  the longest label.
- **Editor and prompts.** No text prompt interrupts the picker any more: a
  text field is typed on fzf's query line under a `📌 pins › alias › edit ›
  title ›` breadcrumb with the current value in place (enter saves, esc
  cancels, ctrl-u clears); the directory field lists completions of what is
  typed and reloads them as you type; a new pin's alias, a key to rebind, the
  prune question, and the opener's missing-directory, branch and already-open
  questions are short lists. The editor's pane renders the unsaved draft with
  changed rows marked `*`, and esc with changes offers save / discard / keep
  editing. What the opener says on the way (a recreated worktree, an unpin)
  is printed once the shell is back, or joins the flash when the open is
  cancelled. Without fzf the same questions are readline prompts with the
  value pre-filled and numbered lists (macOS's libedit cannot pre-fill, so
  there the prompt says what enter keeps and that `c` clears); the plain
  editor shares the breadcrumb and the gutter grouping.
- **Refresh.** alt-r re-reads the store in place, without restarting the
  picker, for a picker left open while another terminal pinned something. Enter
  opens a pin that arrived that way. On fzf 0.46 and newer the rows also re-fit
  when the terminal is resized, and the too-short note follows the height; on
  0.44 the note catches up on the next cursor move or keystroke. On 0.65.2 and
  newer the counter reads `3 of 5 pins · 2 selected`.
- **No flicker between screens.** Every fzf screen draws over the previous one
  (fzf's `--no-clear`); the shell comes back once, when the tool exits or
  hands over to claude.
- `pin list` and `pin sessions` share the renderer, so their columns follow.
- `pin doctor`'s install hint points at fzf's releases page instead of naming a
  version that goes stale.

## 0.4.1 — 2026-09-06

- **Fixed:** typing in the picker's query hid every pin. The rows carried a
  hidden search field that fzf's `--nth` was meant to match, but fzf applies
  `--nth` to the line after `--with-nth` has trimmed it, so the field was
  never there to match (on every supported fzf version). The query now
  matches the visible row: alias, title, directory, age and flags. The same
  applied to the editor, the action palette, the help screen and the session
  chooser. A new test module, `tests/test_fzf_real.py`, feeds what every
  screen sends to the real fzf and asserts what a query keeps; it runs locally
  against the fzf on `PATH` and in CI against 0.44.1, 0.53.0, 0.64.0 and
  0.74.3, and also runs the real interactive picker once in a pseudo-terminal:
  type a query, press enter, see claude launched. It replaces the option-grammar
  check, which only asked whether fzf accepted the options.

## 0.4.0 — 2026-09-06

- **Fixed:** a session started with `claude --worktree`, pinned from inside
  and reopened in place, got a "branch differs" prompt on every open that
  offered to check out the base branch inside the worktree. Claude records
  the branch before the worktree checkout, so the branch check now skips
  Claude worktree directories.
- `pin rename <alias> <new-alias>`.
- `pin add` takes a session id, a unique id prefix, or words from the title
  or directory of one of the 200 most recent sessions; `pin sessions
  [words…]` lists them with their ids, so a session can be pinned by title
  from the terminal.
- zsh completion (`completions/pin.zsh`); the install skill installs the one
  for your shell.
- Transcript and cost caches write through per-process temp files, so two
  fzf previews summarizing the same session cannot clobber each other.
- A store directory that cannot be written (read-only, or a directory where
  `pins.json` should be) is a one-line `cannot write` error instead of a
  traceback.
- A store whose `undo` list was hand-edited into something other than
  objects with a `pins` list no longer crashes `pin undo`; such entries are
  dropped on load.
- Empty-state messages and the completion header say `/pins:pin` and
  `/pins:install` (the namespaced names).
- README preview regenerated; the committed one predated the streaming
  preview and still showed the token count on the context line.
- Dev tooling: `pyproject.toml` with a uv dev group (`coverage`, `pyte`) and
  `tests/coverage.sh`, which traces the `bin/pin` subprocesses the tests
  spawn, exec included.

## 0.3.1 — 2026-09-06

- Install skill quotes the doctor's lines as printed instead of summarizing
  them. The headless install case accepts "Doctor" as well as "doctor".

## 0.3.0 — 2026-09-06

- **Skills renamed** to `/pins:install` and `/pins:doctor` (were
  `/pins:pins-install` and `/pins:pins-doctor`). Claude Code namespaces
  plugin skills with the plugin name, so the prefix was said twice.
- The install skill owns "`pin` is not found"; the doctor skill offers the
  install steps when it finds the symlink missing instead of only pointing
  at the other skill.
- Skill trigger evals: `tests/skills/triggers.sh` scores each skill's
  description over twenty queries in `tests/skills/triggers/` through the
  skill-creator plugin's evaluator, and can run its optimizer. Paid, by hand.
- `DESIGN.md` folded into `CLAUDE.md`; the verified facts the code relies on
  live there now.

## 0.2.1 — 2026-09-06

- Tests for the plugin itself: `tests/test_plugin.py` (free: manifest and
  changelog in step, frontmatter, the commands' dynamic-context snippets,
  the install skill's shell steps in a sandbox, the doctor skill's table
  against the doctor's real lines) and `tests/skills/run.sh` (paid, by hand:
  seven headless `claude -p` cases with a stub `claude` on PATH).
- README: updating and removing, a table of the plugin's commands and
  skills, a command-line reference, files and environment, the palette-only
  actions and `keys.toml` action names, editor and menu keys, the release
  procedure. Tests keep the Keys table, the subcommand list and the
  environment knobs in step with the code.
- Marketplace entries no longer carry a `version`; `plugin.json` is the
  single source (the docs warn against setting both).

## 0.2.0 — 2026-09-06

- **Cost line that knows what it does not know.** ccusage's offline price
  table prices the models it has and silently reports $0 for the rest, so a
  session on a new model looked cheap ($0.05 for haiku subagent turns) rather
  than unpriced ($27 online). `pin` now reads ccusage's per-model breakdown:
  any model with tokens but $0 marks the offline answer as partial and
  triggers one online run (5 s timeout; failures remembered for ten minutes
  so a preview pane never stalls twice). The line reads `est $27.57 (ccusage
  online)` when the fallback priced it, and `est ≥ $0.05 · no price for
  fable-5-1 · npm i -g ccusage@latest` when nothing does. Results cache per
  transcript change as before; a nonzero ccusage exit is an error even when
  JSON was printed.
- **Preview pane streams.** Everything above the cost line prints and
  flushes before the cost lookup, so the pane fills instantly.
- **`pin doctor` checks price coverage across every session**, not just the
  newest: which models the offline table lacks, how many sessions use them,
  and whether the online fallback prices them.
- **Menu fallback edits without fzf.** The no-fzf menu's `eN` opened the fzf
  editor and silently cancelled; it now opens a numbered field editor with
  the same form.
- **`/pins:pins-doctor` skill** runs `pin doctor` and turns each line into a fix.
- Dropped the bare `/pin` and `/unpin` command shims: Claude Code namespaces
  plugin commands by design, so the commands are `/pins:pin` and
  `/pins:unpin`.
- CI: shellcheck on the completion script; macOS temp-dir symlinks no longer
  fail the opener tests.

## 0.1.0 — 2026-09-06

Initial release.

- `pin`: fzf picker with preview pane, status flashes, actions palette,
  help screen with rebinding, new-pin flow, prune and undo, sort cycling,
  multi-select, empty state; numbered-menu fallback without fzf.
- Store at `~/.local/state/claude-pins/pins.json` with atomic writes,
  corrupt-file quarantine and undo tombstones.
- Opening: `cd` + `claude --resume`, per-pin fork and worktree modes with
  one-off overrides, already-open detection, missing-directory tiers that
  recreate Claude worktrees on their surviving `worktree-<name>` branch,
  branch-mismatch prompt that never auto-switches.
- Expiry from Claude's `cleanupPeriodDays`; `keep` pins touched on every run.
- Subcommands: add, list, edit (flags or interactive editor), open,
  rm/unpin, undo, prune, touch, doctor; bash completion.
- Plugin: `/pins:pin`, `/pins:unpin`, `/pins:pins-install`.
- Tests against a stub `claude` and a scripted fzf; CI on ubuntu and macOS
  for Python 3.10–3.13, completion on bash 3.2/4.4/5.2 images, the picker's
  option grammar against a real fzf 0.44.1.
