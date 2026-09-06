# bash completion for `pin` (claude-pins). Works on bash 3.2+.
#   source this file, or: pin doctor tells /pins-install where it was linked.
_pin_complete() {
    local cur prev words cword
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    local subs="add list edit rm unpin undo prune touch doctor open help"
    local aliases
    aliases="$(command pin _complete 2>/dev/null)"
    case "$prev" in
        rm|unpin|touch|edit|open)
            COMPREPLY=( $(compgen -W "$aliases" -- "$cur") ); return 0 ;;
        --sort)
            COMPREPLY=( $(compgen -W "recency alias pinned" -- "$cur") ); return 0 ;;
        --permission-mode)
            COMPREPLY=( $(compgen -W "default acceptEdits plan auto bypassPermissions" -- "$cur") ); return 0 ;;
        --effort)
            COMPREPLY=( $(compgen -W "low medium high max" -- "$cur") ); return 0 ;;
        --cwd)
            COMPREPLY=( $(compgen -d -- "$cur") ); return 0 ;;
    esac
    case "$cur" in
        -*)
            COMPREPLY=( $(compgen -W "--help --version --sort --fork --resume -w --worktree --no-fzf --all --json --title --note --keep --no-keep --fork --no-fork --worktree --no-worktree --model --effort --permission-mode --rename -y" -- "$cur") )
            return 0 ;;
    esac
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$subs $aliases" -- "$cur") )
    else
        COMPREPLY=( $(compgen -W "$aliases" -- "$cur") )
    fi
    return 0
}
complete -F _pin_complete pin
