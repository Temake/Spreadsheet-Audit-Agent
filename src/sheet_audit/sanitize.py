"""Make untrusted spreadsheet text safe to print, log, or put in a Markdown report."""

import re

# C0/C1 control characters, including ESC (blocks ANSI terminal-escape injection).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")
# Enough to block emphasis, links/images, HTML, headings, and table breaks.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def clean(text: object, max_len: int = 80) -> str:
    """Strip control characters, collapse whitespace, and truncate."""
    s = _CONTROL.sub("", str(text))
    s = _WHITESPACE.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def md(text: object, max_len: int = 80) -> str:
    """Clean text and escape Markdown so cell contents render as literal text."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", clean(text, max_len))
