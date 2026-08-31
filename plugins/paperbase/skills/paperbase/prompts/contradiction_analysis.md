   Before labelling anything a contradiction, check — and record in
   `difference_explanations` — whether the findings differ because of:

   - **population / organism / task** (human vs bacterial, one species vs another);
   - **data** (simulated vs real, different accession, different coverage or read length);
   - **preprocessing** (trimming, filtering, deduplication, normalization);
   - **experimental design** (cross-validation scheme, train/test leakage, ablation setup);
   - **metric or outcome definition** (per-base vs per-read, F1 vs recall at fixed precision);
   - **baseline or comparator version** (tool version, hyperparameters, default vs tuned);
   - **statistical power** (sample size, single run vs repeated, no uncertainty reported);
   - **scope of the claim** (a general statement vs one made only for a subset).

   If any of these explains the difference, the correct relation is `qualifies`,
   `studies-different-context` or `partially-contradicts` — with the explanation recorded.
   Reserve `contradicts` for claims that are genuinely incompatible *on the same dimension,
   for the same population, measured the same way*. If you cannot rule out a benign
   explanation, say so in the rationale and lower the confidence rather than asserting a
   contradiction.
