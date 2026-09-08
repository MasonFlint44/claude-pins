#!/usr/bin/env bash
# Headless skill checks: run each case's prompt through `claude -p` with this plugin loaded,
# against a throwaway HOME and config dir, then check what it left behind. Every run is a paid
# model call, so this is run by hand, not in CI (tests/test_plugin.py holds the free checks).
#
#   tests/skills/run.sh [-m MODEL] [-n RUNS] [CASE...]
#     -m MODEL  model alias or id (default: sonnet)
#     -n RUNS   runs per case (default: 1)
#     CASE      case names under tests/skills/cases/ (default: all)
#
# A case is a directory with:
#   prompt     the user's message (a natural-language ask, or /pins:<command> args)
#   setup.sh   sourced before the run with $CFG (throwaway config dir), $HOME (throwaway),
#              $REPO, $WORK, $SID (the run's session id), $PIN (the checkout's bin/pins)
#   check.sh   sourced after the run with the same, plus $OUT (result text) and $RESULT
#              (result JSON); a non-zero `fail` count fails the case.
#              Helpers: expect, expect_out, expect_no_out, store (the pin store as JSON).
#
# The throwaway HOME gets a stub `claude` first on PATH that only records its argv, so no case
# can start a real session; the run itself uses the real claude by absolute path. Results and
# transcripts land in tests/skills/results/<timestamp>/.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
MODEL=sonnet; RUNS=1
while getopts m:n: o; do case $o in m) MODEL=$OPTARG ;; n) RUNS=$OPTARG ;; *) exit 2 ;; esac; done; shift $((OPTIND - 1))
CASES=("$@"); [ ${#CASES[@]} -gt 0 ] || { CASES=(); while IFS= read -r c; do CASES+=("$c"); done < <(cd "$HERE/cases" && find . -mindepth 1 -maxdepth 1 -type d | sed 's#^\./##' | sort); }
REAL_CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; REAL_HOME="$HOME"
CLAUDE_BIN="$(command -v claude)" || { echo "claude not on PATH"; exit 2; }
STAMP=$(date +%Y%m%d-%H%M%S); RES="$HERE/results/$STAMP"; mkdir -p "$RES"
TOOLS="Skill,Bash,Read,Glob,Grep"

fail=0
expect() { [ "$1" = "$2" ] || { echo "    expected: $2"; echo "    got:      $1"; fail=$((fail + 1)); }; }
expect_out() { case "$OUT" in *"$1"*) ;; *) echo "    output lacks: $1"; fail=$((fail + 1)) ;; esac; }
expect_no_out() { case "$OUT" in *"$1"*) echo "    output has: $1"; fail=$((fail + 1)) ;; esac; }
store() { cat "$HOME/.local/state/claude-pins/pins.json" 2>/dev/null || echo '{"pins":[]}'; }

total=0; passed=0; cost_all=0
for case in "${CASES[@]}"; do
    CDIR="$HERE/cases/$case"; [ -r "$CDIR/prompt" ] || { echo "no such case: $case"; continue; }
    for ((run = 1; run <= RUNS; run++)); do
        total=$((total + 1))
        WORK=$(mktemp -d); CFG="$WORK/claude"; HOME="$WORK/home"; mkdir -p "$CFG" "$WORK/cwd" "$HOME/bin" "$HOME/.claude"
        SID=$(python3 -c 'import uuid; print(uuid.uuid4())')
        PIN="$REPO/bin/pins"; export PIN  # for setup.sh/check.sh
        printf '{"hasCompletedOnboarding":true}\n' > "$CFG/.claude.json"
        [ -n "${ANTHROPIC_API_KEY:-}" ] || cp "$REAL_CFG/.credentials.json" "$CFG/.credentials.json"
        printf '#!/bin/sh\necho "$@" >> "%s/claude-argv.txt"\n' "$WORK" > "$HOME/bin/claude"; chmod +x "$HOME/bin/claude"
        fail=0
        # shellcheck disable=SC1090,SC1091
        . "$CDIR/setup.sh"
        RESULT="$RES/$case-$run.json"
        ( cd "$WORK/cwd" && export HOME PATH="$HOME/bin:$PATH" CLAUDE_CONFIG_DIR="$CFG" && "$CLAUDE_BIN" -p "$(cat "$CDIR/prompt")" \
              --plugin-dir "$REPO" --model "$MODEL" --session-id "$SID" --output-format json \
              --permission-mode bypassPermissions --allowedTools "$TOOLS" --max-turns 30 --max-budget-usd 2 \
              --setting-sources user < /dev/null ) > "$RESULT" 2> "$RES/$case-$run.stderr"
        rc=$?
        if [ -z "${ANTHROPIC_API_KEY:-}" ] && ! cmp -s "$CFG/.credentials.json" "$REAL_CFG/.credentials.json"; then
            cp "$CFG/.credentials.json" "$REAL_CFG/.credentials.json" && echo "  (credentials refreshed during the run; copied back)"
        fi
        OUT=$(jq -r '.result // ""' "$RESULT" 2>/dev/null); cost=$(jq -r '(.total_cost_usd // 0) * 1000 | round / 1000' "$RESULT" 2>/dev/null)
        turns=$(jq -r '.num_turns // "?"' "$RESULT" 2>/dev/null)
        [ "$rc" = 0 ] || { echo "    claude exited $rc: $(head -c 300 "$RES/$case-$run.stderr")"; fail=$((fail + 1)); }
        [ -e "$WORK/claude-argv.txt" ] && { echo "    a real claude launch was attempted: $(cat "$WORK/claude-argv.txt")"; fail=$((fail + 1)); }
        # shellcheck disable=SC1090,SC1091
        . "$CDIR/check.sh"
        printf '%s\n' "$OUT" > "$RES/$case-$run.out"
        if [ "$fail" = 0 ]; then passed=$((passed + 1)); verdict=PASS; else verdict=FAIL; fi
        printf '%-4s %-22s run %s  %s turns  $%s\n' "$verdict" "$case" "$run" "$turns" "$cost"
        cost_all=$(awk -v a="$cost_all" -v b="${cost:-0}" 'BEGIN{printf "%.4f", a + b}')
        find "$CFG/projects" -name "$SID.jsonl" -exec cp {} "$RES/$case-$run.transcript.jsonl" \; 2>/dev/null
        HOME="$REAL_HOME"; rm -rf "$WORK"
    done
done
HOME="$REAL_HOME"
printf '\n%s/%s passed, model %s, $%s total; transcripts in %s\n' "$passed" "$total" "$MODEL" "$cost_all" "${RES#"$REPO"/}"
[ "$passed" = "$total" ]
