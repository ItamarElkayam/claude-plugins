"""Turn raw adapter blocks into a normalized, provenance-carrying document.

Outputs per paper (all under <kb>/docs/<paper_id>/):
  text.txt       normalized document text - THE canonical offset space
  blocks.jsonl   every block with role, section path and char span
  units.jsonl    retrieval units (paragraph/caption/table granularity)
  parse.json     adapter, quality metrics, section list, warnings

Key jobs: running header/footer removal, two-column reading order, heading and
section-hierarchy detection, references cut-off, caption/table/footnote roles,
de-hyphenation, and text-quality assessment (including "this needs OCR").
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from . import ids, sentences

CANONICAL_SECTIONS = [
    "abstract", "summary", "significance", "introduction", "background",
    "related work", "prior work", "materials and methods", "methods", "method",
    "methodology", "experimental setup", "experiments", "data and methods",
    "results", "results and discussion", "evaluation", "discussion",
    "limitations", "conclusion", "conclusions", "conclusion and future work",
    "acknowledgements", "acknowledgments", "funding", "author contributions",
    "competing interests", "conflict of interest", "data availability",
    "code availability", "ethics", "references", "bibliography",
    "literature cited", "supplementary material", "supplementary materials",
    "supporting information", "appendix", "appendices",
]
REFERENCE_HEADINGS = ("references", "bibliography", "literature cited", "reference list")
POST_REFERENCE_HEADINGS = ("appendix", "appendices", "supplementary", "supporting information")
CAPTION_RE = re.compile(
    r"^\s*(supplementary\s+)?(table|tab\.|figure|fig\.?|scheme|algorithm|listing|box|equation|eq\.)\s*"
    r"(s?\d+[a-z]?|[ivxlc]+)\b\s*[.:|)]?", re.I)
NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{0,110})$")
_HEADING_STYLE_RE = re.compile(r"^heading([1-6])$")
_MATH_CHARS = set("=+×÷≤≥≈∑∏∫∂√±→←↔∈∉⊂⊆∪∩∀∃¬∧∨αβγδεθλμνπρσςτφχψωΩΔΣΓΘΛΞΠΦΨ^_|")


# ---------------------------------------------------------------- header/footer
def _mask(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))[:90]


def _mark_running_heads(blocks: List[Dict[str, Any]], cfg: Dict[str, Any], n_pages: int) -> None:
    if n_pages < 3:
        return
    hb = cfg["parsing"]["header_band"]
    fb = cfg["parsing"]["footer_band"]
    frac = cfg["parsing"]["header_footer_page_fraction"]
    counts: Dict[Tuple[str, str], set] = {}
    for blk in blocks:
        if not blk.get("bbox") or not blk.get("page_height"):
            continue
        y0, y1 = blk["bbox"][1], blk["bbox"][3]
        height = blk["page_height"]
        band = None
        if y1 <= height * hb:
            band = "header"
        elif y0 >= height * (1.0 - fb):
            band = "footer"
        if band is None or blk["n_lines"] > 3 or len(blk["text"]) > 200:
            continue
        blk["_band"] = band
        counts.setdefault((band, _mask(blk["text"])), set()).add(blk["page_index"])
    threshold = max(3, int(round(frac * n_pages)))
    repeated = {key for key, pages in counts.items() if len(pages) >= threshold}
    for blk in blocks:
        band = blk.get("_band")
        if not band:
            continue
        text = blk["text"].strip()
        if re.match(r"^[ivxlcdm]{1,7}$|^\d{1,5}$", text, re.I):
            blk["role"] = "page_number"
            blk["page_label_candidate"] = text
        elif (band, _mask(text)) in repeated:
            blk["role"] = band


# ---------------------------------------------------------------- inline headings
def _split_inline_headings(blocks: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split "Heading\nbody text..." blocks that PDF extractors merge into one.

    Journals such as BMC emit a section heading and its first paragraph as a single
    text block; without this split the section hierarchy (and the references
    cut-off) silently disappears.
    """
    extra = cfg["parsing"]["extra_section_headings"]
    out: List[Dict[str, Any]] = []
    for blk in blocks:
        text = blk["text"]
        if blk.get("style_hint") in ("table", "table_row", "code_block") or "\n" not in text:
            out.append(blk)
            continue
        first, _, rest = text.partition("\n")
        head = first.strip().rstrip(":")
        if len(head) > 60 or len(rest.strip()) < 40:
            out.append(blk)
            continue
        canon = _canonical_heading(head, extra)
        numbered = NUMBERED_HEADING_RE.match(head)
        if not canon and not (numbered and re.match(r"^[A-Z(]", numbered.group(2))):
            out.append(blk)
            continue
        head_blk = dict(blk)
        head_blk["text"] = head
        head_blk["n_lines"] = 1
        head_blk["style_hint"] = "heading_inline"
        head_blk["split_from"] = blk["text"][:40]
        body_blk = dict(blk)
        body_blk["text"] = rest.strip()
        body_blk["n_lines"] = max(1, rest.count("\n") + 1)
        body_blk["split_from"] = blk["text"][:40]
        out.append(head_blk)
        out.append(body_blk)
    for i, blk in enumerate(out):
        blk["order"] = i
    return out


# ---------------------------------------------------------------- reading order
def _order_page(page_blocks: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two-column aware reading order using spanning blocks as band separators."""
    with_box = [b for b in page_blocks if b.get("bbox")]
    if len(with_box) != len(page_blocks) or not page_blocks:
        return sorted(page_blocks, key=lambda b: b["order"])
    width = page_blocks[0].get("page_width") or 1.0
    xs = [b["bbox"][0] for b in page_blocks]
    xe = [b["bbox"][2] for b in page_blocks]
    left_edge, right_edge = min(xs), max(xe)
    span = max(1.0, right_edge - left_edge)
    mid = left_edge + span / 2.0

    def is_spanning(b):
        return (b["bbox"][2] - b["bbox"][0]) > 0.62 * span

    def column(b):
        centre = (b["bbox"][0] + b["bbox"][2]) / 2.0
        return 0 if centre < mid else 1

    non_span = [b for b in page_blocks if not is_spanning(b)]
    left = [b for b in non_span if column(b) == 0]
    right = [b for b in non_span if column(b) == 1]
    straddle = [b for b in non_span if b["bbox"][0] < mid < b["bbox"][2]]
    two_col = (
        len(left) >= cfg["parsing"]["column_min_blocks"]
        and len(right) >= cfg["parsing"]["column_min_blocks"]
        and (len(straddle) / float(max(1, len(non_span)))) <= cfg["parsing"]["column_max_straddle"]
    )
    if not two_col:
        return sorted(page_blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))

    ordered: List[Dict[str, Any]] = []
    band: List[Dict[str, Any]] = []
    for blk in sorted(page_blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0])):
        if is_spanning(blk):
            ordered.extend(sorted(band, key=lambda b: (column(b), b["bbox"][1])))
            band = []
            ordered.append(blk)
        else:
            band.append(blk)
    ordered.extend(sorted(band, key=lambda b: (column(b), b["bbox"][1])))
    return ordered


# ---------------------------------------------------------------- headings
def _body_font_size(blocks: List[Dict[str, Any]]) -> Optional[float]:
    weighted: List[float] = []
    for blk in blocks:
        if blk.get("font_size") and blk.get("role") not in ("header", "footer", "page_number"):
            weighted.extend([blk["font_size"]] * max(1, len(blk["text"]) // 40))
    if not weighted:
        return None
    try:
        return statistics.median(weighted)
    except statistics.StatisticsError:
        return None


def _canonical_heading(text: str, extra: List[str]) -> Optional[str]:
    probe = re.sub(r"^\s*\d+(\.\d+)*[.)]?\s*", "", text.strip().rstrip(":.").lower())
    probe = re.sub(r"\s+", " ", probe)
    if probe in CANONICAL_SECTIONS or probe in [e.lower() for e in extra or []]:
        return probe
    return None


def _heading_level(blk: Dict[str, Any], body_size: Optional[float], cfg: Dict[str, Any]) -> Optional[int]:
    text = blk["text"].strip()
    hint = blk.get("style_hint") or ""
    match = _HEADING_STYLE_RE.match(hint)
    if match:
        return int(match.group(1))
    if hint in ("table", "table_row", "code_block", "caption"):
        return None
    if len(text) > 130 or blk["n_lines"] > 2 or "\n" in text.strip():
        if not _canonical_heading(text, cfg["parsing"]["extra_section_headings"]):
            return None
    if CAPTION_RE.match(text):
        return None
    canon = _canonical_heading(text, cfg["parsing"]["extra_section_headings"])
    if canon:
        return 1
    numbered = NUMBERED_HEADING_RE.match(text)
    if numbered and len(text) <= 120 and not _looks_like_equation(text):
        depth = numbered.group(1).count(".") + 1
        rest = numbered.group(2)
        looks_titleish = (
            not text.rstrip().endswith((".", ";", ","))
            and re.match(r"^[A-Z(]", rest)
            and len(re.findall(r"[A-Za-z]{2,}", rest)) >= 1
        )
        if looks_titleish:
            return min(depth, 4)
    size = blk.get("font_size")
    if size and body_size:
        if size >= body_size * 1.25 and len(text) <= 120:
            return 1
        if size >= body_size * 1.10 and len(text) <= 90:
            return 2
    if blk.get("bold") and len(text) <= 80 and not text.rstrip().endswith("."):
        return 3
    return None


# ---------------------------------------------------------------- normalization
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\ufeff\ue000-\uf8ff]")


def normalize_block_text(text: str, kind: str, dehyphenate: bool) -> str:
    # PDF font encodings leak control characters and private-use glyphs into the
    # extracted text ("of \x05150 bp"). They are artefacts, not content, and if kept
    # they silently break every excerpt match, so they are removed before offsets
    # are computed.
    text = text.replace("\u00ad", "")
    text = _CONTROL_RE.sub(" ", text)
    if kind in ("table", "code_block"):
        lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in text.split("\n")]
        return "\n".join(ln for ln in lines if ln.strip())
    if dehyphenate:
        text = re.sub(r"(\w)[-‐‑]\n(\p{Ll})" if False else r"([A-Za-z])[-‐‑]\n([a-z])", r"\1\2", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return text.strip()


def _looks_like_equation(text: str) -> bool:
    if len(text) > 220 or len(text) < 3:
        return False
    math = sum(1 for c in text if c in _MATH_CHARS)
    letters = sum(1 for c in text if c.isalpha())
    return math >= 2 and (math / float(max(1, len(text))) > 0.08) and letters < len(text) * 0.6


# ---------------------------------------------------------------- main entry
def build_document(parse_result: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return {'text', 'blocks', 'units', 'report'} for one parsed document."""
    blocks = [dict(b) for b in parse_result["blocks"]]
    for i, blk in enumerate(blocks):
        blk.setdefault("role", None)
        blk["block_id"] = "b%05d" % i
    blocks = _split_inline_headings(blocks, cfg)
    for i, blk in enumerate(blocks):
        blk["block_id"] = "b%05d" % i
    n_pages = parse_result.get("n_pages") or 0
    _mark_running_heads(blocks, cfg, n_pages)

    # reading order
    if blocks and blocks[0].get("page_index") is not None:
        ordered: List[Dict[str, Any]] = []
        pages: Dict[int, List[Dict[str, Any]]] = {}
        for blk in blocks:
            pages.setdefault(blk["page_index"], []).append(blk)
        for page_index in sorted(pages):
            ordered.extend(_order_page(pages[page_index], cfg))
    else:
        ordered = sorted(blocks, key=lambda b: b["order"])

    printed_labels: Dict[int, str] = {}
    for blk in ordered:
        if blk.get("role") == "page_number" and blk.get("page_index") is not None:
            printed_labels.setdefault(blk["page_index"], blk["page_label_candidate"])
    if len(printed_labels) >= 2:
        # only trust folios that form a consistent run, so a stray number is ignored
        pages = sorted(printed_labels)
        numeric = [(p, printed_labels[p]) for p in pages if printed_labels[p].isdigit()]
        consistent = all(int(b[1]) - int(a[1]) == b[0] - a[0]
                         for a, b in zip(numeric, numeric[1:]))
        if consistent and numeric:
            offset = int(numeric[0][1]) - numeric[0][0]
            for blk in ordered:
                if blk.get("page_index") is not None:
                    blk["page_label"] = str(blk["page_index"] + offset)

    body_size = _body_font_size(ordered)
    extra = cfg["parsing"]["extra_section_headings"]
    section_stack: List[str] = []
    in_references = False
    seen_abstract = False
    parts: List[str] = []
    cursor = 0
    out_blocks: List[Dict[str, Any]] = []
    headings: List[Dict[str, Any]] = []
    dehyph = cfg["parsing"]["dehyphenate"]

    for blk in ordered:
        role = blk.get("role")
        raw = blk["text"].strip()
        if role in ("header", "footer", "page_number"):
            blk["section_path"] = list(section_stack)
            blk["char_start"] = blk["char_end"] = None
            out_blocks.append(blk)
            continue

        level = _heading_level(blk, body_size, cfg)
        if (
            level
            and not seen_abstract
            and blk.get("page_index") in (0, None)
            and len(out_blocks) < 12
            and _canonical_heading(raw, extra) is None
            and not NUMBERED_HEADING_RE.match(raw)
        ):
            level = None  # front matter (title, authors, affiliations, journal banner)
        kind = "table" if blk.get("style_hint") in ("table", "table_row") else (
            "code_block" if blk.get("style_hint") == "code_block" else "paragraph")

        if level:
            canon = _canonical_heading(raw, extra)
            if canon and any(canon.startswith(h) for h in REFERENCE_HEADINGS):
                in_references = True
            elif canon and any(canon.startswith(h) for h in POST_REFERENCE_HEADINGS):
                in_references = False
            if canon in ("abstract", "summary"):
                seen_abstract = True
            section_stack = section_stack[: level - 1]
            while len(section_stack) < level - 1:
                section_stack.append("(unnumbered)")
            section_stack.append(re.sub(r"\s+", " ", raw).strip())
            blk["role"] = "heading"
            blk["heading_level"] = level
            headings.append({
                "text": section_stack[-1], "level": level,
                "page_index": blk.get("page_index"), "page_label": blk.get("page_label"),
                "block_id": blk["block_id"], "canonical": canon,
            })
        elif in_references:
            blk["role"] = "references"
        elif CAPTION_RE.match(raw):
            blk["role"] = "caption"
            blk["caption_label"] = re.sub(r"\s+", " ", CAPTION_RE.match(raw).group(0)).strip(" .:|)")
        elif kind == "table":
            blk["role"] = "table"
        elif kind == "code_block":
            blk["role"] = "code"
        elif blk.get("_band") == "footer" and body_size and (blk.get("font_size") or body_size) < body_size * 0.92:
            blk["role"] = "footnote"
        elif _looks_like_equation(raw):
            blk["role"] = "equation_candidate"
        elif not seen_abstract and (blk.get("page_index") in (0, None)) and len(parts) < 6:
            blk["role"] = "front_matter"
        else:
            blk["role"] = "body"

        text = normalize_block_text(raw, kind, dehyph)
        if not text:
            blk["section_path"] = list(section_stack)
            blk["char_start"] = blk["char_end"] = None
            out_blocks.append(blk)
            continue
        if parts:
            cursor += 2  # the "\n\n" separator
        parts.append(text)
        blk["char_start"] = cursor
        blk["char_end"] = cursor + len(text)
        cursor += len(text)
        blk["section_path"] = list(section_stack)
        blk["norm_text"] = text
        out_blocks.append(blk)

    full_text = "\n\n".join(parts)
    for blk in out_blocks:
        if blk.get("char_start") is not None:
            assert full_text[blk["char_start"]:blk["char_end"]] == blk["norm_text"], blk["block_id"]

    units = _build_units(out_blocks, full_text, cfg)
    report = _quality_report(parse_result, out_blocks, full_text, units, headings, cfg)
    for blk in out_blocks:
        blk.pop("_band", None)
        blk.pop("norm_text", None)
    return {"text": full_text, "blocks": out_blocks, "units": units, "report": report}


_UNIT_ROLES = ("front_matter", "body", "caption", "table", "code", "footnote", "equation_candidate")


def _build_units(blocks: List[Dict[str, Any]], full_text: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    max_chars = cfg["units"]["max_chars"]
    min_chars = cfg["units"]["min_chars"]
    keep_refs = cfg["units"]["include_references_section"]
    keep_caps = cfg["units"]["keep_captions"]
    keep_tables = cfg["units"]["keep_tables"]

    units: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    def flush():
        if not pending:
            return
        first, last = pending[0], pending[-1]
        start, end = first["char_start"], last["char_end"]
        text = full_text[start:end]
        unit = {
            "unit_id": ids.unit_id(len(units) + 1),
            "kind": first["role"] if first["role"] != "body" else "paragraph",
            "section_path": first.get("section_path", []),
            "heading": (first.get("section_path") or [None])[-1],
            "page_index_start": first.get("page_index"),
            "page_index_end": last.get("page_index"),
            "page_label_start": first.get("page_label"),
            "page_label_end": last.get("page_label"),
            "char_start": start,
            "char_end": end,
            "block_ids": [b["block_id"] for b in pending],
            "n_chars": end - start,
            "text": text,
            "sentence_offsets": [list(s) for s in sentences.split_offsets(text, base=start)],
        }
        if first.get("caption_label"):
            unit["caption_label"] = first["caption_label"]
        if first.get("line"):
            unit["line_start"] = first["line"]
        if first.get("anchor"):
            unit["anchor"] = first["anchor"]
        units.append(unit)
        del pending[:]

    for blk in blocks:
        role = blk.get("role")
        if blk.get("char_start") is None:
            continue
        if role == "references":
            if keep_refs:
                flush()
                pending.append(blk)
                flush()
            continue
        if role == "heading":
            flush()
            continue
        if role not in _UNIT_ROLES:
            continue
        if role == "caption" and not keep_caps:
            continue
        if role == "table" and not keep_tables:
            continue
        if role in ("caption", "table", "code", "equation_candidate", "footnote"):
            flush()
            pending.append(blk)
            flush()
            continue
        if pending:
            same_section = pending[0].get("section_path") == blk.get("section_path")
            grown = blk["char_end"] - pending[0]["char_start"]
            if not same_section or grown > max_chars:
                flush()
            elif (pending[-1]["char_end"] - pending[0]["char_start"]) >= min_chars:
                flush()
        pending.append(blk)
        if (blk["char_end"] - pending[0]["char_start"]) >= max_chars:
            flush()
    flush()
    return units


def _quality_report(parse_result, blocks, full_text, units, headings, cfg) -> Dict[str, Any]:
    n_pages = parse_result.get("n_pages") or 0
    warnings = list(parse_result.get("warnings") or [])
    body_chars = sum(
        (b["char_end"] - b["char_start"])
        for b in blocks
        if b.get("char_start") is not None and b.get("role") in ("body", "abstract", "front_matter", "caption")
    )
    per_page: Dict[int, int] = {}
    for blk in blocks:
        if blk.get("char_start") is None or blk.get("page_index") is None:
            continue
        if blk.get("role") in ("header", "footer", "page_number"):
            continue
        per_page[blk["page_index"]] = per_page.get(blk["page_index"], 0) + (blk["char_end"] - blk["char_start"])
    poor_pages = [p for p in range(n_pages) if per_page.get(p, 0) < cfg["parsing"]["min_chars_per_page"]]
    poor_fraction = (len(poor_pages) / float(n_pages)) if n_pages else 0.0
    control_hits = len(_CONTROL_RE.findall("".join(b["text"] for b in blocks)))
    cid_hits = full_text.count("(cid:")
    replacement = full_text.count("�")
    alnum = sum(1 for c in full_text if c.isalnum())
    alnum_ratio = alnum / float(max(1, len(full_text)))

    quality = "good"
    needs_ocr = False
    if len(full_text) < 200:
        quality = "empty"
        needs_ocr = bool(n_pages)
    elif n_pages and poor_fraction >= cfg["parsing"]["poor_page_fraction"]:
        quality = "poor"
        needs_ocr = True
    elif cid_hits > 20 or replacement > len(full_text) * 0.01 or alnum_ratio < 0.55:
        quality = "poor"
        needs_ocr = cid_hits > 20
    elif poor_pages or alnum_ratio < 0.65:
        quality = "fair"

    if quality == "empty":
        warnings.append(
            "no extractable text (%d pages): the file is image-only or its fonts carry no "
            "Unicode mapping. Enable parsing.ocr in the KB config (requires pdftoppm + "
            "tesseract) or supply a text version. NOTHING has been inferred about its content."
            % n_pages)
    elif quality == "poor":
        warnings.append(
            "text extraction is poor (%d/%d text-poor pages, %d cid-artefacts): claims from "
            "this paper must be treated as low confidence until OCR or a better source is used."
            % (len(poor_pages), n_pages, cid_hits))
    if not headings:
        warnings.append("no section headings detected: section paths will be empty and "
                        "locators fall back to page + character offsets")
    if n_pages and not any(b.get("role") == "references" for b in blocks):
        warnings.append("no reference section detected: reference text may be mixed into the body")

    return {
        "adapter": parse_result["adapter"],
        "adapter_version": parse_result["adapter_version"],
        "doc_kind": parse_result["doc_kind"],
        "n_pages": parse_result.get("n_pages"),
        "n_blocks": len(blocks),
        "n_units": len(units),
        "n_chars": len(full_text),
        "n_body_chars": body_chars,
        "n_headings": len(headings),
        "headings": headings[:200],
        "sections": [h["text"] for h in headings if h["level"] == 1][:60],
        "roles": _count_roles(blocks),
        "text_quality": quality,
        "needs_ocr": needs_ocr,
        "poor_pages": poor_pages[:50],
        "alnum_ratio": round(alnum_ratio, 3),
        "cid_artefacts": cid_hits,
        "control_chars_removed": control_hits,
        "extraction_method": parse_result["adapter"],
        "layout_signal": "bbox+font" if any(b.get("bbox") for b in blocks) else "text-only",
        "warnings": warnings,
    }


def _count_roles(blocks) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for blk in blocks:
        out[blk.get("role") or "unknown"] = out.get(blk.get("role") or "unknown", 0) + 1
    return out
