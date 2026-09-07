"""The query the built-in picker matches, with fzf's extended syntax.

Space-separated terms must all match (a backslash escapes a space); ``|`` between two terms makes either
one enough. A term is fuzzy (its characters in order, anywhere) unless it starts with ``'`` (the exact
text), ``^`` (a prefix) or ends with ``$`` (a suffix), ``^…$`` both; ``!`` in front negates any of them.
A term with an upper-case letter is matched case-sensitively, otherwise case does not matter (smart
case). Matched characters come back as positions so the rows can highlight them; the list keeps its
order, since fzf's ranking is not reproduced here.

``--nth`` narrows what a query sees: with ``nth="1..3"`` over a row whose columns are tab-separated,
only the text from the first to the third column is searched, as fzf does.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    text: str
    kind: str = "fuzzy"         # fuzzy | exact | prefix | suffix | equal
    negate: bool = False
    case: bool = False          # case-sensitive (the term has an upper-case letter)


def parse(query: str) -> list[list[Term]]:
    """Groups of alternatives: every group must match, one term in a group suffices."""
    words: list[str] = []
    cur, escaped = "", False
    for ch in query:
        if escaped:
            cur += ch; escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == " ":
            if cur:
                words.append(cur); cur = ""
        else:
            cur += ch
    if cur:
        words.append(cur)
    groups: list[list[Term]] = []
    join = False
    for w in words:
        if w == "|":
            join = bool(groups)
            continue
        term = _term(w)
        if term is None:
            continue
        if join and groups:
            groups[-1].append(term)
        else:
            groups.append([term])
        join = False
    return groups


def _term(word: str) -> Term | None:
    negate = word.startswith("!")
    if negate:
        word = word[1:]
    kind = "fuzzy"
    if word.startswith("'"):
        kind, word = "exact", word[1:]
    else:
        prefix = word.startswith("^")
        suffix = word.endswith("$") and len(word) > 1
        if prefix and suffix:
            kind, word = "equal", word[1:-1]
        elif prefix:
            kind, word = "prefix", word[1:]
        elif suffix:
            kind, word = "suffix", word[:-1]
    if not word:
        return None
    return Term(word, kind, negate, any(c.isupper() for c in word))


def match_term(term: Term, text: str) -> tuple[bool, list[int]]:
    """Whether ``term`` matches ``text``, and the positions it matched (none for a negated term)."""
    hay = text if term.case else text.lower()
    needle = term.text if term.case else term.text.lower()
    hit, positions = _find(term.kind, needle, hay)
    if term.negate:
        return not hit, []
    return hit, positions


def _find(kind: str, needle: str, hay: str) -> tuple[bool, list[int]]:
    if kind == "fuzzy":
        positions, j = [], 0
        for i, ch in enumerate(hay):
            if j < len(needle) and ch == needle[j]:
                positions.append(i); j += 1
        return j == len(needle), positions
    if kind == "exact":
        i = hay.find(needle)
        return i >= 0, list(range(i, i + len(needle))) if i >= 0 else []
    # the anchored kinds step over the text's own whitespace, as fzf's do (a column's trailing delimiter
    # would otherwise defeat ``$``), unless the term itself starts or ends with a space
    lead = 0 if needle[:1].isspace() else len(hay) - len(hay.lstrip())
    end = len(hay) if needle[-1:].isspace() else len(hay.rstrip())
    if kind == "prefix":
        hit = hay.startswith(needle, lead)
        return hit, list(range(lead, lead + len(needle))) if hit else []
    if kind == "suffix":
        hit = hay.endswith(needle, 0, end)
        return hit, list(range(end - len(needle), end)) if hit else []
    hit = hay[lead:end] == needle
    return hit, list(range(lead, end)) if hit else []


def match(groups: list[list[Term]], text: str) -> tuple[bool, set[int]]:
    """Every group must match ``text``; the positions of every matching term."""
    positions: set[int] = set()
    for group in groups:
        for term in group:
            hit, pos = match_term(term, text)
            if hit:
                positions.update(pos)
                break
        else:
            return False, set()
    return True, positions


def nth_range(text: str, nth: str | None, delimiter: str = "\t") -> tuple[int, int]:
    """The character span of ``text`` a query sees under fzf's ``--nth``: fields are counted from 1
    and each one runs to the end of its delimiter, so ``1..3`` is the text up to and including the
    third field. ``..`` forms and negative indices are fzf's."""
    if not nth:
        return 0, len(text)
    bounds = []          # (start, end) of every field
    start = 0
    while True:
        i = text.find(delimiter, start)
        if i < 0:
            bounds.append((start, len(text)))
            break
        bounds.append((start, i + len(delimiter)))
        start = i + len(delimiter)
    n = len(bounds)

    def index(s: str, default: int) -> int:
        if not s:
            return default
        i = int(s)
        if i < 0:
            i = n + 1 + i
        return max(1, min(n, i))

    spans = []
    for part in nth.split(","):
        if ".." in part:
            a, b = part.split("..", 1)
            first, last = index(a, 1), index(b, n)
        else:
            first = last = index(part, 1)
        if first <= last:
            spans.append((bounds[first - 1][0], bounds[last - 1][1]))
    if not spans:
        return 0, 0
    return min(s for s, _ in spans), max(e for _, e in spans)


def matches(query: str, text: str, nth: str | None = None) -> tuple[bool, set[int]]:
    """Whether the plain ``text`` of a row matches ``query`` within its ``nth`` columns, with the
    positions in ``text`` to highlight."""
    groups = parse(query)
    if not groups:
        return True, set()
    lo, hi = nth_range(text, nth)
    hit, positions = match(groups, text[lo:hi])
    return hit, {p + lo for p in positions}
