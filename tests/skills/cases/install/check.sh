link="$HOME/.local/bin/pins"
[ -L "$link" ] || { echo "    ~/.local/bin/pins is not a symlink"; fail=$((fail + 1)); }
expect "$(readlink -f "$link")" "$REPO/bin/pins"
case "$OUT" in *doctor*|*Doctor*) ;; *) echo "    output never mentions the doctor"; fail=$((fail + 1)) ;; esac
case "$OUT" in *"cleanupPeriodDays"*|*"fzf"*) ;; *) echo "    doctor output never reached the user"; fail=$((fail + 1)) ;; esac
