case "$OUT" in *"isn't pinned"*|*"is not pinned"*|*"not pinned"*) ;; *) echo "    output never says the session is not pinned"; fail=$((fail + 1)) ;; esac
expect "$(store | jq -r ".undo | length")" 0
