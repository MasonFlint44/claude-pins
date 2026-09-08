#!/usr/bin/env bash
# Every key the picker binds, pressed in real Linux terminals, and one screenshot of the picker in each.
#
#   tests/terminals/run.sh [TERMINAL...]     # all of them, or xterm gnome-terminal konsole kitty alacritty
#                                            # ghostty tmux-in-xterm xterm-metaSendsEscape
#
# Builds a Docker image (Arch: Xvfb, openbox, xdotool, the terminals; about 4 GB, once) and runs
# tests/terminals/inside.py in it: a fixture HOME with four pins, then each terminal runs `pin _keys`
# while xdotool presses every keymap key, the query-editing keys and the mouse, and `pin` for a
# screenshot. About five minutes for the full set. Results in tests/terminals/out: report.md (a
# table of what each terminal delivered), <terminal>.png, <terminal>.keys.log. By hand, not in CI.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
command -v docker >/dev/null || { echo "docker not on PATH" >&2; exit 1; }
docker build -q -t pins-terminals "$HERE" >/dev/null
rm -rf "$HERE/out"; mkdir -p "$HERE/out"
docker run --rm -v "$REPO:/repo:ro" -v "$HERE:/work:ro" -v "$HERE/out:/out" --shm-size=1g \
  pins-terminals python3 /work/inside.py "$@"
echo "report: $HERE/out/report.md"
