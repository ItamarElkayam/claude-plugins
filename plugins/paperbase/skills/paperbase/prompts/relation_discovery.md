# Cross-paper relation discovery

Find and justify the relationships between papers and between individual claims. This is
what turns a pile of per-paper analyses into a knowledge base an agent can reason over.

## Input

All papers (bibliographic records) and all extracted claims, plus the relations that already
exist (deterministic document-family links and any human-authored relations).

## Output

A JSON **array** of objects satisfying `schemas/relation.schema.json`. JSON only. Emit only
new or corrected relations; existing ones need not be repeated.

```json
[
  {
    "source_paper_id": "...", "target_paper_id": "...",
    "source_claim_id": "...#c04-1a2b3c", "target_claim_id": "...#c07-9f8e7d",
    "relation": "partially-contradicts",
    "dimension": "false-positive rate on real Illumina data",
    "rationale": "Both measure FP corrections on real E. coli reads; A reports a reduction, B reports no improvement over the same baseline.",
    "difference_explanations": ["different coverage (30x vs 10x)", "different FP definition (per-read vs per-base)"],
    "evidence_source": [{"unit_id": "u0042", "excerpt": "verbatim", "extraction_method": "pymupdf", "confidence": 0.9}],
    "evidence_target": [{"unit_id": "u0031", "excerpt": "verbatim", "extraction_method": "pymupdf", "confidence": 0.9}],
    "confidence": 0.7,
    "origin": "inferred_by_analysis"
  }
]
```

## Relation vocabulary

`supports`, `contradicts`, `partially-contradicts`, `qualifies`, `extends`, `reproduces`,
`fails-to-reproduce`, `applies`, `improves-on`, `compares-with`, `uses-method-from`,
`uses-data-from`, `shares-dataset`, `addresses-same-question`, `studies-different-context`,
`cites`.

## Rules

1. **A citation is not agreement.** `cites` says only that one paper references another. Never
   upgrade it to `supports` without comparing what the two actually claim.
2. **State the dimension.** Two papers only agree or disagree *about something*: a metric, a
   dataset, an organism, a coverage regime, a definition. Fill `dimension` always.
3. **Evidence from both sides.** A relation between claims needs a locator on each side.
4. **Be conservative with `contradicts`.**
{{include: contradiction_analysis.md}}
5. **Prefer the weaker, more precise relation.** `qualifies`, `studies-different-context` and
   `partially-contradicts` are usually more accurate than `contradicts`.
6. **Confidence** reflects how sure you are that the relation holds as described: 0.9 for an
   author-stated relation with matching claims, 0.5–0.7 for a careful inference, below 0.5
   only when the relation is a genuinely useful hypothesis — and say so in the rationale.
7. **Also record the useful mechanical relations:** `uses-method-from`, `uses-data-from`,
   `shares-dataset`, `improves-on`, `addresses-same-question`. These make the dependency
   structure of a field visible and are cheap to verify.
8. **Never relate a paper to itself**, and never assert a relation to a paper_id that is not
   in the input list.
