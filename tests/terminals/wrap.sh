#!/bin/bash
# What each terminal runs: the fixture environment inside.py wrote, then pin under `script` so its
# output is kept for the report.
mode=$1; name=$2
# shellcheck source=/dev/null
. /out/env.sh
case $mode in
  keys) exec script -q -f -c "python3 /repo/bin/pin _keys" "/out/$name.keys.log" ;;
  picker) exec script -q -f -c "python3 /repo/bin/pin" "/out/$name.picker.log" ;;
esac
