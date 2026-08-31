"""Locator resolution and excerpt verification - the provenance backbone.

A locator produced by an analysing model carries at minimum paper_id, unit_id and
a short verbatim excerpt.  This module fills in everything else deterministically
(section path, page index/label, exact character offsets) by *finding the excerpt
in the normalized text*, which simultaneously proves the quote is real.  If the
excerpt cannot be found, that is recorded as a warning and reported as an error by
`kb validate` - it is never quietly accepted.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from . import sentences, store

_FOLD = {
    "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-",
    "—": "-", "−": "-", " ": " ", "ﬁ": "fi", "ﬂ": "fl",
}


def normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """Return (normalized_lowercase_text, map from normalized index -> original index)."""
    out: List[str] = []
    imap: List[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        if unicodedata.category(ch) in ("Cc", "Cf", "Co") and ch not in "\n\t":
            continue  # extraction artefact; never part of a real quote
        folded = _FOLD.get(ch, ch)
        if folded.isspace():
            if prev_space:
                continue
            out.append(" ")
            imap.append(i)
            prev_space = True
            continue
        prev_space = False
        for sub in folded.lower():
            out.append(sub)
            imap.append(i)
    while out and out[-1] == " ":
        out.pop()
        imap.pop()
    return "".join(out), imap


def find_excerpt(text: str, excerpt: str) -> Optional[Tuple[int, int]]:
    """Locate `excerpt` in `text` ignoring whitespace/quote/ligature differences."""
    if not excerpt or not text:
        return None
    norm_text, imap = normalize_with_map(text)
    norm_ex, _ = normalize_with_map(excerpt)
    if not norm_ex:
        return None
    pos = norm_text.find(norm_ex)
    if pos < 0:
        # tolerate an excerpt whose tail was elided or lightly reflowed
        head = norm_ex[: max(40, int(len(norm_ex) * 0.6))]
        pos = norm_text.find(head)
        if pos < 0:
            return None
        end_norm = pos + len(head)
    else:
        end_norm = pos + len(norm_ex)
    start = imap[pos]
    end = imap[min(end_norm, len(imap)) - 1] + 1
    return start, end


def resolve(kb_dir: str, paper_id: str, loc: Dict[str, Any], units: List[Dict[str, Any]] = None,
            text: str = None, source_filename: str = None,
            extraction_method: str = None) -> Dict[str, Any]:
    """Complete and verify one locator. Always returns a locator (with warnings)."""
    units = units if units is not None else store.read_units(kb_dir, paper_id)
    text = text if text is not None else store.read_text(kb_dir, paper_id)
    by_id = {u["unit_id"]: u for u in units}
    out = dict(loc)
    out["paper_id"] = paper_id
    out.setdefault("warnings", [])
    if source_filename:
        out.setdefault("source_filename", source_filename)
    if extraction_method:
        out.setdefault("extraction_method", extraction_method)
    out.setdefault("extraction_method", "text")
    excerpt = (out.get("excerpt") or "").strip()
    unit = by_id.get(out.get("unit_id"))

    span = None
    if unit and excerpt:
        span = find_excerpt(unit["text"], excerpt)
        if span:
            span = (unit["char_start"] + span[0], unit["char_start"] + span[1])
    if span is None and excerpt:
        span = find_excerpt(text, excerpt)
        if span:
            correct = _unit_for_offset(units, span[0])
            if correct and correct["unit_id"] != out.get("unit_id"):
                if out.get("unit_id"):
                    out["warnings"].append(
                        "excerpt was not in unit %s; corrected to %s" % (out.get("unit_id"), correct["unit_id"]))
                out["unit_id"] = correct["unit_id"]
                unit = correct
    if span is None:
        out["warnings"].append(
            "excerpt not found in the normalized text of %s - the quote is unverified" % paper_id)
        out["confidence"] = min(float(out.get("confidence") or 0.5), 0.3)
    else:
        out["char_start"], out["char_end"] = span
        if unit is None:
            unit = _unit_for_offset(units, span[0])
            if unit:
                out["unit_id"] = unit["unit_id"]

    if unit:
        out.setdefault("section_path", unit.get("section_path") or [])
        if out.get("page_index") is None:
            out["page_index"] = unit.get("page_index_start")
        if not out.get("page_label"):
            out["page_label"] = unit.get("page_label_start")
        if unit.get("line_start") and not out.get("line_start"):
            out["line_start"] = unit["line_start"]
        if unit.get("anchor") and not out.get("anchor"):
            out["anchor"] = unit["anchor"]
        if unit.get("caption_label"):
            label = unit["caption_label"]
            if re.match(r"(?i)tab", label):
                out.setdefault("table_label", label)
            else:
                out.setdefault("figure_label", label)
        if out.get("char_start") is not None and out.get("sentence_index") is None:
            for i, (s, e) in enumerate(unit.get("sentence_offsets") or []):
                if s <= out["char_start"] < e:
                    out["sentence_index"] = i
                    break
    out.setdefault("confidence", 0.8 if span else 0.3)
    out["confidence"] = round(float(out["confidence"]), 2)
    if not out.get("warnings"):
        out.pop("warnings")
    return out


def _unit_for_offset(units: List[Dict[str, Any]], offset: int) -> Optional[Dict[str, Any]]:
    for unit in units:
        if unit["char_start"] <= offset < unit["char_end"]:
            return unit
    return None


def quote(kb_dir: str, paper_id: str, loc: Dict[str, Any], pad: int = 160) -> str:
    """Return the passage around a locator, for `kb show-claim` and validation."""
    text = store.read_text(kb_dir, paper_id)
    if loc.get("char_start") is None:
        return ""
    start = max(0, loc["char_start"] - pad)
    end = min(len(text), (loc.get("char_end") or loc["char_start"]) + pad)
    return text[start:end]
