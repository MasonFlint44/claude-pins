#!/usr/bin/env python3
"""A scripted stand-in for fzf.

Reads the next step from ``$CLAUDE_PINS_FZF_SCRIPT`` (JSON lines). Each step:
  {"key": "alt-t", "query": "", "select": ["substring", ...]}   → prints like fzf would
  {"abort": true}                                             → exit 130 (esc)
  {"unlink": "/path", ...}                                    → delete that file first (a race)
  {"shell": "cmd", ...}                                       → run that first (another terminal at work)
  {"raw": ["id\tdisplay", ...], ...}                          → print these lines as the selection, as a
                                                                reloaded list would (they need not be in the input)
Every invocation appends {"argv": [...], "lines": [...]} to ``$CLAUDE_PINS_FZF_LOG``.
``--version`` reports ``$CLAUDE_PINS_FZF_STUB_VERSION`` (default 0.44.1), so a test can pick the branch of
every version gate.
"""
import json
import os
import sys


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        print(os.environ.get("CLAUDE_PINS_FZF_STUB_VERSION", "0.44.1") + " (stub)")
        return 0
    lines = sys.stdin.read().split("\n")
    lines = [ln for ln in lines if ln]
    log = os.environ.get("CLAUDE_PINS_FZF_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps({"argv": argv, "lines": lines}) + "\n")
    script = os.environ.get("CLAUDE_PINS_FZF_SCRIPT")
    steps = []
    if script and os.path.exists(script):
        with open(script) as fh:
            steps = [ln for ln in fh.read().split("\n") if ln.strip()]
    if not steps:
        return 130
    step = json.loads(steps[0])
    with open(script, "w") as fh:
        fh.write("\n".join(steps[1:]) + ("\n" if len(steps) > 1 else ""))
    if step.get("unlink"):
        os.unlink(step["unlink"])
    if step.get("shell"):
        import subprocess
        subprocess.run(step["shell"], shell=True, check=True, capture_output=True)
    if step.get("abort"):
        return 130
    expect = "--expect" in argv
    print(step.get("query", ""))
    if expect:
        print(step.get("key", ""))
    selected = list(step.get("raw", []))
    for want in step.get("select", []):
        for ln in lines:
            plain = strip(ln)
            if ln.split("\t", 1)[0] == want or want in plain:
                selected.append(ln)
                break
    for ln in selected:
        print(ln)
    return 0 if selected or not lines else 1


def strip(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


if __name__ == "__main__":
    sys.exit(main())
