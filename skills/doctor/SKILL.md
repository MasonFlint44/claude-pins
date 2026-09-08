---
name: doctor
description: Diagnose the pins plugin — why the picker's alt keys do nothing on a Mac, why `pin doctor` says fzf is not found, why the cost line says "no price" or "install ccusage", why a pin shows as expired, or whether the pin store and Claude's projects directory are in order. Use when the user says pins are broken, slow, show no cost, lost a pin, asks what a `pin doctor` line means, or asks whether pins are working. A `pin` command that is not found is the install skill's job.
allowed-tools: Bash
---

# Diagnose pins

`pin doctor` checks each dependency in the foreground and prints one line per check. This
skill runs it and turns each line into a fix.

## Steps

1. **Find the command.** Prefer `pin` on PATH; if `command -v pin` fails, use
   `"${CLAUDE_PLUGIN_ROOT}/bin/pin"` for the checks below and offer to run the
   `/pins:install` steps afterwards (the symlink and completion; both are idempotent).
2. **Run `pin doctor`** and show its output verbatim. Lines start with `✓` (fine), `·`
   (works, with a caveat) or `✗` (broken); the exit code is 1 when anything is `✗`.
3. **Explain each non-✓ line and its fix:**

   | Doctor says | What it means | Fix |
   |---|---|---|
   | `· fzf: not found` / `need ≥ 0.44` | fzf is optional: `pin` draws the same screens itself | the line carries the static-binary install command; `brew install fzf` on macOS; nothing to fix if the user is happy without |
   | `· alt keys: X does not send them` / `not sure X sends them` | on a Mac the terminal types symbols on Option, and stock xterm sets the high bit, so the picker's alt keys (alt-i, alt-t …) do nothing until the terminal sends them as Esc+key; the line names the terminal's setting | turn the named setting on (Terminal.app: Profiles › Keyboard › Use Option as Meta key; iTerm2: Profiles › Keys › Left Option key: Esc+; VS Code: `terminal.integrated.macOptionIsMeta`; Ghostty `macos-option-as-alt`; Kitty `macos_option_as_alt`; Alacritty `window.option_as_alt`; xterm `XTerm*metaSendsEscape: true` in `~/.Xresources`, then `xrdb -merge ~/.Xresources`); the keys without alt (ctrl-x palette, f2, ctrl-r, ctrl-t) work regardless |
   | `✗ ccusage: not installed` | the cost line is the only thing that needs it | `npm i -g ccusage@latest`, optional |
   | `· ccusage …: offline table has no price for X; the online fallback prices them` | ccusage's bundled prices predate model X; `pin` goes online once per changed transcript for those sessions | `npm i -g ccusage@latest` once a release adds the price; harmless otherwise |
   | `· ccusage …: no price for X even online` | no price table knows X yet | wait for ccusage; the cost line shows `≥` the priced part |
   | `· ccusage …: online fallback unreachable` | no network from this shell | the offline estimate stays; nothing to fix in pins |
   | `✗ store: … corrupt` | `pins.json` is not valid JSON; a copy was kept at `pins.json.bak` and the original untouched | fix the JSON by hand or move it aside; `pin undo` cannot help here |
   | `✗ projects dir … not found (set CLAUDE_CONFIG_DIR?)` | Claude's transcripts live elsewhere | set `CLAUDE_CONFIG_DIR` to the directory that holds `projects/` |
   | `cleanupPeriodDays N` | Claude deletes transcripts untouched for N days; pins expire with them | expected; `pin edit <alias> --keep` protects a pin, `pin prune` clears expired ones |

   A pin marked ✗ in the picker means its transcript was already deleted by that sweep; nothing
   restores it. ⏳ means it is inside the warn window: opening or `pin touch <alias>` resets the
   clock.
4. **Apply a fix only with the user's agreement**, then rerun `pin doctor` and show the result.

## Do not

- Run `pin` without a subcommand (it is interactive) or run `claude`.
- Edit `pins.json` yourself; every change goes through `pin` subcommands.
