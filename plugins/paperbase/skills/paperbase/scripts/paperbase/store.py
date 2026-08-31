"""Canonical KB layout: where every artifact lives and how it is read/written.

    <kb>/KB_INDEX.md                       navigation entry point for agents
    <kb>/config/config.yaml                effective configuration (hashed)
    <kb>/corpus/papers.jsonl               machine-readable corpus manifest
    <kb>/docs/<paper_id>/text.txt          normalized text = canonical offsets
    <kb>/docs/<paper_id>/blocks.jsonl      block-level structure + roles
    <kb>/docs/<paper_id>/units.jsonl       retrieval units
    <kb>/docs/<paper_id>/parse.json        parse report, quality, warnings
    <kb>/papers/<paper_id>/paper.md        human-readable profile
    <kb>/papers/<paper_id>/profile.json    structured analysis (schema-validated)
    <kb>/papers/<paper_id>/claims.jsonl    atomic claims with locators
    <kb>/relations/relations.jsonl         cross-paper relations (model + human)
    <kb>/relations/families.jsonl          document families (deterministic)
    <kb>/synthesis/topics/<topic_id>.json|.md
    <kb>/synthesis/terminology.json, TERMINOLOGY.md
    <kb>/synthesis/CORPUS_OVERVIEW.md, corpus_overview.json
    <kb>/synthesis/indexes/*.md            method/dataset/model/benchmark indexes
    <kb>/overrides/*.yaml                  human corrections (never regenerated)
    <kb>/state/manifest.json               fingerprints + dependency state
    <kb>/state/audit.jsonl                 append-only audit log
    <kb>/tasks/                             work orders for model-dependent stages
    <kb>/reports/                            build/update/validation reports
    <kb>/index/kb.sqlite                    DERIVED search index (gitignored)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import fsutil

DIRS = ("config", "corpus", "docs", "papers", "relations", "synthesis/topics",
        "synthesis/indexes", "overrides", "state", "tasks", "reports", "index")

GITIGNORE = """# Derived, rebuildable artifacts - regenerate with `kb reindex`.
index/
cache/
*.tmp
.pb-tmp-*
"""


def init(kb_dir: str) -> None:
    for name in DIRS:
        fsutil.ensure_dir(os.path.join(kb_dir, name))
    path = os.path.join(kb_dir, ".gitignore")
    if not os.path.exists(path):
        fsutil.atomic_write_text(path, GITIGNORE)


# ------------------------------------------------------------------ paths
def doc_dir(kb: str, pid: str) -> str:
    return os.path.join(kb, "docs", pid)


def paper_dir(kb: str, pid: str) -> str:
    return os.path.join(kb, "papers", pid)


def text_path(kb: str, pid: str) -> str:
    return os.path.join(doc_dir(kb, pid), "text.txt")


def blocks_path(kb: str, pid: str) -> str:
    return os.path.join(doc_dir(kb, pid), "blocks.jsonl")


def units_path(kb: str, pid: str) -> str:
    return os.path.join(doc_dir(kb, pid), "units.jsonl")


def parse_path(kb: str, pid: str) -> str:
    return os.path.join(doc_dir(kb, pid), "parse.json")


def profile_path(kb: str, pid: str) -> str:
    return os.path.join(paper_dir(kb, pid), "profile.json")


def paper_md_path(kb: str, pid: str) -> str:
    return os.path.join(paper_dir(kb, pid), "paper.md")


def claims_path(kb: str, pid: str) -> str:
    return os.path.join(paper_dir(kb, pid), "claims.jsonl")


def corpus_path(kb: str) -> str:
    return os.path.join(kb, "corpus", "papers.jsonl")


def relations_path(kb: str) -> str:
    """Model-generated and human-authored relations."""
    return os.path.join(kb, "relations", "relations.jsonl")


def families_path(kb: str) -> str:
    """Deterministic document-family links (supplement-of, preprint-of, duplicates)."""
    return os.path.join(kb, "relations", "families.jsonl")


def tombstoned_relations_path(kb: str) -> str:
    """Relations retired because a paper they referenced left the corpus."""
    return os.path.join(kb, "relations", "tombstoned.jsonl")


def topics_dir(kb: str) -> str:
    return os.path.join(kb, "synthesis", "topics")


def overview_json_path(kb: str) -> str:
    return os.path.join(kb, "synthesis", "corpus_overview.json")


def terminology_path(kb: str) -> str:
    return os.path.join(kb, "synthesis", "terminology.json")


# ------------------------------------------------------------------ writers
def write_document(kb: str, pid: str, doc: Dict[str, Any], extra_report: Dict[str, Any]) -> List[str]:
    fsutil.ensure_dir(doc_dir(kb, pid))
    fsutil.atomic_write_text(text_path(kb, pid), doc["text"])
    fsutil.atomic_write_jsonl(blocks_path(kb, pid), doc["blocks"])
    fsutil.atomic_write_jsonl(units_path(kb, pid), doc["units"])
    report = dict(doc["report"])
    report.update(extra_report)
    fsutil.atomic_write_json(parse_path(kb, pid), report)
    return [text_path(kb, pid), blocks_path(kb, pid), units_path(kb, pid), parse_path(kb, pid)]


def write_corpus_manifest(kb: str, rows: List[Dict[str, Any]]) -> str:
    fsutil.atomic_write_jsonl(corpus_path(kb), rows)
    return corpus_path(kb)


# ------------------------------------------------------------------ readers
def read_corpus_manifest(kb: str) -> List[Dict[str, Any]]:
    return fsutil.read_jsonl(corpus_path(kb))


def read_units(kb: str, pid: str) -> List[Dict[str, Any]]:
    return fsutil.read_jsonl(units_path(kb, pid))


def read_blocks(kb: str, pid: str) -> List[Dict[str, Any]]:
    return fsutil.read_jsonl(blocks_path(kb, pid))


def read_text(kb: str, pid: str) -> str:
    return fsutil.read_text(text_path(kb, pid))


def read_parse(kb: str, pid: str) -> Dict[str, Any]:
    return fsutil.read_json(parse_path(kb, pid), {}) or {}


def read_profile(kb: str, pid: str) -> Optional[Dict[str, Any]]:
    return fsutil.read_json(profile_path(kb, pid), None)


def read_claims(kb: str, pid: str) -> List[Dict[str, Any]]:
    return fsutil.read_jsonl(claims_path(kb, pid))


def read_all_claims(kb: str, paper_ids: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pid in paper_ids:
        out.extend(read_claims(kb, pid))
    return out


def read_relations(kb: str, include_families: bool = True) -> List[Dict[str, Any]]:
    rows = fsutil.read_jsonl(relations_path(kb))
    if include_families:
        rows = rows + fsutil.read_jsonl(families_path(kb))
    return rows


def read_topics(kb: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    directory = topics_dir(kb)
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            data = fsutil.read_json(os.path.join(directory, name), None)
            if data:
                out.append(data)
    return out


def read_overview(kb: str) -> Optional[Dict[str, Any]]:
    return fsutil.read_json(overview_json_path(kb), None)


def read_terminology(kb: str) -> Dict[str, Any]:
    return fsutil.read_json(terminology_path(kb), {"terms": []}) or {"terms": []}


def remove_paper_artifacts(kb: str, pid: str) -> List[str]:
    """Delete derived knowledge for one paper (used on removal / tombstoning)."""
    removed = []
    for path in (doc_dir(kb, pid), paper_dir(kb, pid)):
        if os.path.exists(path):
            fsutil.rm_tree(path)
            removed.append(path)
    return removed
