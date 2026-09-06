#!/usr/bin/env python3
"""shims/*.md must equal commands/*.md with the plugin-root path replaced by `pin` on PATH.

Run with --write to regenerate; CI runs it as a check.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FOOTER = "\n<!-- claude-pins shim: generated from commands/{c}.md by tests/shims_check.py; installed by /pins:pins-install -->\n"


def expected(c: str) -> str:
    text = (REPO / "commands" / f"{c}.md").read_text()
    text = text.replace('"${CLAUDE_PLUGIN_ROOT}/bin/pin"', "pin").replace("Bash(${CLAUDE_PLUGIN_ROOT}/bin/pin:*)", "Bash(pin:*)")
    return text.rstrip("\n") + "\n" + FOOTER.format(c=c)


def main() -> int:
    ok = True
    for c in ("pin", "unpin"):
        path = REPO / "shims" / f"{c}.md"
        want = expected(c)
        if "--write" in sys.argv:
            path.write_text(want)
        elif path.read_text() != want:
            print(f"shims/{c}.md is out of date: python3 tests/shims_check.py --write")
            ok = False
    print("shims ok" if ok else "shims stale")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
