#compdef pin
# zsh completion for `pin` (claude-pins). /pins:install links it as _pin into a directory on fpath.
# Aliases come from `pin _complete`; subcommands and flags mirror completions/pin.bash.

_pin() {
    local -a subs aliases
    subs=(add list sessions edit rename rm unpin undo prune touch doctor open help)
    aliases=(${(f)"$(command pin _complete 2>/dev/null)"})
    case "${words[CURRENT]}" in
        -*)
            compadd -- --help --version --sort --fork --resume -w --worktree --no-fzf --all --json \
                --title --note --keep --no-keep --no-fork --no-worktree --model --effort \
                --permission-mode --rename -y
            return ;;
    esac
    local prev="${words[CURRENT-1]}"
    case "$prev" in
        rm|unpin|touch|edit|open|rename)
            compadd -a aliases; return ;;
        --sort)
            compadd recency alias pinned; return ;;
        --permission-mode)
            compadd default acceptEdits plan auto bypassPermissions; return ;;
        --effort)
            compadd low medium high max; return ;;
        --cwd)
            _path_files -/; return ;;
    esac
    if (( CURRENT == 2 )); then
        compadd -a subs
    fi
    compadd -a aliases
}

_pin "$@"
