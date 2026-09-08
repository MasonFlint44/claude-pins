---
description: Unpin this session (pins undo restores it)
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/pins:*)
---

Unpin the current Claude Code session. Session ID: `${CLAUDE_SESSION_ID}`.
Current pin status: !`"${CLAUDE_PLUGIN_ROOT}/bin/pins" _status "${CLAUDE_SESSION_ID}"`

- If the status is `unpinned`, say "this session isn't pinned" and stop. Nothing to run.
- Otherwise the second column is the alias. Run exactly:

  ```
  "${CLAUDE_PLUGIN_ROOT}/bin/pins" unpin <alias>
  ```

  and report its one-line output verbatim (it mentions `pins undo`, which restores the pin from
  a terminal). No confirmation is needed: unpinning is undoable.
- The output ends with what happened to the session's name: `session named "…" again` means
  the name it had before it was pinned is back, `session name cleared` means it had none. The
  `/resume` picker shows that at once; this session's prompt box and terminal title catch up
  within a few turns. If the line says nothing about the name, the session was renamed by hand
  since it was pinned, and `pins` left that name alone.
