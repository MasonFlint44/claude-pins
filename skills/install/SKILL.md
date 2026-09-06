---
name: install
description: Install the `pin` terminal command from the pins plugin — symlink bin/pin into ~/.local/bin, install bash completion, and check fzf (≥ 0.44) and ccusage. Use when the user runs /pins:install, asks to install or set up claude-pins, or says `pin` is not found in their terminal.
allowed-tools: Bash
---

# pins:install

Make the plugin's `pin` command available in the user's terminal. Everything below is
idempotent; run it again after a plugin update.

1. Locate the plugin: `${CLAUDE_PLUGIN_ROOT}` is this plugin's directory. Verify
   `"${CLAUDE_PLUGIN_ROOT}/bin/pin" --version` prints a version.
2. Symlink the command (replace a stale symlink, never a real file):

   ```bash
   mkdir -p ~/.local/bin
   if [ -L ~/.local/bin/pin ] || [ ! -e ~/.local/bin/pin ]; then
     ln -sfn "${CLAUDE_PLUGIN_ROOT}/bin/pin" ~/.local/bin/pin
   else
     echo "~/.local/bin/pin exists and is not a symlink; leaving it alone"
   fi
   ```

   If `~/.local/bin` is not on `PATH` (`case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo missing;; esac`),
   tell the user to add `export PATH="$HOME/.local/bin:$PATH"` to their shell rc.
3. Bash completion: symlink `completions/pin.bash` to
   `${XDG_DATA_HOME:-~/.local/share}/bash-completion/completions/pin` (bash-completion picks it up
   on the next shell). If bash-completion is not installed, append
   `source "${CLAUDE_PLUGIN_ROOT}/completions/pin.bash"` to `~/.bashrc` unless it is already there.
   On zsh, `autoload -U +X bashcompinit && bashcompinit` before sourcing it.
4. Run `"${CLAUDE_PLUGIN_ROOT}/bin/pin" doctor` and quote its lines as printed, all of them,
   rather than summarizing: each line is a check the user may need to act on later. It reports the fzf version
   (0.44 or newer is required for the picker; older or missing falls back to a numbered menu
   and the doctor prints the one-line static-binary install), whether ccusage is installed
   (`npm i -g ccusage`; optional, only for the cost line), the pin store, and the Claude
   projects directory.
5. Finish with the two-line usage reminder: `pin` opens the picker, `/pins:pin` pins the current
   session. Do not run `pin` itself (it is interactive) and never run `claude`.
