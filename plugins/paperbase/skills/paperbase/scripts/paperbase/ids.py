"""Stable identifiers.

Identity rule (see references/knowledge-model.md):
  paper_id = <lastname>-<year>-<title-slug>-<disc6>
where disc6 = sha256(identity_key)[:6] and identity_key is, in order of
preference, a normalized DOI, an arXiv id, a PMID, or normalized metadata plus
the file content hash.  The readable prefix helps agents; the discriminator makes
the id collision-safe and independent of the rest of the corpus, so an id never
changes because another paper was added or removed.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>,;)\]]+)", re.I)
ARXIV_RE = re.compile(r"\barxiv[:/\s]*((?:\d{4}\.\d{4,5})(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)", re.I)
PMID_RE = re.compile(r"\bPMID[:\s]*(\d{6,9})\b", re.I)
PMC_RE = re.compile(r"\b(PMC\d{6,9})\b", re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_TITLE = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with", "using",
    "via", "from", "by", "at", "into", "based",
}


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def ascii_fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c))


def slug(text: str, max_words: int = 4, drop_stopwords: bool = True) -> str:
    words = _WORD_RE.findall(ascii_fold(text or "").lower())
    if drop_stopwords:
        kept = [w for w in words if w not in _STOP_TITLE] or words
    else:
        kept = words
    return "-".join(kept[:max_words]) or "untitled"


def normalize_doi(raw: str) -> Optional[str]:
    """Lower-cased bare DOI, prefixes and trailing punctuation stripped."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    match = DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(1).rstrip(".,;)]}>'\"").lower()
    # PDF text extraction frequently glues the next word onto the DOI suffix;
    # keep the DOI but record low confidence upstream rather than guessing.
    return doi


def normalize_title(title: str) -> str:
    return " ".join(_WORD_RE.findall(ascii_fold(title or "").lower()))


def last_name(author: str) -> str:
    author = (author or "").strip().strip(",")
    if not author:
        return "anon"
    if "," in author:
        return slug(author.split(",")[0], max_words=1, drop_stopwords=False)
    parts = [p for p in author.split() if p]
    return slug(parts[-1] if parts else "anon", max_words=1, drop_stopwords=False)


def identity_key(meta: Dict[str, Any], content_hash: str) -> Tuple[str, str]:
    """Return (basis, key). Basis is one of doi|arxiv|pmid|pmcid|metadata+content."""
    doi = normalize_doi(meta.get("doi") or "")
    if doi and meta.get("doi_confidence", 0) >= 0.6:
        return "doi", "doi:" + doi
    if meta.get("arxiv_id"):
        return "arxiv", "arxiv:" + str(meta["arxiv_id"]).lower()
    if meta.get("pmid"):
        return "pmid", "pmid:" + str(meta["pmid"])
    if meta.get("pmcid"):
        return "pmcid", "pmcid:" + str(meta["pmcid"]).upper()
    title = normalize_title(meta.get("title") or "")
    year = str(meta.get("year") or "")
    author = last_name((meta.get("authors") or ["anon"])[0])
    return "metadata+content", "meta:%s|%s|%s|%s" % (author, year, title, content_hash[:16])


def paper_id(meta: Dict[str, Any], content_hash: str) -> Tuple[str, str, str]:
    """Return (paper_id, identity_basis, identity_key)."""
    basis, key = identity_key(meta, content_hash)
    disc = hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]
    author = last_name((meta.get("authors") or ["anon"])[0]) or "anon"
    year = str(meta.get("year") or "nd")
    title_slug = slug(meta.get("title") or meta.get("source_stem") or "untitled", max_words=3)
    return "%s-%s-%s-%s" % (author, year, title_slug, disc), basis, key


def claim_id(paper: str, statement: str, ordinal: int) -> str:
    digest = hashlib.sha256(("%s|%s" % (paper, normalize_title(statement))).encode("utf-8")).hexdigest()[:6]
    return "%s#c%02d-%s" % (paper, ordinal, digest)


def relation_id(src: str, rel: str, dst: str, dimension: str = "") -> str:
    digest = hashlib.sha256(
        ("%s|%s|%s|%s" % (src, rel, dst, normalize_title(dimension))).encode("utf-8")
    ).hexdigest()[:8]
    return "r-%s" % digest


def unit_id(index: int) -> str:
    return "u%04d" % index


def topic_id(name: str) -> str:
    return "t-" + slug(name, max_words=5)


def token_set(text: str, extra_stop: Optional[List[str]] = None) -> set:
    stop = set(_STOP_TITLE) | set(extra_stop or [])
    return {w for w in _WORD_RE.findall(ascii_fold(text or "").lower()) if w not in stop and len(w) > 2}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))
