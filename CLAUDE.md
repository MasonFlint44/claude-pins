# claude-pins

Pin Claude Code sessions and reopen them by alias. `bin/pin` is the terminal
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
- `gitBranch` in a worktree session's records is stale (captured before the
  worktree checkout), so the preview reads the branch from git.
- Inside a session the Bash tool sees `CLAUDE_CODE_SESSION_ID`; command templates
  get `${CLAUDE_SESSION_ID}` substituted. Nothing tails history files.
- fzf applies `--nth` to the line *after* `--with-nth` has trimmed it, so a
  hidden field cannot be the search target (this hid every pin from the query
  until 0.4.1). Rows are `id<tab>display` and the query matches the display.
  `--filter` with `--no-sort` prints the trimmed line instead of the whole one,
  which only `tests/test_fzf_real.py` has to work around.
- fzf cannot bind printable characters (they type into the query), which is why
  the help screen's reset keys are ctrl-r and ctrl-alt-r rather than `r` and `R`.
  Its own editing keys are left alone so the filter stays editable, and alt+enter
  is avoided because Windows Terminal takes it.
- `--header-first` draws the header above the prompt, and the status flash takes
  the header's second line (displacing the legend) so the list never moves.
  `--header-lines=1` makes the first input line a sticky column-label row: it is
  cut by `--with-nth` like a row but is not an item, so `start:pos` numbering,
  multi-select and `--filter` output all skip it.
- `--preview-window 'down,55%,<10(hidden)'` hides the pane when it would get
  fewer than ten rows, measured on the pane itself, border rows included, as 55%
  of the terminal rows left after fzf's bottom border (measured on 0.67: 19 rows
  shows, 18 hides; one fewer with the expired footer). fzf re-checks on every
  resize; Python mirrors the arithmetic in `fzf.preview_fits()` at restart for
  the alt-v refusal, and `fzf.header_transform()` carries the same limit into
  the shell that keeps the header note current.
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
  0.64.0); `transform-header` 0.40.0.
  Inside a transform on 0.44 `$FZF_LINES` is empty and `tput lines` answers 24
  whatever the size, while `stty size </dev/tty` is right, which is why the
  header note binds to `focus,change` there and to `resize` on 0.46+.
- The info command runs on every keystroke and the header transform on every
  cursor move (0.44), so both are plain shell over fzf's variables; only a
  reload may start Python (`pin _rows`).
- fzf ends a parenthesised action at the first `)`, and a later `--bind` for a
  trigger replaces the earlier one instead of adding to it: `build_args` merges
  binds per trigger with `+`, and the shell snippets contain no parentheses or
  brackets.
- `--header-lines=1` applies to reloaded input too, the count variables exclude
  the header row, and fzf counts a reload in as it reads it (the counter passes
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
  the screen (`fzf.hold_screen()`) and route messages through headers and
  flashes; the opener queues its banners for the moment the shell is back
  (`launch()` after the restore, or the cancel flash). A prompt run outside a
  hold (`pin prune`, `pin edit`) drops the screen as soon as it ends so the
  command's output is seen.
- Python's `input()` cannot see escape, so the plain prompts (no fzf) cancel
  with ctrl-c; piped stdin bypasses readline, so its pre-fill is only tested in
  a pseudo-terminal. macOS Pythons link readline to libedit, whose pre-input
  hook inserts nothing (seen on CI's 3.10 and 3.12), so `prompt.prefills()`
  falls back to a line saying what enter keeps and that `c` clears (the only
  way to empty a field there), and the pty test skips. Importing readline exports the real terminal's `LINES` and
  `COLUMNS` into the C environment, which `os.execv` passes on but
  `os.environ` does not know about, so the pty tests exec with `os.environ`
  (a picker sized by them would omit the too-short note).
- Terminal automation (opening a new tab for the resumed session) was dropped on
  purpose: Ghostty's D-Bus surface offers new-window only.

## Verify before committing

```
python3 -m unittest -q                 # must exit 0; check the status, not the last line of output
python3 -m unittest -v tests.test_fzf_real   # the real fzf on PATH over every screen's rows; must say "ok", not "skipped"
shellcheck completions/pin.bash tests/completion_check.sh tests/coverage.sh tests/skills/run.sh tests/skills/triggers.sh
bash tests/completion_check.sh         # after touching completions/pin.bash or the subcommand list
docker run --rm -v "$PWD:/repo:ro" zshusers/zsh:5.9 zsh /repo/tests/zsh_completion_check.sh   # no zsh on this machine
CLAUDE_PINS_TEST_FZF=/path/to/fzf python3 -m unittest tests.test_fzf_real   # another fzf build; CI runs 0.44.1 … 0.74.3
bash tests/coverage.sh                 # coverage report; needs uv (dev deps live in pyproject.toml)
```

- Tests never launch the real `claude`: a stub on PATH records argv and cwd, so
  CI spends nothing. `tests/skills/run.sh` runs the skills through `claude -p`
  for real money (about $0.50 on sonnet); only run it by hand. So does
  `tests/skills/triggers.sh`, which scores the skill descriptions' triggering with
  the skill-creator plugin's evaluator; run it after changing a description.
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
  The stub-driven tests are where a hidden-field bug hid for four releases: do
  not judge matching by them.
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
- Coverage only counts when the `bin/pin` subprocesses are traced, which is
  what `tests/coverage.sh` sets up (plain `coverage run` reports about half).
  Paths ending in `os.execv` are saved by the hook in `tests/coverage_hook/`.

## Conventions

- No third-party runtime dependencies. Everything users run is stdlib plus
  optional fzf and ccusage. `pyproject.toml` is dev tooling only (`uv sync
  --group dev` for coverage and pyte); the version there is read from
  `claude_pins/__init__.py`, so a release does not touch it.
- A new subcommand needs a row in the README command table, the list in both
  completion scripts, and their check scripts.
- Plugin commands stay namespaced (`/pins:pin`); do not add bare `/pin` shims.
- ccusage cost is read from `ccusage session --json`: try `--offline` first and
  go online only for models offline cannot price (see `claude_pins/cost.py`).
  The `--id` form reports every cost as 0 and is unusable.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  and nothing else. No session-link trailer: the repository is public.

## Releasing

1. Add a `## X.Y.Z — YYYY-MM-DD` section to `CHANGELOG.md`.
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
