"""Fallback PDF parsing via pypdf (no layout signal: page-level text only).

Used when PyMuPDF is unavailable.  Because it yields no font sizes or bounding
boxes, section detection degrades to text-pattern heuristics and every parse is
marked with a `layout_signal: none` warning so downstream confidence reflects it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

NAME = "pypdf"
pypdf = None
VERSION = "pypdf-unavailable"


def _load() -> bool:
    global pypdf, VERSION
    if pypdf is not None:
        return True
    try:
        import pypdf as _pypdf  # type: ignore
    except Exception:
        return False
    pypdf = _pypdf
    VERSION = "pypdf-%s" % getattr(pypdf, "__version__", "?")
    return True


def available() -> bool:
    return _load()


def can_handle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


def parse(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not _load():
        raise RuntimeError("pypdf is not importable")
    warnings = ["layout_signal: none (pypdf fallback - no font sizes or bounding boxes)"]
    try:
        reader = pypdf.PdfReader(path)
    except Exception as exc:
        raise RuntimeError("pypdf could not open %s: %s" % (os.path.basename(path), exc))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            raise RuntimeError("PDF is password protected: %s" % os.path.basename(path))
    blocks: List[Dict[str, Any]] = []
    pages_text: List[str] = []
    labels: List[str] = []
    order = 0
    for index, page in enumerate(reader.pages):
        label = str(index + 1)
        labels.append(label)
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.append("page %d text extraction failed: %s" % (index + 1, exc))
            text = ""
        pages_text.append(text)
        for chunk in [c.strip() for c in text.split("\n\n") if c.strip()]:
            blocks.append(
                {
                    "page_index": index,
                    "page_label": label,
                    "bbox": None,
                    "page_width": None,
                    "page_height": None,
                    "text": chunk,
                    "font_size": None,
                    "bold": False,
                    "style_hint": None,
                    "order": order,
                    "n_lines": chunk.count("\n") + 1,
                }
            )
            order += 1
    info = {}
    try:
        info = reader.metadata or {}
    except Exception:
        pass
    return {
        "adapter": NAME,
        "adapter_version": VERSION,
        "doc_kind": "pdf",
        "n_pages": len(reader.pages),
        "page_labels": labels,
        "blocks": blocks,
        "pages_text": pages_text,
        "doc_meta": {
            "embedded_title": (info.get("/Title") or "").strip() or None if info else None,
            "embedded_author": (info.get("/Author") or "").strip() or None if info else None,
        },
        "toc": [],
        "warnings": warnings,
    }
