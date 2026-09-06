# claude-pins — design

Status: designed 2026-09-06 (revision 2, same day), not built. Every decision below was
made by Mason in the planning session; the mockups are the ones he picked. Build from this
document.

## 1. Goal

Pin Claude Code sessions so they can be found and resumed without remembering a UUID or
the directory they were launched from. A terminal command `pin` with an fzf picker, plus
`/pin` and `/unpin` slash commands shipped as a plugin.

Workflow: open Ghostty, ctrl+shift+t for a tab, type `pin`, pick, repeat. No terminal
automation (Ghostty's D-Bus surface has new-window only, no new-tab; dropped on purpose).

## 2. Verified facts (from the 2.1.263 binary and disk — do not re-derive)

- **Retention sweep** runs at Claude Code startup. Every file under `~/.claude/projects/*/`
  whose **mtime** is older than `cleanupPeriodDays` (default 30, minimum 1, 0 rejected by
  the schema) is deleted. When a session `.jsonl` goes, its `<id>/` directory (subagents,
  tool-results) and file-history go with it. `ccr-tip.json` / `precompact.json` sidecars are
  swept on their own mtime; they are compaction caches and resume works without them.
  The only exemption is for Claude Desktop/Cowork sessions. Session names do not protect.
  **Touching the `.jsonl` is the whole protection.**
- Mason keeps `cleanupPeriodDays` at 30. Pins expiring after 30 untouched days is desired.
- **Session identity inside a session:** Bash tool commands see `CLAUDE_CODE_SESSION_ID`;
  command templates get `${CLAUDE_SESSION_ID}` substituted. No history.jsonl tailing.
- **Transcript records available:** `ai-title` (auto title), `custom-title` (/rename),
  `last-prompt`, `permission-mode`, `mode`, per-assistant `effort`, `message.model`,
  `message.usage` (cache_read + cache_creation + input of the last assistant message =
  live context size), `gitBranch`, `cwd`, `version`, timestamps.
- `claude --resume <id>` searches the current project first, then every other project
  directory on the machine. The resumed session runs in whatever cwd you launch from.
- `--fork-session` resumes into a new session ID. `-w/--worktree [name]` creates
  `<repo>/.claude/worktrees/<name>` on a new branch; unchanged worktrees are removed on exit.
- **ccusage** (v20 here): `ccusage session --id <sid> --json --offline` returns
  `totalCost`/`totalTokens` in ~0.2 s. `--offline` uses the bundled price table (no network
  in a preview pane); a model newer than the install prices at $0.
- `awaySummaryEnabled` unset = on (recap after 5+ min away, runs only while the prompt
  cache is warm). Mason keeps it on.
- **Remote Control** sessions are ordinary transcripts under `~/.claude/projects/-home-mason/`
  and resume locally. The 04:00 `claude-rc` restart orphans them from the phone (not
  re-queued); they stay resumable locally, which makes them good pin candidates.
  Re-serving to the phone (`claude rc --session-id`) conflicts with the always-on service —
  the tool always opens RC pins as plain local resumes.
- `pin` collides with nothing on PATH or in apt.
- **fzf baseline = 0.44** (Ubuntu 24.04 apt; Mason has 0.67). Needed for `change-header`
  status flashes, `--header-first`, `become`. Older distros: `pin doctor` prints the
  one-line static-binary install. fzf key names: https://github.com/junegunn/fzf (man page
  "KEY/EVENT BINDINGS").
- **Key constraints:** printable characters cannot be bound (they type into the query) →
  no `?` binding. fzf's own query-editing keys (ctrl-a/b/d/e/f/h/j/k/n/p/u/w/y, alt-b/d/f)
  are left alone so the filter stays editable. Windows Terminal takes alt+enter
  (fullscreen) → never used. Plain Python `input()` cannot see esc → inline prompts cancel
  with ctrl-c.
- Work laptop = Windows 11 + WSL Ubuntu → plain Linux code path. macOS support is for
  other users.

### 2.1 Spike findings (2026-09-06, step 9.1 — verified live against 2.1.263 and fzf 0.44.1)

- `claude --resume <id> --fork-session --worktree <name>` works from a foreign cwd: the
  session was found in another project directory, a new session ID was issued, and the
  worktree was created before the first turn. The fork's transcript lands under a **new**
  projects directory keyed on the worktree path, and the copied history records have their
  `cwd` rewritten to the new cwd (so "last record's cwd" is always the effective cwd).
- A fork **does not touch the original transcript** (mtime unchanged) — the tool must.
- **Worktree branch is `worktree-<name>`, not `<name>`.** Tier 2 of the missing-directory
  logic must look for `worktree-<name>` (and accept a bare `<name>` branch as a fallback).
- The `gitBranch` field in a worktree session's records is **stale** (captured before the
  worktree checkout — it said `main` while the session ran on `worktree-spikewt`). Read the
  branch from git, not from the transcript, when the pin is a worktree.
- Claude **locks** its worktree (`git worktree lock`, reason `claude session <name> (pid P
  start S)`) and in `-p` mode left it behind on exit, still locked. A missing-but-locked
  worktree makes `git worktree add` refuse. Recreation recipe:
  `git worktree unlock <path>` (ignore failure) → `git worktree prune` →
  `git worktree add <path> worktree-<name>`. If the branch is gone: `add -b`.
- fzf 0.44.1 accepts everything §6 uses: `change-header`, `transform-header`,
  `--header-first`, `become`, `--info=inline-right`, `--border-label[-pos]`,
  `--preview-label`, `pos()`, `start`/`load`/`focus` events, `unbind`/`rebind`, and every
  key in §6.1. **Not** in 0.44: `transform`, `--footer`, `change-header-label`, `print`,
  `exclude`, the `result` event, `alt-shift-*` keys. The "N expired" footer therefore uses
  the bottom border label (`--border=bottom --border-label-pos=2:bottom`), not `--footer`.
- In `--layout=reverse` the header renders on the line **below** the prompt (the status
  flash position in §6.2) without `--header-first`; `--header-first` moves it above.
- Effort is a top-level `effort` key on assistant records; `permission-mode` records carry
  `permissionMode`; `ai-title` → `aiTitle`; `last-prompt` → `lastPrompt`.
- Cost of the spike: two haiku `-p` turns, ~$0.04. All spike transcripts and worktrees removed.

## 3. Repo

Public, MIT, `claude-pins`. Python 3 stdlib only. Commit rule as in the statusline repo:
keep Co-Authored-By, **no Claude-Session trailer**, no work content.

```
bin/pin                      # entry point (thin CLI over the package)
claude_pins/                 # store, transcript reader, discovery, open paths, picker, editor
completions/pin.bash
.claude-plugin/plugin.json   # name: pins
commands/pin.md
commands/unpin.md
skills/pins-install/SKILL.md
tests/
.github/workflows/ci.yml     # ubuntu + macos for Python; bash 3.2/4.x Docker for completion
README.md                    # with a preview capture
DESIGN.md                    # this file
```

Distribution: marketplace repo `claude-statuslines` renamed **`claude-toolbox`**, `pins`
entry added. Laptop does a one-time marketplace remove/re-add. Slash commands call
`${CLAUDE_PLUGIN_ROOT}/bin/pin` so they work before the PATH symlink exists;
`/pins-install` symlinks `bin/pin` into `~/.local/bin`, installs completions, checks fzf
(version ≥ 0.44) and ccusage. Command names stay `/pin` and `/unpin` (exempt from the
plugin-prefix rule).

## 4. Store

`~/.local/state/claude-pins/pins.json` (honors `XDG_STATE_HOME`; override with
`CLAUDE_PINS_FILE`). Local per machine, never synced. Atomic writes, corrupt-file recovery
(keep a `.bak`, refuse to clobber, tell the user).

Per pin: `alias` (kebab, unique), `title` (free text), `session_id` (unique), `cwd`,
`transcript` (path), `note`, `pinned_at`, `fork` (bool), `worktree` (bool), `keep` (bool),
`launch` (`model`, `effort`, `permission_mode` → claude flags).

Tombstones: the last unpin or prune is kept in the store (`undo` list, capped) so
`pin undo` restores it.

Keymap: `~/.config/claude-pins/keys.toml`-style file with defaults; the palette and help
read labels from the same table so remaps show everywhere.

Env: `CLAUDE_PINS_FILE`, `CLAUDE_PINS_SORT` (default recency), `CLAUDE_PINS_NO_FZF`,
`CLAUDE_PINS_EXPIRE_WARN` (days, default 7), `NO_COLOR` respected, `CLAUDE_CONFIG_DIR`
honored for locating transcripts.

## 5. Behavior

- **Vocabulary.** *Expired* = transcript aged out (✗). *Expiring* = within the warn window
  (⏳). *Missing directory* = cwd gone (separate condition, pre-launch prompt). *Unpin* =
  remove a pin (never "delete"/"remove"; matches `/unpin`; `pin rm` and `pin unpin` are
  aliases).
- **Matching.** `pin` → picker. `pin <words…>` → loose match over alias and title;
  exactly one hit opens, otherwise the picker opens pre-filtered. Bash completion over aliases.
- **Open.** `cd <cwd> && claude --resume <id> [launch flags]`. `--fork` adds
  `--fork-session`; `-w [name]` adds `--worktree`. Per-pin `fork`/`worktree` set the default;
  `--resume`/`--fork`/`-w` are one-off overrides.
- **Touching.** Opening touches the transcript (only matters for forks, which write a new
  file). Pins with `keep` are touched on **every** `pin` run → never expire while the tool
  is used. Everything else expires at 30 days, by design.
- **Already open.** Session IDs found in running `claude` process args are marked ●. The
  tool cannot focus another Ghostty tab, so enter on one says "already open in another
  tab" and offers *resume anyway* / *cancel*.
- **Missing-directory tiers.** (1) exists → silent. (2) was `<repo>/.claude/worktrees/<name>`
  and repo exists: branch `<name>` survives → recreate the worktree on it; else offer a fresh
  worktree off HEAD or the repo root. (3) otherwise offer `~` ("session context won't match
  this directory"), choose a directory, or unpin. Tiers 2–3 use the pre-launch prompt
  (§6.7); every fallback prints a banner and offers to update the pin's cwd.
- **Branch.** Preview shows recorded vs current branch. **Never auto-switch.** On mismatch
  the banner says so; checkout is offered as a prompt option only when the tree is clean.
  Worktree-mode pins sidestep this.
- **Idempotency.** Alias and session ID both unique. Pin an already-pinned session →
  "already pinned as X", offer title/note update, or rename if a different alias was given.
  Alias taken by another session → refuse, show it, suggest `alias-2`. Unpin a missing pin →
  "no pin named x", exit 1, nothing changed. Prune with nothing expired → "nothing to prune".
- **Undo over confirm.** Single unpin needs no confirmation; `pin undo` (and the palette's
  Undo) restores the last unpin or prune. Prune still confirms because it is plural.
- **Expired pins** hidden unless `--all` / alt-a; footer "N expired · pin prune". ⏳ shows
  at ≤ `CLAUDE_PINS_EXPIRE_WARN` days (default 7) before expiry, derived from the configured
  `cleanupPeriodDays`, so it stays right if changed.
- **Empty state.** No pins → the picker shows a message instead of a blank list: "No pins
  yet. alt-n pins a recent session, or run /pin inside a Claude session."
- **Feedback.** Every in-picker action flashes a status line under the prompt
  ("✓ touched standup-prep", "✓ pinned as tax-prep", "✓ unpinned rc-mower · alt-z undo"),
  cleared on the next keypress or reload.
- **Cost.** ccusage offline, cached per pin keyed on transcript mtime. $0 on a non-empty
  session → "pricing unavailable for <model> · update ccusage". Not installed → "cost:
  install ccusage for session cost · npm i -g ccusage". No price table of our own in v1.
- **Rows.** Column widths computed from terminal width; title is the flexible column and
  truncates with `…`. Directory shown with `~`; worktree paths abbreviated to
  `~/git/foo ⌂x`. Age = `2h` / `2d` / `26d` (hours under a day, then days). Colour on top of
  symbols: green ●, yellow ⏳ and its age, dim age otherwise, strikethrough + dim for ✗ rows.
  Symbols carry the meaning alone under `NO_COLOR`.
- **Sort.** Recency (transcript mtime) by default; alt-s cycles recency → alias → pinned
  order; `--sort` and `CLAUDE_PINS_SORT` set the start.
- **Subcommands:** `add <session-id> <alias> [--title …]`, `list [--all] [--sort …]`,
  `edit <alias> [--title … --model … --effort … --permission-mode … --keep/--no-keep …]`,
  `rm|unpin <alias>`, `undo`, `prune`, `touch <alias>`, `doctor` (fzf version ≥ 0.44 with
  install line, ccusage presence + online price check with timeout, store health, projects
  dir, cleanup period). `--help` at every level.
- **Slash commands.** `/pin [alias [title…]]` — bare `/pin` has Claude draft alias+title
  from the conversation and confirm via AskUserQuestion; default title = custom title, else
  AI title. `/unpin` — says so if not pinned. Only these two; list/prune/etc. are terminal-only.
- **Safety.** The tool never runs the real `claude` except to open. Tests use a stub
  `claude` on PATH that records its argv. Zero usage in CI.

## 6. Screens (Mason's picks, verbatim)

### 6.0 Shared anatomy

Every screen: **breadcrumb** at the left of the prompt line (`pins`, `pins › standup-prep ›
actions`, `pins › standup-prep › edit`, `pins › new`, `pins › help`), **key hints** at the
right of the same line, optional **status flash** on the next line, then the list, then the
preview. **Esc always goes back exactly one level.** Ellipsis on an action (`Prune…`) means
it asks something first.

### 6.1 Default keymap (all remappable)

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

Avoided on purpose: every ctrl key fzf uses for query editing, alt-b/alt-d/alt-f (word
ops), alt+enter (Windows Terminal), printable characters.

### 6.2 Main picker — stacked, symbol markers at the right end

```
 pins › █                       enter open · ctrl-space actions · alt-n new · f1 help
 ✓ touched standup-prep
> standup-prep   Standup prep              ~/git/dotclaude         2d  ● ⚑
  cc-collector   Command center collector  ~/git/command-center    9d
  rc-mower       Navimow schedule debug    ~                      26d  ⏳
  insurance      USAA restructure          ~/git/foo ⌂x            1d  ⑂ ⌂
 ┌ standup-prep ─────────────────────────────────────────────────────────┐
 │ Standup prep                                                          │
 │ Tuesday standup, uses jira-cards                                      │
 │                                                                       │
 │ dir       ~/git/dotclaude                                             │
 │ branch    main  (session: main)                                       │
 │ model     fable-5-1 · effort high · mode auto                         │
 │ context   ~121k (60%) · 85 msgs · 4.8M tokens                         │
 │ cost      est $0.07 (ccusage)                                         │
 │ created   2026-09-06 · last 2d · pinned 2026-09-06 · expires 28d      │
 │                                                                       │
 │ you       so the pin command should also…                             │
 │ claude    Yes. The picker can bind keys to run a command…             │
 └───────────────────────────────────────────────────────────────────────┘
 2 expired · alt-a show · pin prune
```

Markers: ● open · ⚑ keep · ⑂ fork · ⌂ worktree · ⏳ expiring · ✗ expired (shown only with
alt-a, struck through). Legend lives on the f1 screen. Tab marks rows for palette actions.
The status flash line is absent until an action runs. Empty state replaces the list with
the message in §5.

### 6.3 Actions palette — grouped, context-sensitive, shows state

```
 pins › standup-prep › actions █                              enter run · esc back
  ── open ──
> Open                          enter
  Open as fork                  alt-o
  Open in new worktree          alt-w
  ── pin ──
  Edit…                         alt-e
  Touch transcript              alt-t
  Toggle keep (on)              alt-k
  Toggle fork mode (off)
  Toggle worktree mode (off)
  Unpin                         alt-x
  ── list ──
  New pin…                      alt-n
  Show expired (2)              alt-a
  Prune expired…                alt-p
  Undo last unpin               alt-z
  Sort: recency                 alt-s
  ── tool ──
  Help & shortcuts              f1
```

Hide what doesn't apply: expired pin → no open/touch; open pin → Open becomes "Resume
anyway (open in another tab)". With rows marked (tab), the title reads
`pins › 3 selected › actions` and touch / unpin / toggles apply to all.

### 6.4 Help & shortcuts — one screen, legend on top, enter rebinds

```
 pins › help █                                     enter rebind · r reset · esc back
  ● open  ⚑ keep  ⑂ fork  ⌂ worktree  ⏳ expiring  ✗ expired      keymap: ~/.config/claude-pins/keys.toml
  ── open ──
> Open                          enter
  Open as fork                  alt-o
  Open in new worktree          alt-w
  ── pin ──
  Edit…                         alt-e
  …
  new key for "Touch transcript" (e.g. alt-t, f5): █      conflicts: none
```

Validate against fzf's key-name grammar; show conflicts (including fzf's own editing keys,
as a warning not a block); `r` resets one row, `R` in the hints resets all. Persist to the
keymap file.

### 6.5 Editor — sectioned field picker with hints

```
 pins › standup-prep › edit (unsaved) █             enter change · alt-s save · esc back
  ── identity ──
> title *     Standup prep (Tue)
  alias       standup-prep
  note        Tuesday standup, uses jira-cards
  ── location ──
  cwd         ~/git/dotclaude
  worktree    off             open in a fresh worktree each time
  ── launch ──
  model       (default)       claude's default model
  effort      (default)       low · medium · high
  permission  auto            default · acceptEdits · plan · auto · bypass
  ── retention ──
  keep        ON              touched every run, never expires
  fork        off             open as a copy, original untouched
  ──
  Done                        Cancel
```

Text field → one-line prompt pre-filled with the current value (readline insert):

```
title: Standup prep▌                    enter save · ctrl-c cancel
```

Boolean → flips and returns. Choice field → short fzf list with `(clear)`:

```
 pins › standup-prep › edit › permission █
> auto
  default
  acceptEdits
  plan
  bypassPermissions
  (clear)
```

`cwd` gets tab completion. Changed fields get `*`; breadcrumb shows `(unsaved)`. Save via
alt-s or Done. **Esc on a dirty form asks `save changes? [Y/n/c]`**; esc on a clean form
just goes back. Nothing is written until save.

### 6.6 New pin (alt-n) — session list, newest first

```
 pins › new █                                          enter pin · esc back
> Pin Claude sessions           ~                       2h   85 msgs
  RC keeper port watchdog       ~                       1d   40 msgs  ⚑ pinned
  Budget statusline v2.2.4      ~/git/claude-budget-st… 1d  212 msgs
  Mowmap heat lens              ~/git/mowmap            6d   31 msgs
  Tax prep questions            ~/Documents            12d    9 msgs
 ┌ Pin Claude sessions ──────────────────────────────────────────────────┐
 │ model     fable-5-1 · effort high · mode auto                         │
 │ context   ~121k (60%) · 85 msgs                                       │
 │ you       so the pin command should also…                             │
 │ claude    Yes. The picker can bind keys to run a command…             │
 └───────────────────────────────────────────────────────────────────────┘

 alias: pin-claude-sessions█               (suggested from title · enter · ctrl-c cancel)
```

Enumerates every `~/.claude/projects/*/<uuid>.jsonl`; title = custom title else AI title
else first prompt truncated. Already-pinned rows marked and skipped on enter. **Only the
alias is asked**; title defaults to the session title (one alt-e away). On enter the main
picker returns with the new pin highlighted and `✓ pinned as pin-claude-sessions` flashed.

### 6.7 Pre-launch prompt — inline numbered, best first

```
 standup-prep: directory ~/git/foo/.claude/worktrees/x is missing
 branch x still exists in ~/git/foo

  1) recreate the worktree on branch x        (enter)
  2) open in the repo root ~/git/foo
  3) open in ~ (session context won't match this directory)
  4) choose another directory
  5) unpin

 choice [1]: █

 → recreated ~/git/foo/.claude/worktrees/x on x · update pin cwd? [Y/n]
```

Plain text, no fzf, so it also serves `pin <alias>` and the menu fallback. Same shape for
branch mismatch (checkout option present only on a clean tree) and for already-open
(resume anyway / cancel).

### 6.8 No-fzf menu fallback — numbered rows, letter-prefixed actions

```
 pins                                (install fzf ≥ 0.44 for the full picker: pin doctor)
  1  standup-prep   Standup prep              ~/git/dotclaude         2d  ● ⚑
  2  cc-collector   Command center collector  ~/git/command-center    9d
  3  rc-mower       Navimow schedule debug    ~                      26d  ⏳
  4  insurance      USAA restructure          ~/git/foo ⌂x            1d  ⑂ ⌂

  N open · oN fork · wN worktree · tN touch · eN edit · xN unpin · pN preview
  n new · a show expired · p prune · z undo · ? help · q quit
 > █
```

Parser rule: **a bare letter is a list action, letter+number is a row action** (`p` prunes,
`p3` previews row 3). No shifted letters. `?` is fine here because this is a plain prompt,
not fzf.

## 7. Preview pane content

Read only the first and last 64 KB of the transcript; cache by mtime. Layout as in §6.2:
title line, note line, blank, labelled block (dir · branch with recorded vs current ·
model/effort/mode · context tokens with % when the model's window is known, message count,
total tokens · cost line per §5 · created/last/pinned/expires), blank, last user prompt
(`last-prompt` record) and last assistant text block, a few lines each. Labels are a fixed
9-character column so values align.

## 8. Tests (written alongside each module)

Store CRUD, atomic write, corrupt recovery, tombstones/undo · matching (exact / unique
loose / ambiguous) · expiry math under a fake clock incl. non-default cleanup period and
warn window · missing-dir tiers on temp git repos with real worktrees (exists / branch
survives / branch gone / repo gone) · branch comparison on dirty vs clean trees ·
already-open detection vs a mocked process table · transcript reader on synthetic,
truncated, malformed, huge files · palette filtering per pin state and multi-select ·
field-picker flows with scripted input incl. dirty-esc · rendering goldens (rows at several
widths, preview, palette, help, menu, empty state, status flashes) · keymap validation +
conflicts + reset · completion output · ccusage parsing vs a stub incl. the $0 case and
not-installed · end-to-end `pin` runs against a stub `claude` recording argv · `NO_COLOR`
output. CI: ubuntu-latest + macos-latest; completion script on the bash 3.2/4.x Docker
images already used by claude-budget-statusline.

## 9. Build order

1. **Verification spike (first):** `--resume` + `--fork-session` + `--worktree` together;
   worktree recreation on a surviving branch; resume-by-ID from a foreign cwd; confirm
   fzf 0.44 has everything §6 needs (`change-header`, `--header-first`, `become`).
2. Core model + store (incl. tombstones).
3. Transcript reader.
4. Session discovery + already-open detection.
5. Open paths (matching, resume/fork/worktree, missing-dir tiers, branch check, touching).
6. Picker (rows, preview, palette, multi-select, status flashes, keymap, sort, empty state,
   menu fallback).
7. Editor + help/shortcuts screen with rebinding.
8. ccusage integration.
9. Subcommands (incl. undo) + help.
10. Plugin: `/pin`, `/unpin`, `/pins-install`.
11. Repo hygiene, README with preview capture, CI.
12. Distribution: rename marketplace → `claude-toolbox`, add `pins`, re-add on laptop,
    `/pins-install` on both machines.

## 10. Follow-up (separate session, unrelated code)

Make the 04:00 `claude-rc` restart conditional: compare the version the `claude` symlink
resolves to against the running process's executable; restart only when they differ.
