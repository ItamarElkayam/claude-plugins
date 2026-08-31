---
name: paperbase
description: Turn a directory of scientific papers (PDF, HTML, DOCX, Markdown, text) into a durable, incrementally maintainable knowledge base optimized for AI-agent context - structure-aware extracted text with exact source locators, per-paper research profiles, atomic claims with evidence, cross-paper relations, topic and corpus syntheses, and a local SQLite FTS index. Use when asked to build, update, query, validate or extend such a knowledge base from a paper directory, or when a question needs evidence-grounded context from a paper corpus with citations back to specific pages and passages.
---

# paperbase

Expensive extraction and analysis happen **once**; afterwards agents pull compact,
citation-carrying context instead of re-reading every paper.

Two halves, kept strictly apart:

- **`scripts/kb.py` — deterministic.** Parsing, identity, normalization, manifests,
  indexing, retrieval, validation. **Never calls a model.**
- **The analysis stages — model work.** `kb tasks` emits a self-contained work order
  (prompt + input + schema + response path); you (Claude) do the science; `kb ingest`
  validates and installs it. One work order = one model call you chose to make.

## Quickstart

Nothing to install or configure first - `build` sets up what it needs and tunes itself
to the corpus (see **First run**, below).

```bash
KB=path/to/kb                                   # anywhere OUTSIDE the paper directory
python3 scripts/kb.py build path/to/papers $KB  # set up, parse, normalize, tune, index
python3 scripts/kb.py status $KB                # what exists, what is stale, what is pending
python3 scripts/kb.py tasks  $KB --stage profile --limit 3   # emit work orders
#   read each tasks/profile/<paper_id>.order.md, write JSON to the response path, then:
python3 scripts/kb.py ingest $KB --stage profile --model-id claude-opus-5
python3 scripts/kb.py validate $KB              # schema + provenance + locator + link checks
python3 scripts/kb.py context  $KB "<question>" --budget 8000
```

`kb --help` and `kb <command> --help` list everything. Commands: `doctor`, `build`,
`update`, `status`, `tasks`, `ingest`, `search`, `context`, `show-paper`, `show-claim`,
`tune`, `validate`, `reindex`, `eval`.

## First run (automatic)

`kb build` does two setup jobs so a new user has nothing to read first:

1. **Dependencies.** If the corpus contains PDFs and no PDF backend is importable, it
   installs one (PyMuPDF, else pypdf) into a **private directory** -
   `~/.cache/paperbase/pylibs` via `pip --target`. System and user site-packages are never
   touched, no sudo, externally-managed Pythons are fine, and `rm -rf ~/.cache/paperbase`
   undoes it. Each install is announced and recorded in `state/audit.jsonl`. Opt out with
   `--no-install` or `PAPERBASE_NO_INSTALL=1`; you then get the exact pip command to run.
   `kb doctor` reports the environment (Python, sqlite FTS5, PDF backend, OCR tools)
   without building anything.
2. **Corpus tuning.** After parsing, it mines this corpus's own vocabulary - acronyms and
   their expansions ("multiple sequence alignment (MSA)"), spelling variants
   (`k-mer`/`kmer`), frequent multi-word terms, and section headings your venue uses that
   are not canonical ("Author summary") - and writes them to `config/derived.yaml`.
   Query expansion in `kb search`/`kb context` uses them immediately. Re-run any time with
   `kb tune`; see `reports/tuning_report.md` for exactly what was learned.

**`config/derived.yaml` is machine-written and layered UNDER your `config/config.yaml`.**
Your file starts fully commented out, so mined values apply until you override them; setting
a key there always wins, and setting it to `[]`/`{}` suppresses the mined value.

## What the KB contains

Canonical knowledge is plain files (Markdown / JSONL / YAML); `index/kb.sqlite` is a
derived accelerator, rebuildable with `kb reindex`.

```
KB_INDEX.md                  navigation entry point, written for agents
corpus/papers.jsonl          normalized metadata, one row per paper, with provenance
docs/<paper_id>/text.txt     normalized text - THE canonical character-offset space
docs/<paper_id>/blocks.jsonl every block with role (body/heading/caption/table/
                             reference/header/footer/page_number) + section path
docs/<paper_id>/units.jsonl  retrieval units: section path, page label, char span, sentences
docs/<paper_id>/parse.json   adapter, quality, needs_ocr, warnings
config/config.yaml           yours (starts fully commented); always wins
config/derived.yaml          mined vocabulary + venue headings; regenerate with `kb tune`
papers/<paper_id>/profile.json   structured research profile (schema-validated)
papers/<paper_id>/claims.jsonl   atomic claims, each with >=1 verified source locator
papers/<paper_id>/paper.md       rendered view of the above
relations/                   relations.jsonl (model+human), families.jsonl, tombstoned.jsonl
synthesis/                   CORPUS_OVERVIEW.md, topics/, TERMINOLOGY.md, indexes/
overrides/                   human corrections - authoritative, never regenerated
state/                       manifest.json (stage fingerprints), audit.jsonl, journal.json
reports/                     build/update, validation and evaluation reports
```

## The workflow

1. **`kb build <papers> <kb>`** — installs any missing backend for the formats present,
   discovers every file (parsed, catalogued-as-archive, or unsupported-with-a-reason),
   parses structure-aware text, assigns stable ids, mines the corpus vocabulary, links
   document families, writes the index and `KB_INDEX.md`. Source papers are never modified;
   building inside the paper directory is refused.
2. **`kb status`** — per-paper `profile`/`claims` freshness (`missing`/`stale`/`current`) and
   which corpus stages need regenerating.
3. **`kb tasks --stage profile|claims|relations|topics|overview`** — writes work orders.
   Paper stages are per paper; corpus stages take the whole claim set.
4. **Do the analysis** following the embedded prompt, then write the JSON answer to the
   stated response path. → `references/scientific-analysis.md`
5. **`kb ingest --stage <stage> --model-id <model>`** — validates against the schema,
   resolves every locator (finding the excerpt in the source text *proves* the quote),
   stamps provenance, records the fingerprint, refreshes index and views. Anything invalid
   is rejected in full with an actionable message — nothing partial is written.
6. **`kb validate`** — see `references/validation.md`.
7. **Query:** `kb search` (lexical, filterable), `kb context` (budgeted evidence pack),
   `kb show-paper`, `kb show-claim`. → `references/retrieval-and-context.md`
8. **`kb update`** — after papers change. Unchanged content is never re-parsed and a no-op
   update does **zero** model work. → `references/ingestion-workflow.md`

## Non-negotiable rules

- **Never invent.** Missing information is `null` / `unavailable` / a warning, never a guess.
  No fabricated findings, numbers, methods, metadata or citations.
- **Every claim carries a locator** (`paper_id`, unit, page, section, char span, verbatim
  excerpt). Excerpts are machine-verified against `docs/<paper_id>/text.txt`; an
  unverifiable quote is an error, not a rounding difference.
- **Label the source of every statement.** `author_stated` / `shown_by_result` /
  `agent_inferred` (needs a rationale) / `unavailable`; synthesis is rendered under
  `[SYNTHESIS]`. Never present an inference as an author's conclusion.
- **Keep the qualifications.** "may", "in our setting", "not statistically significant",
  "on simulated data" survive into the claim. Negative and null results are preserved.
- **A citation is not agreement.** Be conservative with `contradicts`: check population,
  data, preprocessing, design, metric, tool version and power first, and record what you
  checked. → `prompts/contradiction_analysis.md`
- **Distinguish primary evidence from repetition.** A statement found only in an
  introduction is that paper citing someone else (`is_primary_evidence: false`).
- **Human overrides win** and survive every rebuild; an override that no longer resolves is
  reported, never silently dropped.
- **Say what is missing.** Corpus-level statements must name what the corpus does not cover.

## Read next

| File | When |
|---|---|
| [references/knowledge-model.md](references/knowledge-model.md) | layers, identity rules, schemas, the dependency graph, design decisions |
| [references/ingestion-workflow.md](references/ingestion-workflow.md) | build/update semantics, parser adapters, formats, OCR, families, duplicates, config |
| [references/scientific-analysis.md](references/scientific-analysis.md) | how to run the five model stages well; the optional LLM adapter |
| [references/retrieval-and-context.md](references/retrieval-and-context.md) | search, filters, context-pack composition and budgets, semantic extension |
| [references/validation.md](references/validation.md) | every check, the override mechanism, tests and evaluation |
| `schemas/*.json` | the contracts (`locator`, `paper_profile`, `claim`, `relation`, `topic`, `corpus_overview`, `terminology`, `overrides`) |
| `prompts/*.md` | the analysis prompts actually used, versioned by content hash |
| `config/defaults.yaml` | every setting, documented; copied into each KB as `config/config.yaml` |

## Environment

- **Python 3.9+ is the only prerequisite.** PDF support needs PyMuPDF (or pypdf), which the
  first run installs into `~/.cache/paperbase/pylibs` if missing - nothing else is required,
  and non-PDF formats work with the standard library alone. Ships a minimal YAML-subset
  reader/writer and a small JSON-Schema validator instead of adding dependencies.
- **Local-first.** No network, no external service, no hosted database. External metadata
  enrichment and OCR are opt-in adapters; embeddings are an optional accelerator.
- `python3 tests/run_tests.py` runs the suite (27 tests, generates its own fixtures,
  no network and no model calls).
