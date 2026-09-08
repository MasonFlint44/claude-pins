expect "$(store | jq -r ".pins | length")" 0
expect "$(store | jq -r ".undo[0].pins[0].alias")" going-away
expect_out "unpinned going-away"
expect_out "pins undo"
