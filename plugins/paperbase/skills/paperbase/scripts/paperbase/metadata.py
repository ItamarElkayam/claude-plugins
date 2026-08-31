"""Offline bibliographic metadata extraction, with per-field source + confidence.

Nothing here contacts the network: a locally supplied corpus must be fully usable
offline.  Optional external enrichment (Crossref) lives in enrich.py and records
its own provenance, never overwriting a human override.

Every field is returned with a provenance record {source, confidence} so that
downstream agents can tell "the DOI printed on page 1" from "guessed from the
filename".
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import ids

YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-4]\d)\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
AFFIL_HINTS = (
    "university", "department", "institute", "school", "laboratory", "labs",
    "college", "hospital", "center", "centre", "inc.", "gmbh", "faculty",
    "academy", "cnrs", "inria", "correspondence", "©", "copyright", "e-mail",
)
VENUE_HINTS = (
    "bioinformatics", "bmc ", "nature", "science", "cell ", "plos", "genome",
    "nucleic acids research", "briefings", "arxiv", "biorxiv", "medrxiv", "ieee",
    "acm", "elife", "gigascience", "peerj", "frontiers", "microbial genomics",
    "journal", "proceedings", "transactions", "conference", "workshop", "preprint",
)
REVIEW_RE = re.compile(r"\b(review|survey|perspective|primer|tutorial|overview|opinion)\b", re.I)
TOOL_RE = re.compile(r"\b(software|tool|toolkit|package|pipeline|application note|implementation|library)\b", re.I)
BENCH_RE = re.compile(r"\b(benchmark|benchmarking|comparison|comparative (evaluation|assessment)|evaluation of)\b", re.I)
DATASET_RE = re.compile(r"\b(dataset|data descriptor|data resource|corpus)\b", re.I)


def _prov(source: str, confidence: float) -> Dict[str, Any]:
    return {"source": source, "confidence": round(float(confidence), 2)}


def _plausible_title(text: str) -> bool:
    if not text:
        return False
    text = text.strip()
    if not (12 <= len(text) <= 320):
        return False
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) < 3:
        return False
    alpha = sum(1 for c in text if c.isalpha())
    if alpha / float(len(text)) < 0.6:
        return False
    if re.search(r"\.(pdf|docx?|tex)$", text, re.I):
        return False
    if re.match(r"^(untitled|microsoft word|op-[a-z0-9]+|\d+\.\.\d+)", text.strip(), re.I):
        return False
    return True


def _clean_filename_title(stem: str) -> str:
    text = re.sub(r"[_]+", " ", stem)
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _front_text(doc: Dict[str, Any], n_pages_front: int = 2) -> str:
    parts = []
    for blk in doc["blocks"]:
        if blk.get("page_index") is None or blk["page_index"] < n_pages_front:
            if blk.get("role") in ("header", "footer", "page_number"):
                continue
            parts.append(blk["text"])
        if len("\n".join(parts)) > 12000:
            break
    return "\n".join(parts)


def _find_doi(doc: Dict[str, Any], parse_meta: Dict[str, Any], full_text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    embedded = parse_meta.get("embedded_doi")
    if embedded:
        doi = ids.normalize_doi(embedded)
        if doi:
            return doi, _prov("embedded_document_metadata", 0.95)
    front = _front_text(doc)
    match = ids.DOI_RE.search(front)
    if match:
        return ids.normalize_doi(match.group(1)), _prov("printed_on_first_pages", 0.9)
    # last page often carries the DOI in the footer of journal PDFs
    tail = full_text[-6000:]
    match = ids.DOI_RE.search(tail)
    if match:
        return ids.normalize_doi(match.group(1)), _prov("printed_in_document_tail", 0.6)
    return None, _prov("not_found", 0.0)


def _find_title(doc: Dict[str, Any], parse_meta: Dict[str, Any], source_path: str) -> Tuple[str, Dict[str, Any]]:
    embedded = parse_meta.get("embedded_title")
    stem = os.path.splitext(os.path.basename(source_path))[0]
    # 1. explicit heading structure (HTML/DOCX/Markdown)
    for blk in doc["blocks"][:6]:
        if (blk.get("style_hint") or "") == "heading1" and _plausible_title(blk["text"]):
            return re.sub(r"\s+", " ", blk["text"]).strip(), _prov("document_heading_level_1", 0.9)
    # 2. embedded metadata when it looks like a real title
    if _plausible_title(embedded or ""):
        return re.sub(r"\s+", " ", embedded).strip(), _prov("embedded_document_metadata", 0.85)
    # 3. largest-font front-matter block on the first page
    cands = [
        b for b in doc["blocks"]
        if b.get("role") == "front_matter" and b.get("page_index") in (0, None)
        and _plausible_title(re.sub(r"\s+", " ", b["text"]))
        and not EMAIL_RE.search(b["text"])
        and not any(h in b["text"].lower() for h in AFFIL_HINTS)
    ]
    if cands:
        best = max(cands, key=lambda b: ((b.get("font_size") or 0), -b["order"]))
        return re.sub(r"\s+", " ", best["text"]).strip(), _prov(
            "first_page_largest_font_block", 0.75 if best.get("font_size") else 0.55)
    # 4. filename
    return _clean_filename_title(stem), _prov("filename", 0.4)


def _find_authors(doc: Dict[str, Any], parse_meta: Dict[str, Any], title: str) -> Tuple[List[str], Dict[str, Any]]:
    embedded = parse_meta.get("embedded_author")
    if embedded and len(embedded) < 400:
        authors = _split_authors(embedded)
        if authors:
            return authors, _prov("embedded_document_metadata", 0.85)
    norm_title = ids.normalize_title(title)
    seen_title = False
    for blk in doc["blocks"][:14]:
        text = re.sub(r"\s+", " ", blk["text"]).strip()
        if not seen_title:
            if ids.normalize_title(text)[:40] and ids.normalize_title(text)[:40] == norm_title[:40]:
                seen_title = True
            continue
        low = text.lower()
        if any(h in low for h in AFFIL_HINTS) or EMAIL_RE.search(text):
            continue
        if len(text) > 400 or len(text) < 4:
            continue
        authors = _split_authors(text)
        if len(authors) >= 1 and _author_shaped(authors):
            return authors, _prov("first_page_author_line", 0.6)
    return [], _prov("not_found", 0.0)


def _split_authors(text: str) -> List[str]:
    text = re.sub(r"\d+|\*|†|‡|§|¶|\bemail\b.*$", "", text, flags=re.I)
    text = re.sub(r"\s+and\s+", ", ", text)
    text = re.sub(r"\s*[;&]\s*", ", ", text)
    parts = [p.strip(" ,. ") for p in text.split(",")]
    return [p for p in parts if 2 <= len(p) <= 60 and re.search(r"[A-Za-z]{2,}", p)]


NON_AUTHOR_PHRASES = (
    "table of contents", "supplementary", "supporting information", "abstract",
    "contents", "notes", "appendix", "figure", "table", "keywords", "open access",
    "research article", "software", "original article", "review",
)


def _author_shaped(authors: List[str]) -> bool:
    if not authors:
        return False
    joined = " ".join(authors).lower()
    if any(phrase in joined for phrase in NON_AUTHOR_PHRASES):
        return False
    letters = [c for c in " ".join(authors) if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / float(len(letters)) > 0.7:
        return False  # ALL-CAPS banner, not an author list
    ok = 0
    for a in authors:
        words = a.split()
        if 1 <= len(words) <= 5 and all(re.match(r"^[A-ZÀ-Ý]", w) or len(w) <= 3 for w in words):
            ok += 1
    return ok >= max(1, len(authors) // 2)


def _find_year(doc: Dict[str, Any], parse_meta: Dict[str, Any], doi: Optional[str]) -> Tuple[Optional[int], Dict[str, Any]]:
    this_year = time.localtime().tm_year
    front = _front_text(doc)
    for pattern, source, conf in (
        (r"(?:©|\(c\)|copyright)\s*(19[7-9]\d|20[0-4]\d)", "copyright_line", 0.85),
        (r"(?:published|accepted|received|revised|issued)[^\n]{0,40}?(19[7-9]\d|20[0-4]\d)", "publication_line", 0.8),
        (r"arxiv[:\s][^\n]{0,20}\b(1[0-9]|2[0-4])\d{2}\.\d{4,5}", "arxiv_id", 0.8),
    ):
        match = re.search(pattern, front, re.I)
        if match:
            grp = match.group(1)
            year = int(grp) if len(grp) == 4 else 2000 + int(grp)
            if 1970 <= year <= this_year + 1:
                return year, _prov(source, conf)
    embedded = parse_meta.get("embedded_creation_date") or parse_meta.get("embedded_date") or ""
    match = YEAR_RE.search(str(embedded))
    if match:
        return int(match.group(1)), _prov("embedded_document_date", 0.6)
    years = [int(y) for y in YEAR_RE.findall(front) if 1970 <= int(y) <= this_year + 1]
    if years:
        best = max(set(years), key=lambda y: (years.count(y), y))
        return best, _prov("most_frequent_year_on_first_pages", 0.45)
    return None, _prov("not_found", 0.0)


def _find_venue(doc: Dict[str, Any], parse_meta: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    if parse_meta.get("embedded_journal"):
        return parse_meta["embedded_journal"].strip(), _prov("embedded_document_metadata", 0.9)
    for blk in doc["blocks"][:18]:
        text = re.sub(r"\s+", " ", blk["text"]).strip()
        low = text.lower()
        if 3 <= len(text) <= 90 and any(h in low for h in VENUE_HINTS):
            if EMAIL_RE.search(text) or "@" in text:
                continue
            return text.strip(" .,|"), _prov("first_page_journal_banner", 0.6)
    for blk in doc["blocks"]:
        if blk.get("role") in ("header", "footer"):
            text = re.sub(r"\s+", " ", blk["text"]).strip()
            if 3 <= len(text) <= 90 and any(h in text.lower() for h in VENUE_HINTS):
                return text.strip(" .,|"), _prov("running_header", 0.5)
    return None, _prov("not_found", 0.0)


def _abstract(doc: Dict[str, Any]) -> Optional[str]:
    for unit in doc["units"]:
        path = " > ".join(unit.get("section_path") or []).lower()
        if "abstract" in path or "summary" in path:
            return unit["text"][:2500]
    for unit in doc["units"]:
        if unit["kind"] == "paragraph" and unit["n_chars"] > 300:
            return unit["text"][:2500]
    return None


def _keywords(doc: Dict[str, Any]) -> List[str]:
    for blk in doc["blocks"][:40]:
        match = re.match(r"^\s*key\s?words?\s*[:\-—]\s*(.+)$", blk["text"].strip(), re.I | re.S)
        if match:
            raw = re.sub(r"\s+", " ", match.group(1))
            parts = [p.strip(" .;") for p in re.split(r"[;,]", raw)]
            return [p for p in parts if 2 <= len(p) <= 60][:15]
    return []


def _doc_type(title: str, abstract: str, venue: str, filename: str, doc: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    probe = " ".join([title or "", (abstract or "")[:1200], venue or ""])
    sections = " ".join(doc["report"].get("sections") or []).lower()
    if _is_supplementary_name(filename):
        return "supplementary_material", _prov("filename_marker", 0.8)
    if REVIEW_RE.search(title or "") or ("review" in (venue or "").lower()):
        return "review", _prov("title_or_venue_keyword", 0.75)
    if BENCH_RE.search(title or ""):
        return "benchmark_study", _prov("title_keyword", 0.7)
    if TOOL_RE.search(title or "") or "software" in (venue or "").lower() or "application note" in probe.lower():
        return "software_tool_paper", _prov("title_or_venue_keyword", 0.65)
    if DATASET_RE.search(title or ""):
        return "dataset_paper", _prov("title_keyword", 0.6)
    if REVIEW_RE.search(probe) and "method" not in sections:
        return "review", _prov("abstract_keyword_without_methods_section", 0.55)
    if any(s in sections for s in ("method", "materials and methods", "results")):
        return "research_article", _prov("section_structure", 0.7)
    return "unknown", _prov("insufficient_signal", 0.3)


def _is_supplementary_name(filename: str) -> bool:
    low = os.path.basename(filename).lower()
    return any(m in low for m in ("supp", "supplement", "supporting", "_si.", "-si.", "appendix", "_s1", "_s2"))


def _status(venue: Optional[str], full_text_head: str, doi: Optional[str], cfg) -> Tuple[str, Dict[str, Any]]:
    probe = ((venue or "") + " " + full_text_head[:900]).lower()
    for name in cfg["metadata"]["preprint_venues"]:
        if re.search(r"\b%s\b" % re.escape(name), probe):
            return "preprint", _prov("preprint_venue_marker", 0.8)
    if doi and venue:
        return "published", _prov("doi_plus_venue", 0.7)
    if doi:
        return "published", _prov("doi_present", 0.5)
    return "unknown", _prov("insufficient_signal", 0.3)


def extract_metadata(doc: Dict[str, Any], parse_result: Dict[str, Any], source_path: str,
                     cfg: Dict[str, Any]) -> Dict[str, Any]:
    parse_meta = parse_result.get("doc_meta") or {}
    full_text = doc["text"]
    title, title_prov = _find_title(doc, parse_meta, source_path)
    authors, authors_prov = _find_authors(doc, parse_meta, title)
    doi, doi_prov = _find_doi(doc, parse_meta, full_text)
    year, year_prov = _find_year(doc, parse_meta, doi)
    venue, venue_prov = _find_venue(doc, parse_meta)
    abstract = _abstract(doc)
    doc_type, type_prov = _doc_type(title, abstract or "", venue or "", source_path, doc)
    status, status_prov = _status(venue, full_text, doi, cfg)
    front = _front_text(doc)
    arxiv = ids.ARXIV_RE.search(front)
    pmid = ids.PMID_RE.search(front)
    pmcid = ids.PMC_RE.search(front)

    meta = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "doi_confidence": doi_prov["confidence"],
        "arxiv_id": arxiv.group(1) if arxiv else None,
        "pmid": pmid.group(1) if pmid else None,
        "pmcid": pmcid.group(1) if pmcid else None,
        "document_type": doc_type,
        "publication_status": status,
        "keywords": _keywords(doc),
        "abstract": abstract,
        "language": "en",
        "source_stem": os.path.splitext(os.path.basename(source_path))[0],
    }
    provenance = {
        "title": title_prov, "authors": authors_prov, "year": year_prov,
        "venue": venue_prov, "doi": doi_prov, "document_type": type_prov,
        "publication_status": status_prov,
        "extraction": _prov("paperbase_offline_heuristics", 1.0),
    }
    return {"metadata": meta, "metadata_provenance": provenance}


# ------------------------------------------------------------------ families
def detect_families(papers: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Link supplementary files, preprint/published pairs and duplicate candidates.

    Conservative by design: a link is proposed with a basis and a confidence, and
    duplicates are never merged automatically - `kb validate` reports them and a
    human resolves them in overrides.yaml.
    """
    out: List[Dict[str, Any]] = []
    threshold = cfg["metadata"]["family_title_similarity"]
    main = [p for p in papers if p["metadata"]["document_type"] != "supplementary_material"]

    for paper in papers:
        pid = paper["paper_id"]
        meta = paper["metadata"]
        if meta["document_type"] == "supplementary_material":
            best, score, basis = None, 0.0, ""
            stem_tokens = ids.token_set(re.sub(r"(supp\w*|supporting|information|material|appendix|_s\d)", " ",
                                               meta["source_stem"], flags=re.I))
            for cand in main:
                cand_tokens = ids.token_set(cand["metadata"]["title"]) | ids.token_set(cand["metadata"]["source_stem"])
                sim = ids.jaccard(stem_tokens, cand_tokens)
                title_sim = ids.jaccard(ids.token_set(meta["title"]), ids.token_set(cand["metadata"]["title"]))
                combined = max(sim, title_sim)
                if combined > score:
                    best, score, basis = cand, combined, "filename_and_title_token_overlap"
            if best is not None and score >= 0.18:
                out.append({
                    "relation_id": ids.relation_id(pid, "supplement-of", best["paper_id"], "document_family"),
                    "source_paper_id": pid, "target_paper_id": best["paper_id"],
                    "relation": "supplement-of", "dimension": "document_family",
                    "rationale": "filename/title token overlap %.2f between supplementary file and main paper" % score,
                    "confidence": round(min(0.9, 0.4 + score), 2),
                    "origin": "inferred_by_paperbase_heuristic", "basis": basis,
                    "evidence_source": [], "evidence_target": [],
                })
            elif best is None or score < 0.18:
                out.append({
                    "relation_id": ids.relation_id(pid, "supplement-of", "UNRESOLVED", "document_family"),
                    "source_paper_id": pid, "target_paper_id": None,
                    "relation": "supplement-of", "dimension": "document_family",
                    "rationale": "looks like supplementary material but no main paper matched above "
                                 "the 0.18 token-overlap floor - resolve in overrides.yaml",
                    "confidence": 0.2, "origin": "inferred_by_paperbase_heuristic",
                    "basis": "unresolved", "evidence_source": [], "evidence_target": [],
                })

    # preprint / published pairs and duplicate candidates
    linked = set()
    for rel in out:
        if rel["target_paper_id"]:
            linked.add(frozenset((rel["source_paper_id"], rel["target_paper_id"])))
    for i, a in enumerate(papers):
        for b in papers[i + 1:]:
            if frozenset((a["paper_id"], b["paper_id"])) in linked:
                continue  # already a supplement-of pair; not a duplicate
            ta, tb = a["metadata"]["title"], b["metadata"]["title"]
            sim = ids.jaccard(ids.token_set(ta), ids.token_set(tb))
            if sim < threshold:
                continue
            same_doi = a["metadata"]["doi"] and a["metadata"]["doi"] == b["metadata"]["doi"]
            statuses = {a["metadata"]["publication_status"], b["metadata"]["publication_status"]}
            if statuses == {"preprint", "published"}:
                pre = a if a["metadata"]["publication_status"] == "preprint" else b
                pub = b if pre is a else a
                out.append({
                    "relation_id": ids.relation_id(pre["paper_id"], "preprint-of", pub["paper_id"], "document_family"),
                    "source_paper_id": pre["paper_id"], "target_paper_id": pub["paper_id"],
                    "relation": "preprint-of", "dimension": "document_family",
                    "rationale": "title similarity %.2f with preprint/published status pair; treated as related "
                                 "versions, NOT as identical documents" % sim,
                    "confidence": round(min(0.85, sim), 2),
                    "origin": "inferred_by_paperbase_heuristic", "basis": "title_similarity+status",
                    "evidence_source": [], "evidence_target": [],
                })
            else:
                out.append({
                    "relation_id": ids.relation_id(a["paper_id"], "possible-duplicate-of", b["paper_id"], "document_family"),
                    "source_paper_id": a["paper_id"], "target_paper_id": b["paper_id"],
                    "relation": "possible-duplicate-of", "dimension": "document_family",
                    "rationale": ("identical DOI" if same_doi else "title similarity %.2f" % sim) +
                                 " - NOT merged; resolve deliberately in overrides.yaml",
                    "confidence": round(min(0.9, sim + (0.1 if same_doi else 0.0)), 2),
                    "origin": "inferred_by_paperbase_heuristic", "basis": "title_similarity",
                    "evidence_source": [], "evidence_target": [],
                })
    return out
