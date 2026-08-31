"""`kb validate`: prove the KB's knowledge is well-formed and traceable.

Checks (grouped as errors vs warnings by the `validation.fail_on` / `warn_on`
config keys):

  schema_error        artifacts must satisfy their schema
  missing_locator     every claim needs >= 1 source locator
  broken_locator      locator must point at an existing paper/unit/offset range
  excerpt_mismatch    the quoted excerpt must occur in the normalized source text
  dangling_reference  claim/paper/topic ids referenced anywhere must exist
  broken_link         Markdown links inside the KB must resolve
  unsupported_synthesis  synthesis points must cite claim ids
  duplicate_claim     near-identical claims within a paper
  unapplied_override  overrides whose target ids do not exist
  stale_stage         analysis older than its inputs
  poor_parse          papers whose text extraction is poor/empty
  ocr_excerpt         locators resting on OCR text (never treated as exact)
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import (config as cfgmod, fsutil, ids, jsonschema_lite, locators, log,
               manifest, overrides as ovmod, sentences, store)

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


class Issue(object):
    def __init__(self, kind: str, where: str, message: str):
        self.kind = kind
        self.where = where
        self.message = message

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "where": self.where, "message": self.message}

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.kind, self.where, self.message)


def validate(kb_dir: str, deep: bool = True) -> Dict[str, Any]:
    cfg = cfgmod.load(kb_dir)
    man = manifest.load(kb_dir)
    if man is None:
        raise SystemExit("no KB at %s (state/manifest.json missing)" % kb_dir)
    issues: List[Issue] = []
    active = manifest.active_papers(man)
    corpus = {row["paper_id"]: row for row in store.read_corpus_manifest(kb_dir)}
    live_claims: Dict[str, Dict[str, Any]] = {}
    stats = {"papers": len(active), "claims": 0, "locators": 0, "topics": 0, "relations": 0,
             "excerpts_verified": 0, "papers_with_claims": 0}

    # ---------------------------------------------------------------- papers
    for pid in active:
        entry = man["papers"][pid]
        for path in (store.text_path(kb_dir, pid), store.units_path(kb_dir, pid),
                     store.parse_path(kb_dir, pid)):
            if not os.path.exists(path):
                issues.append(Issue("broken_locator", pid, "missing artifact %s" % path))
        source_abs = os.path.join(man["paper_dir"], entry.get("source_path") or "")
        if not os.path.exists(source_abs):
            issues.append(Issue("dangling_reference", pid,
                                "source document is gone: %s (run `kb update` to tombstone it)" % source_abs))
        parse = store.read_parse(kb_dir, pid)
        if parse.get("text_quality") in ("poor", "empty"):
            issues.append(Issue("poor_parse", pid, "text quality %s; %s" % (
                parse.get("text_quality"),
                (parse.get("warnings") or ["no detail"])[0])))
        if pid not in corpus:
            issues.append(Issue("dangling_reference", pid, "active paper missing from corpus/papers.jsonl"))
        state = manifest.analysis_state(man, cfg, pid)
        for stage, value in state.items():
            if value == "stale":
                issues.append(Issue("stale_stage", pid,
                                    "%s analysis is older than its inputs - regenerate with "
                                    "`kb tasks %s --stage %s --paper %s`" % (stage, kb_dir, stage, pid)))

    # ---------------------------------------------------------------- claims
    profile_schema = os.path.join(cfgmod.SCHEMAS_DIR, "paper_profile.schema.json")
    claim_schema = os.path.join(cfgmod.SCHEMAS_DIR, "claim.schema.json")
    for pid in active:
        profile = store.read_profile(kb_dir, pid)
        if profile:
            for err in jsonschema_lite.validate(profile, profile_schema):
                issues.append(Issue("schema_error", "%s/profile.json" % pid, err))
            issues.extend(_check_profile_locators(kb_dir, pid, profile, deep, stats))
        claims = store.read_claims(kb_dir, pid)
        if claims:
            stats["papers_with_claims"] += 1
        text = store.read_text(kb_dir, pid) if deep else ""
        units = {u["unit_id"]: u for u in store.read_units(kb_dir, pid)}
        norms: Dict[str, str] = {}
        for claim in claims:
            stats["claims"] += 1
            live_claims[claim["claim_id"]] = claim
            for err in jsonschema_lite.validate(claim, claim_schema):
                issues.append(Issue("schema_error", claim["claim_id"], err))
            if claim["paper_id"] != pid:
                issues.append(Issue("dangling_reference", claim["claim_id"],
                                    "claim stored under %s but claims paper_id %s" % (pid, claim["paper_id"])))
            support = claim.get("support") or []
            if not support:
                issues.append(Issue("missing_locator", claim["claim_id"], "claim has no source locator"))
            for loc in support:
                stats["locators"] += 1
                issues.extend(_check_locator(kb_dir, pid, loc, units, text, deep, stats,
                                             where=claim["claim_id"]))
            norm = ids.normalize_title(claim.get("statement", ""))
            if norm in norms and not claim.get("duplicate_of"):
                issues.append(Issue("duplicate_claim", claim["claim_id"],
                                    "near-identical statement to %s; set duplicate_of or merge" % norms[norm]))
            norms[norm] = claim["claim_id"]

    # ---------------------------------------------------------------- relations
    relation_schema = os.path.join(cfgmod.SCHEMAS_DIR, "relation.schema.json")
    for rel in store.read_relations(kb_dir):
        stats["relations"] += 1
        for err in jsonschema_lite.validate(rel, relation_schema):
            issues.append(Issue("schema_error", rel.get("relation_id", "?"), err))
        for key in ("source_paper_id", "target_paper_id"):
            value = rel.get(key)
            if value and value not in active:
                issues.append(Issue("dangling_reference", rel.get("relation_id", "?"),
                                    "%s %r is not an active paper" % (key, value)))
        for key in ("source_claim_id", "target_claim_id"):
            value = rel.get(key)
            if value and value not in live_claims:
                issues.append(Issue("dangling_reference", rel.get("relation_id", "?"),
                                    "%s %r does not exist" % (key, value)))
        if rel.get("relation") in ("contradicts", "partially-contradicts") and not rel.get("difference_explanations"):
            issues.append(Issue("unsupported_synthesis", rel.get("relation_id", "?"),
                                "contradiction without the alternative explanations checked"))

    # ---------------------------------------------------------------- synthesis
    topic_schema = os.path.join(cfgmod.SCHEMAS_DIR, "topic.schema.json")
    for topic in store.read_topics(kb_dir):
        stats["topics"] += 1
        for err in jsonschema_lite.validate(topic, topic_schema):
            issues.append(Issue("schema_error", topic.get("topic_id", "?"), err))
        issues.extend(_check_grounding(topic, topic.get("topic_id", "?"), live_claims, active))
    known_topics = {t.get("topic_id") for t in store.read_topics(kb_dir)}
    overview = store.read_overview(kb_dir)
    if overview:
        for entry in overview.get("topic_map") or []:
            if entry.get("topic_id") not in known_topics:
                issues.append(Issue("dangling_reference", "corpus_overview.topic_map",
                                    "topic_id %s has no topic synthesis file" % entry.get("topic_id")))
        for err in jsonschema_lite.validate(overview, os.path.join(cfgmod.SCHEMAS_DIR, "corpus_overview.schema.json")):
            issues.append(Issue("schema_error", "corpus_overview.json", err))
        issues.extend(_check_grounding(overview, "corpus_overview", live_claims, active))

    # ---------------------------------------------------------------- overrides
    ov, problems = ovmod.load(kb_dir)
    for problem in problems:
        issues.append(Issue("schema_error", "overrides", problem))
    for item in ovmod.unapplied(ov, active, sorted(live_claims), [], [], []):
        issues.append(Issue("unapplied_override", "overrides", item))

    # ---------------------------------------------------------------- links
    issues.extend(_check_markdown_links(kb_dir))

    # ---------------------------------------------------------------- journal
    journal = fsutil.journal_state(kb_dir)
    if journal.get("status") == "in_progress":
        issues.append(Issue("stale_stage", "state/journal.json",
                            "a previous %s run was interrupted; re-run `kb update` to finish it"
                            % journal.get("stage")))

    fail_kinds = set(cfg["validation"]["fail_on"])
    errors = [i for i in issues if i.kind in fail_kinds]
    warnings = [i for i in issues if i.kind not in fail_kinds]
    result = {
        "kb_dir": kb_dir,
        "ok": not errors,
        "errors": [i.as_dict() for i in errors],
        "warnings": [i.as_dict() for i in warnings],
        "stats": stats,
    }
    _write_report(kb_dir, result)
    log.audit(kb_dir, "validate", ok=result["ok"], n_errors=len(errors), n_warnings=len(warnings),
              **stats)
    return result


def _check_locator(kb_dir: str, pid: str, loc: Dict[str, Any], units: Dict[str, Any], text: str,
                   deep: bool, stats: Dict[str, int], where: str) -> List[Issue]:
    out: List[Issue] = []
    if loc.get("paper_id") != pid:
        out.append(Issue("broken_locator", where, "locator paper_id %r does not match %r"
                         % (loc.get("paper_id"), pid)))
        return out
    unit = units.get(loc.get("unit_id")) if loc.get("unit_id") else None
    if loc.get("unit_id") and unit is None:
        out.append(Issue("broken_locator", where, "unit %s does not exist in %s"
                         % (loc.get("unit_id"), pid)))
    if loc.get("char_start") is not None and deep:
        if loc["char_start"] < 0 or (loc.get("char_end") or 0) > len(text):
            out.append(Issue("broken_locator", where, "char range %s-%s outside text of length %d"
                             % (loc.get("char_start"), loc.get("char_end"), len(text))))
    if loc.get("page_index") is not None and unit is not None:
        if unit.get("page_index_start") is not None and loc["page_index"] not in (
                unit.get("page_index_start"), unit.get("page_index_end")):
            out.append(Issue("broken_locator", where,
                             "page_index %s disagrees with unit %s (pages %s-%s)"
                             % (loc["page_index"], unit["unit_id"], unit.get("page_index_start"),
                                unit.get("page_index_end"))))
    excerpt = loc.get("excerpt") or ""
    if deep and excerpt:
        found = locators.find_excerpt(text, excerpt)
        if found is None:
            kind = "ocr_excerpt" if loc.get("extraction_method") == "ocr" else "excerpt_mismatch"
            out.append(Issue(kind, where,
                             "excerpt not present in docs/%s/text.txt: %r" % (pid, excerpt[:80])))
        else:
            stats["excerpts_verified"] += 1
            if loc.get("char_start") is not None and abs(found[0] - loc["char_start"]) > 5:
                out.append(Issue("broken_locator", where,
                                 "excerpt found at %d but locator says char_start=%d"
                                 % (found[0], loc["char_start"])))
    if loc.get("extraction_method") == "ocr":
        out.append(Issue("ocr_excerpt", where, "locator rests on OCR text; excerpt is not exact"))
    return out


def _check_profile_locators(kb_dir: str, pid: str, profile: Dict[str, Any], deep: bool,
                            stats: Dict[str, int]) -> List[Issue]:
    out: List[Issue] = []
    text = store.read_text(kb_dir, pid) if deep else ""
    units = {u["unit_id"]: u for u in store.read_units(kb_dir, pid)}
    analysis = profile.get("analysis") or {}

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "locators" and isinstance(value, list):
                    for loc in value:
                        stats["locators"] += 1
                        out.extend(_check_locator(kb_dir, pid, loc, units, text, deep, stats,
                                                  where="%s/profile:%s" % (pid, path)))
                else:
                    walk(value, "%s.%s" % (path, key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, "%s[%d]" % (path, i))

    walk(analysis, "analysis")
    for field in ("primary_findings", "main_contribution", "research_problem"):
        node = analysis.get(field)
        items = node if isinstance(node, list) else ([node] if node else [])
        for item in items:
            if item and item.get("basis") in ("author_stated", "shown_by_result") and not item.get("locators"):
                out.append(Issue("missing_locator", "%s/profile:%s" % (pid, field),
                                 "a document-grounded statement must carry at least one locator"))
    return out


def _check_grounding(node: Any, where: str, live_claims: Dict[str, Any], active: List[str]) -> List[Issue]:
    out: List[Issue] = []
    GROUNDED_KEYS = (
        "agreements", "qualified_agreements", "genuine_disagreements",
        "apparent_disagreements_explained", "common_limitations", "understudied_conditions",
        "open_questions", "strong_agreement", "qualified_agreement", "genuine_contradictions",
        "apparent_contradictions_explained", "evidence_strength_assessment", "failure_modes",
        "research_gaps",
    )
    if isinstance(node, dict):
        for key, value in node.items():
            if key in GROUNDED_KEYS and isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    claim_ids = item.get("claim_ids") or []
                    if not claim_ids:
                        out.append(Issue("unsupported_synthesis", "%s.%s" % (where, key),
                                         "synthesis point cites no claim ids: %r" % (item.get("text", "")[:70])))
                    for cid in claim_ids:
                        if cid not in live_claims:
                            out.append(Issue("dangling_reference", "%s.%s" % (where, key),
                                             "claim_id %s does not exist" % cid))
                    for pid in item.get("paper_ids") or []:
                        if pid not in active:
                            out.append(Issue("dangling_reference", "%s.%s" % (where, key),
                                             "paper_id %s is not active" % pid))
            elif key in ("paper_ids", "papers") and isinstance(value, list):
                for pid in value:
                    if isinstance(pid, str) and pid not in active:
                        out.append(Issue("dangling_reference", where, "paper_id %s is not active" % pid))
            else:
                out.extend(_check_grounding(value, where, live_claims, active))
    elif isinstance(node, list):
        for item in node:
            out.extend(_check_grounding(item, where, live_claims, active))
    return out


def _check_markdown_links(kb_dir: str) -> List[Issue]:
    out: List[Issue] = []
    for root, dirs, files in os.walk(kb_dir):
        dirs[:] = [d for d in dirs if d not in ("index", "tasks", "sources")]
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            body = fsutil.read_text(path)
            for target in MD_LINK_RE.findall(body):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                resolved = os.path.normpath(os.path.join(root, target.split("#")[0]))
                if not os.path.exists(resolved):
                    out.append(Issue("broken_link", os.path.relpath(path, kb_dir),
                                     "link target does not exist: %s" % target))
    return out


def _write_report(kb_dir: str, result: Dict[str, Any]) -> str:
    lines = ["# Validation report", "",
             "- KB: `%s`" % result["kb_dir"],
             "- Result: **%s**" % ("PASS" if result["ok"] else "FAIL"),
             "", "## Stats", ""]
    for key, value in sorted(result["stats"].items()):
        lines.append("- %s: %s" % (key, value))
    for label, key in (("Errors", "errors"), ("Warnings", "warnings")):
        lines += ["", "## %s (%d)" % (label, len(result[key])), ""]
        by_kind: Dict[str, List[Dict[str, str]]] = {}
        for item in result[key]:
            by_kind.setdefault(item["kind"], []).append(item)
        for kind, items in sorted(by_kind.items()):
            lines.append("### `%s` (%d)" % (kind, len(items)))
            for item in items[:40]:
                lines.append("- **%s** — %s" % (item["where"], item["message"]))
            if len(items) > 40:
                lines.append("- _… %d more_" % (len(items) - 40))
            lines.append("")
    path = os.path.join(kb_dir, "reports", "validation_report.md")
    fsutil.atomic_write_text(path, "\n".join(lines) + "\n")
    fsutil.atomic_write_json(os.path.join(kb_dir, "reports", "validation_report.json"), result)
    return path
