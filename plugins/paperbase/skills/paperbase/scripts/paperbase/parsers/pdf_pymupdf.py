"""Structure-aware PDF parsing via PyMuPDF (fitz).

Extracts per-block text with page index, printed page label, bounding box, font
size and boldness - the raw signal that extract.py turns into sections, running
headers, captions and body paragraphs.  Tables are attempted with
`page.find_tables()` when the installed PyMuPDF supports it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

NAME = "pymupdf"
try:  # pragma: no cover - import guard
    import fitz  # type: ignore

    VERSION = "pymupdf-%s" % (getattr(fitz, "__version__", None) or getattr(fitz, "VersionBind", "?"))
    _OK = True
except Exception:  # pragma: no cover
    fitz = None
    VERSION = "pymupdf-unavailable"
    _OK = False


def available() -> bool:
    return _OK


def can_handle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


def _page_label(doc, page, index: int) -> str:
    for getter in (lambda: page.get_label(), lambda: doc.get_page_labels()):
        try:
            value = getter()
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(index + 1)


def parse(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    blocks: List[Dict[str, Any]] = []
    pages_text: List[str] = []
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise RuntimeError("PyMuPDF could not open %s: %s" % (os.path.basename(path), exc))
    if getattr(doc, "needs_pass", False):
        doc.close()
        raise RuntimeError("PDF is password protected: %s" % os.path.basename(path))

    order = 0
    labels: List[str] = []
    for index in range(doc.page_count):
        try:
            page = doc.load_page(index)
        except Exception as exc:
            warnings.append("page %d could not be loaded: %s" % (index + 1, exc))
            pages_text.append("")
            labels.append(str(index + 1))
            continue
        label = _page_label(doc, page, index)
        labels.append(label)
        rect = page.rect
        try:
            data = page.get_text("dict")
        except Exception as exc:
            warnings.append("page %d text extraction failed: %s" % (index + 1, exc))
            pages_text.append("")
            continue
        page_chars = 0
        for blk in data.get("blocks", []):
            if blk.get("type") != 0:
                continue  # image block; recorded via image counts below
            lines_out: List[str] = []
            sizes: List[float] = []
            bold_hits = 0
            span_count = 0
            for line in blk.get("lines", []):
                parts = []
                for span in line.get("spans", []):
                    txt = span.get("text", "")
                    if not txt:
                        continue
                    parts.append(txt)
                    sizes.append(float(span.get("size", 0.0)))
                    span_count += 1
                    font = (span.get("font") or "").lower()
                    if "bold" in font or "black" in font or "semibold" in font:
                        bold_hits += 1
                if parts:
                    lines_out.append("".join(parts))
            text = "\n".join(lines_out).strip()
            if not text:
                continue
            page_chars += len(text)
            bbox = blk.get("bbox", (0, 0, 0, 0))
            blocks.append(
                {
                    "page_index": index,
                    "page_label": label,
                    "bbox": [round(float(v), 2) for v in bbox],
                    "page_width": round(float(rect.width), 2),
                    "page_height": round(float(rect.height), 2),
                    "text": text,
                    "font_size": round(max(sizes), 2) if sizes else None,
                    "bold": bool(span_count and bold_hits >= max(1, span_count // 2)),
                    "style_hint": None,
                    "order": order,
                    "n_lines": len(lines_out),
                }
            )
            order += 1
        try:
            n_images = len(page.get_images(full=True))
        except Exception:
            n_images = 0
        if page_chars < 20 and n_images:
            warnings.append(
                "page %d has %d image(s) and almost no text - likely a scanned page (OCR required)"
                % (index + 1, n_images)
            )
        # tables (best-effort; API present from PyMuPDF 1.23)
        try:
            finder = page.find_tables()
            for ti, table in enumerate(getattr(finder, "tables", []) or []):
                rows = table.extract()
                cells = [
                    " | ".join("" if c is None else str(c).replace("\n", " ").strip() for c in row)
                    for row in rows
                    if any(c for c in row)
                ]
                if len(cells) < 2:
                    continue
                blocks.append(
                    {
                        "page_index": index,
                        "page_label": label,
                        "bbox": [round(float(v), 2) for v in getattr(table, "bbox", (0, 0, 0, 0))],
                        "page_width": round(float(rect.width), 2),
                        "page_height": round(float(rect.height), 2),
                        "text": "\n".join(cells),
                        "font_size": None,
                        "bold": False,
                        "style_hint": "table",
                        "order": order,
                        "n_lines": len(cells),
                    }
                )
                order += 1
        except Exception:
            pass

    # real per-page text for quality metrics
    pages_text = []
    for index in range(doc.page_count):
        joined = " ".join(b["text"] for b in blocks if b["page_index"] == index and b["style_hint"] != "table")
        pages_text.append(joined)

    meta = doc.metadata or {}
    doc_meta = {
        "embedded_title": (meta.get("title") or "").strip() or None,
        "embedded_author": (meta.get("author") or "").strip() or None,
        "embedded_subject": (meta.get("subject") or "").strip() or None,
        "embedded_keywords": (meta.get("keywords") or "").strip() or None,
        "embedded_creation_date": (meta.get("creationDate") or "").strip() or None,
        "producer": (meta.get("producer") or "").strip() or None,
    }
    n_pages = doc.page_count
    try:
        toc = doc.get_toc() or []
    except Exception:
        toc = []
    doc.close()
    return {
        "adapter": NAME,
        "adapter_version": VERSION,
        "doc_kind": "pdf",
        "n_pages": n_pages,
        "page_labels": labels,
        "blocks": blocks,
        "pages_text": pages_text,
        "doc_meta": doc_meta,
        "toc": [{"level": t[0], "title": t[1], "page": t[2]} for t in toc][:200],
        "warnings": warnings,
    }
