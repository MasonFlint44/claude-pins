# Headless and with no conversation to draw on, the right move is to ask rather than pin blindly.
expect "$(store | jq -r ".pins | length")" 0
expect_out "alias"
