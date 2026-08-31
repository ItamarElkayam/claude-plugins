# Topic synthesis

Group the corpus into topics and, for each, write the synthesis a future agent should read
before touching individual papers. Every point must be traceable to claim ids.

## Input

All papers, all claims, all relations.

## Output

A JSON **array** of topic objects satisfying `schemas/topic.schema.json` (2–8 topics for a
small corpus; prefer few, meaningful topics over many thin ones). JSON only.

Optionally also produce a terminology map satisfying `schemas/terminology.schema.json` and
ingest it with `kb ingest <kb> --stage terminology --file <path>`.

## What each topic must contain

- `summary`: what this topic is and what the corpus collectively establishes about it.
  Written as synthesis — it will be rendered under a `[SYNTHESIS]` banner.
- `agreements`: points multiple papers support. Each with `claim_ids` and an
  `evidence_strength` (`strong` = several independent studies agree; `moderate`;
  `single_study`; `weak`; `contested`).
- `qualified_agreements`: agreement that holds only under stated conditions — name them.
- `genuine_disagreements`: incompatible findings on the same dimension, after the
  contradiction checklist has been applied.
- `apparent_disagreements_explained`: results that *look* conflicting but differ because of
  data, metric, population, version or design. Say which. This section is often the most
  valuable page in the whole KB.
- `common_limitations`, `understudied_conditions`, `open_questions`.
- `chronology`: how the ideas developed, with paper ids.
- `comparison_matrix`: when papers report comparable quantities, build the table — columns
  are dimensions (dataset, metric, value, conditions), one row per paper, with `claim_ids`.
  Leave a cell `null` rather than filling it by analogy.

## Rules

1. **No claim id, no point.** Every entry in a grounded list needs at least one `claim_ids`
   entry; `kb validate` fails the KB otherwise.
2. **Do not average across incomparable settings.** If two papers measure different things,
   the honest output is two rows, not one number.
3. **Name the conditions** in every agreement that has them.
4. **Distinguish "no evidence" from "evidence of no effect".**
5. **A single paper is not a consensus.** Mark it `single_study`.
6. **Terminology conflicts matter:** when two papers use the same word differently, record it
   in the terminology map's `conflicting_definitions`, with claim ids.
