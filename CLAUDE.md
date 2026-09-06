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
- fzf cannot bind printable characters (they type into the query), which is why
  the help screen's reset keys are ctrl-r and ctrl-alt-r rather than `r` and `R`.
  Its own editing keys are left alone so the filter stays editable, and alt+enter
  is avoided because Windows Terminal takes it. In `--layout=reverse` the header
  renders on the line below the prompt, which is where the status flash goes.
- Python's `input()` cannot see escape, so the inline prompts cancel with ctrl-c.
- Terminal automation (opening a new tab for the resumed session) was dropped on
  purpose: Ghostty's D-Bus surface offers new-window only.

## Verify before committing

```
python3 -m unittest -q                 # must exit 0; check the status, not the last line of output
shellcheck completions/pin.bash tests/completion_check.sh tests/skills/run.sh tests/skills/triggers.sh
python3 tests/fzf_grammar_check.py     # after touching any fzf option in claude_pins/fzf.py or picker.py
```

- Tests never launch the real `claude` or the real `fzf`: a stub on PATH records
  argv and cwd, and `tests/fzf_stub.py` plays a scripted picker. Keep it that
  way, so CI spends nothing. `tests/skills/run.sh` runs the skills through
  `claude -p` for real money (about $0.50 on sonnet); only run it by hand. So does
  `tests/skills/triggers.sh`, which scores the skill descriptions' triggering with
  the skill-creator plugin's evaluator; run it after changing a description.
- fzf support floors at 0.44.1, which lacks `transform`, `--footer`, `print`,
  `exclude` and the `result` event. The grammar check runs the picker's option
  set against that binary in CI; run it locally when you add an option.
- `tests/test_plugin.py` reads the README: the Keys table must equal the keymap
  defaults, every subcommand must appear in the command reference, and every
  `CLAUDE_PINS_*` variable except `CLAUDE_PINS_EXE` must be in the environment
  table. A new key, subcommand or knob needs its README row in the same change.
- The picker's screenshot is generated: run `python3 docs/preview.py` (needs
  `pyte`, the only dev dependency) after changing the picker's look, and commit
  `docs/preview.svg` and `docs/preview.txt` with the change.

## Conventions

- No third-party runtime dependencies. Everything users run is stdlib plus
  optional fzf and ccusage.
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
   then `/pins:pins-install` again because the symlink points into the versioned
   plugin directory.
