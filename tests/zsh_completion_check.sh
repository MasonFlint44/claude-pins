#!/usr/bin/env zsh
# Source the zsh completion with a stub `pin` and a stub `compadd`, and check what it offers.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
cat > "$tmp/pin" <<'STUB'
#!/bin/sh
[ "$1" = "_complete" ] && printf 'standup-prep\ncc-collector\nrc-mower\n'
STUB
chmod +x "$tmp/pin"
path=("$tmp" $path)

typeset -a got
compadd() {  # records candidates; understands the two forms the script uses: -a NAME and -- words
    local opt
    while [ $# -gt 0 ]; do
        case "$1" in
            -a) shift; got+=("${(P@)1}") ;;
            --) shift; got+=("$@"); return ;;
            -*) ;;
            *) got+=("$1") ;;
        esac
        shift
    done
}
_path_files() { got+=(PATHFILES); }

# Load the function body without running the trailing `_pin "$@"`.
eval "$(sed '$d' "$here/completions/pin.zsh")"

check() {  # check "<command line>" "<expected space-separated>"
    words=(${(s: :)1})
    case "$1" in *" ") words+=("");; esac
    CURRENT=${#words}
    got=()
    _pin
    local want="$2" have="${(j: :)got}"
    if [ "$have" != "$want" ]; then echo "FAIL: '$1' → '$have' (want '$want')"; exit 1; fi
    echo "ok: '$1' → '$have'"
}
check "pin " "add list sessions edit rename rm unpin undo prune touch doctor open help standup-prep cc-collector rc-mower"
check "pin rm " "standup-prep cc-collector rc-mower"
check "pin rename " "standup-prep cc-collector rc-mower"
check "pin --sort " "recency alias pinned"
check "pin add x --cwd " "PATHFILES"
check "pin open --" "--help --version --sort --fork --resume -w --worktree --no-fzf --all --json --title --note --keep --no-keep --no-fork --no-worktree --model --effort --permission-mode --rename -y"
# The hidden helpers (_rows, _preview, …) are never offered; the exact `pin ` list above is the check, and
# this catches one slipping into the source another way.
if grep -Eq '_(rows|preview|spreview|status)\b' "$here/completions/pin.zsh"; then echo "FAIL: a hidden helper is listed"; exit 1; fi
echo "completion ok on zsh $ZSH_VERSION"
