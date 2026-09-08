---
name: install
description: Install the `pins` terminal command from the pins plugin — symlink bin/pins into ~/.local/bin, install bash or zsh completion, and check fzf (≥ 0.44) and ccusage. Use when the user runs /pins:install, asks to install or set up claude-pins, or says `pins` is not found in their terminal.
allowed-tools: Bash
---

# pins:install

Make the plugin's `pins` command available in the user's terminal. Everything below is
idempotent; run it again after a plugin update.

1. Locate the plugin: `${CLAUDE_PLUGIN_ROOT}` is this plugin's directory. Verify
   `"${CLAUDE_PLUGIN_ROOT}/bin/pins" --version` prints a version.
2. Symlink the command (replace a stale symlink, never a real file):

   ```bash
   mkdir -p ~/.local/bin
   if [ -L ~/.local/bin/pins ] || [ ! -e ~/.local/bin/pins ]; then
     ln -sfn "${CLAUDE_PLUGIN_ROOT}/bin/pins" ~/.local/bin/pins
   else
     echo "~/.local/bin/pins exists and is not a symlink; leaving it alone"
   fi
   ```

   If `~/.local/bin` is not on `PATH` (`case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo missing;; esac`),
   tell the user to add `export PATH="$HOME/.local/bin:$PATH"` to their shell rc.
3. Completion for the user's shell (`basename "$SHELL"`):
   - bash: symlink `completions/pins.bash` to
     `${XDG_DATA_HOME:-~/.local/share}/bash-completion/completions/pins` (bash-completion picks it
     up on the next shell). If bash-completion is not installed, append
     `source "${CLAUDE_PLUGIN_ROOT}/completions/pins.bash"` to `~/.bashrc` unless it is already there.
   - zsh: symlink `completions/pins.zsh` to `~/.zsh/completions/_pins`, and unless `~/.zshrc`
     already puts that directory on `fpath` before `compinit`, tell the user to add
     `fpath=(~/.zsh/completions $fpath)` above their `compinit` line. Do not edit `.zshrc`.

     ```bash
     mkdir -p ~/.zsh/completions
     ln -sfn "${CLAUDE_PLUGIN_ROOT}/completions/pins.zsh" ~/.zsh/completions/_pins
     ```
4. Run `"${CLAUDE_PLUGIN_ROOT}/bin/pins" doctor` and quote its lines as printed, all of them,
   rather than summarizing: each line is a check the user may need to act on later. It reports the fzf version
   (optional: without it a built-in picker draws the same screens, and the doctor prints the
   one-line static-binary install), on a Mac whether the terminal sends Option as Meta (the alt
   keys need it; the line names the terminal's setting), whether ccusage is installed
   (`npm i -g ccusage`; optional, only for the cost line), the pin store, whether the plugin's
   session-start hook runs (it ships with the plugin and needs no install step; a `·` keep
   line means the plugin is not enabled, and its fix is `/plugin install pins@claude-toolbox`,
   then a restart of Claude), and the Claude projects directory.
5. Finish with the two-line usage reminder: `pins` opens the picker, `/pins:pin` pins the current
   session. Do not run `pins` itself (it is interactive) and never run `claude`.
