"""`kb context`: build a compact, budgeted, citation-carrying evidence pack.

The pack is what a downstream agent reads instead of the papers.  It deliberately
includes counterevidence and a "where to look next" section, and it always states
the retrieval strategy and its warnings, so the consumer can tell a thin pack from
a rich one.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from . import indexdb, render, search, store


def _tokens(text: str, chars_per_token: int) -> int:
    return int(len(text) / float(chars_per_token)) + 1


class Budget(object):
    def __init__(self, total_tokens: int, split: Dict[str, float], chars_per_token: int):
        norm = sum(split.values()) or 1.0
        self.limits = {k: int(total_tokens * (v / norm)) for k, v in split.items()}
        self.used = {k: 0 for k in split}
        self.chars_per_token = chars_per_token
        self.total = total_tokens
        self.borrowed = {}

    BORROW_FACTOR = 1.5  # a section may exceed its share by at most this much

    def take(self, section: str, text: str) -> bool:
        cost = _tokens(text, self.chars_per_token)
        if self.used[section] + cost <= self.limits[section]:
            self.used[section] += cost
            return True
        # borrow from budget other sections have not used, bounded so that an early
        # section cannot starve a later one
        ceiling = int(self.limits[section] * self.BORROW_FACTOR)
        if self.used[section] + cost <= ceiling and self.spent() + cost <= self.total:
            self.used[section] += cost
            self.borrowed[section] = self.borrowed.get(section, 0) + cost
            return True
        return False

    def spent(self) -> int:
        return sum(self.used.values())

    def report(self) -> Dict[str, Any]:
        return {"budget_tokens": self.total, "used_tokens_estimate": self.spent(),
                "borrowed": self.borrowed,
                "per_section": {k: {"limit": self.limits[k], "used": self.used[k]} for k in self.limits}}


def build_context(kb_dir: str, question: str, cfg: Dict[str, Any], budget_tokens: Optional[int] = None,
                  filters: Optional[search.Filters] = None, raw_query: bool = False,
                  as_json: bool = False) -> Tuple[str, Dict[str, Any]]:
    conn = indexdb.connect(kb_dir, create=False)
    filters = filters or search.Filters()
    retr = cfg["retrieval"]
    budget_tokens = budget_tokens or retr["default_budget_tokens"]
    # ~18% is reserved for the pack's own scaffolding: section headers, the
    # "where to look next" pointers and the retrieval-strategy block.
    evidence_tokens = max(400, int(budget_tokens * 0.82))
    budget = Budget(evidence_tokens, dict(retr["budget_split"]), retr["chars_per_token"])
    budget.requested_total = budget_tokens

    warnings: List[str] = []
    base_query = indexdb.fts_query(question, raw=raw_query)
    if not base_query:
        return ("No searchable terms in the question.", {"warnings": ["empty query"]})
    synonyms = indexdb.expand_synonyms(conn, question, cfg)
    query = base_query
    if synonyms:
        query = base_query + " OR " + " OR ".join('"%s"' % s.replace('"', "") for s in synonyms)

    units = search.search_units(conn, query, retr["top_units"], filters)
    claims = search.search_claims(conn, query, retr["top_claims"], filters)
    papers = search.search_papers(conn, query, 12, filters)
    topics = search.search_topics(conn, query, 3)

    # diversity: cap units per paper so one paper cannot fill the pack
    per_paper: Dict[str, int] = {}
    kept_units = []
    for unit in units:
        count = per_paper.get(unit["paper_id"], 0)
        if count >= retr["max_units_per_paper"]:
            continue
        per_paper[unit["paper_id"]] = count + 1
        kept_units.append(unit)

    # Order papers by how much of the pack's evidence they actually contributed, so the
    # profile section describes the papers being cited rather than whatever matched first.
    contribution: Dict[str, float] = {}
    rank: Dict[str, int] = {}
    for weight, source in ((3.0, claims), (1.0, kept_units), (0.5, papers)):
        for position, row in enumerate(source):
            pid = row["paper_id"]
            contribution[pid] = contribution.get(pid, 0.0) + weight
            rank.setdefault(pid, position)
    paper_ids = sorted(contribution, key=lambda pid: (-contribution[pid], rank[pid]))

    claim_ids = [c["claim_id"] for c in claims]
    counter = search.opposing_claims(conn, claim_ids)
    negatives = search.negative_claims(conn, paper_ids[:8])
    relations = search.relations_for(conn, paper_ids[:10], claim_ids)

    if not claims:
        warnings.append("no atomic claims matched: the claim-extraction stage may not have run yet "
                        "(`kb status`), so this pack rests on raw passages only")
    if not topics:
        warnings.append("no topic synthesis matched this question")
    quality_flags = [u["paper_id"] for u in kept_units if u.get("text_quality") in ("poor", "empty")]
    if quality_flags:
        warnings.append("passages from papers with poor text extraction are included: %s"
                        % ", ".join(sorted(set(quality_flags))))
    if not counter and not negatives:
        warnings.append("no counterevidence or negative results were found for this question - "
                        "absence of disagreement in the KB is NOT evidence of consensus")

    lines: List[str] = [
        "# Evidence pack",
        "",
        "**Question:** %s" % question,
        "",
        "_Assembled by `kb context` from the paperbase KB at `%s`. Citations are "
        "`[paper_id §section p.page unit]`; `docs/<paper_id>/text.txt` holds the exact offsets._" % kb_dir,
        "",
    ]

    # ---- topic synthesis
    if topics:
        lines += ["## Topic synthesis [SYNTHESIS - model-generated, not source text]", ""]
        if filters.active():
            lines += ["_Note: topic syntheses are corpus-level and are NOT restricted by the "
                      "filters (%s); they may cite claims from papers outside the filter._" %
                      filters.describe(), ""]
        for topic in topics[:2]:
            payload = json.loads(topic["payload"]) if topic.get("payload") else {}
            block = ["### %s (`%s`)" % (topic["name"], topic["topic_id"]), "",
                     (topic["summary"] or "")[:1600], ""]
            for label, key in (("Agreement", "agreements"), ("Disagreement", "genuine_disagreements"),
                               ("Apparent disagreement explained", "apparent_disagreements_explained"),
                               ("Open questions", "open_questions")):
                for item in (payload.get(key) or [])[:3]:
                    block.append("- **%s:** %s — claims %s" % (
                        label, item["text"], ", ".join("`%s`" % c for c in item.get("claim_ids") or [])))
            block.append("")
            text = "\n".join(block)
            if budget.take("synthesis", text):
                lines.append(text)
            else:
                # a truncated synthesis beats no synthesis: fit what the budget allows
                room = max(0, budget.limits["synthesis"] - budget.used["synthesis"]) * retr["chars_per_token"]
                if room > 400:
                    clipped = text[: room - 120].rstrip() + (
                        "\n\n_[synthesis truncated to fit the context budget - read "
                        "synthesis/topics/%s.md for the full analysis]_\n" % topic["topic_id"])
                    budget.take("synthesis", clipped[:room])
                    lines.append(clipped)
                    warnings.append("topic synthesis for `%s` was truncated to fit the budget "
                                    "(full text in synthesis/topics/%s.md)"
                                    % (topic["topic_id"], topic["topic_id"]))
                else:
                    lines.append("_Topic synthesis `%s` omitted: the synthesis budget (%d tokens) is "
                                 "too small. Raise --budget or read synthesis/topics/%s.md._\n"
                                 % (topic["topic_id"], budget.limits["synthesis"], topic["topic_id"]))
                    warnings.append("topic synthesis for `%s` omitted: synthesis budget exhausted"
                                    % topic["topic_id"])

    # ---- claims
    lines += ["## Relevant claims (evidence-grounded)", ""]
    if not claims:
        lines += ["_none in the KB yet_", ""]
    included_claims: List[Dict[str, Any]] = []
    trimmable: List[Dict[str, Any]] = []  # {"prio": int, "idx": int} - higher prio drops first
    counter_budget = int(budget.limits["claims"] * retr["min_counterevidence_share"])
    for claim in claims:
        text = _format_claim(claim)
        if budget.used["claims"] + _tokens(text, retr["chars_per_token"]) > (budget.limits["claims"] - counter_budget) \
                and (counter or negatives):
            break
        if not budget.take("claims", text):
            break
        included_claims.append(claim)
        if len(included_claims) > 2:  # always keep the two best-matching claims
            trimmable.append({"prio": 2, "idx": len(lines)})
        lines.append(text)

    # ---- counterevidence (reserved share)
    included_ids = {c["claim_id"] for c in included_claims}
    opposing = _dedupe(counter + negatives, "claim_id", exclude=included_ids)
    # A disagreement whose two sides are BOTH already in the claims section is still
    # counterevidence present in the pack - count and name it rather than reporting none.
    already_paired = []
    for rel in relations:
        if rel["relation"] not in ("contradicts", "partially-contradicts", "qualifies",
                                   "fails-to-reproduce", "studies-different-context"):
            continue
        src, dst = rel.get("source_claim_id"), rel.get("target_claim_id")
        if src and dst and src in included_ids and dst in included_ids:
            already_paired.append((rel, src, dst))
    lines += ["## Counterevidence, qualifications and negative results", ""]
    if filters.active():
        lines += ["_Note: counterevidence is deliberately NOT restricted by the filters - "
                  "filtering it away would make this pack one-sided._", ""]
    for rel, src, dst in already_paired:
        lines.append("- Both sides of a `%s` link are already included above: `%s` vs `%s` — %s\n"
                     % (rel["relation"], src, dst, rel.get("rationale", "")))
    if not opposing and not already_paired:
        lines += ["_None recorded for these claims. This is an absence of recorded disagreement, "
                  "not established consensus._", ""]
    kept_counter = 0
    for claim in opposing:
        text = _format_claim(claim, counter=True)
        if not budget.take("claims", text):
            warnings.append("counterevidence truncated: claim budget exhausted")
            break
        kept_counter += 1
        if kept_counter > 1:  # always keep one piece of counterevidence
            trimmable.append({"prio": 1, "idx": len(lines)})
        lines.append(text)

    # ---- passages
    lines += ["## Source passages", ""]
    passage_slots: List[int] = []
    for unit in kept_units:
        text = _format_unit(unit)
        if not budget.take("passages", text):
            warnings.append("passages truncated: passage budget exhausted")
            break
        passage_slots.append(len(lines))
        trimmable.append({"prio": 4, "idx": len(lines)})
        lines.append(text)

    # ---- paper profiles
    lines += ["## Papers in this pack", ""]
    for pid in paper_ids[:10]:
        row = conn.execute("SELECT * FROM papers WHERE paper_id=?", (pid,)).fetchone()
        if not row:
            continue
        profile = store.read_profile(kb_dir, pid)
        text = _format_paper(row, profile)
        if not budget.take("papers", text):
            warnings.append("paper profiles truncated: profile budget exhausted")
            break
        trimmable.append({"prio": 3, "idx": len(lines)})
        lines.append(text)

    # ---- relations
    lines += ["## Cross-paper relations", ""]
    if not relations:
        lines += ["_none recorded between these papers_", ""]
    for rel in relations[:20]:
        text = ("- `%s` **%s** `%s`%s — %s _(%s, confidence %s)_%s\n" % (
            rel["source_paper_id"], rel["relation"], rel.get("target_paper_id") or "?",
            (" [%s]" % rel["dimension"]) if rel.get("dimension") else "",
            rel.get("rationale"), rel.get("origin"), rel.get("confidence"),
            ("\n  - alternative explanations checked: %s" % ", ".join(json.loads(rel["payload"]).get("difference_explanations") or []))
            if rel.get("payload") and json.loads(rel["payload"]).get("difference_explanations") else ""))
        if not budget.take("relations", text):
            warnings.append("relations truncated: relation budget exhausted")
            break
        trimmable.append({"prio": 3, "idx": len(lines)})
        lines.append(text)

    # ---- follow-ups
    followups = _followups(conn, kb_dir, paper_ids, kept_units, included_claims)
    lines += ["## Where to look next", ""]
    for item in followups:
        lines.append("- %s" % item)
    lines.append("")

    strategy = {
        "mode": "lexical_bm25_fts5" + ("+synonym_expansion" if synonyms else ""),
        "query": query,
        "synonyms_used": synonyms,
        "filters": filters.describe(),
        "candidates": {"units": len(units), "claims": len(claims), "papers": len(papers),
                       "topics": len(topics), "counterevidence": len(opposing)},
        "included": {"units": len([u for u in kept_units]), "claims": len(included_claims),
                     "counterevidence": len(opposing) + len(already_paired),
                     "counterevidence_paired_in_claims": len(already_paired),
                     "papers": len(paper_ids[:10])},
        "budget": budget.report(),
        "warnings": warnings,
        "kb": kb_dir,
    }
    lines += [
        "## Retrieval strategy and warnings",
        "",
        "- strategy: `%s`" % strategy["mode"],
        "- FTS query: `%s`" % query[:400],
        "- filters: %s" % filters.describe(),
        "- candidates considered: %s" % json.dumps(strategy["candidates"]),
        "- budget: %d tokens requested; ~%d used by evidence sections (%d reserved for structure)"
        % (budget_tokens, budget.spent(), budget_tokens - budget.total),
    ]
    for warning in warnings:
        lines.append("- ⚠ %s" % warning)
    lines.append("")
    conn.close()
    # Hard guarantee: the delivered pack never exceeds the requested budget. Optional
    # blocks are dropped lowest-value-first (extra passages, then profiles/relations,
    # then surplus claims), always keeping the two best claims and one counterexample.
    dropped = {"passages": 0, "profiles_relations": 0, "claims": 0, "counterevidence": 0}
    label = {4: "passages", 3: "profiles_relations", 2: "claims", 1: "counterevidence"}
    pack = "\n".join(l for l in lines if l is not None)
    order = sorted(trimmable, key=lambda t: (-t["prio"], -t["idx"]))
    while _tokens(pack, retr["chars_per_token"]) > budget_tokens and order:
        slot = order.pop(0)
        lines[slot["idx"]] = None
        dropped[label[slot["prio"]]] += 1
        pack = "\n".join(l for l in lines if l is not None)
    lines = [l for l in lines if l is not None]
    trimmed = {k: v for k, v in dropped.items() if v}
    if trimmed:
        note = "dropped to stay inside the %d-token budget: %s" % (
            budget_tokens, ", ".join("%d %s" % (v, k) for k, v in sorted(trimmed.items())))
        warnings.append(note)
        lines.append("- ⚠ %s" % note)
        pack = "\n".join(lines)
    if _tokens(pack, retr["chars_per_token"]) > budget_tokens:
        note = ("the minimum useful pack for this question is ~%d tokens, above the %d-token budget; "
                "nothing further could be dropped without losing the evidence itself"
                % (_tokens(pack, retr["chars_per_token"]), budget_tokens))
        warnings.append(note)
        lines.append("- ⚠ %s" % note)
        pack = "\n".join(lines)
    pack_tokens = _tokens(pack, retr["chars_per_token"])
    strategy["budget"]["pack_tokens_estimate"] = pack_tokens
    strategy["budget"]["requested_tokens"] = budget_tokens
    if pack_tokens > budget_tokens:
        strategy["warnings"].append(
            "pack is ~%d tokens against a %d-token budget: the fixed section headers and pointers "
            "did not fit the 18%% structural reserve" % (pack_tokens, budget_tokens))
    return pack, strategy


def _dedupe(rows: List[Dict[str, Any]], key: str, exclude: set) -> List[Dict[str, Any]]:
    seen, out = set(exclude), []
    for row in rows:
        if row.get(key) in seen:
            continue
        seen.add(row.get(key))
        out.append(row)
    return out


def _format_claim(claim: Dict[str, Any], counter: bool = False) -> str:
    support = claim.get("support")
    if isinstance(support, str):
        try:
            support = json.loads(support)
        except ValueError:
            support = []
    support = support or []
    head = "### `%s`%s" % (claim["claim_id"], " (counterevidence)" if counter else "")
    bits = [head, "", claim["statement"], ""]
    meta = ["type: %s" % claim.get("claim_type"), "evidence: %s" % claim.get("evidence_status"),
            "polarity: %s" % claim.get("polarity")]
    if claim.get("evidence_type"):
        meta.append("evidence type: %s" % claim["evidence_type"])
    if not claim.get("is_primary_evidence", 1):
        meta.append("**NOT this paper's own result** (reported from other work)")
    if claim.get("magnitude"):
        meta.append("magnitude: %s" % claim["magnitude"])
    if claim.get("uncertainty"):
        meta.append("uncertainty: %s" % claim["uncertainty"])
    if claim.get("conditions"):
        meta.append("conditions: %s" % claim["conditions"])
    if claim.get("qualifiers"):
        meta.append("qualifiers: %s" % claim["qualifiers"])
    if claim.get("relation"):
        meta.append("linked by `%s`: %s" % (claim["relation"], claim.get("rationale") or ""))
    meta.append("confidence: %s" % claim.get("confidence"))
    bits.append("- " + "  \n- ".join(str(m) for m in meta))
    for loc in support[:3]:
        bits.append("- source %s" % render.cite(loc))
        if loc.get("excerpt"):
            bits.append("  > %s" % loc["excerpt"].replace("\n", " ")[:320])
    bits.append("")
    return "\n".join(bits)


def _format_unit(unit: Dict[str, Any]) -> str:
    loc = {
        "paper_id": unit["paper_id"], "unit_id": unit["unit_id"],
        "section_path": (unit.get("section_path") or "").split(" > ") if unit.get("section_path") else [],
        "page_label": unit.get("page_label_start"), "char_start": unit.get("char_start"),
        "char_end": unit.get("char_end"),
    }
    header = "### %s — %s (%s, %s)" % (render.cite(loc), (unit.get("title") or "")[:70],
                                       unit.get("year"), unit.get("kind"))
    return "%s\n\n> %s\n" % (header, (unit.get("text") or "").replace("\n", "\n> ")[:1400])


def _format_paper(row, profile: Optional[Dict[str, Any]]) -> str:
    bits = ["### `%s` — %s" % (row["paper_id"], row["title"]), "",
            "- %s, %s%s · type: %s · status: %s" % (
                (row["authors"] or "").split("|")[0].strip() or "unknown authors",
                row["year"] or "?", (" · doi:%s" % row["doi"]) if row["doi"] else "",
                row["document_type"], row["publication_status"]),
            "- text quality: %s%s · units: %s · claims: %s" % (
                row["text_quality"], " (OCR RECOMMENDED)" if row["needs_ocr"] else "",
                row["n_units"], row["n_claims"]),
            "- profile: `papers/%s/paper.md`" % row["paper_id"]]
    if profile:
        analysis = profile.get("analysis") or {}
        problem = (analysis.get("research_problem") or {}).get("text")
        contribution = (analysis.get("main_contribution") or {}).get("text")
        if problem:
            bits.append("- problem: %s" % problem[:300])
        if contribution:
            bits.append("- contribution: %s" % contribution[:300])
        study = (analysis.get("study_type") or {}).get("value")
        if study:
            bits.append("- study type: %s" % study)
        # Scope facts (what data, at what scale, measured how) answer a large share of
        # real questions and are cheap, so they belong in the pack, not only in paper.md.
        data = ["%s%s" % (d["name"], (" (%s)" % d["size"]) if d.get("size") else "")
                for d in (analysis.get("data_sources") or [])[:4]]
        if data:
            bits.append("- data: %s" % "; ".join(data))
        metrics = [m["name"] for m in (analysis.get("evaluation_metrics") or [])[:4]]
        if metrics:
            bits.append("- metrics: %s" % "; ".join(metrics))
        scope = [(x.get("text") or "")[:200] for x in (analysis.get("scope_and_boundary_conditions") or [])[:2]]
        if scope:
            bits.append("- scope: %s" % " | ".join(scope))
        limits = [(l.get("text") or "")[:160] for l in (analysis.get("limitations_stated") or [])[:2]]
        if limits:
            bits.append("- stated limitations: %s" % "; ".join(limits))
    elif row["abstract"]:
        bits.append("- abstract (verbatim): %s" % row["abstract"][:400].replace("\n", " "))
    bits.append("")
    return "\n".join(bits)


def _followups(conn, kb_dir: str, paper_ids: List[str], units: List[Dict[str, Any]],
               claims: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    shown = set(u["unit_id"] + u["paper_id"] for u in units)
    for pid in paper_ids[:4]:
        rows = conn.execute(
            "SELECT unit_id, section_path, page_label_start FROM units WHERE paper_id=? "
            "AND kind IN ('paragraph','table','caption') ORDER BY unit_id", (pid,)).fetchall()
        nearby = [r for r in rows if (r["unit_id"] + pid) not in shown][:3]
        if nearby:
            out.append("`%s`: also units %s (`docs/%s/units.jsonl`, full text `docs/%s/text.txt`)"
                       % (pid, ", ".join("%s [%s]" % (r["unit_id"], (r["section_path"] or "")[:28]) for r in nearby),
                          pid, pid))
    more = conn.execute(
        "SELECT paper_id, title FROM papers WHERE status='active' AND paper_id NOT IN (%s) "
        "ORDER BY year DESC LIMIT 5" % ",".join("?" * len(paper_ids or ["-"])),
        tuple(paper_ids or ["-"])).fetchall()
    if more:
        out.append("papers not in this pack that may still be relevant: %s"
                   % ", ".join("`%s`" % r["paper_id"] for r in more))
    out.append("run `kb show-paper <kb> <paper_id>` for a full profile, or "
               "`kb show-claim <kb> <claim_id>` to see a claim with its exact passage")
    return out
