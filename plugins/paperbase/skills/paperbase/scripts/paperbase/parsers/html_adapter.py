"""HTML / XHTML parsing with stdlib html.parser (no bs4/lxml required).

Produces heading and paragraph blocks with a DOM-ish anchor path instead of page
numbers, dropping non-content regions (script, style, nav, aside, figure toolbars)
so boilerplate never enters the scientific body.
"""
from __future__ import annotations

import html
import os
import re
from html.parser import HTMLParser
from typing import Any, Dict, List

NAME = "html"
VERSION = "html-stdlib-1"
_BLOCK_TAGS = {"p", "li", "blockquote", "pre", "dd", "dt", "figcaption", "caption", "td", "th"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_DROP = {"script", "style", "nav", "noscript", "svg", "form", "button", "header", "footer", "aside"}


def available() -> bool:
    return True


def can_handle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".html", ".htm", ".xhtml")


class _Collector(HTMLParser):
    def __init__(self) -> None:
        HTMLParser.__init__(self, convert_charrefs=True)
        self.blocks: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {}
        self._stack: List[str] = []
        self._buf: List[str] = []
        self._current = None  # (tag, level)
        self._drop_depth = 0
        self._in_title = False
        self._anchor = None
        self._row: List[str] = []

    def handle_starttag(self, tag, attrs):
        attrd = dict(attrs)
        if tag in _DROP:
            self._drop_depth += 1
            return
        if tag == "meta":
            name = (attrd.get("name") or attrd.get("property") or "").lower()
            if name and attrd.get("content"):
                self.meta.setdefault(name, attrd["content"].strip())
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in ("section", "div", "article") and attrd.get("id"):
            self._anchor = attrd["id"]
        if tag in _HEADINGS or tag in _BLOCK_TAGS:
            self._flush()
            self._current = (tag, _HEADINGS.get(tag))
            if attrd.get("id"):
                self._anchor = attrd["id"]
        if tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in ("tr",):
            if self._row:
                self.blocks.append(self._make(" | ".join(self._row), None, "table_row"))
                self._row = []
            return
        if self._current and tag == self._current[0]:
            self._flush()

    def handle_data(self, data):
        if self._drop_depth:
            return
        if self._in_title:
            self.meta.setdefault("title", data.strip())
            return
        if self._current is not None:
            self._buf.append(data)

    def _make(self, text: str, level, kind: str) -> Dict[str, Any]:
        return {
            "page_index": None,
            "page_label": None,
            "bbox": None,
            "page_width": None,
            "page_height": None,
            "text": text,
            "font_size": None,
            "bold": False,
            "style_hint": ("heading%d" % level) if level else kind,
            "order": len(self.blocks),
            "n_lines": text.count("\n") + 1,
            "anchor": self._anchor,
        }

    def _flush(self):
        if self._current is None:
            self._buf = []
            return
        tag, level = self._current
        text = html.unescape("".join(self._buf))
        text = re.sub(r"[ \t\xa0]+", " ", text).strip()
        self._buf = []
        self._current = None
        if not text:
            return
        if tag in ("td", "th"):
            self._row.append(text)
            return
        kind = "caption" if tag in ("figcaption", "caption") else ("list_item" if tag == "li" else "paragraph")
        self.blocks.append(self._make(text, level, kind))

    def close(self):
        HTMLParser.close(self)
        self._flush()


def parse(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    collector = _Collector()
    warnings: List[str] = []
    try:
        collector.feed(raw)
        collector.close()
    except Exception as exc:
        warnings.append("HTML parse aborted early: %s" % exc)
    authors = []
    for key in ("citation_author", "dc.creator", "author"):
        if collector.meta.get(key):
            authors.append(collector.meta[key])
    doc_meta = {
        "embedded_title": collector.meta.get("citation_title") or collector.meta.get("title"),
        "embedded_author": "; ".join(authors) or None,
        "embedded_doi": collector.meta.get("citation_doi"),
        "embedded_journal": collector.meta.get("citation_journal_title"),
        "embedded_date": collector.meta.get("citation_publication_date") or collector.meta.get("citation_date"),
        "embedded_keywords": collector.meta.get("keywords"),
    }
    if not collector.blocks:
        warnings.append("no textual blocks recovered from HTML (script-rendered page?)")
    return {
        "adapter": NAME,
        "adapter_version": VERSION,
        "doc_kind": "html",
        "n_pages": None,
        "page_labels": [],
        "blocks": collector.blocks,
        "pages_text": [" ".join(b["text"] for b in collector.blocks)],
        "doc_meta": doc_meta,
        "toc": [],
        "warnings": warnings,
    }
