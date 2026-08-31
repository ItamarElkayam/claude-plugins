"""`kb eval`: measure retrieval usefulness, not just "files were produced".

A question set (YAML or JSON) declares, per question, which papers/claims should
surface and whether counterevidence is expected.  For each question `kb eval`
builds a real context pack and scores:

  expected_papers_found     recall of the papers that should have surfaced
  expected_claims_found     recall of specific claim ids (when declared)
  term_coverage             required terms present in the pack
  citation_coverage         share of included evidence blocks carrying a citation
  locator_validity          share of cited claims whose excerpts verify against source
  counterevidence_present   whether opposing/negative evidence was included
  tokens_used               pack size against the requested budget
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from . import (config as cfgmod, context as ctxmod, fsutil, indexdb, locators,
               log, miniyaml, store)

CITE_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]+-[0-9a-z]{6}(?:-[0-9a-f]{4})?)[^\]]*\]")
CLAIM_RE = re.compile(r"`([^`]+#c\d+-[0-9a-f]{6})`")
BLOCK_RE = re.compile(r"^### ", re.M)


def default_questions_path(kb_dir: str) -> str:
    return os.path.join(kb_dir, "eval", "questions.yaml")


def load_questions(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise SystemExit(
            "no question set at %s.\nCreate one (see the skill's tests/eval/questions.yaml for the "
            "format) or pass a path explicitly." % path)
    if path.endswith(".json"):
        data = fsutil.read_json(path, {})
    else:
        data = miniyaml.load(path)
    questions = data.get("questions") if isinstance(data, dict) else data
    if not questions:
        raise SystemExit("%s contains no questions" % path)
    return questions


def run(kb_dir: str, questions_path: Optional[str] = None, budget: int = 6000) -> Dict[str, Any]:
    path = questions_path or default_questions_path(kb_dir)
    questions = load_questions(path)
    cfg = cfgmod.load(kb_dir)
    conn = indexdb.connect(kb_dir, create=False)
    claim_index = {r["claim_id"]: dict(r) for r in conn.execute("SELECT * FROM claims").fetchall()}
    conn.close()

    rows: List[Dict[str, Any]] = []
    for question in questions:
        qid = question.get("id") or ("q%d" % (len(rows) + 1))
        pack, strategy = ctxmod.build_context(
            kb_dir, question["question"], cfg,
            budget_tokens=question.get("budget") or budget)
        low = pack.lower()
        cited_papers = set(CITE_RE.findall(pack)) | set(
            m for m in re.findall(r"`([a-z0-9\-]+-[0-9a-z]{6})`", pack))
        cited_claims = set(CALL := CLAIM_RE.findall(pack))
        notes: List[str] = []

        expected_papers = question.get("expect_papers") or []
        found_papers = []
        for want in expected_papers:
            hit = any(want == pid or pid.startswith(want) or want in pid for pid in cited_papers) \
                  or want.lower() in low
            found_papers.append(hit)
            if not hit:
                notes.append("expected paper %r did not surface" % want)
        papers_recall = (sum(1 for h in found_papers if h) / float(len(found_papers))) if found_papers else None

        expected_claims = question.get("expect_claims") or []
        claims_recall = None
        if expected_claims:
            hits = [c for c in expected_claims if c in cited_claims]
            claims_recall = len(hits) / float(len(expected_claims))
            for missing in set(expected_claims) - set(hits):
                notes.append("expected claim %s missing" % missing)

        terms = question.get("must_include_terms") or []
        term_hits = [t for t in terms if t.lower() in low]
        term_coverage = (len(term_hits) / float(len(terms))) if terms else None
        for missing in set(terms) - set(term_hits):
            notes.append("required term %r absent from the pack" % missing)

        blocks = len(BLOCK_RE.findall(pack))
        citations = len(CITE_RE.findall(pack))
        citation_coverage = round(min(1.0, citations / float(blocks)), 3) if blocks else 0.0

        verified, checked = 0, 0
        for cid in cited_claims:
            claim = claim_index.get(cid)
            if not claim:
                notes.append("pack cites unknown claim %s" % cid)
                checked += 1
                continue
            support = json.loads(claim["support"] or "[]")
            text = store.read_text(kb_dir, claim["paper_id"])
            for loc in support:
                checked += 1
                if loc.get("excerpt") and locators.find_excerpt(text, loc["excerpt"]):
                    verified += 1
                else:
                    notes.append("locator for %s does not verify against source text" % cid)
        locator_validity = round(verified / float(checked), 3) if checked else None

        counter_needed = question.get("expect_counterevidence")
        counter_present = "(counterevidence)" in pack or bool(
            strategy["included"].get("counterevidence"))
        if counter_needed and not counter_present:
            notes.append("expected counterevidence but the pack contained none")

        tokens_used = strategy["budget"].get("pack_tokens_estimate") or strategy["budget"]["used_tokens_estimate"]
        passed = True
        if papers_recall is not None and papers_recall < float(question.get("min_paper_recall", 1.0)):
            passed = False
        if claims_recall is not None and claims_recall < float(question.get("min_claim_recall", 1.0)):
            passed = False
        if term_coverage is not None and term_coverage < float(question.get("min_term_coverage", 1.0)):
            passed = False
        if locator_validity is not None and locator_validity < float(question.get("min_locator_validity", 1.0)):
            passed = False
        if counter_needed and not counter_present:
            passed = False
        if tokens_used > (question.get("budget") or budget) * 1.15:
            passed = False
            notes.append("pack exceeded its budget (%d > %d)" % (tokens_used, question.get("budget") or budget))

        rows.append({
            "id": qid, "question": question["question"], "passed": passed,
            "expected_papers_found": papers_recall, "expected_claims_found": claims_recall,
            "term_coverage": term_coverage, "citation_coverage": citation_coverage,
            "locator_validity": locator_validity, "counterevidence_present": counter_present,
            "tokens_used": tokens_used, "cited_papers": sorted(cited_papers)[:12],
            "cited_claims": sorted(cited_claims)[:12], "notes": notes,
            "strategy_warnings": strategy.get("warnings") or [],
        })

    result = {
        "kb_dir": kb_dir, "questions_path": path, "n_questions": len(rows),
        "n_passed": sum(1 for r in rows if r["passed"]),
        "passed": all(r["passed"] for r in rows), "questions": rows,
    }
    result["report_path"] = _write_report(kb_dir, result)
    log.audit(kb_dir, "eval", n_questions=len(rows), n_passed=result["n_passed"],
              questions_path=path)
    return result


def _write_report(kb_dir: str, result: Dict[str, Any]) -> str:
    lines = ["# Retrieval evaluation", "",
             "- KB: `%s`" % result["kb_dir"],
             "- Question set: `%s`" % result["questions_path"],
             "- Passed: **%d/%d**" % (result["n_passed"], result["n_questions"]), "",
             "| id | pass | paper recall | claim recall | terms | citations | locators | counter-ev | tokens |",
             "|---|---|---|---|---|---|---|---|---|"]
    for row in result["questions"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %d |" % (
            row["id"], "✓" if row["passed"] else "✗", _fmt(row["expected_papers_found"]),
            _fmt(row["expected_claims_found"]), _fmt(row["term_coverage"]),
            _fmt(row["citation_coverage"]), _fmt(row["locator_validity"]),
            "yes" if row["counterevidence_present"] else "no", row["tokens_used"]))
    for row in result["questions"]:
        lines += ["", "## %s" % row["id"], "", "**%s**" % row["question"], "",
                  "- cited papers: %s" % ", ".join("`%s`" % p for p in row["cited_papers"]),
                  "- cited claims: %s" % ", ".join("`%s`" % c for c in row["cited_claims"])]
        for note in row["notes"]:
            lines.append("- ⚠ %s" % note)
        for warning in row["strategy_warnings"]:
            lines.append("- retrieval warning: %s" % warning)
    path = os.path.join(kb_dir, "reports", "eval_report.md")
    fsutil.atomic_write_text(path, "\n".join(lines) + "\n")
    fsutil.atomic_write_json(os.path.join(kb_dir, "reports", "eval_report.json"), result)
    return path


def _fmt(value) -> str:
    return "-" if value is None else ("%.2f" % value)
