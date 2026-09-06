link="$HOME/.local/bin/pin"
[ -L "$link" ] || { echo "    ~/.local/bin/pin is not a symlink"; fail=$((fail + 1)); }
expect "$(readlink -f "$link")" "$REPO/bin/pin"
expect_out "doctor"
case "$OUT" in *"cleanupPeriodDays"*|*"fzf"*) ;; *) echo "    doctor output never reached the user"; fail=$((fail + 1)) ;; esac
