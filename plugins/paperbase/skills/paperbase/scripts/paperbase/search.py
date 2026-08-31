"""Lexical retrieval over the derived FTS5 index, with structured filters.

Lexical-first is deliberate: scientific questions hinge on exact identifiers,
tool names, metrics and version strings, which embeddings routinely blur.  An
optional semantic layer (embeddings.py) can be enabled to add recall; it reranks
candidates and never replaces the lexical path.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import indexdb

UNIT_WEIGHTS = "bm25(units_fts, 1.0, 2.0, 1.5)"
CLAIM_WEIGHTS = "bm25(claims_fts, 1.0, 1.5, 1.2)"
PAPER_WEIGHTS = "bm25(papers_fts, 3.0, 1.0, 1.5, 2.0)"
TOPIC_WEIGHTS = "bm25(topics_fts, 3.0, 1.0, 1.5)"


class Filters(object):
    """Structured post-filters applied to ranked candidates."""

    def __init__(self, papers=None, years=None, doc_types=None, methods=None, datasets=None,
                 concepts=None, authors=None, study_types=None, min_confidence=None,
                 claim_types=None, primary_only=False, exclude_papers=None):
        self.papers = set(papers or [])
        self.exclude_papers = set(exclude_papers or [])
        self.years = set(years or [])
        self.doc_types = set(t.lower() for t in (doc_types or []))
        self.methods = [m.lower() for m in (methods or [])]
        self.datasets = [d.lower() for d in (datasets or [])]
        self.concepts = [c.lower() for c in (concepts or [])]
        self.authors = [a.lower() for a in (authors or [])]
        self.study_types = set(s.lower() for s in (study_types or []))
        self.min_confidence = min_confidence
        self.claim_types = set(t.lower() for t in (claim_types or []))
        self.primary_only = primary_only

    def active(self) -> bool:
        return bool(self.papers or self.exclude_papers or self.years or self.doc_types or self.methods
                    or self.datasets or self.concepts or self.authors or self.study_types
                    or self.min_confidence is not None or self.claim_types or self.primary_only)

    def describe(self) -> str:
        bits = []
        for name in ("papers", "years", "doc_types", "methods", "datasets", "concepts",
                     "authors", "study_types", "claim_types"):
            value = getattr(self, name)
            if value:
                bits.append("%s=%s" % (name, sorted(value) if isinstance(value, set) else value))
        if self.min_confidence is not None:
            bits.append("min_confidence=%s" % self.min_confidence)
        if self.primary_only:
            bits.append("primary_evidence_only")
        if self.exclude_papers:
            bits.append("exclude=%s" % sorted(self.exclude_papers))
        return ", ".join(bits) or "none"

    # -------------------------------------------------- predicates
    def paper_ok(self, row: sqlite3.Row) -> bool:
        pid = row["paper_id"]
        if self.papers and pid not in self.papers:
            return False
        if pid in self.exclude_papers:
            return False
        if self.years and (row["year"] not in self.years):
            return False
        if self.doc_types and (row["document_type"] or "").lower() not in self.doc_types:
            return False
        if self.authors:
            blob = (row["authors"] or "").lower()
            if not any(a in blob for a in self.authors):
                return False
        return True

    def claim_ok(self, row: sqlite3.Row) -> bool:
        if self.papers and row["paper_id"] not in self.papers:
            return False
        if row["paper_id"] in self.exclude_papers:
            return False
        if self.min_confidence is not None and (row["confidence"] or 0) < self.min_confidence:
            return False
        if self.claim_types and (row["claim_type"] or "").lower() not in self.claim_types:
            return False
        if self.primary_only and not row["is_primary_evidence"]:
            return False
        for values, column in ((self.methods, "methods"), (self.datasets, "datasets"),
                               (self.concepts, "concepts")):
            if values:
                blob = (row[column] or "").lower()
                if not any(v in blob for v in values):
                    return False
        return True


def _match(query: str) -> Optional[str]:
    return query or None


def search_units(conn, query: str, limit: int, filters: Filters) -> List[Dict[str, Any]]:
    if not query:
        return []
    sql = (
        "SELECT f.paper_id AS paper_id, f.unit_id AS unit_id, %s AS score, "
        "u.kind, u.section_path, u.heading, u.page_index_start, u.page_label_start, "
        "u.char_start, u.char_end, u.n_chars, u.text, p.title, p.year, p.document_type, "
        "p.authors, p.text_quality "
        "FROM units_fts f JOIN units u ON u.paper_id=f.paper_id AND u.unit_id=f.unit_id "
        "JOIN papers p ON p.paper_id=f.paper_id "
        "WHERE units_fts MATCH ? AND p.status='active' ORDER BY score LIMIT ?" % UNIT_WEIGHTS
    )
    rows = conn.execute(sql, (query, limit * 4)).fetchall()
    out = []
    for row in rows:
        if not filters.paper_ok(row):
            continue
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def search_claims(conn, query: str, limit: int, filters: Filters) -> List[Dict[str, Any]]:
    if not query:
        return []
    sql = (
        "SELECT f.claim_id AS claim_id, f.paper_id AS paper_id, %s AS score, c.*, "
        "p.title, p.year, p.document_type "
        "FROM claims_fts f JOIN claims c ON c.claim_id=f.claim_id "
        "JOIN papers p ON p.paper_id=c.paper_id "
        "WHERE claims_fts MATCH ? AND p.status='active' ORDER BY score LIMIT ?" % CLAIM_WEIGHTS
    )
    rows = conn.execute(sql, (query, limit * 4)).fetchall()
    out = []
    for row in rows:
        if not filters.claim_ok(row) or not filters.paper_ok(row):
            continue
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def search_papers(conn, query: str, limit: int, filters: Filters) -> List[Dict[str, Any]]:
    if not query:
        return []
    sql = (
        "SELECT f.paper_id AS paper_id, %s AS score, p.* FROM papers_fts f "
        "JOIN papers p ON p.paper_id=f.paper_id WHERE papers_fts MATCH ? AND p.status='active' "
        "ORDER BY score LIMIT ?" % PAPER_WEIGHTS
    )
    rows = conn.execute(sql, (query, limit * 4)).fetchall()
    out = []
    for row in rows:
        if not filters.paper_ok(row):
            continue
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def search_topics(conn, query: str, limit: int) -> List[Dict[str, Any]]:
    if not query:
        return []
    sql = ("SELECT f.topic_id AS topic_id, %s AS score, t.name, t.summary, t.papers, t.payload "
           "FROM topics_fts f JOIN topics t ON t.topic_id=f.topic_id "
           "WHERE topics_fts MATCH ? ORDER BY score LIMIT ?" % TOPIC_WEIGHTS)
    return [dict(r) for r in conn.execute(sql, (query, limit)).fetchall()]


def claims_for_papers(conn, paper_ids: List[str], limit: int = 200) -> List[Dict[str, Any]]:
    if not paper_ids:
        return []
    marks = ",".join("?" * len(paper_ids))
    sql = ("SELECT c.*, p.title, p.year FROM claims c JOIN papers p ON p.paper_id=c.paper_id "
           "WHERE c.paper_id IN (%s) ORDER BY c.confidence DESC LIMIT ?" % marks)
    return [dict(r) for r in conn.execute(sql, tuple(paper_ids) + (limit,)).fetchall()]


def relations_for(conn, paper_ids: List[str], claim_ids: List[str] = None) -> List[Dict[str, Any]]:
    if not paper_ids and not claim_ids:
        return []
    clauses, params = [], []
    if paper_ids:
        marks = ",".join("?" * len(paper_ids))
        clauses.append("(source_paper_id IN (%s) OR target_paper_id IN (%s))" % (marks, marks))
        params.extend(list(paper_ids) + list(paper_ids))
    if claim_ids:
        marks = ",".join("?" * len(claim_ids))
        clauses.append("(source_claim_id IN (%s) OR target_claim_id IN (%s))" % (marks, marks))
        params.extend(list(claim_ids) + list(claim_ids))
    sql = "SELECT * FROM relations WHERE %s ORDER BY confidence DESC" % " OR ".join(clauses)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def opposing_claims(conn, claim_ids: List[str]) -> List[Dict[str, Any]]:
    """Claims linked to the given ones by a disagreement-flavoured relation.

    Used to guarantee that a context pack is not one-sided.
    """
    if not claim_ids:
        return []
    marks = ",".join("?" * len(claim_ids))
    sql = (
        "SELECT r.relation, r.rationale, r.dimension, r.confidence AS rel_confidence, "
        "r.source_claim_id, r.target_claim_id, c.*, p.title, p.year "
        "FROM relations r JOIN claims c ON c.claim_id IN (r.source_claim_id, r.target_claim_id) "
        "JOIN papers p ON p.paper_id = c.paper_id "
        "WHERE r.relation IN ('contradicts','partially-contradicts','qualifies','fails-to-reproduce',"
        "'studies-different-context') AND (r.source_claim_id IN (%s) OR r.target_claim_id IN (%s)) "
        "AND c.claim_id NOT IN (%s)" % (marks, marks, marks)
    )
    params = tuple(claim_ids) * 3
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def negative_claims(conn, paper_ids: List[str], limit: int = 10) -> List[Dict[str, Any]]:
    """Negative/null results and limitations - the evidence one-sided retrieval loses."""
    if not paper_ids:
        return []
    marks = ",".join("?" * len(paper_ids))
    sql = ("SELECT c.*, p.title, p.year FROM claims c JOIN papers p ON p.paper_id=c.paper_id "
           "WHERE c.paper_id IN (%s) AND (c.polarity IN ('negative','null_result','mixed') "
           "OR c.claim_type IN ('negative_result','limitation')) ORDER BY c.confidence DESC LIMIT ?"
           % marks)
    return [dict(r) for r in conn.execute(sql, tuple(paper_ids) + (limit,)).fetchall()]


def resolve_paper(conn, needle: str) -> List[str]:
    """Resolve a paper_id, id prefix, DOI or title fragment to paper ids."""
    row = conn.execute("SELECT paper_id FROM papers WHERE paper_id=?", (needle,)).fetchone()
    if row:
        return [row["paper_id"]]
    like = needle.strip().lower()
    rows = conn.execute(
        "SELECT paper_id FROM papers WHERE lower(paper_id) LIKE ? OR lower(doi)=? "
        "OR lower(title) LIKE ? ORDER BY paper_id",
        (like + "%", like, "%" + like + "%")).fetchall()
    return [r["paper_id"] for r in rows]
