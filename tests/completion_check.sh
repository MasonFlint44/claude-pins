#!/usr/bin/env bash
# Runs on bash 3.2/4.x/5.x images (no python there): source the completion and drive it with a stub `pin`.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
cat > "$tmp/pin" <<'STUB'
#!/bin/sh
[ "$1" = "_complete" ] && printf 'standup-prep\ncc-collector\nrc-mower\n'
STUB
chmod +x "$tmp/pin"
PATH="$tmp:$PATH"
# shellcheck disable=SC1090
source "$here/completions/pin.bash"

check() {  # check "<command line>" "<expected space-separated>"
    read -r -a COMP_WORDS <<< "$1"; COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))
    case "$1" in *" ") COMP_WORDS+=(""); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ));; esac
    COMPREPLY=()
    _pin_complete
    got="${COMPREPLY[*]-}"
    if [ "$got" != "$2" ]; then echo "FAIL: '$1' → '$got' (want '$2')"; exit 1; fi
    echo "ok: '$1' → '$got'"
}
check "pin st" "standup-prep"
check "pin rm " "standup-prep cc-collector rc-mower"
check "pin touch rc" "rc-mower"
check "pin --so" "--sort"
check "pin --sort " "recency alias pinned"
check "pin ad" "add"
check "pin open --f" "--fork"
check "pin rename " "standup-prep cc-collector rc-mower"
echo "completion ok on bash $BASH_VERSION"
