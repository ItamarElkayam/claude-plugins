# Knowledge model

## Why multiple layers

A single large summary loses evidence; a vector store alone loses structure and exactness;
a knowledge graph alone loses the prose an agent needs to judge a result. paperbase keeps
seven layers, each derivable from the one above it, each independently useful:

| Layer | Artifact | Produced by | Purpose |
|---|---|---|---|
| 0 Sources | the original files, untouched (optionally copied to `sources/`) | you | ground truth |
| 1 Normalized text | `docs/<pid>/text.txt` | parser + `extract.py` | **the canonical character-offset space** |
| 2 Structure | `docs/<pid>/blocks.jsonl` | `extract.py` | roles, section paths, pages, bboxes |
| 3 Retrieval units | `docs/<pid>/units.jsonl` | `extract.py` | paragraph/caption/table granularity with sentence offsets |
| 4 Per-paper analysis | `papers/<pid>/profile.json` | model stage `profile` | what the paper investigated, how, and found |
| 5 Atomic claims | `papers/<pid>/claims.jsonl` | model stage `claims` | independently retrievable assertions with evidence |
| 6 Cross-paper | `relations/*.jsonl` | `families` (deterministic) + model stage `relations` | agreement, disagreement, reuse, families |
| 7 Synthesis | `synthesis/topics/*`, `CORPUS_OVERVIEW.*`, `TERMINOLOGY.*`, `indexes/*` | model stages `topics`/`overview`, plus deterministic entity indexes | field-level orientation |

`index/kb.sqlite` (FTS5) is **layer -1**: a derived accelerator over layers 3-7. Deleting it
loses nothing; `kb reindex` rebuilds it. The same is true of any future embedding cache.

## Document identity

```
paper_id = <lastname>-<year>-<title-slug>-<disc6>
disc6    = sha256(identity_key)[:6]
```

`identity_key` is, in order of preference:

1. `doi:<normalized doi>` — lower-cased, prefix- and punctuation-stripped, used only when
   DOI confidence >= 0.6;
2. `arxiv:<id>`, `pmid:<id>`, `pmcid:<id>`;
3. `meta:<author>|<year>|<normalized title>|<content hash[:16]>`.

Consequences, all deliberate:

- the id is **readable** (agents and humans can recognise it) yet collision-safe;
- it does **not** depend on the rest of the corpus, so adding or removing papers never
  renames anything;
- the same DOI in two formats yields the same id; two different documents without usable
  metadata never collapse into one;
- if a re-parse changes the identity basis (e.g. a DOI becomes detectable), the old id is
  **tombstoned** and the change is reported — ids are never silently reused;
- byte-identical files are recognised as one document with both paths recorded
  (`duplicate_source_paths`), and near-duplicates become a `possible-duplicate-of`
  relation for a human to resolve. Nothing is merged automatically.

Recorded per paper (`corpus/papers.jsonl`): title, authors, year, venue, DOI/arXiv/PMID,
document type, publication status, local path, content hash, size, identity basis,
per-field `metadata_provenance` (`source` + `confidence`), parse report (adapter, version,
pages, units, text quality, `needs_ocr`, warnings), family links, topics, analysis state.

## Source locators

Every claim, profile statement and relation carries locators (`schemas/locator.schema.json`):

```json
{"paper_id": "li-2015-bfc-correcting-illumina-27989a", "source_filename": "BFC.pdf",
 "unit_id": "u0012", "block_id": "b00031", "page_index": 1, "page_label": "2886",
 "section_path": ["2 Methods"], "sentence_index": 3, "char_start": 6013, "char_end": 6134,
 "excerpt": "We have not implemented this INDEL-aware algorithm because such errors are rare",
 "extraction_method": "pymupdf", "confidence": 0.95, "warnings": []}
```

- `char_start`/`char_end` index into `docs/<paper_id>/text.txt` — nothing else.
- The **excerpt is the proof**: `kb ingest` locates it in the source text (tolerant of
  reflow, ligatures, smart quotes and extraction control characters, but not of paraphrase)
  and fills in the remaining fields from the containing unit. If it cannot be found, the
  locator is marked and `kb validate` reports `excerpt_mismatch`.
- Non-paginated sources (HTML, Markdown, text, DOCX) use `section_path` + `line_start` +
  `anchor` + character offsets instead of pages.
- OCR-derived locators are stamped `extraction_method: "ocr"` and are never treated as exact.

## Dependency graph and fingerprints

A stage is stale **iff** its recorded fingerprint differs from the freshly computed one.
Modification times are never used.

```
parse    <- content hash, parser adapter + version, parsing config, pipeline version
units    <- parse, units config
metadata <- parse, metadata config
profile  <- units, metadata, paper_analysis prompt hash, model id, schema version
claims   <- profile, claim_extraction prompt hash, model id, schema version
families <- metadata of ALL papers                    (deterministic, always recomputed)
relations<- claims of ALL papers, relation prompt, model
topics   <- claims of ALL papers + relations, topic prompt, model
overview <- topics + relations, overview prompt, model
index    <- units + claims + relations + topics + profiles    (derived, cheap)
```

**Honest limitation:** `relations`, `topics` and `overview` depend on the *whole* claim set.
When one paper changes, paperbase marks them stale and reports it; it does not pretend a
purely local patch is correct. What it can do cheaply is scope the *work*: a relation pass
after adding one paper only needs the new paper's claims compared against existing ones, and
`kb tasks --stage relations` hands you the full claim table so you can do exactly that.
Topic and overview regeneration is genuinely global whenever the changed claims are cited by
them — `kb update` tells you which topics reference now-missing claim ids.

## Schemas

| Schema | Governs |
|---|---|
| `locator.schema.json` | the provenance object above; requires `unit_id` or a char range |
| `paper_profile.schema.json` | the research profile; every statement has a `basis`, inferences need a `rationale` |
| `claim.schema.json` | atomic claims; `support` must be non-empty; `calculated`/`inferred` need a `derivation` |
| `relation.schema.json` | 19 relation types; contradictions require `difference_explanations` |
| `topic.schema.json` | topic synthesis; every grounded point needs `claim_ids` |
| `corpus_overview.schema.json` | consensus, contradictions, gaps, foundational papers, dependencies |
| `terminology.schema.json` | canonical terms, synonyms, and *conflicting* definitions per paper |
| `overrides.schema.json` | the human correction file format |

Validated by `scripts/paperbase/jsonschema_lite.py` — a strict draft-07 subset that
**errors on unknown schema keywords** rather than ignoring them, so a schema can never
appear to check more than it does.

## Design decisions

1. **Files are canonical, SQLite is derived.** Portable, greppable, diffable, git-friendly;
   no service to run; nothing important lives only in a database.
2. **Char offsets into a stored normalized text**, not into the PDF. PDF text extraction is
   not reproducible across tools or versions; a stored `text.txt` makes every locator
   verifiable forever and makes excerpt checking a byte comparison.
3. **Parser behind an adapter interface** (`scripts/paperbase/parsers/`). PyMuPDF today;
   Docling, GROBID or an OCR pipeline can be added without touching the knowledge schema,
   and adapter identity + version participate in staleness so a swap re-parses correctly.
4. **Deterministic code never calls a model.** Cost stays visible and the pipeline is
   testable offline; the reasoning stages are prompts + schemas, executable by Claude Code
   directly or by an optional adapter.
5. **Readable ids with a hashed discriminator** rather than opaque hashes or corpus-position
   counters: greppable in prose, stable under corpus change.
6. **Claims are first-class.** Prose summaries cannot be compared across papers, deduplicated,
   filtered by confidence, or attached to relations; claims can.
7. **Generated knowledge is kept separate from normalized source text** (`papers/` vs
   `docs/`), and human corrections separate from both (`overrides/`).
