"""Markdown and plain-text parsing.

Markdown: ATX/setext headings give a real section hierarchy; fenced code and
tables are preserved as single blocks.  Plain text: blank-line paragraphs, with
conservative heading detection (short, unpunctuated, numbered or upper-case lines).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

NAME = "text"
VERSION = "text-stdlib-1"
_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(\S.{0,90})$")


def available() -> bool:
    return True


def can_handle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".md", ".markdown", ".txt", ".text")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False
    if stripped.endswith((".", ";", ",", ":")) and not _NUMBERED.match(stripped):
        return False
    if _NUMBERED.match(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / float(len(letters)) > 0.8:
        return True
    return False


def parse(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ext = os.path.splitext(path)[1].lower()
    is_md = ext in (".md", ".markdown")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read().replace("\r\n", "\n")
    blocks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    order = 0
    doc_meta: Dict[str, Any] = {}

    if is_md and text.startswith("---"):
        from .. import miniyaml

        try:
            fm, body = miniyaml.split_frontmatter(text)
            if isinstance(fm, dict):
                doc_meta = {
                    "embedded_title": fm.get("title"),
                    "embedded_author": (
                        "; ".join(fm["authors"]) if isinstance(fm.get("authors"), list) else fm.get("author")
                    ),
                    "embedded_doi": fm.get("doi"),
                    "embedded_date": str(fm.get("date")) if fm.get("date") else None,
                }
                text = body
        except Exception as exc:
            warnings.append("YAML frontmatter ignored: %s" % exc)

    def emit(chunk: str, level, kind: str, line_no: int):
        nonlocal order
        chunk = chunk.strip()
        if not chunk:
            return
        blocks.append(
            {
                "page_index": None, "page_label": None, "bbox": None,
                "page_width": None, "page_height": None, "text": chunk,
                "font_size": None, "bold": bool(level),
                "style_hint": ("heading%d" % level) if level else kind,
                "order": order, "n_lines": chunk.count("\n") + 1, "line": line_no,
            }
        )
        order += 1

    lines = text.split("\n")
    buf: List[str] = []
    buf_start = 1
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if is_md and line.strip().startswith("```"):
            if not in_fence:
                emit("\n".join(buf), None, "paragraph", buf_start)
                buf = [line]
                buf_start = i + 1
                in_fence = True
            else:
                buf.append(line)
                emit("\n".join(buf), None, "code_block", buf_start)
                buf = []
                buf_start = i + 2
                in_fence = False
            i += 1
            continue
        if in_fence:
            buf.append(line)
            i += 1
            continue
        atx = _ATX.match(line) if is_md else None
        setext = (
            is_md and buf and i + 0 < len(lines) and re.match(r"^(=+|-{2,})\s*$", line) and len("".join(buf).strip()) < 90
        )
        if atx:
            emit("\n".join(buf), None, "paragraph", buf_start)
            buf = []
            emit(atx.group(2), len(atx.group(1)), "heading", i + 1)
            buf_start = i + 2
        elif setext:
            level = 1 if line.strip().startswith("=") else 2
            emit("\n".join(buf), level, "heading", buf_start)
            buf = []
            buf_start = i + 2
        elif not line.strip():
            emit("\n".join(buf), None, "paragraph", buf_start)
            buf = []
            buf_start = i + 2
        elif not is_md and not buf and _looks_like_heading(line):
            emit(line, 2, "heading", i + 1)
            buf_start = i + 2
        else:
            if not buf:
                buf_start = i + 1
            buf.append(line)
        i += 1
    emit("\n".join(buf), None, "paragraph", buf_start)
    if not blocks:
        warnings.append("file contains no text")
    return {
        "adapter": NAME, "adapter_version": VERSION,
        "doc_kind": "markdown" if is_md else "text",
        "n_pages": None, "page_labels": [], "blocks": blocks,
        "pages_text": [text], "doc_meta": doc_meta, "toc": [], "warnings": warnings,
    }
