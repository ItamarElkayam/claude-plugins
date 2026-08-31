# Ingestion and incremental update

## First-run setup

### Dependencies

`kb build` checks what the corpus actually needs before parsing: a directory of Markdown
and HTML never triggers an install, a directory containing PDFs does. When a needed backend
is missing it is installed with

```
python3 -m pip install --target ~/.cache/paperbase/pylibs 'pymupdf>=1.23,<2'
```

- **private by construction**: `--target` keeps it out of system and user site-packages, so
  nothing else on the machine changes and no sudo or virtualenv is required (this is also
  why externally-managed Pythons, e.g. Homebrew or Debian, are unaffected);
- the directory is put on `sys.path` by `paperbase/__init__.py`, and adapters import their
  backend lazily so an install that happens mid-run takes effect immediately;
- overridable location: `PAPERBASE_HOME`; removable with `rm -rf ~/.cache/paperbase`;
- announced on stderr and recorded in `state/audit.jsonl` as `dependency_installed`;
- opt out with `--no-install` or `PAPERBASE_NO_INSTALL=1` - you then get the exact command
  to run by hand, and the pipeline continues with whatever capability remains.

`kb doctor [<kb>]` prints the whole picture without building: Python version, sqlite
version and whether FTS5 is compiled in (without it, search cannot run), the PDF backend in
use, the private library directory, and the optional OCR tools. Exit code 2 means something
required is missing. Given a KB path it only checks what that corpus needs.

### Corpus tuning

Hand-tuning terminology is the step everyone skips, so it is automatic. After parsing,
`kb build` mines the corpus and writes `config/derived.yaml`:

| Mined | How | Used for |
|---|---|---|
| acronyms | `expansion words (ACRO)` where the letters are the initials - a compact Schwartz-Hearst match, high precision | bidirectional query expansion (`MSA` ↔ `multiple sequence alignment`) |
| spelling variants | surface forms differing only in hyphens/spaces/case (`k-mer`, `kmer`) | query expansion |
| corpus vocabulary | 2-3 word phrases frequent **and** spread across papers | `KB_INDEX.md` orientation, `terminology.priority_terms` |
| venue section headings | short title-like blocks recurring across papers that the font/numbering heuristics missed ("Author summary") | segmentation |

Mining reads the **retrieval units**, not the raw text, so reference-list journal names
never become corpus vocabulary and what is mined matches what search actually sees.

Adopting a section heading changes segmentation, so `kb build` re-parses the corpus **once**
and says so. Adoption is monotonic: an adopted heading is then parsed as a heading and would
otherwise vanish from the candidate pool on the next run, flip-flopping the segmentation, so
the derived list only ever grows. Nothing else in tuning triggers a re-parse.

Inspect with `kb tune <kb> --show` (writes nothing) or `reports/tuning_report.md`.
Re-mine with `kb tune <kb>`. Disable with `--no-tune`, or per-feature under `tuning:` in
your config.

### Configuration layering

```
config/defaults.yaml (shipped)  <-  <kb>/config/derived.yaml (mined)  <-  <kb>/config/config.yaml (yours)
```

Your file is applied last and **starts fully commented out**. That matters: a full
uncommented copy would pin every key to its default and silently override everything mined.
So an untouched KB uses defaults + mined values; uncommenting a key takes control of it; and
setting a key to `[]` or `{}` suppresses the mined value for that key alone. Unknown keys are
rejected by name in both files - `terminology.synonyms` and `terminology.acronyms` are
free-form maps whose keys are data, not schema.

## `kb build` / `kb update`

Both run the same code path; `build` is `update` against an empty KB.

```bash
python3 scripts/kb.py build  <paper-dir> <kb-dir> [--force] [--copy-sources] [-v|-q]
python3 scripts/kb.py update <kb-dir> [<paper-dir>] [--force]
python3 scripts/kb.py reindex <kb-dir>          # rebuild the derived index + views only
```

Steps: discover → classify → (re)parse what is stale → normalize + unitize → extract
metadata → assign identity → apply human overrides → link document families → retire
dangling relations → write the corpus manifest → rebuild the index → render `KB_INDEX.md`,
`papers/*/paper.md`, syntheses and reports → save fingerprints and audit the run.

**Guarantees.** The paper directory is opened read-only and building a KB inside it is
refused. Every canonical write is atomic (temp file + `os.replace`), so an interrupt leaves
either the old or the new version; multi-file stages journal their intent in
`state/journal.json` and `kb status`/`kb validate` report an interrupted run. Every run
appends to `state/audit.jsonl` with counts, versions, config hash and elapsed time.

## Change detection

Driven by **content hashes**, never mtimes:

| Situation | Behaviour |
|---|---|
| unchanged content, unchanged fingerprints | nothing re-parsed, no model work; a no-op update is fast and quiet |
| same content, different path | **rename**: id, claims and profile preserved, path updated |
| changed content | re-parse; that paper's `profile`/`claims` go **stale**; corpus stages marked stale |
| new file | parsed; queued for analysis; corpus stages marked stale |
| deleted file | **tombstoned**: id retained with a reason, derived artifacts deleted, relations moved to `relations/tombstoned.jsonl` |
| byte-identical twin | one document, both paths recorded, warning emitted |
| identity basis changed by a re-parse | old id tombstoned, new id created, change reported |
| parser/prompt/model/schema/config/pipeline version changed | affected stages go stale automatically |

The per-KB config is authoritative for that corpus: changing the skill's shipped
`config/defaults.yaml` affects only keys the KB has not pinned. Uncomment a key in
`<kb>/config/config.yaml` to take control of it — the config hash changes and the affected
stages re-run.

## Parser adapters

`scripts/paperbase/parsers/` — each module exposes `NAME`, `VERSION`, `available()`,
`can_handle(path)`, `parse(path, cfg)` and returns blocks with page index, page label,
bbox, font size and boldness. The knowledge schema depends only on that contract.

| Format | Adapter | Notes |
|---|---|---|
| PDF | `pymupdf` (preferred), `pypdf` (fallback) | fallback has no layout signal and is marked `layout_signal: none`; encrypted PDFs fail with a clear message |
| HTML/XHTML | `html` (stdlib `html.parser`) | `citation_*` metadata read; `script`/`style`/`nav`/`aside`/`header`/`footer` dropped |
| DOCX | `docx` (zipfile + ElementTree) | heading levels from `w:pStyle`, tables flattened, core properties read |
| Markdown | `text` | YAML frontmatter, ATX/setext headings, fenced code preserved |
| Plain text | `text` | blank-line paragraphs, conservative heading detection |
| Archives (`.zip`, `.tar`, …) | — | catalogued with a content listing, never parsed; unpack them if you want the contents as knowledge |
| Anything else | — | listed in `state/manifest.json` `unsupported` with an actionable reason and surfaced in `KB_INDEX.md` |

Why not Docling or GROBID by default: neither is installable in this environment, both add
a heavy dependency (and GROBID a service) for gains that mostly matter on reference parsing.
The adapter interface is the hedge — add one when it earns its keep, and affected papers
re-parse automatically because adapter version is part of the fingerprint.

## What extraction actually does

- **Running headers/footers**: text repeated in the top/bottom band on >= 40% of pages
  (min 3 pages) is marked `header`/`footer` and kept out of the body. Digits-only band
  blocks become `page_number`, and a consistent run of them is adopted as printed page
  labels.
- **Reading order**: per page, full-width blocks act as band separators; within a band,
  blocks are ordered by column then vertical position, so two-column papers read correctly
  and full-width tables stay in place.
- **Section hierarchy**: canonical section names, numbered headings (`3.2 Results`), font
  size relative to the body median, and boldness. Journals that emit "Heading\nfirst
  paragraph" as one block are split (without this, BMC-style PDFs lose their entire
  hierarchy and reference cut-off).
- **References**: everything after a `References`/`Bibliography` heading is role
  `references` — searchable at block level, excluded from retrieval units by default
  (`units.include_references_section`), so a reference title is never mistaken for a claim.
- **Captions, tables, footnotes, equations**: `Table 3.`/`Fig. 2` captions become their own
  units with a `caption_label`; PyMuPDF tables are extracted when available; small-font
  footer blocks become `footnote`; symbol-dense short blocks are flagged
  `equation_candidate` (a heuristic, deliberately labelled as such).
- **Normalization**: de-hyphenation across line breaks, whitespace folding, and removal of
  PDF control/private-use glyph artefacts (these silently break excerpt matching if kept;
  the count removed is recorded in `parse.json`).
- **Quality assessment**: characters per page, alphanumeric ratio, `(cid:` artefacts and
  empty-page fraction produce `text_quality: good|fair|poor|empty` plus `needs_ocr`. An
  image-only PDF yields **zero units and an explicit warning** — never invented content.

## OCR

Opt-in, off by default (`parsing.ocr.enabled`), requiring `pdftoppm` and `tesseract` on
`PATH`; the commands are configurable. OCR text is marked `extraction_method: "ocr"`, gets a
confidence ceiling, and its locators are reported as `ocr_excerpt` by `kb validate` — OCR
output is never presented as an exact quote. Neither tool is installed in this environment,
so enabling OCR here fails with an actionable message rather than silently degrading.

## Metadata and document families

All metadata extraction is offline and every field carries `{source, confidence}`: embedded
document metadata, `citation_*` tags, first-page largest-font block, printed DOI, copyright
and publication lines, `Keywords:` blocks, filename as last resort. External enrichment
(`metadata.enrichment_enabled`, Crossref) is optional, records its own provenance, and is
never required — a locally supplied corpus stays fully usable offline.

Families are inferred conservatively and always with a basis and confidence:
`supplement-of` (filename/title token overlap), `preprint-of` (high title similarity with a
preprint/published status pair — related versions, **not** identical documents),
`possible-duplicate-of` (high title similarity or identical DOI — flagged, never merged).
Confirm or correct any of them in `overrides/*.yaml` (`families:`, `duplicates:`), which
wins over the heuristic and survives every rebuild.

## Configuration

`config/defaults.yaml` documents every key and is copied to `<kb>/config/config.yaml` on
first build. Unknown keys are rejected by name — a typo never silently does nothing. The
corpus-specific parts worth setting early:

- `terminology.synonyms` / `acronyms` / `priority_terms` — drives query expansion; **mined
  automatically**, so set these only to correct or extend what tuning found;
- `parsing.extra_section_headings` — section names peculiar to your venue; also mined
  automatically (set to `[]` to veto);
- `tuning.*` — switch individual miners off, or raise `min_papers` on a large corpus;
- `units.max_chars` / `min_chars` — retrieval granularity;
- `analysis.model_id` — recorded in provenance so a model change invalidates analyses;
- `retrieval.*` — pack sizes, per-paper diversity cap, counterevidence share;
- `validation.fail_on` / `warn_on` — which findings are errors for your corpus.
