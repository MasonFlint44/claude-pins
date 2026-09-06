---
description: Unpin this session (pin undo restores it)
allowed-tools: Bash(pin:*)
---

Unpin the current Claude Code session. Session ID: `${CLAUDE_SESSION_ID}`.
Current pin status: !`pin _status "${CLAUDE_SESSION_ID}"`

- If the status is `unpinned`, say "this session isn't pinned" and stop. Nothing to run.
- Otherwise the second column is the alias. Run exactly:

  ```
  pin unpin <alias>
  ```

  and report its one-line output verbatim (it mentions `pin undo`, which restores the pin from
  a terminal). No confirmation is needed: unpinning is undoable.

<!-- claude-pins shim: generated from commands/unpin.md by tests/shims_check.py; installed by /pins:pins-install -->
