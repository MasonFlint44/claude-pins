#!/usr/bin/env bash
# Runs on bash 3.2/4.x/5.x images (no python there): source the completion and drive it with a stub `pins`.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
cat > "$tmp/pins" <<'STUB'
#!/bin/sh
[ "$1" = "_complete" ] && printf 'standup-prep\ncc-collector\nrc-mower\n'
STUB
chmod +x "$tmp/pins"
PATH="$tmp:$PATH"
# shellcheck disable=SC1090
source "$here/completions/pins.bash"

check() {  # check "<command line>" "<expected space-separated>"
    read -r -a COMP_WORDS <<< "$1"; COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))
    case "$1" in *" ") COMP_WORDS+=(""); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ));; esac
    COMPREPLY=()
    set +e; _pins_complete; set -e      # compgen exits 1 when nothing matches, which is a valid answer
    got="${COMPREPLY[*]-}"
    if [ "$got" != "$2" ]; then echo "FAIL: '$1' → '$got' (want '$2')"; exit 1; fi
    echo "ok: '$1' → '$got'"
}
check "pins st" "standup-prep"
check "pins rm " "standup-prep cc-collector rc-mower"
check "pins touch rc" "rc-mower"
check "pins --so" "--sort"
check "pins --sort " "recency alias pinned"
check "pins ad" "add"
check "pins open --f" "--fork"
check "pins rename " "standup-prep cc-collector rc-mower"
check "pins _" ""   # the hidden helpers (_rows, _preview, …) are never offered
echo "completion ok on bash $BASH_VERSION"
