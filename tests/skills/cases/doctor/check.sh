expect_out "ccusage"
case "$OUT" in *"npm i -g ccusage"*|*"update ccusage"*|*"offline"*) ;; *) echo "    no ccusage explanation"; fail=$((fail + 1)) ;; esac
expect "$(store | jq -r ".pins | length")" 0
