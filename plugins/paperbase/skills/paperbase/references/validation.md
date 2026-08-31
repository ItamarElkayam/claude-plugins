# Validation, human corrections, tests

```bash
python3 scripts/kb.py validate $KB [--fast] [--all] [--json]
```

Exit code 0 = pass, 2 = errors. Reports go to `reports/validation_report.md|json`.
`--fast` skips excerpt verification (no text reads) for a quick structural check.

## Checks

| Finding | Meaning | Default |
|---|---|---|
| `schema_error` | an artifact violates its schema (or an override file is malformed) | error |
| `missing_locator` | a claim has no locator, or a document-grounded profile statement has none | error |
| `broken_locator` | locator points at a missing paper/unit, an out-of-range char span, or a page that disagrees with its unit | error |
| `excerpt_mismatch` | the quoted excerpt does not occur in `docs/<pid>/text.txt` | error |
| `dangling_reference` | a claim/paper/topic id referenced anywhere does not exist; a source file has vanished | error |
| `broken_link` | a Markdown link inside the KB does not resolve | error |
| `unsupported_synthesis` | a synthesis point cites no claim ids; a contradiction lists no alternative explanations | warning |
| `duplicate_claim` | near-identical statements within a paper without `duplicate_of` | warning |
| `unapplied_override` | an override whose target id does not exist | warning |
| `stale_stage` | analysis older than its inputs; or an interrupted run in `state/journal.json` | warning |
| `poor_parse` | `text_quality` is `poor`/`empty` — claims from it are suspect | warning |
| `ocr_excerpt` | a locator rests on OCR text, so the quote is not exact | warning |

Move any finding between the two lists with `validation.fail_on` / `warn_on` in the KB
config. The stats block reports how many locators were checked and how many excerpts
verified — a KB with claims but zero verified excerpts is broken even if nothing errors.

## Human corrections

`<kb>/overrides/*.yaml` (or `.json`), schema `schemas/overrides.schema.json`, merged in
filename order. They are applied **after** automated processing on every build, so they
cannot be lost by regeneration, and each corrected field records
`metadata_provenance.<field>.source = "human_override"`.

```yaml
papers:
  li-2015-bfc-correcting-illumina-27989a:
    metadata: {year: 2015, venue: "Bioinformatics"}
    topics: ["overcorrection"]
    notes: "year corrected from the journal landing page"
  irrelevant-2019-marketing-deck-aa11bb:
    exclude: true                # keep the file, drop it from the KB

claims:
  li-2015-bfc-correcting-illumina-27989a#c04-4f9c02:
    conditions: ["k=55", "16 threads"]     # patch any field
    notes: "the extraction dropped the k-mer size"
  some-paper-2020-x-ab12cd#c09-112233:
    delete: true                            # remove a wrong claim

families:                                   # correct a document-family link
  - {source_paper_id: anon-2014-bfc-supp-c53a8b, target_paper_id: li-2015-bfc-correcting-illumina-27989a,
     relation: supplement-of, reason: "same tool, supplementary notes"}

duplicates:                                 # resolve a duplicate deliberately
  - {keep: paper-a-2020-x-aaa111, drop: paper-a-2020-x-bbb222, reason: "same PDF twice", same_work: true}

relations:                                  # author or delete a relation
  - {source_paper_id: kallenborn-2022-care-2-0-b0d618, target_paper_id: li-2015-bfc-correcting-illumina-27989a,
     relation: cites, rationale: "BFC is in CARE 2.0's reference list", confidence: 1.0}

terminology:
  synonyms: {"overcorrection": ["false-positive correction", "FP correction"]}
```

Overridden claims are flagged `human_override` and `reviewed_by_human`; human relations get
`origin: human_override` and win over heuristics. An override naming an id that no longer
exists is listed in the build report *and* by `kb validate` — never silently discarded. The
YAML subset supported is documented in `scripts/paperbase/miniyaml.py`; parse errors name the
line and are refused rather than ignored.

## Tests

```bash
python3 tests/run_tests.py [-k <substring>] [-v] [--keep]
```

27 tests, stdlib only, no network, no model calls. Fixtures are generated at runtime
(`tests/fixtures.py`): a three-page two-column PDF with headings, a table caption, running
headers, printed folios and a reference list; a second PDF that disagrees with the first on
different data; an **image-only PDF**; HTML with `citation_*` metadata plus script/nav
boilerplate; a real `.docx`; Markdown with frontmatter; plain text; a companion `.zip`; and
one unsupported format.

Coverage by layer:

- **unit** — YAML subset round-trip and line-numbered errors; all shipped schemas load and
  reject malformed instances (including unknown-keyword schemas); id stability and
  DOI-preference; excerpt matching across reflow/ligatures/control characters and the
  negative case; sentence splitting; config unknown-key rejection; prompt include expansion.
- **extraction** — section hierarchy, running-header removal, folio recognition, reference
  cut-off, caption units, two-column reading order, unit offsets matching `text.txt`; every
  declared format parses and unsupported ones are reported with a reason; HTML boilerplate
  never enters the body; the image-only PDF yields zero units, `needs_ocr`, and a warning
  that says nothing was inferred.
- **pipeline** — full layer set produced; sources provably unmodified; building inside the
  paper directory refused; **no-op update re-parses nothing and requests no model work**;
  modifying a paper marks exactly its own analyses stale (and the corpus stages) while
  leaving other papers current; rename preserves ids and knowledge; removal tombstones,
  deletes derived artifacts, retires relations and still validates; adding a paper queues
  only it; **overrides survive regeneration** and unknown ones are reported.
- **knowledge** — ingest rejects missing locators, bad enums, missing derivations and
  unverifiable excerpts (and validation then fails); synthesis must cite existing claim ids;
  contradictions must list alternative explanations; work orders carry prompt, schema, units
  and response path.
- **retrieval** — a single-paper factual query returns the right claim with a resolving
  locator; a cross-paper disagreement query returns both sides, labels synthesis, discloses
  strategy and honours its budget; filters exclude other papers' passages and claims while
  disclosing that counterevidence is not filtered; the evaluation harness scores a real pack.
- **setup + tuning** — `doctor` reports Python/FTS5/PDF-backend/OCR state; installs are
  skipped when the corpus needs nothing and are opt-out-able with an actionable message;
  acronyms, variants and vocabulary are mined from units (not reference lists); the human
  config template is fully commented out so mined values apply, human values win, `{}`
  suppresses, and typos are still rejected; a recurring body-size heading is adopted,
  re-segments the corpus once, then settles with no oscillation, and a human veto reverses
  it; mined acronyms expand queries in both directions without matching inside other words.
- **robustness** — a failed write leaves the previous version intact and no temp files; an
  interrupted stage is reported by `validate`.

## Evaluation questions

Keep a corpus-specific set at `<kb>/eval/questions.yaml`:

```yaml
questions:
  - id: q1-single-paper-fact
    question: "Does BFC correct insertion and deletion errors, and why?"
    expect_papers: ["li-2015-bfc-correcting-illumina-27989a"]
    expect_claims: ["li-2015-bfc-correcting-illumina-27989a#c10-daa65a"]
    must_include_terms: ["INDEL-aware", "rare in Illumina data"]
    expect_counterevidence: false
    budget: 4000
```

Optional thresholds per question: `min_paper_recall`, `min_claim_recall`,
`min_term_coverage`, `min_locator_validity` (all default 1.0). Run `kb eval` after any
prompt, parser, schema or config change: it is what catches a KB that validates cleanly and
still answers badly.
