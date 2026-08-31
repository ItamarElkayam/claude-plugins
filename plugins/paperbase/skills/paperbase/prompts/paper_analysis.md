# Per-paper research profile extraction

You are building a durable knowledge base from one scientific document. Your output is
read by future agents **instead of** the paper, so fidelity matters more than completeness
and an honest `null` is worth more than a plausible guess.

## Input

The work order gives you the paper's bibliographic record, its parse report and its
**retrieval units** — numbered text blocks (`u0001`, `u0002`, …) each with a section path,
page label and character range. The unit ids are the addressing system for evidence.

## Output

One JSON object satisfying `schemas/paper_profile.schema.json`. Emit JSON only — no prose
around it, no markdown fence needed.

```json
{
  "analysis": {
    "research_problem": {"text": "...", "basis": "author_stated",
      "locators": [{"unit_id": "u0007", "excerpt": "verbatim quote from that unit",
                    "extraction_method": "pymupdf", "confidence": 0.9}]},
    "...": "..."
  }
}
```

`paper_id`, `schema_version` and `generated_by` are filled in by `kb ingest` — omit them.

## The rules that make this knowledge base trustworthy

1. **Every field carries a `basis`:**
   - `author_stated` — the authors say this in the document.
   - `shown_by_result` — visible directly in a reported number, table or figure caption,
     even if the authors do not spell it out in words.
   - `agent_inferred` — *your* reading. **Requires a `rationale`.** Use it for inferred
     limitations and threats to validity, never for findings.
   - `unavailable` — the document does not determine it. Say so; list the field name in
     `unavailable_fields` too. Never invent a value, a number, a citation or a dataset.
2. **Locators are mandatory for `author_stated` and `shown_by_result`.** Each locator needs
   the `unit_id` you took it from and a short **verbatim** `excerpt` (25–300 characters)
   copied character-for-character from that unit. `kb ingest` searches for the excerpt in
   the normalized source text and *rejects or flags* what it cannot find, so do not
   paraphrase inside `excerpt`, do not stitch two sentences together, and do not fix typos.
3. **Preserve scientific qualification.** Keep "may", "in our experiments", "for Illumina
   data", "not statistically significant", "on simulated data only". If a result holds
   only under conditions, those conditions belong in `scope_and_boundary_conditions`.
4. **Separate the paper's own evidence from what it reports about other work.** Statements
   that appear only in the introduction or related-work sections are that paper citing
   someone else; capture those in the claim-extraction stage with
   `is_primary_evidence: false`, not as this paper's findings.
5. **Separate result from interpretation from hypothesis from future work.** A discussion
   sentence explaining *why* a result might occur is an interpretation, not a finding.
6. **Keep negative, null and mixed results.** `negative_or_null_findings` exists precisely
   because these are the first things summarisation destroys. If the paper reports that
   something did *not* work, or made things worse, record it.
7. **Numbers must be verbatim,** with their units and their uncertainty as printed
   ("F1 0.87 ± 0.02", "3.2× fewer false positives", "p = 0.03", "not reported"). Never
   round, never convert, never compute a number the paper does not print (the claim stage
   has an explicit `calculated` status for arithmetic you show).
8. **Flag extraction trouble in `extraction_warnings`:** a table you could not interpret,
   a figure whose values are not in the text, a section that appears truncated, OCR-looking
   text, formulas that did not survive extraction. A visible warning is a feature.
9. **`retrieval_terms`** should be what a future agent would actually type: tool names,
   dataset names, accession numbers, metrics, organism names, method names, acronyms *and*
   their expansions. This drives lexical recall for the whole KB.
10. **Length discipline.** Each statement is one or two sentences. Detail belongs in the
    claims, not in prose here.

## Study-type guidance

`method_or_tool` (introduces a method/tool/algorithm), `empirical_study`,
`benchmark_or_comparison` (its contribution is the comparison itself),
`review_or_survey`, `theory`, `dataset_or_resource`, `case_study`, `simulation_study`,
`position_paper`, `other`, `unknown`. A tool paper that also benchmarks itself is
`method_or_tool` — pick by contribution, not by the presence of a table.

## Before you finish

- Does every `author_stated` / `shown_by_result` field have a locator with a verbatim excerpt?
- Does every `agent_inferred` item have a rationale?
- Did you keep the negative results and the conditions?
- Is anything in the profile *not* supported by the units you were shown? Remove it.
