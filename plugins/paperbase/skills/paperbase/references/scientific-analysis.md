# Running the analysis stages

Five model-dependent stages. Deterministic scripts prepare the input and check the output;
the judgement is yours.

| Stage | Scope | Prompt | Output schema | Typical volume |
|---|---|---|---|---|
| `profile` | one paper | `prompts/paper_analysis.md` | `paper_profile.schema.json` | 1 object |
| `claims` | one paper | `prompts/claim_extraction.md` | `claim.schema.json` | 8-25 objects |
| `relations` | corpus | `prompts/relation_discovery.md` (+ `contradiction_analysis.md`) | `relation.schema.json` | array |
| `topics` | corpus | `prompts/topic_synthesis.md` | `topic.schema.json` (+ `terminology.schema.json`) | 2-8 objects |
| `overview` | corpus | `prompts/corpus_overview.md` (+ `contradiction_analysis.md`, `gap_analysis.md`) | `corpus_overview.schema.json` | 1 object |

`{{include: file.md}}` inlines a shared checklist into a prompt; the prompt version hash
covers the includes, so editing a checklist correctly invalidates the analyses that used it.

## The loop

```bash
python3 scripts/kb.py tasks  $KB --stage profile --limit 3        # only what is pending
python3 scripts/kb.py tasks  $KB --stage claims  --paper <pid>    # a specific paper
python3 scripts/kb.py tasks  $KB --stage relations               # whole-corpus stage
#  -> tasks/<stage>/<pid>.order.md   human-readable: prompt + input + where to answer
#  -> tasks/<stage>/<pid>.order.json same input, machine-readable
python3 scripts/kb.py ingest $KB --stage profile --model-id claude-opus-5
python3 scripts/kb.py ingest $KB --stage claims --paper <pid> --file /path/to/answer.json
```

A work order contains the bibliographic record, the parse report (including warnings and
`needs_ocr`), and the paper's retrieval units, each headed with its `unit_id`, kind, page
label, section path and character range. **Cite those `unit_id`s.** Large papers are
truncated at `--max-chars` with an explicit warning naming the total unit count and the file
to read for the rest — never conclude something is absent from a truncated order.

`--model-id` is recorded in provenance and participates in fingerprints: re-analysing with a
different model invalidates the old analysis rather than silently mixing generations. Add
`--reviewed` when a human has checked the answer (it sets `reviewed_by_human`).

## Order of work

`profile` → `claims` for each paper (claims see the profile), then `relations` → `topics` →
`overview` once enough papers have claims. Doing `overview` on a partly analysed corpus is
fine and often useful, but say so in `scope_note` and `warnings` — the shipped prompt
requires it, and a corpus overview that hides how little it rests on is worse than none.

## What ingest enforces

Nothing partial is ever written: if a response has problems, the whole file is rejected with
per-item messages and the KB is untouched.

- schema validation of every object;
- **locator resolution**: the excerpt is located in `docs/<pid>/text.txt`, and `unit_id`,
  page index/label, section path, sentence index and character offsets are filled in from
  the containing unit. A quote that cannot be found downgrades the locator's confidence and
  is reported by `kb validate` as an error;
- at least one locator per claim; `agent_inferred` profile statements need a `rationale`;
  `calculated`/`inferred` claims need a `derivation`;
- contradictions must list the alternative explanations checked;
- relations may only reference active papers and existing claims; topics and the overview may
  only cite existing claim ids, paper ids and topic ids;
- near-duplicate claims within a paper are detected and marked `duplicate_of`;
- claim counts outside `analysis.min/max_claims_per_paper` produce a warning;
- provenance stamping (model id, prompt name, prompt version hash, timestamp) and a stage
  fingerprint, after which `kb status` reports the stage as `current`.

## Doing the science well

The prompts carry the full rules; the ones that most often decide whether a KB is trustworthy:

1. **Quote, don't paraphrase, inside `excerpt`.** It is verified byte-wise (modulo
   whitespace/ligature folding). Copy from the unit text you were given.
2. **Basis labels are not decoration.** `author_stated` (they say it) vs `shown_by_result`
   (visible in a number or caption) vs `agent_inferred` (your reasoning — needs a rationale)
   vs `unavailable`. Findings are never `agent_inferred`.
3. **Keep hedges and conditions.** "may", "in our setting", "on simulated data", "not
   statistically significant" belong in `statement`, `conditions` and `qualifiers`. Removing
   a qualifier is the most damaging error available to you.
4. **Numbers verbatim, with units and uncertainty as printed** — including
   `"uncertainty": "not reported"`, which is itself information. Never compute a number the
   paper does not print unless you set `evidence_status: "calculated"` and show the arithmetic.
5. **Negative, null and mixed results are the point.** They are what summarisation destroys
   and what a downstream agent most needs.
6. **Introduction ≠ contribution.** A statement found only in the introduction or related
   work is the paper citing someone else: `is_primary_evidence: false` plus `cited_source`.
7. **A citation is not agreement**, and a contradiction is a strong claim: run the checklist
   in `prompts/contradiction_analysis.md` (population, organism, task, data, preprocessing,
   design, metric, outcome definition, tool version, statistical power) and record what you
   checked. Prefer `qualifies`, `studies-different-context` or `partially-contradicts`.
8. **Say what you could not do.** `extraction_warnings` for uninterpretable tables,
   figure-only values, truncated sections, OCR text; `unavailable_fields` for what the
   document does not determine. A visible gap is a feature; a confident guess is a defect.
9. **Confidence means "how sure am I that the paper says this"**, not how much you believe
   the science. Poor text extraction lowers it.
10. **Synthesis cites claim ids.** Every grounded point in a topic or the overview must name
    the claims it rests on, and mark single-study evidence as `single_study` rather than
    consensus.

## Optional automated execution

There is deliberately **no hidden model call**. To automate, write a small adapter that
reads `tasks/<stage>/*.order.json`, calls a model with `prompts/<prompt>` and the payload,
writes `<pid>.response.json`, and shells out to `kb ingest`. Keep it outside `kb.py`, set
`analysis.model_id` to the model you use, and remember the cost profile: one call per paper
for `profile` and `claims`, one per corpus for `relations`/`topics`/`overview`, re-run only
when fingerprints go stale. `kb status` is the cost estimate — it lists exactly what is
pending.
