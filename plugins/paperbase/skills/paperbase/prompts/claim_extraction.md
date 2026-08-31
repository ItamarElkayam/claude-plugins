# Atomic claim extraction

Turn one paper into independently referenceable claims. A claim is the unit a future agent
retrieves, compares across papers, and cites. Prose summaries cannot be compared; claims can.

## Input

The paper's retrieval units (`u0001`, …) plus its existing research profile.

## Output

A JSON **array** of objects satisfying `schemas/claim.schema.json`. JSON only.

```json
[
  {
    "statement": "On the 384-sample simulated E. coli set, CARE 2.0 produced 3.2x fewer false-positive corrections than bfc at matched true-positive rate.",
    "claim_type": "performance_comparison",
    "evidence_status": "reported",
    "polarity": "affirmative",
    "subject": "simulated E. coli 30x Illumina reads",
    "intervention": "CARE 2.0 (classifier-gated correction)",
    "comparator": "bfc",
    "outcome": "false-positive corrections",
    "effect": {"direction": "decrease", "magnitude": "3.2x fewer", "uncertainty": "not reported",
               "statistical_test": null, "significant": null},
    "conditions": ["simulated data", "30x coverage", "matched true-positive rate"],
    "qualifiers": ["single dataset"],
    "evidence_type": "benchmark",
    "is_primary_evidence": true,
    "concepts": ["false-positive correction"], "methods": ["CARE 2.0", "bfc"],
    "datasets": ["simulated E. coli"],
    "support": [{"unit_id": "u0042", "excerpt": "verbatim quote", "extraction_method": "pymupdf",
                 "confidence": 0.9}],
    "confidence": 0.9
  }
]
```

`claim_id`, `paper_id`, `section_origin` and `generated_by` are filled in by `kb ingest`.

## What to extract

Aim for the paper's **load-bearing** assertions, typically 8–25 for a research article:

- every primary quantitative result (one claim per comparison that matters);
- mechanism claims (*why* the method works) — `claim_type: mechanism`;
- method properties (complexity, memory, runtime, assumptions);
- dataset properties;
- negative, null and "no difference" results — **never drop these**;
- stated limitations (`claim_type: limitation`);
- recommendations the authors make;
- important background assertions the paper *takes from other work* — with
  `is_primary_evidence: false` and `cited_source` set to the citation as printed.

Do **not** create claims for: section headings, boilerplate, funding, formatting artefacts,
or restatements of the same fact (one claim, best evidence).

## Rules

1. **Self-contained.** A claim must be readable alone: no "it", "this method", "the above".
   Name the system, the data and the metric inside `statement`.
2. **One assertion per claim.** "Faster and more accurate" is two claims.
3. **Qualifiers survive.** If the paper says "may", "tends to", "under our settings",
   "on simulated data", the claim says so too — in `statement`, `conditions` or `qualifiers`.
   Turning a hedged claim into an unconditional one is the most damaging error you can make here.
4. **`evidence_status`:**
   - `reported` — the paper states this result.
   - `quoted` — statement is essentially the paper's own sentence.
   - `calculated` — you did arithmetic on reported numbers. Put the arithmetic in `derivation`.
   - `inferred` — your reading of the paper's evidence. Put your reasoning in `derivation`.
     Use sparingly; an inferred claim is never presented as the authors' conclusion.
5. **`polarity`:** `negative` for "X did not help", `null_result` for "no significant
   difference", `mixed` for "helped here, hurt there".
6. **Statistics as printed.** `uncertainty` gets the CI/sd/p-value verbatim, or
   `"not reported"` — which is itself valuable information. Set `significant` only if the
   paper says so.
7. **Support is mandatory and verbatim.** At least one locator per claim, with `unit_id`
   and an exact `excerpt` from that unit. Multiple locators when the evidence spans a
   sentence plus a table row. Excerpts are machine-verified against the source text.
8. **Tables and figures:** cite the caption unit and quote the caption verbatim. If a value
   exists only inside a figure image, do not invent it — say so in the profile's
   `extraction_warnings` and skip the claim.
9. **`confidence`** is *your certainty that the paper says this*, not how much you believe
   the science. Poorly extracted text ⇒ lower confidence.
