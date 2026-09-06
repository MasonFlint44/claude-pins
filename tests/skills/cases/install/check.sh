link="$HOME/.local/bin/pin"
[ -L "$link" ] || { echo "    ~/.local/bin/pin is not a symlink"; fail=$((fail + 1)); }
expect "$(readlink -f "$link")" "$REPO/bin/pin"
case "$OUT" in *doctor*|*Doctor*) ;; *) echo "    output never mentions the doctor"; fail=$((fail + 1)) ;; esac
case "$OUT" in *"cleanupPeriodDays"*|*"fzf"*) ;; *) echo "    doctor output never reached the user"; fail=$((fail + 1)) ;; esac
