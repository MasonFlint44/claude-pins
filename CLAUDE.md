# claude-pins

Pin Claude Code sessions and reopen them by alias. `bin/pins` is the terminal
command (Python 3.10+, standard library only); `commands/` and `skills/` are the
Claude Code plugin. `README.md` is the user manual.

## Facts the code relies on

Verified against Claude Code 2.1 and fzf 0.44 before the build; they are the
reasons behind several design choices and are recorded nowhere else.

- The retention sweep deletes any file under `~/.claude/projects/` whose mtime is
  older than `cleanupPeriodDays`. Only the transcript's mtime matters: session
  names do not protect it, and its sidecar files are swept on their own mtimes.
  That is why opening a pin touches the transcript and `keep` pins are touched on
  every run.
- `--resume` finds a session in any project directory. `--fork-session` writes a
  new transcript, with the copied records' `cwd` rewritten, and never touches the
  original, so a fork alone would not extend the original's life.
- A session's name is the last `custom-title` record in its transcript: `/rename`
  and `claude --name` both append `{"type":"custom-title","customTitle":"…",
  "sessionId":"…"}` (and rewrite the sidecar `<dir>/<id>/custom-title.json`
  when one exists; an empty title clears the name and deletes the sidecar).
  `claude_pins/naming.py` writes that record itself, byte for byte compact
  JSON, because Claude recognises the line by the substrings
  `"type":"custom-title"` and `"customTitle":"`: a line with spaces after the
  colons was ignored and overridden by the session's own re-append. Claude's
  `/resume` picker reads a head and a tail chunk of each transcript and takes
  the title from either, the sidecar only as a fallback; a running session
  re-reads the last record before each of its metadata re-appends (every
  32 KB written) and adopts it, which took four short haiku turns when
  measured, so no hook is involved and the plugin commands say the prompt box
  catches up within a few turns. Both were checked on 2.1.263. The append
  bumps the mtime that the idle column, the recency sort and the retention
  sweep read, and that is kept on purpose: a pin made on an old session
  would otherwise be swept days later, and the tool already counts opening
  and `keep` as activity (`tests/helpers.pin_aged` pins without it for the
  fixtures that test ages). Claude's transcript GC
  (off by default, files over 5 MB) keeps only the last copy of a
  last-wins record, custom-title included, which is why `transcript.py`'s
  whole-file scan treats an empty title as a clear rather than skipping it.
- `--name` combines with `--resume` and with `--fork-session`, and a fork made
  without it inherits the original's custom title (the record is copied), so
  the opener names a fork with the plain alias. Two live sessions with one
  name are suffixed by Claude itself, and names are compared after NFKC,
  lowercase and whitespace-to-dash with emoji kept, so `📌 mower` and `mower`
  never collide.
- `gitBranch` in a worktree session's records is stale (captured before the
  worktree checkout), so the preview reads the branch from git.
- Inside a session the Bash tool sees `CLAUDE_CODE_SESSION_ID`; command templates
  get `${CLAUDE_SESSION_ID}` substituted. Nothing tails history files.
- fzf applies `--nth` to the line *after* `--with-nth` has trimmed it, so a
  hidden field cannot be the search target (this hid every pin from the query
  until 0.4.1; the 0.64+ template form of `--with-nth` behaves the same). Rows
  are `id<tab>display`, and on the picker and the new-pin screen the display's
  own columns are tab-separated too, so `--nth=1..3` (alias, title, directory)
  and `--nth=1..2` (title, directory) keep the idle time, message count and
  marker glyphs out of the match; `--tabstop=1` draws each tab as one space, so
  the layout is the plain table's (checked on 0.44.1, 0.53.0, 0.64.0, 0.74.3).
  Values have their tabs flattened first, since a tab in a title would shift the
  fields. `--filter` with `--no-sort` prints the trimmed line instead of the
  whole one, which only `tests/test_fzf_real.py` has to work around.
- fzf cannot bind printable characters (they type into the query), which is why
  the help screen's reset keys are ctrl-r and alt-r rather than `r` and `R`.
  Its own editing keys are left alone so the filter stays editable, and alt+enter
  is avoided because Windows Terminal takes it.
- The fzf backend has passed `--no-sort --tiebreak=index` since the first
  commit, so fzf never ranked rows here and the alt-s order holds while typing
  under both backends. That is why fzf is documented as optional rather than
  recommended: what it adds over the built-in picker is nothing a user sees.
- VS Code's integrated terminal keeps these keys from the shell by default (its
  `DEFAULT_COMMANDS_TO_SKIP_SHELL` plus the find, history and suggest
  contributions, read from the vscode main branch): f1 and ctrl-shift-p, f3,
  f5, f10, f11, ctrl-shift-b, ctrl-p, ctrl-f, ctrl-g, ctrl-alt-r and
  ctrl-space (terminal suggest, on by default). ctrl-r, f2, ctrl-x and ctrl-t
  reach the shell, which is why the palette is ctrl-x, refresh ctrl-r, edit
  f2, new pin ctrl-t and the help screen's reset-all alt-r; help stays f1
  because the palette lists it. Alt keys reach the shell there on Linux and
  Windows but on macOS only with `terminal.integrated.macOptionIsMeta`.
- Every macOS terminal starts with Option typing symbols and accents, and
  stock xterm on any system sets the high bit for Meta, so the alt keys are
  dead until a per-terminal switch is on. `claude_pins/altkeys.py` names the
  terminal from `TERM_PROGRAM` (Apple_Terminal, iTerm.app, vscode, ghostty,
  WezTerm), `KITTY_WINDOW_ID` or `TERM=xterm-kitty`, `ALACRITTY_WINDOW_ID`
  and `XTERM_VERSION`, and reads the switch where each keeps it: Terminal.app
  `~/Library/Preferences/com.apple.Terminal.plist` (the default profile's
  `useOptionAsMetaKey`), iTerm2 `com.googlecode.iterm2.plist` (the
  `ITERM_PROFILE` entry of "New Bookmarks", "Option Key Sends" 2 is Esc+; 1 is
  Meta, which sets the high bit and is no use), VS Code's user `settings.json`
  (searched, not parsed: it allows comments), Ghostty's config (the
  Application Support file is loaded after the XDG one and wins), `kitty.conf`,
  `alacritty.toml`; WezTerm sends Meta by default; xterm's `metaSendsEscape`
  (or `eightBitInput` false) from `xrdb -query`, the resources the X server
  holds, else `~/.Xresources` and `~/.Xdefaults`. Over ssh only iTerm2 says
  who it is (`LC_TERMINAL`). Unreadable or unparsable is unknown, which shows
  the note like off; only a switch read as on silences it, so a user who set it
  is never nagged. The keymap is not per-OS: one shortcut inside and outside
  VS Code, on every platform, was the deciding point, so the palette and the
  frequent actions sit on keys that need no switch.
- `--header-first` draws the header above the prompt. Its last line is a status
  line (the flash, the too-short note, or a space: fzf drops a trailing newline
  in `--header` but keeps a line holding a space), so a flash never displaces
  the legend and the list never moves. The legend and the hints share one line
  when the width allows four cells between them (about 147 columns) and stack
  otherwise; that choice needs the width fzf has, so `fzf.Header.text()` lays
  out the launch and the same arithmetic in `fzf.header_transform()` re-fits it.
  `--header-lines=N` makes the first N input lines sticky rows (the blank gap
  row, the column labels): cut by `--with-nth` like rows but not items, so
  `start:pos` numbering, multi-select and `--filter` output all skip them.
  Until 0.62 `--header-first` moves those rows above the prompt too (bisected on
  downloaded builds; the changelog does not say), and 0.53 crashes on enter over
  sticky rows alone, so the gap row is gated to 0.63+ and older builds keep the
  labels above the prompt.
- `--preview-window 'down,55%,<10(hidden)'` hides the pane when it would get
  fewer than ten rows, measured on the pane itself, border rows included, as 55%
  of the terminal rows left after fzf's bottom border (measured on 0.67: 19 rows
  shows, 18 hides; one fewer with the expired footer). fzf re-checks on every
  resize; Python mirrors the arithmetic in `fzf.preview_fits()` at restart for
  the alt-v refusal, and `fzf.header_transform()` carries the same limit into
  the shell that keeps the header note current. Header lines do not enter it:
  fzf sizes the pane from the terminal height alone.
- `--no-clear` only skips leaving the alternate screen: fzf still restores cooked
  mode, the cursor and mouse tracking on exit (measured on 0.44.1, 0.53.0 and
  0.67.0), and the next run enters the screen itself and repaints every row.
  So `fzf.leave_screen()`'s single `\x1b[?1049l` is the whole restore, emitted
  when the outermost screen hold ends, after a prompt run outside any hold,
  before exec, and on every way out of `cli.main`.
- Feature versions, from fzf's CHANGELOG and gated in `fzf.supports()`: the
  `resize` event with `$FZF_LINES`, `$FZF_COLUMNS` and the count variables
  0.46.0; `--info-command` with `$FZF_INFO` 0.54.0, gated at 0.65.2 because
  `--info=inline-right` cut its last cell before that (`3 pin…`, seen on
  0.64.0); `transform-header` 0.40.0; `--with-shell` 0.51.0; sticky rows
  under the prompt 0.63.0.
  Inside a transform on 0.44 `$FZF_LINES` and `$FZF_COLUMNS` are empty and
  `tput lines` answers 24 whatever the size, while `stty size </dev/tty` is
  right, which is why the header transform binds to `focus,change` there and
  to `resize` on 0.46+.
- The info command runs on every keystroke and the header transform on every
  cursor move (0.44), so both are plain shell over fzf's variables; only a
  reload may start Python (`pins _rows`, `pins _dirs`, both told `--gap` rather
  than asking fzf its version). fzf runs them under `$SHELL`, and they are
  POSIX sh (`expr`, `printf "%*s"`), so `--with-shell "sh -c"` is passed where
  fzf has it; below 0.51 a fish login shell would break them.
- fzf ends a parenthesised action at the first `)`, and a later `--bind` for a
  trigger replaces the earlier one instead of adding to it: `build_args` merges
  binds per trigger with `+`, and the shell snippets contain no parentheses or
  brackets.
- `--header-lines` applies to reloaded input too, the count variables exclude
  the sticky rows, and fzf counts a reload in as it reads it (the counter passes
  through `3 of 4 pins`), so the pty test waits for a row, not the count.
- `--filter` mode ignores `--disabled`, so the details screen's real-fzf test
  can only check that its options are accepted and that an empty query keeps
  every line.
- Emoji and other East Asian wide characters take two terminal cells, and fzf's
  runewidth agrees, so every column width goes through `claude_pins/text.py`
  rather than `len()`.
- In `--color`, `header:dim` keeps fzf's own colour for the element (teal on the
  dark theme) and only adds the attribute; `header:-1:dim` is the terminal's
  foreground dimmed, which is what reads on light and dark alike. `-1`,
  `#rrggbb` and `preview-label` are all accepted on 0.44.1, and a bad name is
  exit 2, so `test_options_accepted` runs the spec through every CI build.
- pyte 0.8.2 (`docs/preview.py`) keeps 24-bit foregrounds as six hex digits and
  gives a wide glyph two cells with the second one empty, but drops SGR 2, so
  the generator records dim itself (in the unused italics slot) to grey the
  chrome in the SVG.
- Enter on an fzf run with no input lines exits 1 and still prints the query
  (0.44.1 through 0.74.3; the editor pty test types a title that way), which is
  what makes a text field one `--disabled --query` screen with no rows.
- fzf leaves its alternate screen up under `--no-clear`, so anything printed
  between two screens is drawn over by the next one. The picker and editor hold
  the screen (`screen.hold_screen()`) and route messages through headers and
  flashes; the opener queues its banners for the moment the shell is back
  (`launch()` after the restore, or the cancel flash). A prompt run outside a
  hold (`pins prune`, `pins edit`) drops the screen as soon as it ends so the
  command's output is seen. The built-in picker keeps the same discipline: it
  enters the alternate screen only when no screen left it up, and never leaves
  it itself.
- A plugin's hooks are `hooks/hooks.json` at its root, in the shape a
  `settings.json` `hooks` block has, and they register when the plugin is
  enabled with no settings edit by the user (`claude plugin validate .` checks
  the file, `/hooks` lists what registered). A `SessionStart` hook runs on
  `startup`, `resume`, `clear`, `compact` and `fork` (its matcher values; no
  matcher means all of them), gets `${CLAUDE_PLUGIN_ROOT}` expanded in its
  command and the event JSON on stdin, and cannot block a start; but its
  plain-text stdout is added to Claude's context (one of four events where
  that happens), exit 2 shows its stderr to the user as a hook-error notice,
  and any other nonzero exit is a non-blocking error. That is why `pins _keep`
  prints nothing and returns 0 on every path, a corrupt or missing store
  included: those are `pins doctor`'s to report, not something to put in front
  of the user or the model at every start. The default command timeout is
  600 s; hooks.json sets 10. A session loads a plugin's hooks at start and
  keeps the old version's path after `claude plugin update` until
  `/reload-plugins` or a restart (a skill's SKILL.md is the one part re-read
  live), so the docs and the changelog say to restart. Claude records enabled
  plugins in `settings.json` (`enabledPlugins`, `pins@<marketplace>: true`),
  which is all the doctor's keep line reads: no installed plugin predates the
  hook, so there is no older version to tell apart. Checked against the hooks
  and plugins references on 2026-09-08.
- Terminal automation (opening a new tab for the resumed session) was dropped on
  purpose: Ghostty's D-Bus surface offers new-window only.

## Facts the built-in picker relies on

Every screen is a `screen.Screen`; fzf draws it when it is on PATH and ≥ 0.44,
`tui.py` draws it otherwise. The hooks a screen runs (preview, reload, the rows
for a query) are named, not closures, because fzf can only run commands: the fzf
backend lowers each to a `pins _<hook>` subprocess and the built-in one calls the
same function from `hooks.py` in process, which is what makes a reload draw
exactly what a launch would on either backend. Measured on fzf 0.67.0 in a pty
through pyte, and `tests/test_fzf_real.py` keeps the two backends cell-for-cell
equal (text, colour, bold) on the main list, a filtered query with a selection,
and the editor, on fzf 0.67 and newer (older builds draw the stock counter, no
gap row and a plain gutter, so the test skips there):

- The current row is bold, in 254 where the text has no colour of its own, on
  236 under the pointer, the marker and the text only (the padding stays
  unpainted); the gutter glyph `▌` is drawn in 236 on every other row; the
  prompt and the query are bold. The counter ends one cell before the right
  edge; header and sticky rows are indented two cells; the pane's scrollbar
  takes the padding column inside the right border and its `3/17` position
  sits on the first row; the last list column is left for the list scrollbar.
  `NO_COLOR` keeps only the attributes, as fzf's `--color=bw` does.
- The decoder is fzf's `src/tui/light.go` table (0.67.0), so every name the
  keymap file accepts decodes to itself. Esc alone is esc only after nothing
  follows it for `ESCDELAY` ms (fzf's default 100), because alt-x arrives as
  ESC x; a partial sequence left over after that wait is dropped, as fzf drops
  it; ESC ESC in one read is one esc. fzf's own defaults apply where the keymap
  is silent: home/end edit the query line, tab does nothing without
  multi-select, cycling wraps only from the last row, page moves are one row
  short of a page, a double-click accepts, a right click toggles, the wheel
  moves over the list and scrolls over the pane.
- The matcher is fzf's extended syntax over the `--nth` span of the plain row
  (fields keep their trailing delimiter), and its anchored forms step over the
  text's own whitespace the way fzf's `PrefixMatch`/`SuffixMatch`/`EqualMatch`
  do, or `mower$` could never match a directory column. Rows keep their order,
  as under fzf. `tests/test_fzf_real.py` checks that both keep the same rows
  for a list of queries on every screen.
- Mouse reporting is xterm 1000 with the SGR form 1006 (release events, no
  223-cell limit) and is switched off on every exit path; fzf's double-click
  window is 500 ms on one cell.
- The preview runs on a worker thread that posts chunks through a wake pipe
  the input loop selects on, because `cost.session_cost` can take up to 8 s on
  a ccusage cache miss and the cursor must not wait; results are cached per
  (row, pane width) for the screen's life and dropped on reload. SIGWINCH
  writes to the same pipe; a resize re-runs the `rows` hook at the new width.
- A screen shown where there is no terminal (a pipe, a script, `TERM=dumb`) is
  cancelled with a line on stderr rather than asked, since `input()` was the
  only alternative and it cannot draw a screen; `pins` with no arguments on a
  dumb terminal prints the list and where the commands are.
- The kernel discards SIGTSTP for an orphaned process group, so the ctrl-z test
  runs the picker under an interactive bash in the pty. The macOS runners' own
  `python3` (3.9) is what a shebang finds, so that test symlinks the test's
  interpreter first on PATH, and `keys.Event` is a `typing.Union` so an old
  interpreter fails on the version check rather than on an import.
- `tests/helpers.TuiSandbox` plays the built-in picker from
  `CLAUDE_PINS_TUI_SCRIPT` (one JSON step per screen, the way the fzf stub is
  scripted) and records each screen's items, header, last frame and result in
  `CLAUDE_PINS_TUI_LOG`; the flow tests read the log where the fzf flows read
  the stub's argv. A pty test cannot use `wait_for(fresh=True)` against the
  built-in picker: it enters the alternate screen once and redraws every screen
  over the last, so each wait clears what was read and looks at the new frame.
- The one-time alt-keys note is remembered by `config.noted_file()` in the
  cache directory; the pty and parity tests touch it first so their first
  screen is the ordinary one, and `tests/helpers.Sandbox` sets
  `CLAUDE_PINS_OS=linux` so the macOS runner never sees the note unasked.

## Verify before committing

```
python3 -m unittest -q                 # must exit 0; check the status, not the last line of output
python3 -m unittest -v tests.test_fzf_real   # the real fzf on PATH over every screen's rows; must say "ok", not "skipped"
uv run --group dev python -m unittest tests.test_tui tests.test_fzf_real   # with pyte: the painting test and the fzf parity test (they skip without it; CI installs it)
shellcheck completions/pins.bash tests/completion_check.sh tests/coverage.sh tests/skills/run.sh tests/skills/triggers.sh
bash tests/completion_check.sh         # after touching completions/pins.bash or the subcommand list
docker run --rm -v "$PWD:/repo:ro" zshusers/zsh:5.9 zsh /repo/tests/zsh_completion_check.sh   # no zsh on this machine
CLAUDE_PINS_TEST_FZF=/path/to/fzf python3 -m unittest tests.test_fzf_real   # another fzf build; CI runs 0.44.1 … 0.74.3
bash tests/coverage.sh                 # coverage report; needs uv (dev deps live in pyproject.toml)
tests/terminals/run.sh                 # every key in real Linux terminals under Docker (5 min, 4 GB image); after touching keys.py or the keymap
```

- Tests never launch the real `claude`: a stub on PATH records argv and cwd, so
  CI spends nothing. `tests/skills/run.sh` runs the skills through `claude -p`
  for real money (about $0.50 on sonnet); only run it by hand. So does
  `tests/skills/triggers.sh`, which scores the skill descriptions' triggering with
  the skill-creator plugin's evaluator (when to run it is under Conventions).
- fzf is tested two ways, and both are needed. The flow tests use
  `tests/fzf_stub.py`, a scripted user: the real fzf wants a terminal and a
  person typing, and the stub answers each screen from a script in milliseconds
  with no timing. It cannot say what fzf would match, so `tests/test_fzf_real.py`
  captures the rows and options every screen sends and runs the real binary over
  them in `--filter` mode, which uses the interactive matcher. That module skips
  when no fzf ≥ 0.44 is on PATH (and fails, not skips, when `CLAUDE_PINS_TEST_FZF`
  names a bad binary), so read its verbose output before pushing. A new screen
  must get a test there. The same module holds the few tests that run the real
  interactive picker in a pseudo-terminal (a query then enter launching the
  claude stub; a 16-row terminal hiding the preview). Each step there waits on a
  terminal redraw, so keep them few and make each prove several things; clear
  the captured stream and wait for the new screen before typing, since fzf's
  exit resets the tty and discards anything typed while the next fzf starts.
  A screen keeps drawing for a moment after the key that ends it (its header
  transform redraws on 0.44), and that tail satisfied waits meant for the next
  screen, so `wait_for(fresh=True)` matches only what came after the newest
  alternate-screen entry.
  The stub-driven tests are where a hidden-field bug hid for four releases: do
  not judge matching by them.
- Checking Claude's own behaviour by hand: `claude` started from a Claude
  session's Bash tool inherits `CLAUDE_CODE_CHILD_SESSION` and turns transcript
  saving off, so unset every `CLAUDE_*` variable first (`env -u …`). An empty
  session writes no transcript, and a `-p` session (entrypoint sdk-cli) never
  appears in Claude's `/resume` picker, so a picker check needs an interactive
  pty session with one haiku prompt; a running-session check needs about
  32 KB of new transcript (four 400-word prompts) before the name is re-read.
  A few cents on haiku each. The session-start hook is a free check: after a
  plugin update and a restart, `/hooks` lists it and a kept pin's transcript
  mtime moves on the next start.
- One test run at a time on a machine. Open-session detection reads the real
  `ps` table unless a test sets `CLAUDE_PINS_PS`, so two suites running at
  once see each other's stub `claude --resume` processes, land on the
  already-open prompt and fail across the opener and cli tests for no reason
  in the code. A flake is hunted by looping the suite sequentially with each
  run's output kept in a file: `tail -1` on the 0.6.0 release verify run
  discarded the one failure in eight, and finding it again took thirty runs.
- The built-in picker is tested the same two ways plus one: `tests/test_tui.py`
  runs its flows from a scripted terminal (the fzf stub's counterpart) and its
  frames in process, `tests/test_tui_pty.py` drives the raw-mode terminal for
  what only a terminal shows (cooked mode handed back before claude, the mouse,
  a resize signal, ctrl-z under bash, `pins _keys`), and `tests/test_fzf_real.py`
  holds the matcher agreement and the pyte parity test against the real fzf. A
  change to the chrome must keep the parity test green rather than be judged by
  eye. `pins _keys` is the by-hand check on a terminal: GNOME Terminal (VTE),
  Terminal.app, iTerm2, VS Code, Ghostty, Windows Terminal over WSL, tmux,
  Konsole and Kitty are the ones to try.
- `tests/terminals/` is the terminal checklist made automatic: xdotool presses
  every key into xterm, GNOME Terminal, Konsole, Kitty, Alacritty, Ghostty and
  tmux under Xvfb and `pins _keys` says what arrived. Its report is the record of
  what each terminal keeps for itself (GNOME Terminal F10 and F11, Konsole
  F11, tmux ctrl-b and the key after it) and that stock xterm sends alt keys
  8-bit until `XTerm*metaSendsEscape: true`, which is why the alt-keys probe
  covers xterm as well as macOS. macOS terminals and Windows Terminal stay a
  by-hand check (`pins _keys` there). Docker Desktop mounts only paths under
  the home directory, so the harness runs from the repository, not from /tmp.
- fzf support floors at 0.44.1, which lacks `transform`, `--footer`, `print`,
  `exclude` and the `result` event. The CI `fzf` job runs `tests/test_fzf_real.py`
  against 0.44.1, 0.53.0, 0.64.0 and 0.74.3; add a version there when a release
  changes matching or option grammar.
- `tests/test_plugin.py` reads the README: the Keys table must equal the keymap
  defaults, every subcommand must appear in the command reference, and every
  `CLAUDE_PINS_*` variable except `CLAUDE_PINS_EXE` must be in the environment
  table. A new key, subcommand or knob needs its README row in the same change.
- The picker's screenshot is generated: run `uv run docs/preview.py` after
  changing the picker's look, and commit `docs/preview.svg` and
  `docs/preview.txt` with the change.
- Coverage only counts when the `bin/pins` subprocesses are traced, which is
  what `tests/coverage.sh` sets up (plain `coverage run` reports about half).
  Paths ending in `os.execv` are saved by the hook in `tests/coverage_hook/`.

## Conventions

- No third-party runtime dependencies. Everything users run is stdlib plus
  optional fzf and ccusage. `pyproject.toml` is dev tooling only (`uv sync
  --group dev` for coverage and pyte); the version there is read from
  `claude_pins/__init__.py`, so a release does not touch it.
- Scope: keep a change and its tests to what was asked; scratch checks (a pyte
  capture, a one-off script) need not be kept. Prefer targeted edits over
  rewriting a file when the result is the same. Proceed on reversible steps
  that follow from the request; ask before anything destructive or that changes
  the scope (a release, a paid skill test).
- A change lands with its tests in the same commit, and `bash tests/coverage.sh`
  stays where it was (96% at 0.7.0): the suite is what stands in for a person
  at a terminal, since nothing here runs the real `claude`. A new subcommand,
  screen or hook gets its test in the module that covers its kind
  (`tests/test_cli.py`, `tests/test_fzf_real.py` with `tests/test_tui.py`,
  `tests/test_plugin.py`), and the plugin files (manifest, commands, skills,
  hooks) are checked by `tests/test_plugin.py`, which runs their shell
  snippets against this checkout.
- This file is read into context at every session start, so it holds only what
  the code does not show, each fact with the reason the code depends on it, and
  an edit removes what it makes false: a bare rule without its reason gets
  reinterpreted, and a stale fact gets trusted.
- `commands/*.md` and `skills/*/SKILL.md` are followed at run time by whatever
  model the user has, usually a Sonnet, so they are numbered steps, each
  concrete (the exact command, the exact line to quote), with a "Do not" list
  for what a model would otherwise try, and no emphasis or persuasion. A skill's
  `description` is what decides whether it triggers, so a change to one is
  followed by `tests/skills/triggers.sh` (paid, by hand, after asking) and the
  commit message says whether it ran; a body-only change needs no re-test.
- `pins list` and `pins sessions` print column labels (and `pins list` the legend)
  only when stdout is a terminal, so piped output stays bare rows for grep;
  `--json` is the scripting form. Each glyph has one meaning across screens:
  `*` marks a changed field in the editor and details, 🚩/⚑ is keep, and the
  pinned tag on the session lists is 📌/⚲ with the pin's alias.
- A new subcommand needs a row in the README command table, the list in both
  completion scripts, and their check scripts; a hidden one (`_status`, `_rows`)
  needs none of those, since the README check and the completions skip the
  underscore names.
- Plugin commands stay namespaced (`/pins:pin`); do not add bare `/pin` shims.
- ccusage cost is read from `ccusage session --json`: try `--offline` first and
  go online only for models offline cannot price (see `claude_pins/cost.py`).
  The `--id` form reports every cost as 0 and is unusable.
- A change a user would notice gets its bullet under `## Unreleased` at the
  top of `CHANGELOG.md` in the same commit, written for the user, not the
  code. The release only dates that section, so its notes are complete when
  the release is cut rather than reconstructed from the log; after a release
  the section is gone, so the next such change starts it again.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  and nothing else. No session-link trailer: the repository is public.

## Releasing

1. Rename `## Unreleased` in `CHANGELOG.md` to `## X.Y.Z — YYYY-MM-DD`, after
   reading its bullets against `git log` since the last tag for anything a
   user would notice that was left out.
2. Set the version in `.claude-plugin/plugin.json` and `claude_pins/__init__.py`
   with a targeted edit, not a read-then-write one-liner (the test suite checks
   the three agree, and an emptied `__init__.py` has happened before).
3. Run the checks above, commit `Version X.Y.Z`, push, and confirm the push
   landed before tagging. Then `git tag -a vX.Y.Z` with the changelog section as
   the message, push the tag, and `gh release create vX.Y.Z` with the same notes.
4. The marketplace (`~/git/claude-toolbox`) carries no version for this plugin, so
   a release never touches it. Locally: `claude plugin update pins@claude-toolbox`,
   then `/pins:install` again because the symlink points into the versioned
   plugin directory.
