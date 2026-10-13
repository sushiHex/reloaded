"""PowerShell single-quoted literals: one rule for every module that writes them."""
from __future__ import annotations

# Every character PowerShell reads as a single quote - the ASCII one and four
# typographic ones. Doubled, each is a literal; left single, a U+2019 in a
# captured argument closed the string and the rest ran as commands, in both
# PowerShell 7 and 5.1.
SINGLE_QUOTES = "'‘’‚‛"


def escape(s: str) -> str:
    """`s` made safe inside a PowerShell single-quoted string.

    A single-quoted string is literal all the way through - no variable
    expansion, no subexpressions, no escapes - so the quote characters are the
    only thing to handle, each doubled.
    """
    for quote in SINGLE_QUOTES:
        s = s.replace(quote, quote * 2)
    return s


def literal(s: str) -> str:
    """`s` as a complete PowerShell single-quoted string."""
    return "'" + escape(s) + "'"
