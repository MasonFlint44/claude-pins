expect "$(store | jq -r ".pins[0].alias")" standup-prep
expect "$(store | jq -r ".pins[0].title")" "Standup prep for Tuesday"
expect "$(store | jq -r ".pins[0].session_id")" "$SID"
expect_out "pinned as standup-prep"
