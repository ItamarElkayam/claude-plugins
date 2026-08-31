"""Conservative sentence segmentation shared by extraction, validation and context.

Deliberately simple and deterministic: it must give identical offsets every run,
because sentence offsets are part of stored source locators.  It avoids splitting
on common scientific abbreviations, decimals, and initials.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_ABBREV = {
    "e.g", "i.e", "et al", "cf", "vs", "fig", "figs", "eq", "eqs", "ref", "refs",
    "tab", "no", "approx", "resp", "ca", "sp", "spp", "al", "dr", "prof", "mr",
    "ms", "st", "vol", "pp", "ed", "eds", "min", "max", "std", "avg", "e.g.,",
}
_END = re.compile(r"([.!?])([\"'”’)\]]*)(\s+|$)")


def split_offsets(text: str, base: int = 0) -> List[Tuple[int, int]]:
    """Return absolute [start, end) offsets of sentences within `text`."""
    spans: List[Tuple[int, int]] = []
    start = 0
    for match in _END.finditer(text):
        end = match.end(2)
        head = text[start:end].strip()
        if not head:
            start = match.end()
            continue
        tail = re.split(r"[\s(\[]", head)[-1].rstrip(".!?\"')]").lower()
        if tail in _ABBREV or re.match(r"^[a-z]$", tail) or re.match(r"^\d+$", tail):
            continue
        if len(head) < 15 and not head.endswith(("?", "!")):
            continue
        spans.append((base + start, base + end))
        start = match.end()
    if start < len(text) and text[start:].strip():
        spans.append((base + start, base + len(text.rstrip()) if text.rstrip() else base + len(text)))
    return spans


def normalize_for_match(text: str) -> str:
    """Whitespace/quote folding used when checking that an excerpt really occurs."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"\s+", " ", text).strip().lower()
