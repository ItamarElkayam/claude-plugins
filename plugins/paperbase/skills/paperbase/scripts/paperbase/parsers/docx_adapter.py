"""DOCX parsing with zipfile + ElementTree (python-docx not required).

A .docx is an OPC zip: word/document.xml holds paragraphs whose w:pStyle carries
heading levels, and docProps/core.xml holds title/author/date.  Reading it
directly avoids a dependency that is not installable in this environment.
"""
from __future__ import annotations

import os
import re
import zipfile
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

NAME = "docx"
VERSION = "docx-stdlib-1"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"


def available() -> bool:
    return True


def can_handle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".docx"


def _para_text(node) -> str:
    parts = []
    for child in node.iter():
        if child.tag == W + "t":
            parts.append(child.text or "")
        elif child.tag in (W + "tab",):
            parts.append("\t")
        elif child.tag == W + "br":
            parts.append("\n")
    return re.sub(r"[ \t\xa0]+", " ", "".join(parts)).strip()


def parse(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    blocks: List[Dict[str, Any]] = []
    doc_meta: Dict[str, Any] = {}
    try:
        zf = zipfile.ZipFile(path)
    except Exception as exc:
        raise RuntimeError("not a readable .docx (OPC zip) %s: %s" % (os.path.basename(path), exc))
    with zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            raise RuntimeError("%s has no word/document.xml - not a Word document" % os.path.basename(path))
        try:
            root = ET.fromstring(zf.read("word/document.xml"))
        except Exception as exc:
            raise RuntimeError("malformed word/document.xml: %s" % exc)
        body = root.find(W + "body")
        order = 0
        for node in list(body) if body is not None else []:
            tag = node.tag
            if tag == W + "p":
                style = ""
                ppr = node.find(W + "pPr")
                if ppr is not None:
                    pstyle = ppr.find(W + "pStyle")
                    if pstyle is not None:
                        style = (pstyle.get(W + "val") or "").lower()
                text = _para_text(node)
                if not text:
                    continue
                level = None
                match = re.match(r"heading\s*(\d)", style)
                if match:
                    level = int(match.group(1))
                elif style in ("title",):
                    level = 1
                blocks.append(
                    {
                        "page_index": None, "page_label": None, "bbox": None,
                        "page_width": None, "page_height": None, "text": text,
                        "font_size": None, "bold": bool(level),
                        "style_hint": ("heading%d" % level) if level else ("caption" if style.startswith("caption") else "paragraph"),
                        "order": order, "n_lines": text.count("\n") + 1,
                    }
                )
                order += 1
            elif tag == W + "tbl":
                rows = []
                for tr in node.findall(W + "tr"):
                    cells = [_para_text(tc) for tc in tr.findall(W + "tc")]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    blocks.append(
                        {
                            "page_index": None, "page_label": None, "bbox": None,
                            "page_width": None, "page_height": None,
                            "text": "\n".join(rows), "font_size": None, "bold": False,
                            "style_hint": "table", "order": order, "n_lines": len(rows),
                        }
                    )
                    order += 1
        if "docProps/core.xml" in names:
            try:
                core = ET.fromstring(zf.read("docProps/core.xml"))
                doc_meta = {
                    "embedded_title": (core.findtext(DC + "title") or "").strip() or None,
                    "embedded_author": (core.findtext(DC + "creator") or "").strip() or None,
                    "embedded_date": (core.findtext(CP + "lastPrinted") or core.findtext(DC + "date") or "").strip() or None,
                    "embedded_keywords": (core.findtext(CP + "keywords") or "").strip() or None,
                }
            except Exception as exc:
                warnings.append("docProps/core.xml unreadable: %s" % exc)
    if not blocks:
        warnings.append("no paragraphs recovered from .docx")
    return {
        "adapter": NAME, "adapter_version": VERSION, "doc_kind": "docx",
        "n_pages": None, "page_labels": [], "blocks": blocks,
        "pages_text": [" ".join(b["text"] for b in blocks)],
        "doc_meta": doc_meta, "toc": [], "warnings": warnings,
    }
