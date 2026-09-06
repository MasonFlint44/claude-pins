# Changelog

Versions follow the `version` field in `.claude-plugin/plugin.json`; Claude
Code offers a plugin update when that field changes. Each version is a git
tag (`v0.2.0`) and a GitHub release with this section as its notes.

## Unreleased

- Tests for the plugin itself: `tests/test_plugin.py` (free: manifest and
  changelog in step, frontmatter, the commands' dynamic-context snippets,
  the install skill's shell steps in a sandbox, the doctor skill's table
  against the doctor's real lines) and `tests/skills/run.sh` (paid, by hand:
  seven headless `claude -p` cases with a stub `claude` on PATH).
- README: updating and removing, a table of the plugin's commands and
  skills, files and environment, the release procedure.
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

Initial release, built from `DESIGN.md`.

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
