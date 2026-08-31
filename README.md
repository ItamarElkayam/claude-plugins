# paperbase-tools

A Claude Code plugin marketplace containing **paperbase**: turn a directory of scientific
papers (PDF, HTML, DOCX, Markdown, text) into a durable, incrementally maintainable
knowledge base optimized for use as AI-agent context.

## Install

```
/plugin marketplace add <owner>/<repo>
/plugin install paperbase@paperbase-tools
```

Then ask Claude to build a knowledge base from a paper directory, or use the CLI directly:

```bash
kb=~/my-kb
python3 <plugin>/skills/paperbase/scripts/kb.py build ~/papers $kb
python3 <plugin>/skills/paperbase/scripts/kb.py context $kb "<question>" --budget 8000
```

## What it produces

Structure-aware extracted text with exact source locators (paper, section, page, character
range, verbatim excerpt), per-paper research profiles, atomic claims whose every quote is
machine-verified against the source, cross-paper relations (agreement, contradiction with
the alternative explanations checked, reuse, document families), topic and corpus syntheses
grounded in claim ids, a local SQLite FTS index, and incremental updates driven by content
hashes and stage fingerprints.

Canonical knowledge is plain Markdown/JSONL/YAML; the search index is derived and
rebuildable. No external services, no required third-party Python packages (PyMuPDF is used
for PDFs when present).

Full documentation: `plugins/paperbase/skills/paperbase/SKILL.md` and its `references/`.

## Requirements

- Python 3.9+
- **PyMuPDF** (`pip install pymupdf`) for layout-aware PDF parsing. Without it, `pypdf` is
  used as a fallback (no section detection) and other formats work unchanged.

## Tests

```bash
python3 plugins/paperbase/skills/paperbase/tests/run_tests.py
```
