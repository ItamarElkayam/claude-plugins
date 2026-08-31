# Retrieval and agent navigation

Four ways in, in increasing order of "just answer my question":

```bash
python3 scripts/kb.py search     $KB "<terms>" [--kind units|claims|papers|topics|all]
python3 scripts/kb.py show-paper $KB <paper_id | id-prefix | doi | title fragment>
python3 scripts/kb.py show-claim $KB <claim_id>
python3 scripts/kb.py context    $KB "<question>" --budget 8000 [--out pack.md]
```

All four accept `--json`. An agent that knows nothing else about the KB can start with
`KB_INDEX.md` and `kb context`.

## Lexical search

SQLite FTS5 with BM25 over four indexes: `units` (text/heading/section),
`claims` (statement/facets/tags), `papers` (title/authors/abstract/keywords + retrieval
terms) and `topics`. Natural-language questions are tokenised into quoted OR-terms so exact
identifiers survive (`SRR065390`, `BFC r181`, `F1`, `10.1093/bib/bbv029`); `--raw` passes an
FTS5 expression through unchanged (`"k-mer" NEAR/5 spectrum`).

Lexical-first is a deliberate choice: scientific questions turn on exact tool names,
accessions, metrics and versions, which embeddings blur. Query expansion comes from
`terminology.synonyms` in the config plus the corpus terminology map, so "overcorrection"
also finds "false-positive correction".

Filters (`kb search` and `kb context`): `--paper`, `--exclude-paper`, `--year`, `--type`,
`--author`, `--method`, `--dataset`, `--concept`, `--claim-type`, `--min-confidence`,
`--primary-only` (only claims that are the paper's own primary evidence).

**Semantic retrieval** is an extension point, not a dependency: no embedding model is
installed here, and embeddings would be a rebuildable cache beside `index/`, hybrid-ranked
with BM25 and never the canonical copy. The `search.py` seams (`search_units`,
`search_claims`) are where a reranker would attach.

## `kb context` — the evidence pack

Composition, in order, each section budgeted:

1. **Topic synthesis** — the most relevant topic(s), labelled `[SYNTHESIS]`, truncated with a
   pointer rather than dropped when the budget is tight.
2. **Relevant claims** — statement, type, evidence status, polarity, effect size,
   uncertainty, conditions, qualifiers, confidence, and each supporting locator with its
   verbatim excerpt.
3. **Counterevidence, qualifications and negative results** — a reserved share of the claim
   budget (`retrieval.min_counterevidence_share`) spent on claims linked by
   `contradicts`/`partially-contradicts`/`qualifies`/`fails-to-reproduce`/
   `studies-different-context`, plus negative and null results from the papers in the pack.
   When both sides of a disagreement are already above, the pack says so explicitly. When
   there is none, it says *"absence of recorded disagreement is not consensus"* rather than
   implying agreement.
4. **Source passages** — the best-matching units, capped per paper
   (`retrieval.max_units_per_paper`) so one paper cannot fill the pack, each headed by its
   citation.
5. **Papers in this pack** — ordered by how much evidence each contributed; bibliographic
   line, text quality, study type, data sources with sizes, metrics, scope conditions and
   stated limitations.
6. **Cross-paper relations** — with rationale, origin, confidence and, for contradictions,
   the alternative explanations that were checked.
7. **Where to look next** — further units per paper, the files holding the full text, papers
   *not* in the pack, and the exact `show-paper`/`show-claim` commands.
8. **Retrieval strategy and warnings** — the FTS query, synonym expansion, filters,
   candidate counts, budget accounting, and every warning (thin claim coverage, poor text
   quality, truncation, no counterevidence found).

Citations look like `[paper_id §Section p.2886 u0012 c6013-6134]`: paper, section path,
printed page, unit, character range in `docs/<paper_id>/text.txt`.

## Budget behaviour

`--budget` is a hard ceiling on the delivered pack, not a suggestion. ~18% is reserved for
structure; sections may borrow unused budget from each other (bounded at 1.5× their share);
if the assembled pack still overshoots, optional blocks are dropped lowest-value-first
(extra passages → profiles/relations → surplus claims), always keeping the two
best-matching claims and one piece of counterevidence, and the drop is disclosed in-pack. If
even the minimum useful pack exceeds the budget, it says so rather than silently truncating
the evidence.

Filters intentionally do **not** apply to the topic synthesis or to counterevidence —
filtering those away is how a pack becomes one-sided — and the pack states this whenever a
filter is active.

## Evaluation

`kb eval $KB [questions.yaml]` scores real packs against declared expectations: paper
recall, claim recall, required-term coverage, citation coverage, locator validity (every
cited claim's excerpts must still verify against the source text), counterevidence presence
and budget adherence. Reports land in `reports/eval_report.md|json`. Keep a question set per
corpus at `<kb>/eval/questions.yaml` and re-run it after any prompt, parser or config change
— it is the cheapest guard against a KB that looks fine and retrieves badly.
