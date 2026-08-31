"""Automatic corpus tuning: learn this corpus's vocabulary from the corpus itself.

Hand-tuning `terminology.synonyms` and `parsing.extra_section_headings` is the step
every new user skips, so paperbase mines them deterministically after parsing:

* **acronyms** - "multiple sequence alignment (MSA)" yields a synonym pair, via a
  compact Schwartz-Hearst match (the acronym's letters must be initials of the
  preceding words), which is high precision;
* **orthographic variants** - "k-mer" / "kmer" / "k mer" collapse to one term, so a
  query using one form retrieves all of them;
* **priority terms** - multi-word terms that are frequent *and* spread across papers,
  used to orient an agent in `KB_INDEX.md`;
* **section headings** - short title-like blocks that recur across papers but are not
  in the canonical section list ("Author summary", "Key Points"). Adopting these
  changes segmentation, so it is reported and costs one re-parse.

Everything is written to `<kb>/config/derived.yaml`, which is layered UNDER the human
`config/config.yaml`: a mined value never overrides a human one, and a human can
suppress a mined list by setting the key to `[]` or `{}` in their own file.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from . import extract, fsutil, ids, log, miniyaml, store

ACRONYM_RE = re.compile(r"\(([A-Z][A-Za-z0-9\-]{1,9})s?\)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-/']*")
_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "with", "is", "are",
    "was", "were", "be", "been", "by", "from", "as", "at", "that", "this", "these",
    "those", "which", "we", "our", "it", "its", "their", "they", "can", "may", "not",
    "all", "each", "such", "than", "then", "also", "more", "most", "other", "using",
    "used", "use", "based", "into", "between", "both", "when", "while", "where", "how",
    "one", "two", "three", "however", "thus", "here", "there", "have", "has", "had",
    "but", "if", "only", "same", "different", "shown", "show", "table", "figure", "et",
    "al", "fig", "eq", "section", "paper", "study", "results", "result", "data",
}
_HEADINGISH = re.compile(r"^[A-Z][^.!?]{2,58}$")


def _looks_like_a_heading(probe: str) -> bool:
    """A heading names a section; it does not make a statement.

    Rejects "Conflict of Interest: none declared" (a colon followed by prose) and
    lower-case sentence fragments, while keeping "Author summary", "Key Points",
    "Data Availability" and ALL-CAPS headings.
    """
    if ":" in probe:
        tail = probe.split(":", 1)[1].strip()
        if len(tail.split()) > 1:
            return False
    words = [w for w in re.split(r"[\s/]+", probe.split(":")[0]) if w]
    if not words:
        return False
    if probe.isupper():
        return True
    content = [w for w in words if w.lower() not in _STOP]
    if not content:
        return False
    capitalised = sum(1 for w in content if w[:1].isupper())
    return capitalised >= max(1, int(round(0.6 * len(content))))


def _paper_texts(kb_dir: str, paper_ids: List[str]) -> Dict[str, str]:
    """Body text per paper, assembled from retrieval units.

    Deliberately not text.txt: units exclude reference lists, so cited journal names
    ("genome biol", "bmc bioinformatics") do not enter the corpus vocabulary, and the
    mined terms describe exactly the text that search and context retrieve.
    """
    out: Dict[str, str] = {}
    for pid in paper_ids:
        units = store.read_units(kb_dir, pid)
        if units:
            out[pid] = "\n\n".join(u["text"] for u in units)
    return out


# ------------------------------------------------------------------ acronyms
def mine_acronyms(texts: Dict[str, str], min_papers: int) -> Dict[str, Dict[str, Any]]:
    """Acronym -> {expansion, papers}. Requires the letters to be word initials."""
    hits: Dict[str, Dict[str, Dict[str, int]]] = {}
    for pid, text in texts.items():
        for match in ACRONYM_RE.finditer(text):
            acro = match.group(1)
            letters = [c for c in acro if c.isalpha()]
            if len(letters) < 2:
                continue
            window = text[max(0, match.start() - 12 * len(letters) - 20):match.start()]
            words = _WORD.findall(window)
            if len(words) < len(letters):
                continue
            candidate = words[-len(letters):]
            if all(w[0].lower() == c.lower() for w, c in zip(candidate, letters)):
                expansion = " ".join(candidate)
                if len(expansion) < 6 or expansion.lower() == acro.lower():
                    continue
                bucket = hits.setdefault(acro, {})
                entry = bucket.setdefault(expansion.lower(), {})
                entry[pid] = entry.get(pid, 0) + 1
    out: Dict[str, Dict[str, Any]] = {}
    for acro, expansions in hits.items():
        best = max(expansions.items(), key=lambda kv: (len(kv[1]), sum(kv[1].values())))
        expansion, papers = best[0], sorted(best[1])
        # An acronym defined in one paper is still worth expanding: the term is used
        # elsewhere without its definition, which is exactly when expansion helps.
        out[acro] = {"expansion": expansion, "papers": papers,
                     "confident": len(papers) >= min_papers}
    return out


# ------------------------------------------------------------------ variants
def mine_variants(texts: Dict[str, str], min_occurrences: int) -> Dict[str, List[str]]:
    """Group surface forms that differ only in hyphens/spaces/case."""
    counts: Dict[str, Dict[str, int]] = {}
    for text in texts.values():
        for raw in _WORD.findall(text):
            # trailing/leading hyphens and slashes are extraction artefacts
            # ("count-" from a line break, "assembly/"), not distinct spellings
            token = raw.strip("-/'").lower()
            if len(token) < 4 or token in _STOP:
                continue
            key = re.sub(r"[-/']", "", token)
            if len(key) < 4 or not key.isalnum() or not any(c.isalpha() for c in key):
                continue
            bucket = counts.setdefault(key, {})
            bucket[token] = bucket.get(token, 0) + 1
    # also catch the spaced form of a hyphenated term ("k mer" for "k-mer")
    out: Dict[str, List[str]] = {}
    for key, forms in counts.items():
        useful = {f: n for f, n in forms.items() if n >= 1}
        if len(useful) < 2:
            continue
        if sum(useful.values()) < min_occurrences:
            continue
        canonical = max(useful.items(), key=lambda kv: kv[1])[0]
        aliases = sorted(f for f in useful if f != canonical)
        if aliases:
            out[canonical] = aliases
    return out


# ------------------------------------------------------------------ terms
def mine_priority_terms(texts: Dict[str, str], min_papers: int, min_occurrences: int,
                        limit: int) -> List[Dict[str, Any]]:
    ngram_papers: Dict[str, Set[str]] = {}
    ngram_counts: Dict[str, int] = {}
    for pid, text in texts.items():
        words = [w for w in _WORD.findall(text)]
        lowered = [w.lower() for w in words]
        for size in (2, 3):
            for i in range(len(lowered) - size + 1):
                gram = lowered[i:i + size]
                if gram[0] in _STOP or gram[-1] in _STOP:
                    continue
                if any(len(w) < 3 or not w[0].isalpha() for w in gram):
                    continue
                if sum(1 for w in gram if w in _STOP) > (size - 2):
                    continue
                phrase = " ".join(gram)
                ngram_counts[phrase] = ngram_counts.get(phrase, 0) + 1
                ngram_papers.setdefault(phrase, set()).add(pid)
    scored = []
    for phrase, count in ngram_counts.items():
        papers = ngram_papers[phrase]
        if len(papers) < min_papers or count < min_occurrences:
            continue
        # spread across papers matters more than raw frequency: a term used by the
        # whole corpus is vocabulary, a term used once a lot is one paper's habit
        scored.append({"term": phrase, "occurrences": count, "papers": sorted(papers),
                       "score": round(len(papers) * (count ** 0.5), 2)})
    scored.sort(key=lambda d: (-d["score"], d["term"]))
    # drop phrases fully contained in a higher-scoring one
    kept: List[Dict[str, Any]] = []
    for cand in scored:
        if any(cand["term"] in k["term"] for k in kept):
            continue
        kept.append(cand)
        if len(kept) >= limit:
            break
    return kept


# ------------------------------------------------------------------ headings
def mine_section_headings(kb_dir: str, paper_ids: List[str], cfg: Dict[str, Any],
                          limit: int, min_papers: int) -> List[Dict[str, Any]]:
    """Short title-like blocks recurring across papers that were NOT read as headings."""
    known = set(extract.CANONICAL_SECTIONS)
    already = set(h.lower() for h in cfg["parsing"]["extra_section_headings"])
    candidates: Dict[str, Dict[str, Any]] = {}
    for pid in paper_ids:
        seen_here: Set[str] = set()
        for block in store.read_blocks(kb_dir, pid):
            if block.get("role") not in ("body", "front_matter"):
                continue
            text = " ".join((block.get("text") or "").split())
            if not text or len(text) > 60 or "\n" in (block.get("text") or "").strip():
                continue
            if block.get("n_lines", 1) > 1:
                continue
            probe = text.rstrip(":.").strip()
            low = probe.lower()
            if low in known or low in already or len(probe.split()) > 8 or len(probe) < 4:
                continue
            if not _HEADINGISH.match(probe) and not probe.isupper():
                continue
            if not _looks_like_a_heading(probe):
                continue
            if re.search(r"\d{4}|http|@|doi", low):
                continue
            if extract.CAPTION_RE.match(probe):
                continue
            key = low
            entry = candidates.setdefault(key, {"heading": probe, "papers": set(), "count": 0})
            entry["count"] += 1
            entry["papers"].add(pid)
            seen_here.add(key)
    out = [{"heading": v["heading"], "papers": sorted(v["papers"]), "count": v["count"]}
           for v in candidates.values() if len(v["papers"]) >= min_papers]
    out.sort(key=lambda d: (-len(d["papers"]), -d["count"], d["heading"].lower()))
    return out[:limit]


# ------------------------------------------------------------------ orchestration
def mine(kb_dir: str, cfg: Dict[str, Any], paper_ids: List[str]) -> Dict[str, Any]:
    knobs = cfg["tuning"]
    texts = _paper_texts(kb_dir, paper_ids)
    texts = {pid: t for pid, t in texts.items() if t}
    result: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "papers_analysed": sorted(texts),
        "acronyms": {}, "variants": {}, "priority_terms": [], "section_headings": [],
    }
    if not texts:
        return result
    if knobs["mine_acronyms"]:
        result["acronyms"] = mine_acronyms(texts, knobs["min_papers"])
    if knobs["mine_orthographic_variants"]:
        result["variants"] = mine_variants(texts, knobs["min_occurrences"])
    if knobs["mine_priority_terms"]:
        result["priority_terms"] = mine_priority_terms(
            texts, knobs["min_papers"], knobs["min_occurrences"], knobs["max_priority_terms"])
    if knobs["mine_section_headings"]:
        result["section_headings"] = mine_section_headings(
            kb_dir, sorted(texts), cfg, knobs["max_section_headings"], knobs["min_papers"])
    return result


def to_config(mined: Dict[str, Any]) -> Dict[str, Any]:
    """Turn mined vocabulary into the config fragment written to derived.yaml."""
    synonyms: Dict[str, List[str]] = {}
    acronyms: Dict[str, str] = {}
    for acro, info in sorted(mined.get("acronyms", {}).items()):
        acronyms[acro] = info["expansion"]
        synonyms.setdefault(info["expansion"], [])
        if acro not in synonyms[info["expansion"]]:
            synonyms[info["expansion"]].append(acro)
    for canonical, aliases in sorted(mined.get("variants", {}).items()):
        merged = list(synonyms.get(canonical, []))
        for alias in aliases:
            if alias not in merged:
                merged.append(alias)
        synonyms[canonical] = merged
    fragment: Dict[str, Any] = {
        "terminology": {
            "synonyms": synonyms,
            "acronyms": acronyms,
            "priority_terms": [t["term"] for t in mined.get("priority_terms", [])],
        }
    }
    headings = [h["heading"] for h in mined.get("section_headings", [])]
    if headings:
        fragment["parsing"] = {"extra_section_headings": headings}
    return fragment


HEADER = """# GENERATED BY `kb build` / `kb tune` - DO NOT EDIT.
#
# Vocabulary and section headings mined from this corpus. This file is layered UNDER
# your config.yaml: anything you set there wins, and setting a key to [] or {} in
# config.yaml suppresses the mined value here. Regenerate with `kb tune <kb>`.
# Full report: reports/tuning_report.md
"""


def write_derived(kb_dir: str, mined: Dict[str, Any]) -> Tuple[str, bool, Dict[str, Any]]:
    """Write derived.yaml. Returns (path, parsing_changed, previous_fragment)."""
    from . import config as cfgmod

    path = cfgmod.derived_path(kb_dir)
    previous = {}
    if os.path.exists(path):
        try:
            previous = miniyaml.load(path) or {}
        except Exception:
            previous = {}
    fragment = to_config(mined)
    before = ((previous.get("parsing") or {}).get("extra_section_headings") or [])
    after = ((fragment.get("parsing") or {}).get("extra_section_headings") or [])
    # Adoption is monotonic on purpose: once a heading is adopted it is parsed AS a
    # heading, so it no longer appears as a body block and re-mining would drop it -
    # which would re-segment the corpus back and forth on every update. Keeping the
    # union makes the outcome stable. A human still vetoes via their own config layer.
    if before:
        merged = list(before)
        for heading in after:
            if heading not in merged:
                merged.append(heading)
        after = merged
        fragment.setdefault("parsing", {})["extra_section_headings"] = after
    fsutil.atomic_write_text(path, HEADER + miniyaml.dumps(fragment))
    return path, sorted(before) != sorted(after), previous


def report(kb_dir: str, mined: Dict[str, Any], parsing_changed: bool) -> str:
    lines = ["# Corpus tuning report", "",
             "Generated %s from %d paper(s). Values were written to `config/derived.yaml`, "
             "which is layered under your `config/config.yaml` (your settings always win)."
             % (mined["generated_at"], len(mined["papers_analysed"])), ""]
    acronyms = mined.get("acronyms") or {}
    lines += ["## Acronyms (%d)" % len(acronyms), ""]
    if acronyms:
        lines += ["| acronym | expansion | defined in |", "|---|---|---|"]
        for acro, info in sorted(acronyms.items()):
            lines.append("| `%s` | %s | %s |" % (acro, info["expansion"],
                                                 ", ".join("`%s`" % p for p in info["papers"][:4])))
    else:
        lines.append("_none found_")
    variants = mined.get("variants") or {}
    lines += ["", "## Orthographic variants (%d)" % len(variants), ""]
    if variants:
        for canonical, aliases in sorted(variants.items())[:40]:
            lines.append("- **%s** ← %s" % (canonical, ", ".join(aliases)))
        if len(variants) > 40:
            lines.append("- _… %d more_" % (len(variants) - 40))
    else:
        lines.append("_none found_")
    terms = mined.get("priority_terms") or []
    lines += ["", "## Corpus vocabulary (%d terms)" % len(terms), ""]
    if terms:
        lines += ["| term | occurrences | papers |", "|---|---|---|"]
        for term in terms:
            lines.append("| %s | %d | %d |" % (term["term"], term["occurrences"], len(term["papers"])))
    else:
        lines.append("_none found_")
    headings = mined.get("section_headings") or []
    lines += ["", "## Corpus-specific section headings (%d)" % len(headings), ""]
    if headings:
        lines += ["These recur across papers but are not canonical section names, so they were "
                  "adopted as section starts%s." % (" (the corpus was re-parsed once so section "
                  "paths reflect them)" if parsing_changed else ""), "",
                  "| heading | papers |", "|---|---|"]
        for heading in headings:
            lines.append("| %s | %s |" % (heading["heading"],
                                          ", ".join("`%s`" % p for p in heading["papers"][:4])))
        lines += ["", "To veto them, set `parsing: {extra_section_headings: []}` in "
                  "`config/config.yaml` (your file overrides the derived one)."]
    else:
        lines.append("_none found_")
    path = os.path.join(kb_dir, "reports", "tuning_report.md")
    fsutil.atomic_write_text(path, "\n".join(lines) + "\n")
    fsutil.atomic_write_json(os.path.join(kb_dir, "reports", "tuning_report.json"), mined)
    return path
