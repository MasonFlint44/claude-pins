# claude-pins

Pin Claude Code sessions so you can find and resume them without remembering a UUID or the
directory they were launched from. A terminal command `pin` with an fzf picker, plus `/pin`
and `/unpin` slash commands shipped as a Claude Code plugin.

![the pin picker: four pins, a preview pane with branch, model, context and cost](docs/preview.svg)

```
$ pin                 # picker: enter opens, ctrl-space actions, alt-n pins a recent session, f1 help
$ pin standup         # one match → cd there and `claude --resume <id>`; several → picker pre-filtered
$ pin rc-mower --fork # one-off fork (new session id, original untouched)
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

2. Run `/pins:pins-install` once. It symlinks `bin/pin` into `~/.local/bin`, installs bash
   completion, and runs `pin doctor`, which checks for **fzf ≥ 0.44** (older or missing falls
   back to a numbered menu) and **ccusage** (optional; only for the cost line). `/pins:pins-doctor`
   runs the same checks later and explains each line.

Python 3.10+ standard library only. Linux and macOS (WSL counts as Linux).

**Updating:** `/plugin update pins` (or auto-update for the marketplace in `/plugin`), then
`/pins:pins-install` again, because the plugin directory moves on each version and the symlink
points into it. **Removing:** `/plugin uninstall pins`, delete `~/.local/bin/pin` and the
completion link; the store and cache below can go too.

### Plugin commands and skills

| | |
|---|---|
| `/pins:pin [alias [title…]]` | pin this session; with no arguments Claude drafts an alias and title from the conversation and confirms |
| `/pins:unpin` | unpin this session; says so if it is not pinned |
| `/pins:pins-install` | symlink, completion, `pin doctor` |
| `/pins:pins-doctor` | run `pin doctor` and explain each line with a fix |

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
  and ✗ once the transcript is gone. Opening touches the transcript; pins with **keep** (⚑) are
  touched on every `pin` run and never expire while you use the tool. `pin prune` unpins the
  expired ones; `pin undo` restores the last unpin or prune.
- The cost line comes from [ccusage](https://github.com/ryoppippi/ccusage): offline first,
  and when its bundled price table has no price for a model the session used, one online run
  with a short timeout. If neither prices the model, the line says so and names the update
  command. Results are cached per transcript change.
- A session whose id appears in a running `claude` process is marked ● and asks before
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
| touch transcript | alt-t | | toggle preview | alt-v |
| toggle keep | alt-k | | help / shortcuts | f1 |
| unpin | alt-x | | multi-select | tab |

Every key is remappable from the f1 screen (enter rebinds, ctrl-r resets a row, ctrl-alt-r
resets all) or by editing `~/.config/claude-pins/keys.toml`. fzf's own query-editing keys and
alt+enter (Windows Terminal) are avoided on purpose. Markers: ● open · ⚑ keep · ⑂ fork ·
⌂ worktree · ⏳ expiring · ✗ expired.

## Subcommands

```
pin add <session-id> <alias> [--title …] [--note …] [--keep] [--fork] [--worktree]
pin list [--all] [--sort recency|alias|pinned] [--json]
pin edit <alias> [--title … --note … --cwd … --rename … --model … --effort … --permission-mode …
                  --keep/--no-keep --fork/--no-fork --worktree/--no-worktree]   # no flags → editor
pin open <alias> [--fork] [--resume] [-w [name]]
pin rm|unpin <alias> · pin undo · pin prune [-y] · pin touch <alias> · pin doctor
```

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
| `NO_COLOR` / `CLAUDE_PINS_COLOR=1` | never / always color |
| `CLAUDE_PINS_FZF`, `CLAUDE_PINS_CCUSAGE` | alternate binaries |
| `CLAUDE_PINS_PS`, `CLAUDE_PINS_NOW` | test hooks: a fake process table file, a fake clock (epoch seconds) |

## Development

```
python3 -m unittest            # store, reader, expiry, git worktrees, opener prompts, picker flows
bash tests/completion_check.sh # bash completion (CI also runs it on bash 3.2 / 4.4 / 5.2 images, plus shellcheck)
python3 tests/fzf_grammar_check.py [fzf-binary]   # every option the picker uses, against fzf 0.44.1 in CI
pip install pyte && python3 docs/preview.py       # regenerate the README preview
```

Tests never run the real `claude`: a stub on `PATH` records the argv and cwd it was launched
with, and a scripted stand-in for fzf drives the picker. Zero Claude usage in CI.
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

**Releasing:** add a `CHANGELOG.md` section, bump `version` in `.claude-plugin/plugin.json`
and `claude_pins/__init__.py` (a test keeps the three in step), commit `Version X.Y.Z`, tag
`vX.Y.Z` with the section as its message, `gh release create` with the same notes. The
marketplace needs no change: its entries carry no version.

The design, including the verified facts about Claude Code's retention sweep, transcript
records, and the `--resume`/`--fork-session`/`--worktree` spike, is in [DESIGN.md](DESIGN.md).

MIT — see `LICENSE`. Version history in `CHANGELOG.md`.
