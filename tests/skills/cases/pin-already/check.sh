expect "$(store | jq -r ".pins | length")" 1
expect "$(store | jq -r ".pins[0].alias")" already-here
expect_out "already pinned as"
expect_out "already-here"
