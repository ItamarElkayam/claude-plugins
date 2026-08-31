"""Derived SQLite FTS5 search index.

Everything here is rebuildable from the canonical Markdown/JSONL artifacts with
`kb reindex`; the database is never the only copy of anything and lives under
<kb>/index/ which the KB's own .gitignore excludes.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

INDEX_VERSION = 3
_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS papers (
  paper_id TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER, venue TEXT,
  doi TEXT, document_type TEXT, publication_status TEXT, status TEXT,
  source_path TEXT, content_sha256 TEXT, text_quality TEXT, needs_ocr INTEGER,
  n_units INTEGER, n_claims INTEGER, topics TEXT, abstract TEXT, has_profile INTEGER
);
CREATE TABLE IF NOT EXISTS units (
  paper_id TEXT, unit_id TEXT, kind TEXT, section_path TEXT, heading TEXT,
  page_index_start INTEGER, page_label_start TEXT, char_start INTEGER, char_end INTEGER,
  n_chars INTEGER, text TEXT, PRIMARY KEY (paper_id, unit_id)
);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY, paper_id TEXT, statement TEXT, claim_type TEXT,
  evidence_status TEXT, polarity TEXT, subject TEXT, intervention TEXT, comparator TEXT,
  outcome TEXT, direction TEXT, magnitude TEXT, uncertainty TEXT, conditions TEXT,
  qualifiers TEXT, evidence_type TEXT, confidence REAL, is_primary_evidence INTEGER,
  section_origin TEXT, concepts TEXT, methods TEXT, datasets TEXT, support TEXT,
  duplicate_of TEXT
);
CREATE TABLE IF NOT EXISTS relations (
  relation_id TEXT PRIMARY KEY, source_paper_id TEXT, target_paper_id TEXT,
  source_claim_id TEXT, target_claim_id TEXT, relation TEXT, dimension TEXT,
  rationale TEXT, confidence REAL, origin TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS topics (
  topic_id TEXT PRIMARY KEY, name TEXT, aliases TEXT, summary TEXT, papers TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS terms (
  term TEXT PRIMARY KEY, canonical TEXT, synonyms TEXT, paper_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_paper ON units(paper_id);
CREATE INDEX IF NOT EXISTS idx_claims_paper ON claims(paper_id);
CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(source_paper_id);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON relations(target_paper_id);
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
  text, heading, section, paper_id UNINDEXED, unit_id UNINDEXED, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
  statement, facets, tags, paper_id UNINDEXED, claim_id UNINDEXED, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
  title, authors, abstract, keywords, paper_id UNINDEXED, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS topics_fts USING fts5(
  name, summary, tags, topic_id UNINDEXED, tokenize='porter unicode61'
);
"""


def db_path(kb_dir: str) -> str:
    return os.path.join(kb_dir, "index", "kb.sqlite")


def connect(kb_dir: str, create: bool = True) -> sqlite3.Connection:
    path = db_path(kb_dir)
    if not os.path.exists(path):
        if not create:
            raise SystemExit(
                "search index missing at %s - run `kb reindex %s` (it is derived and safe to rebuild)"
                % (path, kb_dir))
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _j(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " | ".join(str(v) for v in value if v is not None)
    return str(value)


def rebuild(kb_dir: str, papers: List[Dict[str, Any]], units_by_paper: Dict[str, List[Dict[str, Any]]],
            claims: List[Dict[str, Any]], relations: List[Dict[str, Any]],
            topics: List[Dict[str, Any]], terms: List[Dict[str, Any]],
            fingerprint: str) -> Dict[str, int]:
    conn = connect(kb_dir)
    with conn:
        for table in ("papers", "units", "claims", "relations", "topics", "terms",
                      "units_fts", "claims_fts", "papers_fts", "topics_fts"):
            conn.execute("DELETE FROM %s" % table)
        for paper in papers:
            meta = paper["metadata"]
            conn.execute(
                "INSERT OR REPLACE INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (paper["paper_id"], meta.get("title"), _j(meta.get("authors")), meta.get("year"),
                 meta.get("venue"), meta.get("doi"), meta.get("document_type"),
                 meta.get("publication_status"), paper.get("status", "active"),
                 paper.get("source_path"), paper.get("content_sha256"),
                 (paper.get("parse") or {}).get("text_quality"),
                 1 if (paper.get("parse") or {}).get("needs_ocr") else 0,
                 len(units_by_paper.get(paper["paper_id"], [])),
                 sum(1 for c in claims if c["paper_id"] == paper["paper_id"]),
                 _j(paper.get("topics")), (meta.get("abstract") or "")[:4000],
                 1 if paper.get("has_profile") else 0),
            )
            conn.execute(
                "INSERT INTO papers_fts (title, authors, abstract, keywords, paper_id) VALUES (?,?,?,?,?)",
                (meta.get("title") or "", _j(meta.get("authors")), (meta.get("abstract") or "")[:8000],
                 _j(meta.get("keywords")) + " " + _j(paper.get("retrieval_terms")), paper["paper_id"]),
            )
        for pid, units in units_by_paper.items():
            for unit in units:
                section = " > ".join(unit.get("section_path") or [])
                conn.execute(
                    "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, unit["unit_id"], unit["kind"], section, unit.get("heading"),
                     unit.get("page_index_start"), unit.get("page_label_start"),
                     unit["char_start"], unit["char_end"], unit["n_chars"], unit["text"]),
                )
                conn.execute(
                    "INSERT INTO units_fts (text, heading, section, paper_id, unit_id) VALUES (?,?,?,?,?)",
                    (unit["text"], unit.get("heading") or "", section, pid, unit["unit_id"]),
                )
        for claim in claims:
            effect = claim.get("effect") or {}
            conn.execute(
                "INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim["claim_id"], claim["paper_id"], claim["statement"], claim["claim_type"],
                 claim["evidence_status"], claim["polarity"], claim.get("subject"),
                 claim.get("intervention"), claim.get("comparator"), claim.get("outcome"),
                 effect.get("direction"), effect.get("magnitude"), effect.get("uncertainty"),
                 _j(claim.get("conditions")), _j(claim.get("qualifiers")), claim.get("evidence_type"),
                 claim.get("confidence"), 1 if claim.get("is_primary_evidence", True) else 0,
                 claim.get("section_origin"), _j(claim.get("concepts")), _j(claim.get("methods")),
                 _j(claim.get("datasets")), json.dumps(claim.get("support") or []),
                 claim.get("duplicate_of")),
            )
            facets = " ".join(filter(None, [
                claim.get("subject") or "", claim.get("intervention") or "",
                claim.get("comparator") or "", claim.get("outcome") or "",
                effect.get("magnitude") or "", _j(claim.get("conditions")),
            ]))
            tags = " ".join([_j(claim.get("concepts")), _j(claim.get("methods")),
                             _j(claim.get("datasets")), _j(claim.get("entities")),
                             claim.get("claim_type") or "", claim.get("polarity") or ""])
            conn.execute(
                "INSERT INTO claims_fts (statement, facets, tags, paper_id, claim_id) VALUES (?,?,?,?,?)",
                (claim["statement"], facets, tags, claim["paper_id"], claim["claim_id"]),
            )
        for rel in relations:
            conn.execute(
                "INSERT OR REPLACE INTO relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rel["relation_id"], rel["source_paper_id"], rel.get("target_paper_id"),
                 rel.get("source_claim_id"), rel.get("target_claim_id"), rel["relation"],
                 rel.get("dimension"), rel.get("rationale"), rel.get("confidence"),
                 rel.get("origin"), json.dumps(rel)),
            )
        for topic in topics:
            conn.execute(
                "INSERT OR REPLACE INTO topics VALUES (?,?,?,?,?,?)",
                (topic["topic_id"], topic["name"], _j(topic.get("aliases")), topic.get("summary"),
                 _j(topic.get("papers")), json.dumps(topic)),
            )
            conn.execute(
                "INSERT INTO topics_fts (name, summary, tags, topic_id) VALUES (?,?,?,?)",
                (topic["name"] + " " + _j(topic.get("aliases")), topic.get("summary") or "",
                 " ".join([_j(topic.get("key_concepts")), _j(topic.get("methods")),
                           _j(topic.get("datasets_and_benchmarks"))]), topic["topic_id"]),
            )
        for term in terms:
            conn.execute(
                "INSERT OR REPLACE INTO terms VALUES (?,?,?,?)",
                (term["term"].lower(), term.get("canonical"), _j(term.get("synonyms")),
                 _j(term.get("paper_ids"))),
            )
        for key, value in (("index_version", str(INDEX_VERSION)), ("fingerprint", fingerprint)):
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    counts = {
        table: conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        for table in ("papers", "units", "claims", "relations", "topics", "terms")
    }
    conn.close()
    return counts


_TOKEN_RE = re.compile(r'"[^"]+"|[A-Za-z0-9][A-Za-z0-9_.\-/+]*')


def fts_query(text: str, raw: bool = False) -> str:
    """Turn a natural-language question into a safe FTS5 MATCH expression.

    Words become quoted OR-terms so exact identifiers, hyphenated names and
    version strings survive; bm25 does the ranking.  Phrases already in double
    quotes are preserved as phrases.
    """
    if raw:
        return text
    tokens = _TOKEN_RE.findall(text)
    out: List[str] = []
    for tok in tokens:
        if tok.startswith('"'):
            out.append(tok)
            continue
        clean = tok.strip(".-/+_")
        if len(clean) < 2 or clean.lower() in _FTS_STOP:
            continue
        out.append('"%s"' % clean.replace('"', ""))
    return " OR ".join(out)


_FTS_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "with", "is",
    "are", "was", "were", "be", "do", "does", "did", "how", "what", "which",
    "why", "when", "who", "that", "this", "it", "as", "at", "by", "from", "than",
    "then", "there", "these", "those", "any", "all", "can", "could", "should",
    "would", "we", "i", "you", "they", "if", "not", "no", "but", "about", "into",
    "vs", "versus", "under", "over", "between", "does", "do", "using", "use",
}


def expand_synonyms(conn: sqlite3.Connection, text: str, cfg: Dict[str, Any]) -> List[str]:
    """Extra query terms from the corpus terminology map and config synonyms."""
    extra: List[str] = []
    low = text.lower()
    for term, aliases in (cfg["terminology"]["synonyms"] or {}).items():
        if term.lower() in low:
            extra.extend(aliases if isinstance(aliases, list) else [aliases])
        else:
            for alias in (aliases if isinstance(aliases, list) else [aliases]):
                if str(alias).lower() in low:
                    extra.append(term)
    try:
        for row in conn.execute("SELECT term, canonical, synonyms FROM terms"):
            names = [row["term"]] + [s.strip() for s in (row["synonyms"] or "").split("|") if s.strip()]
            if any(n and n.lower() in low for n in names):
                extra.extend([n for n in names if n and n.lower() not in low])
                if row["canonical"]:
                    extra.append(row["canonical"])
    except sqlite3.Error:
        pass
    seen, out = set(), []
    for item in extra:
        key = str(item).lower()
        if key not in seen and len(key) > 1:
            seen.add(key)
            out.append(str(item))
    return out[:12]
