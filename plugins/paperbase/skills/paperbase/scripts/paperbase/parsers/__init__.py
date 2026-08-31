"""Parser adapters.

Every adapter is a module exposing:

    NAME, VERSION            identity recorded in the stage fingerprint
    can_handle(path) -> bool
    parse(path, cfg) -> dict with keys:
        adapter, adapter_version, doc_kind, n_pages, page_labels,
        blocks: [{page_index, page_label, bbox|None, text, font_size, bold,
                  style_hint, order}],
        pages_text: [str]          (used only for text-quality metrics)
        doc_meta: {title, authors, ...}   embedded metadata, may be empty
        warnings: [str]

The knowledge schema depends only on this contract, so a richer parser (Docling,
GROBID, an OCR pipeline) can be dropped in later without touching anything
downstream.  Adapter identity + version participate in staleness detection, so
swapping adapters correctly re-parses affected papers.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from . import docx_adapter, html_adapter, pdf_pymupdf, pdf_pypdf, text_adapter

ALL = [pdf_pymupdf, pdf_pypdf, html_adapter, docx_adapter, text_adapter]
BY_NAME = {m.NAME: m for m in ALL}


class UnsupportedDocument(Exception):
    """Raised when no adapter can handle a file; carries an actionable reason."""


def select(path: str, cfg: Dict[str, Any]) -> List[Any]:
    """Ordered list of adapters to try for this path."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        order = cfg["parsing"]["pdf_adapters"]
        chosen = []
        for name in order:
            mod = BY_NAME.get(name)
            if mod is None:
                raise UnsupportedDocument(
                    "config parsing.pdf_adapters names unknown adapter %r; known: %s"
                    % (name, sorted(BY_NAME))
                )
            if mod.available():
                chosen.append(mod)
        if not chosen:
            raise UnsupportedDocument(
                "no PDF adapter available. Install PyMuPDF (`pip install pymupdf`) or "
                "pypdf, or set parsing.pdf_adapters in the KB config."
            )
        return chosen
    for mod in ALL:
        if mod.can_handle(path) and mod.available():
            return [mod]
    raise UnsupportedDocument(
        "no adapter handles extension %r. Supported: .pdf .html .htm .xhtml .docx "
        ".md .markdown .txt .text" % ext
    )
