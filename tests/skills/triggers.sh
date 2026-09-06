#!/usr/bin/env bash
# Does each skill's description trigger on the right requests and stay quiet on
# near-misses? Runs the skill-creator plugin's description evaluator/optimizer
# (scripts/run_eval.py and scripts/run_loop.py) over tests/skills/triggers/<skill>.json.
#
#   tests/skills/triggers.sh [-e] [-m MODEL] [-r RUNS] [SKILL...]
#     -e        evaluate only: score the current description, propose nothing
#     -m MODEL  model for the claude -p query runs and the rewrites (default sonnet)
#     -r RUNS   runs per query (default 3; 1 for a cheap smoke test)
#
# Each query is one short claude -p run that stops as soon as the model picks a
# skill. A full optimizer pass is about 20 queries x RUNS x up to 5 iterations per
# skill, so it is paid and run by hand. The optimizer never edits SKILL.md: it
# prints the best description it found, scored on a 40% held-out split, and you
# decide whether to paste it in.
#
# Runs in a throwaway HOME and config dir (credentials copied in, and back if the
# token refreshed) so the installed plugins do not compete with the description
# under test, and with one worker: the evaluator writes a command file per
# in-flight query into the same project, so parallel runs see each other's copies
# and the model picks one at random. Needs the skill-creator plugin:
#   /plugin install skill-creator@claude-plugins-official
# Results: tests/skills/results/triggers/<skill>/ (results.json, report.html, log.txt).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
MODEL=sonnet; RUNS=3; EVAL_ONLY=0
while getopts 'em:r:' o; do case $o in e) EVAL_ONLY=1;; m) MODEL=$OPTARG;; r) RUNS=$OPTARG;; *) exit 2;; esac; done
shift $((OPTIND-1))
REAL_CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; REAL_HOME="$HOME"
command -v claude >/dev/null || { echo "claude not on PATH" >&2; exit 1; }

SC=$(python3 - "$REAL_CFG" <<'PY'
import json, sys, pathlib, glob
cfg = pathlib.Path(sys.argv[1])
try:
    d = json.load(open(cfg / "plugins" / "installed_plugins.json"))
    p = d["plugins"]["skill-creator@claude-plugins-official"][0]["installPath"]
    print(pathlib.Path(p) / "skills" / "skill-creator")
except Exception:
    hits = sorted(glob.glob(str(cfg / "plugins/cache/*/skill-creator/*/skills/skill-creator")), key=lambda h: pathlib.Path(h).stat().st_mtime)
    print(hits[-1] if hits else "")
PY
)
if [ -z "$SC" ] || [ ! -f "$SC/scripts/run_loop.py" ]; then
    echo "skill-creator plugin not found: /plugin install skill-creator@claude-plugins-official" >&2; exit 1
fi

if [ $# -eq 0 ]; then set -- "$REPO"/tests/skills/triggers/*.json; fi
STAMP=$(date +%Y%m%d-%H%M%S)
for arg in "$@"; do
    skill=$(basename "$arg" .json)
    evalset="$REPO/tests/skills/triggers/$skill.json"
    [ -f "$evalset" ] || { echo "no eval set: $evalset" >&2; exit 1; }
    [ -f "$REPO/skills/$skill/SKILL.md" ] || { echo "no skill: $skill" >&2; exit 1; }
    RES="$REPO/tests/skills/results/triggers/$skill/$STAMP"; mkdir -p "$RES"
    WORK=$(mktemp -d); CFG="$WORK/claude"; HOME="$WORK/home"
    mkdir -p "$CFG" "$WORK/cwd/.claude" "$HOME/.claude"
    printf '{"hasCompletedOnboarding":true}\n' > "$CFG/.claude.json"
    [ -n "${ANTHROPIC_API_KEY:-}" ] || cp "$REAL_CFG/.credentials.json" "$CFG/.credentials.json"
    echo "== $skill ($MODEL, $RUNS runs/query, $([ $EVAL_ONLY = 1 ] && echo evaluate || echo optimize))"
    # shellcheck disable=SC2030,SC2031  # the subshell scoping is the point
    if [ $EVAL_ONLY = 1 ]; then
        ( cd "$WORK/cwd" && export HOME CLAUDE_CONFIG_DIR="$CFG" PYTHONPATH="$SC" \
          && python3 -m scripts.run_eval --eval-set "$evalset" --skill-path "$REPO/skills/$skill" \
             --model "$MODEL" --runs-per-query "$RUNS" --num-workers 1 --verbose > "$RES/eval.json" )
        python3 - "$RES/eval.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rs = d.get("results", d)
for r in rs:
    print(f"  {'ok  ' if r['pass'] else 'FAIL'} {'trigger' if r['should_trigger'] else 'quiet  '} {r['query'][:90]}")
n = sum(1 for r in rs if r["pass"])
print(f"  {n}/{len(rs)} as expected")
PY
    else
        ( cd "$WORK/cwd" && export HOME CLAUDE_CONFIG_DIR="$CFG" PYTHONPATH="$SC" \
          && python3 -m scripts.run_loop --eval-set "$evalset" --skill-path "$REPO/skills/$skill" \
             --model "$MODEL" --runs-per-query "$RUNS" --num-workers 1 --report none --results-dir "$RES" --verbose > "$RES/loop.json" )
        python3 - "$RES/loop.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  {d.get('exit_reason')}; best {d.get('best_score')}")
print("  best description:\n   ", d.get("best_description"))
PY
    fi
    if [ -z "${ANTHROPIC_API_KEY:-}" ] && ! cmp -s "$CFG/.credentials.json" "$REAL_CFG/.credentials.json"; then
        cp "$CFG/.credentials.json" "$REAL_CFG/.credentials.json" && echo "  (credentials refreshed during the run; copied back)"
    fi
    HOME="$REAL_HOME"; rm -rf "$WORK"
    echo "  results: ${RES#"$REPO"/}"
done
