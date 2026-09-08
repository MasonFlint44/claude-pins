---
description: Pin this session so `pin` can find and resume it by name
argument-hint: "[alias [title…]]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/pin:*), AskUserQuestion
---

Pin the current Claude Code session. Session ID: `${CLAUDE_SESSION_ID}`.
Current pin status (from the store): !`"${CLAUDE_PLUGIN_ROOT}/bin/pin" _status "${CLAUDE_SESSION_ID}"`
Arguments given: `$ARGUMENTS`

Rules:

1. If the status line above starts with `pinned`, this session is already pinned under the alias
   in the second column. Say so ("already pinned as X"). If the user gave a *different* alias,
   rename it with `--rename`; if they gave a title, update it with `--title`. Otherwise offer to
   update the title or note and stop. Do not create a second pin.
2. If arguments were given: the first word is the alias (kebab-case: lowercase letters, digits,
   single dashes); the rest is the title. Run the command in step 4 with them.
3. If no arguments were given, draft **one** alias and title from this conversation, then confirm
   with AskUserQuestion before writing:
   - Alias: short kebab-case, 2–4 words, distinctive (like `standup-prep`, `rc-mower`).
   - Title: default to the session's custom title (set with /rename) if there is one, else the
     auto title; only invent a title if neither exists. Keep it under 60 characters.
   - Offer the draft as the first option, "Other" lets them type their own.
4. Run exactly:

   ```
   "${CLAUDE_PLUGIN_ROOT}/bin/pin" add "${CLAUDE_SESSION_ID}" <alias> --title "<title>"
   ```

   (Bash also sees the session ID as `$CLAUDE_CODE_SESSION_ID`; either works.)
5. Report the tool's one-line output verbatim. If it says the alias is taken, use its
   suggestion (`alias-2`) after confirming with the user. Mention that `pin <alias>` (or just
   `pin`) reopens it from a terminal, and that the pin expires with the transcript after
   Claude's retention period unless `keep` is set (`pin edit <alias> --keep`).
6. The output ends with `session named 📌 <alias>`: pinning renames this session, the way
   `/rename` does. Tell the user that the `/resume` picker shows the new name at once, and
   that this session's own prompt box and terminal title catch up within a few turns.
   `/pins:unpin` puts the previous name back. Do not run `/rename` to hurry it along: a name
   set by hand is left alone by `pin` from then on.

Never touch the pin store any other way, and never run `claude` yourself.
