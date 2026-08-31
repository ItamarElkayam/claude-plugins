"""Deterministic fixture generation: a tiny multi-format corpus plus knowledge.

Real files are generated (not checked in) so the tests exercise the actual parsers:
a two-column PDF with headings, a table caption and a reference list; an
image-only PDF; HTML with citation metadata and boilerplate; a real .docx (OPC zip
written by hand); Markdown with YAML frontmatter; plain text; a companion zip; and
one unsupported format.
"""
from __future__ import annotations

import os
import zipfile

try:
    import fitz  # type: ignore
    HAVE_FITZ = True
except Exception:  # pragma: no cover
    HAVE_FITZ = False

A = {
    "title": "Alpha: a fast corrector for widget reads",
    "authors": "Ada Alpha, Bob Beta",
    "abstract": ("Summary: Alpha corrects widget reads using a trusted-token filter. "
                 "On real widget data Alpha corrects more errors with fewer overcorrections "
                 "than existing tools, at comparable speed."),
    "intro": ("Widget correction is the process of fixing measurement errors using overlapping "
              "reads. Most tools take a greedy approach and do not revisit decisions. We worried "
              "that greedy search loses accuracy on repeat-rich widgets, so we derived a "
              "non-greedy algorithm."),
    "methods": ("Alpha keeps candidate states in a priority queue and expands the lowest-penalty "
                "state. We evaluated Alpha on 12 million real widget reads at 30x coverage. The "
                "worst-case time complexity is exponential in read length."),
    "results": ("On widget data Alpha reduced false-positive corrections by a factor of 3.1 "
                "relative to Gamma, while true-positive counts were similar. We did not implement "
                "indel correction because indels are rare in widget data."),
    "caption": "Table 1. Correction accuracy on widget data",
    "table": "Tool FP TP Time  Alpha 0.9 98.1 3h  Gamma 2.8 98.4 2h",
}


def _pdf_paper_a(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 70), "Widget Analysis", fontsize=9)
    page.insert_text((60, 100), A["title"], fontsize=17)
    page.insert_text((60, 125), A["authors"], fontsize=11)
    page.insert_text((60, 150), "doi: 10.1234/widget.2019.001", fontsize=9)
    page.insert_text((60, 165), "(c) 2019 Widget Press. Received 1 January 2019; accepted 2 March 2019",
                     fontsize=8)
    page.insert_text((60, 198), "Abstract", fontsize=13)
    page.insert_textbox(fitz.Rect(60, 205, 540, 285), A["abstract"], fontsize=10)
    page.insert_text((60, 313), "1 Introduction", fontsize=13)
    page.insert_textbox(fitz.Rect(60, 320, 290, 560), A["intro"], fontsize=10)
    page.insert_text((310, 313), "2 Methods", fontsize=13)
    page.insert_textbox(fitz.Rect(310, 320, 540, 560), A["methods"], fontsize=10)
    page.insert_text((280, 800), "1", fontsize=8)
    page2 = doc.new_page()
    page2.insert_text((60, 70), "Widget Analysis", fontsize=9)
    page2.insert_text((60, 113), "3 Results", fontsize=13)
    page2.insert_textbox(fitz.Rect(60, 122, 540, 260), A["results"], fontsize=10)
    page2.insert_textbox(fitz.Rect(60, 280, 540, 298), A["caption"], fontsize=9)
    page2.insert_textbox(fitz.Rect(60, 302, 540, 340), A["table"], fontsize=9)
    page2.insert_text((280, 800), "2", fontsize=8)
    page3 = doc.new_page()
    page3.insert_text((60, 70), "Widget Analysis", fontsize=9)
    page3.insert_text((60, 113), "4 Conclusion", fontsize=13)
    page3.insert_textbox(fitz.Rect(60, 122, 540, 220),
                         "Alpha shows that non-greedy search can reduce overcorrection on widget "
                         "reads without a speed penalty. Indel-aware correction remains future work.",
                         fontsize=10)
    page3.insert_text((60, 260), "References", fontsize=13)
    page3.insert_textbox(fitz.Rect(60, 270, 540, 360),
                         "1. Gamma G. Gamma: greedy widget correction. Widget J. 2016.\n"
                         "2. Delta D. On repeat-rich widgets. Widget J. 2017.", fontsize=9)
    page3.insert_text((280, 800), "3", fontsize=8)
    doc.set_metadata({"title": A["title"], "author": A["authors"]})
    doc.save(path)
    doc.close()


def _pdf_paper_b(path: str, changed: bool = False) -> None:
    """Second tool paper: disagrees with A on precision, on different data."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 70), "Widget Analysis", fontsize=9)
    page.insert_text((60, 100), "Gamma 2.0: learned gating for widget correction", fontsize=17)
    page.insert_text((60, 125), "Carl Gamma, Dora Delta", fontsize=11)
    page.insert_text((60, 150), "doi: 10.1234/widget.2022.007", fontsize=9)
    page.insert_text((60, 165), "(c) 2022 Widget Press", fontsize=8)
    page.insert_text((60, 198), "Abstract", fontsize=13)
    page.insert_textbox(
        fitz.Rect(60, 205, 540, 300),
        "Summary: Gamma 2.0 replaces hand-crafted conditions with a learned classifier. "
        "On simulated widget data Gamma 2.0 makes %s fewer false-positive corrections than "
        "Alpha, while Alpha retains the highest true-positive count."
        % ("an order of magnitude" if not changed else "two orders of magnitude"), fontsize=10)
    page.insert_text((60, 333), "1 Methods", fontsize=13)
    page.insert_textbox(fitz.Rect(60, 342, 540, 460),
                        "We simulated 20 million widget reads at 30x coverage with the WSIM "
                        "simulator, which provides error-free ground truth. False positives are "
                        "counted per base. Competing tools were run with default settings.",
                        fontsize=10)
    page.insert_text((60, 493), "2 Discussion", fontsize=13)
    page.insert_textbox(fitz.Rect(60, 502, 540, 620),
                        "Good results on simulated data may not translate to real data because "
                        "simulator error models can differ from real instruments. Low coverage "
                        "was not evaluated.", fontsize=10)
    page.insert_text((60, 663), "References", fontsize=13)
    page.insert_textbox(fitz.Rect(60, 672, 540, 720),
                        "1. Alpha A. Alpha: a fast corrector for widget reads. Widget J. 2019.",
                        fontsize=9)
    doc.save(path)
    doc.close()


def _pdf_image_only(path: str) -> None:
    """A PDF with drawings but no extractable text: must warn, never invent content."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(100, 100, 400, 400), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    page.draw_line(fitz.Point(100, 100), fitz.Point(400, 400))
    doc.save(path)
    doc.close()


def _html(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("""<html><head>
<title>Epsilon: a review of widget correction</title>
<meta name="citation_title" content="Epsilon: a review of widget correction">
<meta name="citation_author" content="Eve Epsilon">
<meta name="citation_doi" content="10.1234/widget.2021.100">
<meta name="citation_journal_title" content="Widget Reviews">
<meta name="citation_publication_date" content="2021/05/01">
<meta name="keywords" content="widget, error correction, review">
<script>var boilerplate = "SCRIPTMARKER must not appear in the body";</script>
<nav>NAVMARKER skip to content</nav>
</head><body>
<h1>Epsilon: a review of widget correction</h1>
<h2>Introduction</h2>
<p>This review surveys widget correction methods. Reported precision differences between tools
are difficult to compare because each paper defines overcorrection differently.</p>
<h2>Open problems</h2>
<p>Low-coverage widget correction remains untested by the tools we surveyed.</p>
<figcaption>Figure 1. Timeline of widget correctors</figcaption>
</body></html>""")


def _docx(path: str) -> None:
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Alpha supplementary methods</w:t></w:r></w:p>
<w:p><w:r><w:t>This supplement lists the exact command lines used for the widget benchmark.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Commands</w:t></w:r></w:p>
<w:p><w:r><w:t>alpha --trusted 3 --threads 16 reads.fq</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Tool</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Flags</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Alpha</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>--trusted 3</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    core = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Alpha supplementary methods</dc:title><dc:creator>Ada Alpha</dc:creator></cp:coreProperties>"""
    ctypes = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/></Types>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("word/document.xml", document)
        zf.writestr("docProps/core.xml", core)


def _markdown(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("""---
title: Widget correction lab notes
authors: ["Ada Alpha"]
date: 2023
doi: 10.1234/widget.2023.notes
---

# Widget correction lab notes

## Observations

Re-running Alpha at 5x coverage produced more false-positive corrections than at 30x,
which no published widget paper reports.

## Next steps

Measure Alpha and Gamma 2.0 on one dataset with one false-positive definition.
""")


def _text(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("""WIDGET CORRECTION MEETING MINUTES

ATTENDEES
Ada Alpha, Carl Gamma.

DECISIONS
The group agreed that precision comparisons across papers are not currently like-for-like.
A shared benchmark with a single false-positive definition is needed.
""")


def build_corpus(paper_dir: str, include_b: bool = True, changed_b: bool = False,
                 include_broken: bool = True) -> None:
    os.makedirs(paper_dir, exist_ok=True)
    if HAVE_FITZ:
        _pdf_paper_a(os.path.join(paper_dir, "alpha_2019.pdf"))
        if include_b:
            _pdf_paper_b(os.path.join(paper_dir, "gamma2_2022.pdf"), changed=changed_b)
        if include_broken:
            _pdf_image_only(os.path.join(paper_dir, "scanned_no_text.pdf"))
    _html(os.path.join(paper_dir, "epsilon_review.html"))
    _docx(os.path.join(paper_dir, "alpha_supplementary.docx"))
    _markdown(os.path.join(paper_dir, "lab_notes.md"))
    _text(os.path.join(paper_dir, "minutes.txt"))
    with zipfile.ZipFile(os.path.join(paper_dir, "alpha_supplement.zip"), "w") as zf:
        zf.writestr("s1.csv", "tool,fp,tp\nalpha,0.9,98.1\n")
    with open(os.path.join(paper_dir, "notes.rtf"), "w", encoding="utf-8") as fh:
        fh.write("{\\rtf1 unsupported format}")
