# Changelog

Versions follow the `version` field in `.claude-plugin/plugin.json`; Claude
Code offers a plugin update when that field changes. Each version is a git
tag (`v0.3.1`) and a GitHub release with this section as its notes.

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
  0.74.3. It replaces the option-grammar check, which only asked whether
  fzf accepted the options.

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
