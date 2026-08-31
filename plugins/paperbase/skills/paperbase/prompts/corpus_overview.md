# Corpus overview: consensus, contradictions, gaps, directions

Write the top-level map of the field as this corpus represents it — the page an agent reads
first. Everything rests on claim ids; nothing is asserted from background knowledge.

## Input

All papers, all claims, all relations, all topic syntheses.

## Output

One JSON object satisfying `schemas/corpus_overview.schema.json`. JSON only.

## Required content and its rules

- `field_summary`: what this literature is about and what it collectively establishes.
- `scope_note`: **what this corpus does not cover.** State the boundaries (years, organisms,
  platforms, tasks, languages, missing subfields) so downstream agents do not read absence of
  evidence as evidence of absence.
- `topic_map`: topics and subtopics with paper ids.
- `strong_agreement` / `qualified_agreement`: with `evidence_strength` and claim ids. Anything
  resting on one paper is `single_study`, never "established".
- `genuine_contradictions`: only after the contradiction checklist.
{{include: contradiction_analysis.md}}
- `apparent_contradictions_explained`: the reconciliations, with the dimension that differs.
- `evidence_strength_assessment`: for the corpus's main claims, how strong the evidence
  actually is (how many independent studies, real vs simulated data, reported uncertainty).
- `common_limitations` and `failure_modes`: what repeatedly goes wrong in this literature.
- `understudied_conditions`, `open_questions`, `research_gaps`:
{{include: gap_analysis.md}}
- `foundational_papers` and `field_changing_papers`: with a `why` grounded in claim ids. Judge
  from the corpus itself (what other papers build on, compare against, or reuse), not from
  outside reputation — and say so when the corpus is too small to judge.
- `dependencies`: which methods/datasets/findings depend on which others.
- `warnings`: anything a consumer of this overview must know — thin coverage of a subtopic,
  papers with poor text extraction, a topic represented only by its own authors' work.
